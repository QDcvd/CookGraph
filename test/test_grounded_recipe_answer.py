import unittest

from backend.agent_adapter_local_LLM_harness import _build_grounded_recipe_answer


class GroundedRecipeAnswerTests(unittest.TestCase):
    def test_uses_structured_recipe_evidence_without_inventing_details(self):
        tool_content = """⚠️ 【模糊检索结果】未找到精确匹配"清蒸鲈鱼怎么做"，为您找到相似菜品："清蒸鲈鱼"

【清蒸鲈鱼 完整档案】
==================================================

【基本信息】
cook_id: 40

【cooking_tips】
鲈鱼蒸制时间根据大小调整（500克8分钟）；汤汁倒掉去腥；淋油激发出香味
cooking_method_desc: 1.鲈鱼去鳞、去鳃、去内脏，洗净，两面划斜刀（深至鱼骨）；2.用料酒10毫升、盐3克涂抹鱼身内外，腌制10分钟；3.姜一半切片、一半切丝，葱一半切段、一半切丝（泡冷水卷曲），红椒切丝；4.盘底铺姜片、葱段，放上鱼，鱼肚内塞入姜片；5.蒸锅水烧开，放入鱼盘，大火蒸8分钟，关火焖2分钟；6.取出倒掉盘中汤汁，捡去姜片葱段，铺上葱姜丝、红椒丝；7.烧热油30克至冒烟，淋于鱼身，再淋入蒸鱼豉油30毫升。
fire_control_process: 步骤1-蒸锅烧水：火力档位【大火】，持续时间约5-8分钟，温度阈值100℃（水沸），蒸锅加足量水大火烧开；步骤2-蒸制鲈鱼：火力档位【大火】，持续时间8分钟，温度阈值100℃（蒸汽温度），放入鱼盘大火蒸8分钟；步骤3-关火焖制：火力档位【关】，持续时间2分钟，温度阈值80-90℃，关火焖2分钟；步骤4-淋油激香：火力档位【大火】，持续时间30秒，温度阈值200℃（油冒烟），取出倒掉汤汁铺葱姜丝红椒丝淋热油30g，再淋入蒸鱼豉油；全程总时长：约16分钟，蒸制时间根据鱼大小调整。

主要食材：
  • 鲈鱼（1条（500克））

配料：
  • 姜（30克）
  • 葱（50克）
  • 红椒（20克（点缀））

调味品：
  • 蒸鱼豉油（30毫升）
  • 料酒（15毫升）
  • 盐（3克）
  • 食用油（30克）

结构化摘要：
success: True
query_type: summary
match_mode: fuzzy
"""

        answer = _build_grounded_recipe_answer(
            "我想吃清蒸鲈鱼",
            [{"tool_name": "recipe_query_tool", "args": {"query": "清蒸鲈鱼怎么做"}, "content": tool_content}],
        )

        self.assertIn("大火蒸8分钟", answer)
        self.assertIn("关火焖2分钟", answer)
        self.assertIn("蒸鱼豉油（30毫升）", answer)
        self.assertIn("食用油（30克）", answer)
        self.assertNotIn("香油", answer)
        self.assertNotIn("8-10分钟", answer)


if __name__ == "__main__":
    unittest.main()
