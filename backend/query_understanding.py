#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query Understanding 层 — 先产出结构化意图，再决定查询路径。

重构目标不是继续增加临时正则，而是建立一个清晰的 Query Understanding 层，
先产出结构化意图，再决定是否进入本地图谱查询、追问或旧正向 parser。

doc/query_understanding_refactor_plan.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# ── 数据类型 ──


@dataclass
class QueryIntent:
    """结构化意图。"""

    intent: Literal[
        "forward_recipe_query",
        "forward_unknown_recipe_query",
        "reverse_query",
        "non_recipe_query",
        "ambiguous_query",
        "legacy_forward_parser",
    ]
    target_type: str | None = None
    target_text: str | None = None
    normalized_text: str | None = None
    relation: str | None = None
    dish_name: str | None = None
    attribute: str | None = None
    confidence: float = 0.0
    reason: str = ""
    candidates: list[dict] | None = None


# ── 配置路径 ──

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIAS_PATH = ROOT / "config" / "recipe_aliases.json"
DEFAULT_ENTITY_ALIAS_PATH = ROOT / "config" / "reverse_entity_aliases.json"

# ── 工具函数 ──


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


# ── 已知的非菜谱问题 ──

EXACT_NON_RECIPE = frozenset({
    "你好", "您好", "嗨", "hi", "hello",
    "你是谁", "你是什么模型", "你能做什么",
})

NON_RECIPE_KEYWOR = frozenset({
    "天气", "几点", "日期", "股票", "新闻", "电影", "音乐", "模型",
})

RECIPE_KEYWOR = frozenset({
    "菜", "菜谱", "做法", "怎么做", "烹饪", "配料", "食材", "调料",
    "火候", "火力", "备菜", "下锅", "炒", "蒸", "煮", "炸", "煎",
    "炖", "拌", "烤",
})

# ── 反向查询模式（结构化） ──

REVERSE_PATTERNS: list[dict[str, Any]] = [
    # ── 明确做法/技法反向，必须早于“用了某食材” ──
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)(?:用了|使用|采用)(?P<v>[一-鿿]{1,12}?)(?:这种)?(?:做法|技法|烹饪方式|方法)", kind="technique"),
    # ── 食材反向（最高优先级，避免被 technique 误截） ──
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)(?:用了|使用|包含|有)(?P<v>[一-鿿]{1,12})(?:这种)?(?:食材|材料)?", kind="ingredient"),
    dict(pattern=r"^(?P<v>[一-鿿]{1,12}?)(?:可以|能|可|能够)(?:用来)?做(?:什么|哪些|啥).{0,4}菜?", kind="ingredient"),
    dict(pattern=r"^(?P<v>[一-鿿]{1,12}?)用来做(?:什么|哪些|啥).{0,4}菜?", kind="ingredient"),
    dict(pattern=r"^(?P<v>[一-鿿]{1,12}?)有多少(?:种)?(?:做法|吃法|菜式)", kind="ingredient"),
    # ── 菜系反向 ──
    dict(pattern=r"^(?:有哪些|有什么|哪些)(?P<v>[一-鿿]{1,8})菜(?:推荐)?$", kind="cuisine"),
    dict(pattern=r"什么(?P<v>[一-鿿]{1,6})菜(?:推荐)?$", kind="generic"),
    # ── 口味反向 ──
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)?(?:是|属于)?(?P<v>[一-鿿]{1,8}味)(?:的)?", kind="taste"),
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)?(?:是|属于)?(?P<v>[一-鿿]{1,8}味)", kind="taste"),
    # ── 技法反向（放在食材/口味之后，避免误截） ──
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)(?:是|属于)?(?P<v>[一-鿿]{1,12}?)(?:做法|技法|烹饪方式|方法)", kind="technique"),
    dict(pattern=r"(?:哪些菜|有哪些菜|有什么菜)(?:是|属于)?(?P<v>[一-鿿]{1,12}?)(?:的)", kind="technique"),
    # ── 通用反向 ──
    dict(pattern=r"有(?:什么|哪些).{0,8}菜", kind="generic"),
    dict(pattern=r"什么(?P<v>[一-鿿]{1,6})菜(?:推荐)?$", kind="generic"),
]

