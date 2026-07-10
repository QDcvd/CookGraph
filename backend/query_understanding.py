#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Query Understanding 层 — LLM 驱动的意图分类器。

不再依赖硬编码关键词/正则补丁，通过本地 LLM 路由（与 query_plan 相同的
_call_llm_router 模式）对用户查询做结构化意图分类。

在 classify_intent 中注入当前会话的菜谱上下文，使 LLM 能识别指代追问
（如"火力怎么调节"→ 辣椒炒肉的火力），输出 recipe_followup_query 意图。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.llm_endpoint import ensure_llm_endpoint

# ── 数据类型 ──


@dataclass
class QueryIntent:
    """结构化意图。"""

    intent: Literal[
        "forward_recipe_query",        # 已知菜名的正向查询
        "forward_unknown_recipe_query",  # 未知菜名的正向查询
        "reverse_query",               # 反向查询（"哪些菜用了牛肉"）
        "recipe_followup_query",       # 指代追问（"火力怎么调节""他是怎么做的"）
        "non_recipe_query",            # 非菜谱问题
        "ambiguous_query",             # 歧义（需用户确认）
        "greeting",                    # 打招呼/身份询问
    ]
    target_type: str | None = None          # 意图针对的实体类型
    target_text: str | None = None          # 意图针对的实体/菜名
    relation: str | None = None             # 图谱关系类型
    dish_name: str | None = None            # 正向查询的菜名
    attribute: str | None = None            # 正向查询的属性
    confidence: float = 0.0                 # 置信度
    reason: str = ""                        # LLM 给出的理由
    candidates: list[dict] | None = None    # 歧义选项

    # 指代追问专用：解析后的完整查询（LLM 补全后的自包含版本）
    resolved_query: str | None = None


# ── 环境配置 ──

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIAS_PATH = ROOT / "config" / "recipe_aliases.json"

_QUERY_ROUTER_TIMEOUT = float(os.getenv("QUERY_ROUTER_TIMEOUT", "12"))


# ── 工具函数 ──


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── 别名加载（保持，供下游验证用）──

_alias_map_cache: dict[str, list[str]] | None = None


