# 多轮对话测试脚本生成提示词

你是一个执行型编码 agent。你的任务是严格按照下面规格，在当前项目中新增一套“真实 agent 多轮对话测试”脚本。不要自由发挥，不要修改无关文件，不要重构现有业务代码。

## 目标

新增一套多轮对话测试，用来测试真实 agent 行为的三大能力：

1. 能否正确记住历史信息。
2. 是否会被无关内容干扰。
3. 是否会出现逻辑自相矛盾。

测试必须走真实 agent 链路，不允许 mock agent，不允许只测底层图谱函数。

必须调用：

```python
from backend.agent_adapter_local_LLM_harness import stream_search_agent
```

每一轮对话都要真实执行：

- agent 模型调用
- `recipe_query_tool`
- `web_search_tool`
- 工具 trace
- 最终回答生成
- 历史回灌

允许真实联网。不要 mock `web_search_tool`。

## 必须新增的文件

新增两个源文件：

```text
test/multiturn_test_data.py
test/run_multiturn_dialogue_test.py
```

脚本运行后生成两个结果文件：

```text
test/.artifacts/multiturn_test_results.json
test/.artifacts/multiturn_test_report.md
```

不要删除或覆盖现有单轮测试文件。

## 运行方式

脚本应支持：

```powershell
python test/run_multiturn_dialogue_test.py --all
python test/run_multiturn_dialogue_test.py --category memory
python test/run_multiturn_dialogue_test.py --category distraction
python test/run_multiturn_dialogue_test.py --category contradiction
```

如果当前在 `test` 目录，也应支持：

```powershell
python run_multiturn_dialogue_test.py --all
```

脚本必须自动把项目根目录加入 `sys.path`。

## DeepSeek 裁判

需要实现混合裁判：

1. 规则断言。
2. DeepSeek LLM 裁判。

DeepSeek API key 从环境变量读取：

```text
DEEPSEEK_API_KEY
```

默认 API endpoint：

```text
https://api.deepseek.com/chat/completions
```

默认模型：

```text
deepseek-chat
```

允许通过环境变量覆盖：

```text
DEEPSEEK_API_BASE
DEEPSEEK_JUDGE_MODEL
```

DeepSeek 裁判失败策略：

- 如果没有 `DEEPSEEK_API_KEY`：agent 测试和规则断言照跑，LLM 裁判结果标记为 `judge_unavailable`，不要因为缺 key 直接失败。
- 如果配置了 key，但 API 报错或网络失败：该 case 的 LLM 裁判标记为 `judge_unavailable`，不要因为裁判不可用直接失败。
- 如果 DeepSeek 正常返回，且返回 `passed=false`：该 case 判定失败，并影响最终 exit code。
- 如果规则断言失败：该 case 直接失败，即使 LLM 裁判通过也不能覆盖规则失败。

## DeepSeek 裁判提示词要求

DeepSeek 裁判必须要求返回严格 JSON，不要 Markdown，不要解释性前后缀。

裁判输出 schema：

```json
{
  "passed": true,
  "score": 0.0,
  "failure_type": null,
  "reason": "一句中文原因"
}
```

字段要求：

- `passed`: boolean。
- `score`: 0 到 1 的数字。
- `failure_type`: 只能是 `memory_loss`、`distracted_by_irrelevant_content`、`contradiction`、`tool_misuse`、`unsupported_answer`、`other` 或 null。
- `reason`: 简短中文说明。

裁判时提供给 DeepSeek 的内容必须包含：

- case id
- category
- case description
- 全部 turns
- 每轮 user 输入
- 每轮 assistant 最终回答
- 每轮工具调用列表
- 每轮 trace 摘要
- 规则断言结果
- case 级 expected behavior
- case 级 forbidden behavior

DeepSeek 裁判系统提示词建议：

```text
你是一个严格的多轮对话测试裁判。你只判断 assistant 在给定对话中的行为是否符合测试目标。
不要根据你的常识补全事实，只根据对话、工具调用、trace 摘要、规则断言和期望行为判断。
如果规则断言已经失败，通常应判定失败。
如果回答没有继承历史指代、被无关内容带偏、或与前文/工具结果自相矛盾，应判定失败。
只输出严格 JSON，不要 Markdown，不要额外解释。
```

## 测试数据规模

