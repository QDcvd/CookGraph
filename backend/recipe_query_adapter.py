"""MiniCookingAgent-Demo 菜谱知识图谱查询适配器。

将 backend/4-V1菜谱查询recipe_query-查询火力.py 包装为
agent 可调用的工具函数，处理动态加载、缓存、stdout 捕获和异常兜底。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
from pathlib import Path
from typing import Any

from backend.recipe_semantic_retriever import RecipeSemanticMatch, semantic_match_recipe

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(__file__).resolve().parent / "4-V1菜谱查询recipe_query-查询火力.py"
DEFAULT_RECIPE_KG_PATH = PROJECT_ROOT / "config" / "chem+recipe_kg_updated_fire.pkl"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recipe_aliases.json"

_recipe_module = None
_recipe_system = None
_alias_groups_cache: list[set[str]] | None = None

INGREDIENT_GROUPS: dict[str, list[str]] = {
    "牛肉": ["牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"],
    "鱼": ["鱼", "鲈鱼"],
    "鸡肉": ["鸡肉", "鸡腿肉", "三黄鸡", "鸡胸肉", "鸡翅", "鸡翅中"],
    "猪肉": ["猪肉", "猪里脊", "猪里脊肉", "猪前腿肉", "猪大肠", "猪排骨", "排骨"],
    "虾": ["虾", "鲜虾"],
    "鱿鱼": ["鱿鱼", "鲜鱿鱼"],
    "蛤蜊": ["蛤蜊", "花蛤", "花甲"],
    "木耳": ["木耳", "水发木耳"],
    "玉米": ["玉米", "甜玉米", "甜玉米粒"],
    "豆腐": ["豆腐", "老豆腐"],
    "鸡杂": ["鸡杂", "鸡胗", "鸡肝", "鸡心"],
    "鸡翅": ["鸡翅", "鸡翅中"],
    "包菜": ["包菜", "卷心菜", "圆白菜"],
    "西兰花": ["西兰花", "西蓝花"],
    "蒜苔": ["蒜苔", "蒜薹"],
    "土豆": ["土豆", "马铃薯"],
    "山药": ["山药", "淮山"],
    "香菇": ["香菇", "鲜香菇"],
    "肥牛": ["肥牛", "肥牛卷"],
    "年糕": ["年糕", "年糕条"],
    "排骨": ["排骨", "猪排骨"],
    "鸡胸": ["鸡胸", "鸡胸肉"],
}


def _load_recipe_module():
    """动态加载菜谱查询脚本，返回 module 对象。"""
    global _recipe_module
    if _recipe_module is not None:
        return _recipe_module

    if not SCRIPT_PATH.is_file():
        raise FileNotFoundError(f"脚本不存在：{SCRIPT_PATH}")

    spec = importlib.util.spec_from_file_location(
        "recipe_query_script",
        str(SCRIPT_PATH),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载脚本：{SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    # 抑制脚本顶层 print（import 时不会触发 main，但防御性处理）
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)

    _recipe_module = module
    return module


def _get_recipe_system(kg_path: str | None = None) -> Any:
    """创建或返回缓存的 RecipeQuerySystem 实例。"""
    global _recipe_system
    if _recipe_system is not None:
        return _recipe_system

    resolved_path = Path(kg_path or DEFAULT_RECIPE_KG_PATH).resolve()

    module = _load_recipe_module()
    RecipeQuerySystem = module.RecipeQuerySystem

    # 初始化期间捕获 stdout，避免刷屏
    with contextlib.redirect_stdout(io.StringIO()):
        _recipe_system = RecipeQuerySystem(str(resolved_path))

    return _recipe_system


def _looks_like_reverse_recipe_query(query: str) -> bool:
    """识别不应改写成具体菜名的反向查询。"""
    normalized = _normalize_query_text(query)
    if re.search(r"有(?:什么|哪些).{0,8}菜", normalized):
        return True
    if re.search(r"[\u4e00-\u9fff]{1,12}(?:可以|能|可|能够)?(?:用来)?做(?:什么|哪些|啥).{0,4}菜?", normalized):
        return True
    if re.search(r"[\u4e00-\u9fff]{1,12}有多少(?:种)?(?:做法|吃法|菜式)", normalized):
        return True
    reverse_patterns = [
        "哪些菜",
        "哪些菜式",
        "有什么菜",
        "有哪些菜",
        "哪道菜",
    ]
    return any(pattern in query for pattern in reverse_patterns)


def _extract_reverse_ingredient_query(query: str) -> str:
    """Extract the ingredient from open reverse questions like 牛肉可以做什么菜."""
    text = _normalize_query_text(query)
    patterns = [
        r"^(?P<ingredient>[\u4e00-\u9fff]{1,12}?)(?:可以|能|可|能够)(?:用来)?做(?:什么|哪些|啥).{0,4}菜?$",
        r"^(?P<ingredient>[\u4e00-\u9fff]{1,12}?)用来做(?:什么|哪些|啥).{0,4}菜?$",
        r"^(?P<ingredient>[\u4e00-\u9fff]{1,12}?)做(?:什么|哪些|啥).{0,4}菜?$",
        r"^(?P<ingredient>[\u4e00-\u9fff]{1,12}?)有多少(?:种)?(?:做法|吃法|菜式)$",
        r"^(?:有哪些|有什么|哪些)(?P<ingredient>[\u4e00-\u9fff]{1,12})菜(?:推荐)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            ingredient = match.group("ingredient").strip()
            ingredient = re.sub(r"(可以|能|可|能够|用来)$", "", ingredient).strip()
            if ingredient:
                return ingredient
    return ""


def _normalize_query_text(query: str) -> str:
    """Normalize short Chinese user text for intent checks."""
    return re.sub(r"\s+", "", query.lower())


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
                group = {str(canonical)}
                if isinstance(aliases, list):
                    group.update(str(item) for item in aliases if item)
                group = {item for item in group if item}
                if group:
                    groups.append(group)
    _alias_groups_cache = groups
    return groups


def _dish_name_variants(dish_name: str) -> set[str]:
    variants = {dish_name}
    for _ in range(3):
        before_count = len(variants)
        for value in list(variants):
            for group in _load_alias_groups():
                for source in group:
                    if source not in value:
                        continue
                    for target in group:
                        if target != source:
                            variants.add(value.replace(source, target))
        if len(variants) == before_count:
            break
    return variants


def _alias_rewrite_query(query: str, system: Any) -> tuple[str, str | None]:
    """Strong alias rewrite that does not require embedding dependencies."""
    if _looks_like_reverse_recipe_query(query):
        return query, None
    text = str(query or "").strip()
    normalized = _normalize_query_text(text)
    if not normalized:
        return query, None

    for dish_name in sorted(_kg_dish_names(system), key=len, reverse=True):
        variants = sorted(_dish_name_variants(dish_name), key=len, reverse=True)
        for variant in variants:
            if variant == dish_name:
                continue
            normalized_variant = _normalize_query_text(variant)
            if len(normalized_variant) < 2 or normalized_variant not in normalized:
                continue
            rewritten = text.replace(variant, dish_name, 1)
            if rewritten == text:
                rewritten = re.sub(re.escape(variant), dish_name, text, count=1)
            note = f"别名精确改写：命中文本={variant}；标准菜名={dish_name}；改写查询={rewritten}"
            return rewritten, note
    return query, None


def _looks_like_non_recipe_query(query: str) -> bool:
    """Hard guard for obvious non-recipe chatter or general questions."""
    text = _normalize_query_text(query)
    if not text:
        return True

    exact_non_recipe = {
        "你好",
        "您好",
        "嗨",
        "hi",
        "hello",
        "你是谁",
        "你是什么模型",
        "你能做什么",
    }
    if text in exact_non_recipe:
        return True

    non_recipe_keywords = [
        "天气",
        "几点",
        "日期",
        "股票",
        "新闻",
        "电影",
        "音乐",
        "模型",
    ]
    recipe_keywords = [
        "菜",
        "菜谱",
        "做法",
        "怎么做",
        "烹饪",
        "配料",
        "食材",
        "调料",
        "火候",
        "火力",
        "备菜",
        "下锅",
        "炒",
        "蒸",
        "煮",
        "炸",
        "煎",
        "炖",
        "拌",
        "烤",
    ]
    return any(keyword in text for keyword in non_recipe_keywords) and not any(
        keyword in text for keyword in recipe_keywords
    )


def _looks_like_single_recipe_query(query: str) -> bool:
    """Whether a miss can reasonably fall back to web search as one dish."""
    text = _normalize_query_text(query)
    if not text or _looks_like_non_recipe_query(text) or _looks_like_reverse_recipe_query(text):
        return False

    unsupported_open_patterns = [
        "哪些",
        "有哪些",
        "有什么菜",
        "推荐",
        "菜单",
        "适合",
        "不用",
        "不放",
        "能做几道",
        "能做哪些",
        "空气炸锅",
        "小电锅",
    ]
    if any(pattern in text for pattern in unsupported_open_patterns):
        return False

    single_recipe_markers = [
        "怎么做",
        "做法",
        "烹饪方法",
        "是什么菜",
        "介绍一下",
        "讲讲",
        "配料",
        "食材",
        "调料",
        "火候",
        "火力",
        "备菜",
        "下锅",
        "蒸几分钟",
    ]
    if any(marker in text for marker in single_recipe_markers):
        return True

    # A short bare dish name like "麻婆豆腐" is still a single-dish query.
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,10}", text))


def _format_intent_rejection(query: str, reason: str) -> str:
    """Return a structured tool result that will not trigger web fallback."""
    return (
        f"菜谱查询未执行：{reason}\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "match_mode: none\n"
        "intent: out_of_scope\n"
        "web_fallback_allowed: False"
    )


def _kg_dish_names(system: Any) -> set[str]:
    """从现有图谱查询系统中读取标准菜名。"""
    executor = getattr(system, "executor", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if isinstance(dish_nodes, dict):
        return {str(name) for name in dish_nodes.keys() if name}
    return set()


def kg_dish_names(kg_path: str | None = None) -> set[str]:
    """Public helper for deterministic routing against current runtime KG."""
    return _kg_dish_names(_get_recipe_system(kg_path))


def _ingredient_aliases(ingredient: str) -> list[str]:
    key = str(ingredient or "").strip()
    if not key:
        return []
    aliases = INGREDIENT_GROUPS.get(key, [key])
    return list(dict.fromkeys([key, *aliases]))


REVERSE_RELATION_SPECS: dict[str, dict[str, str]] = {
    "ingredient": {
        "relation": "USES_MAIN_INGREDIENT",
        "label": "Ingredient",
        "display": "食材",
    },
    "auxiliary": {
        "relation": "USES_AUXILIARY",
        "label": "Ingredient",
        "display": "辅料",
    },
    "seasoning": {
        "relation": "USES_SEASONING",
        "label": "Seasoning",
        "display": "调味品",
    },
    "technique": {
        "relation": "USES_TECHNIQUE",
        "label": "Technique",
        "display": "技法",
    },
    "taste": {
        "relation": "HAS_TASTE",
        "label": "Taste",
        "display": "口味",
    },
    "cuisine": {
        "relation": "BELONGS_TO_CUISINE",
        "label": "Cuisine",
        "display": "菜系",
    },
}


def _node_names_by_label(system: Any, label: str) -> set[str]:
    executor = getattr(system, "executor", None)
    nodes_by_label = getattr(executor, "all_nodes_by_label", None)
    if isinstance(nodes_by_label, dict):
        nodes = nodes_by_label.get(label, {})
        if isinstance(nodes, dict):
            return {str(name) for name in nodes.keys() if name}
    graph = getattr(executor, "graph", None)
    if graph is None:
        return set()
    return {
        str(attrs.get("name"))
        for _, attrs in graph.nodes(data=True)
        if attrs.get("label") == label and attrs.get("name")
    }


def _normalize_reverse_value(value: str) -> str:
    text = _normalize_query_text(value)
    cleanup_patterns = [
        "这种做法",
        "这种技法",
        "这种烹饪方式",
        "这个做法",
        "这个技法",
        "做法",
        "技法",
        "方式",
        "方法",
        "口味",
        "味道",
        "味的",
        "味",
        "菜系",
        "推荐",
        "的",
        "菜",
    ]
    for pattern in cleanup_patterns:
        text = text.replace(pattern, "")
    return text.strip()


def _match_graph_value(system: Any, label: str, raw_value: str) -> str | None:
    value = _normalize_reverse_value(raw_value)
    if not value:
        return None
    names = sorted(_node_names_by_label(system, label), key=len, reverse=True)
    normalized_names = {name: _normalize_query_text(name) for name in names}

    for name, normalized in normalized_names.items():
        if normalized == value:
            return name
    for name, normalized in normalized_names.items():
        if normalized.endswith(value) or value.endswith(normalized):
            return name
    for name, normalized in normalized_names.items():
        if value in normalized or normalized in value:
            return name
    return None


def _extract_reverse_value_by_kind(query: str, kind: str) -> str:
    text = _normalize_query_text(query)
    if kind == "cuisine":
        match = re.search(r"(?:有什么|有哪些|哪些)?(?P<value>[\u4e00-\u9fff]{1,8}菜)(?:推荐|有哪些|有什么)?$", text)
        return match.group("value") if match else ""
    if kind == "taste":
        match = re.search(r"(?:哪些菜|有哪些菜|有什么菜)?(?:是|属于|有)?(?P<value>[\u4e00-\u9fff]{1,8}味)(?:的)?", text)
        return match.group("value") if match else ""
    if kind == "technique":
        patterns = [
            r"(?:哪些菜|有哪些菜|有什么菜)?(?:用了|使用|采用|是|属于)?(?P<value>[\u4e00-\u9fff]{1,12})(?:这种)?(?:做法|技法|烹饪方式|方法|的)?$",
            r"(?:哪些菜|有哪些菜|有什么菜)?(?:是|属于)?(?P<value>[\u4e00-\u9fff]{1,12})(?:的)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group("value")
    if kind == "ingredient":
        patterns = [
            r"(?:哪些菜|有哪些菜|有什么菜)?(?:用了|使用|包含|有)(?P<value>[\u4e00-\u9fff]{1,12})(?:这种)?(?:食材|材料)?(?:的)?$",
            r"(?P<value>[\u4e00-\u9fff]{1,12}?)(?:可以|能|可|能够)(?:用来)?做(?:什么|哪些|啥).{0,4}菜?$",
            r"(?P<value>[\u4e00-\u9fff]{1,12}?)用来做(?:什么|哪些|啥).{0,4}菜?$",
            r"(?P<value>[\u4e00-\u9fff]{1,12}?)做(?:什么|哪些|啥).{0,4}菜?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group("value")
    return ""


def _query_reverse_relation_local(
    system: Any,
    *,
    relation: str,
    target_label: str,
    display: str,
    value: str,
    aliases: list[str] | None = None,
    supplemental_relations: list[tuple[str, str, str]] | None = None,
) -> str | None:
    executor = getattr(system, "executor", None)
    graph = getattr(executor, "graph", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if graph is None or not isinstance(dish_nodes, dict):
        return None

    names = aliases or [value]
    matched_values = set(names)
    dishes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def collect(edge_relation: str, label: str, relation_display: str, target_names: set[str]) -> None:
        for dish_name, dish_id in dish_nodes.items():
            for _, target_id, edge_data in graph.edges(dish_id, data=True):
                edge_rel = edge_data.get("relation") or edge_data.get("type")
                if edge_rel != edge_relation:
                    continue
                target_node = graph.nodes[target_id]
                if target_node.get("label") != label:
                    continue
                target_name = str(target_node.get("name") or "")
                if target_name not in target_names:
                    continue
                key = (str(dish_name), edge_relation)
                if key in seen:
                    continue
                seen.add(key)
                dishes.append({
                    "dish_name": str(dish_name),
                    "matched_value": target_name,
                    "amount": str(edge_data.get("amount") or ""),
                    "relation_display": relation_display,
                })

    collect(relation, target_label, display, matched_values)
    for extra_relation, extra_label, extra_display in supplemental_relations or []:
        collect(extra_relation, extra_label, extra_display, matched_values)

    value_text = "、".join(names)
    if not dishes:
        return (
            "【本地图谱反向查询结果】\n"
            f"查询维度：{display}\n"
            f"查询值：{value}\n"
            f"归并值：{value_text}\n"
            "未找到本地图谱中明确命中的菜。\n\n"
            "结构化摘要：\n"
            "success: True\n"
            "query_type: reverse\n"
            "match_mode: exact\n"
            "web_fallback_allowed: False"
        )

    lines = [
        "【本地图谱反向查询结果】",
        f"查询维度：{display}",
        f"查询值：{value}",
        f"归并值：{value_text}",
        f"本地图谱中明确命中的菜（共{len(dishes)}道）：",
    ]
    for index, item in enumerate(dishes, start=1):
        detail = item["matched_value"]
        if item["relation_display"] != display:
            detail = f"{item['relation_display']}={detail}"
        if item["amount"]:
            detail = f"{detail}，用量：{item['amount']}"
        lines.append(f"{index}. {item['dish_name']}（{detail}）")
    lines.extend([
        "",
        "说明：以上只来自本地菜谱图谱，未使用联网搜索，也未补充常识菜。",
        "",
        "结构化摘要：",
        "success: True",
        "query_type: reverse",
        "match_mode: exact",
        "web_fallback_allowed: False",
    ])
    return "\n".join(lines)


def _query_deterministic_reverse_local(system: Any, query: str) -> str | None:
    text = _normalize_query_text(query)
    if not _looks_like_reverse_recipe_query(text):
        return None

    cuisine_value = _extract_reverse_value_by_kind(text, "cuisine")
    if cuisine_value:
        matched = _match_graph_value(system, "Cuisine", cuisine_value)
        if matched:
            spec = REVERSE_RELATION_SPECS["cuisine"]
            return _query_reverse_relation_local(
                system,
                relation=spec["relation"],
                target_label=spec["label"],
                display=spec["display"],
                value=matched,
            )

    taste_value = _extract_reverse_value_by_kind(text, "taste")
    if taste_value:
        matched = _match_graph_value(system, "Taste", taste_value)
        if matched:
            spec = REVERSE_RELATION_SPECS["taste"]
            return _query_reverse_relation_local(
                system,
                relation=spec["relation"],
                target_label=spec["label"],
                display=spec["display"],
                value=matched,
            )

    technique_markers = ["技法", "做法", "方式", "方法", "蒸制", "炒制", "爆炒", "炝炒", "蒜蓉"]
    if any(marker in text for marker in technique_markers):
        technique_value = _extract_reverse_value_by_kind(text, "technique")
        matched = _match_graph_value(system, "Technique", technique_value)
        if matched:
            spec = REVERSE_RELATION_SPECS["technique"]
            aliases = [matched]
            normalized_value = _normalize_reverse_value(technique_value)
            supplemental = []
            if normalized_value and normalized_value != _normalize_query_text(matched):
                ingredient_match = _match_graph_value(system, "Ingredient", normalized_value)
                seasoning_match = _match_graph_value(system, "Seasoning", normalized_value)
                if ingredient_match == normalized_value:
                    aliases.append(ingredient_match)
                    supplemental.extend([
                        ("USES_AUXILIARY", "Ingredient", "辅料"),
                        ("USES_MAIN_INGREDIENT", "Ingredient", "食材"),
                    ])
                if seasoning_match == normalized_value:
                    aliases.append(seasoning_match)
                    supplemental.append(("USES_SEASONING", "Seasoning", "调味品"))
            return _query_reverse_relation_local(
                system,
                relation=spec["relation"],
                target_label=spec["label"],
                display=spec["display"],
                value=matched,
                aliases=list(dict.fromkeys(aliases)),
                supplemental_relations=supplemental,
            )

    ingredient_value = _extract_reverse_value_by_kind(text, "ingredient")
    if ingredient_value:
        matched = _match_graph_value(system, "Ingredient", ingredient_value)
        if matched:
            return _query_reverse_ingredient_local(system, ingredient_value)

    return None


def _query_reverse_ingredient_local(system: Any, ingredient: str) -> str | None:
    aliases = _ingredient_aliases(ingredient)
    if not aliases:
        return None

    executor = getattr(system, "executor", None)
    graph = getattr(executor, "graph", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if graph is None or not isinstance(dish_nodes, dict):
        return None

    dishes: list[dict[str, str]] = []
    seen: set[str] = set()
    for dish_name, dish_id in dish_nodes.items():
        for _, target_id, edge_data in graph.edges(dish_id, data=True):
            edge_rel = edge_data.get("relation") or edge_data.get("type")
            if edge_rel != "USES_MAIN_INGREDIENT":
                continue
            target_node = graph.nodes[target_id]
            ingredient_name = str(target_node.get("name") or "")
            if ingredient_name not in aliases:
                continue
            if dish_name in seen:
                continue
            seen.add(dish_name)
            dishes.append({
                "dish_name": str(dish_name),
                "matched_ingredient": ingredient_name,
                "amount": str(edge_data.get("amount") or ""),
            })

    alias_text = "、".join(aliases)
    if not dishes:
        return (
            "【本地图谱反向查询结果】\n"
            f"查询食材：{ingredient}\n"
            f"归并食材：{alias_text}\n"
            "未找到本地图谱中明确以这些食材为主要食材的菜。\n\n"
            "结构化摘要：\n"
            "success: True\n"
            "query_type: reverse_ingredient\n"
            "match_mode: exact\n"
            "web_fallback_allowed: False"
        )

    lines = [
        "【本地图谱反向查询结果】",
        f"查询食材：{ingredient}",
        f"归并食材：{alias_text}",
        f"本地图谱中明确命中的菜（共{len(dishes)}道）：",
    ]
    for index, item in enumerate(dishes, start=1):
        amount = f"（{item['matched_ingredient']}，用量：{item['amount']}）" if item["amount"] else f"（{item['matched_ingredient']}）"
        lines.append(f"{index}. {item['dish_name']}{amount}")
    lines.extend([
        "",
        "说明：以上只来自本地菜谱图谱的主要食材关系，未使用联网搜索，也未补充常识菜。",
        "",
        "结构化摘要：",
        "success: True",
        "query_type: reverse_ingredient",
        "match_mode: exact",
        "web_fallback_allowed: False",
    ])
    return "\n".join(lines)


def _semantic_rewrite_query(query: str, system: Any) -> tuple[str, RecipeSemanticMatch | None, str | None]:
    """用本地 embedding 将自然菜名改写为图谱标准菜名查询。"""
    if _looks_like_reverse_recipe_query(query):
        return query, None, None

    try:
        match = semantic_match_recipe(query, allowed_dish_names=_kg_dish_names(system))
    except Exception as e:
        return query, None, f"混合召回跳过：{type(e).__name__}: {e}"

    if match is None:
        return query, None, None

    candidates = "；".join(f"{name}({score:.3f})" for name, score in match.candidates)
    if match.accepted and not match.matched_text:
        note = (
            "混合召回未改写："
            f"top={match.dish_name} score={match.score:.3f} margin={match.margin:.3f}；"
            "原因=未能在用户问题中定位菜名/别名强证据；"
            f"候选：{candidates}；{match.retrieval_debug}"
        )
        return query, match, note

    if not match.accepted:
        note = (
            "混合召回未改写："
            f"top={match.dish_name} score={match.score:.3f} margin={match.margin:.3f}；"
            f"候选：{candidates}；{match.retrieval_debug}"
        )
        return query, match, note

    note = (
        "混合召回改写："
        f"原问题={query}；标准菜名={match.dish_name}；"
        f"命中文本={match.matched_text or '未定位'}；"
        f"score={match.score:.3f}；margin={match.margin:.3f}；"
        f"改写查询={match.rewritten_query}；候选：{candidates}；"
        f"{match.retrieval_debug}"
    )
    return match.rewritten_query, match, note


def _query_system(system: Any, query: str) -> dict | str:
    """执行图谱查询，并捕获 stdout。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return system.query(query)


