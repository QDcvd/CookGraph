#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示完整上下文装配后的 mock 示例 — 模拟两轮对话后第三轮的上下文。"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 模拟：用户已问了两轮，现在第三轮问"它蒸多久"
from datetime import datetime

# ── 模拟持久化的消息 ──
messages = [
    {
        "type": "human",
        "content": "小炒黄牛肉怎么做",
        "timestamp": datetime.now().isoformat(),
    },
    {
        "type": "ai",
        "content": "小炒黄牛肉的做法：牛肉切片腌制，热锅凉油，大火快炒...",
        "timestamp": datetime.now().isoformat(),
        "rag_trace": {
            "tool_used": True,
            "tool_name": "recipe_query_tool",
            "tool_calls": [
                {
                    "tool_name": "recipe_query_tool",
                    "args": {"query": "小炒黄牛肉怎么做"},
                    "output_preview": "【小炒黄牛肉 完整档案】...牛肉切片腌制...",
                }
            ],
            "hybrid_retrieval": {
                "standard_dish": "小炒黄牛肉",
                "rewritten_query": "小炒黄牛肉怎么做",
            },
        },
    },
    {
        "type": "human",
        "content": "火候怎么控制",
        "timestamp": datetime.now().isoformat(),
    },
    {
        "type": "ai",
        "content": "小炒黄牛肉的火候很关键：大火快炒锁住肉汁...",
        "timestamp": datetime.now().isoformat(),
        "rag_trace": {
            "tool_used": True,
            "tool_name": "recipe_query_tool",
            "tool_calls": [
                {
                    "tool_name": "recipe_query_tool",
                    "args": {"query": "小炒黄牛肉的火力调节过程"},
                    "output_preview": "火力控制步骤：1.干煸辣椒中火3分钟；2.煸肥肉小火3分钟；3.爆香合炒大火1分钟；4.调味出锅大火2分钟",
                }
            ],
            "hybrid_retrieval": {
                "standard_dish": "小炒黄牛肉",
                "rewritten_query": "小炒黄牛肉的火力调节过程",
            },
        },
    },
]

# ── 模拟偏好记忆 ──
preferences = [
    {"kind": "dietary_restriction", "memory": "用户不能吃辣。"},
    {"kind": "default_cooking_goal", "memory": "偏好少油少盐。"},
]

# ── 模拟当前 session 菜谱上下文（已从 trace 更新） ──
recipe_context = {
    "last_dish": "小炒黄牛肉",
    "last_query": "小炒黄牛肉的火力调节过程",
    "last_recipe_tool_result_summary": "火力控制步骤：干煸辣椒中火3分钟，煸肥肉小火3分钟，爆香合炒大火1分钟，调味出锅大火2分钟",
    "last_tool_names": ["recipe_query_tool"],
    "updated_at": datetime.now().isoformat(),
}

# ── 模拟历史恢复（build_agent_history） ──
from backend.context_manager import build_agent_history, build_runtime_memory_context

history = build_agent_history(messages)
runtime_memory = build_runtime_memory_context(
    preferences=preferences,
    recipe_context=recipe_context,
)
if runtime_memory:
    history.insert(0, {"role": "runtime_memory", "content": runtime_memory})

# ── 模拟系统提示词 ──
from backend.agent_tools import _get_tools
from backend.agent_adapter_local_LLM_harness import (
    _build_tool_loop_system_prompt,
    _build_tool_loop_messages,
    _with_no_think,
)

system_prompt = _build_tool_loop_system_prompt(_get_tools())
context_summary = None
from backend.context_manager import history_context_summary
context_summary = history_context_summary(history)
if context_summary:
    system_prompt += "\n\n" + context_summary

# ── 最终模型看到的 messages ──
user_text = "它蒸多久"  # 第三轮用户输入

print("=" * 72)
print("  最终上下文 — 发给模型的消息结构")
print(f"  用户当前输入：{user_text}")
print("=" * 72)

print(f"\n{'─' * 72}")
print(f"[0] SystemMessage ({len(system_prompt)} chars)")
print(f"{'─' * 72}")
print(system_prompt[:1200])
if len(system_prompt) > 1200:
    print(f"...（截断，共 {len(system_prompt)} 字符）")

for i, msg in enumerate(history):
    role = msg["role"]
    content = msg.get("content", "")
    print(f"\n{'─' * 72}")
    print(f"[{i+1}] role={role} ({len(content)} chars)")
    print(f"{'─' * 72}")
    print(content[:500])
    if len(content) > 500:
        print(f"...（截断，共 {len(content)} 字符）")
    if msg.get("rag_trace"):
        calls = msg["rag_trace"].get("tool_calls", [])
        if calls:
            for c in calls[:2]:
                print(f"  → tool_call: {c.get('tool_name')}({c.get('args')})")

print(f"\n{'─' * 72}")
print(f"[{len(history)+1}] role=user ({len(user_text)} chars) — 当前输入")
print(f"{'─' * 72}")
print(_with_no_think(user_text))

print(f"\n{'=' * 72}")
print(f"  总计：1 条 SystemMessage + {len(history)} 条历史 + 1 条当前输入 = {len(history)+2} 条消息")
print(f"{'=' * 72}")
