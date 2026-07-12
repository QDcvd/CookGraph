from pathlib import Path

import pytest

from backend.recipe_query_adapter import (
    query_recipe_plan,
)
from backend.recipe_recommendation_vector_retriever import (
    RecommendationIndexMissingError,
    format_recommendation_answer,
    retrieve_recommendations,
    load_recommendation_index,
    normalize_recommendation_query,
)


def test_recommendation_index_missing_has_clear_error(tmp_path: Path):
    with pytest.raises(RecommendationIndexMissingError, match="推荐向量索引不存在"):
        load_recommendation_index(tmp_path / "missing.npz")


def test_generic_meat_needs_clarification():
    query = normalize_recommendation_query("我有肉，可以做什么菜")
    assert query.needs_clarification
    answer = format_recommendation_answer(query, [])
    assert "范围比较大" in answer
    assert "match_mode: needs_clarification" in answer


def test_beef_and_pepper_recommendation_prefers_joint_matches():
    query = normalize_recommendation_query("我有辣椒和牛肉，可以做什么菜")
    candidates = retrieve_recommendations(query, top_k=5)
    assert candidates
    assert all({"牛肉", "辣椒"}.issubset(set(item.matched_core_ingredients)) for item in candidates[:3])


def test_tomato_and_egg_recommendation_finds_tomato_egg_dish():
    query = normalize_recommendation_query("家里只有鸡蛋和番茄，推荐一道")
    candidates = retrieve_recommendations(query, top_k=5)
    assert any("番茄" in item.dish_name and ("蛋" in item.dish_name or "鸡蛋" in item.dish_name) for item in candidates)


def test_hot_weather_recommendation_uses_scenario_tags():
    query = normalize_recommendation_query("今天天气热适合吃什么菜")
    candidates = retrieve_recommendations(query, top_k=5)
    assert candidates
    assert any(set(item.matched_scenario_tags) & {"清爽", "开胃", "少油", "凉拌"} for item in candidates)


def test_recipe_query_tool_uses_recommendation_for_ingredient_request():
    output = query_recipe_plan({
        "intent": "ingredient_combo_query",
        "mode": "combo",
        "ingredients": ["辣椒", "牛肉"],
        "source_text": "我有辣椒和牛肉，可以做什么菜",
    })
    assert output["query_type"] == "recommendation"
    assert output["web_fallback_allowed"] is False


def test_recipe_query_tool_keeps_reverse_lookup_out_of_recommendation():
    output = query_recipe_plan({
        "intent": "reverse_entity_query",
        "mode": "combo",
        "ingredients": ["牛肉"],
        "source_text": "牛肉有多少种做法",
    })
    assert output["query_type"] != "recommendation"
    assert output["ok"] is True
