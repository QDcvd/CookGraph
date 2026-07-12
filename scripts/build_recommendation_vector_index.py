#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the offline recommendation vector index for local recipe recommendations."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG_PATH = PROJECT_ROOT / "config" / "2kg_chem+recipe_fire_12K.pkl"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recommendation_aliases.json"
DEFAULT_MODEL_DIR = Path(os.getenv("MINICOOK_EMBEDDING_MODEL_DIR") or PROJECT_ROOT / "models" / "gte-large-zh")
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "backend" / ".cache" / "recipe_recommendation_vector_index.npz"
EXISTING_SEMANTIC_INDEX_PATH = PROJECT_ROOT / "backend" / ".cache" / "recipe_semantic_index.npz"

RELATION_TO_FIELD = {
    "USES_MAIN_INGREDIENT": "main_ingredients",
    "USES_AUXILIARY": "auxiliary_ingredients",
    "USES_SEASONING": "seasonings",
    "HAS_TASTE": "tastes",
    "BELONGS_TO_CUISINE": "cuisines",
    "USES_TECHNIQUE": "techniques",
    "SUITABLE_FOR": "meal_times",
}

LIST_FIELDS = [
    "main_ingredients",
    "auxiliary_ingredients",
    "seasonings",
    "tastes",
    "cuisines",
    "techniques",
    "meal_times",
    "scenario_tags",
]


def _stable_hash(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _valid_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "无数据"}:
        return ""
    return text


def _append_unique(target: list[str], value: str) -> None:
    value = _valid_text(value)
    if value and value not in target:
        target.append(value)


def _valid_meal_time(value: str) -> str:
    text = _valid_text(value)
    if not text or len(text) > 30:
        return ""
    if any(marker in text for marker in ("早餐", "中餐", "午餐", "晚餐", "零食", "夜宵")):
        return text
    return ""


def _edge_relation(edge_data: dict[str, Any]) -> str:
    return str(edge_data.get("relation") or edge_data.get("type") or "").strip()


def extract_recipe_records(kg_path: Path = DEFAULT_KG_PATH) -> list[dict]:
    """从 Dish 节点和出边抽取每道菜的结构化推荐字段。"""
    with kg_path.open("rb") as f:
        graph = pickle.load(f)

    records: list[dict] = []
    for node_id, attrs in graph.nodes(data=True):
        if attrs.get("label") != "Dish":
            continue
        dish_name = _valid_text(attrs.get("name"))
        if not dish_name:
            continue

        record = {
            "dish_name": dish_name,
            "main_ingredients": [],
            "auxiliary_ingredients": [],
            "seasonings": [],
            "tastes": [],
            "cuisines": [],
            "techniques": [],
            "meal_times": [],
            "cooking_method_desc": _valid_text(attrs.get("cooking_method_desc")),
            "cooking_process": _valid_text(attrs.get("cooking_process")),
            "cooking_tips": _valid_text(attrs.get("cooking_tips")),
        }

        for _, target_id, edge_data in graph.edges(node_id, data=True):
            field = RELATION_TO_FIELD.get(_edge_relation(edge_data))
            if not field:
                continue
            target_node = graph.nodes[target_id]
            target_name = _valid_text(target_node.get("name"))
            if field == "meal_times":
                target_name = _valid_meal_time(target_name)
            if target_name:
                _append_unique(record[field], target_name)

        record["scenario_tags"] = generate_scenario_tags(record)
        record["recommendation_reason"] = build_recommendation_reason(record)
        records.append(record)

    records.sort(key=lambda item: item["dish_name"])
    return records


def _contains_any(values: list[str], keywords: tuple[str, ...]) -> bool:
    joined = " ".join(values)
    return any(keyword in joined for keyword in keywords)


def _step_count(text: str) -> int:
    return max(text.count("；"), text.count(";"), text.count("。"), text.count("."), text.count("步骤"))


def generate_scenario_tags(record: dict) -> list[str]:
    """根据技法、口味、食材、步骤长度等确定性规则生成弱标签。"""
    tags: list[str] = []
    techniques = record.get("techniques", [])
    tastes = record.get("tastes", [])
    ingredients = [
        *(record.get("main_ingredients", []) or []),
        *(record.get("auxiliary_ingredients", []) or []),
    ]
    process = record.get("cooking_process") or record.get("cooking_method_desc") or ""

    if _contains_any(techniques, ("凉拌", "拌")):
        tags.extend(["清爽", "少油", "凉拌"])
    if _contains_any(techniques, ("白灼", "清蒸", "蒸制")):
        tags.extend(["清淡", "少油"])
    if _contains_any(tastes, ("酸辣", "酸甜")):
        tags.append("开胃")
    if _contains_any(tastes, ("香辣", "麻辣")):
        tags.extend(["下饭", "重口味"])
    if _contains_any(techniques, ("爆炒", "小炒", "炒制", "清炒", "滑炒")):
        tags.extend(["快手", "热菜"])
    if _contains_any(ingredients, ("黄瓜", "番茄", "西红柿", "生菜", "苦瓜", "冬瓜")):
        tags.extend(["清爽", "夏天"])
    if process and (len(process) <= 180 or _step_count(process) <= 4):
        tags.append("快手")

    return list(dict.fromkeys(tags))