def _load_alias_map() -> dict[str, list[str]]:
    global _alias_map_cache
    if _alias_map_cache is not None:
        return _alias_map_cache
    alias_map: dict[str, list[str]] = {}
    if DEFAULT_ALIAS_PATH.is_file():
        try:
            data = json.loads(DEFAULT_ALIAS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for canonical, aliases in data.items():
                values = [str(item) for item in aliases or [] if item]
                alias_map[str(canonical)] = values
                for alias in values:
                    alias_map.setdefault(alias, []).append(str(canonical))
                for left in [str(canonical), *values]:
                    alias_map.setdefault(left, [])
                    for right in [str(canonical), *values]:
                        if right != left and right not in alias_map[left]:
                            alias_map[left].append(right)
    _alias_map_cache = alias_map
    return alias_map


def _match_dish_name(text: str, dish_names: set[str]) -> str | None:
    for name in sorted(dish_names, key=len, reverse=True):
        if name in text:
            return name
    return None


def _match_alias(text: str, dish_names: set[str]) -> str | None:
    normalized = _normalize_text(text)
    alias_map = _load_alias_map()
    for dish in sorted(dish_names, key=len, reverse=True):
        for variant in sorted(_dish_alias_variants(dish, alias_map), key=len, reverse=True):
            if len(_normalize_text(variant)) >= 2 and _normalize_text(variant) in normalized:
                return dish
    return None


def _dish_alias_variants(canonical: str, alias_map: dict[str, list[str]]) -> set[str]:
    variants = {canonical}
    for _ in range(3):
        before_count = len(variants)
        for value in list(variants):
            for source, targets in alias_map.items():
                if source in value:
                    for target in targets:
                        variants.add(value.replace(source, target))
        if len(variants) == before_count:
            break
    return variants


def _graph_node_names(kg_system: Any, label: str) -> set[str]:
    executor = getattr(kg_system, "executor", None)
    nodes_by_label = getattr(executor, "all_nodes_by_label", None)
    if isinstance(nodes_by_label, dict):
        values = nodes_by_label.get(label)
        if isinstance(values, dict):
            return {str(name) for name in values.keys() if name}
    return set()


# ═══════════════════════════════════════════════
# LLM 路由 — 意图分类
# ═══════════════════════════════════════════════


def _call_llm_router(
    raw: str,
    *,
    dish_names_str: str,
    entity_names_str: str,
    recipe_context_str: str,
) -> dict | None:
    """调用本地 LLM 做意图分类。

    复用 query_plan 相同的 HTTP 调用模式，但用不同的 prompt。
    """
    base_url = ensure_llm_endpoint(os.getenv("LLM_BASE_URL", "http://127.0.0.1:51234/v1")).rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "not-needed")
    model = os.getenv("INTENT_ROUTER_MODEL", os.getenv("LLM_MODEL", "qwen3-4b"))
    no_think = os.getenv("INTENT_ROUTER_NO_THINK", os.getenv("LLM_NO_THINK", "0"))
    timeout = _QUERY_ROUTER_TIMEOUT

    prompt = _build_classifier_prompt(raw, dish_names_str, entity_names_str, recipe_context_str)
    url = f"{base_url}/chat/completions"

    system_msg = (
        "你是菜谱知识图谱查询意图分类器。"
        "只输出 JSON，不要解释，不要用 markdown 包裹。"
        "不能编造图谱实体，只能从用户给出的列表中选择。"
    )

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 512,
            "extra_body": {"no_think": True} if no_think == "1" else {},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        retry_base_url = ensure_llm_endpoint(base_url, force_retry=True).rstrip("/")
        if retry_base_url != base_url:
            url = f"{retry_base_url}/chat/completions"
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as retry_exc:
                print(f"[query_understanding] LLM router unavailable: {retry_exc}", file=__import__("sys").stderr)
                return None
        else:
            print(f"[query_understanding] LLM router unavailable: {exc}", file=__import__("sys").stderr)
            return None

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return _parse_router_json(str(content))


def _build_classifier_prompt(
    raw: str,
    dish_names_str: str,
    entity_names_str: str,
    recipe_context_str: str,
) -> str:
    """构建意图分类 prompt。"""
    context_block = ""
    if recipe_context_str:
        context_block = (
            "\n当前会话菜谱上下文（用户前几轮讨论的内容）：\n"
            f"{recipe_context_str}\n"
        )

    return f"""用户问题：{raw}{context_block}

可用菜名（本地图谱中的标准菜名）：
{dish_names_str or "（无）"}

可用实体（食材、口味、菜系、技法）：
{entity_names_str or "（无）"}

请将用户问题分类为以下 intent 之一：

1. **forward_recipe_query** — 用户明确说出菜名并问做法/配料/火力等属性。
   例："小炒黄牛肉的做法""红烧肉需要什么材料""清蒸鲈鱼怎么做"
   → dish_name 填菜名

2. **forward_unknown_recipe_query** — 用户在问一道菜的做法，但菜名不在本地图谱中，
   或用户没说到具体菜名。
   例："老豆腐的做法""介绍一道家常菜"

3. **reverse_query** — 用户在问"哪些菜用了某食材/口味/菜系/技法"。
   例："哪些菜用了牛肉""有什么川菜推荐""香辣味的菜"
   → target_type 填实体类型，target_text 填实体值

4. **recipe_followup_query** — 用户没提菜名，但用"它""他""这个""火力""做法"等
   指代/省略方式追问当前菜谱上下文中的某道菜。
   **必须检查 recipe_context 中是否有当前讨论的菜品，如果有，在 resolved_query 中
   补全菜名后再传给工具。**
   例：上轮说了"香煎豆腐"，用户说"他是怎么做的呢"
   → resolved_query: "香煎豆腐怎么做"
   例：上轮说了"辣椒炒肉"，用户说"火力怎么调节"
   → resolved_query: "辣椒炒肉的火力怎么调节"
   例：上轮说了"辣椒炒肉"，用户说"具体怎么调火力？"
   → resolved_query: "辣椒炒肉的具体火力调节"
   → 如果 recipe_context 为空或明显不是指代追问，请选 non_recipe_query

5. **non_recipe_query** — 与菜谱完全无关的闲聊。
   例："今天天气怎么样""讲个笑话""1+1等于几""帮我写个邮件"

6. **ambiguous_query** — 用户说的词有多种菜谱含义，无法确定。
   例："蒜蓉"（辅料还是蒜蓉炒技法？）

7. **greeting** — 打招呼、问身份。
   例："你好""你是谁""你能做什么"

输出 JSON schema：
{{
  "intent": "forward_recipe_query|forward_unknown_recipe_query|reverse_query|recipe_followup_query|non_recipe_query|ambiguous_query|greeting",
  "dish_name": "菜名或null",
  "target_type": "ingredient|taste|cuisine|technique|null",
  "target_text": "实体值或null",
  "relation": "USES_MAIN_INGREDIENT|HAS_TASTE|BELONGS_TO_CUISINE|USES_TECHNIQUE|null",
  "resolved_query": "仅 recipe_followup_query 时使用，补全了菜名的完整查询；其他情况填null",
  "confidence": 0.0-1.0,
  "reason": "简短中文理由"
}}"""


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


