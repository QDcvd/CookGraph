"""唯一的菜谱查询意图识别入口。

模型输出 QueryFrame 所需的 JSON；本模块只负责请求、解析和契约校验。
模型不可用或输出不合规时返回 ambiguous_query，不使用语义关键词兜底。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.llm_endpoint import ensure_llm_endpoint, openai_base_available

ROOT = Path(__file__).resolve().parent.parent
_QUERY_ROUTER_TIMEOUT = float(os.getenv("QUERY_ROUTER_TIMEOUT", "12"))


@dataclass
class EntitySlot:
    raw: str
    canonical: str | None = None
    entity_type: str | None = None
    match_mode: str = "unresolved"
    confidence: float = 0.0


@dataclass
class QueryFrame:
    intent: str = ""
    source_text: str = ""
    mode: str | None = None
    dish_text: str | None = None
    dish: EntitySlot | None = None
    dish_candidates: list[EntitySlot] = field(default_factory=list)
    ingredients: list[EntitySlot] = field(default_factory=list)
    techniques: list[EntitySlot] = field(default_factory=list)
    tastes: list[EntitySlot] = field(default_factory=list)
    cuisines: list[EntitySlot] = field(default_factory=list)
    scenario_tags: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    attribute: str | None = None
    resolved_query: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = 0.0
    reason: str = ""


_VALID_INTENTS = {
    "graph_meta_query",
    "dish_existence_query",
    "dish_detail_query",
    "ingredient_combo_query",
    "scenario_recommendation_query",
    "reverse_entity_query",
    "recipe_followup_query",
    "missing_ingredients_query",
    "non_recipe_query",
    "ambiguous_query",
    "greeting",
}

_QUERY_FRAME_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "source_text", "raw_slots", "followup", "confidence", "reason"],
    "properties": {
        "intent": {"type": "string"},
        "source_text": {"type": "string"},
        "raw_slots": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dish_text", "ingredients", "techniques", "tastes", "cuisines", "scenario_tags", "exclusions", "attribute"],
            "properties": {
                "dish_text": {"type": ["string", "null"]},
                "ingredients": {"type": "array", "items": {"type": "string"}},
                "techniques": {"type": "array", "items": {"type": "string"}},
                "tastes": {"type": "array", "items": {"type": "string"}},
                "cuisines": {"type": "array", "items": {"type": "string"}},
                "scenario_tags": {"type": "array", "items": {"type": "string"}},
                "exclusions": {"type": "array", "items": {"type": "string"}},
                "attribute": {"type": ["string", "null"]},
            },
        },
        "followup": {
            "type": "object",
            "additionalProperties": False,
            "required": ["is_followup", "requires_context"],
            "properties": {
                "is_followup": {"type": "boolean"},
                "requires_context": {"type": "boolean"},
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_enabled(raw: str | None, default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _with_no_think(text: str, enabled: bool) -> str:
    if not enabled or text.lstrip().startswith("/no_think"):
        return text
    return f"/no_think\n{text}"


def _parse_router_json(content: str) -> dict | None:
    """仅解析 JSON 外壳，不使用正则判断用户意图。"""
    text = str(content or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    else:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _ensure_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _validate_query_frame(raw: dict, followup_requires_context: bool) -> QueryFrame:
    intent = str(raw.get("intent") or "ambiguous_query").strip()
    aliases = {
        "dish_existence": "dish_existence_query",
        "dish_exists": "dish_existence_query",
        "dish_detail": "dish_detail_query",
        "ingredient_combo": "ingredient_combo_query",
        "scenario_recommendation": "scenario_recommendation_query",
        "reverse_entity": "reverse_entity_query",
        "recipe_followup": "recipe_followup_query",
        "graph_meta": "graph_meta_query",
    }
    intent = aliases.get(intent, intent)
    if intent not in _VALID_INTENTS:
        intent = "ambiguous_query"

    raw_slots = raw.get("raw_slots") if isinstance(raw.get("raw_slots"), dict) else {}
    followup = raw.get("followup") if isinstance(raw.get("followup"), dict) else {}
    is_followup = bool(followup.get("is_followup"))
    requires_context = bool(followup.get("requires_context"))
    needs_clarification = is_followup and requires_context and not followup_requires_context
    source = str(raw.get("source_text") or "")[:500]
    source = source or ""

    def slots(key: str) -> list[EntitySlot]:
        return [EntitySlot(raw=str(item).strip()) for item in _ensure_list(raw_slots.get(key)) if str(item).strip()]

    frame = QueryFrame(
        intent=intent,
        source_text=source,
        dish_text=str(raw_slots.get("dish_text") or "").strip() or None,
        ingredients=slots("ingredients"),
        techniques=slots("techniques"),
        tastes=slots("tastes"),
        cuisines=slots("cuisines"),
        scenario_tags=[str(item).strip() for item in _ensure_list(raw_slots.get("scenario_tags")) if str(item).strip()],
        exclusions=[str(item).strip() for item in _ensure_list(raw_slots.get("exclusions")) if str(item).strip()],
        attribute=str(raw_slots.get("attribute") or "").strip() or None,
        resolved_query=str(raw.get("resolved_query") or "").strip() or None,
        needs_clarification=needs_clarification,
        clarification_question="你是在追问上一道菜吗？请告诉我菜名。" if needs_clarification else None,
        confidence=max(0.0, min(1.0, _safe_float(raw.get("confidence")))),
        reason=str(raw.get("reason") or "").strip(),
    )
    return enforce_query_frame_contract(frame)


def enforce_query_frame_contract(frame: QueryFrame) -> QueryFrame:
    explicit_slots = (frame.ingredients, frame.techniques, frame.tastes, frame.cuisines)
    has_explicit_target = any(slot.raw.strip() for slots in explicit_slots for slot in slots)
    resolved_dish = str(frame.dish.canonical or frame.dish.raw or "").strip() if frame.dish else ""
    if resolved_dish and not frame.dish_text:
        frame = replace(frame, dish_text=resolved_dish)

    if frame.intent in {"ingredient_combo_query", "scenario_recommendation_query", "reverse_entity_query"}:
        if has_explicit_target or frame.scenario_tags:
            return replace(frame, needs_clarification=False, clarification_question=None)
        return replace(
            frame,
            intent="ambiguous_query",
            needs_clarification=True,
            clarification_question="请告诉我具体食材、口味、菜系或技法。",
        )

    if frame.intent in {"dish_detail_query", "dish_existence_query", "missing_ingredients_query"} and not frame.dish_text:
        if has_explicit_target:
            return replace(frame, intent="ingredient_combo_query", needs_clarification=False, clarification_question=None)
        return replace(
            frame,
            needs_clarification=True,
            clarification_question="请告诉我具体菜名或你现有的食材，我才能帮你查询。",
        )

    return frame


def _build_v2_classifier_prompt(raw: str, recipe_context: str) -> str:
    context = f"\n当前会话上下文：{recipe_context}\n" if recipe_context else ""
    return f"""用户问题：{raw}{context}

