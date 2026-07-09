import asyncio
import unittest

from backend.agent_adapter_local_LLM_harness import (
    _build_grounded_web_fallback_answer,
    _emit_final_answer_from_tool_context,
    _pending_recipe_web_search_from_history,
    _expand_web_recipe_query,
    _preflight_recipe_action,
    stream_search_agent,
)
from backend.recipe_query_adapter import query_recipe_kg


class RecipeQueryAdapterGuardrailTests(unittest.TestCase):
    def test_reverse_beef_query_uses_only_local_graph_main_ingredients(self):
        result = query_recipe_kg("牛肉可以用来做什么菜")

        self.assertIn("用户摘要：", result)
        self.assertIn("query_type: entity_lookup", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertIn("小炒黄牛肉", result)
        self.assertIn("干炒牛河", result)
        self.assertIn("黑椒牛柳", result)
        self.assertIn("洋葱炒肥牛", result)
        self.assertIn("丝瓜牛肉", result)
        self.assertIn("番茄金针菇肥牛", result)
        self.assertNotIn("红烧肉", result)
        self.assertNotIn("清蒸鲈鱼", result)
        self.assertNotIn("鱼香肉丝", result)

    def test_reverse_beef_how_many_methods_does_not_become_one_recipe(self):
        result = query_recipe_kg("牛肉有多少做法")

        self.assertIn("用户摘要：", result)
        self.assertIn("query_type: entity_lookup", result)
        self.assertIn("小炒黄牛肉", result)
        self.assertIn("干炒牛河", result)
        self.assertIn("黑椒牛柳", result)
        self.assertIn("洋葱炒肥牛", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertNotIn("根据本地菜谱图谱，小炒黄牛肉可以这样做", result)

    def test_reverse_fish_query_does_not_match_yuxiang_name(self):
        result = query_recipe_kg("鱼可以做什么菜")

        self.assertIn("清蒸鲈鱼", result)
        self.assertNotIn("鱼香肉丝", result)
        self.assertIn("web_fallback_allowed: False", result)

    def test_empty_attribute_hit_falls_back_to_summary(self):
        result = query_recipe_kg("小炒鸡的具体做法")

        self.assertIn("小炒鸡", result)
        self.assertIn("三黄鸡", result)
        self.assertIn("cooking_method_desc", result)
        self.assertNotIn("cooking_process：\n========================================\n无数据", result)

    def test_alias_fire_attribute_rewrites_to_local_standard_dish(self):
        result = query_recipe_kg("告诉我西红柿炒鸡蛋的火力调配参数")

        self.assertIn("番茄炒蛋", result)
        self.assertIn("fire_control_process", result)
        self.assertIn("别名精确改写", result)
        self.assertNotIn("web_fallback_allowed: True", result)

    def test_common_reverse_questions_are_grounded_in_local_graph(self):
        cases = [
            ("哪些菜用了牛肉", ["小炒黄牛肉", "黑椒牛柳", "干炒牛河"]),
            ("哪些菜用了蒜蓉这种做法", ["蒜蓉粉丝虾", "蒜蓉西兰花"]),
            ("有什么川菜推荐", ["干锅肥肠", "香辣牛蛙", "鱼香肉丝"]),
            ("哪些菜用了莲藕", ["荷塘月色", "炝炒藕片", "香辣藕丁"]),
            ("哪些菜是香辣味的", ["小炒黄牛肉", "香辣牛蛙"]),
            ("有哪些菜是蒸制的", ["蒜蓉粉丝虾", "清蒸鲈鱼"]),
        ]

        for query, expected_dishes in cases:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn("success: True", result)
                self.assertIn("web_fallback_allowed: False", result)
                for dish in expected_dishes:
                    self.assertIn(dish, result)
                self.assertNotIn("无法理解的查询格式", result)
                self.assertNotIn("类型：Technique", result)

    def test_reverse_intent_short_terms_do_not_fall_back_to_forward_parser(self):
        bare_entity_cases = [
            ("花甲", ["爆炒花甲"]),
            ("川菜", ["干锅肥肠", "鱼香肉丝"]),
            ("香辣味", ["小炒黄牛肉", "香辣牛蛙"]),
            ("蒸制", ["蒜蓉粉丝虾", "清蒸鲈鱼"]),
        ]

        for query, expected_dishes in bare_entity_cases:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn("用户摘要：", result)
                self.assertIn("query_type: entity_lookup", result)
                self.assertIn("web_fallback_allowed: False", result)
                for dish in expected_dishes:
                    self.assertIn(dish, result)
                self.assertNotIn("完整档案", result)

        legacy_reverse_cases = [
            ("牛肉怎么做", ["小炒黄牛肉", "黑椒牛柳"]),
            ("虾怎么做", ["蒜蓉粉丝虾", "避风塘炒虾"]),
        ]

        for query, expected_dishes in legacy_reverse_cases:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn("【本地图谱反向查询结果】", result)
                self.assertIn("web_fallback_allowed: False", result)
                for dish in expected_dishes:
                    self.assertIn(dish, result)
                self.assertNotIn("完整档案", result)

    def test_forward_direct_dish_names_stay_forward(self):
        for query, dish in [
            ("小炒黄牛肉怎么做", "小炒黄牛肉"),
            ("番茄炒蛋怎么做", "番茄炒蛋"),
            ("清蒸鲈鱼怎么做", "清蒸鲈鱼"),
            ("西红柿炒鸡蛋怎么做", "番茄炒蛋"),
        ]:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn(dish, result)
                self.assertIn("完整档案", result)
                self.assertNotIn("【本地图谱反向查询结果】", result)

    def test_unknown_forward_recipe_can_accept_fuzzy_unless_user_excludes_it(self):
        result = query_recipe_kg("洋葱炒牛肉的做法")

        self.assertIn("洋葱炒肥牛", result)
        self.assertIn("match_mode: fuzzy", result)
        self.assertNotIn("web_fallback_allowed: True", result)

        excluded = query_recipe_kg("我不要肥牛，洋葱炒牛肉的做法")

        self.assertIn("success: False", excluded)
        self.assertIn("web_fallback_allowed: True", excluded)

    def test_unknown_forward_recipe_rejects_fuzzy_when_technique_conflicts(self):
        result = query_recipe_kg("凉拌牛肉怎么做")

        self.assertIn("success: False", result)
        self.assertIn("web_search_offer: True", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertNotIn("根据本地菜谱图谱，小炒黄牛肉可以这样做", result)

    def test_unknown_single_recipe_misses_allow_web_fallback(self):
        for query in ["红烧排骨怎么做", "麻婆豆腐", "凉拌木耳怎么做"]:
            with self.subTest(query=query):
                result = query_recipe_kg(query)
                self.assertIn("success: False", result)
                self.assertIn("web_fallback_allowed: True", result)
                self.assertNotIn("【本地图谱反向查询结果】", result)

    def test_unknown_recipe_attribute_request_offers_web_search(self):
        result = query_recipe_kg("我想做十豆炖鸡，需要准备哪些调味料和配菜?")

        self.assertIn("需要我帮你到网上搜一下吗", result)
        self.assertIn("web_search_offer: True", result)
        self.assertIn("web_fallback_allowed: False", result)
        self.assertNotIn("【本地图谱反向查询结果】", result)
        self.assertNotIn("查询维度：口味", result)

    def test_bare_potato_query_groups_main_and_auxiliary_ingredient_matches(self):
        result = query_recipe_kg("土豆")

        self.assertIn("query_type: entity_lookup", result)
        self.assertIn("主食材：", result)
        self.assertIn("清炒土豆丝", result)
        self.assertIn("配料：", result)
        self.assertIn("干锅肥肠", result)
        self.assertIn("泰式咖喱鸡", result)
        self.assertIn("web_fallback_allowed: False", result)

    def test_compound_spicy_chicken_recommendation_intersects_constraints(self):
        result = query_recipe_kg("我比较喜欢吃辣，香辣口味的鸡肉有什么推荐嘛?")

        self.assertIn("query_type: compound_recommendation", result)
        self.assertIn("鸡肉 + 香辣味", result)
        self.assertIn("小炒鸡", result)
        self.assertNotIn("小炒黄牛肉（", result)
        self.assertNotIn("干锅肥肠（", result)
        self.assertNotIn("香辣牛蛙（", result)
        self.assertIn("没有自动凑数", result)

    def test_graph_dish_count_meta_query_returns_count(self):
        result = query_recipe_kg("告诉我你现在菜谱一共收录了多少菜")

        self.assertIn("当前收录 50 道菜", result)
        self.assertIn("query_type: graph_meta", result)
        self.assertIn("dish_count: 50", result)
        self.assertIn("web_fallback_allowed: False", result)


class AgentPreflightGuardrailTests(unittest.TestCase):
    def test_affirmative_after_web_offer_routes_original_query_to_web_search(self):
        original = "我想做十豆炖鸡，需要准备哪些调味料和配菜?"
        history = [
            {"role": "human", "content": original},
            {
                "role": "ai",
                "content": "由于当前查询未能在本地图谱节点中稳定匹配到“十豆炖鸡”的相关信息，因此无法提供具体的调味料和配菜列表。需要我帮你到网上搜一下吗？",
            },
        ]

        preflight = _preflight_recipe_action("是", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("tool_name"), "web_search_tool")
        self.assertEqual(preflight.get("query"), original)

    def test_alias_dish_fire_attribute_routes_to_recipe_tool(self):
        async def run():
            events = []
            async for event in stream_search_agent("告诉我西红柿炒鸡蛋的火力调配参数", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []
        tool_names = [item.get("tool_name") for item in tool_calls]

        self.assertIn("recipe_query_tool", tool_names)
        self.assertNotIn("请先告诉我要查询哪道菜", content)

    def test_contextless_fire_control_clarifies_without_tools(self):
        async def run():
            events = []
            async for event in stream_search_agent("火力要怎么控制", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []

        self.assertIn("是哪道菜", content)
        self.assertIn("火力控制", content)
        self.assertEqual(tool_calls, [])
        self.assertEqual(trace.get("choice_prompt", {}).get("type"), "missing_recipe_target")
        self.assertEqual(
            [item.get("key") for item in trace.get("choice_prompt", {}).get("options", [])],
            ["A", "B", "C"],
        )

    def test_unknown_forward_offer_includes_web_search_choice_prompt(self):
        async def run():
            events = []
            async for event in stream_search_agent("凉拌牛肉怎么做", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        choice_prompt = trace.get("choice_prompt") if isinstance(trace, dict) else {}

        self.assertEqual(choice_prompt.get("type"), "web_search_confirm")
        self.assertEqual(choice_prompt.get("pending_payload", {}).get("original_query"), "凉拌牛肉怎么做")
        self.assertEqual(choice_prompt.get("options", [])[0].get("send_text"), "是")

    def test_contextual_attribute_followup_runs_before_clarification_gate(self):
        history = [
            {"role": "user", "content": "辣椒炒肉怎么做"},
            {
                "role": "assistant",
                "content": "根据本地菜谱图谱，辣椒炒肉可以这样做。",
                "rag_trace": {
                    "hybrid_retrieval": {"standard_dish": "辣椒炒肉"},
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "辣椒炒肉怎么做"},
                            "output_preview": "【辣椒炒肉 完整档案】",
                        }
                    ],
                },
            },
        ]

        preflight = _preflight_recipe_action("刚才那道菜需要什么调料", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("type"), "tool")
        self.assertEqual(preflight.get("query"), "辣椒炒肉的调味品")

    def test_clear_new_recipe_request_does_not_inherit_last_dish(self):
        history = [
            {"role": "user", "content": "玉米排骨汤怎么做"},
            {
                "role": "assistant",
                "content": "根据本地菜谱图谱，玉米排骨汤可以这样做。",
                "rag_trace": {
                    "hybrid_retrieval": {"standard_dish": "玉米排骨汤"},
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "玉米排骨汤怎么做"},
                            "output_preview": "【玉米排骨汤 完整档案】",
                        }
                    ],
                },
            },
        ]

        preflight = _preflight_recipe_action("小炒肉怎么做", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("type"), "tool")
        self.assertEqual(preflight.get("query"), "小炒肉怎么做")
        self.assertNotEqual(preflight.get("query"), "玉米排骨汤怎么做")

    def test_reverse_query_does_not_inherit_last_dish(self):
        history = [
            {"role": "user", "content": "玉米排骨汤怎么做"},
            {
                "role": "assistant",
                "content": "根据本地菜谱图谱，玉米排骨汤可以这样做。",
                "rag_trace": {
                    "hybrid_retrieval": {"standard_dish": "玉米排骨汤"},
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "玉米排骨汤怎么做"},
                            "output_preview": "【玉米排骨汤 完整档案】",
                        }
                    ],
                },
            },
        ]

        preflight = _preflight_recipe_action("牛肉有多少种做法", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("type"), "tool")
        self.assertEqual(preflight.get("query"), "牛肉有多少种做法")
        self.assertNotEqual(preflight.get("query"), "玉米排骨汤怎么做")

    def test_strong_pronoun_followup_inherits_last_dish(self):
        history = [
            {"role": "user", "content": "玉米排骨汤怎么做"},
            {
                "role": "assistant",
                "content": "根据本地菜谱图谱，玉米排骨汤可以这样做。",
                "rag_trace": {
                    "hybrid_retrieval": {"standard_dish": "玉米排骨汤"},
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "玉米排骨汤怎么做"},
                            "output_preview": "【玉米排骨汤 完整档案】",
                        }
                    ],
                },
            },
        ]

        preflight = _preflight_recipe_action("它的火力如何", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("type"), "tool")
        self.assertEqual(preflight.get("query"), "玉米排骨汤的火力调节过程")
        self.assertEqual(preflight.get("context_followup", {}).get("source_dish"), "玉米排骨汤")

    def test_web_fallback_topic_followup_stays_on_web_instead_of_local_fuzzy(self):
        history = [
            {"role": "user", "content": "锅包肉怎么做"},
            {
                "role": "assistant",
                "content": "本地菜谱图谱没有收录“锅包肉怎么做”。下面是根据联网搜索结果整理的参考做法。",
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": "锅包肉怎么做"},
                            "output_preview": "success: False\nweb_fallback_allowed: True",
                        },
                        {
                            "tool_name": "web_search_tool",
                            "args": {"query": "锅包肉怎么做"},
                            "output_preview": "搜索结果：锅包肉怎么做",
                        },
                    ]
                },
            },
        ]

        preflight = _preflight_recipe_action("火力如何", history)

        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("tool_name"), "web_search_tool")
        self.assertIn("锅包肉", preflight.get("query"))

    def test_graph_dish_count_routes_to_recipe_tool(self):
        async def run():
            events = []
            async for event in stream_search_agent("告诉我你现在菜谱一共收录了多少菜", []):
                events.append(event)
            return events

        events = asyncio.run(run())
        content = "".join(event.get("content", "") for event in events if event.get("type") == "content")
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []
        tool_names = [item.get("tool_name") for item in tool_calls]

        self.assertIn("recipe_query_tool", tool_names)
        self.assertIn("50", content)
        self.assertNotIn("无法获取菜谱知识图谱中收录的菜品数量", content)

    def test_web_recipe_fallback_expands_common_aliases(self):
        expanded = _expand_web_recipe_query("莲藕炖猪脚怎么做")

        self.assertIn("莲藕炖猪脚怎么做", expanded)
        self.assertIn("莲藕猪蹄汤", expanded)
        self.assertIn("下厨房", expanded)
        self.assertIn("美食天下", expanded)

    def test_affirmative_web_search_recovers_pending_recipe_miss_context(self):
        original = "我想做十豆炖鸡，需要准备哪些调味料和配菜?"
        recipe_miss = (
            "由于当前查询未能在本地图谱节点中稳定匹配到“十豆炖鸡”的相关信息，因此无法提供具体的调味料和配菜列表。"
            "需要我帮你到网上搜一下吗？\n\n"
            "结构化摘要：\n"
            "success: False\n"
            "intent: forward_recipe_query\n"
            "match_mode: none\n"
            "web_search_offer: True\n"
            "web_fallback_allowed: False"
        )
        history = [
            {"role": "user", "content": original},
            {
                "role": "assistant",
                "content": recipe_miss.split("结构化摘要：", 1)[0].strip(),
                "rag_trace": {
                    "tool_calls": [
                        {
                            "tool_name": "recipe_query_tool",
                            "args": {"query": original},
                            "output_preview": recipe_miss,
                        }
                    ]
                },
            },
        ]

        pending = _pending_recipe_web_search_from_history(history)
        preflight = _preflight_recipe_action("搜一下", history)

        self.assertIsNotNone(pending)
        self.assertEqual(pending.get("original_query"), original)
        self.assertIn("web_search_offer: True", pending.get("recipe_miss_content", ""))
        self.assertIsNotNone(preflight)
        self.assertEqual(preflight.get("tool_name"), "web_search_tool")
        self.assertEqual(preflight.get("query"), original)
        self.assertIn("pending_recipe_web_search", preflight)

    def test_confirmed_web_search_refuses_weak_generic_evidence(self):
        original = "我想做十豆炖鸡，需要准备哪些调味料和配菜?"
        recipe_miss = (
            "结构化摘要：\n"
            "success: False\n"
            "intent: forward_recipe_query\n"
            "web_search_offer: True\n"
            "web_fallback_allowed: False"
        )
        web_content = (
            "搜索结果：我想做十豆炖鸡，需要准备哪些调味料和配菜?\n"
            "1. 炖鸡教程：步骤、材料及注意事项是什么？\n"
            "链接：https://example.com/generic-chicken\n"
            "摘要：炖鸡是一道传统家常菜。准备鸡肉、大葱、生姜、食盐、食用油、干香菇、枸杞、红枣。"
            "步骤 1 鸡肉切块。 2 冷水下锅焯水。 3 加水炖煮。\n"
            "2. 莲藕猪蹄汤的做法\n"
            "链接：https://example.com/lotus-pork\n"
            "摘要：莲藕猪蹄汤需要猪蹄、莲藕、姜、葱、料酒，焯水后炖煮。\n"
        )

        answer = _build_grounded_web_fallback_answer(
            original,
            [
                {"tool_name": "recipe_query_tool", "args": {"query": original}, "content": recipe_miss},
                {"tool_name": "web_search_tool", "args": {"query": original}, "content": web_content},
            ],
        )

        self.assertIn("没有足够清晰的做法步骤", answer)
        self.assertNotIn("虫草", answer)
        self.assertNotIn("莲藕猪蹄汤需要", answer)

    def test_final_emitter_prefers_executed_web_fallback_over_offer(self):
        async def run():
            original = "我想做十豆炖鸡，需要准备哪些调味料和配菜?"
            tool_context = [
                {
                    "tool_name": "recipe_query_tool",
                    "args": {"query": original},
                    "content": (
                        "由于当前查询未能在本地图谱节点中稳定匹配到“十豆炖鸡”的相关信息，因此无法提供具体的调味料和配菜列表。"
                        "需要我帮你到网上搜一下吗？\n\n"
                        "结构化摘要：\n"
                        "success: False\n"
                        "web_search_offer: True\n"
                        "web_fallback_allowed: False"
                    ),
                },
                {
                    "tool_name": "web_search_tool",
                    "args": {"query": original},
                    "content": (
                        "搜索结果：我想做十豆炖鸡，需要准备哪些调味料和配菜?\n"
                        "1. 炖鸡教程：步骤、材料及注意事项是什么？\n"
                        "链接：https://example.com/generic-chicken\n"
                        "摘要：炖鸡是一道传统家常菜。步骤 1 鸡肉切块。 2 冷水下锅焯水。"
                    ),
                },
            ]
            events = []
            async for event in _emit_final_answer_from_tool_context(original, {"tool_calls": []}, tool_context):
                events.append(event)
            return "".join(event.get("content", "") for event in events if event.get("type") == "content")

        content = asyncio.run(run())

        self.assertIn("没有足够清晰的做法步骤", content)
        self.assertNotIn("需要我帮你到网上搜一下吗", content)

    def test_pending_clarification_resolution_uses_resolved_query_for_web_fallback(self):
        history = [
            {"role": "user", "content": "香辣鸡肉怎么做"},
            {
                "role": "assistant",
                "content": "你是想查一道叫“香辣鸡肉”的具体做法，还是想让我推荐香辣口味、含鸡肉的菜？",
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
            },
        ]

        async def run():
            events = []
            async for event in stream_search_agent("具体做法", history):
                events.append(event)
            return events

        events = asyncio.run(run())
        trace = next((event.get("rag_trace") for event in events if event.get("type") == "trace"), {})
        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []
        web_queries = [
            (item.get("args") or {}).get("query")
            for item in tool_calls
            if item.get("tool_name") == "web_search_tool"
        ]

        self.assertIn("香辣鸡肉怎么做", web_queries)
        self.assertNotIn("具体做法", web_queries)


if __name__ == "__main__":
    unittest.main()
