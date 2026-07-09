import unittest

from backend.query_plan import build_query_plan


NODE_NAMES = {
    "ingredient": {"土豆", "三黄鸡", "鸡胸肉", "黄牛肉"},
    "taste": {"香辣味", "咸鲜味"},
    "cuisine": {"川菜", "湘菜"},
    "technique": {"爆炒", "蒸制"},
}


class QueryPlanTests(unittest.TestCase):
    def test_bare_entity_lookup_uses_all_related_scope(self):
        plan = build_query_plan("土豆", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "entity_lookup")
        self.assertEqual(plan.entity_value, "土豆")
        self.assertEqual(plan.relation_scope, "all_related")

    def test_core_ingredient_question_uses_core_first_scope(self):
        plan = build_query_plan("土豆可以做什么菜", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "entity_lookup")
        self.assertEqual(plan.entity_value, "土豆")
        self.assertEqual(plan.relation_scope, "core_first")

    def test_compound_recommendation_extracts_ingredient_and_taste(self):
        plan = build_query_plan("香辣口味的鸡肉有什么推荐嘛", node_names_by_type=NODE_NAMES, dish_names=set())

        self.assertTrue(plan.supported)
        self.assertEqual(plan.plan_type, "compound_recommendation")
        constraints = [(item.type, item.value) for item in plan.constraints]
        self.assertEqual(constraints, [("ingredient", "鸡肉"), ("taste", "香辣味")])


if __name__ == "__main__":
    unittest.main()
