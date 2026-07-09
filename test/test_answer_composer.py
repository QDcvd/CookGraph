import unittest

from backend.answer_composer import compose_web_recipe_answer


class AnswerComposerTests(unittest.TestCase):
    def test_web_recipe_answer_is_concise_recipe_not_search_dump(self):
        web_content = """搜索结果：莲藕炖猪脚怎么做
1. 莲藕炖猪脚的做法
链接：https://example.com/recipe
摘要：莲藕炖猪脚。1. 猪脚焯水洗净。2. 莲藕去皮切块。3. 猪脚加姜片先炖。4. 加入莲藕继续炖至软糯。5. 出锅前加盐调味。
2. 莲藕猪蹄汤家常做法
链接：https://example.com/soup
摘要：主要材料有猪蹄、莲藕、姜、葱、料酒、盐。"""

        answer = compose_web_recipe_answer("莲藕炖猪脚怎么做", web_content)

        self.assertIn("联网整理", answer)
        self.assertIn("做法：", answer)
        self.assertIn("猪脚焯水洗净", answer)
        self.assertIn("参考来源：", answer)
        self.assertNotIn("搜索结果：", answer)
        self.assertNotIn("摘要：", answer)

    def test_web_recipe_answer_refuses_when_steps_are_not_clear(self):
        web_content = """搜索结果：奇怪菜怎么做
1. 短视频
链接：https://example.com/video
摘要：这个菜很好吃，欢迎观看视频。"""

        answer = compose_web_recipe_answer("奇怪菜怎么做", web_content)

        self.assertIn("没有足够清晰的做法步骤", answer)
        self.assertNotIn("做法：", answer)


if __name__ == "__main__":
    unittest.main()
