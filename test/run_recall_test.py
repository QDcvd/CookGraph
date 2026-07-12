#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菜谱知识图谱查询测试程序。

用法：
    # 第一阶段（核心 50 条）
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 1

    # 第二阶段（扩展 50 条）
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 2

    # 全量 150 条
    PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase all

输出：
    test/.artifacts/test_results.json     — 每条用例的详细结果
    test/.artifacts/test_report.md        — 人类可读的测试报告
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
from backend.agent_adapter_local_LLM_harness import _recipe_query_needs_web_fallback
from backend.agent_tools import web_search_tool
from backend.query_router import route_query
from backend.recipe_query_adapter import query_recipe_plan
from backend.tool_result import serialize_tool_result


def run_local_plan(input_text: str) -> str:
    """通过真实 V2 路由执行一条召回用例。"""
    action = route_query(input_text, [])
    if action.action != "tool" or action.tool_name != "recipe_query_tool" or not isinstance(action.plan, dict):
        return ""
    return serialize_tool_result(query_recipe_plan(action.plan))


# ── 配置 ──
RESULT_DIR = ROOT / "test" / ".artifacts"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
JSON_OUTPUT = RESULT_DIR / "test_results.json"
REPORT_OUTPUT = RESULT_DIR / "test_report.md"

# 预期的 error/失败标记——匹配这些的不算宽松命中。
ERROR_PREFIXES = ("菜谱查询失败", "❌", "query 不能为空")
FAILURE_MARKERS = (
    *ERROR_PREFIXES,
    "success: False",
    "无法理解的查询格式",
    "match_mode: none",
    "未找到",
    "未命中",
)
NEGATIVE_OK_MARKERS = (
    *FAILURE_MARKERS,
    "不支持",
    "图谱中不存在",
    "没有找到",
)
WEB_SEARCH_FAILURE_MARKERS = (
    "网络搜索失败",
    "网络搜索没有返回内容",
)


def check_strict_hit(output: str, expected: list[str]) -> bool:
    """严格命中：success=True 且期望菜名出现在输出中。"""
    if "success: True" not in output:
        return False
    if not expected:
        return False
    return any(dish in output for dish in expected)


def contains_expected(output: str, expected: list[str]) -> bool:
    """期望项命中：任一期望菜名/关键词出现在输出中。"""
    if not expected:
        return False
    return any(item in output for item in expected)


def has_success(output: str) -> bool:
    """查询是否返回成功结果。"""
    return "success: True" in output


def has_failure_marker(output: str) -> bool:
    """查询是否返回失败/拒答/未理解标记。"""
    if not output or not output.strip():
        return True
    return output.startswith(ERROR_PREFIXES) or any(marker in output for marker in FAILURE_MARKERS)


def check_loose_hit(output: str) -> bool:
    """宽松命中：返回了非空、非失败的成功内容。"""
    if not output or not output.strip():
        return False
    return has_success(output) and not has_failure_marker(output)


def infer_eval_type(case: dict) -> str:
    """从用例字段推断评估类型，兼容旧数据。"""
    if case.get("eval_type"):
        return case["eval_type"]
    if case.get("query_type") == "none" or case.get("category", "").startswith("边界-"):
        return "negative"
    if not case.get("strict_ok", True):
        return "recommendation"
    return "positive"


def check_negative_ok(output: str) -> bool:
    """负样本通过：系统明确拒答、未理解或未命中，而不是误召回具体菜谱。"""
    if not output or not output.strip():
        return True
    return (not has_success(output)) and any(marker in output for marker in NEGATIVE_OK_MARKERS)


def check_web_search_ok(output: str) -> bool:
    """联网兜底通过：web_search_tool 实际返回了搜索结果。"""
    text = str(output or "").strip()
    if not text:
        return False
    if text.startswith(WEB_SEARCH_FAILURE_MARKERS):
        return False
    return "搜索结果：" in text or "链接：" in text or "摘要：" in text


def classify(output: str, expected: list[str], strict_ok: bool, eval_type: str) -> dict:
    """对一条结果进行分类判断。"""
    strict = check_strict_hit(output, expected)
    loose = check_loose_hit(output)
    expected_hit = contains_expected(output, expected)

    if eval_type == "negative":
        if check_negative_ok(output):
            return dict(status="negative_ok", label="负样本通过")
        return dict(status="false_positive", label="负样本误召回")

    if eval_type == "recommendation":
        if expected and expected_hit and has_success(output):
            return dict(status="relevant_hit", label="相关命中")
        if loose:
            return dict(status="loose_hit", label="有效推荐")
        return dict(status="miss", label="推荐未命中")

    if strict:
        return dict(status="strict_hit", label="严格命中")
    if loose and strict_ok:
        return dict(status="loose_hit", label="宽松命中（应严格）")
    if loose:
        return dict(status="loose_hit", label="宽松命中")
    return dict(status="miss", label="未命中")


