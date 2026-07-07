#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat persistence 单元测试 — 验证 SQLite round-trip。

用法：
    PYTHONIOENCODING=utf-8 python -m pytest test/test_chat_persistence.py -v
    PYTHONIOENCODING=utf-8 python test/test_chat_persistence.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 先覆盖 DB 路径，避免影响真实数据
_test_db = tempfile.mktemp(suffix=".sqlite3")


def _test_db_path() -> Path:
    return Path(_test_db)


# 打补丁：chat_persistence._db_path → 返回临时路径
import backend.chat_persistence as cp
cp._db_path = _test_db_path

from backend.chat_persistence import (
    append_chat_message,
    archive_chat_session,
    get_persisted_messages,
    init_db,
    list_chat_sessions,
    load_chat_session,
    upsert_chat_session,
    update_session_recipe_context,
)
from backend.session_recipe_context import empty_recipe_context


class TestChatPersistence(unittest.TestCase):
    """chat_persistence 单元测试"""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        # 每个测试前清空表
        import sqlite3
        conn = sqlite3.connect(_test_db)
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_sessions")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            Path(_test_db).unlink(missing_ok=True)
        except Exception:
            pass

    def test_1_create_session_has_record(self):
        """创建 session 后 SQLite 中存在 chat_sessions 记录。"""
        upsert_chat_session("session_001", title="测试会话1")
        loaded = load_chat_session("session_001")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["title"], "测试会话1")
        self.assertIn("messages", loaded)
        self.assertEqual(len(loaded["messages"]), 0)

    def test_2_messages_round_trip(self):
        """写入 human/ai 消息后可以按时序读取。"""
        upsert_chat_session("session_002")
        append_chat_message("session_002", "human", "清蒸鲈鱼怎么做")
        append_chat_message("session_002", "ai", "清蒸鲈鱼需要蒸8分钟...")

        msgs = get_persisted_messages("session_002")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["type"], "human")
        self.assertEqual(msgs[0]["content"], "清蒸鲈鱼怎么做")
        self.assertEqual(msgs[1]["type"], "ai")
        self.assertEqual(msgs[1]["content"], "清蒸鲈鱼需要蒸8分钟...")

    def test_3_rag_trace_round_trip(self):
        """assistant 消息的 rag_trace_json 可以 round-trip。"""
        trace = {
            "tool_used": True,
            "tool_name": "recipe_query_tool",
            "tool_calls": [
                {"tool_name": "recipe_query_tool", "args": {"query": "清蒸鲈鱼怎么做"}}
            ],
            "retrieval_mode": "native_tool_loop",
        }
        upsert_chat_session("session_003")
        append_chat_message("session_003", "ai", "清蒸鲈鱼做法...", rag_trace=trace)

        loaded = load_chat_session("session_003")
        self.assertIsNotNone(loaded)
        msgs = loaded["messages"]
        self.assertEqual(len(msgs), 1)
        saved_trace = msgs[0].get("rag_trace")
        self.assertIsNotNone(saved_trace)
        self.assertTrue(saved_trace["tool_used"])
        self.assertEqual(saved_trace["tool_name"], "recipe_query_tool")
        self.assertEqual(
            saved_trace["tool_calls"][0]["args"]["query"], "清蒸鲈鱼怎么做"
        )

    def test_4_recipe_context_round_trip(self):
        """recipe_context_json 更新后可以恢复。"""
        upsert_chat_session("session_004")

        ctx = empty_recipe_context()
        ctx["last_dish"] = "清蒸鲈鱼"
        ctx["last_query"] = "清蒸鲈鱼怎么做"
        update_session_recipe_context("session_004", ctx)

        loaded = load_chat_session("session_004")
        self.assertIsNotNone(loaded)
        restored_ctx = loaded.get("recipe_context", {})
        self.assertEqual(restored_ctx.get("last_dish"), "清蒸鲈鱼")
        self.assertEqual(restored_ctx.get("last_query"), "清蒸鲈鱼怎么做")

    def test_5_archived_not_in_default_list(self):
        """archived session 不出现在默认列表。"""
        upsert_chat_session("session_005", title="测试会话5")
        upsert_chat_session("session_006", title="测试会话6")

        # 存档 session_005
        archive_chat_session("session_005")

        sessions = list_chat_sessions()
        session_ids = [s["session_id"] for s in sessions]
        self.assertNotIn("session_005", session_ids)
        self.assertIn("session_006", session_ids)

    def test_6_hydrate_session_with_messages(self):
        """get_session 在内存为空时可以从 SQLite hydrate。"""
        # 先写入
        upsert_chat_session("session_hydrate", title="水合测试")
        append_chat_message("session_hydrate", "human", "小炒黄牛肉怎么做")
        append_chat_message("session_hydrate", "ai", "小炒黄牛肉的做法是...")

        # 模拟全新加载（不经过内存）
        loaded = load_chat_session("session_hydrate")
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded["messages"]), 2)
        self.assertEqual(loaded["messages"][0]["content"], "小炒黄牛肉怎么做")
        self.assertEqual(loaded["messages"][1]["content"], "小炒黄牛肉的做法是...")


if __name__ == "__main__":
    unittest.main(verbosity=2)
