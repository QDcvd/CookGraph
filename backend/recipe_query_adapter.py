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

from backend.answer_composer import compose_plan_result
from backend.query_executor import execute_query_plan, node_names_by_type
from backend.query_plan import build_query_plan
from backend.query_understanding import (
    QueryIntent,
    classify_intent,
    format_ambiguous_query,
    format_non_recipe,
)
from backend.recipe_semantic_retriever import RecipeSemanticMatch, semantic_match_recipe

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(__file__).resolve().parent / "4-V1菜谱查询recipe_query-查询火力.py"
DEFAULT_RECIPE_KG_PATH = PROJECT_ROOT / "config" / "2kg_chem+recipe_fire_12K.pkl"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recipe_aliases.json"
DEFAULT_REVERSE_ENTITY_ALIAS_PATH = PROJECT_ROOT / "config" / "reverse_entity_aliases.json"

_recipe_module = None
_recipe_system = None
_alias_groups_cache: list[set[str]] | None = None
_reverse_entity_alias_cache: dict[str, dict[str, list[str]]] | None = None

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


def _contains_graph_dish_name(query: str, system: Any) -> bool:
    """Whether the user text already contains a standard dish name from the KG."""
    normalized = _normalize_query_text(query)
    if not normalized:
        return False
    for dish_name in sorted(_kg_dish_names(system), key=len, reverse=True):
        normalized_dish_name = _normalize_query_text(dish_name)
        if len(normalized_dish_name) >= 2 and normalized_dish_name in normalized:
            return True
    return False


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

    if _contains_graph_dish_name(text, system):
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
    if _looks_like_forward_attribute_request(text):
        return True
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
        f"我先不查本地菜谱图谱：{reason}\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "match_mode: none\n"
        "intent: out_of_scope\n"
        "web_fallback_allowed: False"
    )


def _looks_like_graph_dish_count_query(query: str) -> bool:
    """Whether the user asks for the number of dishes in the local KG."""
    text = _normalize_query_text(query)
    if not text:
        return False
    count_markers = ["多少", "几道", "几种", "数量", "总数", "一共", "共"]
    graph_markers = ["收录", "图谱", "知识库", "菜谱库", "本地", "当前", "你现在"]
    dish_markers = ["菜", "菜品", "菜谱", "道菜"]
    return (
        any(marker in text for marker in count_markers)
        and any(marker in text for marker in dish_markers)
        and any(marker in text for marker in graph_markers)
    )


def _format_graph_dish_count(system: Any) -> str:
    dish_names = sorted(_kg_dish_names(system))
    count = len(dish_names)
    return (
        f"本地菜谱知识图谱当前收录 {count} 道菜。\n\n"
        "结构化摘要：\n"
        "success: True\n"
        "query_type: graph_meta\n"
        "match_mode: exact\n"
        f"dish_count: {count}\n"
        "web_fallback_allowed: False"
    )


def _format_forward_unknown_miss(query: str, reason: str, semantic_note: str | None = None) -> str:
    """Stable result for a single-recipe miss that should trigger web fallback."""
    output = (
        "我在本地菜谱图谱里暂时没找到这道菜，可以继续帮你联网搜索。\n"
        f"原始问题：{query}\n"
        f"未命中原因：{reason}\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "intent: forward_recipe_query\n"
        "match_mode: none\n"
        "web_fallback_allowed: True"
    )
    if semantic_note:
        output += "\n\n语义召回摘要：\n" + semantic_note
    return output


def _looks_like_forward_attribute_request(query: str) -> bool:
    text = _normalize_query_text(query)
    if not re.search(r"(?:我想做|想做|要做|准备做|学做)", text):
        return False
    attribute_markers = [
        "需要准备",
        "准备哪些",
        "调味料",
        "调料",
        "配菜",
        "食材",
        "材料",
        "用料",
    ]
    return any(marker in text for marker in attribute_markers)


def _extract_forward_attribute_dish(query: str) -> str:
    text = _normalize_query_text(query)
    match = re.search(r"(?:我想做|想做|要做|准备做|学做)(?P<dish>[\u4e00-\u9fff]{2,16}?)(?:需要|要|，|,|。|？|\?|$)", text)
    if match:
        return match.group("dish").strip()
    return _normalize_query_text(query).strip(" ？?。！!")


