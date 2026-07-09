"""Rule-based query planning for local recipe graph queries.

This module is intentionally small and deterministic. It turns user wording into
a structured plan; it does not execute graph queries and does not call an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal


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
    """Build a deterministic query plan for the first-stage supported cases."""
    raw = str(query or "").strip()
    text = normalize_query_text(raw)
    if not text:
        return _unsupported(raw, "empty")

    if dish_names and any(name and name in raw for name in dish_names):
        return _unsupported(raw, "direct dish query should stay in legacy forward flow")

    compound = _build_compound_recommendation_plan(raw, text, node_names_by_type)
    if compound.supported:
        return compound

    entity_lookup = _build_entity_lookup_plan(raw, text, node_names_by_type)
    if entity_lookup.supported:
        return entity_lookup

    return _unsupported(raw, "no supported plan pattern")


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

    if core_match:
        value = core_match.group("value")
        resolved = _resolve_entity("ingredient", value, node_names_by_type)
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


def _entity_aliases(entity_type: EntityType, value: str) -> list[str]:
    if entity_type == "ingredient":
        return INGREDIENT_ALIASES.get(value, [value])
    if entity_type == "taste":
        return TASTE_ALIASES.get(value, [value])
    return [value]


def _unsupported(query: str, reason: str) -> QueryPlan:
    return QueryPlan(plan_type="unsupported", original_query=query, supported=False, reason=reason)
