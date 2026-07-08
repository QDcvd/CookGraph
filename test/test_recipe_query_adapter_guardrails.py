import asyncio
import unittest

from backend.agent_adapter_local_LLM_harness import stream_search_agent
from backend.recipe_query_adapter import query_recipe_kg


class RecipeQueryAdapterGuardrailTests(unittest.TestCase):
    def test_reverse_beef_query_uses_only_local_graph_main_ingredients(self):
        result = query_recipe_kg("牛肉可以用来做什么菜")

        self.assertIn("【本地图谱反向查询结果】", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertIn("小炒黄牛肉", result)
        self.assertIn("干炒牛河", result)
        self.assertIn("黑椒牛柳", result)
        self.assertIn("洋葱炒肥牛", result)
        self.assertIn("丝瓜牛肉", result)
        self.assertIn("番茄金针菇肥牛", result)
        self.assertNotIn("红烧肉", result)
        self.assertNotIn("清蒸鲈鱼", result)
        self.assertNotIn("鱼香肉丝", result)

    def test_reverse_beef_how_many_methods_does_not_become_one_recipe(self):
        result = query_recipe_kg("牛肉有多少做法")

        self.assertIn("【本地图谱反向查询结果】", result)
        self.assertIn("本地图谱中明确命中的菜", result)
        self.assertIn("小炒黄牛肉", result)
        self.assertIn("干炒牛河", result)
        self.assertIn("黑椒牛柳", result)
        self.assertIn("洋葱炒肥牛", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertNotIn("根据本地菜谱图谱，小炒黄牛肉可以这样做", result)

    def test_reverse_fish_query_does_not_match_yuxiang_name(self):
        result = query_recipe_kg("鱼可以做什么菜")

        self.assertIn("清蒸鲈鱼", result)
        self.assertNotIn("鱼香肉丝", result)
        self.assertIn("web_fallback_allowed: False", result)

    def test_empty_attribute_hit_falls_back_to_summary(self):
        result = query_recipe_kg("小炒鸡的具体做法")

        self.assertIn("小炒鸡", result)
        self.assertIn("三黄鸡", result)
        self.assertIn("cooking_method_desc", result)
        self.assertNotIn("cooking_process：\n========================================\n无数据", result)

    def test_alias_fire_attribute_rewrites_to_local_standard_dish(self):
        result = query_recipe_kg("告诉我西红柿炒鸡蛋的火力调配参数")

        self.assertIn("番茄炒蛋", result)
        self.assertIn("fire_control_process", result)
        self.assertIn("别名精确改写", result)
        self.assertNotIn("web_fallback_allowed: True", result)

    def test_common_reverse_questions_are_grounded_in_local_graph(self):
        cases = [
            ("哪些菜用了牛肉", ["小炒黄牛肉", "黑椒牛柳", "干炒牛河"]),
            ("哪些菜用了蒜蓉这种做法", ["蒜蓉粉丝虾", "蒜蓉西兰花"]),
            ("有什么川菜推荐", ["干锅肥肠", "香辣牛蛙", "鱼香肉丝"]),
            ("哪些菜用了莲藕", ["荷塘月色", "炝炒藕片", "香辣藕丁"]),
            ("哪些菜是香辣味的", ["小炒黄牛肉", "香辣牛蛙"]),
            ("有哪些菜是蒸制的", ["蒜蓉粉丝虾", "清蒸鲈鱼"]),
        ]

        for query, expected_dishes in cases:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn("【本地图谱反向查询结果】", result)
                self.assertIn("success: True", result)
                self.assertIn("web_fallback_allowed: False", result)
                for dish in expected_dishes:
                    self.assertIn(dish, result)
                self.assertNotIn("无法理解的查询格式", result)
                self.assertNotIn("类型：Technique", result)


class AgentPreflightGuardrailTests(unittest.TestCase):
    def test_alias_dish_fire_attribute_routes_to_recipe_tool(self):
        async def run():
            events = []
            async for event in stream_search_agent("告诉我西红柿炒鸡蛋的火力调配参数", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []
        tool_names = [item.get("tool_name") for item in tool_calls]

        self.assertIn("recipe_query_tool", tool_names)
        self.assertNotIn("请先告诉我要查询哪道菜", content)

    def test_contextless_fire_control_clarifies_without_tools(self):
        async def run():
            events = []
            async for event in stream_search_agent("火力要怎么控制", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []

        self.assertIn("请先告诉我要查询哪道菜", content)
        self.assertEqual(tool_calls, [])


if __name__ == "__main__":
    unittest.main()