def _format_forward_unknown_offer(query: str, semantic_note: str | None = None) -> str:
    dish = _extract_forward_attribute_dish(query) or query
    output = (
        f"由于当前查询未能在本地图谱节点中稳定匹配到“{dish}”的相关信息，"
        "因此无法提供具体的调味料和配菜列表。需要我帮你到网上搜一下吗？\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "intent: forward_recipe_query\n"
        "match_mode: none\n"
        "web_search_offer: True\n"
        "web_fallback_allowed: False"
    )
    if semantic_note:
        output += "\n\n语义召回摘要：\n" + semantic_note
    return output


TECHNIQUE_CONFLICT_GROUPS = {
    "凉拌": {"凉拌", "拌"},
    "拌": {"凉拌", "拌"},
    "小炒": {"小炒", "炒", "爆炒"},
    "爆炒": {"爆炒", "炒", "小炒"},
    "清蒸": {"清蒸", "蒸", "蒸制"},
    "蒸": {"清蒸", "蒸", "蒸制"},
    "炖": {"炖", "焖"},
    "红烧": {"红烧", "烧"},
    "白灼": {"白灼"},
}


def _query_technique_terms(query: str) -> set[str]:
    text = _normalize_query_text(query)
    return {term for term in TECHNIQUE_CONFLICT_GROUPS if term in text}


def _result_technique_text(result: dict) -> str:
    parts: list[str] = []
    for key in ("human_readable", "value", "dish_name"):
        value = result.get(key)
        if value:
            parts.append(str(value))
    structured = result.get("structured")
    if isinstance(structured, dict):
        parts.append(json.dumps(structured, ensure_ascii=False))
    dish = result.get("dish")
    if isinstance(dish, dict):
        parts.append(json.dumps(dish, ensure_ascii=False))
    return _normalize_query_text("\n".join(parts))


def _result_conflicts_query_technique(query: str, result: dict) -> bool:
    query_terms = _query_technique_terms(query)
    if not query_terms:
        return False
    result_text = _result_technique_text(result)
    if not result_text:
        return False
    result_terms = {term for term in TECHNIQUE_CONFLICT_GROUPS if term in result_text}
    if not result_terms:
        return False
    for query_term in query_terms:
        allowed = TECHNIQUE_CONFLICT_GROUPS.get(query_term, {query_term})
        if result_terms & allowed:
            return False
    return True