# ── 反向查询图谱规格 ──

REVERSE_RELATION_SPECS: dict[str, dict[str, str]] = {
    "ingredient": {"relation": "USES_MAIN_INGREDIENT", "label": "Ingredient", "display": "食材"},
    "auxiliary":   {"relation": "USES_AUXILIARY",      "label": "Ingredient", "display": "辅料"},
    "seasoning":   {"relation": "USES_SEASONING",       "label": "Seasoning", "display": "调味品"},
    "technique":   {"relation": "USES_TECHNIQUE",       "label": "Technique", "display": "技法"},
    "taste":       {"relation": "HAS_TASTE",            "label": "Taste",     "display": "口味"},
    "cuisine":     {"relation": "BELONGS_TO_CUISINE",   "label": "Cuisine",   "display": "菜系"},
}

# ── 实体级别反向词（短词命中） ──

ENTITY_LEVEL_REVERSE: dict[str, list[str]] = {
    "ingredient": [
        "牛肉", "猪肉", "鸡肉", "鱼肉", "虾", "花甲", "肥牛", "鸡蛋",
        "莲藕", "包菜", "土豆", "茄子", "豆腐", "青菜",
    ],
    "cuisine": ["川菜", "湘菜", "粤菜", "鲁菜", "苏菜", "闽菜", "浙菜", "徽菜"],
    "taste": ["香辣味", "麻辣味", "酸甜味", "酸辣味", "清淡", "咸鲜"],
    "technique": ["蒸制", "爆炒", "炝炒", "红烧", "清蒸", "白灼"],
}

# ── 多类型歧义词 ──

AMBIGUOUS_TERMS: dict[str, list[dict[str, str]]] = {
    "蒜蓉": [
        {"target_type": "ingredient", "target_text": "蒜蓉"},
        {"target_type": "technique", "target_text": "蒜蓉炒"},
    ],
}

# ═══════════════════════════════════════════════
# 意图分类
# ═══════════════════════════════════════════════


def classify_intent(
    text: str,
    *,
    dish_names: set[str] | None = None,
    kg_system: Any = None,
) -> QueryIntent:
    """优先级规则（见 doc/query_understanding_refactor_plan.md）：

    1. 明确非菜谱问题 -> non_recipe_query
    2. 直接命中标准菜名 -> forward_recipe_query
    3. 直接命中标准菜名别名 -> forward_recipe_query
    4. 明确反向模式 -> reverse_query
    5. 短词命中食材/技法/口味/菜系 -> reverse_query
    6. 多类型命中且无法判断 -> ambiguous_query
    7. 其余 -> legacy_forward_parser
    """
    raw = text.strip()
    normalized = _normalize_text(raw)
    if not normalized:
        return QueryIntent(intent="non_recipe_query", reason="空输入")

    # 1. 明确非菜谱问题
    if text in EXACT_NON_RECIPE:
        return QueryIntent(intent="non_recipe_query", reason="精确命中非菜谱词表")
    if any(kw in normalized for kw in NON_RECIPE_KEYWOR) and not any(
        kw in normalized for kw in RECIPE_KEYWOR
    ):
        return QueryIntent(intent="non_recipe_query", reason=f"非菜谱关键词命中: {_matched_keywords(normalized, NON_RECIPE_KEYWOR)}")

    # 2. 直接命中标准菜名
    if dish_names:
        matched = _match_dish_name(raw, dish_names)
        if matched:
            return QueryIntent(
                intent="forward_recipe_query",
                dish_name=matched,
                confidence=0.95,
                reason=f"直接命中标准菜名: {matched}",
            )

    # 3. 别名命中 → forward_recipe_query
    if dish_names:
        matched = _match_alias(raw, dish_names)
        if matched:
            return QueryIntent(
                intent="forward_recipe_query",
                dish_name=matched,
                confidence=0.9,
                reason=f"别名命中: {matched}",
            )

    # 4. 明确反向模式
    reverse_result = _classify_reverse_pattern(normalized, raw, kg_system=kg_system)
    if reverse_result:
        return reverse_result

    # 5. “实体 + 怎么做”只有在实体能精确归并到图谱节点时才算反向。
    entity_how_to = _classify_entity_how_to(normalized, kg_system=kg_system)
    if entity_how_to:
        return entity_how_to

    # 6. 歧义词
    if normalized in AMBIGUOUS_TERMS:
        candidates = AMBIGUOUS_TERMS[normalized]
        return QueryIntent(
            intent="ambiguous_query",
            candidates=candidates,
            confidence=0.5,
            reason=f"多类型歧义词: {normalized}",
        )

    # 7. 短词命中实体。必须是整句短词，不允许从未知菜名中截子串。
    short_result = _classify_short_term(normalized, kg_system=kg_system)
    if short_result:
        return short_result

    # 8. 正向未知单菜谱。比如“红烧排骨怎么做”“麻婆豆腐”。
    if _looks_like_unknown_single_recipe(normalized):
        return QueryIntent(
            intent="forward_unknown_recipe_query",
            confidence=0.65,
            reason="像单道菜谱查询，但未命中本地图谱菜名/别名",
        )

    # 9. 其余 → 旧 parser
    return QueryIntent(
        intent="legacy_forward_parser",
        confidence=0.3,
        reason="未匹配任何特定模式，交旧 parser",
    )


