#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query Understanding 单元测试 — LLM 路由解析与 QueryFrame 契约。

测试 JSON 解析和结构化契约，不依赖远端 LLM。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.query_understanding import (
    QueryFrame,
    _validate_query_frame,
    enforce_query_frame_contract,
    _parse_router_json,
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


class TestQueryFrameContract(unittest.TestCase):
    def test_explicit_dish_is_preserved_without_keyword_rewrite(self):
        frame = _validate_query_frame(
            {
                "intent": "dish_detail_query",
                "source_text": "番茄炒蛋怎么做",
                "raw_slots": {"dish_text": "番茄炒蛋", "ingredients": [], "techniques": [], "tastes": [], "cuisines": [], "scenario_tags": [], "exclusions": [], "attribute": "cooking_process"},
                "followup": {"is_followup": False, "requires_context": False},
                "confidence": 0.95,
                "reason": "明确菜名查询",
            },
            followup_requires_context=False,
        )
        self.assertEqual(frame.intent, "dish_detail_query")
        self.assertEqual(frame.dish_text, "番茄炒蛋")
        self.assertFalse(frame.needs_clarification)

    def test_followup_uses_context_only_when_model_marks_it(self):
        frame = _validate_query_frame(
            {
                "intent": "recipe_followup_query",
                "source_text": "它的火力怎么样",
                "raw_slots": {"dish_text": None, "ingredients": [], "techniques": [], "tastes": [], "cuisines": [], "scenario_tags": [], "exclusions": [], "attribute": "fire"},
                "followup": {"is_followup": True, "requires_context": True},
                "confidence": 0.9,
                "reason": "指代追问",
            },
            followup_requires_context=True,
        )
        self.assertEqual(frame.intent, "recipe_followup_query")
        self.assertFalse(frame.needs_clarification)

    def test_followup_without_context_is_clarification(self):
        frame = _validate_query_frame(
            {
                "intent": "recipe_followup_query",
                "source_text": "它的火力怎么样",
                "raw_slots": {"dish_text": None, "ingredients": [], "techniques": [], "tastes": [], "cuisines": [], "scenario_tags": [], "exclusions": [], "attribute": "fire"},
                "followup": {"is_followup": True, "requires_context": True},
                "confidence": 0.9,
                "reason": "指代追问",
            },
            followup_requires_context=False,
        )
        self.assertTrue(frame.needs_clarification)

    def test_invalid_model_result_becomes_ambiguous_contract(self):
        frame = enforce_query_frame_contract(QueryFrame(
            intent="",
            source_text="不知道问什么",
            confidence=0.0,
        ))
        self.assertEqual(frame.intent, "")
        self.assertFalse(hasattr(frame, "fallback_query"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