第一版必须只做 9 个 case，每类 3 个。

三类 category 固定为：

```text
memory
distraction
contradiction
```

## 测试数据结构

在 `test/multiturn_test_data.py` 中定义：

```python
MULTITURN_TEST_CASES = [
    {
        "id": "memory_001",
        "category": "memory",
        "description": "...",
        "expected_behavior": "...",
        "forbidden_behavior": "...",
        "turns": [
            {
                "user": "...",
                "expect_tools": ["recipe_query_tool"],
                "expect_any_keywords": ["..."],
                "forbid_keywords": ["..."]
            }
        ]
    }
]
```

字段含义：

- `id`: 唯一 ID。
- `category`: `memory`、`distraction` 或 `contradiction`。
- `description`: case 描述。
- `expected_behavior`: case 级期望行为。
- `forbidden_behavior`: case 级禁止行为。
- `turns`: 多轮对话列表，至少 2 轮，最多 5 轮。
- `user`: 用户输入。
- `expect_tools`: 本轮期望出现的工具名列表，可为空。
- `expect_any_keywords`: 本轮回答或 trace 中至少命中一个的关键词，可为空。
- `expect_all_keywords`: 本轮回答或 trace 中必须全部命中的关键词，可选。
- `forbid_keywords`: 本轮回答中禁止出现的关键词，可为空。
- `expect_web_fallback`: 可选 boolean，表示本轮是否期望调用 `web_search_tool`。

## 必须包含的 9 个 case

### memory 类

1. `memory_001`
   - 目标：记住上一轮菜名。
   - turns:
     - `辣椒炒肉怎么做`
     - `刚才那道菜需要什么调料`
   - 期望：第二轮仍指向辣椒炒肉，不能换菜。

2. `memory_002`
   - 目标：记住上一轮菜，并回答属性追问。
   - turns:
     - `清蒸鲈鱼怎么做`
     - `它要蒸几分钟`
   - 期望：第二轮仍指向清蒸鲈鱼，并回答蒸制时间。

3. `memory_003`
   - 目标：记住联网兜底菜名。
   - turns:
     - `北京烤鸭怎么做`
     - `刚才那道菜主要难点是什么`
   - 期望：第一轮触发 `recipe_query_tool` 和 `web_search_tool`；第二轮仍指向北京烤鸭。

### distraction 类

4. `distraction_001`
   - 目标：中间闲聊不覆盖菜谱上下文。
   - turns:
     - `清蒸鲈鱼怎么做`
     - `今天天气不错`
     - `它要蒸几分钟`
   - 期望：第三轮仍指向清蒸鲈鱼，不被天气话题干扰。

5. `distraction_002`
   - 目标：非菜谱问题不覆盖上一道菜。
   - turns:
     - `小炒黄牛肉火候怎么控制`
     - `你是什么模型`
     - `刚才那道菜为什么要大火快炒`
   - 期望：第三轮仍指向小炒黄牛肉。

6. `distraction_003`
   - 目标：另一个无关联网问题不污染菜谱指代。
   - turns:
     - `糖醋里脊的调味汁怎么配`
     - `搜索一下今天的热门新闻`
     - `回到刚才那道菜，调味重点是什么`
   - 期望：第三轮仍指向糖醋里脊；第二轮可以联网，但不能覆盖菜谱上下文。

### contradiction 类

7. `contradiction_001`
   - 目标：纠正错误前提。
   - turns:
     - `小炒黄牛肉火候怎么控制`
     - `所以它是不是全程小火？`
   - 期望：第二轮必须否定“全程小火”，并说明有大火快炒等阶段。

8. `contradiction_002`
   - 目标：不能否认前文蒸制事实。
   - turns:
     - `清蒸鲈鱼怎么做`
     - `所以它是不是不用蒸？`
   - 期望：第二轮必须否定“不用蒸”，并说明需要蒸。

9. `contradiction_003`
   - 目标：不能接受明显错误的调味结论。
   - turns:
     - `糖醋里脊的调味汁怎么配`
     - `所以不用糖也可以？`
   - 期望：第二轮应指出糖是糖醋味的重要组成，不应直接同意“不用糖”。

## 历史回灌要求

脚本必须模拟真实多轮：

