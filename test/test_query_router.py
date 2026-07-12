import unittest
from unittest.mock import patch

from backend.query_router import route_query
from backend.query_understanding import EntitySlot, QueryFrame


class QueryRouterV2Test(unittest.TestCase):
    def _route(self, frame: QueryFrame, history: list[dict] | None = None):
        with (
            patch("backend.query_router.classify_v2", return_value=frame),
            patch("backend.query_router.kg_dish_names", return_value={"番茄炒蛋", "清蒸牛肉"}),
            patch(
                "backend.query_router.kg_entity_names",
                return_value={"Ingredient": {"牛肉", "辣椒"}, "Dish": {"番茄炒蛋", "清蒸牛肉"}},
            ),
        ):
            return route_query(frame.source_text, history or [])

    def test_greeting_is_direct_chat(self):
        action = self._route(QueryFrame(
            intent="greeting",
            source_text="你好",
            confidence=1.0,
            reason="打招呼",
        ))
        self.assertEqual(action.action, "direct_chat")

    def test_dish_frame_becomes_structured_plan(self):
        action = self._route(QueryFrame(
            intent="dish_detail_query",
            source_text="番茄炒蛋怎么做",
            dish=EntitySlot(raw="番茄炒蛋", canonical="番茄炒蛋", entity_type="Dish", match_mode="exact", confidence=1.0),
            attribute="cooking_process",
            confidence=0.98,
        ))
        self.assertEqual(action.action, "tool")
        self.assertEqual(action.tool_name, "recipe_query_tool")
        self.assertEqual(action.plan["mode"], "dish")
        self.assertEqual(action.plan["dish"], "番茄炒蛋")
        self.assertEqual(action.plan["field"], "cooking_process")
        self.assertNotIn("query", action.plan)

    def test_combo_frame_becomes_recipe_plan(self):
        action = self._route(QueryFrame(
            intent="ingredient_combo_query",
            source_text="我有辣椒和牛肉，可以做什么菜",
            ingredients=[EntitySlot(raw="辣椒"), EntitySlot(raw="牛肉")],
            confidence=0.95,
        ))
        self.assertEqual(action.action, "tool")
        self.assertEqual(action.plan["mode"], "combo")
        self.assertEqual(action.plan["ingredients"], ["辣椒", "牛肉"])

    def test_followup_frame_uses_context_resolved_dish(self):
        action = self._route(QueryFrame(
            intent="recipe_followup_query",
            source_text="那这道菜的火力怎么样",
            dish=EntitySlot(raw="清蒸牛肉", canonical="清蒸牛肉", entity_type="Dish", match_mode="context", confidence=0.95),
            attribute="fire",
            confidence=0.95,
            reason="继承当前会话菜品",
        ))
        self.assertEqual(action.action, "tool")
        self.assertEqual(action.plan["dish"], "清蒸牛肉")
        self.assertEqual(action.plan["field"], "fire")

    def test_missing_context_is_clarification(self):
        action = self._route(QueryFrame(
            intent="recipe_followup_query",
            source_text="它的火力怎么样",
            attribute="fire",
            needs_clarification=True,
            clarification_question="请告诉我具体菜名。",
            confidence=0.9,
        ))
        self.assertEqual(action.action, "content")
        self.assertIn("具体菜名", action.content or "")

    def test_non_recipe_is_direct_chat(self):
        action = self._route(QueryFrame(
            intent="non_recipe_query",
            source_text="你是什么模型",
            confidence=1.0,
        ))
        self.assertEqual(action.action, "direct_chat")


if __name__ == "__main__":
    unittest.main()
