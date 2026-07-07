"""Session-scoped recipe context for Zleap-lite memory."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


SUMMARY_CHARS = 420


def empty_recipe_context() -> dict:
    return {
        "last_dish": None,
        "last_query": None,
        "last_recipe_tool_result_summary": None,
        "last_web_fallback_query": None,
        "last_web_fallback_summary": None,
        "last_tool_names": [],
        "updated_at": None,
    }


def update_context_from_trace(existing: dict | None, user_text: str, rag_trace: dict | None) -> dict:
    context = {**empty_recipe_context(), **(existing or {})}
    if not isinstance(rag_trace, dict):
        return context

    tool_calls = [call for call in rag_trace.get("tool_calls") or [] if isinstance(call, dict)]
    if not tool_calls:
        return context

    tool_names = [str(call.get("tool_name") or call.get("name") or "") for call in tool_calls]
    context["last_tool_names"] = [name for name in tool_names if name]

    recipe_calls = [call for call in tool_calls if str(call.get("tool_name") or call.get("name")) == "recipe_query_tool"]
    web_calls = [call for call in tool_calls if str(call.get("tool_name") or call.get("name")) == "web_search_tool"]

    if recipe_calls:
        latest_recipe = recipe_calls[-1]
        output = str(latest_recipe.get("output_preview") or "")
        dish = _dish_from_trace_or_output(rag_trace, output)
        if dish and _recipe_result_is_success(output):
            context["last_dish"] = dish
            context["last_query"] = user_text
            context["last_recipe_tool_result_summary"] = _summarize(output)
            context["updated_at"] = datetime.now().isoformat()

    if web_calls:
        latest_web = web_calls[-1]
        args = latest_web.get("args") if isinstance(latest_web.get("args"), dict) else {}
        output = str(latest_web.get("output_preview") or "")
        context["last_web_fallback_query"] = str(args.get("query") or user_text)
        context["last_web_fallback_summary"] = _summarize(output)
        context["updated_at"] = datetime.now().isoformat()

    return context


def render_recipe_context(context: dict | None) -> str:
    if not context:
        return ""
    lines = ["当前会话菜谱上下文："]
    if context.get("last_dish"):
        lines.append(f"- 最近菜品：{context['last_dish']}")
    if context.get("last_query"):
        lines.append(f"- 最近问题：{context['last_query']}")
    if context.get("last_recipe_tool_result_summary"):
        lines.append(f"- 最近菜谱摘要：{context['last_recipe_tool_result_summary']}")
    if context.get("last_web_fallback_query"):
        lines.append(f"- 最近联网兜底问题：{context['last_web_fallback_query']}")
    if context.get("last_web_fallback_summary"):
        lines.append(f"- 最近联网摘要：{context['last_web_fallback_summary']}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _dish_from_trace_or_output(trace: dict, output: str) -> str:
    hybrid = trace.get("hybrid_retrieval")
    if isinstance(hybrid, dict):
        dish = str(hybrid.get("standard_dish") or "").strip()
        if dish:
            return dish

    patterns = [
        r"标准菜名=([^；\n]+)",
        r"为您找到相似菜品：\"([^\"]+)\"",
        r"为您找到相似菜品：“([^”]+)”",
        r"【([^】\n]{1,40}) 完整档案】",
    ]
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1).strip()
    return ""


def _recipe_result_is_success(output: str) -> bool:
    text = str(output or "")
    if "success: False" in text or "web_fallback_allowed: True" in text and "本地图谱未命中" in text:
        return False
    if "完整档案" in text or "为您找到相似菜品" in text or "标准菜名=" in text:
        return True
    return bool(text.strip()) and "未找到菜品" not in text and "无法理解的查询格式" not in text


def _summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "...(截断)"