def _matched_keywords(text: str, keywords: frozenset) -> str:
    for kw in sorted(keywords, key=len, reverse=True):
        if kw in text:
            return kw
    return ""


def _match_dish_name(text: str, dish_names: set[str]) -> str | None:
    for name in sorted(dish_names, key=len, reverse=True):
        if name in text:
            return name
    return None


def _dish_alias_variants(canonical: str, alias_map: dict[str, list[str]]) -> set[str]:
    variants = {canonical}
    for _ in range(3):
        before_count = len(variants)
        for value in list(variants):
            for source, targets in alias_map.items():
                if source in value:
                    for target in targets:
                        variants.add(value.replace(source, target))
        if len(variants) == before_count:
            break
    return variants


def _match_alias(text: str, dish_names: set[str]) -> str | None:
    """通过别名反向匹配菜名。

    流程：
    1. 找到文本中所有存在的别名词
    2. 逐一替换为标准名
    3. 执行多轮替换（高优先级的替换可能暴露新的可替换词）
    4. 最终检查替换后文本是否包含标准菜名

    例：
    - "西红柿炒鸡蛋怎么做"
      → "番茄炒鸡蛋怎么做"（西红柿→番茄）
      → "番茄炒蛋怎么做"（鸡蛋→蛋）
      → 包含"番茄炒蛋" ✅
    - "牛肉怎么做" → 替换后仍不包含菜名 ❌
    """
    normalized = _normalize_text(text)
    alias_map = _load_alias_map()
    for dish in sorted(dish_names, key=len, reverse=True):
        for variant in sorted(_dish_alias_variants(dish, alias_map), key=len, reverse=True):
            if len(_normalize_text(variant)) >= 2 and _normalize_text(variant) in normalized:
                return dish
    return None


_alias_map_cache: dict[str, list[str]] | None = None


