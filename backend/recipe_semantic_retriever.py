"""菜谱混合召回层。

这个模块不暴露为 Agent 工具，只作为 recipe_query_tool 内部前置能力。

召回策略：
1. alias：基于 config/recipe_aliases.json 的菜名/食材别名包含匹配。
2. lexical：基于字符 n-gram TF-IDF 的关键词召回。
3. dense：基于本地 gte-large-zh 的语义向量召回。
4. fusion：用 RRF 融合多个 ranked list，不直接混合不同量纲的原始分数。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = Path(os.getenv("MINICOOK_EMBEDDING_MODEL_DIR") or PROJECT_ROOT / "models" / "gte-large-zh")
DEFAULT_RECIPE_XLSX = PROJECT_ROOT / "doc" / "菜谱.xlsx"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "recipe_aliases.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "backend" / ".cache"
DEFAULT_INDEX_PATH = DEFAULT_CACHE_DIR / "recipe_semantic_index.npz"

MIN_REWRITE_SCORE = float(os.getenv("RECIPE_HYBRID_MIN_SCORE", "0.030"))
MIN_REWRITE_MARGIN = float(os.getenv("RECIPE_HYBRID_MIN_MARGIN", "0.0005"))
TOP_K = int(os.getenv("RECIPE_SEMANTIC_TOP_K", "5"))
RRF_K = int(os.getenv("RECIPE_RRF_K", "60"))

_model = None
_index_cache: dict[str, object] | None = None
_alias_groups_cache: list[set[str]] | None = None


@dataclass
class RecipeSemanticMatch:
    """菜谱召回结果。"""

    dish_name: str
    score: float
    margin: float
    accepted: bool
    candidates: list[tuple[str, float]]
    rewritten_query: str
    matched_text: str | None = None
    retrieval_debug: str = ""


def _load_model():
    """懒加载本地 embedding 模型。"""
    global _model
    if _model is not None:
        return _model
    if not DEFAULT_MODEL_PATH.is_dir():
        raise FileNotFoundError(f"本地 embedding 模型不存在：{DEFAULT_MODEL_PATH}")
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError("缺少 sentence-transformers，请先安装 sentence-transformers") from e
    _model = SentenceTransformer(str(DEFAULT_MODEL_PATH))
    return _model


def _stable_hash(parts: Iterable[str]) -> str:
    """生成索引缓存版本号。"""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _normalize_text(text: str) -> str:
    """面向中文短查询的轻量规范化。"""
    return re.sub(r"\s+", "", text.lower())


def _load_alias_groups() -> list[set[str]]:
    """读取同义词组，并补成双向 group。"""
    global _alias_groups_cache
    if _alias_groups_cache is not None:
        return _alias_groups_cache
    groups: list[set[str]] = []
    if DEFAULT_ALIAS_PATH.is_file():
        data = json.loads(DEFAULT_ALIAS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, values in data.items():
                group = {str(key).strip()}
                if isinstance(values, list):
                    group.update(str(item).strip() for item in values if str(item).strip())
                groups.append({item for item in group if item})
    _alias_groups_cache = groups
    return groups


def _expand_aliases_for_text(text: str) -> set[str]:
    """返回 text 中实体词的别名集合。"""
    normalized = _normalize_text(text)
    aliases: set[str] = set()
    for group in _load_alias_groups():
        normalized_group = {_normalize_text(item) for item in group}
        if any(item and item in normalized for item in normalized_group):
            aliases.update(group)
    return aliases


def _variant_texts(text: str) -> set[str]:
    """基于别名组生成少量变体文本，用于菜名和 query 匹配。"""
    variants = {text}
    for _ in range(3):
        before_count = len(variants)
        current = list(variants)
        for value in current:
            for group in _load_alias_groups():
                for source in group:
                    if source not in value:
                        continue
                    for target in group:
                        if target != source:
                            variants.add(value.replace(source, target))
        if len(variants) == before_count:
            break
    return variants


def _read_recipe_rows() -> list[dict[str, str]]:
    """读取 Excel 菜谱行，返回统一字段。"""
    if not DEFAULT_RECIPE_XLSX.is_file():
        return []
    try:
        import openpyxl
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError("缺少 openpyxl，请先安装 openpyxl") from e

    workbook = openpyxl.load_workbook(DEFAULT_RECIPE_XLSX, read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    headers = [str(item or "").strip() for item in next(rows)]

    def find_col(prefix: str) -> int | None:
        for index, header in enumerate(headers):
            if header.startswith(prefix):
                return index
        return None

    columns = {
        "name": find_col("cook_name"),
        "technique": find_col("technique"),
        "taste": find_col("taste"),
        "cuisine": find_col("cuisine"),
        "main_ingredients": find_col("main_ingredients"),
        "auxiliary_ingredients": find_col("auxiliary_ingredients"),
        "seasonings": find_col("seasonings"),
        "method": find_col("cooking_method"),
        "tips": find_col("cooking_tips"),
    }

    recipes: list[dict[str, str]] = []
    for row in rows:
        name_index = columns["name"]
        if name_index is None or name_index >= len(row) or not row[name_index]:
            continue
        item: dict[str, str] = {}
        for key, index in columns.items():
            if index is None or index >= len(row) or row[index] is None:
                item[key] = ""
            else:
                item[key] = str(row[index]).strip()
        recipes.append(item)
    return recipes


def _recipe_document(row: dict[str, str]) -> str:
    """拼接用于检索的菜谱文本。"""
    name = row.get("name", "")
    name_variants = " ".join(sorted(_variant_texts(name)))
    field_aliases = " ".join(
        sorted(
            _expand_aliases_for_text(
                " ".join(
                    [
                        row.get("main_ingredients", ""),
                        row.get("auxiliary_ingredients", ""),
                        row.get("seasonings", ""),
                    ]
                )
            )
        )
    )
    parts = [
        name_variants,
        field_aliases,
        row.get("technique", ""),
        row.get("taste", ""),
        row.get("cuisine", ""),
        row.get("main_ingredients", ""),
        row.get("auxiliary_ingredients", ""),
        row.get("seasonings", ""),
        row.get("method", "")[:500],
        row.get("tips", ""),
    ]
    return " ".join(part for part in parts if part)


def _load_or_build_index(allowed_dish_names: set[str] | None = None) -> dict[str, object]:
    """加载或构建菜谱 dense 向量索引。"""
    global _index_cache

    recipes = _read_recipe_rows()
    if allowed_dish_names:
        recipes = [item for item in recipes if item.get("name") in allowed_dish_names]
        missing = sorted(allowed_dish_names - {item.get("name", "") for item in recipes})
        recipes.extend({"name": name} for name in missing)

    names = [item.get("name", "") for item in recipes if item.get("name")]
    documents = [_recipe_document(item) for item in recipes if item.get("name")]
    version = _stable_hash(
        [
            str(DEFAULT_MODEL_PATH.resolve()),
            str(DEFAULT_RECIPE_XLSX.stat().st_mtime_ns if DEFAULT_RECIPE_XLSX.exists() else 0),
            str(DEFAULT_ALIAS_PATH.stat().st_mtime_ns if DEFAULT_ALIAS_PATH.exists() else 0),
            json.dumps(names, ensure_ascii=False),
            json.dumps(documents, ensure_ascii=False),
        ]
    )

    if _index_cache and _index_cache.get("version") == version:
        return _index_cache

    if DEFAULT_INDEX_PATH.is_file():
        try:
            data = np.load(DEFAULT_INDEX_PATH, allow_pickle=False)
            cached_version = str(data["version"].item())
            if cached_version == version:
                _index_cache = {
                    "version": cached_version,
                    "names": [str(item) for item in data["names"].tolist()],
                    "documents": [str(item) for item in data["documents"].tolist()],
                    "embeddings": data["embeddings"].astype("float32"),
                }
                return _index_cache
        except Exception:
            pass

    if not documents:
        raise ValueError("没有可用于菜谱召回的数据。")

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
        names=np.array(names),
        documents=np.array(documents),
        embeddings=embeddings,
    )

    _index_cache = {
        "version": version,
        "names": names,
        "documents": documents,
        "embeddings": embeddings,
    }
    return _index_cache


def _dense_rank(query: str, names: list[str], embeddings: np.ndarray, limit: int) -> list[tuple[str, float]]:
    """gte-large-zh dense 向量召回。"""
    model = _load_model()
    query_embedding = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")[0]
    scores = np.asarray(embeddings @ query_embedding, dtype="float32")
    order = np.argsort(-scores)[:limit]
    return [(names[int(index)], float(scores[int(index)])) for index in order]


def _lexical_rank(query: str, names: list[str], documents: list[str], limit: int) -> list[tuple[str, float]]:
    """字符 n-gram TF-IDF 关键词召回。"""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ModuleNotFoundError:
        return []

    corpus = documents + [query]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 4), lowercase=False)
    matrix = vectorizer.fit_transform(corpus)
    scores = cosine_similarity(matrix[-1], matrix[:-1])[0]
    order = np.argsort(-scores)[:limit]
    return [(names[int(index)], float(scores[int(index)])) for index in order if float(scores[int(index)]) > 0]


def _alias_rank(query: str, names: list[str], documents: list[str], limit: int) -> list[tuple[str, float]]:
    """别名/包含匹配召回。"""
    normalized_query = _normalize_text(query)
    query_aliases = {_normalize_text(item) for item in _expand_aliases_for_text(query)}
    ranked: list[tuple[str, float]] = []

    for name, document in zip(names, documents):
        variants = {_normalize_text(item) for item in _variant_texts(name)}
        document_aliases = {_normalize_text(item) for item in _expand_aliases_for_text(document)}
        score = 0.0

        # 菜名完整变体是强证据，例如：番茄炒蛋 -> 西红柿炒鸡蛋。
        for term in variants:
            if len(term) < 2:
                continue
            if term in normalized_query:
                score = max(score, 5.0 + min(len(term), 10) / 10)
            elif normalized_query in term and len(normalized_query) >= 2:
                score = max(score, 3.0)

        # 食材/配料别名是弱证据，避免“鸡蛋”把所有鸡蛋菜都顶上来。
        weak_hits = [
            term
            for term in document_aliases
            if len(term) >= 2 and (term in normalized_query or term in query_aliases)
        ]
        if weak_hits:
            score = max(score, min(1.5, 0.35 * len(set(weak_hits)) + 0.05 * max(len(item) for item in weak_hits)))

        if score > 0:
            ranked.append((name, score))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def _rrf_fuse(rankings: list[list[tuple[str, float]]], limit: int) -> list[tuple[str, float]]:
    """用 Reciprocal Rank Fusion 融合多个 ranked list。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, (name, _) in enumerate(ranking, start=1):
            if name in seen:
                continue
            seen.add(name)
            scores[name] = scores.get(name, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]