def build_recommendation_reason(record: dict) -> str:
    parts: list[str] = []
    if record.get("main_ingredients"):
        parts.append("主料包含" + "、".join(record["main_ingredients"][:3]))
    if record.get("tastes"):
        parts.append("口味偏" + "、".join(record["tastes"][:2]))
    if record.get("techniques"):
        parts.append("技法为" + "、".join(record["techniques"][:2]))
    if record.get("scenario_tags"):
        parts.append("适合" + "、".join(record["scenario_tags"][:3]))
    return "；".join(parts) or "本地图谱收录菜品。"


def build_recommendation_document(record: dict) -> str:
    """拼接用于 embedding 的推荐文档文本。"""
    method = record.get("cooking_method_desc") or record.get("cooking_process") or ""
    method = method[:240]
    return "\n".join(
        [
            f"菜名：{record['dish_name']}",
            f"主料：{'、'.join(record.get('main_ingredients', []))}",
            f"辅料：{'、'.join(record.get('auxiliary_ingredients', []))}",
            f"调料：{'、'.join(record.get('seasonings', []))}",
            f"口味：{'、'.join(record.get('tastes', []))}",
            f"菜系：{'、'.join(record.get('cuisines', []))}",
            f"技法：{'、'.join(record.get('techniques', []))}",
            f"适合时段：{'、'.join(record.get('meal_times', []))}",
            f"场景弱标签：{'、'.join(record.get('scenario_tags', []))}",
            f"推荐理由：{record.get('recommendation_reason', '')}",
            f"做法摘要：{method}",
        ]
    )


def build_index(records: list[dict], model_dir: Path = DEFAULT_MODEL_DIR, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    """编码 documents 并保存 npz。"""
    documents = [build_recommendation_document(record) for record in records]
    if not documents:
        raise ValueError("没有可用于推荐索引的菜谱记录。")

    embeddings = _reuse_existing_semantic_embeddings(records)
    if embeddings is None:
        if not model_dir.is_dir():
            raise FileNotFoundError(f"本地 embedding 模型不存在：{model_dir}")
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError("缺少 sentence-transformers，请先安装 sentence-transformers") from e

        model = SentenceTransformer(str(model_dir))
        embeddings = model.encode(
            documents,
            batch_size=16,
            normalize_embeddings=True,
            show_progress_bar=True,
        ).astype("float32")

    alias_mtime = str(DEFAULT_ALIAS_PATH.stat().st_mtime_ns) if DEFAULT_ALIAS_PATH.is_file() else "0"
    version = _stable_hash(
        [
            str(DEFAULT_KG_PATH.resolve()),
            str(DEFAULT_KG_PATH.stat().st_mtime_ns),
            str(model_dir.resolve()),
            alias_mtime,
            _stable_hash(documents),
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": np.array(version),
        "dish_names": np.array([record["dish_name"] for record in records]),
        "documents": np.array(documents),
        "embeddings": embeddings,
        "recommendation_reasons": np.array([record.get("recommendation_reason", "") for record in records]),
    }
    for field in LIST_FIELDS:
        payload[f"{field}_json"] = np.array(
            [json.dumps(record.get(field, []), ensure_ascii=False) for record in records]
        )

    np.savez_compressed(output_path, **payload)
    print(f"[recommendation-index] records={len(records)} dim={embeddings.shape[1]} -> {output_path}")


def _reuse_existing_semantic_embeddings(records: list[dict]) -> np.ndarray | None:
    """复用已存在的 gte 菜谱语义索引，加速本地推荐索引生成。

    设置 RECIPE_RECOMMENDATION_REUSE_SEMANTIC_INDEX=0 可强制重新编码推荐文档。
    """
    if os.getenv("RECIPE_RECOMMENDATION_REUSE_SEMANTIC_INDEX", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    if not EXISTING_SEMANTIC_INDEX_PATH.is_file():
        return None
    try:
        data = np.load(EXISTING_SEMANTIC_INDEX_PATH, allow_pickle=False)
        names = [str(item) for item in data["names"].tolist()]
        embeddings = data["embeddings"].astype("float32")
    except Exception:
        return None
    by_name = {name: embeddings[index] for index, name in enumerate(names)}
    rows: list[np.ndarray] = []
    for record in records:
        embedding = by_name.get(str(record.get("dish_name") or ""))
        if embedding is None:
            return None
        rows.append(embedding)
    reused = np.vstack(rows).astype("float32")
    print(f"[recommendation-index] reused existing gte semantic embeddings: {EXISTING_SEMANTIC_INDEX_PATH}")
    return reused


def main() -> None:
    records = extract_recipe_records(DEFAULT_KG_PATH)
    build_index(records, DEFAULT_MODEL_DIR, DEFAULT_OUTPUT_PATH)


if __name__ == "__main__":
    main()
