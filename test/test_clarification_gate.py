import unittest

from backend.clarification_gate import (
    build_choice_prompt,
    build_web_search_choice_prompt,
    decide_clarification,
)


class ClarificationGateTests(unittest.TestCase):
    def test_exact_dish_executes_recipe_tool(self):
        decision = decide_clarification("清蒸鲈鱼怎么做", dish_names={"清蒸鲈鱼"})
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.tool_name, "recipe_query_tool")

    def test_contextless_fire_control_asks_for_dish(self):
        decision = decide_clarification("火力怎么控制")
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.pending_type, "missing_recipe_target")
        self.assertIn("哪道菜", decision.question or "")

    def test_suspicious_typo_asks_for_confirmation(self):
        decision = decide_clarification("我想做十豆炖鸡，需要准备哪些调味料和配菜?")
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.pending_type, "uncertain_dish_name")
        self.assertIn("土豆炖鸡", decision.question or "")

        prompt = build_choice_prompt(decision)
        self.assertEqual(prompt["type"], "uncertain_dish_name")
        self.assertEqual([item["key"] for item in prompt["options"]], ["A", "B", "C"])
        self.assertEqual(prompt["options"][0]["send_text"], "是")

    def test_uncertain_dish_confirmation_executes_suggested_query(self):
        history = [{
            "role": "assistant",
            "rag_trace": {
                "pending_clarification": {
                    "type": "uncertain_dish_name",
                    "payload": {
                        "original_query": "我想做十豆炖鸡，需要准备哪些调味料和配菜?",
                        "suggested_query": "我想做土豆炖鸡，需要准备哪些调味料和配菜?",
                    },
                }
            },
        }]
        decision = decide_clarification("是", history=history)
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.tool_name, "recipe_query_tool")
        self.assertIn("土豆炖鸡", decision.query or "")

    def test_compound_preference_query_asks_user_to_choose(self):
        decision = decide_clarification("香辣鸡肉怎么做")
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.pending_type, "forward_or_recommendation")
        self.assertIn("推荐", decision.question or "")

        prompt = build_choice_prompt(decision)
        self.assertEqual(prompt["type"], "forward_or_recommendation")
        self.assertEqual(prompt["options"][1]["send_text"], "推荐菜")

    def test_missing_target_choice_prompt_uses_custom_input(self):
        decision = decide_clarification("火力怎么控制")
        prompt = build_choice_prompt(decision)

        self.assertEqual(prompt["type"], "missing_recipe_target")
        self.assertTrue(prompt["options"][0]["custom"])
        self.assertEqual(prompt["options"][1]["send_text"], "取消")

    def test_web_search_choice_prompt_has_affirmative_option(self):
        prompt = build_web_search_choice_prompt("凉拌牛肉怎么做")

        self.assertEqual(prompt["type"], "web_search_confirm")
        self.assertEqual(prompt["pending_payload"]["original_query"], "凉拌牛肉怎么做")
        self.assertEqual(prompt["options"][0]["send_text"], "是")

    def test_compound_recommendation_confirmation_executes_reverse_query(self):
        history = [{
            "role": "assistant",
            "rag_trace": {
                "pending_clarification": {
                    "type": "forward_or_recommendation",
                    "payload": {
                        "original_query": "香辣鸡肉怎么做",
                        "recommended_query": "香辣口味的鸡肉有什么推荐",
                        "dish_query": "香辣鸡肉怎么做",
                    },
                }
            },
        }]
        decision = decide_clarification("推荐菜", history=history)
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.query, "香辣口味的鸡肉有什么推荐")

    def test_unknown_clear_recipe_executes_local_first(self):
        decision = decide_clarification("凉拌牛肉怎么做", dish_names={"清蒸鲈鱼"})
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.tool_name, "recipe_query_tool")

    def test_explicit_web_search_executes_web_tool(self):
        decision = decide_clarification("联网搜一下冬菇滑鸡")
        self.assertEqual(decision.action, "execute")
        self.assertEqual(decision.tool_name, "web_search_tool")


if __name__ == "__main__":
    unittest.main()
