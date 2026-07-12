#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实模型复放一组面向用户的 30 轮菜谱问答。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from replay_session import load_project_env, start_tunnel

load_project_env()
from backend.agent_adapter_local_LLM_harness import stream_search_agent


QUESTIONS = [
    "菜谱一共收录了多少菜",
    "牛肉有多少种做法",
    "菜心有多少种做法",
    "告诉我西红柿炒鸡蛋怎么做",
    "它的火力怎么控制",
    "我有辣椒和牛肉，今晚可以煮什么？",
    "我有西红柿和鸡蛋，可以做什么菜",
    "牛肉和土豆可以做哪些菜",
    "辣椒和肉可以煮什么",
    "我是说猪肉",
    "冰箱里有红萝卜、土豆、瘦肉，可以煮什么菜",
    "给我其中一道完整菜谱",
    "芥兰炒牛肉，菜谱有收录吗",
    "芥蓝牛肉怎么做",
    "芥兰有多少种做法",
    "今天天气热，适合吃什么菜",
    "川味的牛肉有没有推荐",
    "明天想吃清淡一点的菜",
    "我只有蒜苔，能做什么",
    "蒜苔炒肉怎么做",
    "这道菜火候怎么控制",
    "备菜过程是什么",
    "下锅的顺序呢",
    "今天几号",
    "能做什么",
    "豆腐和虾可以做什么菜",
    "我有猪肉，但不想吃辣，有什么推荐",
    "上一道菜要放多少盐",
    "番茄炒蛋有没有收录",
    "谢谢",
]


async def main() -> None:
    tunnel = start_tunnel(os.environ)
    history: list[dict] = []
    records = []
    try:
        for index, question in enumerate(QUESTIONS, start=1):
            answer = ""
            trace = None
            events = []
            try:
                async for event in stream_search_agent(question, history):
                    events.append(event)
                    if event.get("type") == "content":
                        answer += str(event.get("content") or "")
                    elif event.get("type") == "trace":
                        trace = event.get("rag_trace")
            except Exception as exc:
                answer = f"[ERROR] {type(exc).__name__}: {exc}"

            router = trace.get("query_router", {}) if isinstance(trace, dict) else {}
            print(f"[{index:02d}] 用户：{question}")
            print(f"     回答：{answer[:260].replace(chr(10), ' | ')}")
            print(f"     路由：{router.get('action')} / {router.get('tool_name')} / {router.get('reason')}")
            records.append({
                "turn": index,
                "user": question,
                "assistant": answer,
                "query_router": router,
                "tool_calls": (trace or {}).get("tool_calls", []) if isinstance(trace, dict) else [],
                "event_types": [event.get("type") for event in events],
            })
            history.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer, "rag_trace": trace},
            ])
    finally:
        if tunnel:
            tunnel[0].shutdown()
            tunnel[0].server_close()
            tunnel[1].close()

    output = Path(__file__).with_name(".artifacts") / "realistic_30_turn_replay_result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"replayed_at": datetime.now().isoformat(), "turns": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[输出] {output}")


if __name__ == "__main__":
    asyncio.run(main())