你是菜谱知识图谱的意图识别器。请只输出符合 JSON Schema 的 JSON，不要解释，不要 Markdown。

intent 只能是：graph_meta_query、dish_existence_query、dish_detail_query、ingredient_combo_query、scenario_recommendation_query、reverse_entity_query、recipe_followup_query、missing_ingredients_query、non_recipe_query、ambiguous_query、greeting。
attribute 只能描述用户要查的字段，例如 full_recipe、method、prep、cooking_process、fire、tips、ingredients、seasonings、techniques、existence、count。

规则：
1. 有明确菜名且询问做法、属性或是否收录，使用 dish_detail_query 或 dish_existence_query。
2. 询问已有食材能做什么，使用 ingredient_combo_query。
3. 按天气、口味、菜系、技法或场景推荐，使用 scenario_recommendation_query。
4. 询问哪些菜使用某食材、口味、菜系或技法，使用 reverse_entity_query。
5. 只有省略菜名并且依赖当前上下文时，才使用 recipe_followup_query，并设置 requires_context=true。
6. 字段必须严格区分：准备食材、切配、腌制等“备菜”问题用 prep；下锅后的步骤、翻炒、蒸煮等“烹饪过程”用 cooking_process；用户泛问整道菜的做法用 full_recipe 或 method。即使问题使用“它的……”省略菜名，也必须根据本轮字段词选择对应 attribute，不要把 prep 改成 cooking_process。
7. 无法可靠判断时使用 ambiguous_query，不要猜测实体。

