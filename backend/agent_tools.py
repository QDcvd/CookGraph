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

from backend.recipe_query_adapter import query_recipe_kg

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
def web_search_tool(query: str) -> str:
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
        return "网络搜索失败：缺少 ddgs 包。请运行 pip install ddgs。"

    try:
        results = list(DDGS(timeout=15).text(query, max_results=5))
    except Exception as e:
        return f"网络搜索失败：{type(e).__name__}: {e}"[:MAX_RETURN_CHARS]

    clean_results = []
    for item in results:
        title = str(item.get("title") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        body = str(item.get("body") or item.get("snippet") or "").strip()
        if title or url or body:
            clean_results.append((title, url, body))

    if not clean_results:
        return "网络搜索没有返回内容。"

    lines = [f"搜索结果：{query}"]
    for index, (title, url, body) in enumerate(clean_results, start=1):
        lines.append(f"{index}. {title or '无标题'}")
        if url:
            lines.append(f"链接：{url}")
        if body:
            lines.append(f"摘要：{body}")
    return "\n".join(lines)[:MAX_RETURN_CHARS]


@tool
def recipe_query_tool(query: str) -> str:
    """查询本地菜谱知识图谱。query 传用户的自然语言问题即可。

    必须使用此工具的时机：
    - 用户询问某道菜的"做法""怎么做""如何做""步骤""具体做法""烹饪方法"
    - 用户询问某道菜的"配料""食材""调料""用料""需要什么材料"
    - 用户询问某道菜的"火候""火力""火力调节""怎么控制火候"
    - 用户询问某道菜的"备菜""备菜过程""提前准备什么"
    - 用户询问某道菜的"下锅顺序""下锅步骤""烹饪过程"
    - 用户询问"哪些菜用了某食材""哪些菜是某口味""有什么菜推荐"
    - 用户直接说出菜名（"辣椒炒肉""清蒸鲈鱼""小炒鸡"等）
    - 用户说"想吃某道菜""介绍一下某道菜"
    - 用户说的菜名即使不在本地图谱中，也应先用此工具查询

    不需要调用此工具的场景：
    - 用户只是打招呼、问天气、问模型身份等非菜谱问题
    - 用户明确要求联网搜索、搜索最新信息

    注意：
    - 如果用户继续追问上一道菜（如"它蒸多久""火力如何""刚才那道菜"），仍应调用此工具，query 中带上菜名。
    - 如果用户提到一个**新的菜名**（如"小炒鸡怎么做""红烧排骨怎么做"），应以新菜名作为查询重点，不要沿用历史菜名。
    - query 直接传用户的自然语言问题即可，不要自行改写或删减。
    """
    return query_recipe_kg(query)


def _get_tools() -> list[Any]:
    """返回暴露给 Agent 的工具列表。"""
    return [web_search_tool, recipe_query_tool]
