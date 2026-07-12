"""关键菜谱关系/属性向量召回层。

这个模块只作为 recipe_query_tool 内部补召回能力，不暴露为 Agent 工具。
第一版聚焦具体菜的长文本属性：做法、火力、备菜、下锅过程、提示。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from backend.recipe_semantic_retriever import _load_model

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "backend" / ".cache"
DEFAULT_INDEX_PATH = DEFAULT_CACHE_DIR / "recipe_relation_vector_index.npz"

RELATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("fire_control_process", "火力调节过程"),
    ("prep_process", "备菜过程"),
    ("cooking_process", "下锅/烹饪过程"),
    ("cooking_method_desc", "完整做法"),
    ("cooking_tips", "烹饪提示"),
)

MIN_RELATION_VECTOR_SCORE = float(os.getenv("RECIPE_RELATION_VECTOR_MIN_SCORE", "0.58"))
TOP_K = int(os.getenv("RECIPE_RELATION_VECTOR_TOP_K", "3"))

_index_cache: dict[str, object] | None = None


@dataclass(frozen=True)
class RecipeRelationVectorMatch:
    dish_name: str
    field: str
    field_label: str
    text: str
    score: float


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _stable_hash(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _dish_nodes(system: Any) -> list[tuple[str, dict[str, Any]]]:
    executor = getattr(system, "executor", None)
    graph = getattr(executor, "graph", None)
    if graph is None:
        return []
    rows: list[tuple[str, dict[str, Any]]] = []
    for _, attrs in graph.nodes(data=True):
        if attrs.get("label") != "Dish":
            continue
        name = str(attrs.get("name") or "").strip()
        if name:
            rows.append((name, attrs))
    return rows


def _valid_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "无数据"}:
        return ""
    return text


def _document_text(dish_name: str, field_label: str, value: str) -> str:
    return f"菜名：{dish_name}\n字段：{field_label}\n内容：{value}"


def _kg_version_key(system: Any, documents: list[str]) -> str:
    kg_path = Path(str(getattr(system, "kg_path", "") or ""))
    kg_mtime = str(kg_path.stat().st_mtime_ns) if kg_path.is_file() else "0"
    model_dir = str(Path(os.getenv("MINICOOK_EMBEDDING_MODEL_DIR") or PROJECT_ROOT / "models" / "gte-large-zh").resolve())
    return _stable_hash(
        [
            model_dir,
            str(kg_path.resolve()) if str(kg_path) else "",
            kg_mtime,
            "|".join(field for field, _ in RELATION_FIELDS),
            _stable_hash(documents),
        ]
    )


def _collect_relation_documents(
    system: Any,
    allowed_dish_names: set[str] | None = None,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    dish_names: list[str] = []
    fields: list[str] = []
    field_labels: list[str] = []
    texts: list[str] = []
    documents: list[str] = []

    for dish_name, attrs in _dish_nodes(system):
        if allowed_dish_names and dish_name not in allowed_dish_names:
            continue
        for field, label in RELATION_FIELDS:
            value = _valid_value(attrs.get(field))
            if not value:
                continue
            dish_names.append(dish_name)
            fields.append(field)
            field_labels.append(label)
            texts.append(value)
            documents.append(_document_text(dish_name, label, value))

    return dish_names, fields, field_labels, texts, documents


def _build_transient_index(system: Any, allowed_dish_names: set[str]) -> dict[str, object]:
    dish_names, fields, field_labels, texts, documents = _collect_relation_documents(system, allowed_dish_names)
    if not documents:
        raise ValueError("没有可用于关系向量召回的目标菜长文本字段。")
    model = _load_model()
    embeddings = model.encode(
        documents,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")
    return {
        "version": "transient",
        "dish_names": dish_names,
        "fields": fields,
        "field_labels": field_labels,
        "texts": texts,
        "documents": documents,
        "embeddings": embeddings,
    }


def _load_or_build_index(system: Any) -> dict[str, object]:
    global _index_cache

    dish_names, fields, field_labels, texts, documents = _collect_relation_documents(system)
    if not documents:
        raise ValueError("没有可用于关系向量召回的菜谱长文本字段。")

    version = _kg_version_key(system, documents)
    if _index_cache and _index_cache.get("version") == version:
        return _index_cache

    if DEFAULT_INDEX_PATH.is_file():
        try:
            data = np.load(DEFAULT_INDEX_PATH, allow_pickle=False)
            if str(data["version"].item()) == version:
                _index_cache = {
                    "version": version,
                    "dish_names": [str(item) for item in data["dish_names"].tolist()],
                    "fields": [str(item) for item in data["fields"].tolist()],
                    "field_labels": [str(item) for item in data["field_labels"].tolist()],
                    "texts": [str(item) for item in data["texts"].tolist()],
                    "documents": [str(item) for item in data["documents"].tolist()],
                    "embeddings": data["embeddings"].astype("float32"),
                }
                return _index_cache
        except Exception:
            pass

    model = _load_model()
    embeddings = model.encode(
        documents,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")

    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        DEFAULT_INDEX_PATH,
        version=np.array(version),
        dish_names=np.array(dish_names),
        fields=np.array(fields),
        field_labels=np.array(field_labels),
        texts=np.array(texts),
        documents=np.array(documents),
        embeddings=embeddings,
    )

    _index_cache = {
        "version": version,
        "dish_names": dish_names,
        "fields": fields,
        "field_labels": field_labels,
        "texts": texts,
        "documents": documents,
        "embeddings": embeddings,
    }
    return _index_cache


def search_relation_vectors(
    query: str,
    system: Any,
    *,
    dish_names: Iterable[str] | None = None,
    fields: Iterable[str] | None = None,
    top_k: int = TOP_K,
    min_score: float = MIN_RELATION_VECTOR_SCORE,
) -> list[RecipeRelationVectorMatch]:
    """检索关键关系/属性向量，返回高置信候选。"""
    text = query.strip()
    if not text:
        return []

    allowed_names = {item for item in (dish_names or []) if item}
    allowed_fields = {item for item in (fields or []) if item}
    if not allowed_names:
        # A 用途是具体菜长文本属性补召回。没有菜名约束时不做全量即时向量检索，
        # 避免首次请求触发 13k 菜谱的重型全量编码。
        return []

    index = _build_transient_index(system, allowed_names)
    names = list(index["dish_names"])  # type: ignore[arg-type]
    index_fields = list(index["fields"])  # type: ignore[arg-type]
    field_labels = list(index["field_labels"])  # type: ignore[arg-type]
    values = list(index["texts"])  # type: ignore[arg-type]
    embeddings = index["embeddings"]  # type: ignore[assignment]

    allowed_indices = [
        idx
        for idx, (name, field) in enumerate(zip(names, index_fields))
        if (not allowed_names or name in allowed_names) and (not allowed_fields or field in allowed_fields)
    ]
    if not allowed_indices:
        return []

    model = _load_model()
    query_embedding = model.encode(
        [text],
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")[0]
    subset_embeddings = embeddings[allowed_indices]
    scores = np.asarray(subset_embeddings @ query_embedding, dtype="float32")
    order = np.argsort(-scores)[: max(top_k, 1)]

    matches: list[RecipeRelationVectorMatch] = []
    for local_index in order:
        score = float(scores[int(local_index)])
        if score < min_score:
            continue
        idx = allowed_indices[int(local_index)]
        matches.append(
            RecipeRelationVectorMatch(
                dish_name=names[idx],
                field=index_fields[idx],
                field_label=field_labels[idx],
                text=values[idx],
                score=score,
            )
        )
    return matches