# ═══════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════


def classify_intent(
    text: str,
    *,
    dish_names: set[str] | None = None,
    kg_system: Any = None,
    recipe_context: dict | None = None,
) -> QueryIntent:
    """对用户查询做意图分类。

    参数：
        text: 用户原始输入。
        dish_names: 本地图谱的标准菜名集合（用于辅助 LLM 识别菜名）。
        kg_system: RecipeQuerySystem 实例（用于提取实体节点名）。
        recipe_context: 当前会话菜谱上下文（用于识别指代追问），
                       格式如 {"current_dish": "香煎豆腐", "current_entity": "豆腐"}。
    """
    raw = str(text or "").strip()
    if not raw:
        return QueryIntent(intent="non_recipe_query", reason="空输入")

    # ── 构建 LLM 上下文 ──

    # 菜名列
    dish_names_str = ""
    if dish_names:
        sorted_dishes = sorted(dish_names, key=len, reverse=True)
        dish_names_str = "、".join(sorted_dishes[:200])

    # 实体列
    entity_names_str = ""
    if kg_system is not None:
        parts = []
        for label, display in [("Ingredient", "食材"), ("Taste", "口味"),
                                ("Cuisine", "菜系"), ("Technique", "技法")]:
            names = _graph_node_names(kg_system, label)
            if names:
                sample = "、".join(sorted(names)[:80])
                parts.append(f"- {display}: {sample}")
        entity_names_str = "\n".join(parts)

    # 会话上下文
    recipe_context_str = ""
    if recipe_context:
        parts = []
        if recipe_context.get("current_dish"):
            parts.append(f"当前菜品：{recipe_context['current_dish']}")
        if recipe_context.get("current_entity"):
            parts.append(f"当前实体：{recipe_context['current_entity']}")
        if recipe_context.get("last_query"):
            parts.append(f"用户上一轮说：{recipe_context['last_query']}")
        if recipe_context.get("last_answer_head"):
            parts.append(f"助手上一轮回答开头：{recipe_context['last_answer_head']}")
        recipe_context_str = "；".join(parts)

    # ── 调用 LLM ──

    result = _call_llm_router(
        raw,
        dish_names_str=dish_names_str,
        entity_names_str=entity_names_str,
        recipe_context_str=recipe_context_str,
    )

    if result is None or not isinstance(result, dict):
        return _fallback_classify(raw, dish_names)

    intent = str(result.get("intent") or "").strip()
    confidence = _safe_float(result.get("confidence"), 0.0)

    # 低置信度走保底
    if confidence < 0.4:
        return _fallback_classify(raw, dish_names)

    resolved_query = result.get("resolved_query") or None

    return QueryIntent(
        intent=intent,  # type: ignore[arg-type]
        dish_name=result.get("dish_name") or None,
        target_type=result.get("target_type") or None,
        target_text=result.get("target_text") or None,
        relation=result.get("relation") or None,
        confidence=confidence,
        reason=str(result.get("reason") or intent),
        resolved_query=resolved_query,
    )


