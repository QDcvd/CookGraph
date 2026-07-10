import unittest
from unittest.mock import patch

from backend.tool_calling import _execute_tool_call


class FakeTool:
    def __init__(self, name: str, calls: list[dict]):
        self.name = name
        self.calls = calls

    def invoke(self, args: dict) -> str:
        self.calls.append(dict(args))
        return f"called:{self.name}:{args.get('query')}"


class ToolInputContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_json_fragment_query_with_current_user_text(self):
        calls: list[dict] = []
        with patch("backend.tool_calling._get_tools", return_value=[FakeTool("recipe_query_tool", calls)]):
            tool_name, args, content = await _execute_tool_call(
                {"name": "recipe_query_tool", "args": {"query": "{"}},
                current_user_text="我想吃可乐鸡翅，要怎么做",
                history=[],
            )

        self.assertEqual(tool_name, "recipe_query_tool")
        self.assertEqual(args["query"], "我想吃可乐鸡翅，要怎么做")
        self.assertEqual(calls, [{"query": "我想吃可乐鸡翅，要怎么做"}])
        self.assertIn("called:recipe_query_tool", content)

    async def test_affirmative_web_search_uses_pending_original_query(self):
        calls: list[dict] = []
        history = [
            {
                "role": "assistant",
                "content": "需要我帮你到网上搜一下吗？",
                "rag_trace": {
                    "pending_recipe_web_search": {
                        "original_query": "土豆炖鸡需要准备哪些调味料和配菜",
                    }
                },
            }
        ]
        with patch("backend.tool_calling._get_tools", return_value=[FakeTool("web_search_tool", calls)]):
            tool_name, args, content = await _execute_tool_call(
                {"name": "web_search_tool", "args": {"query": "是"}},
                current_user_text="是",
                history=history,
            )

        self.assertEqual(tool_name, "web_search_tool")
        self.assertEqual(args["query"], "土豆炖鸡需要准备哪些调味料和配菜")
        self.assertEqual(calls, [{"query": "土豆炖鸡需要准备哪些调味料和配菜"}])
        self.assertIn("called:web_search_tool", content)

    async def test_invalid_query_without_repair_source_does_not_execute(self):
        calls: list[dict] = []
        with patch("backend.tool_calling._get_tools", return_value=[FakeTool("recipe_query_tool", calls)]):
            tool_name, args, content = await _execute_tool_call(
                {"name": "recipe_query_tool", "args": {"query": "}"}},
                current_user_text="是",
                history=[],
            )

        self.assertEqual(tool_name, "recipe_query_tool")
        self.assertEqual(args["query"], "}")
        self.assertEqual(calls, [])
        self.assertIn("工具参数无效", content)


if __name__ == "__main__":
    unittest.main()
