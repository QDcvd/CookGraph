#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat persistence — SQLite 读写层。

管理 chat_sessions / chat_messages 两张持久化表。
与 memory_store.py 配合使用：memory_store 是运行时兼容层（内存缓存），
chat_persistence 是进程重启后的事实来源。

表结构见 doc/zleap_lite_chat_persistence_plan.md
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

CHAT_PERSISTENCE_SCHEMA_VERSION = 1


def _db_path() -> Path:
    """返回 SQLite 文件路径（与 preference_memory 共用）。"""
    return Path(__file__).resolve().parent.parent / "data" / "memory.sqlite3"


def _conn() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """创建 chat_sessions / chat_messages 表（幂等）。"""
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
          id TEXT PRIMARY KEY,
          title TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          recipe_context_json TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          archived_at TEXT
        );

        CREATE INDEX IF NOT EXISTS chat_sessions_status_updated_idx
          ON chat_sessions (status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS chat_messages (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          rag_trace_json TEXT,
          created_at TEXT NOT NULL,
          deleted_at TEXT,
          FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx
          ON chat_messages (session_id, created_at);
        """)


def _now() -> str:
    return datetime.now().isoformat()


def _msg_id(session_id: str, idx: int) -> str:
    return f"{session_id}_{idx:06d}"


# ── Session CRUD ──


def upsert_chat_session(
    session_id: str,
    title: Optional[str] = None,
    status: Optional[str] = None,
    recipe_context_json: Optional[str] = None,
) -> dict:
    """插入或更新 chat_sessions 行。"""
    now = _now()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if existing:
            fields = ["updated_at = ?"]
            params: list[Any] = [now]
            if title is not None:
                fields.append("title = ?")
                params.append(title)
            if status is not None:
                fields.append("status = ?")
                params.append(status)
            if recipe_context_json is not None:
                fields.append("recipe_context_json = ?")
                params.append(recipe_context_json)
            params.append(session_id)
            conn.execute(
                f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?",
                params,
            )
        else:
            conn.execute(
                """INSERT INTO chat_sessions (id, title, status, recipe_context_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    title or "新对话",
                    status or "active",
                    recipe_context_json or "{}",
                    now,
                    now,
                ),
            )
    return load_chat_session(session_id) or {}


def load_chat_session(session_id: str) -> Optional[dict]:
    """从 SQLite 完整加载一个 session（含 messages）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None

        recipe_context = {}
        raw = row["recipe_context_json"]
        if raw:
            try:
                recipe_context = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                recipe_context = {}

        msg_rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (session_id,),
        ).fetchall()

        messages = []
        for m in msg_rows:
            rag_trace = None
            if m["rag_trace_json"]:
                try:
                    rag_trace = json.loads(m["rag_trace_json"])
                except (json.JSONDecodeError, TypeError):
                    pass
            messages.append({
                "type": "human" if m["role"] == "human" else "ai",
                "content": m["content"],
                "timestamp": m["created_at"],
                "rag_trace": rag_trace,
            })

        return {
            "title": row["title"] or session_id,
            "updated_at": row["updated_at"],
            "messages": messages,
            "recipe_context": recipe_context,
        }


def list_chat_sessions(limit: int = 50) -> list[dict]:
    """列出 active session 列表，按 updated_at 倒序。"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, title, updated_at,
                      (SELECT COUNT(*) FROM chat_messages
                       WHERE session_id = chat_sessions.id AND deleted_at IS NULL) AS msg_count
               FROM chat_sessions
               WHERE status = 'active'
               ORDER BY updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "session_id": row["id"],
                "title": row["title"] or row["id"],
                "message_count": row["msg_count"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


def archive_chat_session(session_id: str) -> bool:
    """软删除：status='archived'，不再出现在默认列表。"""
    now = _now()
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE chat_sessions SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        return cursor.rowcount > 0


# ── Message CRUD ──


def append_chat_message(
    session_id: str,
    role: str,
    content: str,
    rag_trace: Optional[dict] = None,
) -> dict:
    """向 chat_messages 追加一条消息并更新 session updated_at。"""
    now = _now()
    # 计算下一个消息序号
    idx = 0
    with _conn() as conn:
        last = conn.execute(
            "SELECT id FROM chat_messages WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if last:
            parts = last["id"].rsplit("_", 1)
            if len(parts) == 2 and parts[-1].isdigit():
                idx = int(parts[-1]) + 1
        msg_id = _msg_id(session_id, idx)
        rag_trace_str = json.dumps(rag_trace, ensure_ascii=False) if rag_trace else None
        conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, rag_trace_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, rag_trace_str, now),
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
    return {"id": msg_id, "role": role, "content": content, "rag_trace": rag_trace}


def get_persisted_messages(session_id: str) -> list[dict]:
    """获取 session 的持久化消息列表。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [
            {
                "type": "human" if r["role"] == "human" else "ai",
                "content": r["content"],
                "timestamp": r["created_at"],
                "rag_trace": json.loads(r["rag_trace_json"]) if r["rag_trace_json"] else None,
            }
            for r in rows
        ]


def update_session_recipe_context(session_id: str, recipe_context: dict):
    """更新 chat_sessions 的 recipe_context_json 快照。"""
    now = _now()
    raw = json.dumps(recipe_context, ensure_ascii=False)
    with _conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET recipe_context_json = ?, updated_at = ? WHERE id = ?",
            (raw, now, session_id),
        )
