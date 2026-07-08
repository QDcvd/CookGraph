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


class AgentPreflightGuardrailTests(unittest.TestCase):
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
