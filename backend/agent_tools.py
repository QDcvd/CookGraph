"""MiniCookingAgent-Demo 的 Agent 工具定义。

导出当前注册工具（web_search_tool、recipe_query_tool）。
find_tool/read_file_tool 暂时保留实现但不注册，后续需要本地文件能力时可重新启用。
以及在工具执行过程中用到的文件搜索辅助函数。
"""

import fnmatch
import os
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from backend.recipe_query_adapter import query_recipe_plan
from backend.tool_result import error_result, make_tool_result

# 搜索时跳过的目录
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}
MAX_SEARCH_SECONDS = 5.0       # 单次搜索超时（秒）
MAX_VISITED_FILES = 90000      # 最多遍历文件数
MAX_MATCHES = 800              # 搜索结果上限
MAX_RETURN_CHARS = 2000        # 返回结果最大字符数
MAX_READ_CHARS = 4000          # 读取文件时最大字符数


def _format_limit_notes(
    skipped_dirs: int,
    timed_out: bool,
    hit_file_limit: bool,
    hit_match_limit: bool,
) -> list[str]:
    """生成搜索限制说明文本。"""
    notes = []
    if skipped_dirs:
        notes.append(f"跳过 {skipped_dirs} 个依赖/缓存/隐藏目录。")
    if timed_out:
        notes.append(f"达到 {MAX_SEARCH_SECONDS:.0f} 秒搜索时间限制，已停止。")
    if hit_file_limit:
        notes.append(f"已遍历 {MAX_VISITED_FILES} 个文件，达到上限。")
    if hit_match_limit:
        notes.append(f"匹配数达到 {MAX_MATCHES} 个上限。")
    return notes


def _extract_paths_from_tool_text(text: str) -> list[str]:
    """从 find_tool 返回文本中提取文件路径。"""
    paths = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("...") or stripped.startswith("Skipped "):
            continue
        if stripped.startswith("Stopped ") or stripped.startswith("No files matched"):
            continue
        if "\\" in stripped or "/" in stripped:
            paths.append(stripped)
    return paths


@tool
def find_tool(path: str = ".", pattern: str = "*") -> str:
    """按文件名通配符在真实本地目录中查找文件；path 必须是存在目录如 G:\\ 或 G:\\project，pattern 必须是文件名 glob 如 *.md、package.json、*blog*，不要传自然语言。"""
    try:
        root = Path(path).expanduser()
        if not root.exists():
            return f"目录不存在：{path}"
        if not root.is_dir():
            return f"路径不是目录：{path}"

        deadline = time.monotonic() + MAX_SEARCH_SECONDS
        visited = 0
        skipped_dirs = 0
        timed_out = False
        hit_file_limit = False
        files: list[Path] = []

        for current_root, dirs, filenames in os.walk(root, topdown=True):
            original_dir_count = len(dirs)
            dirs[:] = [
                dirname
                for dirname in dirs
                if dirname not in EXCLUDED_DIRS and not dirname.startswith(".")
            ]
            skipped_dirs += original_dir_count - len(dirs)

            for filename in filenames:
                if time.monotonic() > deadline:
                    timed_out = True
                    break

                visited += 1
                if visited > MAX_VISITED_FILES:
                    hit_file_limit = True
                    break

                if fnmatch.fnmatch(filename, pattern):
                    files.append(Path(current_root) / filename)
                    if len(files) >= MAX_MATCHES:
                        break

            if timed_out or hit_file_limit or len(files) >= MAX_MATCHES:
                break

        lines = [str(p) for p in files[:50]]
        result = "\n".join(lines)
        if not result:
            result = f"路径 {path} 下未匹配到 {pattern}。"
        if len(result) > MAX_RETURN_CHARS:
            result = result[:MAX_RETURN_CHARS] + "\n...(截断)"
        if len(files) > 50:
            result += f"\n... 共 {len(files)} 个匹配，仅显示前 50 个。"

        notes = _format_limit_notes(
            skipped_dirs=skipped_dirs,
            timed_out=timed_out,
            hit_file_limit=hit_file_limit,
            hit_match_limit=len(files) >= MAX_MATCHES,
        )
        if notes:
            result += "\n" + "\n".join(notes)
        return result
    except PermissionError as e:
        return f"权限不足：{e}"
    except Exception as e:
        return f"搜索失败：{e}"


@tool
def read_file_tool(path: str) -> str:
    """读取一个真实存在的本地文本文件；path 必须是完整文件路径，不要传目录或自然语言问题。"""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return f"文件不存在：{path}"
        if not p.is_file():
            return f"路径不是文件：{path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_READ_CHARS:
            content = content[:MAX_READ_CHARS] + "\n...(截断)"
        return content
    except Exception as e:
        return f"读取失败：{e}"


