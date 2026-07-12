"""统一的内部工具结果协议。

工具之间传递 JSON 兼容的 dict；只有交给 LangChain ToolMessage 时才序列化。
前端不直接展示这个对象，而由回答层读取字段后渲染自然语言。
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA_VERSION = "1.0"


def make_tool_result(
    *,
    tool: str,
    query_type: str,
    ok: bool,
    source: str,
    data: Any = None,
    message: str = "",
    web_fallback_allowed: bool = False,
    error: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建可跨模块传输的 JSON 兼容工具结果。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "tool": str(tool),
        "query_type": str(query_type),
        "source": str(source),
        "data": data,
        "message": str(message or ""),
        "web_fallback_allowed": bool(web_fallback_allowed),
        "error": error,
        "meta": meta or {},
    }


def error_result(
    *,
    tool: str,
    query_type: str,
    code: str,
    message: str,
    detail: str = "",
    source: str = "system",
    web_fallback_allowed: bool = False,
) -> dict[str, Any]:
    return make_tool_result(
        tool=tool,
        query_type=query_type,
        ok=False,
        source=source,
        message=message,
        web_fallback_allowed=web_fallback_allowed,
        error={"code": code, "detail": detail},
    )


def serialize_tool_result(result: Any) -> str:
    """将工具结果编码为发送给模型的 JSON 文本。"""
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if isinstance(result, str):
        return result
    return json.dumps({"value": result}, ensure_ascii=False, default=str)


def parse_tool_result(value: Any) -> dict[str, Any] | None:
    """读取 dict 或 JSON 文本；旧文本结果不伪装成结构化结果。"""
    if isinstance(value, dict) and value.get("schema_version"):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("schema_version") else None


def result_message(result: Any) -> str:
    parsed = parse_tool_result(result)
    if parsed is not None:
        return str(parsed.get("message") or "")
    return str(result or "")
