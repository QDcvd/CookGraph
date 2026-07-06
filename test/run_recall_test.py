#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菜谱知识图谱召回率测试程序。

用法：
    # 第一阶段（核心 55 条）
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 1

    # 第二阶段（扩展 45 条）
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 2

    # 全量 100 条
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase all

输出：
    test/test_results.json     — 每条用例的详细结果
    test/test_report.md        — 人类可读的召回率报告
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test.recipe_test_data import TEST_CASES
from backend.recipe_query_adapter import query_recipe_kg


# ── 配置 ──
RESULT_DIR = ROOT / "test"
JSON_OUTPUT = RESULT_DIR / "test_results.json"
REPORT_OUTPUT = RESULT_DIR / "test_report.md"

# 预期的 error/失败前缀——匹配这些的不算宽松命中
ERROR_PREFIXES = ("菜谱查询失败", "❌", "query 不能为空")


def check_strict_hit(output: str, expected: list[str]) -> bool:
    """严格命中：success=True 且期望菜名出现在输出中。"""
    if "success: True" not in output:
        return False
    if not expected:
        return False
    return any(dish in output for dish in expected)


def check_loose_hit(output: str) -> bool:
    """宽松命中：返回了非空、非错误的内容。"""
    if not output or not output.strip():
        return False
    return not output.startswith(ERROR_PREFIXES)


def classify(output: str, expected: list[str], strict_ok: bool) -> dict:
    """对一条结果进行分类判断。"""
    strict = check_strict_hit(output, expected)
    loose = check_loose_hit(output)

    if strict:
        return dict(status="strict_hit", label="严格命中")
    if loose and strict_ok:
        return dict(status="loose_hit", label="宽松命中（应严格）")
    if loose:
        return dict(status="loose_hit", label="宽松命中")
    return dict(status="miss", label="未命中")


def run_single_case(case: dict) -> dict:
    """执行一条测试用例。"""
    input_text = case["input"]
    expected = case["expected"]
    strict_ok = case["strict_ok"]

    start = time.time()
    try:
        output = query_recipe_kg(input_text)
    except Exception as e:
        elapsed = time.time() - start
        return dict(
            id=case["id"],
            input=input_text,
            category=case["category"],
            phase=case["phase"],
            expected_dish=expected,
            strict_ok=strict_ok,
            status="error",
            label=f"异常崩溃：{type(e).__name__}: {e}",
            success=False,
            match_mode="error",
            actual_output="",
            elapsed=round(elapsed, 2),
        )

    elapsed = time.time() - start
    result = classify(output, expected, strict_ok)

    # 从输出中提取 match_mode
    match_mode = "unknown"
    for line in output.splitlines():
        if "match_mode:" in line:
            match_mode = line.split("match_mode:")[-1].strip()
            break

    return dict(
        id=case["id"],
        input=input_text,
        category=case["category"],
        phase=case["phase"],
        expected_dish=expected,
        strict_ok=strict_ok,
        status=result["status"],
        label=result["label"],
        success="success: True" in output,
        match_mode=match_mode,
        actual_output=output[:300],  # 只存前 300 字符
        elapsed=round(elapsed, 2),
    )


def run_phase(phase: str) -> list[dict]:
    """执行指定阶段的所有用例。phase: '1' / '2' / 'all'"""
    results = []
    cases = [
        c for c in TEST_CASES
        if phase == "all" or c["phase"] == int(phase)
    ]
    total = len(cases)
    print(f"\n共 {total} 条用例，开始测试...\n")

    for idx, case in enumerate(cases, start=1):
        result = run_single_case(case)
        results.append(result)
        icon = {"strict_hit": "✅", "loose_hit": "🟡", "miss": "❌", "error": "💥"}.get(
            result["status"], "❓"
        )
        print(
            f"  [{idx:3d}/{total}] {icon} #{result['id']:3d} "
            f"[{result['category']:8s}] {result['input'][:30]:30s} "
            f"→ {result['label']} ({result['elapsed']:.1f}s)"
        )
        sys.stdout.flush()

    return results


def compute_stats(results: list[dict]) -> dict:
    """计算各维度的召回率统计。"""
    total = len(results)
    strict = sum(1 for r in results if r["status"] == "strict_hit")
    loose = sum(1 for r in results if r["status"] in ("strict_hit", "loose_hit"))
    errors = sum(1 for r in results if r["status"] == "error")

    # 按 category 分组统计
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    cat_stats = {}
    for cat, items in sorted(by_cat.items()):
        n = len(items)
        s = sum(1 for r in items if r["status"] == "strict_hit")
        l = sum(1 for r in items if r["status"] in ("strict_hit", "loose_hit"))
        cat_stats[cat] = dict(
            total=n, strict=s, loose=l,
            strict_rate=round(s / n * 100, 1) if n else 0,
            loose_rate=round(l / n * 100, 1) if n else 0,
        )

    return dict(
        total=total,
        strict=strict,
        loose=loose,
        errors=errors,
        strict_rate=round(strict / total * 100, 1) if total else 0,
        loose_rate=round(loose / total * 100, 1) if total else 0,
        by_category=cat_stats,
    )


