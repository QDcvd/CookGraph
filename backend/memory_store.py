"""内存会话存储 + SQLite 持久化。

内存是运行期缓存，SQLite 是进程重启后的事实来源。
Session 按需从 SQLite hydrate：get_session() 先查缓存，缓存未命中则从 SQLite 加载。
写入路径（add_message / update_recipe_context）同时写内存和 SQLite。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from backend.chat_persistence import (
    append_chat_message,
    archive_chat_session,
    init_db as _init_chat_db,
    list_chat_sessions as _list_persisted_sessions,
    load_chat_session as _load_persisted_session,
    update_session_recipe_context as _persist_recipe_context,
    upsert_chat_session,
)
from backend.session_recipe_context import empty_recipe_context

# 内存存储：{ session_id: { title, updated_at, messages, recipe_context } }
_sessions: dict = {}

# ── 初始化 ──


def init_chat_db():
    """初始化持久化表（后端启动时调用一次）。"""
    _init_chat_db()


# ── 会话 CRUD ──


def create_session(session_id: str, title: str = "新对话") -> dict:
    """创建新会话（内存 + SQLite）。"""
    now = datetime.now().isoformat()
    _sessions[session_id] = {
        "title": title,
        "updated_at": now,
        "messages": [],
        "recipe_context": empty_recipe_context(),
    }
    upsert_chat_session(session_id, title=title)
    return _sessions[session_id]


def get_session(session_id: str) -> Optional[dict]:
    """获取单个会话。

    内存缓存命中则返回缓存，否则从 SQLite hydrate。
    """
    cached = _sessions.get(session_id)
    if cached is not None:
        return cached

    # 缓存未命中，从 SQLite 加载
    persisted = _load_persisted_session(session_id)
    if persisted is None:
        return None

    _sessions[session_id] = persisted
    return persisted


def list_sessions() -> list[dict]:
    """列出所有会话，按更新时间倒序。

    优先从 SQLite 读取（进程重启后 SQLite 是事实来源），
    合并内存中尚未写入的新 session。
    """
    sqlite_sessions = _list_persisted_sessions()
    seen = {s["session_id"] for s in sqlite_sessions}

    # 合并内存中有但 SQLite 暂无的 session
    for sid, data in _sessions.items():
        if sid not in seen:
            sqlite_sessions.append({
                "session_id": sid,
                "title": data.get("title", sid),
                "message_count": len(data.get("messages", [])),
                "updated_at": data.get("updated_at", ""),
            })
    sqlite_sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return sqlite_sessions


def delete_session(session_id: str) -> bool:
    """软删除会话（内存清除 + SQLite archive）。"""
    in_memory = session_id in _sessions
    if in_memory:
        del _sessions[session_id]
    archived = archive_chat_session(session_id)
    return in_memory or archived


# ── 消息操作 ──


def add_message(
    session_id: str,
    msg_type: str,
    content: str,
    rag_trace: Optional[dict] = None,
):
    """向会话追加一条消息（内存 + SQLite）。"""
    session = get_session(session_id)
    if not session:
        session = create_session(session_id)

    # 写内存
    session["messages"].append({
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "rag_trace": rag_trace,
    })
    session["updated_at"] = datetime.now().isoformat()

    # 写 SQLite
    role = "human" if msg_type == "human" else "ai"
    append_chat_message(session_id, role, content, rag_trace=rag_trace)


def get_messages(session_id: str) -> list[dict]:
    """获取会话的消息列表。"""
    session = get_session(session_id)
    if not session:
        return []
    return session["messages"]


def get_recipe_context(session_id: str) -> dict:
    """获取当前 session 的菜谱上下文槽位。"""
    session = get_session(session_id)
    if not session:
        return empty_recipe_context()
    context = session.get("recipe_context")
    if not isinstance(context, dict):
        context = empty_recipe_context()
        session["recipe_context"] = context
    return context


def update_recipe_context(session_id: str, recipe_context: dict):
    """更新当前 session 的菜谱上下文槽位（内存 + SQLite）。"""
    session = get_session(session_id)
    if not session:
        session = create_session(session_id)

    merged = {**empty_recipe_context(), **recipe_context}
    session["recipe_context"] = merged
    session["updated_at"] = datetime.now().isoformat()

    _persist_recipe_context(session_id, merged)


def update_session_title(session_id: str, title: str):
    """更新会话标题（内存 + SQLite）。"""
    session = get_session(session_id)
    if session:
        session["title"] = title
        session["updated_at"] = datetime.now().isoformat()
        upsert_chat_session(session_id, title=title)


def clear_sessions():
    """清空所有会话（仅测试用）。"""
    _sessions.clear()