@tool
def web_search_tool(query: str) -> dict:
    """联网搜索公开网页信息。query 应是搜索关键词或问题，不要传本地路径或文件名通配符。

    使用规则：
    - 只有当用户明确要求"网络搜索""网页搜索""联网查询""搜一下""搜索一下""查一下""最新信息""网上查"时，才调用此工具。
    - 当 recipe_query_tool 返回的结果包含"web_search_offer: True"且用户同意联网时，可以调用此工具。
    - 普通菜谱做法、配料、火候、食材等问题，应先用 recipe_query_tool，不要直接用此工具。
    - 如果用户只是问"怎么做""如何做""具体做法"，没有说要联网搜索，不要调用此工具。
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return error_result(
            tool="web_search_tool",
            query_type="web_search",
            code="DEPENDENCY_MISSING",
            message="网络搜索暂不可用：缺少 ddgs 包。",
            detail="请安装 ddgs 后重试。",
            source="public_web",
        )

    try:
        results = list(DDGS(timeout=15).text(query, max_results=5))
    except Exception as e:
        return error_result(
            tool="web_search_tool",
            query_type="web_search",
            code="SEARCH_FAILED",
            message="网络搜索失败，请稍后重试。",
            detail=f"{type(e).__name__}: {e}",
            source="public_web",
        )

    clean_results = []
    for item in results:
        title = str(item.get("title") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        body = str(item.get("body") or item.get("snippet") or "").strip()
        if title or url or body:
            clean_results.append((title, url, body))

    if not clean_results:
        return make_tool_result(
            tool="web_search_tool",
            query_type="web_search",
            ok=False,
            source="public_web",
            message="网络搜索没有返回内容。",
            meta={"query": query},
        )

    search_results = [
        {"title": title, "url": url, "snippet": body}
        for title, url, body in clean_results
    ]
    return make_tool_result(
        tool="web_search_tool",
        query_type="web_search",
        ok=True,
        source="public_web",
        data={"query": query, "results": search_results},
        message=f"已找到 {len(search_results)} 条网页结果。",
        meta={"result_count": len(search_results)},
    )


@tool
def recipe_query_tool(plan: dict) -> dict:
    """查询本地菜谱知识图谱。plan 传结构化查询参数，不要传自然语言。

    以下是支持的计划结构。你只能在下方列出的结构中选择，不能自己发明格式。

    ===== 模式1: dish — 查某道菜的做法/属性/完整档案 =====
    {{"intent": "dish_detail_query", "mode": "dish", "dish": "菜名",
      "field": "属性名（可选）", "show_all": true（可选）}}

    field 可选值：full_recipe, method, prep, cooking_process, fire, tips,
    ingredients, seasonings, techniques, existence, count, cooking_method,
    prep_process, cooking_tips, fire_control_process。

    示例：
    查"小炒黄牛肉"的做法 → {{"intent": "dish_detail_query", "mode": "dish", "dish": "小炒黄牛肉", "field": "cooking_process"}}
    查"清蒸鲈鱼"完整档案 → {{"intent": "dish_detail_query", "mode": "dish", "dish": "清蒸鲈鱼", "show_all": true}}

    ===== 模式2: combo — 组合条件查询 =====
    {{"intent": "ingredient_combo_query", "mode": "combo",
      "ingredients": ["食材1", "食材2"], "technique": "技法", "taste": "味道",
      "cuisine": "菜系", "exclude": ["排除项"]}}

    示例：
    哪些菜用了牛肉 → {{"intent": "reverse_entity_query", "mode": "combo", "ingredients": ["牛肉"]}}
    牛肉配芥兰做什么 → {{"intent": "ingredient_combo_query", "mode": "combo", "ingredients": ["牛肉", "芥蓝"]}}

    ===== 模式3: missing — 缺失食材查询 =====
    {{"intent": "missing_ingredients_query", "mode": "missing",
      "dish": "菜名", "ingredients": ["已有食材1", "已有食材2"]}}

    ===== 通用规则 =====
    1. dish 字段必须填图谱标准菜名，不要填用户原话。
    2. 不要把不存在的字段塞进 plan。
    3. 如果 plan 结构不被支持，返回错误，不要自己重新解释成自然语言。
    """
    if not isinstance(plan, dict):
        return error_result(
            tool="recipe_query_tool",
            query_type="invalid_plan",
            code="PLAN_NOT_OBJECT",
            message="菜谱查询参数无效：plan 必须是对象。",
            detail=f"收到 {type(plan).__name__}",
            source="local_kg",
        )
    return query_recipe_plan(plan)


def _get_tools() -> list[Any]:
    """返回暴露给 Agent 的工具列表。"""
    return [web_search_tool, recipe_query_tool]