1. 第一轮调用 `stream_search_agent(user, history=[])`。
2. 收集该轮所有 event。
3. 拼接所有 `type == "content"` 的内容作为 assistant 最终回答。
4. 保存最后一次 `type == "trace"` 的 `rag_trace`。
5. 将用户消息和 assistant 消息追加到下一轮 history。

history 形状应兼容 `stream_search_agent`：

```python
history.append({"role": "user", "content": user_text})
history.append({"role": "assistant", "content": assistant_text, "rag_trace": rag_trace})
```

如果项目已有 `build_agent_history()` 需要的 session message 格式，也可以复用，但必须保证下一轮真实拿到上一轮 assistant 内容和 rag_trace。

## 事件收集要求

每一轮需要保存：

```python
{
    "turn_index": 1,
    "user": "...",
    "assistant": "...",
    "events": [...],
    "rag_trace": {...},
    "tool_calls": [...],
    "rule_assertions": [...]
}
```

`tool_calls` 从 `rag_trace["tool_calls"]` 中提取。

## 规则断言要求

每轮至少检查：

1. `expect_tools`: 期望工具是否都出现。
2. `expect_web_fallback`: 如果为 true，必须出现 `web_search_tool`。
3. `expect_any_keywords`: 如果非空，assistant 文本或 trace 文本中至少出现一个。
4. `expect_all_keywords`: 如果存在，assistant 文本或 trace 文本中必须全部出现。
5. `forbid_keywords`: assistant 文本中不能出现。

case 级还要检查：

- 如果 category 是 `memory`：最后一轮必须仍然提到或 trace 指向目标菜名。
- 如果 category 是 `distraction`：最后一轮不能把无关轮次主题当作当前主题。
- 如果 category 是 `contradiction`：最后一轮不能顺从用户错误前提。

规则断言的结果要结构化保存：

```json
{
  "name": "expect_tools",
  "passed": true,
  "detail": "..."
}
```

## 报告要求

`test/.artifacts/multiturn_test_report.md` 必须包含：

1. 测试时间。
2. 总 case 数。
3. 总通过率。
4. 按 category 的通过率。
5. 规则断言通过率。
6. LLM 裁判可用率。
7. DeepSeek 裁判通过率。
8. 每个失败 case 的摘要。
9. 每个 `judge_unavailable` case 的原因。
10. 明确标注：

```text
network_dependent: true
judge_model: deepseek-chat
```

## JSON 结果要求

`test/.artifacts/multiturn_test_results.json` 必须包含完整结构：

```json
{
  "summary": {
    "total": 9,
    "passed": 0,
    "failed": 0,
    "judge_unavailable": 0,
    "network_dependent": true
  },
  "cases": []
}
```

每个 case 至少包含：

- id
- category
- description
- turns
- rule_passed
- judge_result
- final_status
- elapsed

## 退出码要求

脚本 exit code：

- 所有规则断言通过，且所有可用的 DeepSeek 裁判通过：返回 0。
- 任一规则断言失败：返回 1。
- DeepSeek 正常返回且任一 case `passed=false`：返回 1。
- DeepSeek 不可用但规则断言全部通过：返回 0。

## 实现约束

- 不要改业务逻辑文件。
- 不要改现有单轮测试逻辑。
- 不要引入大型依赖；DeepSeek API 用标准库 `urllib.request` 或项目已有 HTTP 库。
- 输出 JSON 使用 `ensure_ascii=False`。
- 运行时设置或建议使用 `PYTHONIOENCODING=utf-8`。
- 捕获单个 case 异常，不要让整个脚本中途崩掉；异常 case 标记失败并继续跑后续 case。
- 所有网络/API 错误要写入结果文件。

## 完成后必须执行

至少执行：

```powershell
python -m py_compile test/run_multiturn_dialogue_test.py test/multiturn_test_data.py
```

如果环境中有可用模型服务和 DeepSeek key，再执行：

```powershell
python test/run_multiturn_dialogue_test.py --all
```

如果没有 DeepSeek key，也要运行一次 `--all`，确认 `judge_unavailable` 路径能正常生成报告。

## 最终回复要求

完成后只汇报：

- 新增了哪些文件。
- 是否跑过 py_compile。
- 是否跑过 `--all`。
- 通过率是多少。
- DeepSeek 裁判是否可用。
- 结果文件路径。

