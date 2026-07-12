"""Agent-first query router for MiniCookingAgent-Demo.

This layer runs before the model tool loop. It turns the latest user message
plus session context into a small execution action, so normal recipe questions
do not depend on the model freely deciding whether to call tools.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from backend.clarification_gate import build_choice_prompt, decide_clarification
from backend.entity_resolver import extract_ingredient_slots_from_source, resolve as resolve_entities
from backend.query_understanding import (
    QueryFrame,
    classify_v2,
    enforce_query_frame_contract,
)
from backend.recipe_query_adapter import kg_dish_names, kg_entity_names
from backend.tool_result import parse_tool_result


RouterActionType = Literal[
    "content",
    "tool",
    "direct_chat",
]


@dataclass
class QueryAction:
    action: RouterActionType
    reason: str
    source: str = "query_router"
    tool_name: str | None = None
    query: str | None = None
    plan: dict | None = None
    answer_user_text: str | None = None
    content: str | None = None
    pending_clarification: dict | None = None
    choice_prompt: dict | None = None
    intent: dict | None = None
    query_frame: QueryFrame | None = None
    confidence: float = 0.0

    def to_trace(self) -> dict:
        payload = asdict(self)
        intent = payload.pop("intent", None)
        if isinstance(intent, dict):
            payload["intent"] = intent
        qf = payload.pop("query_frame", None)
        if isinstance(qf, dict):
            payload["query_frame"] = qf
        return {key: value for key, value in payload.items() if value is not None}


def _build_plan(frame: QueryFrame) -> dict:
    """将已解析的 QueryFrame 转换为 V2 执行 plan。"""
    mode = "dish"
    if frame.intent in ("ingredient_combo_query", "scenario_recommendation_query"):
        mode = "combo"
    elif frame.intent == "reverse_entity_query":
        mode = "combo"
    elif frame.intent == "missing_ingredients_query":
        mode = "missing"
    if frame.dish is not None and frame.attribute in {"seasonings", "ingredients", "prep"}:
        mode = "dish"

    dish_name = (frame.dish.canonical or frame.dish.raw) if frame.dish else (frame.dish_text or None)

    # cannibal resolution
    def _to_canonical(slots: list) -> list[str]:
        return [s.canonical or s.raw for s in slots if s.raw]

    plan: dict[str, Any] = {
        "intent": frame.intent,
        "mode": mode,
        "source_text": frame.source_text,
        "dish": dish_name,
        "field": frame.attribute,
        "ingredients": _to_canonical(frame.ingredients),
        "technique": None,
        "taste": None,
        "cuisine": None,
        "exclude": frame.exclusions or [],
        "scenario_tags": frame.scenario_tags or [],
        "seasonings": [],
        "limit": 20,
        "confidence": frame.confidence,
        "resolution": {
            "ingredients": [
                {"raw": s.raw, "canonical": s.canonical, "match_mode": s.match_mode, "confidence": s.confidence}
                for s in frame.ingredients
            ],
        },
    }

    if frame.techniques:
        plan["technique"] = frame.techniques[0].canonical or frame.techniques[0].raw
    if frame.tastes:
        plan["taste"] = frame.tastes[0].canonical or frame.tastes[0].raw
    if frame.cuisines:
        plan["cuisine"] = frame.cuisines[0].canonical or frame.cuisines[0].raw

    if frame.attribute == "full_recipe":
        plan["show_all"] = True
    if frame.attribute == "seasonings":
        plan["show_seasonings"] = True

    if frame.dish_candidates:
        plan["dish_candidates"] = [d.canonical or d.raw for d in frame.dish_candidates]

    return plan


def _action_from_frame(frame: QueryFrame) -> QueryAction:
    """Build the single recipe-tool action from a resolved QueryFrame."""
    if frame.intent == "greeting":
        return QueryAction(
            action="direct_chat",
            content=_friendly_greeting(),
            reason=frame.reason or "打招呼",
            query_frame=frame,
            confidence=frame.confidence,
        )
    if frame.intent == "non_recipe_query":
        return QueryAction(
            action="direct_chat",
            content=_friendly_non_recipe_reply(),
            reason=frame.reason or "非菜谱问题",
            query_frame=frame,
            confidence=frame.confidence,
        )
    if frame.intent == "ambiguous_query":
        return QueryAction(
            action="content",
            content=frame.clarification_question or "我有点不确定你想查哪一种含义，可以再具体说一下吗？",
            reason=frame.reason or "意图不明确",
            query_frame=frame,
            confidence=frame.confidence,
        )
    if frame.needs_clarification:
        return QueryAction(
            action="content",
            content=frame.clarification_question or "我猜你是在接着问上一道菜。告诉我具体菜名，我就能继续帮你查。",
            reason=frame.reason or "追问缺少上下文",
            query_frame=frame,
            confidence=frame.confidence,
        )

    ambiguous_ingredients = [
        slot.raw for slot in frame.ingredients if slot.match_mode == "ambiguous" and slot.raw.strip()
    ]
    if ambiguous_ingredients and frame.dish is None and frame.intent in {
        "ingredient_combo_query",
        "scenario_recommendation_query",
        "reverse_entity_query",
    }:
        names = "、".join(ambiguous_ingredients)
        return QueryAction(
            action="content",
            content=f"你说的“{names}”范围比较大。请说明是猪肉、牛肉、鸡肉，还是其他肉类？",
            pending_clarification={
                "type": "ambiguous_ingredient",
                "payload": {
                    "original_query": frame.source_text,
                    "ambiguous": ambiguous_ingredients,
                    "known_ingredients": [
                        slot.canonical or slot.raw
                        for slot in frame.ingredients
                        if slot.match_mode != "ambiguous" and slot.raw.strip()
                    ],
                    "candidate_terms": ["猪肉", "牛肉", "鸡肉", "羊肉", "鸭肉"],
                },
                "question": f"你说的“{names}”范围比较大。请说明具体是哪类食材。",
                "reason": "食材泛称存在多个图谱实体，不能擅自选择具体实体",
            },
            reason="食材泛称存在多个图谱实体，不能擅自选择具体实体",
            query_frame=frame,
            confidence=frame.confidence,
        )

    plan = _build_plan(frame)
    return QueryAction(
        action="tool",
        tool_name="recipe_query_tool",
        # 保留 query 字段作为 trace/旧调用方的显示字段；真正执行参数是 plan。
        query=frame.resolved_query or frame.source_text,
        plan=plan,
        answer_user_text=frame.resolved_query or frame.source_text,
        reason=frame.reason or frame.intent,
        query_frame=frame,
        confidence=frame.confidence,
    )


def _classify_resolve_action(text: str, recipe_context: dict) -> QueryAction:
    frame = classify_v2(text, recipe_context=recipe_context)
    extracted = extract_ingredient_slots_from_source(text, entity_names=kg_entity_names())
    if extracted:
        existing = {slot.raw for slot in frame.ingredients}
        frame = replace(frame, ingredients=[*frame.ingredients, *(slot for slot in extracted if slot.raw not in existing)])
    frame = enforce_query_frame_contract(frame)
    frame = resolve_entities(frame, entity_names=kg_entity_names())
    return _action_from_frame(frame)


def route_query(user_text: str, history: list[dict] | None = None) -> QueryAction:
    """Return the first-hop action for the current user query."""
    history = history or []
    text = str(user_text or "").strip()
    if not text:
        return QueryAction(action="direct_chat", content="", reason="空输入")

    recipe_context = _recipe_context_from_history(history)
    pending_state = any(
        isinstance(item, dict)
        and isinstance(item.get("rag_trace"), dict)
        and (
            isinstance(item["rag_trace"].get("pending_clarification"), dict)
            or isinstance(item["rag_trace"].get("choice_prompt"), dict)
            or isinstance(item["rag_trace"].get("pending_recipe_web_search"), dict)
        )
        for item in history
    )
    clarification = None
    if pending_state:
        try:
            dish_names = kg_dish_names()
        except Exception:
            dish_names = set()
        clarification = decide_clarification(text, dish_names=dish_names, history=history)

    if clarification is not None and clarification.action == "ask":
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
        if (clarification.tool_name or "recipe_query_tool") == "recipe_query_tool":
            return _classify_resolve_action(clarification.query or text, recipe_context)
        return QueryAction(
            action="tool",
            tool_name=clarification.tool_name or "web_search_tool",
            query=clarification.query or text,
            reason=clarification.reason,
            confidence=1.0,
        )

    # ── V2 流水线 ──
    return _classify_resolve_action(text, recipe_context)


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
                plan = args.get("plan") if isinstance(args.get("plan"), dict) else {}
                query = str(args.get("query") or "")
                output = str(call.get("output_preview") or "")
                dish = ""
                if tool_name == "recipe_query_tool":
                    dish = str(plan.get("dish") or "").strip()
                    result = call.get("result") if isinstance(call.get("result"), dict) else parse_tool_result(output)
                    if not dish and result is not None:
                        data = result.get("data") if isinstance(result.get("data"), dict) else {}
                        dish = str(data.get("dish") or data.get("dish_name") or "").strip()
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


def _friendly_greeting() -> str:
    return "你好呀，我可以帮你查本地菜谱、做法、用料、火力控制，也可以在本地没有收录时帮你联网找参考。"


def _friendly_non_recipe_reply() -> str:
    return "这个问题看起来不像菜谱查询。我主要擅长查菜谱、食材、做法和火力控制；你可以问我一道菜怎么做。"
