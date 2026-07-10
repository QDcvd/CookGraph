"""MiniCookingAgent-Demo 的工具调用解析、执行与 trace 记录。

本模块依赖 agent_tools 获取真实工具注册表和路径提取辅助函数。
禁止导入 agent_adapter_local_LLM_harness。
"""

import asyncio
import ast
import json
import re
from pathlib import Path
from typing import Any

from backend.agent_tools import _extract_paths_from_tool_text, _get_tools

# LLM 意图识别层，用于判断指代追问
from backend.query_understanding import classify_intent

TOOL_NAME_ALIASES = {
    "recipe_query": "recipe_query_tool",
    "recipe": "recipe_query_tool",
    "菜谱查询": "recipe_query_tool",
    "web_search": "web_search_tool",
    "search": "web_search_tool",
    "联网搜索": "web_search_tool",
}


# ---------------------------------------------------------------------------
# _execute_tool_call 内部使用的辅助函数（避免从 harness 循环导入）
# ---------------------------------------------------------------------------

def _message_content_to_text(content: Any) -> str:
    """将 langchain 消息内容标准化为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, str):
                text += block
            elif isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
        return text
    return ""


# ---------------------------------------------------------------------------
# 工具调用名称 / 参数提取
# ---------------------------------------------------------------------------

def _tool_call_name(call: Any) -> str:
    """从工具调用中提取工具名称。"""
    if isinstance(call, dict):
        raw_name = call.get("name") or call.get("function", {}).get("name") or "tool"
    else:
        raw_name = getattr(call, "name", "tool")
    return _normalize_tool_name(str(raw_name))


def _normalize_tool_name(tool_name: str) -> str:
    """Normalize legacy/model-invented tool names to registered tool names."""
    name = str(tool_name or "").strip()
    return TOOL_NAME_ALIASES.get(name, name)


def _tool_call_args(call: Any) -> dict:
    """从工具调用中提取参数字典。"""
    if isinstance(call, dict):
        args = call.get("args") or call.get("arguments") or {}
        return args if isinstance(args, dict) else {"raw": str(args)}
    args = getattr(call, "args", {})
    return args if isinstance(args, dict) else {"raw": str(args)}


def _tool_call_id(call: Any, fallback: str) -> str:
    """从工具调用中提取 ID，缺失时使用 fallback。"""
    if isinstance(call, dict):
        return str(call.get("id") or fallback)
    return str(getattr(call, "id", "") or fallback)


# ---------------------------------------------------------------------------
# 工具参数名解析
# ---------------------------------------------------------------------------

def _tool_arg_names(tool_name: str) -> list[str]:
    """根据工具的实际 args schema 返回参数名列表。"""
    tool_name = _normalize_tool_name(tool_name)
    tool_by_name = {getattr(item, "name", getattr(item, "__name__", "")): item for item in _get_tools()}
    selected = tool_by_name.get(tool_name)
    args = getattr(selected, "args", None)
    if isinstance(args, dict) and args:
        return list(args.keys())
    return {
        "find_tool": ["path", "pattern"],
        "read_file_tool": ["path"],
        "web_search_tool": ["query"],
    }.get(tool_name, [])


# ---------------------------------------------------------------------------
# 工具输入契约
# ---------------------------------------------------------------------------

_QUERY_TOOL_NAMES = {"recipe_query_tool", "web_search_tool"}
_INVALID_QUERY_FRAGMENTS = {
    "",
    "{",
    "}",
    "[",
    "]",
    "(",
    ")",
    "\"",
    "'",
    "null",
    "none",
    "undefined",
}
_AFFIRMATIVE_REPLIES = {"是", "好", "好的", "可以", "行", "嗯", "对", "搜", "搜一下", "帮我搜", "帮我搜一下"}


def _history_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                value = part.get("text") or part.get("content")
                if value:
                    parts.append(str(value))
            elif part:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content or "")


def _pending_query_from_history(history: list[dict] | None) -> str | None:
    """从 runtime memory 或最近 trace 中恢复等待用户确认的原始查询。"""
    for item in reversed(history or []):
        trace = item.get("rag_trace") if isinstance(item, dict) else None
        if isinstance(trace, dict):
            pending_web = trace.get("pending_recipe_web_search")
            if isinstance(pending_web, dict):
                query = str(pending_web.get("original_query") or "").strip()
                if query:
                    return query
            pending = trace.get("pending_clarification")
            if isinstance(pending, dict):
                payload = pending.get("payload")
                if isinstance(payload, dict):
                    query = str(payload.get("query") or payload.get("resolved_query") or payload.get("original_query") or "").strip()
                    if query:
                        return query

        text = _history_text(item)
        match = re.search(r"pending_recipe_web_search.*?original_query['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"original_query['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", text)
        if match:
            return match.group(1).strip()
    return None


def _looks_like_invalid_query(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    query = value.strip()
    if query.lower() in _INVALID_QUERY_FRAGMENTS:
        return True
    if len(query) <= 1:
        return True
    if re.fullmatch(r"[\W_]+", query, flags=re.UNICODE):
        return True
    if query.startswith("{") and not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", query):
        return True
    return False


def _query_source_for_repair(current_user_text: str | None, history: list[dict] | None) -> str | None:
    user_text = str(current_user_text or "").strip()
    if user_text and user_text not in _AFFIRMATIVE_REPLIES:
        return user_text
    pending_query = _pending_query_from_history(history)
    if pending_query:
        return pending_query
    return user_text or None


def _build_recipe_context_from_history(history: list[dict] | None) -> dict | None:
    """从 history 中提取最近一轮对话，以纯文本形式提供给 classify_intent。

    让 LLM 自己判断上下文中的菜名和指代关系，不用正则提取。
    """
    if not history or len(history) < 1:
        return None

    # 取最近一轮 user + assistant 的原文
    last_user: str | None = None
    last_assistant: str | None = None

    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")

        if role == "user" and last_user is None:
            last_user = content
        elif role in ("assistant", "ai") and last_assistant is None:
            last_assistant = content

        if last_user is not None and last_assistant is not None:
            break

    if last_user is None and last_assistant is None:
        return None

    ctx: dict[str, str] = {}
    if last_user:
        ctx["last_query"] = last_user[:200]
    if last_assistant:
        # 取回答开头（包含了菜名信息）即可
        ctx["last_answer_head"] = last_assistant[:300]

    return ctx if ctx else None


def _resolve_followup_via_intent_classifier(query: str, history: list[dict] | None) -> str:
    """通过 LLM 意图分类器判断 query 是否是指代追问，若是则返回补全后的查询。

    不依赖正则提取菜名，直接把最近一轮对话原文喂给 LLM，
    让意图识别层自己判断上下文关联。
    """
    recipe_ctx = _build_recipe_context_from_history(history)
    if not recipe_ctx:
        return query

    intent = classify_intent(query, recipe_context=recipe_ctx)

    if intent.intent == "recipe_followup_query" and intent.resolved_query:
        return intent.resolved_query

    return query
def _normalize_tool_call_args(
    tool_name: str,
    args: dict,
    *,
    current_user_text: str | None = None,
    history: list[dict] | None = None,
) -> tuple[dict, str | None]:
    """Validate and repair model-produced tool arguments before execution.

    The model may produce structurally valid but semantically unusable arguments
    such as {"query": "{"}. Query tools should receive a complete natural
    language request, preferably the latest user utterance or a pending original
    query after an affirmative reply.
    """
    if tool_name not in _QUERY_TOOL_NAMES:
        return args, None

    normalized = dict(args or {})
    raw_query = normalized.get("query")
    repair_source = _query_source_for_repair(current_user_text, history)

    if _looks_like_invalid_query(raw_query):
        if repair_source and not _looks_like_invalid_query(repair_source):
            normalized["query"] = repair_source
            return normalized, None
        return normalized, "工具参数无效：query 必须是完整的自然语言问题，不能是空值、符号片段或 JSON 残片。"

    if str(raw_query).strip() in _AFFIRMATIVE_REPLIES:
        pending_query = _pending_query_from_history(history)
        if pending_query:
            normalized["query"] = pending_query
            return normalized, None
        return normalized, "工具参数无效：用户只是确认/同意，但当前上下文里没有可恢复的原始问题。"

    normalized["query"] = str(raw_query).strip()

    # ── 指代追问：通过 LLM 意图识别层判断 ──
    # 把 query 和 history 中的上下文交给 classify_intent，让 LLM 自己决定
    # 是否需要补全菜名。如果 LLM 认为是指代追问，resolved_query 会包含
    # 补全后的完整查询。
    if tool_name == "recipe_query_tool":
        resolved = _resolve_followup_via_intent_classifier(normalized["query"], history)
        if resolved != normalized["query"]:
            print(f"  [QU 意图识别] query 改写: \"{normalized['query']}\" → \"{resolved}\"", flush=True)
            normalized["query"] = resolved

    return normalized, None


# ---------------------------------------------------------------------------
# 文本式工具调用解析（兼容小模型把工具调用写成普通文本的场景）
# ---------------------------------------------------------------------------

def _literal_ast_value(node: ast.AST) -> Any:
    """安全地计算 AST 字面量节点。"""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _parse_textual_tool_args(tool_name: str, args_text: str) -> dict:
    """将文本形式的参数解析为参数字典。

    支持 JSON 格式、ast 可解析的函数调用格式，以及纯字符串兜底。
    """
    text = args_text.strip()
    if not text:
        return {}

    if text.startswith("{") and text.endswith("}"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        parsed = ast.parse(f"_tool({text})", mode="eval")
    except SyntaxError:
        key = _tool_arg_names(tool_name)[:1] or ["query"]
        return {key[0]: text.strip().strip("'\"")}

    if not isinstance(parsed.body, ast.Call):
        return {}

    arg_names = _tool_arg_names(tool_name)
    args: dict[str, Any] = {}
    for index, node in enumerate(parsed.body.args):
        if index >= len(arg_names):
            break
        value = _literal_ast_value(node)
        if value is not None:
            args[arg_names[index]] = value

    for keyword in parsed.body.keywords:
        if keyword.arg:
            value = _literal_ast_value(keyword.value)
            if value is not None:
                args[keyword.arg] = value

    return args


def _parse_textual_tool_call(raw_text: str) -> dict | None:
    """将文本式工具调用（如 web_search_tool("...")）转换为真实工具调用。

    部分本地 OpenAI 兼容后端暴露了工具 schema，但模型仍然输出函数调用文本，
    此函数将文本视为调用工具的意图。
    """
    text = raw_text.strip()
    if not text:
        return None

    # 去除可能的 Markdown 代码块围栏
    fenced = re.search(r"```(?:python|json|text)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    available = [getattr(item, "name", getattr(item, "__name__", "")) for item in _get_tools()]
    accepted_names = list(dict.fromkeys([*available, *TOOL_NAME_ALIASES.keys()]))
    tool_pattern = "|".join(re.escape(name) for name in accepted_names if name)
    if not tool_pattern:
        return None

    # 尝试 JSON 格式的调用声明
    jsonish = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if jsonish:
        try:
            data = json.loads(jsonish.group(0))
            name = _normalize_tool_name(data.get("name") or data.get("tool_name"))
            args = data.get("args") or data.get("arguments") or {}
            if name in available and isinstance(args, dict):
                return {"name": name, "args": args}
        except Exception:
            pass

    # 尝试 工具名(参数) 格式
    call_match = re.search(
        rf"(?P<name>{tool_pattern})\s*\((?P<args>.*?)\)\s*$",
        text,
        flags=re.DOTALL,
    )
    if not call_match:
        call_match = re.search(
            rf"(?P<name>{tool_pattern})\s*\((?P<args>.*?)\)",
            text,
            flags=re.DOTALL,
    )
    if call_match:
        name = _normalize_tool_name(call_match.group("name"))
        args = _parse_textual_tool_args(name, call_match.group("args"))
        return {"name": name, "args": args}

    # 尝试 ReAct 风格 Action/Action Input 格式
    action_match = re.search(
        rf"(?:Action|工具)\s*[:：]\s*(?P<name>{tool_pattern}).*?(?:Action Input|参数|输入)\s*[:：]\s*(?P<args>.+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if action_match:
        name = _normalize_tool_name(action_match.group("name"))
        args_text = action_match.group("args").strip()
        args = _parse_textual_tool_args(name, args_text)
        return {"name": name, "args": args}

    return None


# ---------------------------------------------------------------------------
# 通用工具路由器的响应解析
# ---------------------------------------------------------------------------

def _parse_missing_tool_router_response(raw_text: str) -> dict | None:
    """解析通用工具路由器返回的 JSON，提取工具名和参数。"""
    text = raw_text.strip()
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
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    tool_name = data.get("tool_name")
    if tool_name is None or str(tool_name).strip().lower() in {"", "null", "none", "no_tool"}:
        return None

    available = {getattr(item, "name", getattr(item, "__name__", "")) for item in _get_tools()}
    tool_name = _normalize_tool_name(str(tool_name).strip())
    if tool_name not in available:
        return None

    args = data.get("args")
    if not isinstance(args, dict):
        args = {}
    return {"name": tool_name, "args": args}


# ---------------------------------------------------------------------------
# Trace 记录
# ---------------------------------------------------------------------------

def _first_regex_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_semantic_candidates(text: str) -> list[dict[str, Any]]:
    candidates_text = _first_regex_group(r"候选：(.+?)(?:；(?:alias|$)|$)", text)
    if not candidates_text:
        return []
    candidates = []
    for name, score in re.findall(r"([^；,，()（）]+)[(（]([0-9.]+)[)）]", candidates_text):
        candidates.append({"name": name.strip(), "score": _parse_float(score)})
    return candidates


def _parse_recipe_hybrid_retrieval(content: str) -> dict[str, Any] | None:
    """Parse the semantic retrieval summary appended by recipe_query_tool."""
    marker = "语义召回摘要："
    if marker not in content:
        return None

    summary = content.split(marker, 1)[1].strip()
    if not summary:
        return None

    accepted = "混合召回改写：" in summary
    skipped = "混合召回跳过：" in summary
    not_rewritten = "混合召回未改写：" in summary
    payload: dict[str, Any] = {
        "strategy": "alias + lexical + dense + rrf",
        "accepted": accepted,
        "skipped": skipped,
        "not_rewritten": not_rewritten,
        "summary": summary[:1000],
        "candidates": _parse_semantic_candidates(summary),
    }

    fields = {
        "original_query": r"原问题=(.+?)；",
        "standard_dish": r"标准菜名=(.+?)；",
        "matched_text": r"命中文本=(.+?)；",
        "rewritten_query": r"改写查询=(.+?)；",
        "top": r"top=(.+?)\s+score=",
        "score": r"score=([0-9.]+)",
        "margin": r"margin=([0-9.]+)",
        "alias_debug": r"alias=\[(.*?)\]\s+lexical=",
        "lexical_debug": r"lexical=\[(.*?)\]\s+dense=",
        "dense_debug": r"dense=\[(.*?)\]\s*$",
    }
    for key, pattern in fields.items():
        value = _first_regex_group(pattern, summary)
        if key in {"score", "margin"}:
            payload[key] = _parse_float(value)
        elif value is not None:
            payload[key] = value

    if accepted and "standard_dish" not in payload and "top" in payload:
        payload["standard_dish"] = payload["top"]

    return payload


def _append_tool_result_to_trace(trace: dict, tool_name: str, args: dict, content: str) -> None:
    """将工具执行结果记录到 trace 中。"""
    trace["tool_used"] = True
    trace["tool_name"] = tool_name
    trace["tool_calls"].append(
        {
            "tool_name": tool_name,
            "args": args,
            "output_preview": content[:800],
        }
    )

    if tool_name == "find_tool":
        path = str(args.get("path", "."))
        pattern = str(args.get("pattern", "*"))
        trace["searched_paths"].append({"path": path, "pattern": pattern})
        for matched_path in _extract_paths_from_tool_text(content):
            if matched_path not in trace["matched_files"]:
                trace["matched_files"].append(matched_path)
                trace["retrieved_chunks"].append(
                    {
                        "filename": matched_path,
                        "text": "通过本地文件搜索匹配。",
                    }
                )
    elif tool_name == "read_file_tool":
        filename = str(args.get("path", ""))
        trace["read_files"].append(filename)
        trace["retrieved_chunks"].append(
            {
                "filename": filename,
                "text": content[:1000],
            }
        )
    elif tool_name == "web_search_tool":
        trace["retrieved_chunks"].append(
            {
                "filename": "web_search_tool",
                "text": content[:1000],
            }
        )
    elif tool_name == "recipe_query_tool":
        hybrid_retrieval = _parse_recipe_hybrid_retrieval(content)
        if hybrid_retrieval:
            trace["hybrid_retrieval"] = hybrid_retrieval
            trace["retrieval_mode"] = "hybrid_recipe_kg"
            trace["retrieval_pipeline"] = "alias + char_ngram_tfidf + gte-large-zh + rrf + knowledge_graph"
            trace["retrieval_top_k"] = len(hybrid_retrieval.get("candidates") or [])
        trace["retrieved_chunks"].append(
            {
                "filename": "recipe_query_tool",
                "text": content[:1000],
            }
        )


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------

async def _execute_tool_call(
    call: Any,
    *,
    current_user_text: str | None = None,
    history: list[dict] | None = None,
) -> tuple[str, dict, str]:
    """执行一次工具调用，返回（工具名、参数、结果文本）。"""
    tool_name = _tool_call_name(call)
    args = _tool_call_args(call)
    args, validation_error = _normalize_tool_call_args(
        tool_name,
        args,
        current_user_text=current_user_text,
        history=history,
    )
    if validation_error:
        return tool_name, args, validation_error

    tool_by_name = {getattr(item, "name", getattr(item, "__name__", "")): item for item in _get_tools()}
    selected = tool_by_name.get(tool_name)
    if selected is None:
        return tool_name, args, f"工具不存在：{tool_name}"

    try:
        result = await asyncio.to_thread(selected.invoke, args)
        return tool_name, args, _message_content_to_text(result) if not isinstance(result, str) else result
    except Exception as e:
        return tool_name, args, f"工具执行失败：{e}"
