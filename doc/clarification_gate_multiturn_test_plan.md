# Clarification Gate 与多轮测试 v2 方案

## 背景

当前 agent 已经完成了本地图谱查询、反向查询、复合推荐、联网兜底、pending web search continuation 等多轮链路优化。继续在现有工具路由上增加零散规则，收益会下降，并且容易重新变成“遇到一个坏例子补一个补丁”。

下一阶段的核心目标是引入 **Clarification Gate**：当用户语义不确定、正反向意图冲突、菜名不稳定、或本地图谱未收录但尚未授权联网时，agent 应主动追问，而不是猜测、硬拆、硬路由或直接联网。

同时，多轮测试框架也要升级。旧版 `run_multiturn_dialogue_test.py` 默认每轮都应直接回答或直接调用工具；Clarification Gate 上线后，“主动追问”会成为正确行为，因此测试数据和断言模型必须支持对话状态机。

## 设计原则

1. 高确定性才执行工具。
2. 低确定性必须追问。
3. 本地图谱之外的信息不自动联网，除非用户明确授权。
4. 用户确认后必须继承上一轮 pending intent，不重新猜。
5. 不做具体菜名补丁，例如不硬编码“十豆=土豆”。
6. 多轮测试必须允许追问、确认、再执行的完整链路。

## Clarification Gate v1 边界

### 直接执行本地图谱查询

明确命中本地图谱菜名或别名时，直接调用 `recipe_query_tool`。

示例：

- `清蒸鲈鱼怎么做`
- `番茄炒蛋火力怎么控制`
- `西红柿炒鸡蛋怎么做`
- `小炒黄牛肉需要什么调料`

### 单个稳定食材反向查询

采用已确认的 C 策略：单个明确食材加“怎么做 / 多少种做法 / 可以做什么”时，默认归为反向食材查询。

示例：

- `牛肉怎么做`
- `土豆怎么做`
- `虾怎么做`
- `牛肉有多少种做法`

这些应执行本地图谱反向查询，而不是追问。

### 复合推荐直接执行

当用户表达的是“食材 + 口味/技法/菜系 + 推荐/有哪些”时，直接进入复合推荐或反向交集查询。

示例：

- `香辣口味的鸡肉有什么推荐`
- `川菜里有哪些牛肉菜`
- `蒸制的鱼有哪些`

### 本地未收录但菜名清晰

采用已确认的 B 策略：先查本地图谱；本地未收录时，先询问是否联网，不直接搜。

标准话术：

```text
我先查了本地菜谱图谱，暂时没有收录“凉拌牛肉”，所以不能直接给出确定做法。需要我帮你联网搜索一下吗？
```

示例：

- `凉拌牛肉怎么做`
- `冬菇滑鸡的具体做法`
- `土豆炖鸡怎么做`
- `北京烤鸭怎么做`

### 用户明确要求联网

如果用户已经明确要求联网，则直接调用 `web_search_tool`。

示例：

- `联网搜一下冬菇滑鸡怎么做`
- `网上查一下凉拌牛肉做法`
- `帮我搜索北京烤鸭怎么做`

### 疑似错别字或菜名不稳定

不自动纠错，不直接联网，不硬改菜名。应追问确认。

示例：

- `十豆炖鸡怎么做`
- `我的素材怎么做`

建议话术：

```text
我没能稳定识别“十豆炖鸡”。你是不是想问“土豆炖鸡”？确认后我再帮你查。
```

### 正向/反向意图冲突

如果句子既像具体菜名，又像“食材 + 口味”的推荐需求，应追问二选一。

示例：

- `香辣鸡肉怎么做`

建议话术：

```text
你是想查一道叫“香辣鸡肉”的具体做法，还是想找“香辣味 + 鸡肉”的菜品推荐？
```

### 无菜名属性问题

如果 session 有 `last_dish`，用当前 session 菜谱上下文补全。

