"""内存会话存储（Plan 要求：不引用 PostgreSQL/Redis）"""

from datetime import datetime
from typing import Optional

# 内存存储：{ session_id: { title, updated_at, messages } }
_sessions: dict = {}

# ── 会话 CRUD ──

def create_session(session_id: str, title: str = "新对话") -> dict:
    """创建新会话"""
    now = datetime.now().isoformat()
    _sessions[session_id] = {
        "title": title,
        "updated_at": now,
        "messages": [],
    }
    return _sessions[session_id]


def get_session(session_id: str) -> Optional[dict]:
    """获取单个会话"""
    return _sessions.get(session_id)


def list_sessions() -> list[dict]:
    """列出所有会话，按更新时间倒序"""
    result = []
    for sid, data in _sessions.items():
        result.append({
            "session_id": sid,
            "title": data["title"],
            "message_count": len(data["messages"]),
            "updated_at": data["updated_at"],
        })
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result


def delete_session(session_id: str) -> bool:
    """删除会话"""
    if session_id in _sessions:
        del _sessions[session_id]
        return True
    return False


# ── 消息操作 ──

def add_message(session_id: str, msg_type: str, content: str, rag_trace: Optional[dict] = None):
    """向会话追加一条消息"""
    session = get_session(session_id)
    if not session:
        session = create_session(session_id)
    session["messages"].append({
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "rag_trace": rag_trace,
    })
    session["updated_at"] = datetime.now().isoformat()


def get_messages(session_id: str) -> list[dict]:
    """获取会话的消息列表"""
    session = get_session(session_id)
    if not session:
        return []
    return session["messages"]


def update_session_title(session_id: str, title: str):
    """更新会话标题"""
    session = get_session(session_id)
    if session:
        session["title"] = title
        session["updated_at"] = datetime.now().isoformat()


def clear_sessions():
    """清空所有会话（仅测试用）"""
    _sessions.clear()
