import unittest
from unittest.mock import patch

from backend.query_plan import build_query_plan


NODE_NAMES = {
    "ingredient": {"土豆", "三黄鸡", "鸡胸肉", "黄牛肉", "猪肉", "鸡蛋"},
    "taste": {"香辣味", "咸鲜味"},
    "cuisine": {"川菜", "湘菜"},
    "technique": {"爆炒", "蒸制"},
}


class QueryPlanTests(unittest.TestCase):
    def test_bare_entity_lookup_uses_all_related_scope(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "entity_lookup",
            "entity_type": "ingredient",
            "entity_value": "土豆",
            "relation_scope": "all_related",
            "confidence": 0.9,
            "reason": "裸实体查询",
        }):
            plan = build_query_plan("土豆", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "entity_lookup")
        self.assertEqual(plan.entity_value, "土豆")
        self.assertEqual(plan.relation_scope, "all_related")

    def test_core_ingredient_question_uses_core_first_scope(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "entity_lookup",
            "entity_type": "ingredient",
            "entity_value": "土豆",
            "relation_scope": "core_first",
            "confidence": 0.9,
            "reason": "食材能做什么菜",
        }):
            plan = build_query_plan("土豆可以做什么菜", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "entity_lookup")
        self.assertEqual(plan.entity_value, "土豆")
        self.assertEqual(plan.relation_scope, "core_first")

    def test_entity_method_short_query_uses_core_first_scope(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "entity_lookup",
            "entity_type": "ingredient",
            "entity_value": "猪肉",
            "relation_scope": "core_first",
            "confidence": 0.86,
            "reason": "短句食材做法等价于查本地图谱相关菜",
        }):
            plan = build_query_plan("猪肉做法", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "entity_lookup")
        self.assertEqual(plan.entity_value, "猪肉")
        self.assertEqual(plan.relation_scope, "core_first")

    def test_dish_like_method_query_does_not_become_entity_lookup(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "forward_recipe_query",
            "entity_type": None,
            "entity_value": None,
            "relation_scope": "all_related",
            "confidence": 0.88,
            "reason": "明确单菜谱做法",
        }):
            plan = build_query_plan("洋葱炒牛肉的做法", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertFalse(plan.supported)

    def test_compound_recommendation_extracts_ingredient_and_taste(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "compound_recommendation",
            "constraints": [
                {"type": "ingredient", "value": "鸡肉"},
                {"type": "taste", "value": "香辣味"},
            ],
            "confidence": 0.9,
            "reason": "食材+口味推荐",
        }):
            plan = build_query_plan("香辣口味的鸡肉有什么推荐嘛", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "compound_recommendation")
        constraints = [(item.type, item.value) for item in plan.constraints]
        self.assertEqual(constraints, [("ingredient", "鸡肉"), ("taste", "香辣味")])

    def test_router_entity_must_validate_against_graph(self):
        with patch("backend.query_plan._call_llm_router", return_value={
            "intent": "entity_lookup",
            "entity_type": "ingredient",
            "entity_value": "不存在的食材",
            "relation_scope": "core_first",
            "confidence": 0.9,
        }):
            plan = build_query_plan("不存在的食材做法", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertFalse(plan.supported)


if __name__ == "__main__":
    unittest.main()
