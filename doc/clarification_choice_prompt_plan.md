# Agent 反问选择框方案

## 背景

当前 `clarification_gate` 已经让 agent 在不确定时主动追问，例如：

- 本地图谱未收录明确菜谱时，询问是否联网搜索。
- 疑似错字菜名时，询问是否按候选菜名查询。
- “香辣鸡肉怎么做”这类正向菜谱/反向推荐冲突时，询问用户想查具体菜还是推荐菜。
- “火力怎么控制”这类缺少菜名的属性问题，要求用户补充菜名。

这些追问目前只通过自然语言表达。用户需要手动输入“是”“搜一下”“推荐菜”等，模型和测试 runner 都容易受到措辞差异影响。下一步优化是在前端为 agent 反问消息展示 A/B/C 选择框，提高确认动作的稳定性。

## 范围

只用于 agent 反问/确认场景。

包含：

- `ask_clarification`
- `offer_web_search`
- 菜名疑似错字确认
- 正向菜谱 / 反向推荐意图区分
- 缺少菜名的属性追问

不包含：

- 普通菜谱回答后的“下一步推荐”
- 普通菜品推荐列表
- 联网搜索结果总结后的继续追问
- 营销式快捷按钮
- 前端自行判断何时弹框

## 设计原则

1. 后端是唯一语义来源。
   前端不根据文本猜测是否展示按钮，只读取后端结构化字段。

2. 选择框不绕过 agent。
   用户点击 A/B 后，本质仍然是发送一条普通用户消息，复用现有 `/chat/stream`。

3. 老客户端可降级。
   即使前端不支持选择框，assistant 文本里仍保留自然语言问题。

4. 选择框只绑定当前 assistant 消息。
   不做跨会话 pending，不做全局浮层状态。

5. C 永远代表用户自行输入。
   C 不自动发送，只聚焦输入框，让用户补充更细内容。

## 后端协议

在 SSE `trace` 的 `rag_trace` 中新增可选字段 `choice_prompt`。

```json
{
  "choice_prompt": {
    "id": "clarify_20260709_abc123",
    "type": "web_search_confirm",
    "question": "需要我帮你到网上搜一下吗？",
    "options": [
      {
        "key": "A",
        "label": "是",
        "send_text": "是"
      },
      {
        "key": "B",
        "label": "不是",
        "send_text": "不是"
      },
      {
        "key": "C",
        "label": "我自己输入",
        "custom": true
      }
    ],
    "pending_type": "recipe_web_search_offer",
    "pending_payload": {
      "original_query": "凉拌牛肉怎么做"
    }
  }
}
```

### 字段说明

- `id`
  - 当前选择框唯一 id。
  - 用于前端记录按钮是否已点击。

- `type`
  - 选择框类型。
  - 第一批固定为：
    - `web_search_confirm`
    - `uncertain_dish_name`
    - `forward_or_recommendation`
    - `missing_recipe_target`

- `question`
  - 展示给用户的简短问题。
  - 应与 assistant 文本含义一致，但可以更短。

- `options`
  - A/B/C 选项。
  - A/B 必须有 `send_text`。
  - C 必须有 `custom: true`，不自动发送。

- `pending_type`
  - 与 `pending_clarification.type` 或 `pending_recipe_web_search.type` 对齐。

- `pending_payload`
  - 仅给前端展示/调试使用。
  - 真正执行仍以后端 session context 里的 pending 为准。

## 选择框类型

### 1. 联网搜索确认

触发场景：

- 本地图谱未收录明确单菜谱。
- 后端返回 `web_search_offer: True`。
- 用户尚未授权联网。

示例：

用户：`凉拌牛肉怎么做`

assistant 文本：

```text
本地菜谱图谱暂时没有稳定收录“凉拌牛肉”。需要我帮你到网上搜一下吗？
```

选项：

| key | label | send_text | 行为 |
| --- | --- | --- | --- |
| A | 是，帮我搜 | 是 | 触发上一轮原问题的 `web_search_tool` |
| B | 先不用 | 不是 | 清掉或忽略 pending，不联网 |
| C | 我自己输入 | - | 聚焦输入框 |

### 2. 疑似错字菜名确认

触发场景：

- `clarification_gate` 判断菜名不稳定。
- 不能自动纠错。

示例：

用户：`我想做十豆炖鸡，需要准备哪些调味料和配菜?`

assistant 文本：

```text
我没能稳定识别“十豆炖鸡”。你是不是想问“土豆炖鸡”？确认后我再帮你查。
```

选项：

| key | label | send_text | 行为 |
| --- | --- | --- | --- |
| A | 是，按土豆炖鸡查 | 是 | 用 `suggested_query` 查询本地图谱 |
| B | 不是 | 不是 | 不执行候选纠错，等待用户重新说明 |
| C | 我自己输入 | - | 聚焦输入框 |

### 3. 正向菜谱 / 反向推荐确认

触发场景：

