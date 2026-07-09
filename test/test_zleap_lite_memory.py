import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.context_manager import build_runtime_memory_context
from backend.preference_memory import (
    apply_preference_actions,
    extract_preference_actions,
    list_preferences,
)
from backend.session_recipe_context import empty_recipe_context, update_context_from_trace


class PreferenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICOOK_MEMORY_DB"] = str(Path(self.tmp.name) / "memory.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("MINICOOK_MEMORY_DB", None)

    def test_extracts_high_confidence_restriction(self):
        actions = extract_preference_actions("我不能吃辣")

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "remember")
        self.assertEqual(actions[0].kind, "dietary_restriction")
        self.assertEqual(actions[0].normalized_key, "辣")

    def test_skips_temporary_and_third_party_preferences(self):
        self.assertEqual(extract_preference_actions("今天不想吃辣"), [])
        self.assertEqual(extract_preference_actions("我朋友不吃香菜"), [])

    def test_persists_and_archives_preference(self):
        apply_preference_actions("我不能吃辣", source_session_id="s1")
        active = list_preferences()
        self.assertEqual(len(active), 1)
        self.assertIn("不能吃辣", active[0]["memory"])

        apply_preference_actions("我现在可以吃辣了", source_session_id="s1")
        self.assertEqual(list_preferences(), [])

    def test_replaces_same_key_preference(self):
        apply_preference_actions("我喜欢清淡", source_session_id="s1")
        apply_preference_actions("我偏好清淡", source_session_id="s2")

        active = list_preferences()
        self.assertEqual(len(active), 1)
        self.assertIn("偏好清淡", active[0]["memory"])

    def test_replaces_default_cooking_goal(self):
        apply_preference_actions("以后尽量清淡", source_session_id="s1")
        apply_preference_actions("把我的偏好改成少油少盐", source_session_id="s2")

        active = list_preferences()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["normalized_key"], "default_cooking_goal")
        self.assertIn("少油少盐", active[0]["memory"])


class SessionRecipeContextTests(unittest.TestCase):
    def test_updates_last_dish_from_recipe_trace(self):
        trace = {
            "hybrid_retrieval": {"standard_dish": "清蒸鲈鱼"},
            "tool_calls": [
                {
                    "tool_name": "recipe_query_tool",
                    "args": {"query": "清蒸鲈鱼怎么做"},
                    "output_preview": "【清蒸鲈鱼 完整档案】蒸制时间约8分钟，倒掉蒸鱼水。",
                }
            ],
        }

        context = update_context_from_trace(empty_recipe_context(), "清蒸鲈鱼怎么做", trace)

        self.assertEqual(context["last_dish"], "清蒸鲈鱼")
        self.assertEqual(context["last_query"], "清蒸鲈鱼怎么做")
        self.assertIn("蒸制时间", context["last_recipe_tool_result_summary"])

    def test_does_not_overwrite_without_tools(self):
        previous = {**empty_recipe_context(), "last_dish": "辣椒炒肉"}
        context = update_context_from_trace(previous, "今天天气怎么样", {"tool_calls": []})

        self.assertEqual(context["last_dish"], "辣椒炒肉")

    def test_records_web_fallback_summary(self):
        trace = {
            "tool_calls": [
                {
                    "tool_name": "web_search_tool",
                    "args": {"query": "北京烤鸭怎么做"},
                    "output_preview": "搜索结果：北京烤鸭怎么做",
                }
            ],
        }

        context = update_context_from_trace(empty_recipe_context(), "北京烤鸭怎么做", trace)

        self.assertEqual(context["last_web_fallback_query"], "北京烤鸭怎么做")
        self.assertIn("搜索结果", context["last_web_fallback_summary"])

    def test_records_and_clears_pending_recipe_web_search_offer(self):
        offer_trace = {
            "tool_calls": [
                {
                    "tool_name": "recipe_query_tool",
                    "args": {"query": "我想做十豆炖鸡，需要准备哪些调味料和配菜?"},
                    "output_preview": (
                        "由于当前查询未能在本地图谱节点中稳定匹配到“十豆炖鸡”的相关信息。需要我帮你到网上搜一下吗？\n\n"
                        "结构化摘要：\n"
                        "success: False\n"
                        "web_search_offer: True\n"
                        "web_fallback_allowed: False"
                    ),
                }
            ],
        }

        context = update_context_from_trace(empty_recipe_context(), "我想做十豆炖鸡，需要准备哪些调味料和配菜?", offer_trace)

        pending = context["pending_recipe_web_search"]
        self.assertIsNotNone(pending)
        self.assertEqual(pending["original_query"], "我想做十豆炖鸡，需要准备哪些调味料和配菜?")

        web_trace = {
            "tool_calls": [
                {
                    "tool_name": "web_search_tool",
                    "args": {"query": pending["original_query"]},
                    "output_preview": "搜索结果：我想做十豆炖鸡，需要准备哪些调味料和配菜?",
                }
            ],
        }
        context = update_context_from_trace(context, "搜一下", web_trace)

        self.assertIsNone(context["pending_recipe_web_search"])

    def test_records_and_renders_pending_clarification(self):
        trace = {
            "pending_clarification": {
                "type": "uncertain_dish_name",
                "payload": {
                    "original_query": "我想做十豆炖鸡，需要准备哪些调味料和配菜?",
                    "suggested_query": "我想做土豆炖鸡，需要准备哪些调味料和配菜?",
                },
            },
            "tool_calls": [],
        }

        context = update_context_from_trace(empty_recipe_context(), "我想做十豆炖鸡，需要准备哪些调味料和配菜?", trace)

        self.assertEqual(context["pending_clarification"]["type"], "uncertain_dish_name")
        text = build_runtime_memory_context(recipe_context=context)
        self.assertIn("待澄清菜谱问题(uncertain_dish_name)", text)


class RuntimeMemoryRenderTests(unittest.TestCase):
    def test_renders_runtime_memory_block(self):
        text = build_runtime_memory_context(
            preferences=[{"kind": "dietary_restriction", "memory": "用户不能吃辣。"}],
            recipe_context={**empty_recipe_context(), "last_dish": "清蒸鲈鱼"},
        )

        self.assertIn("<runtime_memory>", text)
        self.assertIn("用户不能吃辣", text)
        self.assertIn("最近菜品：清蒸鲈鱼", text)


if __name__ == "__main__":
    unittest.main()
