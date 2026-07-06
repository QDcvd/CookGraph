"""Pydantic 请求/响应模型"""

from pydantic import BaseModel
from typing import Optional


# ── Auth ──

class AuthRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"
    admin_code: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    username: str
    role: str


class UserInfo(BaseModel):
    username: str
    role: str


# ── Session ──

class SessionListItem(BaseModel):
    session_id: str
    title: str
    message_count: int
    updated_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class MessageItem(BaseModel):
    type: str  # "human" | "ai"
    content: str
    timestamp: str
    rag_trace: Optional[dict] = None


class SessionDetailResponse(BaseModel):
    messages: list[MessageItem]


# ── Chat ──

class ChatRequest(BaseModel):
    message: str
    session_id: str
