"""实体归一化层 — 将 raw 槽位匹配到图谱标准实体。

输入：QueryFrame(raw slots)
输出：QueryFrame(resolved EntitySlot)
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from backend.query_understanding import EntitySlot, QueryFrame

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIAS_PATH = ROOT / "config" / "recipe_aliases.json"
RECOMMENDATION_ALIAS_PATH = ROOT / "config" / "recommendation_aliases.json"
REVERSE_ALIAS_PATH = ROOT / "config" / "reverse_entity_aliases.json"
FUZZY_THRESHOLD = 0.6
DISH_FUZZY_THRESHOLD = 0.85
DISH_CONNECTOR_CHARS = "炒烧炖煮蒸炸烤煎拌焖熘煲烩烙"
GENERIC_AMBIGUOUS_INGREDIENTS = {"肉"}


def _load_aliases() -> dict[str, list[str]]:
    """加载别名配置: canonical -> [alias, ...]"""
    alias_map: dict[str, list[str]] = {}
    for path in (DEFAULT_ALIAS_PATH, RECOMMENDATION_ALIAS_PATH):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            # recommendation_aliases.json 是 kind -> canonical -> aliases
            if all(isinstance(value, dict) for value in data.values()):
                for groups in data.values():
                    for canonical, aliases in groups.items():
                        alias_map.setdefault(str(canonical), [])
                        alias_map[str(canonical)].extend(str(a) for a in (aliases or []) if a)
            else:
                for canonical, aliases in data.items():
                    alias_map.setdefault(str(canonical), [])
                    alias_map[str(canonical)].extend(str(a) for a in (aliases or []) if a)
    # 手工保底别名属于实体解析层，不散落到 adapter/executor。
    alias_map.setdefault("芥蓝", []).extend(["芥兰", "芥蓝菜", "芥兰菜"])
    for key, values in list(alias_map.items()):
        alias_map[key] = list(dict.fromkeys([key, *values]))
    return alias_map


def _build_reverse_alias_map(alias_map: dict[str, list[str]]) -> dict[str, str]:
    """alias -> canonical 反向映射。"""
    rev: dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        for a in aliases:
            rev[a] = canonical
    return rev


def _canonical_entity_types() -> dict[str, str]:
    """标准食材 -> 实体类型映射（从 reverse_entity_aliases 加载）。"""
    mapping: dict[str, str] = {}
    if REVERSE_ALIAS_PATH.is_file():
        try:
            data = json.loads(REVERSE_ALIAS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for entity_type, names in data.items():
                if isinstance(names, list):
                    for name in names:
                        mapping[str(name)] = entity_type
    return mapping


def _fuzzy_match(query: str, candidates: set[str]) -> tuple[str | None, float]:
    best = None
    best_score = 0
    q = query.lower()
    for c in candidates:
        if c.lower() == q:
            return c, 1.0
        if q in c.lower() or c.lower() in q:
            score = 0.9
            if score > best_score:
                best_score = score
                best = c
    if best_score < FUZZY_THRESHOLD:
        for c in candidates:
            score = SequenceMatcher(None, q, c.lower()).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best = c
    return best, best_score


def _dish_name_variants(raw: str, alias_map: dict[str, list[str]]) -> list[str]:
    """生成菜名归一候选：先替换食材别名，再去掉菜名中的烹饪连接词。"""
    text = str(raw or "").strip()
    if not text:
        return []

    variants = [text]
    alias_pairs: list[tuple[str, str]] = []
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            if alias and alias != canonical:
                alias_pairs.append((str(alias), str(canonical)))

    for alias, canonical in sorted(alias_pairs, key=lambda item: len(item[0]), reverse=True):
        for value in list(variants):
            if alias in value:
                replaced = value.replace(alias, canonical)
                if replaced not in variants:
                    variants.append(replaced)

    for value in list(variants):
        compact = "".join(ch for ch in value if ch not in DISH_CONNECTOR_CHARS)
        if compact and compact != value and compact not in variants:
            variants.append(compact)

    return variants[:16]


def _family_match(canonical: str, candidate: str) -> bool:
    """判断配置中的泛实体是否覆盖图谱节点。"""
    left = str(canonical or "").strip().lower()
    right = str(candidate or "").strip().lower().replace("（", "(").replace("）", ")")
    if not left or not right:
        return False
    root = left.split("(", 1)[0]
    return len(root) >= 1 and root in right


def _resolve_single(
    raw: str,
    entity_names: dict[str, set[str]],
    alias_map: dict[str, list[str]],
    rev_alias: dict[str, str],
    type_map: dict[str, str],
    allowed_types: set[str] | None = None,
) -> EntitySlot:
    """解析单个 raw 实体。"""
    slot = EntitySlot(raw=raw)
    names_by_type = {
        etype: names
        for etype, names in entity_names.items()
        if not allowed_types or etype in allowed_types
    }

    # 1. 在各实体类型中精确匹配
    for etype, names in names_by_type.items():
        if raw in names:
            slot.canonical = raw
            slot.entity_type = etype
            slot.match_mode = "exact"
            slot.confidence = 1.0
            return slot

    # 2. 别名匹配
    if raw in rev_alias:
        canonical = rev_alias[raw]
        for etype, names in names_by_type.items():
            if canonical in names:
                slot.canonical = canonical
                slot.entity_type = etype
                slot.match_mode = "alias"
                slot.confidence = 0.95
                return slot
            if any(_family_match(canonical, name) for name in names):
                slot.canonical = canonical
                slot.entity_type = etype
                slot.match_mode = "family"
                slot.confidence = 0.9
                return slot

    # 3. 别名延伸（通过 alias_map 扩展）
    for canonical, aliases in alias_map.items():
        if raw in aliases:
            for etype, names in names_by_type.items():
                if canonical in names:
                    slot.canonical = canonical
                    slot.entity_type = etype
                    slot.match_mode = "alias"
                    slot.confidence = 0.95
                    return slot
                if any(_family_match(canonical, name) for name in names):
                    slot.canonical = canonical
                    slot.entity_type = etype
                    slot.match_mode = "family"
                    slot.confidence = 0.9
                    return slot

    # 3.5 菜名归一变体：地方食材名 + 烹饪动词常导致菜名表述不同。
    is_dish_only = allowed_types == {"Dish"}
    if is_dish_only:
        dish_names = names_by_type.get("Dish", set())
        for variant in _dish_name_variants(raw, alias_map):
            if variant in dish_names:
                slot.canonical = variant
                slot.entity_type = "Dish"
                slot.match_mode = "normalized"
                slot.confidence = 0.92 if variant != raw else 1.0
                return slot

    # 泛称不能凭模糊相似度擅自选择一个具体肉类。
    if raw.strip() in GENERIC_AMBIGUOUS_INGREDIENTS and not is_dish_only:
        slot.match_mode = "ambiguous"
        slot.confidence = 0.0
        return slot

    # 4. 模糊匹配
    best_type = None
    best_name = None
    best_score = 0
    for etype, names in names_by_type.items():
        matched, score = _fuzzy_match(raw, names)
        if matched and score > best_score:
            best_score = score
            best_name = matched
            best_type = etype

    if is_dish_only and len(str(raw or "").strip()) < 3:
        best_name = None
    threshold = DISH_FUZZY_THRESHOLD if is_dish_only else FUZZY_THRESHOLD
    if best_name and best_score >= threshold:
        slot.canonical = best_name
        slot.entity_type = best_type
        slot.match_mode = "fuzzy"
        slot.confidence = best_score
        return slot

    # 5. 从 type_map 推断实体类型
    if raw in type_map and (not allowed_types or type_map[raw] in allowed_types):
        slot.canonical = raw
        slot.entity_type = type_map[raw]
        slot.match_mode = "exact"
        slot.confidence = 0.8
        return slot

    slot.match_mode = "missing"
    slot.confidence = 0.0
    return slot


def resolve(
    frame: QueryFrame,
    *,
    entity_names: dict[str, set[str]] | None = None,
    kg_system: Any = None,
) -> QueryFrame:
    """归一化 QueryFrame 中的 raw 槽位。

    参数：
        frame: 待解析的 QueryFrame
        entity_names: 可选。{entity_type: {name1, name2, ...}}
                      如果不传，从 kg_system 提取。
        kg_system: 可选。RecipeQuerySystem 实例，用于提取图谱节点名。

    返回：
        解析后的 QueryFrame（EntitySlot 的 canonical/match_mode 已填充）
    """
    # 提取实体名集
    if entity_names is None and kg_system is not None:
        entity_names = {}
        executor = getattr(kg_system, "executor", None)
        nodes_by_label = getattr(executor, "all_nodes_by_label", None)
        if isinstance(nodes_by_label, dict):
            for label, nodes in nodes_by_label.items():
                if isinstance(nodes, dict):
                    entity_names[label] = {str(k) for k in nodes.keys() if k}
    entity_names = entity_names or {}

    alias_map = _load_aliases()
    rev_alias = _build_reverse_alias_map(alias_map)
    type_map = _canonical_entity_types()

    # 解析食材
    resolved_ingredients = [
        _resolve_single(i.raw, entity_names, alias_map, rev_alias, type_map, {"Ingredient", "Seasoning"})
        for i in frame.ingredients
    ]

    # 解析技法
    resolved_techniques = [
        _resolve_single(t.raw, entity_names, alias_map, rev_alias, type_map, {"Technique"})
        for t in frame.techniques
    ]

    # 解析味道
    resolved_tastes = [
        _resolve_single(t.raw, entity_names, alias_map, rev_alias, type_map, {"Taste"})
        for t in frame.tastes
    ]

    # 解析菜系
    resolved_cuisines = [
        _resolve_single(c.raw, entity_names, alias_map, rev_alias, type_map, {"Cuisine"})
        for c in frame.cuisines
    ]

    # 解析 dish_text（如果有）
    resolved_dish = frame.dish
    if frame.dish_text and frame.dish is None:
        dish_slot = _resolve_single(frame.dish_text, entity_names, alias_map, rev_alias, type_map, {"Dish"})
        resolved_dish = dish_slot

    return QueryFrame(
        intent=frame.intent,
        source_text=frame.source_text,
        mode=frame.mode,
        dish_text=frame.dish_text,
        dish=resolved_dish,
        dish_candidates=frame.dish_candidates,
        ingredients=resolved_ingredients,
        techniques=resolved_techniques,
        tastes=resolved_tastes,
        cuisines=resolved_cuisines,
        scenario_tags=frame.scenario_tags,
        exclusions=frame.exclusions,
        attribute=frame.attribute,
        resolved_query=frame.resolved_query,
        needs_clarification=frame.needs_clarification,
        clarification_question=frame.clarification_question,
        confidence=frame.confidence,
        reason=frame.reason,
    )


def extract_ingredient_slots_from_source(
    text: str,
    *,
    entity_names: dict[str, set[str]] | None = None,
) -> list[EntitySlot]:
    """从用户原文按别名目录补齐模型遗漏的食材槽位。"""
    source = str(text or "").strip()
    compact = "".join(source.split())
    if not compact:
        return []
    entity_names = entity_names or {}
    allowed = set(entity_names.get("Ingredient", set())) | set(entity_names.get("Seasoning", set()))
    alias_map = _load_aliases()
    candidates: list[tuple[int, int, str]] = []
    for canonical, aliases in alias_map.items():
        if not any(_family_match(canonical, name) or canonical == name for name in allowed):
            continue
        for variant in dict.fromkeys([canonical, *aliases]):
            value = str(variant or "").strip()
            start = compact.find(value) if value else -1
            if start >= 0:
                candidates.append((len(value), start, value))
    for name in allowed:
        value = str(name or "").strip()
        start = compact.find(value) if value else -1
        if start >= 0:
            candidates.append((len(value), start, value))

    selected: list[tuple[int, int, int]] = []
    for length, start, _value in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        end = start + length
        if any(start < other_end and end > other_start for _, other_start, other_end in selected):
            continue
        selected.append((length, start, end))
    slots = [EntitySlot(raw=compact[start:end]) for _, start, end in sorted(selected, key=lambda item: item[1])]
    if "肉" in compact and not any("肉" in slot.raw for slot in slots):
        slots.append(EntitySlot(raw="肉"))
    return slots