def _find_matched_text(query: str, dish_name: str, document: str) -> str | None:
    """找出 query 中最可能对应标准菜名的文本片段。"""
    normalized_query = _normalize_text(query)
    candidates = set(_variant_texts(dish_name)) | _expand_aliases_for_text(dish_name) | _expand_aliases_for_text(document)
    candidates = {item for item in candidates if len(_normalize_text(item)) >= 2}
    for candidate in sorted(candidates, key=lambda item: len(_normalize_text(item)), reverse=True):
        if _normalize_text(candidate) in normalized_query:
            return candidate
    return None


def _is_unsafe_partial_dish_match(query: str, dish_name: str, matched_text: str | None) -> bool:
    """Reject rewriting an unknown compound dish from a short ingredient fragment."""
    if not matched_text:
        return False
    normalized_query = _normalize_text(query)
    normalized_match = _normalize_text(matched_text)
    normalized_dish = _normalize_text(dish_name)
    if normalized_match == normalized_dish:
        return False
    if len(normalized_match) >= 4:
        return False
    action_markers = ("炒", "炖", "焖", "蒸", "煮", "炸", "煎", "烤", "拌", "煲")
    if not any(marker in normalized_query for marker in action_markers):
        return False
    if normalized_query == normalized_match:
        return False
    return True


