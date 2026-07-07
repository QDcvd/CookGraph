#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮对话测试脚本 — 测试真实 agent 行为的三大能力。

用法：
    python test/run_multiturn_dialogue_test.py --all
    python test/run_multiturn_dialogue_test.py --category memory
    python test/run_multiturn_dialogue_test.py --category distraction
    python test/run_multiturn_dialogue_test.py --category contradiction

输出：
    test/multiturn_test_results.json  — 每条用例的详细结果
    test/multiturn_test_report.md     — 测试报告
"""

import asyncio
import atexit
import json
import os
import select
import signal
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_project_env() -> dict:
    """读取 .env 并合并到进程环境；命令行/系统环境变量优先。"""
    env = os.environ.copy()
    env_path = ROOT / ".env"
    if not env_path.exists():
        return env

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in env:
            env[key] = value
            os.environ[key] = value
    return env


PROJECT_ENV = load_project_env()

from test.multiturn_test_data import MULTITURN_TEST_CASES
from backend.agent_adapter_local_LLM_harness import stream_search_agent


# ── 配置 ──
RESULT_DIR = ROOT / "test"
JSON_OUTPUT = RESULT_DIR / "multiturn_test_results.json"
REPORT_OUTPUT = RESULT_DIR / "multiturn_test_report.md"
NETWORK_DEPENDENT = True

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/chat/completions")
DEEPSEEK_JUDGE_MODEL = os.getenv("DEEPSEEK_JUDGE_MODEL", "deepseek-chat")
ACTIVE_LLM_TUNNEL = None


# ── LLM SSH 隧道 ──

def env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(env: dict, key: str, default: int) -> int:
    try:
        return int(str(env.get(key, default)).strip())
    except ValueError:
        return default


def openai_base_available(base_url: str, timeout: float = 2.0) -> bool:
    if not base_url:
        return False
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_forward_handler(transport, remote_host: str, remote_port: int):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                channel = transport.open_channel(
                    "direct-tcpip",
                    (remote_host, remote_port),
                    self.request.getpeername(),
                )
            except Exception as e:
                print(f"[tunnel] 打开远端通道失败：{e}", flush=True)
                return

            if channel is None:
                print("[tunnel] 打开远端通道失败：SSH channel 为空", flush=True)
                return

            try:
                while True:
                    readable, _, _ = select.select([self.request, channel], [], [], 1.0)
                    if self.request in readable:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in readable:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                channel.close()

    return Handler


def maybe_start_llm_tunnel(env: dict, disabled: bool = False):
    """测试生命周期内建立本地 OpenAI-compatible LLM SSH 隧道。"""
    if disabled or not env_truthy(env.get("LLM_SSH_TUNNEL")):
        return None

    base_url = env.get("LLM_BASE_URL", "")
    parsed = urlparse(base_url)
    local_port = env_int(env, "LLM_LOCAL_PORT", parsed.port or 1234)
    local_host = env.get("LLM_LOCAL_HOST", "127.0.0.1")
    local_base_url = f"http://{local_host}:{local_port}/v1"
    env["LLM_BASE_URL"] = local_base_url
    os.environ["LLM_BASE_URL"] = local_base_url

    if openai_base_available(local_base_url):
        print(f"[tunnel] 本地 LLM API 已可用：{local_base_url}")
        return None

    remote_host = str(env.get("LLM_REMOTE_HOST", "")).strip()
    remote_user = str(env.get("LLM_REMOTE_USER", "")).strip()
    remote_password = str(env.get("LLM_REMOTE_PASSWORD", ""))
    remote_bind_host = env.get("LLM_REMOTE_BIND_HOST", "127.0.0.1")
    remote_port = env_int(env, "LLM_REMOTE_PORT", 1234)
    if not remote_host or not remote_user:
        print("[tunnel] 未配置 LLM_REMOTE_HOST/LLM_REMOTE_USER，跳过 SSH 隧道")
        return None

    try:
        import paramiko
    except ImportError:
        print("[tunnel] 缺少 paramiko，无法自动建立 SSH 隧道；请运行 pip install paramiko")
        return None

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f"[tunnel] 正在建立本地 {local_host}:{local_port} → {remote_host}:{remote_bind_host}:{remote_port}")
        client.connect(
            remote_host,
            username=remote_user,
            password=remote_password or None,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        server = _ForwardServer(
            (local_host, local_port),
            _make_forward_handler(client.get_transport(), remote_bind_host, remote_port),
        )
        thread = threading.Thread(target=server.serve_forever, name="multiturn-llm-ssh-tunnel", daemon=True)
        thread.start()
        if openai_base_available(local_base_url, timeout=5.0):
            print(f"[tunnel] 已连接远端 LLM：{local_base_url}")
        else:
            print(f"[tunnel] 隧道已启动，但 {local_base_url}/models 暂未响应")
        return server, client
    except Exception as e:
        print(f"[tunnel] 建立 LLM SSH 隧道失败：{type(e).__name__}: {e}")
        return None


def stop_llm_tunnel(tunnel) -> None:
    if not tunnel:
        return
    server, client = tunnel
    print("[tunnel] 正在关闭 LLM SSH 隧道...")
    server.shutdown()
    server.server_close()
    client.close()


def _cleanup_active_tunnel() -> None:
    global ACTIVE_LLM_TUNNEL
    if ACTIVE_LLM_TUNNEL is not None:
        stop_llm_tunnel(ACTIVE_LLM_TUNNEL)
        ACTIVE_LLM_TUNNEL = None


def _handle_shutdown_signal(signum, _frame) -> None:
    print(f"\n[tunnel] 收到退出信号 {signum}，清理 LLM SSH 隧道...")
    _cleanup_active_tunnel()
    raise SystemExit(130)


atexit.register(_cleanup_active_tunnel)
for _signal_name in ("SIGINT", "SIGTERM"):
    if hasattr(signal, _signal_name):
        signal.signal(getattr(signal, _signal_name), _handle_shutdown_signal)


# ── 事件收集 ──

async def run_single_turn(user_text: str, history: list[dict]) -> dict:
    """执行一轮对话，收集所有事件并提取 assistant 回答和 trace。"""
    events: list[dict] = []
    assistant_parts: list[str] = []
    rag_trace: dict | None = None

    try:
        async for event in stream_search_agent(user_text, history):
            events.append(event)
            if event.get("type") == "content":
                content = event.get("content", "")
                if content:
                    assistant_parts.append(content)
            elif event.get("type") == "trace":
                rag_trace = event.get("rag_trace")
    except Exception as e:
        return dict(
            turn_index=len(history) // 2 + 1,
            user=user_text,
            assistant="",
            events=[],
            rag_trace=None,
            tool_calls=[],
            rule_assertions=[],
            error=str(e),
        )

    assistant_text = "".join(assistant_parts)
    tool_calls = []
    if rag_trace and "tool_calls" in rag_trace:
        raw_calls = rag_trace["tool_calls"]
        if isinstance(raw_calls, list):
            tool_calls = raw_calls

    return dict(
        turn_index=len(history) // 2 + 1,
        user=user_text,
        assistant=assistant_text,
        events=events,
        rag_trace=rag_trace,
        tool_calls=tool_calls,
        error=None,
    )


# ── 规则断言 ──

def _find_forbidden_with_negation_check(text: str, keywords: list[str]) -> list[str]:
    """在 text 中查找禁止关键词，跳过被中文否定词否定掉的匹配。"""
    found = []
    for kw in keywords:
        if not kw:
            continue
        pos = text.find(kw)
        while pos != -1:
            start = max(0, pos - 6)
            prefix = text[start:pos]
            negations = ["不是", "不能", "不需要", "不应该", "不必", "不要",
                         "不用", "不可以", "不可能", "不会", "没有", "没", "别",
                         "不", "并非"]
            is_negated = any(prefix.endswith(n) for n in negations)
            if not is_negated:
                found.append(kw)
                break
            pos = text.find(kw, pos + 1)
    return found


def check_turn_assertions(turn: dict, turn_spec: dict) -> list[dict]:
    """对一轮对话执行规则断言。"""
    assertions: list[dict] = []
    assistant_text = turn.get("assistant", "") or ""
    events_text = json.dumps(turn.get("events", []), ensure_ascii=False)
    tool_names = [c.get("tool_name", "") or c.get("name", "") for c in turn.get("tool_calls", [])]

    # 1. expect_tools
    expect_tools = turn_spec.get("expect_tools", [])
    if expect_tools:
        missing = [t for t in expect_tools if t not in tool_names]
        assertions.append(dict(
            name="expect_tools",
            passed=len(missing) == 0,
            detail=f"期望工具: {expect_tools}, 实际工具: {tool_names}" + (f", 缺失: {missing}" if missing else ""),
        ))
    else:
        assertions.append(dict(
            name="expect_tools",
            passed=True,
            detail="本轮无期望工具要求",
        ))

    # 2. expect_web_fallback
    if turn_spec.get("expect_web_fallback"):
        has_web = "web_search_tool" in tool_names
        assertions.append(dict(
            name="expect_web_fallback",
            passed=has_web,
            detail=f"期望联网兜底, 工具列表: {tool_names}",
        ))

    # 3. expect_any_keywords
    any_keywords = turn_spec.get("expect_any_keywords", [])
    if any_keywords:
        full_text = assistant_text + "\n" + events_text
        hit = any(kw in full_text for kw in any_keywords)
        assertions.append(dict(
            name="expect_any_keywords",
            passed=hit,
            detail=f"期望关键词之一: {any_keywords}" + (f", 命中: {[kw for kw in any_keywords if kw in full_text]}" if hit else ", 未命中"),
        ))

    # 4. expect_all_keywords
    all_keywords = turn_spec.get("expect_all_keywords", [])
    if all_keywords:
        full_text = assistant_text + "\n" + events_text
        missing = [kw for kw in all_keywords if kw not in full_text]
        assertions.append(dict(
            name="expect_all_keywords",
            passed=len(missing) == 0,
            detail=f"期望全部关键词: {all_keywords}" + (f", 缺失: {missing}" if missing else ", 全部命中"),
        ))

    # 5. forbid_keywords — 带中文否定感知
    forbid_kw = turn_spec.get("forbid_keywords", [])
    if forbid_kw:
        found = _find_forbidden_with_negation_check(assistant_text, forbid_kw)
        assertions.append(dict(
            name="forbid_keywords",
            passed=len(found) == 0,
            detail=f"禁止关键词: {forbid_kw}" + (f", 发现: {found}" if found else ", 未出现"),
        ))

    return assertions


def check_case_level_assertions(case: dict, turns_result: list[dict]) -> list[dict]:
    """对 case 级期望进行检查。"""
    assertions: list[dict] = []
    category = case["category"]
    last_turn = turns_result[-1] if turns_result else None
    if not last_turn:
        return assertions

    last_assistant = last_turn.get("assistant", "") or ""
    all_text = last_assistant + "\n" + json.dumps(turns_result, ensure_ascii=False)

    if category == "memory":
        # 最后一轮必须仍然提到或 trace 指向目标菜名
        target_dishes = case["turns"][0].get("expect_any_keywords", [])
        if target_dishes:
            hit = any(d in all_text for d in target_dishes)
            assertions.append(dict(
                name="case_memory_retain_target",
                passed=hit,
                detail=f"memory 类: 最后一轮应保留目标菜名 {target_dishes}" + (f", 命中" if hit else ", 未命中"),
            ))

    elif category == "distraction":
        # 最后一轮不能把无关轮次主题当作当前主题
        forbidden = case["turns"][-1].get("forbid_keywords", [])
        if forbidden:
            found = [kw for kw in forbidden if kw in last_assistant]
            assertions.append(dict(
                name="case_distraction_no_drift",
                passed=len(found) == 0,
                detail=f"distraction 类: 最后一轮禁止主题偏移关键词 {forbidden}" + (f", 发现: {found}" if found else ", 未漂移"),
            ))

    elif category == "contradiction":
        # 最后一轮不能顺从用户错误前提（带否定感知）
        forbidden = case["turns"][-1].get("forbid_keywords", [])
        if forbidden:
            found = _find_forbidden_with_negation_check(last_assistant, forbidden)
            assertions.append(dict(
                name="case_contradiction_no_follow",
                passed=len(found) == 0,
                detail=f"contradiction 类: 不应顺从错误前提，禁止关键词 {forbidden}" + (f", 发现: {found}" if found else ", 未顺从"),
            ))

    return assertions


# ── DeepSeek 裁判 ──

VALID_JUDGE_FAILURE_TYPES = {
    "memory_loss",
    "distraction",
    "contradiction",
    "tool_misuse",
    "rule_failed",
    "other",
}
JUDGE_POSITIVE_REASON_MARKERS = (
    "符合期望",
    "符合预期",
    "无违规",
    "没有违规",
    "正确",
    "通过",
    "未被干扰",
    "没有被干扰",
    "未漂移",
    "没有漂移",
)

JUDGE_SYSTEM_PROMPT = """你是一个严格但一致的多轮对话测试裁判。你只判断 assistant 在给定对话中的行为是否符合测试目标。
不要根据你的常识补全事实，只根据对话、工具调用、trace 摘要、规则断言和期望行为判断。

