"""Agent-first query router for MiniCookingAgent-Demo.

This layer runs before the model tool loop. It turns the latest user message
plus session context into a small execution action, so normal recipe questions
do not depend on the model freely deciding whether to call tools.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from backend.clarification_gate import build_choice_prompt, decide_clarification
from backend.query_understanding import QueryIntent, classify_intent
from backend.recipe_query_adapter import kg_dish_names


RouterActionType = Literal[
    "content",
    "tool",
    "direct_chat",
    "fallback_tool_loop",
]


@dataclass
class QueryAction:
    action: RouterActionType
    reason: str
    source: str = "query_router"
    tool_name: str | None = None
    query: str | None = None
    answer_user_text: str | None = None
    content: str | None = None
    pending_clarification: dict | None = None
    choice_prompt: dict | None = None
    intent: QueryIntent | None = None
    confidence: float = 0.0

    def to_trace(self) -> dict:
        payload = asdict(self)
        intent = payload.pop("intent", None)
        if isinstance(intent, dict):
            payload["intent"] = intent
        return {key: value for key, value in payload.items() if value is not None}


def route_query(user_text: str, history: list[dict] | None = None) -> QueryAction:
    """Return the first-hop action for the current user query."""
    history = history or []
    text = str(user_text or "").strip()
    if not text:
        return QueryAction(action="direct_chat", content="", reason="空输入")

    try:
        dish_names = kg_dish_names()
    except Exception:
        dish_names = set()

    recipe_context = _recipe_context_from_history(history)
    clarification = decide_clarification(text, dish_names=dish_names, history=history)
    contextual_attr_query = _contextual_attribute_query(
        text,
        recipe_context,
        dish_names=dish_names,
    )
    if contextual_attr_query:
        return QueryAction(
            action="tool",
            tool_name="recipe_query_tool",
            query=contextual_attr_query,
            answer_user_text=contextual_attr_query,
            reason="前置路由根据当前会话菜品补全属性追问",
            confidence=0.95,
        )

    if clarification.action == "ask":
        if (
            clarification.pending_type == "missing_recipe_target"
            and recipe_context.get("current_dish")
        ):
            clarification = None  # type: ignore[assignment]
        else:
            pending = {
                "type": clarification.pending_type,
                "payload": clarification.pending_payload or {},
                "question": clarification.question,
                "reason": clarification.reason,
            }
            return QueryAction(
                action="content",
                content=clarification.question or "我需要先确认一下你的意思。",
                pending_clarification=pending,
                choice_prompt=build_choice_prompt(clarification),
                reason=clarification.reason,
                confidence=1.0,
            )

    if clarification is not None and clarification.action == "execute":
        return QueryAction(
            action="tool",
            tool_name=clarification.tool_name or "recipe_query_tool",
            query=clarification.query or text,
            reason=clarification.reason,
            confidence=1.0,
        )

    intent = classify_intent(text, dish_names=dish_names, recipe_context=recipe_context)

    if intent.intent == "greeting":
        return QueryAction(
            action="direct_chat",
            content=_friendly_greeting(),
            reason=intent.reason or "打招呼",
            intent=intent,
            confidence=intent.confidence,
        )

    if intent.intent == "non_recipe_query":
        return QueryAction(
            action="direct_chat",
            content=_friendly_non_recipe_reply(),
            reason=intent.reason or "非菜谱问题",
            intent=intent,
            confidence=intent.confidence,
        )

    if intent.intent == "ambiguous_query":
        content = "我有点不确定你想查哪一种含义，可以再具体说一下吗？"
        return QueryAction(
            action="content",
            content=content,
            reason=intent.reason or "意图不明确",
            intent=intent,
            confidence=intent.confidence,
        )

    if intent.intent == "recipe_followup_query":
        if intent.resolved_query:
            return QueryAction(
                action="tool",
                tool_name="recipe_query_tool",
                query=intent.resolved_query,
                answer_user_text=intent.resolved_query,
                reason=intent.reason or "上下文追问已补全",
                intent=intent,
                confidence=intent.confidence,
            )
        return QueryAction(
            action="content",
            content="可以的，你先告诉我是哪道菜，我再帮你查。",
            reason=intent.reason or "上下文追问缺少可继承菜名",
            intent=intent,
            confidence=intent.confidence,
        )

    if intent.intent in {"forward_recipe_query", "forward_unknown_recipe_query", "reverse_query"}:
        query = intent.resolved_query or text
        return QueryAction(
            action="tool",
            tool_name="recipe_query_tool",
            query=query,
            reason=intent.reason or intent.intent,
            intent=intent,
            confidence=intent.confidence,
        )

    return QueryAction(
        action="fallback_tool_loop",
        reason=f"router 未覆盖意图: {intent.intent}",
        intent=intent,
        confidence=intent.confidence,
    )


def _recipe_context_from_history(history: list[dict]) -> dict:
    context: dict[str, str] = {}
    for item in reversed(history or []):
        if str(item.get("role") or "").lower() == "user" and not context.get("last_query"):
            context["last_query"] = str(item.get("content") or "")[:160]
        if str(item.get("role") or "").lower() in {"assistant", "ai"}:
            content = str(item.get("content") or "")
            if content and not context.get("last_answer_head"):
                context["last_answer_head"] = content[:220]
            trace = item.get("rag_trace") if isinstance(item.get("rag_trace"), dict) else {}
            for call in reversed(trace.get("tool_calls") or []):
                if not isinstance(call, dict):
                    continue
                tool_name = str(call.get("tool_name") or call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                query = str(args.get("query") or "")
                output = str(call.get("output_preview") or "")
                dish = ""
                if tool_name == "recipe_query_tool":
                    dish = _extract_dish_from_tool_output(output)
                dish = dish or _extract_dish_from_query(query)
                if dish:
                    context["current_dish"] = dish
                    return context
    return context


def _extract_dish_from_query(query: str) -> str:
    text = str(query or "").strip()
    for marker in ("怎么做", "的做法", "火力", "火候", "调料", "配料", "用料"):
        if marker in text:
            candidate = text.split(marker, 1)[0].strip("，,。 的")
            if 2 <= len(candidate) <= 12:
                return candidate
    return ""


def _contextual_attribute_query(
    text: str,
    recipe_context: dict,
    *,
    dish_names: set[str] | None = None,
) -> str:
    dish = str(recipe_context.get("current_dish") or "").strip()
    if not dish:
        return ""
    normalized = str(text or "").strip()
    compact = "".join(normalized.split())
    explicit_dish = _known_dish_in_text(compact, dish_names or set())
    if explicit_dish and explicit_dish != dish:
        return ""
    if any(marker in compact for marker in ("火力", "火候", "调火", "温度")):
        if dish not in compact:
            return f"{dish}的火力要怎么样"
    if any(marker in compact for marker in ("调料", "配料", "用料", "材料")):
        if dish not in compact:
            return f"{dish}需要哪些调料和配料"
    if any(marker in compact for marker in ("注意事项", "注意点", "难点", "要点")):
        if dish not in compact:
            return f"{dish}的注意事项"
    return ""


def _known_dish_in_text(text: str, dish_names: set[str]) -> str:
    compact = "".join(str(text or "").split())
    matches = [name for name in dish_names if name and name in compact]
    if not matches:
        return ""
    return max(matches, key=len)


def _extract_dish_from_tool_output(output: str) -> str:
    text = str(output or "")
    markers = ["【查询结果】 菜品：", "菜品："]
    for marker in markers:
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1]
        tail = tail.splitlines()[0].strip()
        tail = tail.split()[0].strip("：:，,。")
        if 2 <= len(tail) <= 12:
            return tail
    archive = re.match(r"^【(.{2,12}) 完整档案】", text)
    if archive:
        return archive.group(1).strip()
    return ""


def _friendly_greeting() -> str:
    return "你好呀，我可以帮你查本地菜谱、做法、用料、火力控制，也可以在本地没有收录时帮你联网找参考。"


def _friendly_non_recipe_reply() -> str:
    return "这个问题看起来不像菜谱查询。我主要擅长查菜谱、食材、做法和火力控制；你可以问我一道菜怎么做。"