- 用户说法像单菜谱，又像条件推荐。
- 例如 `香辣鸡肉怎么做`。

assistant 文本：

```text
你是想查一道叫“香辣鸡肉”的具体做法，还是想让我推荐香辣口味、含鸡肉的菜？
```

选项：

| key | label | send_text | 行为 |
| --- | --- | --- | --- |
| A | 查具体做法 | 具体做法 | 按单菜谱查询 |
| B | 推荐菜 | 推荐菜 | 按复合推荐/反向查询 |
| C | 我自己输入 | - | 聚焦输入框 |

### 4. 缺少菜名的属性追问

触发场景：

- 用户问 `火力怎么控制`、`注意事项`、`调料呢`。
- 当前 session 没有可靠最近菜品。

assistant 文本：

```text
可以的，你先告诉我是哪道菜，我再帮你查它的火力控制。
```

选项：

| key | label | send_text | 行为 |
| --- | --- | --- | --- |
| A | 补充菜名 | - | 聚焦输入框 |
| B | 取消 | 取消 | 不执行工具 |
| C | 我自己输入 | - | 聚焦输入框 |

说明：

- 这个类型没有稳定的 A 自动查询目标，因此 A 和 C 都是输入型动作。
- 前端可显示 A/B/C，但 A/C 都只聚焦输入框。

## 后端改造点

### 1. `backend/clarification_gate.py`

在 `ClarificationDecision` 中增加可选字段：

```python
choice_prompt: dict | None = None
```

或增加生成函数：

```python
build_choice_prompt(decision: ClarificationDecision) -> dict | None
```

推荐后者，避免 gate 的核心判定对象变胖。

### 2. `backend/agent_adapter_local_LLM_harness.py`

当 `_preflight_recipe_action()` 返回 `type=content` 且存在 `pending_clarification` 时：

- 写入 `trace["pending_clarification"]`
- 写入 `trace["choice_prompt"]`

当 recipe 工具返回 `web_search_offer: True` 时：

- 最终 answer 仍是当前自然语言确认。
- trace 中补充 `choice_prompt.type = web_search_confirm`。

注意：

- 如果工具已经执行了 `web_search_tool`，不能再展示 `web_search_confirm`。
- 如果用户点击 A 后已执行联网，后续 assistant 消息不再携带旧 `choice_prompt`。

### 3. `backend/session_recipe_context.py`

保留现有 pending 逻辑。

可选增强：

- session context 不必保存完整 `choice_prompt`。
- 只保存 pending 语义即可。
- choice prompt 每次由当前响应生成，避免旧 UI 状态污染后续轮次。

## 前端改造点

### 1. 类型定义

在 `frontend/src/types/chat.ts` 增加：

```ts
export interface ChoicePromptOption {
  key: 'A' | 'B' | 'C' | string;
  label: string;
  send_text?: string;
  custom?: boolean;
}

export interface ChoicePrompt {
  id: string;
  type: string;
  question: string;
  options: ChoicePromptOption[];
  pending_type?: string;
  pending_payload?: Record<string, unknown>;
}
```

并扩展：

```ts
export interface RagTrace {
  choice_prompt?: ChoicePrompt | null;
}

export interface Message {
  choicePrompt?: ChoicePrompt | null;
  selectedChoiceKey?: string | null;
}
```

### 2. Store 流式处理

在 `frontend/src/stores/chat.ts` 的 `data.type === 'trace'` 分支中：

```ts
this.messages[botMsgIdx].choicePrompt = data.rag_trace?.choice_prompt || null;
```

新增 action：

```ts
choosePromptOption(msgIndex: number, optionKey: string)
```

行为：

- 找到对应 message 的 `choicePrompt`。
- 找到 option。
- 如果 `custom: true`：
  - 设置 `selectedChoiceKey`
  - 聚焦输入框
  - 不发送。
- 如果有 `send_text`：
  - 设置 `selectedChoiceKey`
  - 把 `userInput = send_text`
  - 调用 `handleSend()`。

### 3. 组件

新增：

```text
frontend/src/components/Chat/ChoicePromptCard.vue
```

渲染位置：

- `MessageItem.vue`
- 在 `MessageContent` 下方、`TokenUsageBadge` 附近展示。

交互：

- A/B/C 使用按钮。
- 已选择后全部禁用。
- 加 hover/title。
- 移动端按钮纵向排列，桌面端横向排列。

### 4. 输入框聚焦

当前 `ChatInput.vue` 的 textarea ref 是局部的。

需要二选一：

方案 A：store 增加 `focusInputRequestedAt`，`ChatInput` watch 后聚焦。

方案 B：事件总线或 provide/inject。

推荐方案 A，因为 Pinia 已经是全局状态，最少引入新抽象。

## 测试集同步

### 多轮 runner 新字段

在 `test/run_multiturn_dialogue_test.py` 支持：

