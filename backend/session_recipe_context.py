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
        "last_reverse_candidates": [],
        "last_reverse_source_query": None,
        "pending_recipe_web_search": None,
        "pending_clarification": None,
        "last_tool_names": [],
        "updated_at": None,
    }


def update_context_from_trace(existing: dict | None, user_text: str, rag_trace: dict | None) -> dict:
    context = {**empty_recipe_context(), **(existing or {})}
    if not isinstance(rag_trace, dict):
        return context

    pending_clarification = rag_trace.get("pending_clarification")
    if isinstance(pending_clarification, dict):
        context["pending_clarification"] = pending_clarification
        context["updated_at"] = datetime.now().isoformat()
        return context
    if isinstance(context.get("pending_clarification"), dict):
        context["pending_clarification"] = None
        context["updated_at"] = datetime.now().isoformat()

    tool_calls = [call for call in rag_trace.get("tool_calls") or [] if isinstance(call, dict)]
    if not tool_calls:
        return context

    tool_names = [str(call.get("tool_name") or call.get("name") or "") for call in tool_calls]
    context["last_tool_names"] = [name for name in tool_names if name]

    recipe_calls = [call for call in tool_calls if str(call.get("tool_name") or call.get("name")) == "recipe_query_tool"]
    web_calls = [call for call in tool_calls if str(call.get("tool_name") or call.get("name")) == "web_search_tool"]

    if recipe_calls:
        latest_recipe = recipe_calls[-1]
        args = latest_recipe.get("args") if isinstance(latest_recipe.get("args"), dict) else {}
        output = str(latest_recipe.get("output_preview") or "")
        dish = _dish_from_trace_or_output(rag_trace, output)
        reverse_candidates = _reverse_candidates_from_output(output)
        if reverse_candidates:
            context["last_reverse_candidates"] = reverse_candidates
            context["last_reverse_source_query"] = str(args.get("query") or user_text)
            context["updated_at"] = datetime.now().isoformat()
        if dish and _recipe_result_is_success(output) and _recipe_context_write_is_consistent(user_text, dish, rag_trace, args):
            context["last_dish"] = dish
            context["last_query"] = user_text
            context["last_recipe_tool_result_summary"] = _summarize(output)
            context["last_reverse_candidates"] = []
            context["last_reverse_source_query"] = None
            context["pending_recipe_web_search"] = None
            context["pending_clarification"] = None
            context["updated_at"] = datetime.now().isoformat()
        elif _recipe_result_is_web_search_offer(output):
            context["last_reverse_candidates"] = []
            context["last_reverse_source_query"] = None
            context["pending_recipe_web_search"] = {
                "type": "recipe_web_search_offer",
                "original_query": str(args.get("query") or user_text),
                "recipe_miss_summary": _summarize(output),
                "created_at": datetime.now().isoformat(),
            }
            context["updated_at"] = datetime.now().isoformat()

    if web_calls:
        latest_web = web_calls[-1]
        args = latest_web.get("args") if isinstance(latest_web.get("args"), dict) else {}
        output = str(latest_web.get("output_preview") or "")
        context["last_web_fallback_query"] = str(args.get("query") or user_text)
        context["last_web_fallback_summary"] = _summarize(output)
        context["last_reverse_candidates"] = []
        context["last_reverse_source_query"] = None
        context["pending_recipe_web_search"] = None
        context["pending_clarification"] = None
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
    reverse_candidates = context.get("last_reverse_candidates")
    if isinstance(reverse_candidates, list) and reverse_candidates:
        lines.append(f"- 最近反向查询候选菜：{'、'.join(str(item) for item in reverse_candidates if item)}")
    pending = context.get("pending_recipe_web_search") if isinstance(context.get("pending_recipe_web_search"), dict) else None
    if pending and pending.get("original_query"):
        lines.append(f"- 待确认联网菜谱问题：{pending['original_query']}")
    clarification = context.get("pending_clarification") if isinstance(context.get("pending_clarification"), dict) else None
    if clarification:
        pending_type = str(clarification.get("type") or "")
        payload = clarification.get("payload") if isinstance(clarification.get("payload"), dict) else {}
        original_query = str(payload.get("original_query") or "").strip()
        if original_query:
            lines.append(f"- 待澄清菜谱问题({pending_type})：{original_query}")
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


def _reverse_candidates_from_output(output: str) -> list[str]:
    text = str(output or "")
    if "query_type: entity_lookup" not in text and '"plan_type": "entity_lookup"' not in text:
        return []
    candidates: list[str] = []
    for dish in re.findall(r'"dish_name"\s*:\s*"([^"]+)"', text):
        if dish and dish not in candidates:
            candidates.append(dish)
    for dish in re.findall(r"^\s*\d+[.、]\s*([^（(\n\r]{1,30})", text, flags=re.MULTILINE):
        dish = dish.strip()
        if dish and dish not in candidates:
            candidates.append(dish)
    return candidates


def _recipe_result_is_success(output: str) -> bool:
    text = str(output or "")
    if "success: False" in text or "web_fallback_allowed: True" in text and "本地图谱未命中" in text:
        return False
    if "完整档案" in text or "为您找到相似菜品" in text or "标准菜名=" in text:
        return True
    return bool(text.strip()) and "未找到菜品" not in text and "无法理解的查询格式" not in text


def _recipe_result_is_web_search_offer(output: str) -> bool:
    text = str(output or "")
    return "success: False" in text and "web_search_offer: True" in text


def _recipe_context_write_is_consistent(user_text: str, dish: str, trace: dict, args: dict) -> bool:
    context_followup = trace.get("context_followup")
    if isinstance(context_followup, dict) and context_followup.get("used"):
        return str(context_followup.get("source_dish") or "") == dish

    user = re.sub(r"\s+", "", str(user_text or ""))
    query = re.sub(r"\s+", "", str(args.get("query") or ""))
    dish_text = re.sub(r"\s+", "", str(dish or ""))
    if dish_text and dish_text in user:
        return True

    reverse_markers = ["有多少种做法", "有多少做法", "多少种做法", "可以做什么菜", "能做什么菜", "有哪些菜", "有什么菜", "推荐"]
    if any(marker in user for marker in reverse_markers):
        return False

    recipe_markers = ["怎么做", "做法", "咋做", "如何做"]
    if any(marker in user for marker in recipe_markers) and dish_text and dish_text not in user:
        return False

    if dish_text and dish_text in query:
        return True

    return True


def _summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "...(截断)"
