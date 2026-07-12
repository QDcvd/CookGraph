"""统一解释工具结果，控制最终回答是否允许生成。

工具结果是 agent 的证据边界：失败结果可以触发明确的下一步，但不能
直接交给普通模型，让模型用常识补写一个看似真实的菜名或做法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.tool_result import parse_tool_result


@dataclass(frozen=True)
class ToolEvidence:
    tool_name: str
    content: str
    success: bool | None
    query_type: str
    web_fallback_allowed: bool
    result: dict[str, Any] | None = None
    message: str = ""

    @property
    def terminal_failure(self) -> bool:
        return self.success is False and not self.web_fallback_allowed


def inspect_tool_result(item: dict[str, Any]) -> ToolEvidence:
    content = str(item.get("content") or "")
    result = item.get("result") if isinstance(item.get("result"), dict) else parse_tool_result(content)
    success = result.get("ok") if result is not None else None
    query_type = str(result.get("query_type") or "") if result is not None else ""
    allowed = bool(result.get("web_fallback_allowed")) if result is not None else False
    message = str(result.get("message") or "") if result is not None else content
    return ToolEvidence(
        tool_name=str(item.get("tool_name") or ""),
        content=content,
        success=success,
        query_type=query_type,
        web_fallback_allowed=allowed,
        result=result,
        message=message,
    )


def latest_terminal_recipe_failure(tool_context: list[dict[str, Any]]) -> tuple[ToolEvidence, dict[str, Any]] | None:
    for item in reversed(tool_context):
        if str(item.get("tool_name") or "") != "recipe_query_tool":
            continue
        evidence = inspect_tool_result(item)
        if evidence.terminal_failure:
            return evidence, item
    return None


def render_terminal_recipe_failure(user_text: str, tool_context: list[dict[str, Any]]) -> str:
    """Return a grounded failure answer, or an empty string when generation is allowed."""
    failure = latest_terminal_recipe_failure(tool_context)
    if failure is None:
        return ""

    evidence, item = failure
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    plan = args.get("plan") if isinstance(args.get("plan"), dict) else {}
    ingredients = [str(value).strip() for value in plan.get("ingredients", []) if str(value).strip()]
    if evidence.query_type in {"combo", "ingredient_combo_query", "reverse_entity_query"} or ingredients:
        target = "、".join(ingredients) or str(user_text or "").strip()
        return (
            f"本地菜谱图谱没有找到同时满足“{target}”的已收录菜式。\n\n"
            "当前没有可靠的本地图谱证据支持具体菜名，我不会把推测出来的菜名当作查询结果。"
        )

    detail = evidence.message.strip()
    detail = detail or "本地菜谱图谱没有返回可用结果。"
    return f"本地菜谱查询没有返回可靠结果：{detail}"
