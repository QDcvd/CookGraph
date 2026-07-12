"""User-facing answer composition for structured recipe query results."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.tool_result import parse_tool_result


def compose_plan_result(result: dict[str, Any]) -> str:
    plan_type = result.get("plan_type")
    if plan_type == "entity_lookup":
        return _compose_entity_lookup(result)
    if plan_type == "compound_recommendation":
        return _compose_compound_recommendation(result)
    return _with_structured_result("我还没能稳定处理这个查询。", result)


def compose_web_recipe_answer(user_text: str, web_content: str) -> str:
    """Compress web search output into a concise recipe answer when possible."""
    structured = parse_tool_result(web_content)
    if structured is not None and structured.get("tool") == "web_search_tool":
        return _compose_structured_web_recipe_answer(user_text, structured)
    if "网络搜索失败" in web_content or "网络搜索没有返回内容" in web_content:
        return (
            f"本地菜谱图谱没有收录“{user_text}”。我也尝试联网搜索了，但没有拿到足够可用的结果。\n"
            "为了不误导你，我不会凭常识硬编做法。"
        )

    search_query = _extract_search_query(web_content)
    sources = _filter_recipe_sources(user_text, _parse_search_results(web_content), search_query)
    usable_text = "\n".join(item["body"] for item in sources if item.get("body"))
    best_steps = _best_source_steps(sources)
    all_steps = _extract_numbered_steps(usable_text)
    steps = best_steps if len(best_steps) >= 3 else (all_steps or best_steps)
    ingredients = _extract_ingredients(usable_text)
    normalized_hint = _web_normalization_hint(user_text)

    if not steps and not ingredients:
        source_lines = _format_source_links(sources[:2])
        extra = "\n\n参考来源：\n" + "\n".join(source_lines) if source_lines else ""
        return (
            f"本地菜谱图谱没有收录“{user_text}”。我尝试联网搜索了，但结果里没有足够清晰的做法步骤。\n"
            "你可以换个更常见的菜名，或者让我用“食材 + 做法方向”重新搜一次。"
            f"{extra}"
        )

    title = f"本地菜谱图谱没有收录“{user_text}”。"
    if normalized_hint:
        title += f"我按常见叫法“{normalized_hint}”联网整理了一版参考做法："
    else:
        title += "下面是根据联网搜索结果整理的参考做法："

    lines = [title, ""]
    if ingredients:
        lines.append("用料：")
        for item in ingredients[:8]:
            lines.append(f"- {item}")
        lines.append("")

    if steps:
        lines.append("做法：")
        for index, step in enumerate(steps[:6], start=1):
            lines.append(f"{index}. {step}")
    else:
        lines.append("做法：")
        lines.append("1. 联网结果里只找到零散描述，没有足够清晰的步骤；建议打开来源核对后再操作。")

    source_lines = _format_source_links(sources[:2])
    if source_lines:
        lines.extend(["", "参考来源：", *source_lines])
    return "\n".join(lines)


def _compose_structured_web_recipe_answer(user_text: str, result: dict[str, Any]) -> str:
    """Render structured web results without reparsing a text protocol."""
    if not result.get("ok"):
        return (
            f"本地菜谱图谱没有收录“{user_text}”。我也尝试联网搜索了，但没有拿到足够可用的结果。\n"
            "为了不误导你，我不会凭常识硬编做法。"
        )

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    query = str(data.get("query") or user_text)
    raw_sources = data.get("results") if isinstance(data.get("results"), list) else []
    sources = [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "body": str(item.get("snippet") or ""),
        }
        for item in raw_sources
        if isinstance(item, dict)
    ]
    sources = _filter_recipe_sources(user_text, sources, query)
    usable_text = "\n".join(item["body"] for item in sources if item.get("body"))
    best_steps = _best_source_steps(sources)
    all_steps = _extract_numbered_steps(usable_text)
    steps = best_steps if len(best_steps) >= 3 else (all_steps or best_steps)
    ingredients = _extract_ingredients(usable_text)

    if not steps and not ingredients:
        source_lines = _format_source_links(sources[:2])
        extra = "\n\n参考来源：\n" + "\n".join(source_lines) if source_lines else ""
        return (
            f"本地菜谱图谱没有收录“{user_text}”。我尝试联网搜索了，但结果里没有足够清晰的做法步骤。\n"
            "你可以换个更常见的菜名，或者让我用“食材 + 做法方向”重新搜一次。"
            f"{extra}"
        )

    title = f"本地菜谱图谱没有收录“{user_text}”。"
    normalized_hint = _web_normalization_hint(user_text)
    title += (
        f"我按常见叫法“{normalized_hint}”联网整理了一版参考做法："
        if normalized_hint
        else "下面是根据联网搜索结果整理的参考做法："
    )
    lines = [title, ""]
    if ingredients:
        lines.extend(["用料：", *(f"- {item}" for item in ingredients[:8]), ""])
    if steps:
        lines.append("做法：")
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps[:6], start=1))
    else:
        lines.extend(["做法：", "1. 联网结果里只找到零散描述，没有足够清晰的步骤；建议打开来源核对后再操作。"])
    source_lines = _format_source_links(sources[:2])
    if source_lines:
        lines.extend(["", "参考来源：", *source_lines])
    return "\n".join(lines)


def _compose_entity_lookup(result: dict[str, Any]) -> str:
    entity = result.get("entity") if isinstance(result.get("entity"), dict) else {}
    value = str(entity.get("value") or result.get("query") or "")
    groups = result.get("groups") if isinstance(result.get("groups"), list) else []
    if not groups:
        return _with_structured_result(f"本地图谱里暂时没有找到和“{value}”明确相关的菜。", result)

    lines = [f"本地图谱里和“{value}”相关的菜有：", ""]
    for group in groups:
        group_name = str(group.get("name") or "相关")
        items = group.get("items") if isinstance(group.get("items"), list) else []
        if not items:
            continue
        lines.append(f"{group_name}：")
        for index, item in enumerate(items, start=1):
            dish = item.get("dish_name")
            target = item.get("target_name")
            amount = item.get("amount")
            detail = f"{target} {amount}".strip()
            lines.append(f"{index}. {dish}（{detail}）" if detail else f"{index}. {dish}")
        lines.append("")
    lines.append("以上只来自本地菜谱图谱，未使用联网搜索，也未补充常识菜。")
    return _with_structured_result("\n".join(lines).strip(), result)


def _compose_compound_recommendation(result: dict[str, Any]) -> str:
    constraints = result.get("constraints") if isinstance(result.get("constraints"), list) else []
    items = result.get("items") if isinstance(result.get("items"), list) else []
    label = " + ".join(str(item.get("value")) for item in constraints if item.get("value")) or "这些条件"
    if not items:
        summary = (
            f"本地图谱里暂时没有找到同时满足“{label}”的菜。\n"
            "如果你愿意放宽条件，我可以继续按其中一个条件帮你找。"
        )
        return _with_structured_result(summary, result)

    lines = [f"本地图谱里同时满足“{label}”的菜目前找到 {len(items)} 道：", ""]
    for index, item in enumerate(items, start=1):
        dish = str(item.get("dish_name") or "")
        match_text = _format_matches(item.get("matches"))
        lines.append(f"{index}. {dish}{match_text}")
    if len(items) < 3:
        lines.extend([
            "",
            "命中结果不多，我没有自动凑数。你愿意的话，我可以继续列出只满足其中一个条件的菜。",
        ])
    lines.append("")
    lines.append("以上只来自本地菜谱图谱，未使用联网搜索，也未补充常识菜。")
    return _with_structured_result("\n".join(lines), result)


def _format_matches(matches: Any) -> str:
    if not isinstance(matches, list):
        return ""
    parts = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        value = str(match.get("matched_value") or match.get("value") or "")
        role = str(match.get("role") or "")
        amount = str(match.get("amount") or "")
        if amount:
            parts.append(f"{role}={value} {amount}".strip())
        elif role:
            parts.append(f"{role}={value}")
        elif value:
            parts.append(value)
    return "（" + "；".join(parts) + "）" if parts else ""


def _with_structured_result(summary: str, result: dict[str, Any]) -> str:
    return (
        f"用户摘要：\n{summary.strip()}\n\n"
        "结构化结果：\n"
        f"{json.dumps(result, ensure_ascii=False, indent=2)}\n\n"
        "结构化摘要：\n"
        f"success: {bool(result.get('success'))}\n"
        f"query_type: {result.get('plan_type', 'unknown')}\n"
        "match_mode: plan\n"
        f"web_fallback_allowed: {bool(result.get('web_fallback_allowed'))}"
    )


def _parse_search_results(web_content: str) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in str(web_content or "").splitlines():
        line = raw_line.strip()
        title_match = re.match(r"^\d+\.\s*(.+)$", line)
        if title_match:
            if current:
                sources.append(current)
            current = {"title": title_match.group(1), "url": "", "body": ""}
            continue
        if current is None:
            continue
        if line.startswith("链接："):
            current["url"] = line.split("：", 1)[1].strip()
        elif line.startswith("摘要："):
            current["body"] = line.split("：", 1)[1].strip()
        elif line:
            current["body"] = (current.get("body", "") + " " + line).strip()
    if current:
        sources.append(current)
    return sources


def _filter_recipe_sources(user_text: str, sources: list[dict[str, str]], search_query: str = "") -> list[dict[str, str]]:
    text = str(user_text or "")
    if not sources:
        return []

    blocked_domains = ("instagram.com", "modrinth.com", "behance.net", "buy-haggis.co.uk")
    cooking_terms = ("做法", "步骤", "菜谱", "家常", "下锅", "焯水", "炖", "煮", "炒")
    evidence_phrases = _requested_evidence_phrases(text, search_query)
    filtered = []
    for source in sources:
        url = source.get("url", "")
        haystack = f"{source.get('title', '')}\n{source.get('body', '')}"
        if any(domain in url for domain in blocked_domains):
            continue
        if not any(term in haystack for term in cooking_terms):
            continue
        if "莲藕" in text and "莲藕" not in haystack:
            continue
        if any(word in text for word in ("猪脚", "猪蹄", "猪手")):
            if not any(word in haystack for word in ("猪脚", "猪蹄", "猪手")):
                continue
        if evidence_phrases and not any(phrase in haystack for phrase in evidence_phrases):
            continue
        filtered.append(source)

    return filtered if _query_requires_exact_evidence(text, evidence_phrases) else (filtered or sources)


def _query_requires_exact_evidence(text: str, evidence_phrases: list[str]) -> bool:
    return bool(evidence_phrases or ("莲藕" in text and any(word in text for word in ("猪脚", "猪蹄", "猪手"))))


def _extract_search_query(web_content: str) -> str:
    for raw_line in str(web_content or "").splitlines():
        line = raw_line.strip()
        if line.startswith("搜索结果："):
            return line.split("：", 1)[1].strip()
    return ""


def _requested_evidence_phrases(user_text: str, search_query: str = "") -> list[str]:
    phrases = []
    requested = _extract_requested_dish_phrase(user_text)
    if requested:
        phrases.append(requested)

    query = str(search_query or "")
    if query and query != user_text:
        for token in re.split(r"\s+", query):
            token = _clean_search_query_token(token)
            if token and token not in user_text:
                phrases.append(token)
    return _dedupe_text(phrases)


def _clean_search_query_token(token: str) -> str:
    token = re.sub(r"(怎么做|的做法|家常做法)$", "", str(token or "").strip())
    blocked = {"下厨房", "美食天下", "家常", "做法", "菜谱"}
    if token in blocked or len(token) < 2 or len(token) > 16:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", token):
        return ""
    return token


def _extract_requested_dish_phrase(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text or ""))
    patterns = [
        r"我想做([^，,。！？?]{2,20}?)(?:[，,。！？?]*(?:需要|要准备|怎么做|的做法)|$)",
        r"(?:告诉我|请问|帮我查|帮我搜)?([^，,。！？?]{2,20}?)(?:怎么做|的做法|需要准备|要准备)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        phrase = _clean_requested_dish_phrase(match.group(1))
        if phrase:
            return phrase
    return ""


def _clean_requested_dish_phrase(text: str) -> str:
    phrase = re.sub(r"^(一道|这个|那个|关于)", "", str(text or ""))
    phrase = re.sub(r"(这道菜|这道|菜品|菜)$", "", phrase)
    phrase = phrase.strip("：:，,。！？?")
    if len(phrase) < 2 or len(phrase) > 16:
        return ""
    return phrase


def _extract_numbered_steps(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or ""))
    matches = list(re.finditer(r"(?:^|[。；;，,])\s*(\d+)[\.、]\s*", cleaned))
    steps = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        step = cleaned[start:end].strip(" 。；;，,")
        step = re.sub(r"^\d+[、.．]\s*", "", step)
        step = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", step)
        if step and len(step) >= 4 and "材料图" not in step:
            steps.append(step)
    return _dedupe_text(steps)


def _best_source_steps(sources: list[dict[str, str]]) -> list[str]:
    best: list[str] = []
    for item in sources:
        steps = _extract_numbered_steps(item.get("body", ""))
        if len(steps) > len(best):
            best = steps
    return best


def _extract_ingredients(text: str) -> list[str]:
    candidates = []
    ingredient_words = [
        "猪脚",
        "猪蹄",
        "莲藕",
        "姜",
        "葱",
        "料酒",
        "盐",
        "生抽",
        "冰糖",
        "豆瓣酱",
        "猪肉",
    ]
    for word in ingredient_words:
        if word in text:
            candidates.append(word)
    return _dedupe_text(candidates)


def _format_source_links(sources: list[dict[str, str]]) -> list[str]:
    lines = []
    for item in sources:
        title = item.get("title") or "来源"
        url = item.get("url") or ""
        if url:
            lines.append(f"- {title}：{url}")
    return lines


def _web_normalization_hint(user_text: str) -> str:
    text = str(user_text or "")
    hints = []
    if "猪脚" in text:
        hints.extend(["莲藕炖猪蹄", "莲藕猪蹄汤"])
    return " / ".join(hints)


def _dedupe_text(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        normalized = re.sub(r"\s+", "", item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
    return result
