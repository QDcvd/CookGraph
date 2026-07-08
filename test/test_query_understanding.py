#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query Understanding 单元测试 — 不加载 LLM，只测意图分类。

用法：
    PYTHONIOENCODING=utf-8 python test/test_query_understanding.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.query_understanding import (
    classify_intent,
    format_ambiguous_query,
    format_non_recipe,
)


class TestClassifyIntent(unittest.TestCase):
    """意图分类测试 — 见 doc/query_understanding_refactor_plan.md"""

    maxDiff = None

    def _classify(self, text: str, dish_names: set[str] | None = None):
        return classify_intent(text, dish_names=dish_names or _DISH_NAMES)

    def test_forward_recipe_query(self):
        """明确菜名 -> forward_recipe_query"""
        for query in ["小炒黄牛肉怎么做", "番茄炒蛋怎么做", "清蒸鲈鱼怎么做"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "forward_recipe_query", query)

    def test_forward_alias_query(self):
        """别名命中 -> forward_recipe_query"""
        intent = self._classify("西红柿炒鸡蛋怎么做")
        self.assertEqual(intent.intent, "forward_recipe_query")

    def test_unknown_single_recipe_is_not_reverse_by_ingredient_substring(self):
        """未知菜名里含食材词，不应被截成反向食材查询。"""
        for query in ["洋葱炒牛肉的做法", "红烧排骨怎么做", "麻婆豆腐", "凉拌木耳怎么做"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "forward_unknown_recipe_query", query)

    def test_unknown_recipe_attribute_request_is_not_reverse_query(self):
        """想做某道菜并询问调味/配菜，应按单菜谱属性请求处理。"""
        intent = self._classify("我想做十豆炖鸡，需要准备哪些调味料和配菜?")
        self.assertEqual(intent.intent, "forward_unknown_recipe_query")
        self.assertNotEqual(intent.target_type, "taste")

    def test_reverse_ingredient_query(self):
        """食材反向 -> reverse_query / ingredient"""
        for query in ["牛肉怎么做", "虾怎么做", "莲藕怎么做好吃"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "ingredient", query)

    def test_reverse_short_ingredient(self):
        """短词食材 -> reverse_query / ingredient"""
        for query in ["花甲", "肥牛", "鸡蛋", "莲藕", "包菜"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "ingredient", query)

    def test_reverse_cuisine(self):
        """菜系反向 -> reverse_query / cuisine"""
        for query in ["川菜", "湘菜"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "cuisine", query)

    def test_reverse_taste(self):
        """口味反向 -> reverse_query / taste"""
        for query in ["香辣味"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "taste", query)

    def test_reverse_technique(self):
        """技法反向 -> reverse_query / technique"""
        for query in ["蒸制", "爆炒"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "technique", query)

    def test_reverse_pattern_哪些菜用了(self):
        """哪些菜用了某食材 -> reverse_query / ingredient"""
        for query in ["哪些菜用了牛肉", "哪些菜用了莲藕"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "reverse_query", query)
                self.assertEqual(intent.target_type, "ingredient", query)

    def test_reverse_pattern_有什么菜(self):
        """有什么菜推荐 -> reverse_query / cuisine"""
        intent = self._classify("有什么川菜推荐")
        self.assertEqual(intent.intent, "reverse_query")
        self.assertEqual(intent.target_type, "cuisine")

    def test_reverse_pattern_是香辣味的(self):
        """哪些菜是香辣味的 -> reverse_query / taste"""
        intent = self._classify("哪些菜是香辣味的")
        self.assertEqual(intent.intent, "reverse_query")
        self.assertEqual(intent.target_type, "taste")

    def test_reverse_pattern_是蒸制的(self):
        """有哪些菜是蒸制的 -> reverse_query / technique"""
        intent = self._classify("有哪些菜是蒸制的")
        self.assertEqual(intent.intent, "reverse_query")
        self.assertEqual(intent.target_type, "technique")

    def test_ambiguous_query(self):
        """歧义词 -> ambiguous_query"""
        intent = self._classify("蒜蓉")
        self.assertEqual(intent.intent, "ambiguous_query")
        self.assertIsNotNone(intent.candidates)

    def test_non_recipe_query(self):
        """非菜谱 -> non_recipe_query"""
        for query in ["你好", "今天天气怎么样", "你是什么模型"]:
            with self.subTest(query=query):
                intent = self._classify(query)
                self.assertEqual(intent.intent, "non_recipe_query", query)

    def test_empty_input(self):
        """空输入 -> non_recipe_query"""
        intent = classify_intent("")
        self.assertEqual(intent.intent, "non_recipe_query")

    def test_legacy_forward_parser(self):
        """无明确模式 -> legacy_forward_parser"""
        intent = self._classify("这是一个完全随机的测试文本")
        self.assertEqual(intent.intent, "legacy_forward_parser")


class TestFormatFunctions(unittest.TestCase):
    def test_format_non_recipe(self):
        result = format_non_recipe("你好")
        self.assertIn("success: False", result)
        self.assertIn("out_of_scope", result)

    def test_format_ambiguous(self):
        from backend.query_understanding import QueryIntent
        intent = QueryIntent(
            intent="ambiguous_query",
            candidates=[
                {"target_type": "ingredient", "target_text": "蒜蓉"},
                {"target_type": "technique", "target_text": "蒜蓉炒"},
            ],
        )
        result = format_ambiguous_query(intent)
        self.assertIn("success: False", result)
        self.assertIn("ambiguous", result)
        self.assertIn("蒜蓉", result)


# ⚠️ 测试用的菜名列表 —— 需要与本地图谱一致
# 这些菜名必须存在于 config/chem+recipe_kg_updated_fire.pkl 中
_DISH_NAMES = {
    "小炒黄牛肉", "番茄炒蛋", "清蒸鲈鱼", "糖醋里脊",
    "鱼香肉丝", "可乐鸡翅", "干锅肥肠", "手撕包菜",
    "辣椒炒肉", "蒜蓉粉丝虾", "白灼菜心", "黑椒牛柳",
    "丝瓜牛肉", "彩椒炒鸡丁", "西蓝花炒鸡胸肉",
    "韭菜炒鸡蛋", "西葫芦炒鸡蛋", "荷塘月色", "香辣藕丁",
    "炝炒藕片", "洋葱炒肥牛", "番茄金针菇肥牛",
    "清炒丝瓜", "避风塘炒虾", "香辣牛蛙", "姜葱炒鱿鱼",
    "蒜蓉西兰花", "蒜蓉生菜", "爆炒花甲",
}


if __name__ == "__main__":
    unittest.main(verbosity=2)