如果没有 `last_dish`，追问菜名。

示例：

- `火力怎么控制`
- `注意事项`
- `要放什么调料`

## 模块设计

### `backend/clarification_gate.py`

新增 Clarification Gate 模块，负责工具调用前的意图判断。

建议数据结构：

```python
from dataclasses import dataclass, field
from typing import Any, Literal

ClarificationAction = Literal["execute", "ask", "reject"]
ClarificationIntent = Literal[
    "forward_recipe",
    "reverse_entity",
    "compound_recommendation",
    "recipe_attribute",
    "web_search",
    "non_recipe",
]
ClarificationConfidence = Literal["high", "medium", "low"]

@dataclass
class ClarificationDecision:
    action: ClarificationAction
    intent: ClarificationIntent
    confidence: ClarificationConfidence
    normalized_query: str = ""
    tool_name: str | None = None
    question: str = ""
    pending_type: str | None = None
    options: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
```

### `query_understanding.py`

保留，但职责收窄：

- 做候选抽取。
- 做实体、菜名、口味、技法、菜系特征识别。
- 不直接决定最终路由。

最终路由由 Clarification Gate 决定。

### `session_recipe_context.py`

扩展当前 session context，增加：

```json
{
  "pending_clarification": {
    "type": "clarify_forward_or_compound",
    "original_query": "香辣鸡肉怎么做",
    "question": "你是想查一道叫“香辣鸡肉”的具体做法，还是想找“香辣味 + 鸡肉”的菜品推荐？",
    "options": ["forward_recipe", "compound_recommendation"],
    "created_at": "..."
  }
}
```

已有 `pending_recipe_web_search` 继续保留，专门处理“本地未收录后用户确认联网”。

### `agent_adapter_local_LLM_harness.py`

preflight 阶段顺序调整为：

1. 如果用户回答的是 pending clarification，先解析并执行 pending intent。
2. 如果用户回答的是 pending web search，执行 `web_search_tool`。
3. 调用 Clarification Gate。
4. `action=ask`：直接返回追问，并写入 pending。
5. `action=execute`：执行工具。
6. `action=reject`：非菜谱或无法处理，礼貌说明。

### `recipe_query_adapter.py`

继续负责本地图谱查询、反向查询、复合推荐和查询结果结构化。

Clarification Gate 不替代图谱查询层，只负责“是否应该进入工具”和“以什么意图进入工具”。

## 多轮测试 v2

### 需要改造的原因

旧版多轮测试默认每轮应直接回答或直接调用工具。Clarification Gate 上线后，下列行为都可能是正确的：

- 主动追问。
- 请求联网确认。
- 等待用户确认 pending intent。
- 用户确认后再调用工具。

因此测试脚本和测试数据都要支持状态机。

### 新增字段

#### `expected_action`

用于声明本轮期望行为。

可选值：

```python
"tool" | "ask_clarification" | "answer" | "offer_web_search"
```

示例：

```python
dict(
    user="香辣鸡肉怎么做",
    expected_action="ask_clarification",
    expect_any_keywords=["具体做法", "菜品推荐", "香辣味", "鸡肉"],
    forbid_tools=["recipe_query_tool", "web_search_tool"],
)
```

#### `expect_pending_type`

用于断言本轮应写入某类 pending。

示例：

```python
expect_pending_type="clarify_forward_or_compound"
```

#### `resolves_pending`

用于声明本轮用户是在回答上一轮追问。

示例：

```python
dict(
    user="推荐菜",
    expected_action="tool",
    resolves_pending=True,
    expect_tools=["recipe_query_tool"],
)
```

#### `expect_offer_web_search`

用于断言本轮应请求联网确认，而不是直接联网。

示例：

```python
dict(
    user="凉拌牛肉怎么做",
    expected_action="offer_web_search",
    expect_tools=["recipe_query_tool"],
    forbid_tools=["web_search_tool"],
    expect_any_keywords=["本地菜谱图谱", "暂时没有收录", "需要我帮你联网搜索一下吗"],
)
```