def _excluded_food_terms(query: str) -> list[str]:
    """Extract explicit user exclusions like 不要肥牛 / 不想用肥牛."""
    text = _normalize_query_text(query)
    terms: list[str] = []
    patterns = [
        r"(?:不要|不用|不想用|别用|不放|不加)(?P<term>[\u4e00-\u9fff]{1,8})",
        r"(?P<term>[\u4e00-\u9fff]{1,8})(?:不要|不用|不想用|别用|不放|不加)",
    ]
    stop_words = (
        "给我", "推荐", "三种", "适合", "分别", "做法", "步骤", "重复", "可以", "合并",
        "的", "菜", "菜谱", "做", "炒",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            term = match.group("term").strip()
            for stop in stop_words:
                if stop in term:
                    term = term.split(stop, 1)[0].strip()
            if term and term not in terms:
                terms.append(term)
    return terms


def _result_mentions_any(result: dict, terms: list[str]) -> bool:
    if not terms:
        return False
    haystack = json.dumps(result, ensure_ascii=False)
    return any(term and term in haystack for term in terms)


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


def _load_reverse_entity_aliases() -> dict[str, dict[str, list[str]]]:
    global _reverse_entity_alias_cache
    if _reverse_entity_alias_cache is not None:
        return _reverse_entity_alias_cache
    if DEFAULT_REVERSE_ENTITY_ALIAS_PATH.is_file():
        try:
            data = json.loads(DEFAULT_REVERSE_ENTITY_ALIAS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            normalized: dict[str, dict[str, list[str]]] = {}
            for kind, groups in data.items():
                if not isinstance(groups, dict):
                    continue
                normalized[str(kind)] = {
                    str(key): [str(item) for item in values if item]
                    for key, values in groups.items()
                    if isinstance(values, list)
                }
            _reverse_entity_alias_cache = normalized
            return normalized
    _reverse_entity_alias_cache = {}
    return _reverse_entity_alias_cache


def _entity_kind_label(kind: str) -> str:
    spec = REVERSE_RELATION_SPECS.get(kind) or {}
    return str(spec.get("label") or "")


def _graph_node_aliases(system: Any, kind: str, value: str) -> list[str]:
    label = _entity_kind_label(kind)
    graph_names = _node_names_by_label(system, label)
    if not graph_names:
        return []

    normalized_value = _normalize_reverse_value(value)
    alias_config = _load_reverse_entity_aliases().get(kind, {})
    for canonical, aliases in alias_config.items():
        group = [canonical, *aliases]
        normalized_group = {_normalize_reverse_value(item) for item in group}
        if normalized_value not in normalized_group:
            continue
        filtered = [item for item in group if item in graph_names]
        if filtered:
            return list(dict.fromkeys(filtered))

    if value in graph_names:
        return [value]
    matched = _match_graph_value(system, label, value)
    return [matched] if matched else []


def _dense_match_graph_value(system: Any, label: str, raw_value: str) -> tuple[str | None, str]:
    names = sorted(_node_names_by_label(system, label))
    query = _normalize_reverse_value(raw_value)
    if not names or not query:
        return None, ""
    try:
        import numpy as np
        from backend.recipe_semantic_retriever import _load_model

        model = _load_model()
        embeddings = model.encode(
            names,
            batch_size=16,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        query_embedding = model.encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")[0]
        scores = np.asarray(embeddings @ query_embedding, dtype="float32")
    except Exception as e:
        return None, f"dense_skip={type(e).__name__}: {e}"

    order = np.argsort(-scores)
    best_index = int(order[0])
    second_score = float(scores[int(order[1])]) if len(order) > 1 else 0.0
    best_name = names[best_index]
    best_score = float(scores[best_index])
    margin = best_score - second_score
    debug = f"dense_node={best_name}:{best_score:.3f}; margin={margin:.3f}"
    if best_score >= 0.72 and margin >= 0.08:
        return best_name, debug
    return None, debug


def _resolve_reverse_entity(system: Any, kind: str, raw_value: str) -> tuple[list[str], str, str]:
    label = _entity_kind_label(kind)
    value = _normalize_reverse_value(raw_value)
    if not label or not value:
        return [], raw_value, "empty"

    aliases = _graph_node_aliases(system, kind, value)
    if aliases:
        return aliases, aliases[0], "exact_or_alias"

    matched = _match_graph_value(system, label, value)
    if matched:
        return [matched], matched, "lexical"

    dense_match, dense_debug = _dense_match_graph_value(system, label, value)
    if dense_match:
        return [dense_match], dense_match, f"dense; {dense_debug}"
    return [], raw_value, dense_debug or "not_found"


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


def execute_reverse_query(system: Any, intent: QueryIntent) -> str:
    kind = str(intent.target_type or "")
    spec = REVERSE_RELATION_SPECS.get(kind)
    raw_value = str(intent.target_text or "").strip()
    if not spec or not raw_value:
        return (
            "我还没判断清楚你想按哪种维度查菜，可以再具体一点吗？\n\n"
            "结构化摘要：\n"
            "success: False\n"
            "intent: ambiguous\n"
            "match_mode: none\n"
            "web_fallback_allowed: False"
        )

    aliases, resolved_value, match_mode = _resolve_reverse_entity(system, kind, raw_value)
    if not aliases:
        return (
            "我暂时没在本地菜谱图谱里找到稳定匹配的对象。\n"
            f"查询维度：{spec['display']}\n"
            f"原始对象：{raw_value}\n"
            f"匹配说明：{match_mode}\n\n"
            "结构化摘要：\n"
            "success: False\n"
            "intent: ambiguous\n"
            "match_mode: none\n"
            "web_fallback_allowed: False"
        )

    supplemental: list[tuple[str, str, str]] = []
    if kind == "technique":
        raw_normalized = _normalize_reverse_value(raw_value)
        resolved_normalized = _normalize_reverse_value(resolved_value)
        if raw_normalized and raw_normalized != resolved_normalized:
            ingredient_match = _match_graph_value(system, "Ingredient", raw_normalized)
            seasoning_match = _match_graph_value(system, "Seasoning", raw_normalized)
            if ingredient_match == raw_normalized:
                aliases.append(ingredient_match)
                supplemental.extend([
                    ("USES_AUXILIARY", "Ingredient", "辅料"),
                    ("USES_MAIN_INGREDIENT", "Ingredient", "食材"),
                ])
            if seasoning_match == raw_normalized:
                aliases.append(seasoning_match)
                supplemental.append(("USES_SEASONING", "Seasoning", "调味品"))

    return _query_reverse_relation_local(
        system,
        relation=spec["relation"],
        target_label=spec["label"],
        display=spec["display"],
        value=resolved_value,
        aliases=list(dict.fromkeys(aliases)),
        supplemental_relations=supplemental,
    ) or ""


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

    is_graph_dish_count_query = _looks_like_graph_dish_count_query(text)
    if _looks_like_non_recipe_query(text) and not is_graph_dish_count_query:
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

    if is_graph_dish_count_query:
        return _format_graph_dish_count(system)

    node_names = node_names_by_type(system)
    plan = build_query_plan(text, node_names_by_type=node_names, dish_names=_kg_dish_names(system))
    if plan.supported:
        return compose_plan_result(execute_query_plan(plan, system))

    # ── Query Understanding 层 ──
    dish_names = _kg_dish_names(system)
    intent = classify_intent(text, dish_names=dish_names, kg_system=system)
    if intent.intent == "non_recipe_query":
        return format_non_recipe(text)
    if intent.intent == "greeting":
        # 打招呼/问身份，让 LLM 自行回答，不进菜谱查询
        return ""
    if intent.intent == "ambiguous_query":
        return format_ambiguous_query(intent)
    if intent.intent == "reverse_query":
        return execute_reverse_query(system, intent)
    if intent.intent == "recipe_followup_query":
        # 指代追问：使用 LLM 补全后的 resolved_query 作为查询文本
        if intent.resolved_query:
            text = intent.resolved_query
        # 补全后继续走现有查询链路
    forward_unknown = intent.intent == "forward_unknown_recipe_query"
    if intent.intent in ("forward_recipe_query", "forward_unknown_recipe_query"):
        # 继续走现有查询链路
        pass

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
    if effective_query == text and not _contains_graph_dish_name(text, system):
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
        forward_unknown
        and _result_is_success(result)
        and str(result.get("match_mode") or "").lower() == "fuzzy"
        and _result_conflicts_query_technique(text, result)
        and not (semantic_match is not None and semantic_match.accepted and semantic_match.matched_text)
    ):
        return _format_forward_unknown_offer(
            text,
            (semantic_note + "；" if semantic_note else "")
            + "本地图谱 fuzzy 候选与用户显式烹饪技法不一致，不能当作当前菜谱命中。",
        )

    if (
        forward_unknown
        and _result_is_success(result)
        and str(result.get("match_mode") or "").lower() == "fuzzy"
        and _result_mentions_any(result, _excluded_food_terms(text))
        and not (semantic_match is not None and semantic_match.accepted and semantic_match.matched_text)
    ):
        return _format_forward_unknown_miss(
            text,
            "本地图谱只给出了包含用户明确排除食材的相似菜品，不能当作当前菜谱命中。",
            semantic_note,
        )

    if forward_unknown and _looks_like_forward_attribute_request(text) and not _result_is_success(result):
        return _format_forward_unknown_offer(text, semantic_note)

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

    if (
        forward_unknown
        and _result_is_success(result)
        and _result_conflicts_query_technique(text, result)
        and not (semantic_match is not None and semantic_match.accepted and semantic_match.matched_text and semantic_match.dish_name in text)
    ):
        return _format_forward_unknown_offer(
            text,
            (semantic_note + "；" if semantic_note else "")
            + "本地图谱候选菜与用户显式烹饪技法不一致，不能当作当前菜谱命中。",
        )

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
