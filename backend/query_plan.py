"""LLM-backed query planning for local recipe graph queries.

This module turns user wording into a structured plan. Natural-language intent
boundaries are decided by a small LLM router, then validated against graph
entities before any executor can use the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Literal
from urllib import request, error


PlanType = Literal[
    "unsupported",
    "entity_lookup",
    "compound_recommendation",
]

EntityType = Literal["ingredient", "taste", "cuisine", "technique"]


@dataclass(frozen=True)
class QueryConstraint:
    type: EntityType
    value: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueryPlan:
    plan_type: PlanType
    original_query: str
    supported: bool = False
    entity_type: EntityType | None = None
    entity_value: str | None = None
    relation_scope: Literal["all_related", "core_first"] = "all_related"
    constraints: tuple[QueryConstraint, ...] = field(default_factory=tuple)
    answer_style: str = "default"
    reason: str = ""


ENTITY_TYPE_LABELS: dict[EntityType, str] = {
    "ingredient": "Ingredient",
    "taste": "Taste",
    "cuisine": "Cuisine",
    "technique": "Technique",
}


TASTE_ALIASES: dict[str, list[str]] = {
    "香辣味": ["香辣味", "香辣", "辣香辣", "吃辣", "辣味"],
    "麻辣味": ["麻辣味", "麻辣"],
    "酸甜味": ["酸甜味", "酸甜"],
    "酸辣味": ["酸辣味", "酸辣"],
}

INGREDIENT_ALIASES: dict[str, list[str]] = {
    "鸡肉": ["鸡肉", "鸡腿肉", "三黄鸡", "鸡胸肉", "鸡翅", "鸡翅中"],
    "牛肉": ["牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"],
    "猪肉": ["猪肉", "猪里脊", "猪里脊肉", "猪前腿肉", "猪大肠", "猪排骨", "排骨"],
    "鱼": ["鱼", "鲈鱼"],
    "土豆": ["土豆", "马铃薯"],
    "虾": ["虾", "鲜虾"],
}


def normalize_query_text(query: str) -> str:
    return re.sub(r"\s+", "", str(query or "").lower())


def build_query_plan(
    query: str,
    *,
    node_names_by_type: dict[str, set[str]],
    dish_names: set[str] | None = None,
) -> QueryPlan:
    """Build a validated query plan using the LLM query router."""
    raw = str(query or "").strip()
    text = normalize_query_text(raw)
    if not text:
        return _unsupported(raw, "empty")

    if dish_names and any(name and name in raw for name in dish_names):
        return _unsupported(raw, "direct dish query should stay in legacy forward flow")

    routed = _build_llm_query_plan(raw, node_names_by_type)
    if routed.supported:
        return routed

    return routed


def _build_llm_query_plan(raw: str, node_names_by_type: dict[str, set[str]]) -> QueryPlan:
    payload = _call_llm_router(raw, node_names_by_type)
    if not isinstance(payload, dict):
        return _unsupported(raw, "llm router unavailable or invalid")

    intent = str(payload.get("intent") or "").strip()
    confidence = _safe_float(payload.get("confidence"), 0.0)
    if confidence < 0.55:
        return _unsupported(raw, f"llm router confidence too low: {confidence:.2f}")

    if intent == "entity_lookup":
        entity_type = _as_entity_type(payload.get("entity_type"))
        if entity_type is None:
            return _unsupported(raw, "llm router entity_lookup missing valid entity_type")
        entity_value = _resolve_entity_strict(entity_type, str(payload.get("entity_value") or ""), node_names_by_type)
        if not entity_value:
            return _unsupported(raw, "llm router entity value not in graph")
        relation_scope = "core_first" if payload.get("relation_scope") == "core_first" else "all_related"
        return QueryPlan(
            plan_type="entity_lookup",
            original_query=raw,
            supported=True,
            entity_type=entity_type,
            entity_value=entity_value,
            relation_scope=relation_scope,
            answer_style="core_first_entity_lookup" if relation_scope == "core_first" else "grouped_entity_lookup",
            reason=f"llm router: {payload.get('reason') or intent}",
        )

    if intent == "compound_recommendation":
        constraints: list[QueryConstraint] = []
        for item in payload.get("constraints") or []:
            if not isinstance(item, dict):
                continue
            entity_type = _as_entity_type(item.get("type"))
            if entity_type is None:
                continue
            value = _resolve_entity_strict(entity_type, str(item.get("value") or ""), node_names_by_type)
            if value:
                constraints.append(QueryConstraint(entity_type, value, tuple(_entity_aliases(entity_type, value))))
        constraint_types = {item.type for item in constraints}
        if "ingredient" not in constraint_types or len(constraints) < 2:
            return _unsupported(raw, "llm router compound recommendation missing validated constraints")
        return QueryPlan(
            plan_type="compound_recommendation",
            original_query=raw,
            supported=True,
            constraints=tuple(constraints),
            answer_style="direct_recommendation",
            reason=f"llm router: {payload.get('reason') or intent}",
        )

    return _unsupported(raw, f"llm router intent not executable by query_plan: {intent or 'empty'}")


def _call_llm_router(raw: str, node_names_by_type: dict[str, set[str]]) -> dict | None:
    base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:51234/v1").rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "not-needed")
    model = os.getenv("LLM_MODEL", "qwen3-4b")
    timeout = _safe_float(os.getenv("QUERY_PLAN_LLM_TIMEOUT"), 12.0)
    url = f"{base_url}/chat/completions"
    prompt = _build_llm_router_prompt(raw, node_names_by_type)
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是菜谱知识图谱查询路由器。只输出 JSON，不要解释。"
                        "不能编造图谱实体，只能从用户给出的实体列表中选择。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 512,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (OSError, error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return _parse_router_json(str(content))


def _build_llm_router_prompt(raw: str, node_names_by_type: dict[str, set[str]]) -> str:
    entity_lines = []
    for entity_type in ("ingredient", "taste", "cuisine", "technique"):
        names = "、".join(sorted(node_names_by_type.get(entity_type, set()))[:120])
        entity_lines.append(f"- {entity_type}: {names}")
    return (
        f"用户问题：{raw}\n\n"
        "可用图谱实体：\n"
        f"{chr(10).join(entity_lines)}\n\n"
        "请选择一种 intent：\n"
        "- entity_lookup：用户在问某个食材/口味/菜系/技法能对应哪些本地菜，例如“猪肉做法”“牛肉有多少种做法”“土豆可以做什么菜”。\n"
        "- compound_recommendation：用户同时给出食材和口味/菜系/技法约束，要推荐本地菜，例如“香辣口味的牛肉有什么推荐”。\n"
        "- forward_recipe_query：用户明确问一道菜的做法，例如“洋葱炒牛肉的做法”“香菇炖鸡的具体做法”。query_plan 不执行此 intent。\n"
        "- needs_clarification：无法确定。\n\n"
        "硬性例子：\n"
        "- “猪肉做法” => entity_lookup, ingredient=猪肉, relation_scope=core_first。\n"
        "- “鸡蛋做法” => entity_lookup, ingredient=鸡蛋, relation_scope=core_first。\n"
        "- “洋葱炒牛肉的做法” => forward_recipe_query，不要拆成牛肉 entity_lookup。\n"
        "- “香辣口味的牛肉有什么推荐” => compound_recommendation，constraints 必须同时包含 ingredient=牛肉 和 taste=香辣味。\n\n"
        "输出 JSON schema：\n"
        "{\n"
        '  "intent": "entity_lookup|compound_recommendation|forward_recipe_query|needs_clarification",\n'
        '  "entity_type": "ingredient|taste|cuisine|technique|null",\n'
        '  "entity_value": "实体名或null",\n'
        '  "relation_scope": "core_first|all_related",\n'
        '  "constraints": [{"type":"ingredient|taste|cuisine|technique","value":"实体名"}],\n'
        '  "confidence": 0.0,\n'
        '  "reason": "简短原因"\n'
        "}"
    )


def _parse_router_json(content: str) -> dict | None:
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
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_entity_type(value: object) -> EntityType | None:
    text = str(value or "")
    return text if text in ENTITY_TYPE_LABELS else None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Legacy regex planner.
#
# 旧实现是自然语言正则路由，已经不再由 build_query_plan() 主路径调用。
# 保留这些函数只用于回溯对比；不要继续向这里添加新问法补丁。
# ---------------------------------------------------------------------------


def _build_entity_lookup_plan(
    raw: str,
    text: str,
    node_names_by_type: dict[str, set[str]],
) -> QueryPlan:
    core_match = re.fullmatch(
        r"(?P<value>[\u4e00-\u9fff]{1,12}?)(?:可以|能|可|能够)?(?:用来)?做(?:什么|哪些|啥).{0,4}菜?",
        text,
    )
    if not core_match:
        core_match = re.fullmatch(r"(?P<value>[\u4e00-\u9fff]{1,12}?)有多少(?:种)?(?:做法|吃法|菜式)", text)
    method_short_match = None
    if not core_match:
        method_short_match = re.fullmatch(r"(?P<value>[\u4e00-\u9fff]{1,12}?)(?:的)?(?:做法|吃法)$", text)
        core_match = method_short_match

    if core_match:
        value = core_match.group("value")
        resolved = (
            _resolve_entity_strict("ingredient", value, node_names_by_type)
            if method_short_match
            else _resolve_entity("ingredient", value, node_names_by_type)
        )
        if resolved:
            return QueryPlan(
                plan_type="entity_lookup",
                original_query=raw,
                supported=True,
                entity_type="ingredient",
                entity_value=resolved,
                relation_scope="core_first",
                answer_style="core_first_entity_lookup",
                reason="core ingredient dish query",
            )

    if re.fullmatch(r"[\u4e00-\u9fff]{1,8}", text):
        for entity_type in ("ingredient", "technique", "taste", "cuisine"):
            resolved = _resolve_entity(entity_type, text, node_names_by_type)
            if resolved:
                return QueryPlan(
                    plan_type="entity_lookup",
                    original_query=raw,
                    supported=True,
                    entity_type=entity_type,
                    entity_value=resolved,
                    relation_scope="all_related",
                    answer_style="grouped_entity_lookup",
                    reason="bare graph entity",
                )

    return _unsupported(raw, "not an entity lookup")


def _build_compound_recommendation_plan(
    raw: str,
    text: str,
    node_names_by_type: dict[str, set[str]],
) -> QueryPlan:
    recommendation_markers = ("推荐", "有什么", "有哪些", "哪些", "什么菜", "哪道菜")
    if any(marker in text for marker in ("怎么做", "做法")) and not any(
        marker in text for marker in recommendation_markers
    ):
        return _unsupported(raw, "single recipe wording should not become compound recommendation")

    ingredient = _find_entity_in_text("ingredient", text, node_names_by_type)
    if not ingredient:
        return _unsupported(raw, "no ingredient constraint")

    for entity_type in ("taste", "cuisine", "technique"):
        value = _find_entity_in_text(entity_type, text, node_names_by_type)
        if value:
            if not any(marker in text for marker in ["推荐", "有什么", "有哪些", "什么", value, "口味", "菜系", "做法", "技法"]):
                return _unsupported(raw, "not a recommendation wording")
            constraints: list[QueryConstraint] = [
                QueryConstraint("ingredient", ingredient, tuple(_entity_aliases("ingredient", ingredient))),
                QueryConstraint(entity_type, value, tuple(_entity_aliases(entity_type, value))),
            ]
            return QueryPlan(
                plan_type="compound_recommendation",
                original_query=raw,
                supported=True,
                constraints=tuple(constraints),
                answer_style="direct_recommendation",
                reason=f"compound recommendation: ingredient + {entity_type}",
            )

    return _unsupported(raw, "no supported second constraint")


def _find_entity_in_text(entity_type: EntityType, text: str, node_names_by_type: dict[str, set[str]]) -> str | None:
    names = sorted(node_names_by_type.get(entity_type, set()), key=len, reverse=True)
    if entity_type == "ingredient":
        for canonical, aliases in INGREDIENT_ALIASES.items():
            if any(normalize_query_text(alias) in text for alias in aliases):
                return canonical
    if entity_type == "taste":
        for canonical, aliases in TASTE_ALIASES.items():
            if any(alias in text for alias in aliases):
                resolved = _resolve_entity("taste", canonical, node_names_by_type)
                if resolved:
                    return resolved
    for name in names:
        normalized = normalize_query_text(name)
        if normalized and normalized in text:
            return name
    return None


def _resolve_entity(entity_type: EntityType, value: str, node_names_by_type: dict[str, set[str]]) -> str | None:
    normalized_value = normalize_query_text(value)
    if not normalized_value:
        return None
    if entity_type == "taste":
        for canonical, aliases in TASTE_ALIASES.items():
            if normalized_value in {normalize_query_text(item) for item in aliases}:
                normalized_value = normalize_query_text(canonical)
                break
    if entity_type == "ingredient":
        for canonical, aliases in INGREDIENT_ALIASES.items():
            if normalized_value in {normalize_query_text(item) for item in aliases}:
                return canonical
    for name in sorted(node_names_by_type.get(entity_type, set()), key=len, reverse=True):
        normalized = normalize_query_text(name)
        if normalized == normalized_value:
            return name
    for name in sorted(node_names_by_type.get(entity_type, set()), key=len, reverse=True):
        normalized = normalize_query_text(name)
        if normalized and (normalized.endswith(normalized_value) or normalized_value.endswith(normalized)):
            return name
    return None


def _resolve_entity_strict(entity_type: EntityType, value: str, node_names_by_type: dict[str, set[str]]) -> str | None:
    normalized_value = normalize_query_text(value)
    if not normalized_value:
        return None
    if entity_type == "ingredient":
        for canonical, aliases in INGREDIENT_ALIASES.items():
            if normalized_value in {normalize_query_text(item) for item in aliases}:
                return canonical
    if entity_type == "taste":
        for canonical, aliases in TASTE_ALIASES.items():
            if normalized_value in {normalize_query_text(item) for item in aliases}:
                normalized_value = normalize_query_text(canonical)
                break
    for name in sorted(node_names_by_type.get(entity_type, set()), key=len, reverse=True):
        if normalize_query_text(name) == normalized_value:
            return name
    return None


def _entity_aliases(entity_type: EntityType, value: str) -> list[str]:
    if entity_type == "ingredient":
        return INGREDIENT_ALIASES.get(value, [value])
    if entity_type == "taste":
        return TASTE_ALIASES.get(value, [value])
    return [value]


def _unsupported(query: str, reason: str) -> QueryPlan:
    return QueryPlan(plan_type="unsupported", original_query=query, supported=False, reason=reason)
