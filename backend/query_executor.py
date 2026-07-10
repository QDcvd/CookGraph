"""Executors for structured recipe query plans."""

from __future__ import annotations

from typing import Any

from backend.query_plan import QueryConstraint, QueryPlan


RELATION_LABELS = {
    "USES_MAIN_INGREDIENT": ("ingredient", "主食材"),
    "USES_AUXILIARY": ("ingredient", "配料"),
    "USES_SEASONING": ("seasoning", "调味品"),
    "USES_TECHNIQUE": ("technique", "技法"),
    "HAS_TASTE": ("taste", "口味"),
    "BELONGS_TO_CUISINE": ("cuisine", "菜系"),
}

ENTITY_RELATIONS = {
    "ingredient": ["USES_MAIN_INGREDIENT", "USES_AUXILIARY"],
    "taste": ["HAS_TASTE"],
    "cuisine": ["BELONGS_TO_CUISINE"],
    "technique": ["USES_TECHNIQUE"],
}

ENTITY_VALUE_ALIASES = {
    "鸡肉": {"鸡肉", "鸡腿肉", "三黄鸡", "鸡胸肉", "鸡翅", "鸡翅中"},
    "牛肉": {"牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"},
    "猪肉": {"猪肉", "猪里脊", "猪里脊肉", "猪前腿肉", "猪大肠", "猪排骨", "排骨"},
    "鱼": {"鱼", "鲈鱼"},
    "土豆": {"土豆", "马铃薯"},
    "虾": {"虾", "鲜虾"},
}


def execute_query_plan(plan: QueryPlan, system: Any) -> dict[str, Any]:
    if plan.plan_type == "entity_lookup":
        return _execute_entity_lookup(plan, system)
    if plan.plan_type == "compound_recommendation":
        return _execute_compound_recommendation(plan, system)
    return {
        "success": False,
        "plan_type": plan.plan_type,
        "message": "unsupported plan",
        "items": [],
    }


def node_names_by_type(system: Any) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "ingredient": set(),
        "taste": set(),
        "cuisine": set(),
        "technique": set(),
    }
    executor = getattr(system, "executor", None)
    graph = getattr(executor, "graph", None)
    if graph is None:
        return result
    label_to_type = {
        "Ingredient": "ingredient",
        "Taste": "taste",
        "Cuisine": "cuisine",
        "Technique": "technique",
    }
    for _, attrs in graph.nodes(data=True):
        entity_type = label_to_type.get(str(attrs.get("label") or ""))
        name = str(attrs.get("name") or "").strip()
        if entity_type and name:
            result[entity_type].add(name)
    return result


def _execute_entity_lookup(plan: QueryPlan, system: Any) -> dict[str, Any]:
    entity_type = str(plan.entity_type or "")
    value = str(plan.entity_value or "")
    relations = ENTITY_RELATIONS.get(entity_type, [])
    if plan.relation_scope == "core_first" and entity_type == "ingredient":
        relations = ["USES_MAIN_INGREDIENT", "USES_AUXILIARY"]
    accepted_values = ENTITY_VALUE_ALIASES.get(value, {value})
    edges = _scan_dish_edges(system)
    if entity_type == "technique":
        derived_ingredients = _derived_ingredient_values(value, edges)
        if derived_ingredients:
            relations = [*relations, "USES_AUXILIARY", "USES_MAIN_INGREDIENT"]
            accepted_values = {*accepted_values, *derived_ingredients}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in edges:
        if item["relation"] not in relations:
            continue
        if item["target_name"] not in accepted_values:
            continue
        group_name = RELATION_LABELS.get(item["relation"], ("", item["relation"]))[1]
        grouped.setdefault(group_name, []).append(item)

    ordered_groups = []
    group_order = ["主食材", "配料", "调味品", "技法", "口味", "菜系"]
    for group_name in group_order:
        items = grouped.get(group_name, [])
        if items:
            ordered_groups.append({"name": group_name, "items": _dedupe_items(items)})

    return {
        "success": True,
        "plan_type": "entity_lookup",
        "query": plan.original_query,
        "entity": {"type": entity_type, "value": value},
        "relation_scope": plan.relation_scope,
        "groups": ordered_groups,
        "items": [item for group in ordered_groups for item in group["items"]],
        "source_policy": "local_graph_only",
        "web_fallback_allowed": False,
    }


