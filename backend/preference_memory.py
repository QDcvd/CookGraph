"""Zleap-lite preference memory backed by SQLite.

The first version is deliberately conservative: deterministic rules only,
soft-delete for removals, and active newest-first injection.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


DEFAULT_USER_ID = "default"
MAX_ACTIVE_PREFERENCES = 20
TEMPORARY_MARKERS = ("今天", "这次", "这顿", "临时", "现在想", "今晚", "中午", "明天")
THIRD_PARTY_MARKERS = ("朋友", "同事", "家人", "孩子", "爸", "妈", "对象", "老婆", "老公")


PreferenceActionType = Literal["remember", "archive"]


@dataclass(frozen=True)
class PreferenceAction:
    action: PreferenceActionType
    kind: str
    memory: str
    normalized_key: str
    confidence: float = 1.0


def memory_db_path() -> Path:
    raw = os.getenv("MINICOOK_MEMORY_DB", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "data" / "memory.sqlite3"


def init_db(path: Path | None = None) -> None:
    db_path = path or memory_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS preference_memory (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'default',
              kind TEXT NOT NULL,
              memory TEXT NOT NULL,
              normalized_key TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              confidence REAL NOT NULL DEFAULT 1.0,
              source_session_id TEXT,
              source_message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS preference_memory_active_idx
              ON preference_memory (user_id, status, updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS preference_memory_key_idx
              ON preference_memory (user_id, normalized_key, status)
            """
        )
        conn.commit()
    finally:
        conn.close()


