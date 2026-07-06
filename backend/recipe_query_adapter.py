"""MiniCookingAgent-Demo 菜谱知识图谱查询适配器。

将 backend/4-V1菜谱查询recipe_query-查询火力.py 包装为
agent 可调用的工具函数，处理动态加载、缓存、stdout 捕获和异常兜底。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

from backend.recipe_semantic_retriever import RecipeSemanticMatch, semantic_match_recipe

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(__file__).resolve().parent / "4-V1菜谱查询recipe_query-查询火力.py"
DEFAULT_RECIPE_KG_PATH = PROJECT_ROOT / "config" / "chem+recipe_kg_updated_fire.pkl"

_recipe_module = None
_recipe_system = None


def _load_recipe_module():
    """动态加载菜谱查询脚本，返回 module 对象。"""
    global _recipe_module
    if _recipe_module is not None:
        return _recipe_module

    if not SCRIPT_PATH.is_file():
        raise FileNotFoundError(f"脚本不存在：{SCRIPT_PATH}")

    spec = importlib.util.spec_from_file_location(
        "recipe_query_script",
        str(SCRIPT_PATH),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载脚本：{SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    # 抑制脚本顶层 print（import 时不会触发 main，但防御性处理）
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)

    _recipe_module = module
    return module


def _get_recipe_system(kg_path: str | None = None) -> Any:
    """创建或返回缓存的 RecipeQuerySystem 实例。"""
    global _recipe_system
    if _recipe_system is not None:
        return _recipe_system

    resolved_path = Path(kg_path or DEFAULT_RECIPE_KG_PATH).resolve()

    module = _load_recipe_module()
    RecipeQuerySystem = module.RecipeQuerySystem

    # 初始化期间捕获 stdout，避免刷屏
    with contextlib.redirect_stdout(io.StringIO()):
        _recipe_system = RecipeQuerySystem(str(resolved_path))

    return _recipe_system


def _looks_like_reverse_recipe_query(query: str) -> bool:
    """识别不应改写成具体菜名的反向查询。"""
    reverse_patterns = [
        "哪些菜",
        "哪些菜式",
        "什么菜",
        "有什么菜",
        "有哪些菜",
        "哪道菜",
    ]
    return any(pattern in query for pattern in reverse_patterns)


def _kg_dish_names(system: Any) -> set[str]:
    """从现有图谱查询系统中读取标准菜名。"""
    executor = getattr(system, "executor", None)
    dish_nodes = getattr(executor, "dish_nodes", None)
    if isinstance(dish_nodes, dict):
        return {str(name) for name in dish_nodes.keys() if name}
    return set()


def _semantic_rewrite_query(query: str, system: Any) -> tuple[str, RecipeSemanticMatch | None, str | None]:
    """用本地 embedding 将自然菜名改写为图谱标准菜名查询。"""
    if _looks_like_reverse_recipe_query(query):
        return query, None, None

    try:
        match = semantic_match_recipe(query, allowed_dish_names=_kg_dish_names(system))
    except Exception as e:
        return query, None, f"混合召回跳过：{type(e).__name__}: {e}"

    if match is None:
        return query, None, None

    candidates = "；".join(f"{name}({score:.3f})" for name, score in match.candidates)
    if not match.accepted:
        note = (
            "混合召回未改写："
            f"top={match.dish_name} score={match.score:.3f} margin={match.margin:.3f}；"
            f"候选：{candidates}；{match.retrieval_debug}"
        )
        return query, match, note

    note = (
        "混合召回改写："
        f"原问题={query}；标准菜名={match.dish_name}；"
        f"命中文本={match.matched_text or '未定位'}；"
        f"score={match.score:.3f}；margin={match.margin:.3f}；"
        f"改写查询={match.rewritten_query}；候选：{candidates}；"
        f"{match.retrieval_debug}"
    )
    return match.rewritten_query, match, note


def _query_system(system: Any, query: str) -> dict | str:
    """执行图谱查询，并捕获 stdout。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return system.query(query)