def _fallback_classify(
    raw: str,
    dish_names: set[str] | None,
) -> QueryIntent:
    """LLM 不可用时的最小保底分类。"""
    normalized = _normalize_text(raw)

    # 打招呼
    if raw in {"你好", "您好", "嗨", "hi", "hello", "你是谁", "你是什么模型", "你能做什么"}:
        return QueryIntent(intent="greeting", confidence=0.9, reason="fallback: greeting match")

    # 非菜谱关键词
    non_recipe_keywords = {"天气", "几点", "日期", "股票", "新闻", "电影", "音乐", "python", "代码"}
    recipe_keywords = {"菜", "菜谱", "做法", "怎么做", "烹饪", "配料", "食材", "调料",
                       "火候", "火力", "炒", "蒸", "煮", "炸", "煎", "炖", "烤"}
    if any(kw in normalized for kw in non_recipe_keywords) and not any(kw in normalized for kw in recipe_keywords):
        return QueryIntent(intent="non_recipe_query", confidence=0.7, reason="fallback: non-recipe keyword")

    # 菜名匹配
    if dish_names:
        matched = _match_dish_name(raw, dish_names)
        if matched:
            return QueryIntent(
                intent="forward_recipe_query",
                dish_name=matched,
                confidence=0.8,
                reason=f"fallback: dish name match: {matched}",
            )
        matched_alias = _match_alias(raw, dish_names)
        if matched_alias:
            return QueryIntent(
                intent="forward_recipe_query",
                dish_name=matched_alias,
                confidence=0.75,
                reason=f"fallback: alias match: {matched_alias}",
            )

    # 反向查询标记
    if any(m in raw for m in ["哪些菜", "有哪些菜", "有什么菜"]):
        return QueryIntent(intent="reverse_query", confidence=0.6, reason="fallback: reverse marker")

    # 做法标记 → 未知菜谱
    if any(m in raw for m in ["怎么做", "做法", "如何做"]):
        return QueryIntent(intent="forward_unknown_recipe_query", confidence=0.55, reason="fallback: cooking marker")

    # 兜底
    return QueryIntent(intent="forward_unknown_recipe_query", confidence=0.3, reason="fallback: default")


def format_ambiguous_query(intent: QueryIntent) -> str:
    """将歧义意图格式化为结构化的工具输出。"""
    if not intent.candidates:
        return "我有点不确定你想查哪一种含义，可以再补充一句吗？\n\n结构化摘要：\nsuccess: False\nintent: ambiguous\nweb_fallback_allowed: False"

    lines = [
        "我有点不确定你想查哪一种含义，可以帮我确认一下吗？",
        "",
        "候选解释：",
    ]
    for c in intent.candidates:
        lines.append(f"- 「{c['target_text']}」作为{c['target_type']}")
    lines.extend([
        "",
        "你确认后，我再按对应方向帮你查。",
        "",
        "结构化摘要：",
        "success: False",
        "intent: ambiguous",
        "match_mode: none",
        "web_fallback_allowed: False",
    ])
    return "\n".join(lines)


def format_non_recipe(text: str) -> str:
    """非菜谱问题格式化输出。"""
    return (
        "这个问题看起来不像菜谱查询，我先不查本地菜谱图谱。\n\n"
        "结构化摘要：\n"
        "success: False\n"
        "match_mode: none\n"
        "intent: out_of_scope\n"
        "web_fallback_allowed: False"
    )
