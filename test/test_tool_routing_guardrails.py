import json
import unittest

from backend.agent_adapter_local_LLM_harness import _recipe_query_needs_web_fallback
from backend.tool_calling import _parse_textual_tool_call, _tool_call_name


class ToolRoutingGuardrailTests(unittest.TestCase):
    def test_recipe_tool_alias_is_normalized_but_legacy_query_is_not_executed(self):
        self.assertEqual(
            _tool_call_name({"name": "recipe_query", "args": {"query": "凉拌牛肉怎么做"}}),
            "recipe_query_tool",
        )
        parsed = _parse_textual_tool_call('recipe_query("凉拌牛肉怎么做")')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "recipe_query_tool")
        self.assertNotIn("query", parsed["args"])

    def test_recipe_miss_with_allowed_web_fallback_uses_json_fields(self):
        content = json.dumps({
            "schema_version": "1.0",
            "ok": False,
            "tool": "recipe_query_tool",
            "query_type": "dish_detail",
            "source": "local_kg",
            "message": "本地图谱暂未收录。",
            "web_fallback_allowed": True,
        }, ensure_ascii=False)
        self.assertTrue(_recipe_query_needs_web_fallback(content))

    def test_recipe_success_does_not_trigger_web_fallback(self):
        content = json.dumps({
            "schema_version": "1.0",
            "ok": True,
            "tool": "recipe_query_tool",
            "query_type": "dish_detail",
            "source": "local_kg",
            "message": "已找到菜谱。",
            "web_fallback_allowed": False,
        }, ensure_ascii=False)
        self.assertFalse(_recipe_query_needs_web_fallback(content))


if __name__ == "__main__":
    unittest.main()