```python
expect_choice_prompt=True
expect_choice_type="web_search_confirm"
expect_choice_options=["A", "B", "C"]
choose_option="A"
```

规则：

- `expect_choice_prompt=True`
  - 断言 `rag_trace.choice_prompt` 存在。

- `expect_choice_type`
  - 断言 `choice_prompt.type` 一致。

- `expect_choice_options`
  - 断言 key 集合包含指定选项。

- `choose_option`
  - runner 不再使用硬编码下一轮用户文本。
  - 从上一轮 `choice_prompt.options` 中找 key。
  - 如果 option 有 `send_text`，作为下一轮输入。
  - 如果 option 是 `custom: true`，测试 case 必须提供 `custom_input`。

示例：

```python
dict(
    user="凉拌牛肉怎么做",
    expected_action="offer_web_search",
    expect_choice_prompt=True,
    expect_choice_type="web_search_confirm",
    expect_choice_options=["A", "B", "C"],
)
dict(
    choose_option="A",
    expected_action="tool",
    expect_tools=["web_search_tool"],
    expect_any_keywords=["凉拌牛肉"],
)
```

### 需要更新的 case

1. `memory_008`
   - `凉拌牛肉怎么做`
   - 第一轮期望 `web_search_confirm`
   - 第二轮模拟点击 A

2. `memory_007`
   - `十豆炖鸡`
   - 第一轮期望 `uncertain_dish_name`
   - 第二轮模拟点击 A

3. 新增 compound case
   - `香辣鸡肉怎么做`
   - 第一轮期望 `forward_or_recommendation`
   - 第二轮模拟点击 B 推荐菜

4. 新增 missing target case
   - `火力怎么控制`
   - 第一轮期望 `missing_recipe_target`
   - 第二轮模拟点击 C + `custom_input="清蒸鲈鱼"`

### 前端测试建议

如果当前项目没有前端测试框架，先不强行引入大型测试体系。

最低限度：

- TypeScript build 通过。
- 手动 Playwright 验证：
  - 反问消息出现 A/B/C。
  - 点击 A 自动发送。
  - 点击 C 聚焦输入框。
  - 点击后按钮禁用。

后续如果引入 Vitest，再补：

- `ChoicePromptCard` 组件单测。
- `chatStore.choosePromptOption` 单测。

## 验收标准

### 功能验收

- agent 反问时出现选择框。
- 非反问普通回答不出现选择框。
- 点击 A/B 会自动发送下一轮消息。
- 点击 C 不发送，只聚焦输入框。
- 旧的自然语言输入仍然有效。
- 刷新历史消息后，已存在的 choice prompt 能被展示，但已完成选择的按钮不应重新触发旧 pending。

### 行为验收

- `凉拌牛肉怎么做`：先请求联网确认，点击 A 后调用 `web_search_tool`，查询仍是原始问题。
- `十豆炖鸡`：先问是否为 `土豆炖鸡`，点击 A 后查 `土豆炖鸡`。
- `香辣鸡肉怎么做`：先询问具体做法还是推荐菜，点击推荐菜后走推荐/反向查询。
- `火力怎么控制`：没有上下文时要求补充菜名，不直接乱查。

### 测试验收

- 相关单元测试通过。
- memory 多轮测试通过。
- 新增 choice prompt 多轮测试通过。

## 实施顺序

1. 后端先生成 `choice_prompt`。
2. 多轮 runner 先支持 `expect_choice_prompt`，验证后端 schema。
3. 更新 `memory_007`、`memory_008` 和新增两个 choice case。
4. 前端增加类型、store action、`ChoicePromptCard`。
5. 手动跑前端验证。
6. 跑单元测试和多轮测试。

## 风险与规避

### 风险 1：按钮状态和后端 pending 不一致

规避：

- 后端 pending 是唯一真相。
- 按钮只是发送消息。
- 后端收到用户下一轮输入后正常解析 pending。

### 风险 2：用户点击旧消息按钮

规避：

- 前端对非最后一条 assistant 消息的 choice prompt 默认禁用。
- 或者点击时提示“这个选择已经不是当前问题了，请在输入框重新说明”。

推荐第一版采用：只有最后一条 assistant 消息的 choice prompt 可点击。

### 风险 3：测试 runner 和真实前端行为不一致

规避：

- runner 使用 `choice_prompt.options[*].send_text` 生成下一轮输入。
- 不在测试里手写“是/搜一下”，减少与前端点击行为偏差。

### 风险 4：C 选项语义过宽

规避：

- C 固定为 `custom: true`。
- 不携带 `send_text`。
- 测试中如需 C，必须显式提供 `custom_input`。

## 推荐决策

第一版采用：

- 消息内选择卡片，不用浏览器原生弹窗。
- 后端通过 `rag_trace.choice_prompt` 下发选项。
- 前端只渲染后端选项，不自行推断。
- 只允许最后一条 assistant 消息的选择框可点击。
- 测试 runner 模拟点击选项，而不是手写确认文本。

