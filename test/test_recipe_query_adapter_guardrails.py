import asyncio
import unittest
from unittest.mock import patch

from backend.agent_adapter_local_LLM_harness import _emit_final_answer_from_tool_context, stream_search_agent
from backend.recipe_query_adapter import query_recipe_plan
from backend.tool_result import parse_tool_result, serialize_tool_result
from backend.tool_result_policy import render_terminal_recipe_failure


class RecipeQueryToolResultTests(unittest.TestCase):
    def test_dish_query_returns_json_result(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "番茄炒蛋",
            "field": "cooking_process",
            "source_text": "番茄炒蛋怎么做",
        })
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["tool"], "recipe_query_tool")
        self.assertTrue(result["ok"])
        self.assertTrue(any(name in result["message"] for name in ("番茄炒蛋", "番茄炒鸡蛋")))
        self.assertIsInstance(result["data"], dict)

    def test_graph_meta_query_returns_count_in_data(self):
        result = query_recipe_plan({
            "intent": "graph_meta_query",
            "mode": "dish",
            "field": "count",
            "source_text": "菜谱一共收录了多少菜",
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["query_type"], "graph_meta")
        self.assertGreater(result["data"]["dish_count"], 0)

    def test_relation_query_uses_alias_node_when_requested_field_is_missing(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "番茄炒蛋",
            "field": "prep",
            "source_text": "西红柿炒鸡蛋的备菜过程",
        })
        self.assertTrue(result["ok"])
        self.assertIn("prep_process", result["message"])
        self.assertEqual(result["data"]["alias_resolution"]["requested_dish"], "番茄炒蛋")

    def test_full_recipe_is_rendered_as_user_facing_recipe_card(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "辣椒炒肉",
            "field": "full_recipe",
            "show_all": True,
            "source_text": "辣椒炒肉给一个完整的菜谱",
        })
        message = result["message"]
        assert result["ok"] is True
        assert message.startswith("根据本地菜谱图谱，辣椒炒肉可以这样做：")
        assert "用料：" in message
        assert "主要食材：" in message
        assert "做法：" in message
        assert "火力和时间：" in message
        assert "以上内容来自本地菜谱知识图谱。" in message
        assert "cook_id" not in message
        assert "结构化摘要" not in message

    def test_ingredient_query_is_not_rendered_as_full_archive(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "小炒黄牛肉",
            "field": "ingredients",
            "show_ingredients": True,
            "source_text": "小炒黄牛肉的食材是什么",
        })
        message = result["message"]
        assert result["ok"] is True
        assert message.startswith("根据本地菜谱图谱，小炒黄牛肉需要准备这些食材：")
        assert "主要食材：" in message
        assert "配料：" in message
        assert "调味品：" in message
        assert "cook_id" not in message

    def test_unknown_single_dish_allows_web_fallback(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "红烧排骨",
            "field": "cooking_process",
            "source_text": "红烧排骨怎么做",
        })
        self.assertFalse(result["ok"])
        self.assertTrue(result["web_fallback_allowed"])
        self.assertEqual(result["query_type"], "dish_detail")

    def test_combo_query_does_not_allow_web_fallback(self):
        result = query_recipe_plan({
            "intent": "ingredient_combo_query",
            "mode": "combo",
            "ingredients": ["辣椒", "牛肉"],
            "source_text": "我有辣椒和牛肉，可以做什么菜",
        })
        self.assertFalse(result["web_fallback_allowed"])
        self.assertIn(result["query_type"], {"combo", "recommendation"})

    def test_terminal_failure_uses_json_fields_not_text_markers(self):
        result = {
            "schema_version": "1.0",
            "ok": False,
            "tool": "recipe_query_tool",
            "query_type": "combo",
            "source": "local_kg",
            "data": None,
            "message": "本地图谱里没有找到同时满足这些条件的菜。",
            "web_fallback_allowed": False,
            "error": None,
            "meta": {},
        }
        answer = render_terminal_recipe_failure(
            "红萝卜、土豆、瘦肉可以煮什么",
            [{"tool_name": "recipe_query_tool", "args": {"plan": {"mode": "combo", "ingredients": ["红萝卜", "土豆"]}}, "content": serialize_tool_result(result)}],
        )
        self.assertIn("没有找到同时满足", answer)


class AgentResultRenderingTests(unittest.TestCase):
    def test_final_answer_uses_json_tool_message(self):
        result = query_recipe_plan({
            "intent": "dish_detail_query",
            "mode": "dish",
            "dish": "番茄炒蛋",
            "field": "fire",
            "source_text": "番茄炒蛋的火力怎么样",
        })

        async def run():
            events = []
            async for event in _emit_final_answer_from_tool_context(
                "番茄炒蛋的火力怎么样",
                {"tool_calls": []},
                [{"tool_name": "recipe_query_tool", "args": {"plan": {}}, "content": serialize_tool_result(result)}],
            ):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        self.assertIn("番茄炒蛋", content)
        self.assertNotIn('"schema_version"', content)


if __name__ == "__main__":
    unittest.main()
