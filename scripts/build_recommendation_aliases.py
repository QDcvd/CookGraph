#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build recommendation aliases from the current 12K recipe graph.

The output is intentionally graph-validated. Old alias files may come from a
smaller demo dataset, so this script only keeps groups that still touch the
current graph nodes.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG_PATH = PROJECT_ROOT / "config" / "2kg_chem+recipe_fire_12K.pkl"
RECIPE_ALIAS_PATH = PROJECT_ROOT / "config" / "recipe_aliases.json"
REVERSE_ALIAS_PATH = PROJECT_ROOT / "config" / "reverse_entity_aliases.json"
OUTPUT_PATH = PROJECT_ROOT / "config" / "recommendation_aliases.json"
REJECTED_PATH = PROJECT_ROOT / "config" / "recommendation_aliases.rejected.json"

LABEL_TO_KIND = {
    "Ingredient": "ingredient",
    "Seasoning": "seasoning",
    "Taste": "taste",
    "Cuisine": "cuisine",
    "Technique": "technique",
}


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_group(canonical: str, aliases: list[str]) -> list[str]:
    values = [str(canonical).strip(), *(str(item).strip() for item in aliases)]
    return list(dict.fromkeys(item for item in values if item))


def load_graph_nodes(kg_path: Path = DEFAULT_KG_PATH) -> dict[str, set[str]]:
    """返回当前图谱节点名，至少包含 ingredient/seasoning/taste/cuisine/technique。"""
    with kg_path.open("rb") as f:
        graph = pickle.load(f)

    nodes: dict[str, set[str]] = {kind: set() for kind in LABEL_TO_KIND.values()}
    for _, attrs in graph.nodes(data=True):
        kind = LABEL_TO_KIND.get(str(attrs.get("label") or ""))
        name = str(attrs.get("name") or "").strip()
        if kind and name:
            nodes[kind].add(name)
    nodes["scenario"] = set()
    return nodes


def _guess_kind(group: list[str], graph_nodes: dict[str, set[str]]) -> str | None:
    for kind in ("ingredient", "seasoning", "taste", "cuisine", "technique"):
        names = graph_nodes.get(kind, set())
        if any(item in names for item in group):
            return kind
    return None


def _merge_group(target: dict[str, dict[str, list[str]]], kind: str, canonical: str, group: list[str]) -> None:
    if kind not in target:
        target[kind] = {}
    current = target[kind].get(canonical, [])
    target[kind][canonical] = _normalize_group(canonical, [*current, *group])


def load_existing_alias_sources(project_root: Path = PROJECT_ROOT) -> dict[str, dict[str, list[str]]]:
    """读取旧 alias 配置，转换成推荐别名表结构。"""
    graph_nodes = load_graph_nodes(project_root / "config" / "2kg_chem+recipe_fire_12K.pkl")
    aliases: dict[str, dict[str, list[str]]] = {kind: {} for kind in [*LABEL_TO_KIND.values(), "scenario"]}

    reverse_data = _read_json(project_root / "config" / "reverse_entity_aliases.json")
    if isinstance(reverse_data, dict):
        for kind, groups in reverse_data.items():
            if kind not in aliases or not isinstance(groups, dict):
                continue
            for canonical, values in groups.items():
                if isinstance(values, list):
                    _merge_group(aliases, kind, str(canonical), _normalize_group(str(canonical), values))

    recipe_data = _read_json(project_root / "config" / "recipe_aliases.json")
    if isinstance(recipe_data, dict):
        for canonical, values in recipe_data.items():
            if not isinstance(values, list):
                continue
            group = _normalize_group(str(canonical), values)
            kind = _guess_kind(group, graph_nodes)
            if kind:
                _merge_group(aliases, kind, str(canonical), group)

    return aliases


