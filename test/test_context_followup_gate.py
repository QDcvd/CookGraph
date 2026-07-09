import unittest

from backend.context_followup_gate import decide_context_followup


class ContextFollowupGateTests(unittest.TestCase):
    def test_new_recipe_request_does_not_inherit_last_dish(self):
        decision = decide_context_followup("小炒肉怎么做", last_dish="玉米排骨汤")

        self.assertEqual(decision.action, "new_task")

    def test_reverse_ingredient_query_does_not_inherit_last_dish(self):
        decision = decide_context_followup("牛肉有多少种做法", last_dish="玉米排骨汤")

        self.assertEqual(decision.action, "new_task")

    def test_negative_switch_does_not_inherit_last_dish(self):
        decision = decide_context_followup("可以不要玉米排骨汤吗？我要吃别的", last_dish="玉米排骨汤")

        self.assertEqual(decision.action, "new_task")

    def test_pronoun_fire_question_inherits_last_dish(self):
        decision = decide_context_followup("它的火力如何", last_dish="玉米排骨汤")

        self.assertEqual(decision.action, "inherit")
        self.assertEqual(decision.rewritten_query, "玉米排骨汤的火力调节过程")

    def test_bare_attribute_fragment_inherits_when_last_dish_exists(self):
        decision = decide_context_followup("火力如何", last_dish="玉米排骨汤")

        self.assertEqual(decision.action, "inherit")
        self.assertEqual(decision.rewritten_query, "玉米排骨汤的火力调节过程")

    def test_bare_attribute_without_last_dish_does_not_inherit(self):
        decision = decide_context_followup("火力如何", last_dish=None)

        self.assertEqual(decision.action, "new_task")


if __name__ == "__main__":
    unittest.main()
