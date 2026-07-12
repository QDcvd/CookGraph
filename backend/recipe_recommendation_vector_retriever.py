"""本地菜谱推荐向量召回层。

B 用途专用：原材料搭配推荐、场景推荐。该模块不暴露为 Agent 工具，
只由 recipe_query_tool 内部调用。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recommendation_aliases.json"
DEFAULT_INDEX_PATH = PROJECT_ROOT / "backend" / ".cache" / "recipe_recommendation_vector_index.npz"
DEFAULT_MODEL_PATH = Path(os.getenv("MINICOOK_EMBEDDING_MODEL_DIR") or PROJECT_ROOT / "models" / "gte-large-zh")

SCENARIO_TAG_RULES = {
    "天气热": ["清爽", "开胃", "少油", "凉拌", "酸辣", "快手"],
    "炎热": ["清爽", "开胃", "少油", "凉拌", "酸辣", "快手"],
    "天热": ["清爽", "开胃", "少油", "凉拌", "酸辣", "快手"],
    "夏天": ["清爽", "开胃", "少油", "凉拌"],
    "热天": ["清爽", "开胃", "少油", "凉拌"],
    "清爽": ["清爽", "少油", "清淡"],
    "开胃": ["开胃", "酸辣", "酸甜"],
    "下饭": ["香辣", "麻辣", "爆炒", "重口味", "下饭"],
    "清淡": ["清蒸", "白灼", "少油", "清淡"],
    "快手": ["快手", "步骤少", "短时间"],
    "少油": ["少油", "清淡", "清爽"],
}

GENERIC_AMBIGUOUS_INGREDIENTS = {"肉", "蔬菜", "青菜", "菜", "海鲜"}
WEAK_INGREDIENTS = {"葱", "姜", "蒜", "香菜"}
COMMON_SEASONINGS = {"盐", "油", "食用油", "生抽", "老抽", "酱油", "白糖", "糖", "料酒", "醋", "鸡精", "味精"}


class RecommendationIndexMissingError(FileNotFoundError):
    """Raised when the offline recommendation index has not been built."""


@dataclass(frozen=True)
class RecommendationQuery:
    original_query: str
    core_ingredients: tuple[str, ...] = ()
    weak_ingredients: tuple[str, ...] = ()
    seasonings: tuple[str, ...] = ()
    scenario_tags: tuple[str, ...] = ()
    cuisines: tuple[str, ...] = ()
    tastes: tuple[str, ...] = ()
    techniques: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    needs_clarification: bool = False
    clarification_reason: str = ""


@dataclass(frozen=True)
class RecommendationCandidate:
    dish_name: str
    score: float
    graph_reasons: tuple[str, ...]
    vector_score: float
    matched_core_ingredients: tuple[str, ...] = ()
    matched_weak_ingredients: tuple[str, ...] = ()
    matched_scenario_tags: tuple[str, ...] = ()


_model = None
_index_cache: dict[str, Any] | None = None
_alias_cache: dict[str, dict[str, list[str]]] | None = None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _load_model():
    global _model
    if _model is not None:
        return _model
    if not DEFAULT_MODEL_PATH.is_dir():
        raise FileNotFoundError(f"本地 embedding 模型不存在：{DEFAULT_MODEL_PATH}")
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError("缺少 sentence-transformers，请先安装 sentence-transformers") from e
    _model = SentenceTransformer(str(DEFAULT_MODEL_PATH))
    return _model


def load_recommendation_aliases(path: Path = DEFAULT_ALIAS_PATH) -> dict[str, dict[str, list[str]]]:
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache
    if not path.is_file():
        _alias_cache = {}
        return _alias_cache
    data = json.loads(path.read_text(encoding="utf-8"))
    _alias_cache = data if isinstance(data, dict) else {}
    return _alias_cache


def load_recommendation_index(path: Path | None = None) -> dict:
    """加载 backend/.cache/recipe_recommendation_vector_index.npz；不存在时抛出清晰异常。"""
    global _index_cache
    resolved = path or DEFAULT_INDEX_PATH
    if _index_cache is not None and _index_cache.get("_path") == str(resolved):
        return _index_cache
    if not resolved.is_file():
        raise RecommendationIndexMissingError(
            "推荐向量索引不存在，请先运行：python scripts/build_recommendation_vector_index.py"
        )

    data = np.load(resolved, allow_pickle=False)

    def parse_json_array(field: str) -> list[list[str]]:
        return [json.loads(str(item)) for item in data[field].tolist()]

    index = {
        "_path": str(resolved),
        "version": str(data["version"].item()),
        "dish_names": [str(item) for item in data["dish_names"].tolist()],
        "documents": [str(item) for item in data["documents"].tolist()],
        "embeddings": data["embeddings"].astype("float32"),
        "recommendation_reasons": [str(item) for item in data["recommendation_reasons"].tolist()],
        "main_ingredients": parse_json_array("main_ingredients_json"),
        "auxiliary_ingredients": parse_json_array("auxiliary_ingredients_json"),
        "seasonings": parse_json_array("seasonings_json"),
        "tastes": parse_json_array("tastes_json"),
        "cuisines": parse_json_array("cuisines_json"),
        "techniques": parse_json_array("techniques_json"),
        "meal_times": parse_json_array("meal_times_json"),
        "scenario_tags": parse_json_array("scenario_tags_json"),
    }
    _index_cache = index
    return index


def _alias_group_matches(text: str, group: list[str]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(item) and _normalize_text(item) in normalized for item in group)


def _matched_alias_canonical(text: str, kind: str, aliases: dict[str, dict[str, list[str]]]) -> list[str]:
    matches: list[str] = []
    for canonical, group in aliases.get(kind, {}).items():
        if _alias_group_matches(text, [canonical, *group]):
            matches.append(str(canonical))
    return list(dict.fromkeys(matches))


def normalize_recommendation_query(query: str, aliases: dict | None = None) -> RecommendationQuery:
    """把用户原材料/场景词归一成结构化查询。"""
    alias_map = aliases if isinstance(aliases, dict) else load_recommendation_aliases()
    text = query.strip()
    normalized = _normalize_text(text)

    ingredients = _matched_alias_canonical(text, "ingredient", alias_map)
    seasonings = _matched_alias_canonical(text, "seasoning", alias_map)
    cuisines = _matched_alias_canonical(text, "cuisine", alias_map)
    tastes = _matched_alias_canonical(text, "taste", alias_map)
    techniques = _matched_alias_canonical(text, "technique", alias_map)
    exclusions: list[str] = []
    for marker in ("不想吃辣", "不吃辣", "不要辣", "少辣", "不放辣"):
        if marker in normalized:
            exclusions.append("辣")

    scenario_tags: list[str] = []
    for marker, tags in SCENARIO_TAG_RULES.items():
        if marker in normalized:
            scenario_tags.extend(tags)
    for canonical, group in alias_map.get("scenario", {}).items():
        if _alias_group_matches(text, [canonical, *group]):
            scenario_tags.extend(SCENARIO_TAG_RULES.get(canonical, []))
            scenario_tags.extend(group)

    ambiguous = [item for item in GENERIC_AMBIGUOUS_INGREDIENTS if item in normalized]
    # "青菜" is a real graph ingredient in this project; keep it usable.
    ambiguous = [item for item in ambiguous if item != "青菜"]
    if (
        ambiguous
        and not scenario_tags
        and not any(_matched_alias_canonical(text, kind, alias_map) for kind in ("ingredient", "seasoning"))
    ):
        return RecommendationQuery(
            original_query=text,
            needs_clarification=True,
            clarification_reason=f"“{ambiguous[0]}”范围比较大，请补充具体食材。",
        )

    core_ingredients: list[str] = []
    weak_ingredients: list[str] = []
    for ingredient in ingredients:
        if ingredient in WEAK_INGREDIENTS:
            weak_ingredients.append(ingredient)
        else:
            core_ingredients.append(ingredient)

    # If a seasoning alias is also a common seasoning, never promote it to core.
    seasonings = [item for item in seasonings if item not in core_ingredients]

    return RecommendationQuery(
        original_query=text,
        core_ingredients=tuple(dict.fromkeys(core_ingredients)),
        weak_ingredients=tuple(dict.fromkeys(weak_ingredients)),
        seasonings=tuple(dict.fromkeys(seasonings)),
        scenario_tags=tuple(dict.fromkeys(scenario_tags)),
        cuisines=tuple(dict.fromkeys(cuisines)),
        tastes=tuple(dict.fromkeys(tastes)),
        techniques=tuple(dict.fromkeys(techniques)),
        exclusions=tuple(dict.fromkeys(exclusions)),
    )


def recommendation_query_from_plan(plan: dict[str, Any]) -> RecommendationQuery:
    """从 QueryFrame 生成推荐查询；工具执行阶段不再重新猜用户意图。"""
    ingredients = [str(item).strip() for item in plan.get("ingredients", []) if str(item).strip()]
    weak = [item for item in ingredients if item in WEAK_INGREDIENTS]
    core = [item for item in ingredients if item not in WEAK_INGREDIENTS]
    seasonings = plan.get("seasonings", [])
    cuisines = plan.get("cuisines", []) or ([plan.get("cuisine")] if plan.get("cuisine") else [])
    tastes = plan.get("tastes", []) or ([plan.get("taste")] if plan.get("taste") else [])
    techniques = plan.get("techniques", []) or ([plan.get("technique")] if plan.get("technique") else [])
    return RecommendationQuery(
        original_query=str(plan.get("source_text") or ""),
        core_ingredients=tuple(dict.fromkeys(core)),
        weak_ingredients=tuple(dict.fromkeys(weak)),
        seasonings=tuple(str(item).strip() for item in seasonings if str(item).strip()),
        scenario_tags=tuple(str(item).strip() for item in plan.get("scenario_tags", []) if str(item).strip()),
        cuisines=tuple(str(item).strip() for item in cuisines if str(item).strip()),
        tastes=tuple(str(item).strip() for item in tastes if str(item).strip()),
        techniques=tuple(str(item).strip() for item in techniques if str(item).strip()),
        exclusions=tuple(str(item).strip() for item in plan.get("exclude", []) if str(item).strip()),
    )


def _record_values(index: dict, field: str, idx: int) -> list[str]:
    return list(index.get(field, [])[idx] or [])


def _contains_alias(value: str, candidates: list[str], aliases: dict[str, dict[str, list[str]]], kind: str) -> bool:
    group = aliases.get(kind, {}).get(value, [value])
    normalized_candidates = {_normalize_text(item) for item in candidates}
    return any(_normalize_text(item) in normalized_candidates for item in [value, *group])


def _query_vector_scores(query: str, index: dict) -> np.ndarray:
    model = _load_model()
    query_embedding = model.encode([query], normalize_embeddings=True, show_progress_bar=False).astype("float32")[0]
    return np.asarray(index["embeddings"] @ query_embedding, dtype="float32")


def _rrf_ranks(scores: np.ndarray) -> dict[int, float]:
    order = np.argsort(-scores)
    return {int(idx): 1.0 / (60 + rank) for rank, idx in enumerate(order, start=1)}


def retrieve_recommendations(query: RecommendationQuery, top_k: int = 5) -> list[RecommendationCandidate]:
    """返回排序后的本地图谱推荐候选。"""
    if query.needs_clarification:
        return []

    index = load_recommendation_index()
    aliases = load_recommendation_aliases()
    vector_scores = _query_vector_scores(query.original_query, index)
    vector_rrf = _rrf_ranks(vector_scores)

    candidates: list[RecommendationCandidate] = []
    for idx, dish_name in enumerate(index["dish_names"]):
        main = _record_values(index, "main_ingredients", idx)
        auxiliary = _record_values(index, "auxiliary_ingredients", idx)
        seasonings = _record_values(index, "seasonings", idx)
        tastes = _record_values(index, "tastes", idx)
        cuisines = _record_values(index, "cuisines", idx)
        techniques = _record_values(index, "techniques", idx)
        scenario_tags = _record_values(index, "scenario_tags", idx)

        if query.exclusions:
            searchable = "".join([dish_name, *tastes, *techniques, *scenario_tags])
            if any(exclusion in searchable for exclusion in query.exclusions):
                continue

        matched_core_main = [
            item for item in query.core_ingredients if _contains_alias(item, main, aliases, "ingredient")
        ]
        matched_core_aux = [
            item for item in query.core_ingredients if item not in matched_core_main and _contains_alias(item, auxiliary, aliases, "ingredient")
        ]
        matched_weak = [
            item for item in query.weak_ingredients if _contains_alias(item, auxiliary + main, aliases, "ingredient")
        ]
        matched_seasonings = [
            item for item in query.seasonings if _contains_alias(item, seasonings, aliases, "seasoning")
        ]
        matched_scenarios = [item for item in query.scenario_tags if item in scenario_tags]
        matched_cuisines = [item for item in query.cuisines if _contains_alias(item, cuisines, aliases, "cuisine")]
        matched_tastes = [item for item in query.tastes if _contains_alias(item, tastes, aliases, "taste")]
        matched_techniques = [item for item in query.techniques if _contains_alias(item, techniques, aliases, "technique")]

        core_hit_count = len(set(matched_core_main + matched_core_aux))
        if query.core_ingredients and core_hit_count == 0:
            continue
        if query.scenario_tags and not matched_scenarios and vector_scores[idx] < 0.55:
            continue

        graph_score = 0.0
        graph_reasons: list[str] = []
        if query.core_ingredients:
            all_core_hit = core_hit_count == len(query.core_ingredients)
            graph_score += 100.0 if all_core_hit else 40.0 * core_hit_count
            graph_score += 12.0 * len(matched_core_main)
            graph_score += 5.0 * len(matched_core_aux)
            if all_core_hit:
                graph_reasons.append("命中全部核心食材：" + "、".join(query.core_ingredients))
            elif core_hit_count:
                graph_reasons.append("命中核心食材：" + "、".join(matched_core_main + matched_core_aux))
        if matched_weak:
            graph_score += 1.5 * len(matched_weak)
            graph_reasons.append("命中弱食材：" + "、".join(matched_weak))
        if matched_seasonings:
            graph_score += 0.5 * len(matched_seasonings)
        if matched_scenarios:
            graph_score += 8.0 * len(set(matched_scenarios))
            graph_reasons.append("匹配场景标签：" + "、".join(list(dict.fromkeys(matched_scenarios))[:4]))
        if matched_cuisines:
            graph_score += 10.0 * len(matched_cuisines)
            graph_reasons.append("匹配菜系：" + "、".join(matched_cuisines))
        if matched_tastes:
            graph_score += 6.0 * len(matched_tastes)
            graph_reasons.append("匹配口味：" + "、".join(matched_tastes))
        if matched_techniques:
            graph_score += 5.0 * len(matched_techniques)
            graph_reasons.append("匹配技法：" + "、".join(matched_techniques))

        vector_bonus = vector_rrf.get(idx, 0.0) * 20.0
        final_score = graph_score + vector_bonus
        if final_score <= 0:
            continue
        if not graph_reasons:
            graph_reasons.append(index["recommendation_reasons"][idx])

        candidates.append(
            RecommendationCandidate(
                dish_name=dish_name,
                score=final_score,
                graph_reasons=tuple(graph_reasons),
                vector_score=float(vector_scores[idx]),
                matched_core_ingredients=tuple(dict.fromkeys(matched_core_main + matched_core_aux)),
                matched_weak_ingredients=tuple(dict.fromkeys(matched_weak)),
                matched_scenario_tags=tuple(dict.fromkeys(matched_scenarios)),
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:top_k]


def format_recommendation_answer(query: RecommendationQuery, candidates: list[RecommendationCandidate]) -> str:
    """生成面向用户的推荐文案；召回分数等信息只留在结构化 data 中。"""
    if query.needs_clarification:
        return "我可以帮你搭配菜谱。你手头有哪些具体食材，或者想要什么口味？比如：我有牛肉和青椒，想做一道下饭菜。"

    if not candidates:
        return "我暂时没有找到完全匹配的菜。你可以少限定一个条件，或者告诉我更具体的食材和口味，我再帮你换个方向找找。"

    ingredient_text = "、".join(_display_ingredient(item) for item in query.core_ingredients)
    if query.cuisines:
        intro = f"按你想吃的{query.cuisines[0]}口味，{ingredient_text or '这些条件'}可以试试："
    elif query.scenario_tags:
        intro = f"按“{'、'.join(query.scenario_tags[:2])}”这个方向，我先帮你挑了几道："
    elif ingredient_text:
        intro = f"根据你手头的{ingredient_text}，我先帮你挑了几道："
    else:
        intro = "我先帮你挑了几道，看看有没有合口味的："

    display_limit = min(5, len(candidates))
    lines = [intro, ""]
    for index, candidate in enumerate(candidates[:display_limit], start=1):
        lines.append(f"{index}. {candidate.dish_name}")
        reasons = [_friendly_recommendation_reason(reason) for reason in candidate.graph_reasons[:2]]
        reasons = [reason for reason in reasons if reason]
        if reasons:
            lines.append("   " + "；".join(reasons))
        lines.append("")
    lines.append("想看哪一道的完整做法？直接告诉我菜名就行。")
    return "\n".join(lines).rstrip()


def _display_ingredient(value: str) -> str:
    return str(value or "").replace("猪肉(瘦)", "瘦肉")


def _friendly_recommendation_reason(reason: str) -> str:
    """把检索层理由翻译成用户能快速理解的表达。"""
    text = str(reason or "").strip()
    replacements = (
        ("命中全部核心食材：", "主要食材有"),
        ("命中核心食材：", "用到了"),
        ("命中弱食材：", "还可以搭配"),
        ("匹配菜系：", "属于"),
        ("匹配口味：", "口味偏"),
        ("匹配技法：", "做法是"),
        ("匹配场景标签：", "适合"),
    )
    for old, new in replacements:
        if text.startswith(old):
            return new + _display_ingredient(text[len(old):])
    return text


def recommend_from_query(query: str, top_k: int = 5) -> str:
    parsed = normalize_recommendation_query(query)
    candidates = retrieve_recommendations(parsed, top_k=top_k)
    return format_recommendation_answer(parsed, candidates)