输出必须是严格 JSON，且只包含这些字段：
{
  "passed": boolean,
  "score": number,
  "failure_type": "memory_loss" | "distraction" | "contradiction" | "tool_misuse" | "rule_failed" | "other" | null,
  "reason": string
}

一致性要求：
1. 如果 assistant 符合 expected_behavior，且没有出现 forbidden_behavior，必须输出 passed=true。
2. passed=true 时 score 必须 >= 0.8，failure_type 必须为 null，reason 必须说明通过依据。
3. passed=false 时 score 必须 < 0.8，failure_type 必须是非 null 的固定枚举值，reason 必须指出具体失败证据。
4. 如果规则断言全部通过，且你没有发现明确行为问题，应判 passed=true；不要因为“严格”而随意失败。
5. 如果规则断言失败，通常应判 passed=false，并使用 failure_type="rule_failed" 或更具体的类型。
6. reason 和 passed 必须语义一致：reason 如果说明“符合期望、无违规、正确、未被干扰、未漂移”，则不能输出 passed=false。

如果回答没有继承历史指代、被无关内容带偏、或与前文/工具结果自相矛盾，应判定失败。
不要输出 Markdown，不要额外解释。"""


def normalize_judge_result(raw_result: dict) -> dict:
    """把裁判 JSON 规整成稳定 schema，便于后续一致性检查。"""
    passed = raw_result.get("passed", False)
    if isinstance(passed, str):
        passed = passed.strip().lower() in {"true", "1", "yes", "pass", "passed", "通过"}
    else:
        passed = bool(passed)

    try:
        score = float(raw_result.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))

    failure_type = raw_result.get("failure_type")
    if failure_type == "":
        failure_type = None
    if failure_type is not None:
        failure_type = str(failure_type).strip()

    reason = raw_result.get("reason", "")
    if not isinstance(reason, str):
        reason = json.dumps(reason, ensure_ascii=False)

    return dict(
        passed=passed,
        score=score,
        failure_type=failure_type,
        reason=reason.strip(),
    )


def validate_judge_consistency(result: dict) -> list[str]:
    """检测 DeepSeek 裁判字段之间的自相矛盾。"""
    errors: list[str] = []
    passed = result["passed"]
    score = result["score"]
    failure_type = result["failure_type"]
    reason = result["reason"]

    if passed and score < 0.8:
        errors.append("passed=true 但 score < 0.8")
    if not passed and score >= 0.8:
        errors.append("passed=false 但 score >= 0.8")
    if passed and failure_type is not None:
        errors.append("passed=true 但 failure_type 非空")
    if not passed and failure_type is None:
        errors.append("passed=false 但 failure_type 为空")
    if failure_type is not None and failure_type not in VALID_JUDGE_FAILURE_TYPES:
        errors.append(f"failure_type 不在枚举中: {failure_type}")
    if not passed and any(marker in reason for marker in JUDGE_POSITIVE_REASON_MARKERS):
        errors.append("passed=false 但 reason 使用了通过/正确语义")

    return errors


def invalid_judge_result(reason: str, last_result: dict | None = None, attempts: int = 1) -> dict:
    result = normalize_judge_result(last_result or {})
    result.update(
        passed=False,
        score=0.0,
        failure_type="judge_invalid",
        reason=reason,
        _status="judge_invalid",
        attempts=attempts,
    )
    return result


def call_deepseek_judge(case: dict, turns_result: list[dict],
                        turn_assertions: list[list[dict]],
                        case_assertions: list[dict]) -> dict:
    """调用 DeepSeek 裁判，返回裁判结果。"""
    if not DEEPSEEK_API_KEY:
        return dict(passed=False, score=0.0, failure_type="judge_unavailable",
                    reason="DEEPSEEK_API_KEY 未设置", _status="judge_unavailable")

    # 构建 case 级上下文
    turns_summary = []
    for i, (turn, turn_spec) in enumerate(zip(turns_result, case["turns"])):
        turns_summary.append(dict(
            turn_index=i + 1,
            user=turn["user"],
            assistant=turn.get("assistant", ""),
            tool_calls=turn.get("tool_calls", []),
            rule_assertions=turn_assertions[i] if i < len(turn_assertions) else [],
        ))

    user_prompt = json.dumps(dict(
        case_id=case["id"],
        category=case["category"],
        description=case["description"],
        expected_behavior=case["expected_behavior"],
        forbidden_behavior=case["forbidden_behavior"],
        turns=turns_summary,
        case_level_assertions=case_assertions,
    ), ensure_ascii=False)

    last_result = None
    last_errors: list[str] = []
    for attempt in range(1, 3):
        messages = [
            dict(role="system", content=JUDGE_SYSTEM_PROMPT),
            dict(role="user", content=user_prompt),
        ]
        if last_errors:
            messages.append(dict(
                role="user",
                content=(
                    "你上一次输出的 JSON 字段自相矛盾，请重新裁判。"
                    f"矛盾点：{'; '.join(last_errors)}。"
                    "必须让 passed、score、failure_type、reason 完全一致。"
                ),
            ))

        payload = json.dumps(dict(
            model=DEEPSEEK_JUDGE_MODEL,
            messages=messages,
            temperature=0.0,
            response_format=dict(type="json_object"),
        )).encode("utf-8")

        req = urllib.request.Request(
            DEEPSEEK_API_BASE,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
            return dict(passed=False, score=0.0, failure_type="judge_unavailable",
                        reason=f"DeepSeek API 调用失败: {type(e).__name__}: {e}",
                        _status="judge_unavailable")

        try:
            content = body["choices"][0]["message"]["content"]
            raw_result = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
            return dict(passed=False, score=0.0, failure_type="judge_unavailable",
                        reason=f"DeepSeek 返回解析失败: {type(e).__name__}: {e}",
                        _status="judge_unavailable")

        result = normalize_judge_result(raw_result)
        last_result = result
        last_errors = validate_judge_consistency(result)
        if not last_errors:
            result["_status"] = "judge_ok"
            result["attempts"] = attempt
            return result

    return invalid_judge_result(
        reason=f"DeepSeek 裁判输出自相矛盾，重试后仍无效：{'; '.join(last_errors)}",
        last_result=last_result,
        attempts=2,
    )


# ── Case 运行器 ──

async def run_single_case(case: dict) -> dict:
    """执行一个完整的 case（多轮对话）。"""
    case_id = case["id"]
    history: list[dict] = []
    turns_result: list[dict] = []
    all_turn_assertions: list[list[dict]] = []
    start = time.time()

    for turn_idx, turn_spec in enumerate(case["turns"]):
        # 执行本轮
        turn_result = await run_single_turn(turn_spec["user"], history)
        turns_result.append(turn_result)

        # 规则断言
        assertions = check_turn_assertions(turn_result, turn_spec)
        all_turn_assertions.append(assertions)
        turn_result["rule_assertions"] = assertions

        if turn_result.get("error"):
            # 本轮异常，不再继续后续轮次
            break

        # 回灌历史
        assistant_text = turn_result.get("assistant", "") or ""
        rag_trace = turn_result.get("rag_trace")
        history.append({"role": "user", "content": turn_spec["user"]})

        assistant_entry: dict[str, Any] = {"role": "assistant", "content": assistant_text}
        if rag_trace:
            assistant_entry["rag_trace"] = rag_trace
        history.append(assistant_entry)

    elapsed = time.time() - start

    # Case 级规则断言
    case_assertions = check_case_level_assertions(case, turns_result)
    all_assertions_flat = [a for sub in all_turn_assertions for a in sub] + case_assertions
    rule_passed = all(a["passed"] for a in all_assertions_flat)

    # DeepSeek 裁判
    judge_result = call_deepseek_judge(case, turns_result, all_turn_assertions, case_assertions)

    # 最终判定
    if not rule_passed:
        final_status = "failed"
    elif judge_result.get("_status") == "judge_ok":
        final_status = "passed" if judge_result.get("passed", False) else "failed"
    elif judge_result.get("_status") == "judge_invalid":
        # 裁判自身输出不一致时，避免把坏裁判结果计为 agent 失败。
        final_status = "passed"
    else:
        # DeepSeek 不可用但规则通过
        final_status = "passed"

    return dict(
        id=case_id,
        category=case["category"],
        description=case["description"],
        expected_behavior=case["expected_behavior"],
        forbidden_behavior=case["forbidden_behavior"],
        turns=turns_result,
        turn_assertions=all_turn_assertions,
        case_assertions=case_assertions,
        rule_passed=rule_passed,
        judge_result=dict(
            passed=judge_result.get("passed", False),
            score=judge_result.get("score", 0.0),
            failure_type=judge_result.get("failure_type"),
            reason=judge_result.get("reason", ""),
            status=judge_result.get("_status", "judge_unavailable"),
            attempts=judge_result.get("attempts", 1),
        ),
        final_status=final_status,
        elapsed=round(elapsed, 2),
    )


# ── 统计与报告 ──

def compute_stats(results: list[dict]) -> dict:
    """计算统计数据。"""
    total = len(results)
    passed = sum(1 for r in results if r["final_status"] == "passed")
    failed = sum(1 for r in results if r["final_status"] == "failed")
    judge_unavailable = sum(1 for r in results if r["judge_result"]["status"] == "judge_unavailable")
    judge_invalid = sum(1 for r in results if r["judge_result"]["status"] == "judge_invalid")
    rule_passed_count = sum(1 for r in results if r["rule_passed"])

    by_category: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)

    cat_stats = {}
    for cat, items in sorted(by_category.items()):
        n = len(items)
        p = sum(1 for r in items if r["final_status"] == "passed")
        cat_stats[cat] = dict(total=n, passed=p, rate=round(p / n * 100, 1) if n else 0)

    all_rule_assertions = []
    for r in results:
        for sub in r.get("turn_assertions", []):
            all_rule_assertions.extend(sub)
        all_rule_assertions.extend(r.get("case_assertions", []))
    rule_total = len(all_rule_assertions)
    rule_passed_count_assertions = sum(1 for a in all_rule_assertions if a["passed"])

    judge_ok = sum(1 for r in results if r["judge_result"]["status"] == "judge_ok")
    judge_ok_passed = sum(1 for r in results if r["judge_result"]["status"] == "judge_ok" and r["judge_result"]["passed"])
    judge_total = judge_ok + judge_unavailable + judge_invalid

    return dict(
        total=total,
        passed=passed,
        failed=failed,
        rule_passed_count=rule_passed_count,
        rule_total_assertions=rule_total,
        rule_passed_assertions=rule_passed_count_assertions,
        judge_available=judge_ok,
        judge_unavailable=judge_unavailable,
        judge_invalid=judge_invalid,
        judge_passed=judge_ok_passed,
        pass_rate=round(passed / total * 100, 1) if total else 0,
        rule_pass_rate=round(rule_passed_count_assertions / rule_total * 100, 1) if rule_total else 0,
        judge_available_rate=round(judge_ok / judge_total * 100, 1) if judge_total else 0,
        by_category=cat_stats,
    )


def write_json(results: list[dict], stats: dict, path: Path):
    """写入 JSON 结果文件。"""
    output = dict(
        summary=dict(
            total=stats["total"],
            passed=stats["passed"],
            failed=stats["failed"],
            judge_unavailable=stats["judge_unavailable"],
            judge_invalid=stats["judge_invalid"],
            network_dependent=NETWORK_DEPENDENT,
        ),
        cases=results,
    )
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📄 详细结果已写入：{path}")


def write_report(stats: dict, results: list[dict], category: str | None, path: Path):
    """写入 Markdown 报告。"""
    category_label = {"memory": "记忆", "distraction": "抗干扰", "contradiction": "逻辑自洽"}

    lines = [
        f"# 多轮对话测试报告",
        f"",
        f"- **测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **筛选类别**：{category if category else '全部'}",
        f"- **总 case 数**：{stats['total']}",
        f"- **network_dependent**: true",
        f"- **judge_model**: {DEEPSEEK_JUDGE_MODEL}",
        f"",
        f"## 总体结果",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总通过 | **{stats['passed']} / {stats['total']}（{stats['pass_rate']}%）** |",
        f"| 总失败 | {stats['failed']} |",
        f"| 规则断言通过率 | {stats['rule_passed_assertions']} / {stats['rule_total_assertions']}（{stats['rule_pass_rate']}%） |",
        f"| 规则断言全通过的 case | {stats['rule_passed_count']} / {stats['total']} |",
        f"| DeepSeek 裁判有效 | {stats['judge_available']} / {stats['judge_available'] + stats['judge_unavailable'] + stats['judge_invalid']}（{stats['judge_available_rate']}%） |",
        f"| DeepSeek 裁判无效 | {stats['judge_invalid']} |",
        f"| DeepSeek 裁判不可用 | {stats['judge_unavailable']} |",
        f"| DeepSeek 裁判通过率 | {stats['judge_passed']} / {stats['judge_available']}（{round(stats['judge_passed'] / stats['judge_available'] * 100, 1) if stats['judge_available'] else 0}%） |",
        f"",
        f"## 按类别通过率",
        f"",
        f"| 类别 | 总数 | 通过 | 通过率 |",
        f"|------|------|------|--------|",
    ]
    for cat, s in stats["by_category"].items():
        cat_cn = category_label.get(cat, cat)
        lines.append(f"| {cat_cn} | {s['total']} | {s['passed']} | {s['rate']}% |")

    # 失败 case 详情
    failed_cases = [r for r in results if r["final_status"] == "failed"]
    if failed_cases:
        lines.extend([
            f"",
            f"## 失败 Case 详情",
            f"",
            f"| ID | 类别 | 描述 | 规则断言 | DeepSeek 裁判 | 耗时 |",
            f"|----|------|------|----------|---------------|------|",
        ])
        for r in failed_cases:
            judge_info = r["judge_result"]
            judge_label = f"{'✅' if judge_info.get('passed') else '❌'} {judge_info.get('reason', '')[:30]}"
            if judge_info["status"] == "judge_unavailable":
                judge_label = "⚠️ 裁判不可用"
            elif judge_info["status"] == "judge_invalid":
                judge_label = f"⚠️ 裁判无效 {judge_info.get('reason', '')[:24]}"
            lines.append(
                f"| {r['id']} | {category_label.get(r['category'], r['category'])} | "
                f"{r['description'][:20]} | {'✅' if r['rule_passed'] else '❌'} | {judge_label} | {r['elapsed']}s |"
            )

    # judge_unavailable case
    unavailable = [r for r in results if r["judge_result"]["status"] == "judge_unavailable"]
    if unavailable:
        lines.extend([
            f"",
            f"## DeepSeek 裁判不可用 Case",
            f"",
            f"| ID | 原因 |",
            f"|----|------|",
        ])
        for r in unavailable:
            reason = r["judge_result"].get("reason", "未知")
            lines.append(f"| {r['id']} | {reason} |")

    invalid = [r for r in results if r["judge_result"]["status"] == "judge_invalid"]
    if invalid:
        lines.extend([
            f"",
            f"## DeepSeek 裁判无效 Case",
            f"",
            f"这些 case 的裁判 JSON 字段或语义自相矛盾，最终结果按规则断言兜底。",
            f"",
            f"| ID | 原因 |",
            f"|----|------|",
        ])
        for r in invalid:
            reason = r["judge_result"].get("reason", "未知")
            lines.append(f"| {r['id']} | {reason} |")

    lines.extend([
        f"",
        f"---",
        f"*报告由 run_multiturn_dialogue_test.py 自动生成*",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 测试报告已写入：{path}")


# ── 主流程 ──

def main():
    import argparse

    parser = argparse.ArgumentParser(description="多轮对话测试")
    parser.add_argument(
        "--category", choices=["memory", "distraction", "contradiction", "all"], default="all",
        help="测试类别：memory=记忆, distraction=抗干扰, contradiction=逻辑自洽, all=全部（默认: all）",
    )
    parser.add_argument("--all", action="store_true", dest="run_all", help="运行全部类别")
    parser.add_argument("--no-llm-tunnel", action="store_true", help="禁用测试生命周期内的远端 LLM SSH 隧道")
    parser.add_argument("--case-timeout", type=int, default=180, help="单个 case 最大运行秒数（默认: 180）")
    args = parser.parse_args()

    category = args.category
    if args.run_all:
        category = "all"

    category_label_map = {
        "memory": "记忆保持",
        "distraction": "抗干扰能力",
        "contradiction": "逻辑自洽",
        "all": "全部",
    }

    cases = MULTITURN_TEST_CASES
    if category != "all":
        cases = [c for c in cases if c["category"] == category]

    print(f"{'='*60}")
    print(f"  多轮对话测试 — {category_label_map.get(category, category)}")
    print(f"  DeepSeek 裁判: {'可用' if DEEPSEEK_API_KEY else '不可用（跳过裁判）'}")
    print(f"  模型: {DEEPSEEK_JUDGE_MODEL}")
    print(f"{'='*60}")
    print(f"\n共 {len(cases)} 个 case，开始测试...\n")

    global ACTIVE_LLM_TUNNEL
    tunnel = maybe_start_llm_tunnel(PROJECT_ENV, disabled=args.no_llm_tunnel)
    ACTIVE_LLM_TUNNEL = tunnel
    try:
        results = asyncio.run(_run_all_cases(cases, case_timeout=args.case_timeout))

        stats = compute_stats(results)

        print(f"\n{'='*60}")
        print(f"  测试完成")
        print(f"  通过率：{stats['passed']}/{stats['total']} = {stats['pass_rate']}%")
        print(f"  规则断言全通过：{stats['rule_passed_count']}/{stats['total']}")
        print(f"  DeepSeek 裁判有效：{stats['judge_available']}/{stats['judge_available'] + stats['judge_unavailable'] + stats['judge_invalid']}")
        print(f"  DeepSeek 裁判无效：{stats['judge_invalid']}")
        print(f"{'='*60}")

        write_json(results, stats, JSON_OUTPUT)
        write_report(stats, results, category, REPORT_OUTPUT)

        # 退出码
        if stats["failed"] > 0:
            return 1
        return 0
    finally:
        _cleanup_active_tunnel()


async def _run_all_cases(cases: list[dict], case_timeout: int) -> list[dict]:
    """运行所有 case。"""
    results = []
    for idx, case in enumerate(cases, start=1):
        icon_map = {"memory": "🧠", "distraction": "🎯", "contradiction": "⚡"}
        icon = icon_map.get(case["category"], "❓")
        print(f"  [{idx:3d}/{len(cases)}] {icon} {case['id']} [{case['category']}] {case['description'][:40]}")

        try:
            result = await asyncio.wait_for(run_single_case(case), timeout=case_timeout)
        except asyncio.TimeoutError:
            result = dict(
                id=case["id"],
                category=case["category"],
                description=case["description"],
                expected_behavior=case.get("expected_behavior", ""),
                forbidden_behavior=case.get("forbidden_behavior", ""),
                turns=[],
                turn_assertions=[],
                case_assertions=[
                    dict(name="case_timeout", passed=False, detail=f"case 超过 {case_timeout} 秒未完成")
                ],
                rule_passed=False,
                judge_result=dict(passed=False, score=0.0, failure_type=None,
                                  reason=f"case 超过 {case_timeout} 秒未完成",
                                  status="judge_unavailable", attempts=0),
                final_status="failed",
                elapsed=float(case_timeout),
            )
        except Exception as e:
            result = dict(
                id=case["id"],
                category=case["category"],
                description=case["description"],
                expected_behavior=case.get("expected_behavior", ""),
                forbidden_behavior=case.get("forbidden_behavior", ""),
                turns=[],
                turn_assertions=[],
                case_assertions=[],
                rule_passed=False,
                judge_result=dict(passed=False, score=0.0, failure_type=None,
                                  reason=f"异常崩溃: {type(e).__name__}: {e}",
                                  status="judge_unavailable", attempts=0),
                final_status="failed",
                elapsed=0.0,
            )

        status_icon = "✅" if result["final_status"] == "passed" else "❌"
        judge_status = result["judge_result"]["status"]
        judge_icon = {"judge_ok": "🤖", "judge_unavailable": "⚠️", "judge_invalid": "⚠️"}.get(judge_status, "❓")
        print(f"         {status_icon} 规则={'✅' if result['rule_passed'] else '❌'} "
              f"{judge_icon} 裁判={judge_status} "
              f"→ {result['final_status']} ({result['elapsed']:.1f}s)")
        sys.stdout.flush()

        results.append(result)

    return results


if __name__ == "__main__":
    sys.exit(main())