def _derived_ingredient_values(value: str, edges: list[dict[str, Any]]) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    ingredient_values = {
        str(item.get("target_name") or "")
        for item in edges
        if item.get("relation") in {"USES_MAIN_INGREDIENT", "USES_AUXILIARY"}
    }
    candidates = {text}
    for suffix in ("炒", "制", "煮", "蒸", "炸", "煎", "炖"):
        if text.endswith(suffix) and len(text) > len(suffix):
            candidates.add(text[: -len(suffix)])
    return {candidate for candidate in candidates if candidate in ingredient_values}


def _execute_compound_recommendation(plan: QueryPlan, system: Any) -> dict[str, Any]:
    constraints = list(plan.constraints)
    dish_matches: dict[str, dict[str, Any]] = {}
    for dish_name in _dish_names(system):
        edges = [item for item in _scan_dish_edges(system) if item["dish_name"] == dish_name]
        matched_constraints = []
        for constraint in constraints:
            match = _match_constraint(edges, constraint)
            if match is not None:
                matched_constraints.append(match)
        if len(matched_constraints) == len(constraints):
            dish_matches[dish_name] = {
                "dish_name": dish_name,
                "matches": matched_constraints,
            }

    items = list(dish_matches.values())
    return {
        "success": True,
        "plan_type": "compound_recommendation",
        "query": plan.original_query,
        "constraints": [{"type": item.type, "value": item.value} for item in constraints],
        "items": items,
        "source_policy": "local_graph_only",
        "web_fallback_allowed": False,
    }


def _match_constraint(edges: list[dict[str, Any]], constraint: QueryConstraint) -> dict[str, Any] | None:
    relation_types = {
        "ingredient": {"USES_MAIN_INGREDIENT", "USES_AUXILIARY"},
        "taste": {"HAS_TASTE"},
        "cuisine": {"BELONGS_TO_CUISINE"},
        "technique": {"USES_TECHNIQUE"},
    }.get(constraint.type, set())
    accepted_values = {constraint.value, *constraint.aliases}
    for edge in edges:
        if edge["relation"] in relation_types and edge["target_name"] in accepted_values:
            return {
                "type": constraint.type,
                "value": constraint.value,
                "matched_value": edge["target_name"],
                "relation": edge["relation"],
                "role": RELATION_LABELS.get(edge["relation"], ("", edge["relation"]))[1],
                "amount": edge.get("amount") or "",
            }
    return None


def _scan_dish_edges(system: Any) -> list[dict[str, Any]]:
    executor = getattr(system, "executor", None)
    graph = getattr(executor, "graph", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if graph is None or not isinstance(dish_nodes, dict):
        return []
    edges: list[dict[str, Any]] = []
    for dish_name, dish_id in dish_nodes.items():
        for _, target_id, edge_data in graph.edges(dish_id, data=True):
            relation = str(edge_data.get("relation") or edge_data.get("type") or "")
            target_node = graph.nodes[target_id]
            target_name = str(target_node.get("name") or "")
            if not relation or not target_name:
                continue
            edges.append({
                "dish_name": str(dish_name),
                "relation": relation,
                "target_name": target_name,
                "amount": str(edge_data.get("amount") or ""),
            })
    return edges


def _dish_names(system: Any) -> list[str]:
    executor = getattr(system, "executor", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if not isinstance(dish_nodes, dict):
        return []
    return [str(name) for name in dish_nodes.keys()]


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result = []
    for item in items:
        key = (str(item.get("dish_name")), str(item.get("relation")), str(item.get("target_name")))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
