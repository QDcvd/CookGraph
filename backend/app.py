"""FastAPI 主应用 — 认证 / 会话 / 流式聊天 / 文档桩"""

import asyncio
import json
import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.staticfiles import StaticFiles

from backend.chat_persistence import init_db as init_chat_db
from backend.schemas import AuthRequest, AuthResponse, UserInfo, ChatRequest
from backend.memory_store import (
    create_session,
    list_sessions,
    get_session,
    delete_session,
    add_message,
    get_messages,
    get_recipe_context,
    update_recipe_context,
    update_session_title,
)
from backend.context_manager import build_agent_history, build_runtime_memory_context
from backend.preference_memory import apply_preference_actions, list_preferences
from backend.session_recipe_context import update_context_from_trace
from backend.rag_stub import router as rag_router
import importlib

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 动态导入 Agent 适配器（通过环境变量 AGENT_ADAPTER 切换）
#   agent_adapter           → DeepSeek API
#   agent_adapter_local_LLM → 本地 vLLM
_adapter_name = os.getenv("AGENT_ADAPTER", "agent_adapter")
_adapter = importlib.import_module(f"backend.{_adapter_name}")
stream_search_agent = _adapter.stream_search_agent
ERROR_LOG = Path(__file__).resolve().parent.parent / "backend-error.log"
print(
    "[adapter] "
    f"AGENT_ADAPTER={_adapter_name} "
    f"module={getattr(_adapter, '__file__', 'unknown')} "
    f"MINICOOK_LLM_DEBUG={os.getenv('MINICOOK_LLM_DEBUG', '')}",
    flush=True,
)

# ── FastAPI 应用 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化持久化表。"""
    init_chat_db()
    print("[app] chat_persistence 表已就绪", flush=True)
    yield


app = FastAPI(title="MiniCookingAgent-Demo", lifespan=lifespan)

# CORS：允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 RAG 桩路由
app.include_router(rag_router)


# ════════════════════════════════════════════════════════════════
# Auth — Mock 认证（Plan 要求：不验证密码，不生成 JWT）
# ════════════════════════════════════════════════════════════════

def _get_token_from_header(request: Request) -> str | None:
    """从 Authorization header 提取 token（不做验证）"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


@app.post("/auth/login", response_model=AuthResponse)
async def login(body: AuthRequest):
    """模拟登录 — 接受任意用户名/密码"""
    return AuthResponse(
        access_token="dev-token",
        username=body.username,
        role=body.role or "user",
    )


@app.post("/auth/register", response_model=AuthResponse)
async def register(body: AuthRequest):
    """模拟注册 — 与 login 行为一致"""
    return AuthResponse(
        access_token="dev-token",
        username=body.username,
        role=body.role or "user",
    )


@app.get("/auth/me", response_model=UserInfo)
async def me(request: Request):
    """模拟获取当前用户信息"""
    token = _get_token_from_header(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    return UserInfo(username="dev-user", role="user")


# ════════════════════════════════════════════════════════════════
# Sessions — 内存会话管理
# ════════════════════════════════════════════════════════════════

@app.get("/sessions")
async def get_sessions():
    """获取所有会话列表"""
    return {"sessions": list_sessions()}


@app.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取单个会话的完整消息"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"messages": get_messages(session_id)}


@app.delete("/sessions/{session_id}")
async def remove_session(session_id: str):
    """删除会话"""
    if delete_session(session_id):
        return {"message": "会话已删除"}
    raise HTTPException(status_code=404, detail="会话不存在")


# ════════════════════════════════════════════════════════════════
# Chat — SSE 流式聊天
# ════════════════════════════════════════════════════════════════

@app.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    """流式聊天接口 — 返回 SSE 事件流"""
    if os.getenv("MINICOOK_LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on", "debug"}:
        print(
            f"[debug] 收到聊天请求 session_id={body.session_id} message={body.message!r}",
            flush=True,
        )

    # 确保会话存在
    session = get_session(body.session_id)
    if not session:
        create_session(body.session_id)
        update_session_title(body.session_id, body.message[:20])

    # 保存用户消息
    add_message(body.session_id, "human", body.message)
    apply_preference_actions(body.message, source_session_id=body.session_id)

    # 构造历史消息格式（给 agent_adapter 使用）
    all_msgs = get_messages(body.session_id)
    # DEPRECATED: 不再用粗暴的 {role, content} 历史列表。
    # 那种做法会丢掉上一轮工具调用、工具结果和 rag_trace，导致“那/它/这个文件”
    # 这类追问失去指向。现在统一交给 context_manager 做 Zleap 风格的上下文投影：
    # message + synthetic tool_call/tool_result + trace context note。
    history = build_agent_history(all_msgs[:-1])  # 排除刚加入的用户消息
    runtime_memory = build_runtime_memory_context(
        preferences=list_preferences(),
        recipe_context=get_recipe_context(body.session_id),
    )
    if runtime_memory:
        history.insert(0, {"role": "runtime_memory", "content": runtime_memory})

    async def event_generator():
        """SSE 事件生成器"""
        full_response = ""
        rag_trace = None
        try:
            async for event in stream_search_agent(body.message, history):
                if os.getenv("MINICOOK_LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on", "debug"}:
                    print(f"[debug] SSE事件 type={event.get('type')}", flush=True)
                if event.get("type") == "content":
                    full_response += event.get("content", "")
                elif event.get("type") == "trace":
                    rag_trace = event.get("rag_trace")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 保存 AI 回复到会话（从 stream 中收集完整文本）
            if full_response:
                add_message(body.session_id, "ai", full_response, rag_trace=rag_trace)
                if rag_trace:
                    next_context = update_context_from_trace(
                        get_recipe_context(body.session_id),
                        body.message,
                        rag_trace,
                    )
                    update_recipe_context(body.session_id, next_context)
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_text = traceback.format_exc()
            print(error_text, flush=True)
            ERROR_LOG.write_text(error_text, encoding="utf-8")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ════════════════════════════════════════════════════════════════
# 静态前端挂载（仅当 frontend/dist 存在时）
# ════════════════════════════════════════════════════════════════

_frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")


# ════════════════════════════════════════════════════════════════
# 启动入口
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.app:app", host=host, port=port, reload=True)
