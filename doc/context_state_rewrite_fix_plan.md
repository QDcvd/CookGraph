# 多轮上下文状态与弱命中修复方案

## 背景

数据源：`test/testdata/memory.sqlite3`，按 `chat_sessions.updated_at desc` 读取最近三条多轮会话。

本次先把三条真实会话注册进 `test/multiturn_test_data.py`：

1. `memory_015`：鲜虾反向查询 -> 蒜蓉粉丝虾具体做法 -> 可乐鸡翅新菜。
   - 风险：明确新菜被上一道虾菜劫持。
2. `memory_016`：拉丝土豆 -> 酸甜排骨 -> 红烧肉 -> 酸辣土豆丝。
   - 风险：第一轮弱命中清炒土豆丝后，后续新菜继续被清炒土豆丝污染。
3. `memory_017`：生菜可以做什么 -> 具体菜谱是怎么样的。
   - 风险：反向查询给出唯一菜后，泛化追问没有继承该菜，反而把“具体菜谱是怎么样的”当作新菜联网搜索。

## 外部做法吸收

主流方案共同点不是给每种菜名加补丁，而是把用户最新输入先变成可检索的独立任务，再决定是否继承历史：

- LlamaIndex 的 `condense_question` 模式会先用对话上下文和最后一条消息生成 standalone question，再查 query engine；它也明确指出这种方式适合与知识库直接相关的问题。参考：<https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_question/>
- LangChain 的 context engineering 把会话历史、工具中间结果归入 dynamic runtime context；这些状态应作为可读写 state，而不是混进模型自由发挥。参考：<https://docs.langchain.com/oss/python/concepts/context>
- LangChain RAG 文档强调：检索上下文不包含答案时应承认不知道，并把 retrieved context 当作 data only。参考：<https://docs.langchain.com/oss/python/langchain/rag>
- Rasa forms/slots 的做法是显式维护 `requested_slot`/active loop；用户中断或换意图时要 deactivate/reset，不能让旧 pending 继续劫持后续轮次。参考：<https://legacy-docs-oss.rasa.com/docs/rasa/forms/>

落到本项目，合适的方向是“已有模块内的状态机收束”：

- `context_followup_gate.py`：负责把最新用户输入判定为 `inherit` 或 `new_task`，并在允许继承时生成 standalone query。
- `session_recipe_context.py`：负责保存和清理当前 session 内可继承的菜谱上下文。
- `recipe_semantic_retriever.py`：负责判定本地图谱命中是否强到可以改写 query。
- `agent_adapter_local_LLM_harness.py`：只做编排，把历史状态传给 gate，不承载更多业务判断。

## 设计原则

1. 当前轮优先。
   用户输入包含明确新菜名、新食材、新做法请求时，必须按当前轮重新路由。

2. 只有强指代才能继承。
   允许继承的输入包括：
   - 明确指代：它、这道菜、刚才那道菜、上面那个。
   - 短属性片段：火力、调料、注意事项、具体做法。
   - 泛化追问：具体菜谱是怎么样的、那具体怎么做、步骤呢。

3. 继承对象必须可信。
   可以继承：
   - 上一轮明确成功的 `standard_dish`。
   - 上一轮反向查询结果里唯一明确菜名，或用户刚选中的菜名。
   不可以继承：
   - semantic retrieval `accepted=False` 的 `top`。
   - web 搜索结果里的弱相关标题。
   - 多个候选菜但用户未选择的推荐列表。

4. 弱命中不改写。
   如果 query 中只有食材片段命中，比如“土豆”命中“清炒土豆丝”，但原 query 还带有未保留限定词，如“拉丝/酸辣/酸甜/可乐/红烧”等，应视为未知单菜谱，进入本地未命中 + 联网兜底，而不是替换成图谱中相似菜。

5. pending 只消费一次。
   澄清、联网确认、推荐选择都必须是最近一条 assistant 的 pending；用户选择后立即消费，后续新问题不能再被旧 pending 劫持。

## 实施计划

### 1. 扩展 `session_recipe_context`

在现有 `recipe_context` 中增加轻量字段，不引入新模块：

- `last_reverse_candidates`: 最近一次反向查询明确列出的菜名列表。
- `last_reverse_source_query`: 产生这些候选的用户 query。

更新规则：

- recipe trace 中如果是反向查询，并且结果列出本地图谱菜名，则写入 `last_reverse_candidates`。
- 如果只有 1 个候选，允许后续“具体菜谱/具体做法/步骤呢”继承它。
- 如果当前轮是明确新任务、联网搜索、未命中未知菜，清空不再相关的 reverse candidates。

### 2. 收紧 `context_followup_gate`

保留现有执行器，但增强输入：

```python
decide_context_followup(
    user_text,
    last_dish=...,
    last_reverse_candidates=...,
)
```

新增判定：

- `具体菜谱是怎么样的`、`具体做法`、`步骤呢` 属于泛化属性追问。
- 若有 `last_dish`，优先继承 `last_dish`。
- 若没有 `last_dish` 且 `last_reverse_candidates` 只有一个，继承该菜。
- 若 `last_reverse_candidates` 多于一个，必须追问用户选哪道菜，不能猜。

### 3. 收紧 `recipe_semantic_retriever`

把当前的短片段保护升级为“限定词保真”：

- 当 `matched_text` 不是完整菜名/别名，仅是食材或短片段时，检查 query 中是否存在没有被候选菜名覆盖的限定词。
- 限定词包括口味、技法、品牌/特殊做法、非图谱菜名词根，例如：拉丝、酸辣、酸甜、可乐、红烧、拔丝、糖醋、凉拌等。
- 如果限定词没有出现在候选菜名或候选菜谱文档中，则 `accepted=False`。

目标不是列无限词表，而是将词表作为“限定词类别”的最小实现，并配合现有 dense/lexical/alias 分数使用。

### 4. 保持 harness 编排简洁

`agent_adapter_local_LLM_harness.py` 只增加状态读取：

- 从 history 的 `rag_trace` 里恢复 `last_reverse_candidates`。
- 调用 `decide_context_followup`。
- 如果 gate 返回 inherit，就执行对应工具。
- 如果 gate 返回 new_task，就进入现有 clarification/query understanding 流程。

### 5. 测试策略

先不立即跑测试。完成代码后，先按三条真实会话风格扩充到 10 条，再统一验证。

扩充方向：

1. 反向食材 -> 唯一菜 -> 泛化追问。
2. 反向食材 -> 多个菜 -> 泛化追问，必须追问选择。
3. 已查一道菜 -> 明确新未知菜，不能继承旧菜。
4. 弱食材片段 + 限定词，不能改写成本地图谱相似菜。
5. 未命中联网后 -> 属性追问，应继承联网主题。

## 完成标准

- 最近三条真实会话注册为测试。
- 总计扩充到 10 条同风格多轮测试。
- 新增测试覆盖：
  - 明确新菜覆盖旧上下文。
  - 反向唯一候选可继承。
  - 反向多候选需追问。
  - 弱命中限定词不丢失。
  - pending 一次性消费。
- `python test/run_multiturn_dialogue_test.py --category memory --case-timeout 120 --no-llm-tunnel` 通过。
- 相关单元测试通过。
