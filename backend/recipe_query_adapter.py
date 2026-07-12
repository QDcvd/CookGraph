"""V2 菜谱查询适配器。

本模块只接收 query_router 生成的结构化 plan，不解析用户自然语言。
所有工具结果都返回统一的 JSON 兼容 dict。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.recipe_recommendation_vector_retriever import (
    RecommendationIndexMissingError,
    recommendation_query_from_plan,
    format_recommendation_answer,
    retrieve_recommendations,
)
from backend.recipe_relation_vector_retriever import search_relation_vectors
from backend.tool_result import error_result, make_tool_result

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECIPE_KG_PATH = PROJECT_ROOT / "config" / "2kg_chem+recipe_fire_12K.pkl"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recipe_aliases.json"

_recipe_v2_system: Any = None
_recipe_v2_path: Path | None = None


def _get_recipe_v2_system(kg_path: str | None = None) -> Any:
    """创建或复用当前知识图谱实例。"""
    global _recipe_v2_system, _recipe_v2_path
    resolved_path = Path(kg_path or DEFAULT_RECIPE_KG_PATH).resolve()
    if _recipe_v2_system is not None and _recipe_v2_path == resolved_path:
        return _recipe_v2_system

    from backend.recipe_query_v2 import RecipeQuerySystem

    if not resolved_path.is_file():
        raise FileNotFoundError(str(resolved_path))
    _recipe_v2_system = RecipeQuerySystem(str(resolved_path))
    _recipe_v2_path = resolved_path
    return _recipe_v2_system


def kg_dish_names(kg_path: str | None = None) -> set[str]:
    """返回当前 V2 图谱中的标准菜名。"""
    system = _get_recipe_v2_system(kg_path)
    dishes = getattr(getattr(system, "executor", None), "dish_nodes", {})
    return {str(name) for name in dishes if name}


def kg_entity_names(kg_path: str | None = None) -> dict[str, set[str]]:
    """返回当前 V2 图谱的实体节点名。"""
    system = _get_recipe_v2_system(kg_path)
    nodes_by_label = getattr(getattr(system, "executor", None), "all_nodes_by_label", {})
    result: dict[str, set[str]] = {}
    if isinstance(nodes_by_label, dict):
        for label, nodes in nodes_by_label.items():
            if isinstance(nodes, dict):
                result[str(label)] = {str(name) for name in nodes if name}
    return result


def _normalize_v2_field(field: Any, attribute: Any = None) -> str | None:
    raw = str(field or attribute or "").strip()
    if not raw:
        return None
    return {
        "full_recipe": None,
        "method": "cooking_method_desc",
        "cooking_process": "cooking_process",
        "cooking_method": "cooking_method_desc",
        "prep": "prep_process",
        "tips": "cooking_tips",
        "fire": "fire_control_process",
        "ingredients": None,
        "seasonings": None,
        "techniques": None,
        "existence": None,
        "count": None,
    }.get(raw, raw)


def _dish_alias_candidates(dish: str) -> list[str]:
    """Return configured graph-node candidates for field-aware disambiguation."""
    if not dish or not DEFAULT_ALIAS_PATH.is_file():
        return [dish] if dish else []
    try:
        payload = json.loads(DEFAULT_ALIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [dish]
    if not isinstance(payload, dict):
        return [dish]
    candidates = [dish]
    for canonical, aliases in payload.items():
        values = [str(canonical), *(str(alias) for alias in (aliases or []))]
        if dish not in values:
            continue
        for value in values:
            if value not in candidates:
                candidates.append(value)
    return candidates


def _has_field_value(result: dict, field: str | None) -> bool:
    if not field:
        return bool(result.get("success"))
    value = result.get("value")
    return value is not None and str(value).strip() not in {"", "无数据", "None", "null"}


def _query_type(mode: str, intent: str) -> str:
    if intent == "graph_meta_query":
        return "graph_meta"
    if intent == "dish_existence_query":
        return "dish_existence"
    if intent == "dish_detail_query":
        return "dish_detail"
    if intent in {"ingredient_combo_query", "scenario_recommendation_query"}:
        return "recommendation"
    return mode or "recipe_query"


def _relation_field_for_plan(plan: dict) -> list[str]:
    field = str(plan.get("field") or plan.get("attribute") or "").strip()
    return {
        "fire": ["fire_control_process"],
        "prep": ["prep_process"],
        "cooking_process": ["cooking_process"],
        "method": ["cooking_method_desc", "cooking_process"],
        "tips": ["cooking_tips"],
    }.get(field, [])


def _relation_vector_result(plan: dict, system: Any) -> dict | None:
    """按 plan.field 检索关系文本，不在向量层自行判断用户意图。"""
    fields = _relation_field_for_plan(plan)
    dish = str(plan.get("dish") or "").strip()
    if not fields or not dish:
        return None
    try:
        matches = search_relation_vectors(
            str(plan.get("source_text") or dish),
            system,
            dish_names=[dish],
            fields=fields,
            top_k=int(os.getenv("RECIPE_RELATION_VECTOR_TOP_K", "3")),
        )
    except Exception:
        return None
    if not matches:
        return None
    data = {
        "dish": dish,
        "field": str(plan.get("field") or plan.get("attribute") or ""),
        "matches": [
            {
                "dish_name": item.dish_name,
                "field": item.field,
                "field_label": item.field_label,
                "text": item.text,
                "score": item.score,
            }
            for item in matches
        ],
    }
    content = matches[0].text.strip()
    return make_tool_result(
        tool="recipe_query_tool",
        query_type="relation_vector",
        ok=True,
        source="local_kg_relation_vector",
        data=data,
        message=f"根据本地菜谱图谱，{dish}的{matches[0].field_label}如下：\n{content}",
        meta={"match_mode": "vector", "score": matches[0].score},
    )


def _execute_v2_recommendation(plan: dict) -> dict | None:
    source_text = str(plan.get("source_text") or "").strip()
    if not source_text:
        return None
    try:
        parsed = recommendation_query_from_plan(plan)
        if parsed.needs_clarification:
            return make_tool_result(
                tool="recipe_query_tool",
                query_type="recommendation",
                ok=False,
                source="local_kg",
                message=format_recommendation_answer(parsed, []),
                meta={"match_mode": "needs_clarification"},
            )
        candidates = retrieve_recommendations(parsed, top_k=int(plan.get("limit") or 5))
    except (RecommendationIndexMissingError, FileNotFoundError, ModuleNotFoundError):
        return None
    if not candidates:
        return None
    return make_tool_result(
        tool="recipe_query_tool",
        query_type="recommendation",
        ok=True,
        source="local_kg",
        data={
            "candidates": [
                {
                    "dish_name": item.dish_name,
                    "score": item.score,
                    "graph_reasons": list(item.graph_reasons),
                    "vector_score": item.vector_score,
                    "matched_core_ingredients": list(item.matched_core_ingredients),
                }
                for item in candidates
            ],
        },
        message=format_recommendation_answer(parsed, candidates),
        meta={"match_mode": "hybrid_rrf"},
    )


def query_recipe_plan(plan: dict, kg_path: str | None = None) -> dict:
    """执行结构化 plan，返回统一 JSON 兼容结果。"""
    if not isinstance(plan, dict):
        return error_result(
            tool="recipe_query_tool",
            query_type="invalid_plan",
            code="PLAN_NOT_OBJECT",
            message="菜谱查询参数无效：plan 必须是对象。",
            detail=f"收到 {type(plan).__name__}",
            source="local_kg",
        )

    mode = str(plan.get("mode") or "dish")
    intent = str(plan.get("intent") or "dish_detail_query")
    try:
        system = _get_recipe_v2_system(kg_path)
    except FileNotFoundError as exc:
        return error_result(
            tool="recipe_query_tool",
            query_type="system_error",
            code="KG_NOT_FOUND",
            message="本地菜谱知识图谱文件不存在。",
            detail=str(exc),
            source="local_kg",
        )
    except Exception as exc:
        return error_result(
            tool="recipe_query_tool",
            query_type="system_error",
            code="KG_LOAD_FAILED",
            message="本地菜谱知识图谱加载失败。",
            detail=f"{type(exc).__name__}: {exc}",
            source="local_kg",
        )

    if intent == "graph_meta_query":
        count = len(getattr(system.executor, "dish_nodes", {}) or {})
        return make_tool_result(
            tool="recipe_query_tool",
            query_type="graph_meta",
            ok=True,
            source="local_kg",
            data={"dish_count": count},
            message=f"本地菜谱知识图谱当前收录 {count} 道菜。",
            meta={"match_mode": "exact"},
        )

    if intent in {"ingredient_combo_query", "scenario_recommendation_query"}:
        recommendation = _execute_v2_recommendation(plan)
        if recommendation is not None:
            return recommendation

    try:
        if mode == "combo" or intent in {"ingredient_combo_query", "reverse_entity_query", "scenario_recommendation_query"}:
            result = system.query_combo(
                ingredients=plan.get("ingredients"),
                technique=plan.get("technique"),
                taste=plan.get("taste"),
                cuisine=plan.get("cuisine"),
                exclude=plan.get("exclude"),
                limit=plan.get("limit", 20),
            )
        elif mode == "missing" or intent == "missing_ingredients_query":
            dish = str(plan.get("dish") or "").strip()
            ingredients = plan.get("ingredients") or []
            if not dish or not ingredients:
                return error_result(
                    tool="recipe_query_tool",
                    query_type="invalid_plan",
                    code="MISSING_FIELDS",
                    message="缺失食材查询需要菜名和已有食材。",
                    source="local_kg",
                )
            result = system.query_missing(dish_name=dish, ingredients=ingredients)
        else:
            dish = str(plan.get("dish") or "").strip()
            if not dish:
                return error_result(
                    tool="recipe_query_tool",
                    query_type="invalid_plan",
                    code="DISH_REQUIRED",
                    message="菜谱查询需要明确的菜名。",
                    source="local_kg",
                )
            if intent == "dish_existence_query":
                dish_id, matched, score = system.executor.find_dish(dish)
                if not dish_id:
                    return make_tool_result(
                        tool="recipe_query_tool",
                        query_type="dish_existence",
                        ok=False,
                        source="local_kg",
                        data={"dish": dish, "candidates": plan.get("dish_candidates", [])},
                        message=f"本地图谱暂时没有收录「{dish}」。",
                        web_fallback_allowed=True,
                        meta={"match_mode": "none"},
                    )
                return make_tool_result(
                    tool="recipe_query_tool",
                    query_type="dish_existence",
                    ok=True,
                    source="local_kg",
                    data={"dish": matched, "score": score},
                    message=f"本地菜谱图谱有收录「{matched}」。",
                    meta={"match_mode": "exact" if score >= 1.0 else "fuzzy", "confidence": score},
                )
            normalized_field = _normalize_v2_field(plan.get("field"), plan.get("attribute"))
            result = system.query_dish(
                dish_name=dish,
                field=normalized_field,
                show_ingredients=bool(plan.get("show_ingredients") or plan.get("show_all")),
                show_techniques=bool(plan.get("show_techniques") or plan.get("show_all")),
                show_seasonings=bool(plan.get("show_seasonings") or plan.get("show_all")),
                show_all=bool(plan.get("show_all")),
            )
            if result.get("success") and normalized_field and not _has_field_value(result, normalized_field):
                # 同义菜名可能对应多个图谱节点；只在目标字段缺失时，
                # 按配置中的候选节点寻找有该字段的实体，避免丢失有效关系。
                for candidate in _dish_alias_candidates(dish)[1:]:
                    alternate = system.query_dish(candidate, field=normalized_field)
                    if alternate.get("success") and _has_field_value(alternate, normalized_field):
                        alternate["alias_resolution"] = {
                            "requested_dish": dish,
                            "matched_dish": alternate.get("dish_name"),
                            "reason": "requested node lacks requested field",
                        }
                        result = alternate
                        break
    except Exception as exc:
        return error_result(
            tool="recipe_query_tool",
            query_type="execution_error",
            code="QUERY_EXECUTION_FAILED",
            message="本地菜谱查询执行失败。",
            detail=f"{type(exc).__name__}: {exc}",
            source="local_kg",
        )

    if not isinstance(result, dict):
        return error_result(
            tool="recipe_query_tool",
            query_type="invalid_result",
            code="RESULT_NOT_OBJECT",
            message="本地菜谱查询返回了无效结果。",
            detail=f"返回类型：{type(result).__name__}",
            source="local_kg",
        )

    success = bool(result.get("success"))
    if not success and mode == "dish":
        vector_result = _relation_vector_result(plan, system)
        if vector_result is not None:
            return vector_result

    message = str(result.get("human_readable") or "").strip()
    if not message:
        message = "本地菜谱查询完成。" if success else "本地图谱没有找到符合条件的结果。"
    query_type = _query_type(mode, intent)
    return make_tool_result(
        tool="recipe_query_tool",
        query_type=query_type,
        ok=success,
        source="local_kg",
        data=result,
        message=message,
        web_fallback_allowed=(not success and intent in {"dish_detail_query", "dish_existence_query"}),
        meta={
            "match_mode": "fuzzy" if result.get("is_fuzzy") else ("exact" if success else "none"),
            "count": result.get("count"),
            "plan": plan,
        },
    )