def build_default_alias_groups() -> dict[str, dict[str, list[str]]]:
    """返回默认厨房常见同义词规则。"""
    return {
        "ingredient": {
            "番茄": ["番茄", "西红柿", "洋柿子"],
            "蒜苔": ["蒜苔", "蒜薹"],
            "土豆": ["土豆", "马铃薯", "洋芋"],
            "包菜": ["包菜", "卷心菜", "圆白菜", "洋白菜", "高丽菜", "莲花白"],
            "蛤蜊": ["蛤蜊", "花甲", "花蛤"],
            "辣椒": ["辣椒", "青椒", "尖椒", "小米辣", "泡椒", "红椒", "青红椒"],
            "牛肉": ["牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"],
            "鸡蛋": ["鸡蛋", "蛋"],
        },
        "seasoning": {
            "酱油": ["酱油", "生抽", "老抽"],
            "食用油": ["食用油", "油"],
        },
        "taste": {
            "香辣味": ["香辣味", "香辣"],
            "麻辣味": ["麻辣味", "麻辣"],
            "酸甜味": ["酸甜味", "酸甜", "糖醋味"],
            "酸辣味": ["酸辣味", "酸辣"],
        },
        "cuisine": {
            "川菜": ["川菜", "四川菜", "川味", "四川味"],
            "湘菜": ["湘菜", "湖南菜", "湘味"],
            "粤菜": ["粤菜", "广东菜"],
        },
        "technique": {
            "爆炒": ["爆炒", "大火快炒"],
            "清蒸": ["清蒸", "蒸"],
            "白灼": ["白灼"],
            "红烧": ["红烧"],
            "凉拌": ["凉拌", "拌"],
        },
        "scenario": {
            "天气热": ["天气热", "夏天", "热天", "清爽", "开胃"],
            "下饭": ["下饭", "重口味", "香辣", "麻辣"],
            "清淡": ["清淡", "少油", "清爽"],
            "快手": ["快手", "简单", "省事", "短时间"],
        },
    }


def validate_aliases(
    aliases: dict[str, dict[str, list[str]]],
    graph_nodes: dict[str, set[str]],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, list[str]]]]:
    """只保留 canonical 或 alias 至少一个存在于当前图谱节点中的条目。"""
    accepted: dict[str, dict[str, list[str]]] = {}
    rejected: dict[str, dict[str, list[str]]] = {}

    for kind, groups in aliases.items():
        if not isinstance(groups, dict):
            continue
        for canonical, values in groups.items():
            group = _normalize_group(str(canonical), values if isinstance(values, list) else [])
            if not group:
                continue
            if kind == "scenario":
                _merge_group(accepted, kind, str(canonical), group)
                continue
            nodes = graph_nodes.get(kind, set())
            if any(item in nodes for item in group):
                _merge_group(accepted, kind, str(canonical), group)
            else:
                _merge_group(rejected, kind, str(canonical), group)

    return accepted, rejected


def _merge_alias_sources(*sources: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    merged: dict[str, dict[str, list[str]]] = {}
    for source in sources:
        for kind, groups in source.items():
            if not isinstance(groups, dict):
                continue
            for canonical, values in groups.items():
                if isinstance(values, list):
                    _merge_group(merged, kind, str(canonical), _normalize_group(str(canonical), values))
    return merged


def main() -> None:
    graph_nodes = load_graph_nodes(DEFAULT_KG_PATH)
    aliases = _merge_alias_sources(load_existing_alias_sources(PROJECT_ROOT), build_default_alias_groups())
    accepted, rejected = validate_aliases(aliases, graph_nodes)

    OUTPUT_PATH.write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
    REJECTED_PATH.write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")

    accepted_count = sum(len(groups) for groups in accepted.values())
    rejected_count = sum(len(groups) for groups in rejected.values())
    print(f"[aliases] accepted={accepted_count} -> {OUTPUT_PATH}")
    print(f"[aliases] rejected={rejected_count} -> {REJECTED_PATH}")


if __name__ == "__main__":
    main()
