"""Gate for deciding whether a user turn may inherit the last recipe topic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ContextFollowupDecision:
    action: Literal["inherit", "new_task"]
    rewritten_query: str | None = None
    attribute: str | None = None
    reason: str = ""


REFERENCE_MARKERS = (
    "它",
    "他",
    "这道菜",
    "这个菜",
    "刚才那道菜",
    "刚才那个",
    "上面那道",
    "上一道",
)

NEGATIVE_OR_SWITCH_MARKERS = (
    "不要",
    "不想要",
    "不吃",
    "不做",
    "换",
    "别的",
    "其他",
    "不是",
    "不对",
)

REVERSE_MARKERS = (
    "有多少种做法",
    "有多少做法",
    "多少种做法",
    "多少种吃法",
    "可以做什么菜",
    "能做什么菜",
    "有哪些菜",
    "有什么菜",
    "推荐",
)

ATTRIBUTE_MAP = (
    (("火力", "火候"), "fire", "火力调节过程"),
    (("注意事项", "注意点", "提示", "要点", "为什么"), "tips", "烹饪提示"),
    (("调料", "调味", "配料", "用料", "材料"), "seasoning", "调味品"),
    (("怎么做", "做法", "步骤", "具体菜谱", "具体做法", "具体怎么做"), "method", "怎么做"),
)


def decide_context_followup(
    user_text: str,
    *,
    last_dish: str | None,
    last_reverse_candidates: list[str] | None = None,
) -> ContextFollowupDecision:
    text = _normalize(user_text)
    dish = str(last_dish or "").strip()
    reverse_candidates = [str(item).strip() for item in (last_reverse_candidates or []) if str(item).strip()]
    if not text:
        return ContextFollowupDecision(action="new_task", reason="缺少用户输入或最近菜品")

    if _contains_negative_or_switch(text):
        return ContextFollowupDecision(action="new_task", reason="用户表达否定、排除或换菜意图")

    if _looks_like_reverse_or_recommendation(text):
        return ContextFollowupDecision(action="new_task", reason="用户提出反向查询或推荐新任务")

    if not _is_bare_attribute_fragment(text) and _mentions_explicit_new_topic(text, dish):
        return ContextFollowupDecision(action="new_task", reason="用户输入包含明确新菜名或新食材")

    if not dish and _is_bare_attribute_fragment(text) and len(reverse_candidates) == 1:
        dish = reverse_candidates[0]
    elif not dish and _is_bare_attribute_fragment(text) and len(reverse_candidates) > 1:
        return ContextFollowupDecision(action="new_task", reason="上一轮有多个候选菜，不能自动继承")

    if not dish:
        return ContextFollowupDecision(action="new_task", reason="缺少可继承的最近菜品")

    if not _has_reference_marker(text) and not _is_bare_attribute_fragment(text):
        return ContextFollowupDecision(action="new_task", reason="没有强指代词或短属性片段，不能继承最近菜品")

    for markers, attribute, suffix in ATTRIBUTE_MAP:
        if any(marker in text for marker in markers):
            query = f"{dish}{suffix}" if suffix.startswith("的") else f"{dish}的{suffix}"
            if suffix == "怎么做":
                query = f"{dish}怎么做"
            return ContextFollowupDecision(
                action="inherit",
                rewritten_query=query,
                attribute=attribute,
                reason="强指代属性追问，继承最近菜品",
            )

    return ContextFollowupDecision(
        action="inherit",
        rewritten_query=f"{dish}怎么做",
        attribute="method",
        reason="强指代追问但属性不明确，默认查询做法",
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _has_reference_marker(text: str) -> bool:
    return any(marker in text for marker in REFERENCE_MARKERS)


def _contains_negative_or_switch(text: str) -> bool:
    return any(marker in text for marker in NEGATIVE_OR_SWITCH_MARKERS)


def _looks_like_reverse_or_recommendation(text: str) -> bool:
    return any(marker in text for marker in REVERSE_MARKERS)


def _mentions_explicit_new_topic(text: str, last_dish: str) -> bool:
    if last_dish and last_dish in text:
        return False
    recipe_markers = ("怎么做", "要怎么做", "做法", "咋做", "如何做")
    if any(marker in text for marker in recipe_markers):
        without_prefix = re.sub(r"^(?:告诉我|请问|我想做|我想吃|想做|想吃|要做|要吃|我要做|我要吃|帮我查)", "", text)
        topic = re.split(r"(?:怎么做|做法|咋做|如何做)", without_prefix, maxsplit=1)[0]
        topic = topic.strip("，。？！?,!")
        topic = re.sub(r"(?:要|该|应该)$", "", topic)
        topic = topic.strip("，。？！?,!")
        return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,12}", topic))
    return False


def _is_bare_attribute_fragment(text: str) -> bool:
    if len(text) > 12:
        return False
    if text in {"怎么做", "咋做", "如何做", "做法", "步骤", "步骤呢", "具体做法", "具体菜谱", "具体怎么做"}:
        return True
    if any(marker in text for marker in ("具体菜谱", "具体做法", "具体怎么做")):
        if re.search(r"[\u4e00-\u9fff]{2,12}的(?:具体菜谱|具体做法|具体怎么做)", text):
            return False
        return True
    for markers, _attribute, _suffix in ATTRIBUTE_MAP:
        if any(marker in text for marker in markers):
            if any(marker in text for marker in ("怎么做", "做法", "步骤")):
                continue
            return True
    return False
