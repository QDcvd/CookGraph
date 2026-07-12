#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量测试入口 — 依次运行所有测试项目，汇总生成 test_report.md。

用法：
    PYTHONIOENCODING=utf-8 python test/run_all_tests.py

输出：
    test/test_report.md  — 统一汇总报告
"""

import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT_PATH = ROOT / "test" / ".artifacts" / "test_report.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

PYTHON = sys.executable
ENV = os.environ.copy()
ENV["PYTHONIOENCODING"] = "utf-8"
# 确保 DeepSeek 裁判可用
if "DEEPSEEK_API_KEY" not in ENV:
    # 从 .env 读取
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "DEEPSEEK_API_KEY":
                    ENV["DEEPSEEK_API_KEY"] = v
                    os.environ["DEEPSEEK_API_KEY"] = v


def _compact_output(text: str, *, head: int = 1800, tail: int = 3000) -> str:
    """Keep both beginning and end so reports show final cases."""
    if len(text) <= head + tail + 200:
        return text
    omitted = len(text) - head - tail
    return (
        text[:head].rstrip()
        + f"\n\n...（中间省略 {omitted} 字符，保留末尾以便查看最后一个测试）...\n\n"
        + text[-tail:].lstrip()
    )


def _reader_thread(pipe, out_queue: "queue.Queue[str]") -> None:
    try:
        for line in iter(pipe.readline, ""):
            out_queue.put(line)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def run_test(label: str, cmd: list[str], cwd: str | None = None, timeout: int = 600) -> dict:
    """运行一个测试命令，返回结果摘要。"""
    start = time.time()
    stdout_parts: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd or str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=ENV,
            bufsize=1,
        )
        assert proc.stdout is not None
        out_queue: "queue.Queue[str]" = queue.Queue()
        thread = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue), daemon=True)
        thread.start()

        timed_out = False
        while proc.poll() is None or not out_queue.empty():
            try:
                line = out_queue.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is None and time.time() - start > timeout:
                    timed_out = True
                    proc.kill()
                    break
                continue
            stdout_parts.append(line)
            print(line, end="", flush=True)
            if proc.poll() is None and time.time() - start > timeout:
                timed_out = True
                proc.kill()
                break

        thread.join(timeout=1)
        while not out_queue.empty():
            line = out_queue.get_nowait()
            stdout_parts.append(line)
            print(line, end="", flush=True)

        elapsed = round(time.time() - start, 1)
        stdout = "".join(stdout_parts)
        stderr = ""
        returncode = proc.wait(timeout=3) if proc.poll() is None else proc.returncode
        if timed_out:
            return dict(
                label=label,
                status="timeout",
                elapsed=elapsed,
                summary=f"超时退出（>{timeout}s）",
                details=_compact_output(stdout),
                stdout=_compact_output(stdout, head=1500, tail=2500),
                stderr="",
            )
    except Exception as e:
        return dict(
            label=label, status="error", elapsed=round(time.time() - start, 1),
            summary=f"执行异常: {e}", details=_compact_output("".join(stdout_parts)), stdout="", stderr="",
        )

    # 从 stdout/stderr 提取摘要
    summary = _extract_summary(label, stdout + stderr, returncode)
    return dict(
        label=label, status="passed" if returncode == 0 else "failed",
        elapsed=elapsed,
        summary=summary,
        details=_compact_output(stdout),
        stdout=_compact_output(stdout, head=1500, tail=2500),
        stderr=stderr[:1000],
    )


def _extract_summary(label: str, text: str, rc: int) -> str:
    """从测试输出中提取关键指标。"""
    lines = text.splitlines()

    if "持久化" in label or "zleap_lite_memory" in label or "test_chat_persistence" in label:
        for line in lines:
            if "Ran " in line and " tests in " in line:
                return line.strip()
            if "OK" in line and ("test_" in text[:200] or "FAILED" not in text[:200]):
                pass
        if "FAILED" in text or "failures" in text.lower():
            fails = [l.strip() for l in lines if "FAIL" in l or "fail" in l.lower()]
            return ("失败" if rc else "通过") + (f" ({fails[0]})" if fails else "")
        return "通过" if rc == 0 else "失败"

    if "单元测试" in label or "unittest" in label:
        for line in lines:
            if "Ran " in line and " tests in " in line:
                return line.strip()
        return "通过" if rc == 0 else "失败"

    if "召回率" in label or "run_recall_test" in label:
        hits = [l for l in lines if "严格命中率" in l or "总通过率" in l or "联网兜底" in l or "异常" in l]
        return " | ".join(h.strip() for h in hits) if hits else ("通过" if rc == 0 else "失败")

    if "多轮" in label or "run_multiturn" in label:
        hits = [l for l in lines if "通过率" in l or "规则断言" in l or "DeepSeek 裁判" in l]
        return " | ".join(h.strip() for h in hits) if hits else ("通过" if rc == 0 else "失败")

    return "通过" if rc == 0 else "失败"


def run_all():
    tests = [
        dict(
            label="基础单元测试（全量）",
            cmd=[
                PYTHON,
                "-m",
                "unittest",
                "test.test_query_understanding",
                "test.test_recipe_query_adapter_guardrails",
                "test.test_tool_routing_guardrails",
                "test.test_grounded_recipe_answer",
                "test.test_token_usage_tracker",
            ],
            timeout=180,
        ),
        dict(
            label="持久化测试",
            cmd=[PYTHON, "test/test_chat_persistence.py"],
            timeout=120,
        ),
        dict(
            label="Zleap-lite 记忆测试",
            cmd=[PYTHON, "test/test_zleap_lite_memory.py"],
            timeout=120,
        ),
        dict(
            label="单轮召回率测试（全量）",
            cmd=[PYTHON, "test/run_recall_test.py", "--phase", "all"],
            timeout=900,
        ),
        dict(
            label="多轮对话测试（全量）",
            cmd=[PYTHON, "test/run_multiturn_dialogue_test.py", "--all"],
            timeout=1200,
        ),
    ]

    results = []
    print("=" * 60)
    print("  MiniCookingAgent-Demo 全量测试")
    print(f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Python：{sys.executable}")
    print("=" * 60)

    for t in tests:
        label = t["label"]
        print(f"\n▶ {label} ...", end=" ", flush=True)
        r = run_test(label, t["cmd"], timeout=t.get("timeout", 600))
        results.append(r)
        icon = "✅" if r["status"] == "passed" else ("⏰" if r["status"] == "timeout" else "❌")
        print(f"{icon} ({r['elapsed']}s)")
        if r["summary"]:
            print(f"   {r['summary']}")

    _write_report(results)

    print("\n" + "=" * 60)
    print("  全部测试完成")
    passed = sum(1 for r in results if r["status"] == "passed")
    print(f"  通过：{passed}/{len(results)}")
    print(f"  报告：{REPORT_PATH}")
    print("=" * 60)

    return 0 if passed == len(results) else 1


def _write_report(results: list[dict]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# MiniCookingAgent-Demo 全量测试报告",
        "",
        f"- **测试时间**：{now}",
        f"- **Python**：{sys.executable}",
        f"- **DeepSeek 裁判**：{'可用' if ENV.get('DEEPSEEK_API_KEY') else '不可用'}",
        "",
        "## 总体结果",
        "",
        "| 测试项目 | 状态 | 耗时 |",
        "|---------|------|------|",
    ]
    passed = sum(1 for r in results if r["status"] == "passed")
    for r in results:
        icon = {"passed": "✅", "failed": "❌", "timeout": "⏰", "error": "💥"}.get(r["status"], "❓")
        lines.append(f"| {r['label']} | {icon} {r['status']} | {r['elapsed']}s |")

    lines.extend([
        "",
        f"**汇总：{passed}/{len(results)} 通过**",
        "",
        "---",
        "",
        "## 各项目详情",
        "",
    ])

    for r in results:
        icon = {"passed": "✅", "failed": "❌", "timeout": "⏰", "error": "💥"}.get(r["status"], "❓")
        lines.append(f"### {icon} {r['label']}")
        lines.append("")
        if r["summary"]:
            lines.append(f"**摘要**：{r['summary']}")
            lines.append("")
        if r["details"]:
            lines.append("```")
            lines.append(r["details"].rstrip())
            lines.append("```")
            lines.append("")

    lines.extend([
        "---",
        f"*报告由 run_all_tests.py 自动生成 — {now}*",
        "",
    ])

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(run_all())