def list_preferences(user_id: str = DEFAULT_USER_ID, limit: int = MAX_ACTIVE_PREFERENCES) -> list[dict]:
    init_db()
    safe_limit = max(1, min(limit, MAX_ACTIVE_PREFERENCES))
    conn = sqlite3.connect(memory_db_path())
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, user_id, kind, memory, normalized_key, status, confidence,
                   source_session_id, source_message, created_at, updated_at
            FROM preference_memory
            WHERE user_id = ? AND status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, safe_limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def remember_preference(
    *,
    kind: str,
    memory: str,
    normalized_key: str,
    user_id: str = DEFAULT_USER_ID,
    source_session_id: str | None = None,
    source_message: str | None = None,
    confidence: float = 1.0,
) -> dict:
    init_db()
    now = datetime.now().isoformat()
    preference_id = f"pref_{uuid.uuid4().hex[:20]}"
    conn = sqlite3.connect(memory_db_path())
    try:
        if normalized_key:
            conn.execute(
                """
                UPDATE preference_memory
                SET status = 'archived', updated_at = ?
                WHERE user_id = ? AND normalized_key = ? AND status = 'active'
                """,
                (now, user_id, normalized_key),
            )
        conn.execute(
            """
            INSERT INTO preference_memory
              (id, user_id, kind, memory, normalized_key, status, confidence,
               source_session_id, source_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                preference_id,
                user_id,
                kind,
                memory,
                normalized_key,
                confidence,
                source_session_id,
                source_message,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": preference_id,
        "user_id": user_id,
        "kind": kind,
        "memory": memory,
        "normalized_key": normalized_key,
        "status": "active",
        "confidence": confidence,
        "created_at": now,
        "updated_at": now,
    }


def archive_preference(normalized_key: str, user_id: str = DEFAULT_USER_ID) -> int:
    if not normalized_key:
        return 0
    init_db()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(memory_db_path())
    try:
        cursor = conn.execute(
            """
            UPDATE preference_memory
            SET status = 'archived', updated_at = ?
            WHERE user_id = ? AND normalized_key = ? AND status = 'active'
            """,
            (now, user_id, normalized_key),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def apply_preference_actions(
    user_text: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    source_session_id: str | None = None,
) -> list[dict]:
    results: list[dict] = []
    for action in extract_preference_actions(user_text):
        if action.action == "archive":
            count = archive_preference(action.normalized_key, user_id=user_id)
            results.append({"action": "archive", "normalized_key": action.normalized_key, "archived": count})
        else:
            saved = remember_preference(
                kind=action.kind,
                memory=action.memory,
                normalized_key=action.normalized_key,
                user_id=user_id,
                source_session_id=source_session_id,
                source_message=user_text,
                confidence=action.confidence,
            )
            results.append({"action": "remember", **saved})
    return results


def extract_preference_actions(user_text: str) -> list[PreferenceAction]:
    text = _normalize_text(user_text)
    if not text:
        return []
    if _is_temporary_or_third_party(text):
        return []

    archive = _extract_archive_action(text)
    if archive:
        return [archive]

    remember = _extract_remember_action(text)
    if remember:
        return [remember]
    return []


def render_preferences_for_memory(preferences: list[dict]) -> str:
    if not preferences:
        return ""
    lines = ["用户长期偏好："]
    for item in preferences[:MAX_ACTIVE_PREFERENCES]:
        kind = str(item.get("kind") or "preference")
        memory = str(item.get("memory") or "").strip()
        if memory:
            lines.append(f"- [{kind}] {memory}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _extract_archive_action(text: str) -> PreferenceAction | None:
    if any(marker in text for marker in ("忘掉", "别记", "不要记", "删除")):
        target = _extract_food_or_value(text)
        if target:
            return PreferenceAction("archive", "dietary_restriction", f"归档偏好：{target}", _key(target))
    match = re.search(r"我现在可以吃(?P<target>[^了。！？!?，,；;\s]+)", text)
    if match:
        target = _clean_value(match.group("target"))
        return PreferenceAction("archive", "dietary_restriction", f"归档偏好：可以吃{target}", _key(target))
    return None


def _extract_remember_action(text: str) -> PreferenceAction | None:
    patterns = [
        (r"我(?:不能|不可以|吃不了)吃(?P<value>[^。！？!?，,；;\s]+)", "dietary_restriction", "用户不能吃{value}。"),
        (r"我不吃(?P<value>[^。！？!?，,；;\s]+)", "dietary_restriction", "用户不吃{value}。"),
        (r"我对(?P<value>[^。！？!?，,；;\s]+)过敏", "dietary_restriction", "用户对{value}过敏。"),
        (r"我(?:喜欢|爱吃)(?P<value>[^。！？!?，,；;\s]+)(?:口味|味|菜)?", "taste_preference", "用户喜欢{value}。"),
        (r"我偏好(?P<value>[^。！？!?，,；;\s]+)", "taste_preference", "用户偏好{value}。"),
        (r"我家(?:没有|没)(?P<value>[^。！？!?，,；;\s]+)", "equipment", "用户家没有{value}。"),
        (r"以后.*?(?:尽量|都|要)(?P<value>少油少盐|少油|少盐|清淡|不辣|低脂|低糖)", "cooking_goal", "以后给用户推荐或改菜时尽量{value}。"),
        (r"偏好(?:改成|改为)(?P<value>少油少盐|少油|少盐|清淡|不辣|低脂|低糖)", "cooking_goal", "用户当前默认烹饪偏好改为{value}。"),
    ]
    for pattern, kind, template in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = _clean_value(match.group("value"))
        if not value:
            continue
        normalized_key = "default_cooking_goal" if kind == "cooking_goal" else _key(value)
        return PreferenceAction("remember", kind, template.format(value=value), normalized_key)
    return None


def _extract_food_or_value(text: str) -> str:
    for pattern in (
        r"(?:不能吃|不吃|不可以吃|吃不了)(?P<value>[^了。！？!?，,；;\s]+)",
        r"(?:偏好|喜欢|爱吃)(?P<value>[^了。！？!?，,；;\s]+)",
        r"(?:没有|没)(?P<value>[^了。！？!?，,；;\s]+)",
    ):
        match = re.search(pattern, text)
        if match:
            return _clean_value(match.group("value"))
    return ""


def _is_temporary_or_third_party(text: str) -> bool:
    return any(marker in text for marker in TEMPORARY_MARKERS) or any(marker in text for marker in THIRD_PARTY_MARKERS)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"[了呢吧啊呀哦嘛]+$", "", str(value or "").strip())
    return cleaned.strip("，,。；;！!?？")


def _key(value: str) -> str:
    return _clean_value(value).lower()
