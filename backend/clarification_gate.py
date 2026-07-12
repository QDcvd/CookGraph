"""Clarification gate for recipe routing.

This module decides whether a user message is stable enough to execute a tool,
or whether the agent should ask a short confirmation question first.
"""

from __future__ import annotations

import re
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ClarificationDecision:
    action: Literal["ask", "execute", "none"]
    tool_name: str | None = None
    query: str | None = None
    question: str | None = None
    pending_type: str | None = None
    pending_payload: dict[str, Any] | None = None
    reason: str = ""


def build_choice_prompt(decision: ClarificationDecision) -> dict | None:
    """Build frontend choice metadata for clarification decisions."""
    if decision.action != "ask" or not decision.pending_type:
        return None
    pending_payload = decision.pending_payload or {}
    if decision.pending_type == "uncertain_dish_name":
        suggested = str(pending_payload.get("suggested_query") or "").strip()
        label = "是，按候选菜查"
        if "土豆炖鸡" in suggested:
            label = "是，按土豆炖鸡查"
        return _choice_prompt(
            prompt_type="uncertain_dish_name",
            question=decision.question or "需要我按候选菜名继续查询吗？",
            pending_type=decision.pending_type,
            pending_payload=pending_payload,
            options=[
                {"key": "A", "label": label, "send_text": "是"},
                {"key": "B", "label": "不是", "send_text": "不是"},
                {"key": "C", "label": "我自己输入", "custom": True},
            ],
        )
    if decision.pending_type == "forward_or_recommendation":
        return _choice_prompt(
            prompt_type="forward_or_recommendation",
            question=decision.question or "你想查具体做法，还是想让我推荐菜？",
            pending_type=decision.pending_type,
            pending_payload=pending_payload,
            options=[
                {"key": "A", "label": "查具体做法", "send_text": "具体做法"},
                {"key": "B", "label": "推荐菜", "send_text": "推荐菜"},
                {"key": "C", "label": "我自己输入", "custom": True},
            ],
        )
    if decision.pending_type == "missing_recipe_target":
        return _choice_prompt(
            prompt_type="missing_recipe_target",
            question=decision.question or "你想查询哪道菜？",
            pending_type=decision.pending_type,
            pending_payload=pending_payload,
            options=[
                {"key": "A", "label": "补充菜名", "custom": True},
                {"key": "B", "label": "取消", "send_text": "取消"},
                {"key": "C", "label": "我自己输入", "custom": True},
            ],
        )
    return None


def build_web_search_choice_prompt(original_query: str, *, question: str | None = None) -> dict:
    payload = {"original_query": str(original_query or "").strip()}
    return _choice_prompt(
        prompt_type="web_search_confirm",
        question=question or "需要我帮你到网上搜一下吗？",
        pending_type="recipe_web_search_offer",
        pending_payload=payload,
        options=[
            {"key": "A", "label": "是，帮我搜", "send_text": "是"},
            {"key": "B", "label": "先不用", "send_text": "不是"},
            {"key": "C", "label": "我自己输入", "custom": True},
        ],
    )


