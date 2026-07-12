from backend.recipe_query_adapter import _relation_field_for_plan


def test_relation_vector_field_is_selected_from_structured_plan():
    assert _relation_field_for_plan({"field": "fire"}) == ["fire_control_process"]
    assert _relation_field_for_plan({"field": "prep"}) == ["prep_process"]
    assert _relation_field_for_plan({"field": "cooking_process"}) == ["cooking_process"]
    assert _relation_field_for_plan({"field": "method"}) == ["cooking_method_desc", "cooking_process"]


def test_recommendation_plan_does_not_select_relation_fields():
    assert _relation_field_for_plan({"intent": "ingredient_combo_query", "mode": "combo"}) == []
