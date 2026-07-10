import unittest

from backend.agent_adapter_local_LLM_harness import (
    _build_final_prompt,
    _build_route_prompt,
    _build_direct_chat_prompt,
    _looks_like_tool_request,
    _recipe_query_needs_web_fallback,
)
from backend.tool_calling import (
    _parse_missing_tool_router_response,
    _parse_textual_tool_call,
    _tool_call_name,
)


class ToolRoutingGuardrailTests(unittest.TestCase):
    def test_recipe_how_to_requires_tools(self):
        self.assertTrue(_looks_like_tool_request("告诉我，凉拌牛肉怎么做"))
        self.assertTrue(_looks_like_tool_request("我想吃清蒸鲈鱼"))

    def test_plain_identity_question_does_not_require_tools(self):
        self.assertFalse(_looks_like_tool_request("告诉我你是什么模型"))

    def test_legacy_recipe_query_name_is_normalized(self):
        self.assertEqual(_tool_call_name({"name": "recipe_query", "args": {"query": "凉拌牛肉怎么做"}}), "recipe_query_tool")
        parsed = _parse_textual_tool_call('recipe_query("凉拌牛肉怎么做")')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "recipe_query_tool")
        self.assertEqual(parsed["args"]["query"], "凉拌牛肉怎么做")

    def test_missing_tool_router_normalizes_alias(self):
        parsed = _parse_missing_tool_router_response(
            '{"tool_name":"recipe_query","args":{"query":"凉拌牛肉怎么做"}}'
        )
        self.assertEqual(parsed["name"], "recipe_query_tool")

    def test_recipe_miss_with_allowed_web_fallback_needs_web(self):
        content = """❌ 未找到菜品"凉拌牛肉怎么做"。

结构化摘要：
success: False
match_mode: none
web_fallback_allowed: True
"""
        self.assertTrue(_recipe_query_needs_web_fallback(content))

    def test_direct_chat_prompt_forbids_recipe_hallucination(self):
        messages = _build_direct_chat_prompt("凉拌牛肉怎么做", [])
        system = messages[0].content
        self.assertIn("不能凭常识编菜谱", system)

    def test_intent_routing_can_think_while_final_answer_is_no_think(self):
        route_messages = _build_route_prompt("牛肉做法")
        route_user = route_messages[-1].content
        self.assertNotIn("/no_think", route_user)

        final_messages = _build_final_prompt(
            "牛肉做法",
            trace={"tool_calls": []},
            tool_context=[],
        )
        final_user = final_messages[-1].content
        self.assertTrue(final_user.lstrip().startswith("/no_think"))


if __name__ == "__main__":
    unittest.main()