def _choice_prompt(
    *,
    prompt_type: str,
    question: str,
    pending_type: str,
    pending_payload: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict:
    identity = json.dumps(
        {
            "type": prompt_type,
            "pending_type": pending_type,
            "pending_payload": pending_payload,
            "question": question,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    prompt_id = "choice_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return {
        "id": prompt_id,
        "type": prompt_type,
        "question": question,
        "options": options,
        "pending_type": pending_type,
        "pending_payload": pending_payload,
    }


AFFIRMATIVE_WORDS = ("是", "对", "确认", "没错", "可以", "好的", "嗯", "行")
NEGATIVE_WORDS = ("不是", "不对", "否", "别", "不要")
WEB_SEARCH_MARKERS = ("联网", "网上", "网络", "搜索", "搜一下", "查一下")
RECIPE_ACTION_MARKERS = ("怎么做", "做法", "配料", "调味料", "火力", "火候", "步骤", "准备哪些", "需要准备")
RECOMMEND_MARKERS = ("推荐", "有哪些", "有什么", "哪些", "多少种", "可以做什么", "能做什么")
TASTE_WORDS = ("香辣", "麻辣", "酸辣", "酸甜", "清淡", "咸鲜")
RECIPE_ENTITY_WORDS = ("鸡肉", "牛肉", "猪肉", "鱼", "虾", "土豆", "鸡", "牛蛙", "黄瓜")
CONTEXTLESS_ATTR = ("火力", "火候", "注意事项", "注意点", "调料", "配料", "用料", "材料")


def decide_clarification(
    user_text: str,
    *,
    dish_names: set[str] | None = None,
    history: list[dict] | None = None,
) -> ClarificationDecision:
    text = _normalize(user_text)
    if not text:
        return ClarificationDecision(action="none", reason="空输入")

    pending = _last_pending_clarification(history or [])
    resolved = resolve_pending_clarification(user_text, pending)
    if resolved is not None:
        return resolved

    dish_names = dish_names or set()
    if _explicit_web_search(text):
        return ClarificationDecision(
            action="execute",
            tool_name="web_search_tool",
            query=user_text,
            reason="用户明确要求联网搜索",
        )

    matched_dish = _match_dish(text, dish_names)
    if matched_dish:
        return ClarificationDecision(
            action="execute",
            tool_name="recipe_query_tool",
            query=user_text,
            reason=f"直接命中本地图谱菜名: {matched_dish}",
        )

    contextless = _contextless_attribute_question(text)
    if contextless:
        return ClarificationDecision(
            action="ask",
            question=f"可以的，你先告诉我是哪道菜，我再帮你查它的{contextless}。",
            pending_type="missing_recipe_target",
            pending_payload={"attribute": contextless, "original_query": user_text},
            reason="菜谱属性问题缺少明确菜名",
        )

    typo = _suspicious_typo_candidate(text)
    if typo:
        original, candidate = typo
        return ClarificationDecision(
            action="ask",
            question=f"我没能稳定识别“{original}”。你是不是想问“{candidate}”？确认后我再帮你查。",
            pending_type="uncertain_dish_name",
            pending_payload={"original_query": user_text, "suggested_query": _replace_once(user_text, original, candidate)},
            reason="疑似错别字菜名，需要用户确认",
        )

    compound = _compound_preference_query(text)
    if compound:
        query = f"{compound['taste']}口味的{compound['ingredient']}有什么推荐"
        return ClarificationDecision(
            action="ask",
            question=(
                f"你是想查一道叫“{compound['taste']}{compound['ingredient']}”的具体做法，"
                f"还是想让我推荐{compound['taste']}口味、含{compound['ingredient']}的菜？"
            ),
            pending_type="forward_or_recommendation",
            pending_payload={
                "original_query": user_text,
                "recommended_query": query,
                "dish_query": user_text,
            },
            reason="口味+食材+做法问法存在正向菜名和推荐意图冲突",
        )

    if _looks_like_recipe_request(text) or _looks_like_reverse_request(text):
        return ClarificationDecision(
            action="execute",
            tool_name="recipe_query_tool",
            query=user_text,
            reason="菜谱相关问题先交本地图谱工具判断",
        )

    return ClarificationDecision(action="none", reason="非明确菜谱路由")


def resolve_pending_clarification(user_text: str, pending: dict | None) -> ClarificationDecision | None:
    if not isinstance(pending, dict):
        return None
    text = _normalize(user_text)
    pending_type = str(pending.get("type") or "")
    payload = pending.get("payload") if isinstance(pending.get("payload"), dict) else {}

    if pending_type == "uncertain_dish_name":
        if _is_negative(text):
            return ClarificationDecision(action="none", reason="用户否定候选菜名，交给后续路由")
        if _is_affirmative(text):
            return ClarificationDecision(
                action="execute",
                tool_name="recipe_query_tool",
                query=str(payload.get("suggested_query") or payload.get("original_query") or user_text),
                reason="用户确认疑似菜名修正",
            )

    if pending_type == "forward_or_recommendation":
        if any(marker in text for marker in ("推荐", "有哪些", "有什么", "哪些")):
            return ClarificationDecision(
                action="execute",
                tool_name="recipe_query_tool",
                query=str(payload.get("recommended_query") or payload.get("original_query") or user_text),
                reason="用户确认按推荐/反向查询处理",
            )
        if any(marker in text for marker in ("具体做法", "这道菜", "做法", "怎么做")) or _is_affirmative(text):
            return ClarificationDecision(
                action="execute",
                tool_name="recipe_query_tool",
                query=str(payload.get("dish_query") or payload.get("original_query") or user_text),
                reason="用户确认按单菜谱处理",
            )

    if pending_type == "reverse_candidate_choice":
        candidates = [str(item).strip() for item in payload.get("candidates", []) if str(item).strip()]
        for candidate in candidates:
            if candidate and (candidate in user_text or _normalize(candidate) == text):
                return ClarificationDecision(
                    action="execute",
                    tool_name="recipe_query_tool",
                    query=f"{candidate}怎么做",
                    reason="用户从反向查询候选中选择了具体菜",
                )

    if pending_type == "ambiguous_ingredient":
        original = str(payload.get("original_query") or "").strip()
        candidate_terms = [
            str(item).strip()
            for item in payload.get("candidate_terms", [])
            if str(item).strip()
        ]
        if original and text and not _is_negative(text) and any(term in text for term in candidate_terms):
            return ClarificationDecision(
                action="execute",
                tool_name="recipe_query_tool",
                query=f"{original}；补充说明：{user_text}",
                reason="用户补充了泛称食材的具体类别",
            )

    return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _match_dish(text: str, dish_names: set[str]) -> str | None:
    for name in sorted(dish_names, key=len, reverse=True):
        if name and name in text:
            return name
    return None


def _explicit_web_search(text: str) -> bool:
    return any(marker in text for marker in WEB_SEARCH_MARKERS)


def _looks_like_recipe_request(text: str) -> bool:
    return any(marker in text for marker in RECIPE_ACTION_MARKERS) or text.endswith(("菜谱", "做法"))


def _looks_like_reverse_request(text: str) -> bool:
    return any(marker in text for marker in RECOMMEND_MARKERS)


def _contextless_attribute_question(text: str) -> str | None:
    if len(text) > 12:
        return None
    for marker in CONTEXTLESS_ATTR:
        if marker in text:
            if marker in {"火力", "火候"}:
                return "火力控制"
            if marker in {"调料", "配料", "用料", "材料"}:
                return "用料或调料"
            return marker
    return None


def _suspicious_typo_candidate(text: str) -> tuple[str, str] | None:
    if "十豆" in text:
        return "十豆炖鸡", "土豆炖鸡"
    if "白灼蔬菜" in text or "白灼素材" in text or "白灼素菜" in text:
        return text, "白灼菜心"
    return None


def _compound_preference_query(text: str) -> dict[str, str] | None:
    if not any(marker in text for marker in ("怎么做", "做法")):
        return None
    if any(marker in text for marker in RECOMMEND_MARKERS):
        return None
    taste = next((word for word in TASTE_WORDS if word in text), "")
    ingredient = next((word for word in RECIPE_ENTITY_WORDS if word in text), "")
    if taste and ingredient:
        topic = re.split(r"(?:怎么做|做法)", text, maxsplit=1)[0]
        topic = topic.rstrip("的")
        if topic != f"{taste}{ingredient}":
            return None
    if taste and ingredient:
        return {"taste": taste, "ingredient": ingredient}
    return None


def _is_affirmative(text: str) -> bool:
    return any(word == text or text.startswith(word) for word in AFFIRMATIVE_WORDS) and not _is_negative(text)


def _is_negative(text: str) -> bool:
    return any(word in text for word in NEGATIVE_WORDS)


def _replace_once(text: str, old: str, new: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if "十豆" in text and old == "十豆炖鸡":
        return text.replace("十豆", "土豆", 1)
    return new


def _last_pending_clarification(history: list[dict]) -> dict | None:
    items = list(history or [])
    if not items:
        return None
    last = items[-1]
    if not isinstance(last, dict):
        return None
    role = str(last.get("role") or last.get("type") or "").lower()
    if role not in {"ai", "assistant"}:
        return None
    trace = last.get("rag_trace") if isinstance(last.get("rag_trace"), dict) else None
    if isinstance(trace, dict) and isinstance(trace.get("pending_clarification"), dict):
        return trace["pending_clarification"]
    return None
