from __future__ import annotations

from backend import query_router
from backend.entity_resolver import resolve
from backend.query_understanding import EntitySlot, QueryFrame, _validate_query_frame
from backend.recipe_query_v2 import RecipeQuerySystem, DEFAULT_KG_PATH
from backend.tool_result_policy import render_terminal_recipe_failure
from backend.tool_result import make_tool_result, serialize_tool_result


def test_explicit_ingredient_slots_override_missing_followup_context():
    frame = _validate_query_frame(
        {
            "intent": "ingredient_combo_query",
            "source_text": "我有辣椒和牛肉，可以煮什么菜",
            "raw_slots": {"ingredients": ["辣椒", "牛肉"]},
            "followup": {"is_followup": True, "requires_context": True},
            "confidence": 0.95,
        },
        followup_requires_context=False,
    )

    assert frame.needs_clarification is False
    assert frame.intent == "ingredient_combo_query"
    assert [slot.raw for slot in frame.ingredients] == ["辣椒", "牛肉"]


def test_router_does_not_turn_explicit_combo_into_clarification(monkeypatch):
    frame = QueryFrame(
        intent="ingredient_combo_query",
        source_text="我有辣椒和牛肉，可以煮什么菜",
        ingredients=[EntitySlot(raw="辣椒"), EntitySlot(raw="牛肉")],
        needs_clarification=True,
        clarification_question="你是在追问上一道菜吗？请告诉我菜名。",
        confidence=0.95,
    )
    monkeypatch.setattr(query_router, "classify_v2", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(query_router, "kg_entity_names", lambda: {"Ingredient": {"辣椒", "牛肉"}})
    monkeypatch.setattr(query_router, "kg_dish_names", lambda: set())

    action = query_router.route_query(frame.source_text, history=[])

    assert action.action == "tool"
    assert action.tool_name == "recipe_query_tool"
    assert action.plan["mode"] == "combo"


def test_recommendation_alias_resolves_thin_meat_to_graph_entity():
    frame = QueryFrame(
        intent="ingredient_combo_query",
        source_text="红萝卜、土豆、瘦肉可以煮什么",
        ingredients=[EntitySlot(raw="红萝卜"), EntitySlot(raw="土豆"), EntitySlot(raw="瘦肉")],
        confidence=0.95,
    )
    resolved = resolve(
        frame,
        entity_names={"Ingredient": {"红萝卜", "土豆", "猪肉(瘦)"}},
    )

    assert resolved.ingredients[-1].canonical == "猪肉(瘦)"
    assert resolved.ingredients[-1].match_mode == "alias"


def test_generic_meat_term_is_not_silently_resolved_to_one_species():
    frame = QueryFrame(
        intent="ingredient_combo_query",
        source_text="辣椒和肉可以煮什么",
        ingredients=[EntitySlot(raw="辣椒"), EntitySlot(raw="肉")],
        confidence=0.95,
    )
    resolved = resolve(
        frame,
        entity_names={"Ingredient": {"干辣椒", "猪肉(瘦)", "牛肉(肥瘦)"}},
    )

    assert resolved.ingredients[-1].canonical is None
    assert resolved.ingredients[-1].match_mode == "ambiguous"


def test_generic_egg_term_is_not_silently_resolved_to_chicken_egg():
    frame = QueryFrame(
        intent="ingredient_combo_query",
        source_text="我有蛋，可以做什么",
        ingredients=[EntitySlot(raw="蛋")],
        confidence=0.95,
    )
    resolved = resolve(
        frame,
        entity_names={"Ingredient": {"鸡蛋", "鸭蛋"}},
    )

    assert resolved.ingredients[-1].canonical is None
    assert resolved.ingredients[-1].match_mode == "ambiguous"


def test_generic_egg_term_asks_chicken_or_duck_egg():
    frame = QueryFrame(
        intent="ambiguous_query",
        source_text="蛋",
        ingredients=[EntitySlot(raw="蛋", match_mode="ambiguous")],
        confidence=0.95,
    )
    action = query_router._action_from_frame(frame)

    assert action.action == "content"
    assert "鸡蛋还是鸭蛋" in (action.content or "")


def test_pork_family_alias_does_not_choose_pork_floss():
    frame = QueryFrame(
        intent="ingredient_combo_query",
        source_text="辣椒和猪肉可以煮什么",
        ingredients=[EntitySlot(raw="辣椒"), EntitySlot(raw="猪肉")],
        confidence=0.95,
    )
    resolved = resolve(
        frame,
        entity_names={"Ingredient": {"干辣椒", "猪肉(瘦)", "猪肉(肥瘦)", "猪肉松"}},
    )

    assert resolved.ingredients[-1].canonical == "猪肉"
    assert resolved.ingredients[-1].match_mode == "family"


def test_generic_meat_inside_an_explicit_dish_name_does_not_trigger_clarification():
    frame = QueryFrame(
        intent="dish_detail_query",
        source_text="蒜苔炒肉怎么做",
        dish=EntitySlot(raw="蒜苔炒肉", canonical="蒜苔炒肉", entity_type="Dish", match_mode="exact", confidence=1.0),
        ingredients=[EntitySlot(raw="蒜苔"), EntitySlot(raw="肉", match_mode="ambiguous")],
        confidence=0.95,
    )
    action = query_router._action_from_frame(frame)

    assert action.action == "tool"
    assert action.plan["dish"] == "蒜苔炒肉"
    assert action.pending_clarification is None


def test_combo_results_are_ranked_before_limit():
    system = RecipeQuerySystem(DEFAULT_KG_PATH)
    result = system.query_combo(["干辣椒", "牛肉"], limit=20)

    assert result["success"] is True
    assert result["count"] >= len(result["dishes"])
    assert result["dishes"]
    assert "match_score" in result["dishes"][0]


def test_failed_recipe_result_cannot_become_an_invented_recipe():
    answer = render_terminal_recipe_failure(
        "红萝卜、土豆、瘦肉可以煮什么",
        [
            {
                "tool_name": "recipe_query_tool",
                "args": {"plan": {"mode": "combo", "ingredients": ["红萝卜", "土豆", "猪肉(瘦)"]}},
                "content": serialize_tool_result(make_tool_result(
                    tool="recipe_query_tool",
                    query_type="combo",
                    ok=False,
                    source="local_kg",
                    message="本地图谱里没有找到同时满足这些条件的菜。",
                )),
            }
        ],
    )

    assert "没有找到同时满足" in answer
    assert "不会把推测出来的菜名" in answer
