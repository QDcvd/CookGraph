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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT_PATH = ROOT / "test" / "test_report.md"

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


def run_test(label: str, cmd: list[str], cwd: str | None = None) -> dict:
    """运行一个测试命令，返回结果摘要。"""
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=ENV,
            timeout=600,
        )
        elapsed = round(time.time() - start, 1)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        return dict(
            label=label, status="timeout", elapsed=round(time.time() - start, 1),
            summary="超时退出", details="", stdout="", stderr="",
        )
    except Exception as e:
        return dict(
            label=label, status="error", elapsed=round(time.time() - start, 1),
            summary=f"执行异常: {e}", details="", stdout="", stderr="",
        )

    # 从 stdout/stderr 提取摘要
    summary = _extract_summary(label, stdout + stderr, returncode)
    return dict(
        label=label, status="passed" if returncode == 0 else "failed",
        elapsed=elapsed, summary=summary, details=stdout[:2000],
        stdout=stdout[:3000], stderr=stderr[:1000],
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
            label="持久化测试",
            cmd=[PYTHON, "test/test_chat_persistence.py"],
        ),
        dict(
            label="Zleap-lite 记忆测试",
            cmd=[PYTHON, "test/test_zleap_lite_memory.py"],
        ),
        dict(
            label="单轮召回率测试（全量）",
            cmd=[PYTHON, "test/run_recall_test.py", "--phase", "all"],
        ),
        dict(
            label="多轮对话测试（全量）",
            cmd=[PYTHON, "test/run_multiturn_dialogue_test.py", "--all"],
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
        r = run_test(label, t["cmd"])
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
            lines.append(r["details"].rstrip()[:1500])
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