def _result_is_success(result: dict) -> bool:
    """判断图谱查询结果是否成功。"""
    if result.get("success") is not None:
        return bool(result.get("success"))
    structured = result.get("structured")
    if isinstance(structured, dict) and structured.get("success") is not None:
        return bool(structured.get("success"))
    human = str(result.get("human_readable") or "")
    failure_markers = ["无法理解的查询格式", "未找到菜品", "未找到", "查询失败"]
    return bool(human.strip()) and not any(marker in human for marker in failure_markers)


def query_recipe_kg(query: str, kg_path: str | None = None) -> str:
    """查询本地菜谱知识图谱，返回适合 agent 使用的字符串结果。

    参数
    ----------
    query : str
        自然语言菜谱问题，例如"西红柿炒鸡蛋怎么做""小炒黄牛肉的火力调节过程"。
    kg_path : str, optional
        知识图谱 pkl 路径，默认使用 DEFAULT_RECIPE_KG_PATH。

    返回
    -------
    str
        适合大模型总结的文本结果，最长 4000 字符。
    """
    text = query.strip()
    if not text:
        return "菜谱查询失败：query 不能为空。"

    # 检查 KG 文件
    resolved_kg = Path(kg_path or DEFAULT_RECIPE_KG_PATH).resolve()
    if not resolved_kg.is_file():
        return f"菜谱查询失败：知识图谱文件不存在：{resolved_kg}"

    try:
        import networkx  # noqa: F401
    except ModuleNotFoundError:
        return "菜谱查询失败：缺少 networkx，请运行 pip install networkx"

    try:
        system = _get_recipe_system(str(resolved_kg))
    except ModuleNotFoundError as e:
        name = getattr(e, "name", str(e))
        return f"菜谱查询失败：缺少依赖模块 {name}，请运行 pip install {name}"
    except FileNotFoundError as e:
        return f"菜谱查询失败：{e}"
    except SystemExit:
        return "菜谱查询失败：查询脚本尝试退出进程，请检查知识图谱路径或配置。"
    except Exception as e:
        return f"菜谱查询失败：{type(e).__name__}: {e}"

    effective_query, semantic_match, semantic_note = _semantic_rewrite_query(text, system)

    # 执行查询，捕获 stdout
    try:
        result = _query_system(system, effective_query)
    except SystemExit:
        return "菜谱查询失败：查询脚本尝试退出进程，请检查知识图谱路径或配置。"
    except Exception as e:
        return f"菜谱查询失败：{type(e).__name__}: {e}"

    if not isinstance(result, dict):
        return f"菜谱查询失败：查询返回非字典类型: {type(result).__name__}"

    if (
        semantic_match is not None
        and semantic_match.accepted
        and effective_query != semantic_match.dish_name
        and not _result_is_success(result)
    ):
        try:
            fallback_result = _query_system(system, semantic_match.dish_name)
        except Exception:
            fallback_result = None
        if isinstance(fallback_result, dict) and _result_is_success(fallback_result):
            result = fallback_result
            fallback_note = f"图谱自校正：改写查询未命中，已退回标准菜名 {semantic_match.dish_name} 查询。"
            semantic_note = f"{semantic_note}；{fallback_note}" if semantic_note else fallback_note

    # 优先取 human_readable
    human = result.get("human_readable")
    if isinstance(human, str) and human.strip():
        parts = [human.strip()]

        # 附上少量结构化摘要
        structured = result.get("structured", {})
        summary_parts = []
        if result.get("success") is not None:
            summary_parts.append(f"success: {result['success']}")
        elif structured.get("success") is not None:
            summary_parts.append(f"success: {structured['success']}")
        if result.get("query_type"):
            summary_parts.append(f"query_type: {result['query_type']}")
        if result.get("match_mode"):
            summary_parts.append(f"match_mode: {result['match_mode']}")

        if summary_parts:
            parts.append("结构化摘要：\n" + "\n".join(summary_parts))

        output = "\n\n".join(parts)
    else:
        # 没有 human_readable，返回完整 JSON
        output = json.dumps(result, ensure_ascii=False, indent=2)

    # 限制长度
    if len(output) > 4000:
        output = output[:4000] + "\n...(截断)"

    if semantic_note:
        output += "\n\n语义召回摘要：\n" + semantic_note

    return output