#### `expect_no_web_before_confirmation`

用于更明确地表达“未授权联网前不能调用 web_search_tool”。

### Runner 修改点

`run_multiturn_dialogue_test.py` 需要修改：

1. `check_turn_assertions` 支持 `expected_action`。
2. 支持 `expect_pending_type`。
3. 支持 `resolves_pending`。
4. 支持 `expect_offer_web_search`。
5. 支持 `expect_no_web_before_confirmation`。
6. DeepSeek 裁判提示词增加“主动追问是合格行为”的说明。

### DeepSeek 裁判提示词更新

需要加入以下判断原则：

```text
当用户语义不确定、菜名不稳定、正反向意图冲突、本地未收录但未授权联网时，agent 主动追问或请求联网确认，应视为合格行为。
不要把“没有直接给出最终菜谱”自动判为失败；应先判断该轮是否应追问。
```

## 需要更新的旧测试

以下类型旧 case 可能需要拆成两轮：

- `北京烤鸭怎么做`
- `锅包肉怎么做`
- `冬菇滑鸡的具体做法`
- `凉拌牛肉怎么做`
- 任何“本地未收录后直接联网”的测试

新结构：

```text
用户：北京烤鸭怎么做
Agent：我先查了本地菜谱图谱，暂时没有收录“北京烤鸭”，所以不能直接给出确定做法。需要我帮你联网搜索一下吗？
用户：搜一下
Agent：调用 web_search_tool，并基于 evidence composer 回答或拒绝编造。
```

## 新增 v2 测试建议

### Case 1：清晰菜名本地未收录，先请求联网确认

```text
用户：凉拌牛肉怎么做
Agent：本地未收录，需要联网吗？
用户：搜一下
Agent：调用 web_search_tool
```

### Case 2：正向/复合推荐冲突，主动追问

```text
用户：香辣鸡肉怎么做
Agent：你是想查具体做法，还是找香辣味 + 鸡肉的菜品推荐？
用户：推荐菜
Agent：执行 compound_recommendation
```

### Case 3：疑似错别字，先确认

```text
用户：十豆炖鸡怎么做
Agent：我没能稳定识别“十豆炖鸡”。你是不是想问“土豆炖鸡”？
```

### Case 4：稳定食材直接反向查询

```text
用户：牛肉怎么做
Agent：执行 reverse_entity 查询，列本地图谱明确命中的牛肉菜。
```

### Case 5：无菜名属性问题

```text
用户：火力怎么控制
Agent：请先告诉我要查询哪道菜的火力控制。
```

### Case 6：已有上下文属性追问

```text
用户：清蒸鲈鱼怎么做
Agent：查本地图谱。
用户：火力怎么控制
Agent：用清蒸鲈鱼补全查询。
```

## 实施顺序

1. 修改 `run_multiturn_dialogue_test.py`，支持多轮测试 v2 字段。
2. 将少量新 v2 case 加入数据集，先验证 runner。
3. 新增 `backend/clarification_gate.py` 和单元测试。
4. 接入 agent preflight。
5. 更新旧多轮 case 中“本地未收录直接联网”的预期。
6. 跑单元测试、目标多轮 case、全量多轮测试。

## 非目标

本阶段不做：

- 跨 session pending。
- 自动把“十豆”改成“土豆”。
- 大模型自由纠错。
- 复杂网页正文抓取。
- 多搜索引擎 rerank。
- 完整事件图或长期记忆系统改造。

## 成功标准

1. 不确定语句会追问，而不是猜。
2. 清晰菜名本地未收录时先请求联网确认。
3. 用户确认后能继承 pending intent。
4. 反向查询、正向查询、复合推荐边界稳定。
5. 旧测试中正确的追问行为不再被误判失败。
6. 新增 v2 多轮 case 可以真实跑通。