def rewrite_query_with_dish(original_query: str, dish_name: str, matched_text: str | None = None) -> str:
    """只做菜名归一，不硬编码用户意图。

    如果能定位到原句里的菜名表达，就把它替换成标准菜名，让原图谱解析器继续判断
    火力、备菜、食材、反向等意图；如果定位不到，则退回标准菜名做 summary 查询。
    """
    text = original_query.strip()
    if not text:
        return dish_name
    if dish_name in text:
        return text
    if matched_text and matched_text in text:
        return text.replace(matched_text, dish_name, 1)
    return dish_name


def semantic_match_recipe(
    query: str,
    allowed_dish_names: Iterable[str] | None = None,
    min_score: float = MIN_REWRITE_SCORE,
    min_margin: float = MIN_REWRITE_MARGIN,
    top_k: int = TOP_K,
) -> RecipeSemanticMatch | None:
    """返回 query 最接近的标准菜名；低置信时 accepted=False。"""
    text = query.strip()
    if not text:
        return None

    allowed = {item for item in (allowed_dish_names or []) if item}
    index = _load_or_build_index(allowed or None)
    names = list(index["names"])  # type: ignore[arg-type]
    documents = list(index["documents"])  # type: ignore[arg-type]
    embeddings = index["embeddings"]  # type: ignore[assignment]
    if not names:
        return None

    rank_limit = max(top_k * 4, 12)
    alias = _alias_rank(text, names, documents, rank_limit)
    lexical = _lexical_rank(text, names, documents, rank_limit)
    dense = _dense_rank(text, names, embeddings, rank_limit)
    fused = _rrf_fuse([alias, lexical, dense], max(top_k, 2))
    if not fused:
        return None

    best_name, best_score = fused[0]
    second_score = fused[1][1] if len(fused) > 1 else 0.0
    margin = best_score - second_score
    accepted = best_score >= min_score and margin >= min_margin
    document_by_name = dict(zip(names, documents))
    matched_text = _find_matched_text(text, best_name, document_by_name.get(best_name, ""))
    if accepted and _is_unsafe_partial_dish_match(text, best_name, matched_text):
        accepted = False
    rewritten_query = rewrite_query_with_dish(text, best_name, matched_text)

    dense_debug = ", ".join(f"{name}:{score:.3f}" for name, score in dense[:3])
    lexical_debug = ", ".join(f"{name}:{score:.3f}" for name, score in lexical[:3])
    alias_debug = ", ".join(f"{name}:{score:.3f}" for name, score in alias[:3])
    retrieval_debug = f"alias=[{alias_debug}] lexical=[{lexical_debug}] dense=[{dense_debug}]"

    return RecipeSemanticMatch(
        dish_name=best_name,
        score=best_score,
        margin=margin,
        accepted=accepted,
        candidates=fused[:top_k],
        rewritten_query=rewritten_query,
        matched_text=matched_text,
        retrieval_debug=retrieval_debug,
    )