字段示例：
- “它的备菜过程呢” -> intent=dish_detail_query, attribute=prep
- “它下锅后怎么做” -> intent=dish_detail_query, attribute=cooking_process
- “这道菜怎么做” -> intent=dish_detail_query, attribute=full_recipe

raw_slots 必须包含 dish_text、ingredients、techniques、tastes、cuisines、scenario_tags、exclusions、attribute。
source_text 必须原样保留用户问题。"""


def _ambiguous_frame(raw: str, reason: str) -> QueryFrame:
    return QueryFrame(
        intent="ambiguous_query",
        source_text=raw,
        confidence=0.0,
        needs_clarification=True,
        clarification_question="我暂时无法可靠判断你的查询类型，请说明具体菜名、食材或查询目标。",
        reason=reason,
    )


def classify_v2(text: str, *, recipe_context: dict | None = None) -> QueryFrame:
    """调用本地/远端 OpenAI 兼容模型，返回经过校验的 QueryFrame。"""
    raw = str(text or "").strip()
    if not raw:
        return QueryFrame(intent="non_recipe_query", source_text="", confidence=1.0, reason="空输入")

    context_parts = []
    if recipe_context:
        if recipe_context.get("current_dish"):
            context_parts.append(f"当前菜品：{recipe_context['current_dish']}")
        if recipe_context.get("last_query"):
            context_parts.append(f"上一轮用户问题：{recipe_context['last_query']}")
    context_text = "；".join(context_parts)

    base_url = ensure_llm_endpoint(os.getenv("LLM_BASE_URL", "http://127.0.0.1:51234/v1")).rstrip("/")
    retry_url = ensure_llm_endpoint(base_url, force_retry=True).rstrip("/")
    endpoints = list(dict.fromkeys([base_url, retry_url]))
    api_key = os.getenv("LLM_API_KEY", "not-needed")
    model = os.getenv("INTENT_ROUTER_MODEL", os.getenv("LLM_MODEL", "qwen3-4b"))
    no_think = _env_enabled(os.getenv("INTENT_ROUTER_NO_THINK", os.getenv("LLM_NO_THINK")), True)
    prompt = _with_no_think(_build_v2_classifier_prompt(raw, context_text), no_think)
    base_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是菜谱知识图谱查询意图识别器。只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 512,
        "extra_body": {"no_think": True} if no_think else {},
    }
    schema_payload = {
        **base_payload,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "query_frame", "strict": True, "schema": _QUERY_FRAME_JSON_SCHEMA},
        },
    }
    bodies = [
        json.dumps(schema_payload, ensure_ascii=False).encode("utf-8"),
        json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
    ]

    last_error: Exception | None = None
    for endpoint in endpoints:
        if endpoint != base_url and not openai_base_available(endpoint, timeout=min(_QUERY_ROUTER_TIMEOUT, 3.0)):
            last_error = ConnectionError(f"LLM endpoint unavailable: {endpoint}")
            continue
        for body in bodies:
            request = urllib.request.Request(
                f"{endpoint}/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=_QUERY_ROUTER_TIMEOUT) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = _parse_router_json(str(content))
                if parsed is None:
                    continue
                frame = _validate_query_frame(parsed, followup_requires_context=bool(context_text))
                if frame.confidence >= 0.4:
                    return frame
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                continue

    if last_error is not None:
        print(f"[query_understanding] LLM endpoint unavailable after retry: {last_error}", file=__import__("sys").stderr)
    return _ambiguous_frame(raw, "意图模型不可用或输出未通过 JSON Schema 校验")
