#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query Understanding 单元测试 — LLM 路由解析 + 格式化函数 + 保底分类。

测试分类器本身（_parse_router_json、_fallback_classify）是纯代码，不依赖 LLM。
需要 LLM 的 classify_intent 集成测试放在 replay 脚本中。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.query_understanding import (
    QueryIntent,
    classify_intent,
    format_ambiguous_query,
    format_non_recipe,
    _parse_router_json,
    _fallback_classify,
)


class TestParseRouterJson(unittest.TestCase):
    """LLM 返回 JSON 的解析器测试（纯逻辑，不调 LLM）。"""

    def test_plain_json(self):
        result = _parse_router_json('{"intent": "forward_recipe_query", "dish_name": "清蒸鲈鱼", "confidence": 0.95}')
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "forward_recipe_query")
        self.assertEqual(result["dish_name"], "清蒸鲈鱼")

    def test_fenced_json(self):
        result = _parse_router_json('```json\n{"intent": "reverse_query", "target_type": "ingredient", "target_text": "牛肉"}\n```')
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "reverse_query")
        self.assertEqual(result["target_type"], "ingredient")

    def test_fenced_without_lang(self):
        result = _parse_router_json('```\n{"intent": "non_recipe_query"}\n```')
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "non_recipe_query")

    def test_empty_input(self):
        self.assertIsNone(_parse_router_json(""))
        self.assertIsNone(_parse_router_json("   "))
        self.assertIsNone(_parse_router_json(None))

    def test_broken_json(self):
        self.assertIsNone(_parse_router_json('{"intent": broken}'))

    def test_followup_resolved_query(self):
        result = _parse_router_json(
            '{"intent": "recipe_followup_query", "resolved_query": "香煎豆腐怎么做", "confidence": 0.88}'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "recipe_followup_query")
        self.assertEqual(result["resolved_query"], "香煎豆腐怎么做")


class TestFallbackClassify(unittest.TestCase):
    """LLM 不可用时的保底分类测试。"""

    def _fb(self, text: str, dish_names: set[str] | None = None):
        return _fallback_classify(text, dish_names or _DISH_NAMES)

    def test_greeting(self):
        for q in ["你好", "您好", "你是谁"]:
            with self.subTest(query=q):
                intent = self._fb(q)
                self.assertEqual(intent.intent, "greeting", q)
                self.assertGreaterEqual(intent.confidence, 0.8)

    def test_non_recipe_keyword(self):
        for q in ["今天天气怎么样", "帮我查一下股票"]:
            with self.subTest(query=q):
                intent = self._fb(q)
                self.assertEqual(intent.intent, "non_recipe_query", q)

    def test_forward_recipe_by_dish_name(self):
        for q in ["小炒黄牛肉怎么做", "清蒸鲈鱼的做法"]:
            with self.subTest(query=q):
                intent = self._fb(q)
                self.assertEqual(intent.intent, "forward_recipe_query", q)

    def test_forward_by_alias(self):
        intent = self._fb("西红柿炒鸡蛋怎么做")
        self.assertEqual(intent.intent, "forward_recipe_query")

    def test_reverse_marker(self):
        for q in ["哪些菜用了牛肉", "有哪些菜是川菜"]:
            with self.subTest(query=q):
                intent = self._fb(q)
                self.assertEqual(intent.intent, "reverse_query", q)

    def test_cooking_marker(self):
        for q in ["老豆腐的做法", "洋葱炒牛肉怎么做"]:
            with self.subTest(query=q):
                intent = self._fb(q)
                self.assertEqual(intent.intent, "forward_unknown_recipe_query", q)

    def test_empty_input(self):
        intent = classify_intent("")
        self.assertEqual(intent.intent, "non_recipe_query")

    def test_random_text_default(self):
        intent = self._fb("这是一个完全随机的测试文本")
        self.assertEqual(intent.intent, "forward_unknown_recipe_query")


class TestFormatFunctions(unittest.TestCase):
    def test_format_non_recipe(self):
        result = format_non_recipe("你好")
        self.assertIn("success: False", result)
        self.assertIn("out_of_scope", result)

    def test_format_ambiguous(self):
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

    def test_format_ambiguous_no_candidates(self):
        intent = QueryIntent(intent="ambiguous_query")
        result = format_ambiguous_query(intent)
        self.assertIn("success: False", result)

    def test_format_non_recipe_contains_out_of_scope(self):
        result = format_non_recipe("今天天气")
        self.assertIn("out_of_scope", result)

    def test_format_ambiguous_contains_ambiguous(self):
        intent = QueryIntent(intent="ambiguous_query", candidates=[{"target_type": "ingredient", "target_text": "蒜蓉"}])
        result = format_ambiguous_query(intent)
        self.assertIn("ambiguous", result)


# ⚠️ 测试用的菜名列表
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