def _extract_match_mode(output: str) -> str:
    """从工具输出中提取 match_mode。"""
    for line in output.splitlines():
        if "match_mode:" in line:
            return line.split("match_mode:")[-1].strip()
    return "unknown"


def run_web_fallback_case(case: dict) -> dict:
    """执行一条图谱未命中后联网兜底测试用例。"""
    input_text = case["input"]
    expected = case["expected"]
    strict_ok = case["strict_ok"]
    start = time.time()

    try:
        local_output = run_local_plan(input_text)
        fallback_needed = _recipe_query_needs_web_fallback(local_output)
        web_output = ""
        web_ok = False
        if fallback_needed:
            web_output = str(web_search_tool.invoke({"query": input_text}))
            web_ok = check_web_search_ok(web_output)
    except Exception as e:
        elapsed = time.time() - start
        return dict(
            id=case["id"],
            input=input_text,
            category=case["category"],
            phase=case["phase"],
            expected_dish=expected,
            strict_ok=strict_ok,
            eval_type="web_fallback",
            expected_query_type=case.get("query_type", "unknown"),
            status="error",
            label=f"异常崩溃：{type(e).__name__}: {e}",
            success=False,
            match_mode="error",
            web_fallback_triggered=False,
            web_search_success=False,
            actual_output="",
            elapsed=round(elapsed, 2),
        )

    elapsed = time.time() - start
    if fallback_needed and web_ok:
        status = "web_fallback_hit"
        label = "联网兜底命中"
    elif fallback_needed:
        status = "web_fallback_failed"
        label = "联网兜底失败"
    else:
        status = "web_fallback_not_triggered"
        label = "未触发联网兜底"

    return dict(
        id=case["id"],
        input=input_text,
        category=case["category"],
        phase=case["phase"],
        expected_dish=expected,
        strict_ok=strict_ok,
        eval_type="web_fallback",
        expected_query_type=case.get("query_type", "unknown"),
        status=status,
        label=label,
        success=web_ok,
        match_mode=_extract_match_mode(local_output),
        web_fallback_triggered=fallback_needed,
        web_search_success=web_ok,
        actual_output=("【本地图谱】\n" + local_output[:500] + "\n\n【联网搜索】\n" + web_output[:500])[:1000],
        elapsed=round(elapsed, 2),
    )