def _result_is_success(result: dict) -> bool:
    """判断图谱查询结果是否成功。"""
    if result.get("success") is not None:
        return bool(result.get("success"))
    structured = result.get("structured")
    if isinstance(structured, dict) and structured.get("success") is not None:
        return bool(structured.get("success"))
    human = str(result.get("human_readable") or "")
    failure_markers = ["无法理解的查询格式", "未找到菜品", "未找到", "查询失败"]
    return bool(human.strip()) and not any(marker in human for marker in failure_markers)


def query_recipe_kg(query: str, kg_path: str | None = None) -> str:
    """查询本地菜谱知识图谱，返回适合 agent 使用的字符串结果。

    参数
    ----------
    query : str
        自然语言菜谱问题，例如"西红柿炒鸡蛋怎么做""小炒黄牛肉的火力调节过程"。
    kg_path : str, optional
        知识图谱 pkl 路径，默认使用 DEFAULT_RECIPE_KG_PATH。

    返回
    -------
    str
        适合大模型总结的文本结果，最长 4000 字符。
    """
    text = query.strip()
    if not text:
        return "菜谱查询失败：query 不能为空。"

    if _looks_like_non_recipe_query(text):
        return _format_intent_rejection(text, "当前问题不是单道菜谱查询。")

    # 检查 KG 文件
    resolved_kg = Path(kg_path or DEFAULT_RECIPE_KG_PATH).resolve()
    if not resolved_kg.is_file():
        return f"菜谱查询失败：知识图谱文件不存在：{resolved_kg}"

    try:
        import networkx  # noqa: F401
    except ModuleNotFoundError:
        return "菜谱查询失败：缺少 networkx，请运行 pip install networkx"

    try:
        system = _get_recipe_system(str(resolved_kg))
    except ModuleNotFoundError as e:
        name = getattr(e, "name", str(e))
        return f"菜谱查询失败：缺少依赖模块 {name}，请运行 pip install {name}"
    except FileNotFoundError as e:
        return f"菜谱查询失败：{e}"
    except SystemExit:
        return "菜谱查询失败：查询脚本尝试退出进程，请检查知识图谱路径或配置。"
    except Exception as e:
        return f"菜谱查询失败：{type(e).__name__}: {e}"

    deterministic_reverse_output = _query_deterministic_reverse_local(system, text)
    if deterministic_reverse_output:
        return deterministic_reverse_output

    reverse_ingredient = _extract_reverse_ingredient_query(text)
    if reverse_ingredient:
        reverse_output = _query_reverse_ingredient_local(system, reverse_ingredient)
        if reverse_output:
            return reverse_output

    effective_query, alias_note = _alias_rewrite_query(text, system)
    semantic_match = None
    semantic_note = alias_note
    if effective_query == text:
        effective_query, semantic_match, semantic_note = _semantic_rewrite_query(text, system)

    # 执行查询，捕获 stdout
    try:
        result = _query_system(system, effective_query)
    except SystemExit:
        return "菜谱查询失败：查询脚本尝试退出进程，请检查知识图谱路径或配置。"
    except Exception as e:
        return f"菜谱查询失败：{type(e).__name__}: {e}"

    if not isinstance(result, dict):
        return f"菜谱查询失败：查询返回非字典类型: {type(result).__name__}"

    if (
        semantic_match is not None
        and semantic_match.accepted
        and semantic_match.matched_text
        and effective_query != semantic_match.dish_name
        and not _result_is_success(result)
    ):
        try:
            fallback_result = _query_system(system, semantic_match.dish_name)
        except Exception:
            fallback_result = None
        if isinstance(fallback_result, dict) and _result_is_success(fallback_result):
            result = fallback_result
            fallback_note = f"图谱自校正：改写查询未命中，已退回标准菜名 {semantic_match.dish_name} 查询。"
            semantic_note = f"{semantic_note}；{fallback_note}" if semantic_note else fallback_note

    if _result_is_success(result) and _result_has_empty_payload(result):
        fallback_result = _fallback_to_summary_when_empty(system, result)
        if isinstance(fallback_result, dict) and _result_is_success(fallback_result):
            result = fallback_result
            fallback_note = "图谱自校正：属性查询命中但内容为空，已退回完整档案查询。"
            semantic_note = f"{semantic_note}；{fallback_note}" if semantic_note else fallback_note

    # 优先取 human_readable
    human = result.get("human_readable")
    if isinstance(human, str) and human.strip():
        parts = [human.strip()]

        # 附上少量结构化摘要
        structured = result.get("structured", {})
        summary_parts = []
        if result.get("success") is not None:
            summary_parts.append(f"success: {result['success']}")
        elif structured.get("success") is not None:
            summary_parts.append(f"success: {structured['success']}")
        if result.get("query_type"):
            summary_parts.append(f"query_type: {result['query_type']}")
        if result.get("match_mode"):
            summary_parts.append(f"match_mode: {result['match_mode']}")
        if not _result_is_success(result):
            summary_parts.append(f"web_fallback_allowed: {_looks_like_single_recipe_query(text)}")

        if summary_parts:
            parts.append("结构化摘要：\n" + "\n".join(summary_parts))

        output = "\n\n".join(parts)
    else:
        # 没有 human_readable，返回完整 JSON
        output = json.dumps(result, ensure_ascii=False, indent=2)

    # 限制长度
    if len(output) > 4000:
        output = output[:4000] + "\n...(截断)"

    if semantic_note:
        output += "\n\n语义召回摘要：\n" + semantic_note

    return output


def _result_has_empty_payload(result: dict) -> bool:
    human = str(result.get("human_readable") or "")
    value = str(result.get("value") or "")
    empty_markers = ["无数据", "无数据或未记录", "cooking_process：\n========================================\n无数据"]
    return any(marker in human for marker in empty_markers) or value.strip() in {"", "无数据", "None", "nan"}


def _fallback_to_summary_when_empty(system: Any, result: dict) -> dict | None:
    structured = result.get("structured") if isinstance(result.get("structured"), dict) else {}
    dish_name = ""
    dish = result.get("dish")
    if isinstance(dish, dict):
        dish_name = str(dish.get("matched") or dish.get("original") or "").strip()
    if not dish_name:
        dish_name = str(structured.get("dish_name") or "").strip()
    if not dish_name:
        return None
    try:
        fallback = _query_system(system, dish_name)
    except Exception:
        return None
    return fallback if isinstance(fallback, dict) else None
