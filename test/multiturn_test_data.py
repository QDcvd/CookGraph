#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮对话测试数据集 — 供 run_multiturn_dialogue_test.py 使用。

共 10 个 case，分三类：memory / distraction / contradiction。

注意：是否设置 expect_tools 取决于该轮是否是新的菜谱/联网任务。
如果后续轮次提出了新的菜名或新的菜谱请求，仍应设置 expect_tools。
"""

from typing import Any

MULTITURN_TEST_CASES: list[dict[str, Any]] = [
    # ═══════════════════════════════════════════
    # memory 类 — 记住历史信息
    # ═══════════════════════════════════════════
    dict(
        id="memory_001",
        category="memory",
        description="记住上一轮菜名——第二轮用'刚才那道菜'指代",
        expected_behavior="第二轮必须仍指向辣椒炒肉，不能换成其他菜",
        forbidden_behavior="第二轮换菜或回答其他不相关的菜",
        turns=[
            dict(
                user="辣椒炒肉怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["辣椒炒肉"],
                forbid_keywords=[],
            ),
            dict(
                user="刚才那道菜需要什么调料",
                expect_tools=[],
                expect_any_keywords=["辣椒炒肉"],
                forbid_keywords=["清蒸鲈鱼", "糖醋里脊", "小炒黄牛肉"],
            ),
        ],
    ),
    dict(
        id="memory_002",
        category="memory",
        description="记住菜名并回答属性追问——第二轮用'它'指代",
        expected_behavior="第二轮仍指向清蒸鲈鱼，回答蒸制时间",
        forbidden_behavior="第二轮答非所问或指代其他菜",
        turns=[
            dict(
                user="清蒸鲈鱼怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["清蒸鲈鱼"],
                forbid_keywords=[],
            ),
            dict(
                user="它要蒸几分钟",
                expect_tools=[],
                expect_any_keywords=["清蒸鲈鱼", "蒸"],
                forbid_keywords=["辣椒炒肉", "糖醋里脊"],
            ),
        ],
    ),
    dict(
        id="memory_003",
        category="memory",
        description="记住联网兜底菜名——第二轮追问难点",
        expected_behavior="第一轮触发 recipe_query_tool 和 web_search_tool；第二轮仍指向北京烤鸭",
        forbidden_behavior="第二轮忘记北京烤鸭，回答其他菜",
        turns=[
            dict(
                user="北京烤鸭怎么做",
                expect_tools=["recipe_query_tool", "web_search_tool"],
                expect_any_keywords=["北京烤鸭"],
                forbid_keywords=[],
                expect_web_fallback=True,
            ),
            dict(
                user="刚才那道菜主要难点是什么",
                expect_tools=[],
                expect_any_keywords=["北京烤鸭"],
                forbid_keywords=["清蒸鲈鱼", "辣椒炒肉"],
            ),
        ],
    ),
    # ═══════════════════════════════════════════
    # distraction 类 — 不被无关内容干扰
    # ═══════════════════════════════════════════
    dict(
        id="distraction_001",
        category="distraction",
        description="中间闲聊天气不覆盖菜谱上下文",
        expected_behavior="第三轮仍指向清蒸鲈鱼，不被天气话题干扰",
        forbidden_behavior="第三轮忘记清蒸鲈鱼或回答天气相关内容",
        turns=[
            dict(
                user="清蒸鲈鱼怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["清蒸鲈鱼"],
                forbid_keywords=[],
            ),
            dict(
                user="今天天气不错",
                expect_tools=[],
                expect_any_keywords=[],
                forbid_keywords=[],
            ),
            dict(
                user="它要蒸几分钟",
                expect_tools=[],
                expect_any_keywords=["清蒸鲈鱼", "蒸"],
                forbid_keywords=["天气", "晴天", "下雨"],
            ),
        ],
    ),
    dict(
        id="distraction_002",
        category="distraction",
        description="非菜谱问题不覆盖上一道菜",
        expected_behavior="第三轮仍指向小炒黄牛肉",
        forbidden_behavior="第三轮忘记小炒黄牛肉或回答模型身份问题",
        turns=[
            dict(
                user="小炒黄牛肉火候怎么控制",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["小炒黄牛肉", "火候"],
                forbid_keywords=[],
            ),
            dict(
                user="你是什么模型",
                expect_tools=[],
                expect_any_keywords=[],
                forbid_keywords=[],
            ),
            dict(
                user="刚才那道菜为什么要大火快炒",
                expect_tools=[],
                expect_any_keywords=["小炒黄牛肉", "大火", "快炒"],
                forbid_keywords=["我是", "模型", "AI"],
            ),
        ],
    ),
    dict(
        id="distraction_003",
        category="distraction",
        description="无关联网问题不污染菜谱指代",
        expected_behavior="第二轮回车联网搜索不覆盖菜谱上下文；第三轮仍指向糖醋里脊",
        forbidden_behavior="第三轮忘记糖醋里脊或回答新闻内容",
        turns=[
            dict(
                user="糖醋里脊的调味汁怎么配",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["糖醋里脊", "调味"],
                forbid_keywords=[],
            ),
            dict(
                user="搜索一下今天的热门新闻",
                expect_tools=["web_search_tool"],
                expect_any_keywords=[],
                forbid_keywords=[],
            ),
            dict(
                user="回到刚才那道菜，调味重点是什么",
                expect_tools=[],
                expect_any_keywords=["糖醋里脊", "调味", "糖醋"],
                forbid_keywords=["新闻", "热搜"],
            ),
        ],
    ),
    dict(
        id="distraction_004",
        category="distraction",
        description="顺序干扰与工具指令遵循——菜谱、天气、闲聊、荒诞菜、上下文那、新菜谱依次出现",
        expected_behavior=(
            "菜谱类轮次必须调用 recipe_query_tool；非菜谱闲聊不能调用菜谱工具；"
            "荒诞但形式明确的单菜谱问题应先查本地图谱，未命中后联网兜底；"
            "带'那'的新菜名清蒸鲈鱼不能被误解为钉子炒螺丝；最后凉拌黄瓜也必须走工具链"
        ),
        forbidden_behavior="天气/你好污染菜谱上下文，或未调用工具却凭常识编造菜谱做法",
        turns=[
            dict(
                user="告诉我西红柿炒鸡蛋怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["西红柿炒鸡蛋", "番茄炒蛋", "鸡蛋"],
                forbid_keywords=["钉子炒螺丝", "凉拌黄瓜", "清蒸鲈鱼"],
            ),
            dict(
                user="今天天气怎么样",
                expect_tools=[],
                forbid_tools=["recipe_query_tool"],
                expect_any_keywords=[],
                forbid_keywords=["西红柿炒鸡蛋", "番茄炒蛋", "钉子炒螺丝"],
            ),
            dict(
                user="你好",
                expect_tools=[],
                forbid_tools=["recipe_query_tool", "web_search_tool"],
                expect_any_keywords=[],
                forbid_keywords=["西红柿炒鸡蛋", "番茄炒蛋", "钉子炒螺丝"],
            ),
            dict(
                user="我想吃钉子炒螺丝",
                expect_tools=["recipe_query_tool", "web_search_tool"],
                expect_any_keywords=["钉子炒螺丝", "未找到", "本地图谱", "联网"],
                forbid_keywords=["根据本地菜谱图谱，钉子炒螺丝可以这样做"],
                expect_web_fallback=True,
            ),
            dict(
                user="那清蒸鲈鱼怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["清蒸鲈鱼", "鲈鱼", "蒸"],
                forbid_keywords=["钉子炒螺丝", "西红柿炒鸡蛋", "番茄炒蛋"],
            ),
            dict(
                user="告诉我，凉拌黄瓜的做法",
                expect_tools=["recipe_query_tool", "web_search_tool"],
                expect_any_keywords=["凉拌黄瓜", "未找到", "本地图谱", "联网"],
                forbid_keywords=["未启用 RAG", "根据本地菜谱图谱，凉拌黄瓜可以这样做"],
                expect_web_fallback=True,
            ),
        ],
    ),
    # ═══════════════════════════════════════════
    # contradiction 类 — 逻辑自洽
    # ═══════════════════════════════════════════
    dict(
        id="contradiction_001",
        category="contradiction",
        description="纠正错误前提——用户说'全程小火'",
        expected_behavior="第二轮必须否定'全程小火'，说明有大火快炒等阶段",
        forbidden_behavior="第二轮默认同意'全程小火'或回避回答",
        turns=[
            dict(
                user="小炒黄牛肉火候怎么控制",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["小炒黄牛肉", "火候"],
                forbid_keywords=[],
            ),
            dict(
                user="所以它是不是全程小火？",
                expect_tools=[],
                # 必须出现"大火"或"快炒"等说明火力分阶段的关键词
                expect_any_keywords=["大火", "快炒"],
                # 禁止同意"全程小火"——注意"是全程小火"匹配完整的同意句式
                forbid_keywords=["是全程小火"],
            ),
        ],
    ),
    dict(
        id="contradiction_002",
        category="contradiction",
        description="不能否认前文蒸制事实",
        expected_behavior="第二轮必须否定'不用蒸'，说明需要蒸",
        forbidden_behavior="第二轮默认同意'不用蒸'或回避回答",
        turns=[
            dict(
                user="清蒸鲈鱼怎么做",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["清蒸鲈鱼", "蒸"],
                forbid_keywords=[],
            ),
            dict(
                user="所以它是不是不用蒸？",
                expect_tools=[],
                expect_any_keywords=["需要蒸", "要蒸"],
                forbid_keywords=["不用蒸", "不需要蒸"],
            ),
        ],
    ),
    dict(
        id="contradiction_003",
        category="contradiction",
        description="不能接受明显错误的调味结论",
        expected_behavior="第二轮应指出糖是糖醋味的重要组成，不应直接同意'不用糖'",
        forbidden_behavior="第二轮默认同意'不用糖'或回避回答",
        turns=[
            dict(
                user="糖醋里脊的调味汁怎么配",
                expect_tools=["recipe_query_tool"],
                expect_any_keywords=["糖醋里脊", "调味"],
                forbid_keywords=[],
            ),
            dict(
                user="所以不用糖也可以？",
                expect_tools=[],
                expect_any_keywords=["需要糖", "要糖", "糖是"],
                forbid_keywords=["不用糖", "可以不用糖"],
            ),
        ],
    ),
]