def run_single_case(case: dict) -> dict:
    """执行一条测试用例。"""
    input_text = case["input"]
    expected = case["expected"]
    strict_ok = case["strict_ok"]
    eval_type = infer_eval_type(case)

    if eval_type == "web_fallback":
        return run_web_fallback_case(case)

    start = time.time()
    try:
        output = run_local_plan(input_text)
    except Exception as e:
        elapsed = time.time() - start
        return dict(
            id=case["id"],
            input=input_text,
            category=case["category"],
            phase=case["phase"],
            expected_dish=expected,
            strict_ok=strict_ok,
            eval_type=eval_type,
            expected_query_type=case.get("query_type", "unknown"),
            status="error",
            label=f"异常崩溃：{type(e).__name__}: {e}",
            success=False,
            match_mode="error",
            actual_output="",
            elapsed=round(elapsed, 2),
        )

    elapsed = time.time() - start
    result = classify(output, expected, strict_ok, eval_type)

    match_mode = _extract_match_mode(output)

    return dict(
        id=case["id"],
        input=input_text,
        category=case["category"],
        phase=case["phase"],
        expected_dish=expected,
        strict_ok=strict_ok,
        eval_type=eval_type,
        expected_query_type=case.get("query_type", "unknown"),
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
        icon = {
            "strict_hit": "✅",
            "relevant_hit": "🟢",
            "loose_hit": "🟡",
            "negative_ok": "☑️",
            "web_fallback_hit": "🌐",
            "web_fallback_failed": "⚠️",
            "web_fallback_not_triggered": "🚫",
            "false_positive": "🚫",
            "miss": "❌",
            "error": "💥",
        }.get(
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
    """计算各维度的测试统计。"""
    total = len(results)
    strict = sum(1 for r in results if r["status"] == "strict_hit")
    relevant = sum(1 for r in results if r["status"] == "relevant_hit")
    loose = sum(1 for r in results if r["status"] == "loose_hit")
    negative_ok = sum(1 for r in results if r["status"] == "negative_ok")
    web_fallback = sum(1 for r in results if r["status"] == "web_fallback_hit")
    web_fallback_failed = sum(
        1 for r in results if r["status"] in ("web_fallback_failed", "web_fallback_not_triggered")
    )
    false_positive = sum(1 for r in results if r["status"] == "false_positive")
    passed = sum(
        1
        for r in results
        if r["status"] in ("strict_hit", "relevant_hit", "loose_hit", "negative_ok", "web_fallback_hit")
    )
    errors = sum(1 for r in results if r["status"] == "error")

    # 按 category 分组统计
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    cat_stats = {}
    for cat, items in sorted(by_cat.items()):
        n = len(items)
        s = sum(1 for r in items if r["status"] == "strict_hit")
        rel = sum(1 for r in items if r["status"] == "relevant_hit")
        l = sum(1 for r in items if r["status"] == "loose_hit")
        neg = sum(1 for r in items if r["status"] == "negative_ok")
        web = sum(1 for r in items if r["status"] == "web_fallback_hit")
        p = sum(
            1
            for r in items
            if r["status"] in ("strict_hit", "relevant_hit", "loose_hit", "negative_ok", "web_fallback_hit")
        )
        cat_stats[cat] = dict(
            total=n, strict=s, relevant=rel, loose=l, negative_ok=neg, web_fallback=web, passed=p,
            strict_rate=round(s / n * 100, 1) if n else 0,
            pass_rate=round(p / n * 100, 1) if n else 0,
        )

    return dict(
        total=total,
        strict=strict,
        relevant=relevant,
        loose=loose,
        negative_ok=negative_ok,
        web_fallback=web_fallback,
        web_fallback_failed=web_fallback_failed,
        false_positive=false_positive,
        passed=passed,
        errors=errors,
        strict_rate=round(strict / total * 100, 1) if total else 0,
        pass_rate=round(passed / total * 100, 1) if total else 0,
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
    has_recommendation_stats = bool(stats["relevant"] or stats["loose"])
    has_web_fallback_stats = bool(stats["web_fallback"] or stats["web_fallback_failed"])

    lines = [
        f"# 菜谱知识图谱查询测试报告",
        f"",
        f"- **测试阶段**：{phase_label}",
        f"- **测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **用例总数**：{stats['total']}",
        f"",
        f"## 总体结果",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 严格命中（success:True + 菜名匹配） | **{stats['strict']} / {stats['total']}（{stats['strict_rate']}%）** |",
    ]
    if has_recommendation_stats:
        lines.extend([
            f"| 相关命中（推荐类命中期望菜名/关键词） | {stats['relevant']} |",
            f"| 有效推荐（推荐类有成功内容） | {stats['loose']} |",
        ])
    lines.extend([
        f"| 负样本通过（拒答/未命中符合预期） | {stats['negative_ok']} |",
    ])
    if has_web_fallback_stats:
        lines.extend([
            f"| 联网兜底成功（图谱未命中 + web_search_tool 返回结果） | {stats['web_fallback']} |",
            f"| 联网兜底失败/未触发 | {stats['web_fallback_failed']} |",
        ])
    lines.extend([
        f"| 总通过 | **{stats['passed']} / {stats['total']}（{stats['pass_rate']}%）** |",
        f"| 负样本误召回 | {stats['false_positive']} |",
        f"| 异常崩溃 | {stats['errors']} |",
        f"",
        f"## 各维度结果",
        f"",
    ])
    if has_recommendation_stats:
        lines.extend([
            f"| 维度 | 总数 | 严格 | 相关 | 有效推荐 | 负样本通过 | 联网兜底 | 总通过 | 通过率 |",
            f"|------|------|------|------|----------|------------|----------|--------|--------|",
        ])
    elif has_web_fallback_stats:
        lines.extend([
            f"| 维度 | 总数 | 严格 | 负样本通过 | 联网兜底 | 总通过 | 通过率 |",
            f"|------|------|------|------------|----------|--------|--------|",
        ])
    else:
        lines.extend([
            f"| 维度 | 总数 | 严格 | 负样本通过 | 总通过 | 通过率 |",
            f"|------|------|------|------------|--------|--------|",
        ])
    for cat, s in stats["by_category"].items():
        if has_recommendation_stats:
            lines.append(
                f"| {cat} | {s['total']} | {s['strict']} | {s['relevant']} | {s['loose']} | {s['negative_ok']} | {s['web_fallback']} | {s['passed']} | {s['pass_rate']}% |"
            )
        elif has_web_fallback_stats:
            lines.append(
                f"| {cat} | {s['total']} | {s['strict']} | {s['negative_ok']} | {s['web_fallback']} | {s['passed']} | {s['pass_rate']}% |"
            )
        else:
            lines.append(
                f"| {cat} | {s['total']} | {s['strict']} | {s['negative_ok']} | {s['passed']} | {s['pass_rate']}% |"
            )

    # 未通过 / 异常列表
    failed = [
        r
        for r in results
        if r["status"] in ("miss", "false_positive", "web_fallback_failed", "web_fallback_not_triggered", "error")
    ]
    if failed:
        lines.extend([
            f"",
            f"## 未通过 / 异常用例详情",
            f"",
            f"| ID | 输入 | 类型 | 期望菜名 | 实际状态 | 说明 | match_mode |",
            f"|----|------|------|---------|---------|------|-----------|",
        ])
        for r in failed:
            expected = "、".join(r["expected_dish"]) if r["expected_dish"] else "（无）"
            lines.append(
                f"| #{r['id']} | {r['input'][:25]} | {r['eval_type']} | {expected[:20]} | {r['label']} | {r['status']} | {r['match_mode']} |"
            )

        lines.extend([
            f"",
            f"### 失败原因归类",
            f"",
        ])
        miss_strict = [r for r in results if r["status"] == "loose_hit" and r["strict_ok"]]
        miss_all = [r for r in failed if r["status"] == "miss"]
        false_positive_list = [r for r in failed if r["status"] == "false_positive"]
        web_fallback_failed_list = [
            r for r in failed if r["status"] in ("web_fallback_failed", "web_fallback_not_triggered")
        ]
        errors_list = [r for r in failed if r["status"] == "error"]
        if miss_strict:
            lines.append(f"- **应严格但仅宽松**（{len(miss_strict)} 条）：语义改写未命中或 match_mode=fuzzy 导致 strict_ok 条件不满足")
        if miss_all:
            lines.append(f"- **完全未命中**（{len(miss_all)} 条）：图谱中可能不存在该菜，或需要语义改写增强")
        if false_positive_list:
            lines.append(f"- **负样本误召回**（{len(false_positive_list)} 条）：边界/非菜谱查询返回了具体菜谱，需要收紧拒答或分类逻辑")
        if web_fallback_failed_list:
            lines.append(f"- **联网兜底失败/未触发**（{len(web_fallback_failed_list)} 条）：本地图谱未命中后没有成功取得网页搜索结果")
        if errors_list:
            lines.append(f"- **异常崩溃**（{len(errors_list)} 条）：需要修复代码 bug")

    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*报告由 run_recall_test.py 自动生成*")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 测试报告已写入：{path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="菜谱知识图谱查询测试")
    parser.add_argument(
        "--phase", choices=["1", "2", "all"], default="1",
        help="测试阶段：1=第一阶段核心50条, 2=第二阶段扩展50条, all=全量100条（默认: 1）",
    )
    args = parser.parse_args()

    phase = args.phase
    phase_label = {"1": "第一阶段（核心）", "2": "第二阶段（扩展）", "all": "全量"}.get(phase, phase)
    print(f"{'='*60}")
    print(f"  菜谱知识图谱查询测试 — {phase_label}")
    print(f"{'='*60}")

    results = run_phase(phase)
    stats = compute_stats(results)

    print(f"\n{'='*60}")
    print(f"  测试完成")
    print(f"  严格命中率：{stats['strict']}/{stats['total']} = {stats['strict_rate']}%")
    print(f"  总通过率：{stats['passed']}/{stats['total']} = {stats['pass_rate']}%")
    print(f"  联网兜底：{stats['web_fallback']} 成功 / {stats['web_fallback_failed']} 失败或未触发")
    print(f"  负样本误召回：{stats['false_positive']}")
    print(f"  异常：{stats['errors']}")
    print(f"{'='*60}")

    write_json(results, JSON_OUTPUT)
    write_report(stats, results, phase, REPORT_OUTPUT)

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
