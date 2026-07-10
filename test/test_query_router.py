import unittest
from unittest.mock import patch

from backend.query_router import route_query
from backend.query_understanding import QueryIntent


class QueryRouterTest(unittest.TestCase):
    def test_greeting_returns_direct_chat(self):
        with patch("backend.query_router.kg_dish_names", return_value=set()):
            action = route_query("你好", [])

        self.assertEqual(action.action, "direct_chat")
        self.assertIn("菜谱", action.content or "")

    def test_known_recipe_forces_recipe_tool(self):
        with patch("backend.query_router.kg_dish_names", return_value={"清蒸鲈鱼"}):
            action = route_query("清蒸鲈鱼怎么做", [])

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.tool_name, "recipe_query_tool")
        self.assertEqual(action.query, "清蒸鲈鱼怎么做")

    def test_reverse_query_forces_recipe_tool(self):
        with (
            patch("backend.query_router.kg_dish_names", return_value=set()),
            patch(
                "backend.query_router.classify_intent",
                return_value=QueryIntent(
                    intent="reverse_query",
                    target_type="ingredient",
                    target_text="牛肉",
                    confidence=0.9,
                    reason="反向食材查询",
                ),
            ),
        ):
            action = route_query("牛肉可以用来做什么菜", [])

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.tool_name, "recipe_query_tool")
        self.assertEqual(action.query, "牛肉可以用来做什么菜")

    def test_contextless_fire_question_asks_for_dish(self):
        with patch("backend.query_router.kg_dish_names", return_value={"小炒黄牛肉"}):
            action = route_query("火力多少", [])

        self.assertEqual(action.action, "content")
        self.assertIn("哪道菜", action.content or "")
        self.assertIsInstance(action.pending_clarification, dict)

    def test_followup_query_uses_resolved_query(self):
        history = [
            {"role": "user", "content": "清蒸牛肉怎么做"},
            {
                "role": "assistant",
                "content": "清蒸牛肉可以这样做",
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "清蒸牛肉怎么做"},
                            "output_preview": "本地菜谱图谱没有收录“清蒸牛肉怎么做”。",
                        }
                    ]
                },
            },
        ]
        with (
            patch("backend.query_router.kg_dish_names", return_value=set()),
            patch(
                "backend.query_router.classify_intent",
                return_value=QueryIntent(
                    intent="recipe_followup_query",
                    resolved_query="清蒸牛肉的火力要怎么样",
                    confidence=0.85,
                    reason="继承上一轮菜品",
                ),
            ),
        ):
            action = route_query("那这道菜的火力要怎么样", history)

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.query, "清蒸牛肉的火力要怎么样")
        self.assertEqual(action.answer_user_text, "清蒸牛肉的火力要怎么样")

    def test_contextual_attribute_query_rewrites_before_clarification_execute(self):
        history = [
            {"role": "user", "content": "清蒸牛肉怎么做"},
            {
                "role": "assistant",
                "content": "清蒸牛肉可以这样做",
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "清蒸牛肉怎么做"},
                            "output_preview": "本地菜谱图谱没有收录“清蒸牛肉怎么做”。",
                        }
                    ]
                },
            },
        ]
        with patch("backend.query_router.kg_dish_names", return_value=set()):
            action = route_query("我只要知道火力是多少", history)

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.query, "清蒸牛肉的火力要怎么样")

    def test_web_result_title_does_not_pollute_context_dish(self):
        history = [
            {"role": "user", "content": "清蒸牛肉怎么做"},
            {
                "role": "assistant",
                "content": "这是联网结果",
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "web_search_tool",
                            "args": {"query": "清蒸牛肉怎么做"},
                            "output_preview": "【步骤图】清蒸牛肉的做法_家常菜_下厨房",
                        }
                    ]
                },
            },
        ]
        with patch("backend.query_router.kg_dish_names", return_value={"清蒸牛肉"}):
            action = route_query("那这道菜的火力要怎么样", history)

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.query, "清蒸牛肉的火力要怎么样")

    def test_explicit_dish_overrides_previous_context_attribute_query(self):
        history = [
            {"role": "user", "content": "清蒸牛肉怎么做"},
            {
                "role": "assistant",
                "content": "这是联网结果",
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "web_search_tool",
                            "args": {"query": "清蒸牛肉怎么做"},
                            "output_preview": "【步骤图】清蒸牛肉的做法_家常菜_下厨房",
                        }
                    ]
                },
            },
        ]
        with patch("backend.query_router.kg_dish_names", return_value={"清蒸牛肉", "小炒黄牛肉"}):
            action = route_query("我只要知道小炒黄牛肉火力是多少", history)

        self.assertEqual(action.action, "tool")
        self.assertEqual(action.query, "我只要知道小炒黄牛肉火力是多少")


if __name__ == "__main__":
    unittest.main()
