"""MiniCookingAgent-Demo 的对话上下文组装模块。

将上下文处理逻辑从 FastAPI 路由中抽离，采用 Zleap 风格：
将会话消息投影为模型可用的历史条目，
上一轮的工具调用和结果保持附加在对话中，而不是退化为纯文本的 assistant 消息。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.preference_memory import render_preferences_for_memory
from backend.session_recipe_context import render_recipe_context


MAX_HISTORY_MESSAGES = 12      # 保留的最近历史消息数
MAX_TRACE_TOOL_CALLS = 6       # 每条 trace 最多还原的工具调用数
MAX_TOOL_RESULT_CHARS = 1200   # 工具结果摘要最大字符数
MAX_CONTEXT_PATHS = 8          # 上下文中最多包含的文件路径数
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s，。；;：:\n\r]+")


def build_agent_history(session_messages: list[dict]) -> list[dict]:
    """从存储的会话消息构建模型可用的历史记录。

    旧的实现只传了 {role, content}，丢失了工具调用中的重要信息：
    调用了哪个工具、什么参数、返回了什么结果。
    此函数保留最近的 human/assistant 消息，并将 rag_trace 展开为
    合成的 tool-call/tool-result 对。
    """
    recent_messages = session_messages[-MAX_HISTORY_MESSAGES:]
    history: list[dict] = []

    for index, msg in enumerate(recent_messages):
        msg_type = msg.get("type")
        content = str(msg.get("content") or "")
        if msg_type == "human":
            history.append({"role": "user", "content": content})
            continue

        if msg_type != "ai":
            continue

        history.append({
            "role": "assistant",
            "content": content,
            "rag_trace": msg.get("rag_trace"),
        })
        history.extend(_trace_to_history_entries(msg.get("rag_trace"), index))

    return history


def build_runtime_memory_context(preferences: list[dict] | None = None, recipe_context: dict | None = None) -> str:
    """渲染 Zleap-lite runtime memory 注入块。"""
    parts = [
        render_preferences_for_memory(preferences or []),
        render_recipe_context(recipe_context or {}),
    ]
    body = "\n\n".join(part for part in parts if part.strip())
    if not body:
        return ""

    return "\n".join([
        "<runtime_memory>",
        body,
        "",
        "使用规则：",
        "- 用户说“它/这道菜/刚才那道菜/这个火候”时，优先指向当前会话最近菜品。",
        "- 用户长期偏好是跨会话约束，推荐或改写菜谱时必须主动考虑。",
        "- 当前会话菜谱上下文只用于本 session，不代表用户长期偏好。",
        "- 如果最新工具结果与 runtime memory 冲突，以最新工具结果为准并纠正旧上下文。",
        "</runtime_memory>",
    ])


def history_context_summary(history: list[dict]) -> str:
    """从历史记录中提取可复用的结构化上下文摘要。"""
    paths = recent_context_paths(history)[:MAX_CONTEXT_PATHS]
    if not paths:
        return ""

    lines = [
        "上一轮可复用的结构化上下文：",
        "相关文件：",
        *[f"- {path}" for path in paths],
        "如果用户用“它/他/这个/那/上面/刚才”追问，优先理解为在追问这些文件、上一轮工具结果或上一轮结论。",
        "不要把文件名或上一轮主题里的“搜索/实现/功能”等词误判为新的联网搜索请求。",
    ]
    return "\n".join(lines)


def recent_context_paths(history: list[dict]) -> list[str]:
    """从历史记录的 rag_trace 和内容中提取最近引用的文件路径。"""
    paths: list[str] = []

    def add_path(value: Any) -> None:
        if not value:
            return
        path = str(value).strip().strip("`'\"")
        path = path.rstrip("。；;,，")
        if path and path not in paths:
            paths.append(path)

    for item in reversed(history[-MAX_HISTORY_MESSAGES:]):
        trace = item.get("rag_trace") or {}
        if isinstance(trace, dict):
            for key in ("read_files", "matched_files"):
                values = trace.get(key) or []
                if isinstance(values, list):
                    for value in reversed(values):
                        add_path(value)
            for chunk in reversed(trace.get("retrieved_chunks") or []):
                if isinstance(chunk, dict):
                    add_path(chunk.get("filename"))

        for key in ("path", "filename", "source"):
            add_path(item.get(key))

        content = str(item.get("content", ""))
        for match in reversed(WINDOWS_PATH_PATTERN.findall(content)):
            add_path(match)

    existing_files = [path for path in paths if Path(path).is_file()]
    return existing_files or paths


def context_followup_tool_call(user_text: str, history: list[dict] | None = None) -> dict | None:
    """将上下文追问解析为最近的具体上下文对象的工具调用。

    本模块故意不处理通用工具意图（如网络搜索）。
    工具选择应由 agent 适配器处理，因为只有运行时知道已注册的工具列表。
    """
    text = user_text.strip()
    history = history or []

    if looks_like_context_followup(text):
        context_paths = recent_context_paths(history)
        if context_paths:
            return {"name": "read_file_tool", "args": {"path": context_paths[0]}}

    return None


def looks_like_context_followup(user_text: str) -> bool:
    """判断用户输入是否看起来像在追问上一轮的上下文。"""
    text = user_text.strip()
    followup_markers = ["那", "那么", "他", "它", "这个", "这个文件", "刚才", "上面", "上述", "前面", "你刚"]
    question_markers = ["怎么", "如何", "为什么", "讲讲", "解释", "实现", "内容", "里面", "方式", "原理"]
    return any(marker in text for marker in followup_markers) and any(marker in text for marker in question_markers)


def _trace_to_history_entries(trace: Any, message_index: int) -> list[dict]:
    """将 rag_trace 展开为 synthentic tool_call + tool_result 历史条目。"""
    if not isinstance(trace, dict):
        return []

    entries: list[dict] = []
    tool_calls = trace.get("tool_calls") or []
    if isinstance(tool_calls, list):
        for index, call in enumerate(tool_calls[-MAX_TRACE_TOOL_CALLS:]):
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool_name") or call.get("name") or "tool")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            result = str(call.get("output_preview") or "").strip()
            call_id = f"history_{message_index}_{index}_{_safe_id(tool_name)}"
            entries.append({
                "role": "assistant_tool_call",
                "content": f"历史工具调用：{tool_name}",
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "args": args,
            })
            entries.append({
                "role": "tool",
                "content": _truncate(result or "历史工具调用没有记录返回内容。", MAX_TOOL_RESULT_CHARS),
                "tool_call_id": call_id,
                "tool_name": tool_name,
            })

    context_note = _trace_context_note(trace)
    if context_note:
        entries.append({
            "role": "context",
            "content": context_note,
            "rag_trace": trace,
        })
    return entries


def _trace_context_note(trace: dict) -> str:
    """从 trace 中生成人类可读的上下文摘要文本。"""
    lines = ["历史工具上下文摘要："]
    matched_files = [str(path) for path in trace.get("matched_files") or [] if str(path).strip()]
    read_files = [str(path) for path in trace.get("read_files") or [] if str(path).strip()]
    searched_paths = trace.get("searched_paths") or []
    retrieved_chunks = trace.get("retrieved_chunks") or []

    if matched_files:
        lines.append("匹配文件：")
        lines.extend(f"- {path}" for path in matched_files[:MAX_CONTEXT_PATHS])
    if read_files:
        lines.append("已读取文件：")
        lines.extend(f"- {path}" for path in read_files[:MAX_CONTEXT_PATHS])
    if searched_paths:
        lines.append("搜索路径：")
        lines.append(_truncate(json.dumps(searched_paths[:4], ensure_ascii=False), 600))
    if isinstance(retrieved_chunks, list) and retrieved_chunks:
        lines.append("检索片段：")
        for chunk in retrieved_chunks[:4]:
            if not isinstance(chunk, dict):
                continue
            filename = str(chunk.get("filename") or "工具结果")
            text = str(chunk.get("text") or "").strip()
            if text:
                lines.append(f"- 来源：{filename}\n  摘要：{_truncate(text, 260)}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _safe_id(value: str) -> str:
    """将任意字符串转换为安全的 tool_call_id。"""
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "_", value).strip("_") or "tool"


def _truncate(text: str, limit: int) -> str:
    """截断文本到指定长度，末尾添加截断标记。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...(截断)"
