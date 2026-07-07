"""Tool-loop agent adapter for MiniCookingAgent-Demo.

The stream is deliberately split into two phases:
1. run the tool agent and expose only process events / trace data;
2. generate a clean final answer from the collected tool context.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from backend.agent_tools import _get_tools
from backend.context_manager import (
    context_followup_tool_call,
    history_context_summary,
    recent_context_paths,
)
from backend.token_usage_tracker import TokenUsageTracker
from backend.tool_calling import (
    _append_tool_result_to_trace,
    _execute_tool_call,
    _parse_missing_tool_router_response,
    _parse_textual_tool_call,
    _tool_call_args,
    _tool_call_id,
    _tool_call_name,
)

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


AGENT_TIMEOUT_SECONDS = 900
FINAL_ANSWER_TIMEOUT_SECONDS = 180
DEFAULT_LLM_MODEL = "qwen3-4b"
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 2048)
LLM_NO_THINK = _env_bool("LLM_NO_THINK", True)
MAX_MODEL_LEN = _env_int("MAX_MODEL_LEN", _env_int("LLM_MAX_MODEL_LEN", 32768))
MAX_TOOL_TURNS = _env_int("MAX_TOOL_TURNS", 10)
MAX_TOTAL_TOOL_CALLS = _env_int("MAX_TOTAL_TOOL_CALLS", 16)
MAX_CONSECUTIVE_TOOL_CALLS = _env_int("MAX_CONSECUTIVE_TOOL_CALLS", 5)

FINAL_MARKER_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:\d+\.\s*)?(?:final\s+output\s+generation|final\s+answer|"
    r"final\s+response|final|answer|response|最终回答|最终答案|最终|答案|回答)\s*[:：]\s*",
    flags=re.IGNORECASE,
)
THINKING_MARKER_PATTERN = re.compile(
    r"(?:here(?:'s| is)\s+a\s+thinking\s+process|thinking\s+process|reasoning\s+process|"
    r"analysis\s+process|internal\s+reasoning|analyze\s+user\s+input|check\s+constraints|"
    r"identify\s+key\s+constraints|思考过程|推理过程|分析过程)\s*[:：]",
    flags=re.IGNORECASE,
)
_model = None


def _llm_debug_enabled() -> bool:
    return os.getenv("MINICOOK_LLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on", "debug"}


def _debug_plain_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _debug_plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_debug_plain_value(item) for item in value]
    return value


def _debug_dump_model_return(label: str, value: Any) -> None:
    """调试模式下打印大模型返回的完整对象，便于排查 tool_call 解析问题。"""
    if not _llm_debug_enabled():
        return

    payload = {
        "标签": label,
        "类型": f"{type(value).__module__}.{type(value).__name__}",
        "完整对象": _debug_plain_value(value),
        "repr": repr(value),
    }
    if isinstance(value, (AIMessage, AIMessageChunk)):
        payload["content"] = getattr(value, "content", None)
        payload["tool_calls"] = getattr(value, "tool_calls", None)
        payload["invalid_tool_calls"] = getattr(value, "invalid_tool_calls", None)
        payload["additional_kwargs"] = getattr(value, "additional_kwargs", None)
        payload["response_metadata"] = getattr(value, "response_metadata", None)
        payload["usage_metadata"] = getattr(value, "usage_metadata", None)
        payload["id"] = getattr(value, "id", None)

    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = repr(payload)

    print("\n========== 大模型返回值调试开始 ==========", file=sys.stderr, flush=True)
    print(text, file=sys.stderr, flush=True)
    print("========== 大模型返回值调试结束 ==========\n", file=sys.stderr, flush=True)


def _debug_log(message: str) -> None:
    if _llm_debug_enabled():
        print(f"[llm-debug] {message}", file=sys.stderr, flush=True)



def _build_tool_inventory_prompt(tools: list[Any]) -> str:
    """根据实际注册工具生成中文系统提示词片段。"""
    lines = [
        "可用工具列表：",
        "下面的工具列表来自运行时实际注册的 tools 参数。只能调用列表中存在的工具，不要调用未注册工具。",
    ]
    for item in tools:
        name = getattr(item, "name", getattr(item, "__name__", "tool"))
        description = (getattr(item, "description", None) or getattr(item, "__doc__", "") or "").strip()
        args = getattr(item, "args", None)
        if isinstance(args, dict) and args:
            args_text = ", ".join(args.keys())
        else:
            args_text = "见工具参数结构"
        lines.append(f"- {name}({args_text}): {description}")
    return "\n".join(lines)


def get_model() -> ChatOpenAI:
    """Return the shared chat model."""
    global _model
    if _model is None:
        _model = ChatOpenAI(
            model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
            api_key=os.getenv("LLM_API_KEY", "not-needed"),
            base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:51234/v1"),
            temperature=0,
            max_tokens=LLM_MAX_TOKENS,
        )
    return _model


def _build_tool_loop_system_prompt(tools: list[Any]) -> str:
    return (
        "你是迷你烹饪问答机器人，一个会使用工具的中文助手。你的主要任务是帮助用户查找、阅读和总结烹饪、菜谱、菜单与项目文件相关信息。\n"
        f"{_build_tool_inventory_prompt(tools)}\n\n"
        "工具调用协议：\n"
        "- 运行时已经把上面的工具作为结构化 tools schema 传给模型；需要工具时必须发起正式 tool_call，不要在文字里假装调用。\n"
        "- 当前使用 /no_think 模式，请不要输出隐藏推理过程，把 token 留给工具调用和最终答案。\n"
        "- 如果用户明确点名某个工具，就调用该工具。\n"
        "- 工具名必须使用精确注册名：recipe_query_tool 或 web_search_tool；不要输出 recipe_query、recipe、search 等不存在的别名。\n"
        "- 如果用户的输入像是在询问某道菜、某个菜式、想吃某道菜，或询问菜谱、菜品做法、怎么做、备菜过程、烹饪过程、火力调节、食材、调料、技法、口味、菜系，或问“哪些菜用了某食材/技法”，必须先调用 recipe_query_tool；不要直接凭常识回答，也不要先调用 web_search_tool。\n"
        "- 如果用户只是打招呼、问天气、问模型身份，或其他明显非菜谱问题，不要调用 recipe_query_tool。\n"
        "- 例如“辣椒炒肉怎么做”“我想吃清蒸鲈鱼”“西红柿炒鸡蛋的配料”“小炒黄牛肉火候怎么控制”都必须调用 recipe_query_tool。\n"
        "- recipe_query_tool 返回的是本地菜谱知识图谱结果；最终回答必须优先依据该工具结果。如果工具未找到，再说明本地图谱未命中，不要编造做法。\n"
        "- 当 recipe_query_tool 返回 web_fallback_allowed: True 且本地图谱未命中时，系统可能自动补充 web_search_tool；最终回答要区分“本地图谱结果”和“联网补充资料”。\n"
        "- 只有当用户明确要求网页搜索、网络搜索、联网查询、在线查找、最新信息，或本地菜谱知识图谱未命中且需要公共网页补充时，才调用 web_search_tool。\n"
        "- find_tool 和 read_file_tool 当前未注册，不能调用；如果用户要求查找或读取本地文件，请说明当前暂未启用本地文件工具。\n"
        "- 工具返回结果后，必须以最新工具结果作为最高优先级证据；如果它与历史回答或你的先验冲突，明确纠正旧说法，再给最终答案。\n"
        f"- 当前模型按 {MAX_MODEL_LEN} tokens 上下文预算运行，最终回答输出上限为 {LLM_MAX_TOKENS} tokens；不要为了微小增益反复调用工具。\n"
        f"- 本轮最多执行 {MAX_TOTAL_TOOL_CALLS} 次工具调用、最多 {MAX_TOOL_TURNS} 个模型工具回合；达到上限时必须基于已有信息总结。\n"
        f"- 同一个工具最多只能连续调用 {MAX_CONSECUTIVE_TOOL_CALLS} 次；如果连续达到上限，也要基于已经掌握的信息给出阶段性回答，不要无限重试。\n"
        "- 只要可用工具能够满足用户请求，就不要声称自己无法使用工具。"
    )


def _recipe_query_needs_web_fallback(content: str) -> bool:
    """判断菜谱图谱结果是否明确未命中，需要联网兜底。"""
    text = str(content or "")
    if not text.strip():
        return True
    if "web_fallback_allowed: False" in text:
        return False
    if "web_fallback_allowed: True" in text and "success: False" in text:
        return True
    if "为您找到相似" in text:
        return False
    if "success: False" in text:
        return False
    miss_markers = [
        "未找到菜品",
        "未找到使用",
        "未找到\"",
        "未找到“",
        "无法理解的查询格式",
        "无数据或未记录",
        "本地图谱未命中",
    ]
    return any(marker in text for marker in miss_markers)


async def _execute_web_fallback_after_recipe(
    user_text: str,
    messages: list[Any],
    trace: dict,
    tool_call_id: str,
):
    """菜谱图谱未命中时，自动补一次公网搜索。"""
    fallback_call = {"name": "web_search_tool", "args": {"query": user_text}, "id": tool_call_id}
    messages.append(AIMessage(content="", tool_calls=[fallback_call]))
    executed_name, executed_args, content = await _execute_tool_call(fallback_call)
    _append_tool_result_to_trace(trace, executed_name, executed_args, content)
    messages.append(ToolMessage(content=content, tool_call_id=tool_call_id, name=executed_name))
    return executed_name, executed_args, content


async def _execute_forced_tool_call(
    user_text: str,
    messages: list[Any],
    trace: dict,
    tool_context: list[dict],
    tool_name: str,
    args: dict,
    call_id: str,
) -> int:
    """Execute a tool call forced by deterministic routing. Returns call count."""
    call = {"name": tool_name, "args": args, "id": call_id}
    messages.append(AIMessage(content="", tool_calls=[call]))
    executed_name, executed_args, content = await _execute_tool_call(call)
    _append_tool_result_to_trace(trace, executed_name, executed_args, content)
    tool_context.append({"tool_name": executed_name, "args": executed_args, "content": content})
    messages.append(ToolMessage(content=content, tool_call_id=call_id, name=executed_name))

    calls_used = 1
    if executed_name == "recipe_query_tool" and _recipe_query_needs_web_fallback(content):
        web_name, web_args, web_content = await _execute_web_fallback_after_recipe(
            user_text,
            messages,
            trace,
            f"{call_id}_web_fallback",
        )
        tool_context.append({"tool_name": web_name, "args": web_args, "content": web_content})
        calls_used += 1
    return calls_used


async def _emit_final_answer_from_tool_context(
    user_text: str,
    trace: dict,
    tool_context: list[dict],
    runtime_memory: str = "",
    token_tracker: TokenUsageTracker | None = None,
):
    """工具执行完成后，用不绑定 tools 的普通模型整理最终回答。"""
    yield {"type": "trace", "rag_trace": trace}
    yield {"type": "rag_step", "step": {"label": "正在整理最终回答...", "icon": "✍️"}}
    grounded_recipe_answer = _build_grounded_recipe_answer(user_text, tool_context)
    if grounded_recipe_answer:
        yield {"type": "content", "content": grounded_recipe_answer}
        if token_tracker is not None:
            trace["token_usage"] = token_tracker.snapshot(final=True)
            yield {"type": "token_usage", "token_usage": trace["token_usage"]}
        return

    try:
        async with asyncio.timeout(FINAL_ANSWER_TIMEOUT_SECONDS):
            async for event in _stream_model_answer(
                _build_final_prompt(user_text, trace, tool_context, runtime_memory),
                token_tracker=token_tracker,
            ):
                yield event
    except asyncio.TimeoutError:
        reason = f"最终回答生成超时：超过 {FINAL_ANSWER_TIMEOUT_SECONDS}s；我先基于工具结果给出摘要。"
        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        reason = f"最终回答生成失败：{type(e).__name__}: {e}；我先基于工具结果给出摘要。"
        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}


def get_tool_bound_model():
    """返回绑定了当前工具 schema 的模型。"""
    return get_model().bind_tools(_get_tools())


def _message_content_to_text(content: Any) -> str:
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


def _with_no_think(text: str) -> str:
    if not LLM_NO_THINK:
        return text
    stripped = text.lstrip()
    if stripped.startswith("/no_think"):
        return text
    return f"/no_think\n{text}"


def _message_reasoning_to_text(chunk: AIMessageChunk) -> str:
    reasoning = getattr(chunk, "reasoning_content", None)
    if isinstance(reasoning, str):
        return reasoning

    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning, str):
        return reasoning

    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict):
                value = block.get("reasoning_content") or block.get("reasoning")
                if isinstance(value, str):
                    text += value
        return text
    return ""


def _pick_final_answer(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    chinese_lines = [line for line in lines if re.search(r"[\u4e00-\u9fff]", line)]
    if chinese_lines:
        return chinese_lines[-1].strip("“”\"' ")
    return text.strip()


def _split_local_llm_output(text: str) -> tuple[str, str]:
    """Return (thinking, answer) while keeping Qwen analysis out of final content."""
    if not text:
        return "", ""

    thinking_parts = [
        block.strip()
        for block in re.findall(r"<think>(.*?)</think>", text, flags=re.IGNORECASE | re.DOTALL)
        if block.strip()
    ]
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()

    final_matches = list(FINAL_MARKER_PATTERN.finditer(cleaned))
    if final_matches:
        marker = final_matches[-1]
        prefix = cleaned[:marker.start()].strip()
        answer_source = cleaned[marker.end():].strip()
        if prefix:
            thinking_parts.append(prefix)
        answer = _pick_final_answer(answer_source)
        trailing_thinking = answer_source[: answer_source.rfind(answer)].strip() if answer else ""
        if trailing_thinking:
            thinking_parts.append(trailing_thinking)
        return "\n\n".join(thinking_parts).strip(), answer

    if THINKING_MARKER_PATTERN.search(cleaned):
        answer = _pick_final_answer(cleaned)
        thinking = cleaned[: cleaned.rfind(answer)].strip() if answer else cleaned
        if thinking:
            thinking_parts.append(thinking)
        return "\n\n".join(thinking_parts).strip(), answer

    return "\n\n".join(thinking_parts).strip(), cleaned



def _looks_like_context_followup(user_text: str) -> bool:
    """Deprecated: context routing now lives in backend.context_manager."""
    from backend.context_manager import looks_like_context_followup

    return looks_like_context_followup(user_text)


def _recent_context_paths(history: list[dict]) -> list[str]:
    """Deprecated: use backend.context_manager.recent_context_paths."""
    return recent_context_paths(history)


def _history_context_summary(history: list[dict]) -> str:
    """Deprecated: use backend.context_manager.history_context_summary."""
    return history_context_summary(history)


def _build_missing_tool_router_prompt(user_text: str, history: list[dict]) -> list[Any]:
    tools = _get_tools()
    tool_lines = []
    for item in tools:
        name = getattr(item, "name", getattr(item, "__name__", "tool"))
        description = (getattr(item, "description", None) or getattr(item, "__doc__", "") or "").strip()
        args = getattr(item, "args", None)
        args_text = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else "{}"
        tool_lines.append(f"- {name}: {description}\n  参数结构：{args_text}")

    context_summary = history_context_summary(history) or "无结构化历史上下文。"
    system = (
        "你是迷你烹饪问答机器人的工具路由器，只负责判断是否需要补发一次工具调用。\n"
        "背景：主模型刚才没有返回正式 tool_call。你要根据用户问题、历史上下文和可用工具，判断是否应该调用一个工具。\n"
        "只允许从可用工具列表里选择一个工具；如果不需要工具或信息不足，返回 tool_name 为 null。\n"
        "不要写解释，不要输出 Markdown，只输出 JSON。"
    )
    user = (
        f"可用工具列表：\n{chr(10).join(tool_lines)}\n\n"
        f"历史上下文：\n{context_summary}\n\n"
        f"用户问题：\n{user_text}\n\n"
        "返回格式：\n"
        '{"tool_name": "工具名或null", "args": {"参数名": "参数值"}}\n\n'
        "路由原则：\n"
        "1. 如果用户询问菜谱、菜品做法、备菜过程、烹饪过程、火力调节、食材、调料、技法、口味、菜系，选择 recipe_query_tool。\n"
        "2. 如果用户明确需要外部、在线、最新或公共网页信息，选择 web_search_tool。\n"
        "3. 如果用户要查找本地路径、读取文件、查看项目或 README，返回 null，因为本地文件工具当前未注册。\n"
        "4. 如果用户只是普通聊天、打招呼、问天气、问模型身份、写作或基于已有上下文已经能回答，返回 null。\n"
        "5. 参数必须尽量具体，不要用空对象敷衍。"
    )
    return [SystemMessage(content=system), HumanMessage(content=_with_no_think(user))]



async def _route_missing_tool_call(user_text: str, history: list[dict]) -> dict | None:
    context_call = context_followup_tool_call(user_text, history)
    if context_call is not None:
        available = {getattr(item, "name", getattr(item, "__name__", "")) for item in _get_tools()}
        if context_call.get("name") in available:
            return context_call

    try:
        async with asyncio.timeout(20):
            _debug_log("主模型未返回 tool_call，正在请求通用工具路由器")
            result = await get_model().ainvoke(_build_missing_tool_router_prompt(user_text, history))
            _debug_dump_model_return("通用工具路由器返回", result)
    except Exception as e:
        _debug_log(f"通用工具路由器失败：{e}")
        return None

    raw = _message_reasoning_to_text(result) + _message_content_to_text(getattr(result, "content", ""))
    return _parse_missing_tool_router_response(raw)



def _build_partial_tool_answer(user_text: str, trace: dict, stop_reason: str | None = None) -> str:
    calls = trace.get("tool_calls", [])
    useful_chunks = []
    for item in trace.get("retrieved_chunks", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if text.startswith("工具执行失败") or text.startswith("网络搜索失败") or text.startswith("网络搜索没有返回内容"):
            continue
        if (
            str(item.get("filename", "")) == "recipe_query_tool"
            and ("success: False" in text or "未找到菜品" in text or "无法理解的查询格式" in text)
        ):
            continue
        useful_chunks.append((str(item.get("filename", "工具结果")), text))

    conclusion = _build_tool_fallback_conclusion(user_text, trace, useful_chunks, calls)
    reason = stop_reason or "工具循环已停止。"
    lines = [
        conclusion,
        "",
        f"说明：{reason}下面是我基于已拿到的工具结果整理出的依据。",
        "",
        f"用户问题：{user_text}",
        "",
    ]
    if useful_chunks:
        lines.append("主要依据：")
        for index, (source, text) in enumerate(useful_chunks[:5], start=1):
            preview = text[:500].strip()
            lines.append(f"{index}. 来源：{source}\n{preview}")
    else:
        lines.append("目前没有拿到可靠的工具结果，所以只能给出有限结论。")

    if calls:
        lines.append("")
        lines.append("已尝试的工具调用：")
        for index, call in enumerate(calls[-5:], start=1):
            tool_name = call.get("tool_name", "tool")
            args = call.get("args", {})
            preview = str(call.get("output_preview", "")).strip()
            if preview:
                preview = preview[:220]
            else:
                preview = "无返回内容"
            lines.append(f"{index}. {tool_name} 参数={args}，结果摘要：{preview}")

    lines.append("")
    lines.append(_build_tool_fallback_next_step(trace, useful_chunks))
    return "\n".join(lines)


def _build_grounded_recipe_answer(user_text: str, tool_context: list[dict]) -> str:
    """Return a deterministic answer when recipe_query_tool has enough structured evidence.

    This prevents the final LLM pass from adding common-cooking details that were not
    present in the local recipe graph, such as changing exact times or inventing
    seasonings.
    """
    recipe_item = None
    for item in reversed(tool_context):
        if item.get("tool_name") == "recipe_query_tool":
            content = str(item.get("content") or "")
            if "success: True" in content and "cooking_method_desc:" in content:
                recipe_item = item
                break
    if recipe_item is None:
        return ""

    content = str(recipe_item.get("content") or "")
    dish = _extract_recipe_dish_name(content) or _extract_query_dish_name(user_text) or "这道菜"
    method = _extract_recipe_field(content, "cooking_method_desc")
    tips = _extract_recipe_field(content, "cooking_tips")
    fire = _extract_recipe_field(content, "fire_control_process")
    ingredients = _extract_bullet_section(content, "主要食材")
    sides = _extract_bullet_section(content, "配料")
    seasonings = _extract_bullet_section(content, "调味品")

    if not method:
        return ""

    lines = [f"根据本地菜谱图谱，{dish}可以这样做：", ""]

    if ingredients or sides or seasonings:
        lines.append("用料：")
        for label, values in (("主要食材", ingredients), ("配料", sides), ("调味品", seasonings)):
            if values:
                lines.append(f"- {label}：" + "、".join(values))
        lines.append("")

    steps = _split_numbered_recipe_steps(method)
    lines.append("做法：")
    if steps:
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step}")
    else:
        lines.append(method)

    fire_points = _split_semicolon_items(fire)
    if fire_points:
        lines.append("")
        lines.append("火力和时间：")
        for item in fire_points[:6]:
            lines.append(f"- {item}")

    if tips:
        lines.append("")
        lines.append(f"要点：{tips}")

    lines.append("")
    lines.append("以上内容来自本地菜谱知识图谱。")
    return "\n".join(lines)


def _extract_recipe_dish_name(content: str) -> str:
    match = re.search(r"【([^】\n]+?)\s+完整档案】", content)
    if match:
        return match.group(1).strip()
    match = re.search(r"为您找到相似菜品[：:][\"“]?([^\"”\n]+)", content)
    if match:
        return match.group(1).strip()
    return ""


def _extract_query_dish_name(user_text: str) -> str:
    text = str(user_text or "").strip()
    text = re.sub(r"^(我想吃|想吃|我要吃|帮我做|请问)", "", text)
    text = re.sub(r"(怎么做|的做法|做法|蒸多久|火候.*|配料.*)$", "", text)
    return text.strip(" ？?。！!")


def _extract_recipe_field(content: str, field: str) -> str:
    pattern = rf"(?m)^{re.escape(field)}:\s*(.*)$"
    match = re.search(pattern, content)
    return match.group(1).strip() if match else ""


def _extract_bullet_section(content: str, heading: str) -> list[str]:
    pattern = rf"{re.escape(heading)}：\s*\n(?P<body>(?:\s*• .+\n?)+)"
    match = re.search(pattern, content)
    if not match:
        return []
    values = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if line.startswith("•"):
            value = line.lstrip("•").strip()
            if value:
                values.append(value)
    return values


def _split_numbered_recipe_steps(method: str) -> list[str]:
    text = str(method or "").strip()
    if not text:
        return []
    matches = list(re.finditer(r"(?:^|；|;)\s*(\d+)[.．、]\s*", text))
    if not matches:
        return [item for item in _split_semicolon_items(text) if item]

    steps: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        step = text[start:end].strip("；; ，,")
        if step:
            steps.append(step)
    return steps


def _split_semicolon_items(text: str) -> list[str]:
    return [item.strip(" ；;") for item in re.split(r"[；;]\s*", str(text or "")) if item.strip(" ；;")]


def _build_tool_fallback_conclusion(
    user_text: str,
    trace: dict,
    useful_chunks: list[tuple[str, str]],
    calls: list[dict],
) -> str:
    """Build a user-facing conclusion when the model stops after tool use without final content."""
    matched_files = [str(item) for item in trace.get("matched_files", []) if str(item).strip()]
    read_files = [str(item) for item in trace.get("read_files", []) if str(item).strip()]
    tool_names = [str(call.get("tool_name", "")) for call in calls]
    lowered_question = user_text.lower()

    if matched_files or read_files:
        project_hint_files = matched_files + read_files
        blog_indicators = [
            path
            for path in project_hint_files
            if any(
                token in Path(path).name.lower()
                for token in ["blog", "post", "article", "db.json", "deploy_config", "favicon"]
            )
        ]
        config_indicators = [
            path
            for path in project_hint_files
            if Path(path).name.lower() in {"package.json", "vite.config.js", "vue.config.js", "next.config.js", "nuxt.config.js"}
        ]

        if config_indicators:
            examples = "、".join(Path(path).name for path in config_indicators[:4])
            return f"结论：工具已经找到项目配置线索，例如 {examples}；可以据此继续读取文件来判断项目结构和运行方式。"

        examples = "、".join(Path(path).name for path in project_hint_files[:5])
        return f"结论：工具已经在目标目录中找到文件线索，例如 {examples}；目前能确认目录可访问，并且下一步应读取关键文件来解释项目。"

    if "web_search_tool" in tool_names:
        search_text = "\n".join(text for _, text in useful_chunks)
        titles = []
        for line in search_text.splitlines():
            stripped = line.strip()
            if re.match(r"^\d+\.\s+", stripped):
                titles.append(re.sub(r"^\d+\.\s+", "", stripped))
        if titles:
            examples = "；".join(titles[:3])
            return f"结论：网络搜索已经返回了一些可用结果，优先相关的结果包括：{examples}。下面给出依据摘要。"
        return "结论：已经尝试网络搜索，但结果不足以形成可靠答案；下面保留已拿到的信息和下一步建议。"

    if useful_chunks:
        return "结论：工具已经返回了部分有效信息，但模型没有完成最终整合；我先基于这些信息给出阶段性总结。"

    return "结论：这轮工具调用没有拿到足够有效的信息，因此暂时不能给出可靠判断。"


def _build_tool_fallback_next_step(trace: dict, useful_chunks: list[tuple[str, str]]) -> str:
    matched_files = [str(item) for item in trace.get("matched_files", []) if str(item).strip()]
    if matched_files:
        likely_files = [
            path
            for path in matched_files
            if Path(path).name.lower() in {"readme.md", "package.json", "bloginfolist.json", "db.json", "deploy_config.json"}
        ]
        if likely_files:
            return "下一步：建议继续读取这些关键文件：" + "、".join(likely_files[:5])
        return "下一步：建议读取 README、package.json、配置文件或数据文件，才能把“找到了什么”进一步解释成“这个项目怎么工作”。"

    if not useful_chunks:
        return "下一步：需要换一个更明确的路径、关键词，或检查工具/网络是否可用。"

    return "下一步：如果需要更完整的答案，可以换一个更具体的菜名，或打开联网搜索结果来源核对。"


def _build_tool_loop_messages(user_text: str, history: list[dict]) -> list[Any]:
    tools = _get_tools()
    system_prompt = _build_tool_loop_system_prompt(tools)
    context_summary = _history_context_summary(history)
    if context_summary:
        system_prompt += "\n\n" + context_summary
    messages: list[Any] = [SystemMessage(content=system_prompt)]
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            messages.append(("user", content))
        elif role == "assistant":
            messages.append(("ai", content))
        elif role == "assistant_tool_call":
            tool_name = str(msg.get("tool_name") or "tool")
            tool_args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
            tool_call_id = str(msg.get("tool_call_id") or f"history_{len(messages)}")
            messages.append(AIMessage(
                content=str(content or ""),
                tool_calls=[{"name": tool_name, "args": tool_args, "id": tool_call_id}],
            ))
        elif role == "tool":
            tool_name = str(msg.get("tool_name") or "tool")
            tool_call_id = str(msg.get("tool_call_id") or f"history_{len(messages)}")
            messages.append(ToolMessage(content=str(content or ""), tool_call_id=tool_call_id, name=tool_name))
        elif role == "context":
            messages.append(("user", f"<历史工具上下文>\n{content}\n</历史工具上下文>"))
        elif role == "runtime_memory":
            messages.append(("user", str(content or "")))
    messages.append(("user", _with_no_think(user_text)))
    return messages



def _build_final_prompt(user_text: str, trace: dict, tool_context: list[dict], runtime_memory: str = "") -> list[Any]:
    context_lines = []
    for index, item in enumerate(tool_context, start=1):
        context_lines.append(
            f"[{index}] 工具={item['tool_name']} 参数={item['args']}\n{item['content']}"
        )
    context = "\n\n".join(context_lines) or "没有收集到工具上下文。"

    system = (
        "你是迷你烹饪问答机器人，一个简洁的中文助手。请只输出给用户看的最终回答。"
        "不要复述内部搜索步骤、工具调用过程、失败模式或原始日志。"
        "如果找到了菜谱图谱或网页信息，只能根据已收集的工具上下文回答。"
        "不得新增工具上下文中没有出现的步骤、食材、调料、用量、时间、火力档位或温度。"
        "不得把工具给出的精确时间改写成更宽泛的范围；例如工具写8分钟，就不要写8-10分钟。"
        "如果工具上下文给出了 cooking_method_desc、fire_control_process、配料或调味品，必须优先保留这些字段里的事实。"
        "如果最新工具上下文与历史回答或先验冲突，必须以最新工具上下文为准并明确纠正旧说法。"
        "如果上下文不足，请简短说明还缺少什么。"
    )
    user = (
        f"用户问题：\n{user_text}\n\n"
        f"运行时记忆：\n{runtime_memory or '无'}\n\n"
        f"已收集的工具上下文：\n{context}\n\n"
        f"检索摘要：\n"
        f"- 已用工具：{[call.get('tool_name') for call in trace.get('tool_calls', [])]}\n"
        "请用中文写出干净、直接的最终回答。回答前自检：每一个步骤、时间、用量、调料都必须能在工具上下文中找到依据。"
    )
    return [SystemMessage(content=system), HumanMessage(content=_with_no_think(user))]


def _runtime_memory_from_history(history: list[dict]) -> str:
    parts = [
        str(item.get("content") or "").strip()
        for item in history
        if item.get("role") == "runtime_memory" and str(item.get("content") or "").strip()
    ]
    return "\n\n".join(parts)


def _build_route_prompt(user_text: str) -> list[Any]:
    return [
        SystemMessage(
            content=(
        "你是请求路由器，只能输出一个中文词：工具 或 直接回答。\n"
        "可用工具名单：recipe_query_tool, web_search_tool。\n"
        "输出 工具：用户要求菜谱知识图谱查询、菜品做法、怎么做、想吃某道菜、备菜过程、烹饪过程、火力调节、食材、调料、技法、口味、菜系、联网搜索、网络搜索、搜索一下、最新信息，或明确提到已注册工具名。\n"
        "输出 直接回答：普通闲聊、身份问题、解释概念、写作润色、本地文件查找/读取请求，且不需要已注册工具。菜谱怎么做不是直接回答。\n"
        "示例：西红柿炒鸡蛋怎么做 => 工具\n"
        "示例：告诉我，凉拌牛肉怎么做 => 工具\n"
        "示例：我想吃清蒸鲈鱼 => 工具\n"
        "示例：网络搜索粤菜清蒸鱼做法 => 工具\n"
                "示例：查找 README => 直接回答\n"
                "示例：你是什么模型 => 直接回答\n"
                "不要解释，不要输出标点。"
            )
        ),
        HumanMessage(content=_with_no_think(f"用户请求：\n{user_text}\n\n路由结果：")),
    ]


def _looks_like_tool_request(user_text: str) -> bool:
    """用确定性规则兜住明确的工具意图，避免中文模型在路由阶段误判。"""
    text = user_text.lower()
    explicit_tool_names = ["web_search_tool", "recipe_query_tool"]
    if any(name in text for name in explicit_tool_names):
        return True

    tool_keywords = [
        "搜索",
        "搜一下",
        "查询",
        "联网",
        "网络搜索",
        "网页搜索",
        "最新",
        "怎么做",
        "如何做",
        "咋做",
        "怎样做",
        "想吃",
        "我要吃",
        "菜谱",
        "菜品",
        "做法",
        "备菜",
        "烹饪",
        "火力",
        "食材",
        "调料",
        "技法",
        "口味",
        "菜系",
    ]
    return any(keyword in text for keyword in tool_keywords)


async def _route_query(user_text: str) -> str:
    if _looks_like_tool_request(user_text):
        return "tools"

    try:
        async with asyncio.timeout(ROUTE_TIMEOUT_SECONDS):
            _debug_log("即将请求路由模型")
            result = await get_model().ainvoke(_build_route_prompt(user_text))
            _debug_dump_model_return("路由模型返回", result)
    except Exception:
        return "direct_chat"

    raw = _message_reasoning_to_text(result) + _message_content_to_text(result.content)
    answer = _split_local_llm_output(raw)[1].lower()
    if "工具" in answer or re.search(r"\btools\b", answer):
        return "tools"
    return "direct_chat"


def _build_direct_chat_prompt(user_text: str, history: list[dict]) -> list[Any]:
    messages: list[Any] = [
        SystemMessage(
            content=(
                "你是迷你烹饪问答机器人，一个简洁的中文助手。请直接回答用户。"
                "不要提及工具调用、内部分析或隐藏推理。"
                "如果用户在问某道菜怎么做、想吃某道菜、菜谱、配料、火候、烹饪步骤或食材做法，"
                "你不能凭常识编菜谱；只能回复：这个问题需要调用菜谱工具，请重新发送或稍后再试。"
            )
        )
    ]
    for msg in history:
        if msg.get("role") == "runtime_memory" and msg.get("content"):
            messages.append(("user", str(msg.get("content"))))
    for msg in history[-6:]:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            messages.append(("user", content))
        elif role == "assistant":
            messages.append(("ai", content))
    messages.append(HumanMessage(content=_with_no_think(user_text)))
    return messages


def _token_usage_event(token_tracker: TokenUsageTracker, *, final: bool = False) -> dict:
    return {"type": "token_usage", "token_usage": token_tracker.snapshot(final=final)}


async def _stream_model_answer(messages: list[Any], token_tracker: TokenUsageTracker | None = None):
    raw_output = ""
    _debug_log(f"即将开始流式请求大模型，消息数量={len(messages)}")
    async for chunk in get_model().astream(messages):
        _debug_dump_model_return("流式模型返回片段", chunk)
        if not isinstance(chunk, AIMessageChunk):
            continue

        chunk_text = _message_reasoning_to_text(chunk) + _message_content_to_text(chunk.content)
        raw_output += chunk_text
        if token_tracker is not None:
            token_tracker.add_generated_text(chunk_text)
            token_tracker.add_model_usage(chunk)
            yield _token_usage_event(token_tracker)

    thinking, answer = _split_local_llm_output(raw_output)
    if thinking:
        yield {"type": "thinking", "content": thinking}
    if answer:
        yield {"type": "content", "content": answer}
    if token_tracker is not None:
        yield _token_usage_event(token_tracker, final=True)


async def stream_search_agent(user_text: str, history: list[dict]):
    """Zleap 风格工具循环：模型每轮都拿到 tools schema，自行决定是否 tool_call。"""
    trace = {
        "tool_used": False,
        "tool_name": None,
        "tool_calls": [],
        "searched_paths": [],
        "matched_files": [],
        "read_files": [],
        "retrieved_chunks": [],
        "retrieval_stage": "tool_call_harness",
        "retrieval_mode": "native_tool_loop",
        "model_name": os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        "max_model_len": MAX_MODEL_LEN,
        "max_output_tokens": LLM_MAX_TOKENS,
        "max_tool_turns": MAX_TOOL_TURNS,
        "max_total_tool_calls": MAX_TOTAL_TOOL_CALLS,
        "max_consecutive_tool_calls": MAX_CONSECUTIVE_TOOL_CALLS,
    }
    messages = _build_tool_loop_messages(user_text, history)
    token_tracker = TokenUsageTracker()
    runtime_memory = _runtime_memory_from_history(history)
    model = get_tool_bound_model()
    tool_context: list[dict] = []
    continue_nudges = 0
    last_tool_name = None
    consecutive_tool_calls = 0
    total_tool_calls = 0

    yield {"type": "rag_step", "step": {"label": "正在装载工具上下文...", "icon": "🧰"}}

    try:
        async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
            for turn_index in range(MAX_TOOL_TURNS):
                yield {
                    "type": "rag_step",
                    "step": {
                        "label": f"模型回合 {turn_index + 1}",
                        "icon": "🧠",
                        "detail": "已向模型提供结构化工具列表。",
                    },
                }
                _debug_log(f"即将请求工具循环模型，第{turn_index + 1}轮，消息数量={len(messages)}")
                response = await model.ainvoke(messages)
                _debug_dump_model_return(f"工具循环模型返回-第{turn_index + 1}轮", response)
                token_tracker.add_model_usage(response)
                trace["token_usage"] = token_tracker.snapshot()
                yield _token_usage_event(token_tracker)
                if not isinstance(response, AIMessage):
                    raw_answer = _message_content_to_text(getattr(response, "content", ""))
                    token_tracker.add_generated_text(raw_answer)
                    trace["token_usage"] = token_tracker.snapshot(final=True)
                    yield {"type": "trace", "rag_trace": trace}
                    yield _token_usage_event(token_tracker, final=True)
                    if raw_answer:
                        yield {"type": "content", "content": raw_answer}
                    return

                tool_calls = getattr(response, "tool_calls", None) or []
                if not tool_calls:
                    raw_output = _message_reasoning_to_text(response) + _message_content_to_text(response.content)
                    textual_call = _parse_textual_tool_call(raw_output)
                    if textual_call is not None:
                        forced_tool_name = _tool_call_name(textual_call)
                        forced_args = _tool_call_args(textual_call)
                        reason = f"模型输出了文本式工具调用，已转换为真实工具调用 {forced_tool_name}。"
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": f"转换文本工具调用：{forced_tool_name}",
                                "icon": "🔎",
                                "detail": str(forced_args),
                            },
                        }
                        messages.append(AIMessage(content="", tool_calls=[{"name": forced_tool_name, "args": forced_args, "id": f"textual_{turn_index}"}]))
                        executed_name, executed_args, content = await _execute_tool_call(textual_call)
                        total_tool_calls += 1
                        _append_tool_result_to_trace(trace, executed_name, executed_args, content)
                        tool_context.append({"tool_name": executed_name, "args": executed_args, "content": content})
                        messages.append(ToolMessage(content=content, tool_call_id=f"textual_{turn_index}", name=executed_name))
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": f"{executed_name} 返回结果",
                                "icon": "📄",
                                "detail": reason + "\n" + content[:180],
                            },
                        }
                        if executed_name == "recipe_query_tool" and _recipe_query_needs_web_fallback(content):
                            if total_tool_calls < MAX_TOTAL_TOOL_CALLS:
                                yield {
                                    "type": "rag_step",
                                    "step": {
                                        "label": "本地图谱未命中，补充联网搜索",
                                        "icon": "🌐",
                                        "detail": user_text,
                                    },
                                }
                                web_name, _web_args, web_content = await _execute_web_fallback_after_recipe(
                                    user_text,
                                    messages,
                                    trace,
                                    f"recipe_web_fallback_{turn_index}",
                                )
                                total_tool_calls += 1
                                tool_context.append({"tool_name": web_name, "args": _web_args, "content": web_content})
                                yield {
                                    "type": "rag_step",
                                    "step": {
                                        "label": f"{web_name} 返回结果",
                                        "icon": "📄",
                                        "detail": web_content[:180],
                                    },
                                }
                        async for event in _emit_final_answer_from_tool_context(user_text, trace, tool_context, runtime_memory, token_tracker):
                            if event.get("type") == "token_usage":
                                trace["token_usage"] = event.get("token_usage")
                            yield event
                        return

                    thinking, answer = _split_local_llm_output(raw_output)
                    if answer:
                        if _looks_like_tool_request(user_text):
                            forced_call = await _route_missing_tool_call(user_text, history)
                            if forced_call is None:
                                forced_call = {"name": "recipe_query_tool", "args": {"query": user_text}}
                            yield {
                                "type": "rag_step",
                                "step": {
                                    "label": f"模型未调用工具，已强制调用：{_tool_call_name(forced_call)}",
                                    "icon": "🧭",
                                    "detail": str(_tool_call_args(forced_call)),
                                },
                            }
                            total_tool_calls += await _execute_forced_tool_call(
                                user_text,
                                messages,
                                trace,
                                tool_context,
                                _tool_call_name(forced_call),
                                _tool_call_args(forced_call),
                                f"forced_missing_tool_{turn_index}",
                            )
                            async for event in _emit_final_answer_from_tool_context(user_text, trace, tool_context, runtime_memory, token_tracker):
                                if event.get("type") == "token_usage":
                                    trace["token_usage"] = event.get("token_usage")
                                yield event
                            return
                        token_tracker.add_generated_text(raw_output)
                        trace["token_usage"] = token_tracker.snapshot(final=True)
                        yield {"type": "trace", "rag_trace": trace}
                        yield _token_usage_event(token_tracker, final=True)
                        if thinking:
                            yield {"type": "thinking", "content": thinking}
                        yield {"type": "content", "content": answer}
                        return

                    if continue_nudges < 1:
                        continue_nudges += 1
                        messages.append(response)
                        messages.append(HumanMessage(content="你已经停止调用工具，但还没有给出最终回答。请以最新工具结果为最高优先级证据，若它与历史回答冲突先纠正旧说法，再用中文回答用户。"))
                        continue

                    reason = "模型没有继续调用工具，也没有生成最终回答；我先返回目前掌握的信息。"
                    trace["token_usage"] = token_tracker.snapshot(final=True)
                    yield {"type": "trace", "rag_trace": trace}
                    yield _token_usage_event(token_tracker, final=True)
                    yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
                    return

                messages.append(response)
                for index, call in enumerate(tool_calls):
                    call_id = _tool_call_id(call, f"call_{turn_index}_{index}")
                    tool_name = _tool_call_name(call)
                    args = _tool_call_args(call)
                    if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
                        reason = f"本轮工具调用已达到总上限 {MAX_TOTAL_TOOL_CALLS} 次，我先停止继续调用工具。"
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": "工具调用达到总上限",
                                "icon": "🛑",
                                "detail": reason,
                            },
                        }
                        trace["token_usage"] = token_tracker.snapshot(final=True)
                        yield {"type": "trace", "rag_trace": trace}
                        yield _token_usage_event(token_tracker, final=True)
                        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
                        return

                    if tool_name == last_tool_name:
                        consecutive_tool_calls += 1
                    else:
                        last_tool_name = tool_name
                        consecutive_tool_calls = 1

                    if consecutive_tool_calls > MAX_CONSECUTIVE_TOOL_CALLS:
                        reason = f"{tool_name} 已连续调用 {MAX_CONSECUTIVE_TOOL_CALLS} 次，我先停止继续调用这个工具。"
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": f"{tool_name} 连续调用达到上限",
                                "icon": "🛑",
                                "detail": reason,
                            },
                        }
                        trace["token_usage"] = token_tracker.snapshot(final=True)
                        yield {"type": "trace", "rag_trace": trace}
                        yield _token_usage_event(token_tracker, final=True)
                        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
                        return

                    yield {
                        "type": "rag_step",
                        "step": {
                            "label": f"调用工具：{tool_name}",
                            "icon": "🔎",
                            "detail": str(args),
                        },
                    }
                    executed_name, executed_args, content = await _execute_tool_call(call)
                    total_tool_calls += 1
                    _append_tool_result_to_trace(trace, executed_name, executed_args, content)
                    tool_context.append({"tool_name": executed_name, "args": executed_args, "content": content})
                    messages.append(ToolMessage(content=content, tool_call_id=call_id, name=executed_name))
                    yield {
                        "type": "rag_step",
                        "step": {
                            "label": f"{executed_name} 返回结果",
                            "icon": "📄",
                            "detail": content[:180],
                        },
                    }
                    if executed_name == "recipe_query_tool" and _recipe_query_needs_web_fallback(content):
                        if total_tool_calls < MAX_TOTAL_TOOL_CALLS:
                            yield {
                                "type": "rag_step",
                                "step": {
                                    "label": "本地图谱未命中，补充联网搜索",
                                    "icon": "🌐",
                                    "detail": user_text,
                                },
                            }
                            web_name, _web_args, web_content = await _execute_web_fallback_after_recipe(
                                user_text,
                                messages,
                                trace,
                                f"recipe_web_fallback_{turn_index}_{index}",
                            )
                            total_tool_calls += 1
                            tool_context.append({"tool_name": web_name, "args": _web_args, "content": web_content})
                            yield {
                                "type": "rag_step",
                                "step": {
                                    "label": f"{web_name} 返回结果",
                                    "icon": "📄",
                                    "detail": web_content[:180],
                                },
                            }
                    if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
                        reason = f"本轮工具调用已达到总上限 {MAX_TOTAL_TOOL_CALLS} 次，我先停止继续调用工具。"
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": "工具调用达到总上限",
                                "icon": "🛑",
                                "detail": reason,
                            },
                        }
                        trace["token_usage"] = token_tracker.snapshot(final=True)
                        yield {"type": "trace", "rag_trace": trace}
                        yield _token_usage_event(token_tracker, final=True)
                        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
                        return

                    if consecutive_tool_calls >= MAX_CONSECUTIVE_TOOL_CALLS:
                        reason = f"{executed_name} 已连续调用 {MAX_CONSECUTIVE_TOOL_CALLS} 次，我先停止继续调用这个工具。"
                        yield {
                            "type": "rag_step",
                            "step": {
                                "label": f"{executed_name} 连续调用达到上限",
                                "icon": "🛑",
                                "detail": reason,
                            },
                        }
                        trace["token_usage"] = token_tracker.snapshot(final=True)
                        yield {"type": "trace", "rag_trace": trace}
                        yield _token_usage_event(token_tracker, final=True)
                        yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace, reason)}
                        return

                if tool_context:
                    async for event in _emit_final_answer_from_tool_context(user_text, trace, tool_context, runtime_memory, token_tracker):
                        if event.get("type") == "token_usage":
                            trace["token_usage"] = event.get("token_usage")
                        yield event
                    return

            trace["token_usage"] = token_tracker.snapshot(final=True)
            yield {"type": "trace", "rag_trace": trace}
            yield _token_usage_event(token_tracker, final=True)
            yield {"type": "content", "content": _build_partial_tool_answer(user_text, trace)}
    except asyncio.TimeoutError:
        trace["token_usage"] = token_tracker.snapshot(final=True)
        yield {"type": "error", "content": f"工具循环超时：超过 {AGENT_TIMEOUT_SECONDS}s，已停止本轮执行"}
        yield {"type": "trace", "rag_trace": trace}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        trace["token_usage"] = token_tracker.snapshot(final=True)
        yield {"type": "error", "content": f"工具循环出错: {str(e)}"}
        yield {"type": "trace", "rag_trace": trace}