def write_json(results: list[dict], path: Path):
    """写入 JSON 结果文件。"""
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n📄 详细结果已写入：{path}")


def write_report(stats: dict, results: list[dict], phase: str, path: Path):
    """写入 Markdown 报告。"""
    phase_label = {"1": "第一阶段（核心）", "2": "第二阶段（扩展）", "all": "全量"}.get(phase, phase)

    lines = [
        f"# 菜谱知识图谱召回率测试报告",
        f"",
        f"- **测试阶段**：{phase_label}",
        f"- **测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **用例总数**：{stats['total']}",
        f"",
        f"## 总体召回率",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 严格命中（success:True + 菜名匹配） | **{stats['strict']} / {stats['total']}（{stats['strict_rate']}%）** |",
        f"| 宽松命中（有返回内容，非错误） | **{stats['loose']} / {stats['total']}（{stats['loose_rate']}%）** |",
        f"| 异常崩溃 | {stats['errors']} |",
        f"",
        f"## 各维度召回率",
        f"",
        f"| 维度 | 总数 | 严格命中 | 严格率 | 宽松命中 | 宽松率 |",
        f"|------|------|---------|--------|---------|--------|",
    ]
    for cat, s in stats["by_category"].items():
        lines.append(
            f"| {cat} | {s['total']} | {s['strict']} | {s['strict_rate']}% | {s['loose']} | {s['loose_rate']}% |"
        )

    # 未命中 / 异常列表
    failed = [r for r in results if r["status"] in ("miss", "error")]
    if failed:
        lines.extend([
            f"",
            f"## 未命中 / 异常用例详情",
            f"",
            f"| ID | 输入 | 期望菜名 | 实际状态 | 说明 | match_mode |",
            f"|----|------|---------|---------|------|-----------|",
        ])
        for r in failed:
            expected = "、".join(r["expected_dish"]) if r["expected_dish"] else "（无）"
            lines.append(
                f"| #{r['id']} | {r['input'][:25]} | {expected[:20]} | {r['label']} | {r['status']} | {r['match_mode']} |"
            )

        lines.extend([
            f"",
            f"### 失败原因归类",
            f"",
        ])
        miss_strict = [r for r in results if r["status"] == "loose_hit" and r["strict_ok"]]
        miss_all = [r for r in failed if r["status"] == "miss"]
        errors_list = [r for r in failed if r["status"] == "error"]
        if miss_strict:
            lines.append(f"- **应严格但仅宽松**（{len(miss_strict)} 条）：语义改写未命中或 match_mode=fuzzy 导致 strict_ok 条件不满足")
        if miss_all:
            lines.append(f"- **完全未命中**（{len(miss_all)} 条）：图谱中可能不存在该菜，或需要语义改写增强")
        if errors_list:
            lines.append(f"- **异常崩溃**（{len(errors_list)} 条）：需要修复代码 bug")

    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*报告由 run_recall_test.py 自动生成*")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 召回率报告已写入：{path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="菜谱知识图谱召回率测试")
    parser.add_argument(
        "--phase", choices=["1", "2", "all"], default="1",
        help="测试阶段：1=第一阶段核心55条, 2=第二阶段扩展45条, all=全量100条（默认: 1）",
    )
    args = parser.parse_args()

    phase = args.phase
    phase_label = {"1": "第一阶段（核心）", "2": "第二阶段（扩展）", "all": "全量"}.get(phase, phase)
    print(f"{'='*60}")
    print(f"  菜谱知识图谱召回率测试 — {phase_label}")
    print(f"{'='*60}")

    results = run_phase(phase)
    stats = compute_stats(results)

    print(f"\n{'='*60}")
    print(f"  测试完成")
    print(f"  严格召回率：{stats['strict']}/{stats['total']} = {stats['strict_rate']}%")
    print(f"  宽松召回率：{stats['loose']}/{stats['total']} = {stats['loose_rate']}%")
    print(f"  异常：{stats['errors']}")
    print(f"{'='*60}")

    write_json(results, JSON_OUTPUT)
    write_report(stats, results, phase, REPORT_OUTPUT)

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