def _load_alias_map() -> dict[str, list[str]]:
    global _alias_map_cache
    if _alias_map_cache is not None:
        return _alias_map_cache
    alias_map: dict[str, list[str]] = {}
    if DEFAULT_ALIAS_PATH.is_file():
        try:
            data = json.loads(DEFAULT_ALIAS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for canonical, aliases in data.items():
                values = [str(item) for item in aliases or [] if item]
                alias_map[str(canonical)] = values
                for alias in values:
                    alias_map.setdefault(alias, []).append(str(canonical))
                for left in [str(canonical), *values]:
                    alias_map.setdefault(left, [])
                    for right in [str(canonical), *values]:
                        if right != left and right not in alias_map[left]:
                            alias_map[left].append(right)
    _alias_map_cache = alias_map
    return alias_map


def _match_alias_old(text: str, dish_names: set[str]) -> str | None:
    alias_groups = _load_alias_groups()
    normalized = _normalize_text(text)
    for group in alias_groups:
        for dish in sorted(dish_names, key=len, reverse=True):
            if dish not in group:
                continue
            for alias in group:
                if _normalize_text(alias) in normalized:
                    return dish
    return None


def _match_alias_with_rewrite(text: str, dish_names: set[str]) -> str | None:
    alias_map = _load_alias_map()
    variants = {text}
    for _ in range(4):
        before_count = len(variants)
        for value in list(variants):
            for source, targets in alias_map.items():
                if source not in value:
                    continue
                for target in targets:
                    variants.add(value.replace(source, target))
        if len(variants) == before_count:
            break
    normalized_variants = {_normalize_text(item) for item in variants}
    for dish in sorted(dish_names, key=len, reverse=True):
        dish_norm = _normalize_text(dish)
        if any(dish_norm in item for item in normalized_variants):
            return dish
    return None


def _has_reverse_markers(text: str) -> bool:
    """检查文本是否包含反向查询标记。"""
    markers = ["哪些菜", "有哪些菜", "有什么菜", "哪道菜"]
    return any(m in text for m in markers)


def _has_cooking_markers(text: str) -> bool:
    """检查文本是否包含烹饪/做法关键词。"""
    markers = ["怎么做", "做法", "如何做", "烹饪", "怎么做好吃"]
    return any(m in text for m in markers)


def _looks_like_unknown_single_recipe(text: str) -> bool:
    """识别应按“未知单菜谱”处理的问题，而不是反向实体查询。"""
    if _has_reverse_markers(text):
        return False
    if _has_cooking_markers(text):
        return True
    if any(marker in text for marker in ["是什么菜", "介绍一下", "讲讲"]):
        return True
    return bool(re.fullmatch(r"[一-鿿]{2,10}", text))


def _graph_node_names(kg_system: Any, label: str) -> set[str]:
    executor = getattr(kg_system, "executor", None)
    nodes_by_label = getattr(executor, "all_nodes_by_label", None)
    if isinstance(nodes_by_label, dict):
        values = nodes_by_label.get(label)
        if isinstance(values, dict):
            return {str(name) for name in values.keys() if name}
    return set()


def _load_reverse_entity_aliases() -> dict[str, dict[str, list[str]]]:
    try:
        data = json.loads(DEFAULT_ENTITY_ALIAS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, dict[str, list[str]]] = {}
    for kind, mapping in data.items():
        if not isinstance(mapping, dict):
            continue
        clean: dict[str, list[str]] = {}
        for canonical, aliases in mapping.items():
            values = [str(canonical)]
            if isinstance(aliases, list):
                values.extend(str(item) for item in aliases if item)
            clean[str(canonical)] = list(dict.fromkeys(values))
        result[str(kind)] = clean
    return result


def _resolve_entity_exact(kind: str, text: str, kg_system: Any = None) -> str | None:
    spec = REVERSE_RELATION_SPECS.get(kind)
    if not spec:
        return None
    normalized = _normalize_text(text)
    node_names = _graph_node_names(kg_system, spec["label"]) if kg_system is not None else set()
    for name in node_names:
        if _normalize_text(name) == normalized:
            return name

    aliases_by_kind = _load_reverse_entity_aliases()
    for canonical, values in aliases_by_kind.get(kind, {}).items():
        if any(_normalize_text(value) == normalized for value in values):
            return canonical
    return None


def _classify_entity_how_to(normalized: str, kg_system: Any = None) -> QueryIntent | None:
    match = re.fullmatch(r"(?P<v>[一-鿿]{1,12}?)(?:怎么做|怎么做好吃)$", normalized)
    if not match:
        return None
    value = match.group("v")
    resolved = _resolve_entity_exact("ingredient", value, kg_system=kg_system)
    if not resolved:
        return None
    spec = REVERSE_RELATION_SPECS["ingredient"]
    return QueryIntent(
        intent="reverse_query",
        target_type="ingredient",
        target_text=resolved,
        relation=spec["relation"],
        confidence=0.82,
        reason=f"实体做法问法命中ingredient: {resolved}",
    )


_alias_groups_cache: list[set[str]] | None = None


def _load_alias_groups() -> list[set[str]]:
    global _alias_groups_cache
    if _alias_groups_cache is not None:
        return _alias_groups_cache
    groups: list[set[str]] = []
    if DEFAULT_ALIAS_PATH.is_file():
        try:
            data = json.loads(DEFAULT_ALIAS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for canonical, aliases in data.items():
                group = {str(canonical)} | {str(a) for a in (aliases or []) if a}
                group = {g for g in group if g}
                if group:
                    groups.append(group)
    _alias_groups_cache = groups
    return groups


def _classify_reverse_pattern(
    normalized: str,
    raw: str,
    kg_system: Any = None,
) -> QueryIntent | None:
    """按结构化反向模式分类。"""
    for spec in REVERSE_PATTERNS:
        match = re.search(spec["pattern"], normalized)
        if not match:
            continue
        kind = spec["kind"]
        if kind == "generic":
            return QueryIntent(
                intent="reverse_query",
                reason=f"匹配反向模式: {spec['pattern']}",
                confidence=0.85,
            )
        if kind in ("ingredient", "cuisine", "taste", "technique"):
            value = match.group("v") if "v" in match.groupdict() else raw
            spec = REVERSE_RELATION_SPECS.get(kind)
            return QueryIntent(
                intent="reverse_query",
                target_type=kind,
                target_text=value,
                relation=spec["relation"] if spec else None,
                confidence=0.7,
                reason=f"反向模式匹配: {kind}={value}",
            )
    return None


def _classify_short_term(
    normalized: str,
    kg_system: Any = None,
) -> QueryIntent | None:
    """短词命中实体级别反向查询。"""
    for kind, terms in ENTITY_LEVEL_REVERSE.items():
        for term in terms:
            if _normalize_text(term) == normalized:
                spec = REVERSE_RELATION_SPECS.get(kind)
                if spec:
                    return QueryIntent(
                        intent="reverse_query",
                        target_type=kind,
                        target_text=term,
                        relation=spec["relation"],
                        confidence=0.8,
                        reason=f"短词命中{kind}: {term}",
                    )
        resolved = _resolve_entity_exact(kind, normalized, kg_system=kg_system)
        if resolved:
            spec = REVERSE_RELATION_SPECS.get(kind)
            if spec:
                return QueryIntent(
                    intent="reverse_query",
                    target_type=kind,
                    target_text=resolved,
                    relation=spec["relation"],
                    confidence=0.8,
                    reason=f"短词命中图谱{kind}: {resolved}",
                )
    return None


def format_ambiguous_query(intent: QueryIntent) -> str:
    """将歧义意图格式化为结构化的工具输出。"""
    if not intent.candidates:
        return "菜谱查询未执行：查询存在歧义。\n\n结构化摘要：\nsuccess: False\nintent: ambiguous\nweb_fallback_allowed: False"

    lines = [
        "菜谱查询未执行：查询存在歧义。",
        "",
        "候选解释：",
    ]
    for c in intent.candidates:
        lines.append(f"- 如果查「{c['target_text']}」作为{c['target_type']}：请补充说明。")
    lines.extend([
        "",
        "说明：未执行任何图谱查询，请用户明确后再查。",
        "",
        "结构化摘要：",
        "success: False",
        "intent: ambiguous",
        "match_mode: none",
        "web_fallback_allowed: False",
    ])
    return "\n".join(lines)


def format_non_recipe(text: str) -> str:
    """非菜谱问题格式化输出。"""
    return (
        f"菜谱查询未执行：当前问题不是菜谱查询。\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "match_mode: none\n"
        "intent: out_of_scope\n"
        "web_fallback_allowed: False"
    )
