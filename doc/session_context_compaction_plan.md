# Session Context Compaction Plan

## 背景

当前项目已经有 Zleap-lite 记忆：

- `preference_memory`：跨会话保留用户偏好。
- `recipe_context_json`：当前 session 内保留最近菜品、最近工具摘要等菜谱上下文。
- `build_agent_history()`：每轮只取最近 12 条消息，并把历史 `rag_trace` 展开为工具上下文。

这套机制能解决近期多轮指代，但还没有真正的“上下文接近模型窗口时自动压缩”。当对话很长、工具结果较多时，当前做法主要靠固定窗口裁剪，旧消息会直接淡出模型上下文，缺少一个 session 内的压缩摘要来承接远历史。

本方案借鉴 `E:\Zleap-Agent` 的主对话 compaction 思路，但不完整移植事件图。第一版目标是：**当即将发送给模型的上下文长度接近 token 上限时，把当前 session 的远历史折叠成结构化 `conversation_summary`，保留最近上下文和关键证据引用。**

## 已确认决策

- 作用域：只做当前 session，不跨会话复用。
- 触发时机：本轮回答前触发。
- 触发阈值：估算输入上下文达到 `MAX_MODEL_LEN * 0.93` 时触发。
- 压缩模型：复用当前 LLM 生成结构化摘要。
- 失败策略：压缩失败不阻塞本轮回答，写入规则兜底摘要并推进游标。
- 存储位置：给 `chat_sessions` 增加独立 conversation summary 字段，不塞进 `recipe_context_json`。
- 保留窗口：最近 6 轮自然对话 + 最近 2 组工具结果 + 当前用户最新问题 + runtime memory 原样保留。
- 摘要合并：增量合并，用 `summary_until_message_id` 记录已经压缩到哪里。
- 纠错策略：只保留纠错后的结论，不保留错误回答原文。
- 工具证据：摘要里只保留引用，不保留完整工具结果。
- 前端可见性：默认只显示一条轻提示，不展示完整摘要。

## 压缩原则

### 1. 受保护消息：绝不压缩

这些内容必须原样保留或由专门 runtime memory 保证：

- 当前用户最新问题。
- system prompt 和工具协议。
- runtime memory 中的关键规则。
- 当前 session 的最近菜品上下文。
- 用户明确偏好。
- 最近 6 轮自然对话。
- 最近 2 组工具调用结果，尤其是最新菜谱命中或联网兜底结果。

### 2. 非关键状态：可摘要压缩

这些内容可以被归纳为结构化摘要：

- 普通闲聊。
- 中间解释。
- 已被结构化记忆覆盖的内容。
- 重复的过程性描述。
- 与当前任务关联较弱的历史问答。

### 3. 记忆片段：可替换为检索线索

旧工具结果、旧菜谱详情、旧联网结果不再把全文塞入模型上下文，而是压成：

- 用户问过什么。
- 命中过什么菜。
- 调用了什么工具。
- 当时的可靠结论是什么。
- 完整证据可从哪条 `chat_messages.rag_trace_json` 追溯。

### 4. 远历史对话：优先舍弃或折叠

与当前任务无关、较早的对话优先折叠进 `conversation_summary_json`。如果摘要本身继续增长，再只保留事实、决策、纠错结论、开放问题和工具证据引用。

## 数据设计

建议扩展 `chat_sessions`：

```sql
ALTER TABLE chat_sessions
  ADD COLUMN conversation_summary_json TEXT;

ALTER TABLE chat_sessions
  ADD COLUMN summary_until_message_id TEXT;

ALTER TABLE chat_sessions
  ADD COLUMN summary_updated_at TEXT;
```

如果 SQLite 迁移工具当前只走 `CREATE TABLE IF NOT EXISTS`，则在初始化时做 `PRAGMA table_info(chat_sessions)` 检查，缺字段时执行 `ALTER TABLE`。

### `conversation_summary_json` 结构

建议版本化：

```json
{
  "version": 1,
  "source": "llm_compaction",
  "summary_until_message_id": "msg_123",
  "updated_at": "2026-07-08T12:00:00+08:00",
  "core_user_intent": [
    "用户正在测试和修复迷你烹饪问答 agent 的多轮上下文能力。"
  ],
  "active_recipe_context": [
    "最近明确讨论过的小炒鸡在本地图谱命中，应以本地图谱工具结果为准。"
  ],
  "important_constraints": [
    "菜式问题必须先查 recipe_query_tool，不能直接凭常识回答。",
    "本地图谱未命中且允许兜底时，才能使用 web_search_tool。"
  ],
  "tool_evidence_refs": [
    {
      "message_id": "ai_msg_123",
      "tool": "recipe_query_tool",
      "query": "小炒鸡的具体做法",
      "result_kind": "local_recipe_hit",
      "standard_dish": "小炒鸡",
      "short_finding": "本地图谱命中小炒鸡，包含用料、步骤和火力信息。"
    }
  ],
  "corrections": [
    "藤条焖猪肉不应作为本地图谱菜谱回答；本地未命中时只能作为联网兜底摘要处理。"
  ],
  "open_questions": [],
  "discarded_or_low_value_history": [
    "早期普通寒暄和与当前任务无关的闲聊已省略。"
  ]
}
```

失败兜底时：

```json
{
  "version": 1,
  "source": "fallback_rules",
  "summary_until_message_id": "msg_123",
  "updated_at": "2026-07-08T12:00:00+08:00",
  "core_user_intent": [],
  "active_recipe_context": [],
  "important_constraints": [],
  "tool_evidence_refs": [],
  "corrections": [],
  "open_questions": [],
  "discarded_or_low_value_history": [
    "LLM 压缩失败，本摘要由规则兜底生成，仅推进压缩游标并保留可追溯证据引用。"
  ]
}
```

## 触发流程

接入点建议放在 `backend/app.py` 的 `chat_stream()` 中，用户消息入库之后、构造最终 agent history 之前。

当前流程：

```text
add_message(human)
apply_preference_actions()
all_msgs = get_messages(session_id)
history = build_agent_history(all_msgs[:-1])
runtime_memory = build_runtime_memory_context(...)
history.insert(runtime_memory)
stream_search_agent(user_text, history)
```

建议改为：

```text
add_message(human)
apply_preference_actions()
maybe_compact_session_context(session_id)
all_msgs = get_messages(session_id)
history = build_agent_history(all_msgs[:-1])
runtime_memory = build_runtime_memory_context(..., conversation_summary=...)
history.insert(runtime_memory)
stream_search_agent(user_text, history)
```

`maybe_compact_session_context()` 内部流程：

```text
1. 读取 session 所有消息。
2. 构造一次候选 history + runtime memory。
3. 估算将发送给模型的输入 token。
4. 如果 estimated_input_tokens < MAX_MODEL_LEN * 0.93，直接返回。
5. 计算保留窗口：
   - 当前用户最新问题。
   - 最近 6 轮 user/assistant 自然消息。
   - 最近 2 组工具调用结果。
6. 找出 summary_until_message_id 之后、保留窗口之前的可折叠消息。
7. 用旧 summary + 新可折叠消息生成合并摘要。
8. 成功则写入 conversation_summary_json，并更新 summary_until_message_id。
9. 失败则写 fallback_rules 摘要，并更新 summary_until_message_id。
10. 返回轻提示事件给 SSE，或在后续 trace 中展示。
```

## Token 估算

第一版不引入 tokenizer 依赖也可以落地。推荐策略：

```text
estimated_tokens = ceil(char_count / 1.8)
```

原因：

- 当前主要是中文上下文，字符/token 估算需要保守。
- 只用于触发压缩，不用于精确计费。
- 已有前端 token 展示也包含估算口径。

后续如果本地 tokenizer 依赖稳定，再替换为模型 tokenizer。

需要估算的内容：

- system prompt。
- tools schema 近似成本。
- runtime memory。
- 历史消息。
- 历史工具结果摘要。
- 当前用户消息。
- 预留 `LLM_MAX_TOKENS` 输出空间。

触发判断建议：

```text
estimated_input_tokens + LLM_MAX_TOKENS >= MAX_MODEL_LEN * 0.93
```

这样不会把输出空间挤掉。

## History 构造调整

当前 `build_agent_history()` 固定取最近 12 条消息。

压缩上线后建议改成：

```text
build_agent_history(session_messages, conversation_summary=None)
```

行为：

- 如果没有 summary，沿用当前最近 12 条策略。
- 如果有 summary：
  - 注入一条 `role=runtime_memory` 或独立 `role=system_context` 的 session 摘要块。
  - 原文只保留 summary 游标之后的消息。
  - 在原文窗口中继续保留最近 6 轮自然对话和最近 2 组工具结果。

摘要注入文本示例：

```text
<conversation_summary scope="session">
这是当前 session 早期对话的压缩摘要，只用于保持上下文连续性。若它与最新工具结果冲突，以最新工具结果为准。

用户目标：
- ...

当前有效约束：
- ...

工具证据引用：
- message_id=..., tool=recipe_query_tool, query=..., finding=...

纠错后结论：
- ...
</conversation_summary>
```

## 压缩 Prompt

压缩模型必须输出严格 JSON，不能输出 Markdown。

建议 prompt：

```text
你是会话上下文压缩器。你的任务是把当前 session 的旧对话合并进已有 conversation_summary。

重要规则：
1. 只保留当前 session 内对未来回答仍有帮助的信息。
2. 不要保留错误回答原文；如果历史中发生纠错，只保留纠错后的结论。
3. 工具结果只能保留引用和短结论，不要复制完整工具输出。
4. 用户长期偏好如果已经在 preference_memory 中出现，不要重复冗长记录；只记录它影响当前 session 的方式。
5. 本地菜谱图谱命中、联网兜底、本地未命中这三类证据必须区分。
6. 如果新旧摘要冲突，以较新的工具结果和较新的用户指令为准。
7. 输出必须是合法 JSON，字段必须完整，不要额外解释。

输出 JSON schema：
{
  "version": 1,
  "source": "llm_compaction",
  "summary_until_message_id": "...",
  "updated_at": "...",
  "core_user_intent": ["..."],
  "active_recipe_context": ["..."],
  "important_constraints": ["..."],
  "tool_evidence_refs": [
    {
      "message_id": "...",
      "tool": "...",
      "query": "...",
      "result_kind": "local_recipe_hit | local_recipe_miss | web_fallback | other",
      "standard_dish": "...",
      "short_finding": "..."
    }
  ],
  "corrections": ["..."],
  "open_questions": ["..."],
  "discarded_or_low_value_history": ["..."]
}
```

输入给压缩模型：

- 旧 `conversation_summary_json`。
- 待折叠消息列表。
- 每条消息的 `id / role / content`。
- assistant 消息关联的 `rag_trace` 概览。
- 本次计划推进到的 `summary_until_message_id`。

## 轻提示与 trace

压缩触发后，前端默认只显示轻提示：

```text
上下文接近模型上限，已压缩较早对话并保留关键结论。
```

不展示完整 `conversation_summary_json`。

建议在 `rag_trace` 中增加：

```json
{
  "context_compaction": {
    "triggered": true,
    "source": "llm_compaction",
    "estimated_input_tokens_before": 30500,
    "max_model_len": 32768,
    "threshold_ratio": 0.93,
    "summary_until_message_id": "msg_123",
    "folded_messages": 18,
    "kept_recent_turns": 6,
    "kept_tool_result_groups": 2
  }
}
```

如果压缩失败：

```json
{
  "context_compaction": {
    "triggered": true,
    "source": "fallback_rules",
    "error": "LLM compaction failed: ...",
    "summary_until_message_id": "msg_123"
  }
}
```

## 建议文件改动

新增：

- `backend/session_context_compaction.py`
  - `estimate_context_tokens(...)`
  - `should_compact_context(...)`
  - `select_compaction_window(...)`
  - `build_compaction_prompt(...)`
  - `compact_session_context(...)`
  - `fallback_compaction_summary(...)`
  - `render_conversation_summary_for_memory(...)`

修改：

- `backend/chat_persistence.py`
  - 给 `chat_sessions` 增加 summary 字段迁移。
  - 增加 `get_conversation_summary(session_id)`。
  - 增加 `update_conversation_summary(session_id, summary_json, summary_until_message_id)`。

- `backend/context_manager.py`
  - `build_runtime_memory_context()` 增加 `conversation_summary` 参数。
  - `build_agent_history()` 支持 summary 游标后的历史窗口。

- `backend/app.py`
  - 在回答前调用 `maybe_compact_session_context()`。
  - 将压缩结果注入本轮 `rag_trace` 或 `rag_step`。

- `backend/agent_adapter_local_LLM_harness.py`
  - 可复用当前 `get_model()` 做压缩。
  - 压缩调用不要绑定 tools。

- `frontend/src/components/Chat/RetrievalTraceDetails.vue`
  - 展示轻提示和压缩元信息。

## 测试计划

### 单元测试

新增 `test/test_session_context_compaction.py`：

1. 未接近阈值不触发压缩。
2. 达到 `MAX_MODEL_LEN * 0.93` 触发压缩。
3. 触发判断会预留 `LLM_MAX_TOKENS`。
4. 保留最近 6 轮自然对话。
5. 保留最近 2 组工具结果引用。
6. 不把完整工具结果写入摘要。
7. LLM 压缩失败时写入 `fallback_rules` 摘要。
8. fallback 也推进 `summary_until_message_id`。
9. 纠错历史只保留纠错后的结论。

### 集成测试

扩展多轮测试：

1. 长对话后追问最近菜品：
   - 早期讨论 A 菜。
   - 中间大量闲聊和工具结果。
   - 最近讨论 B 菜。
   - 触发压缩后问“它火候呢”。
   - 期望指向 B 菜。

2. 长对话后保留纠错结论：
   - 早期错误回答某个未收录菜。
   - 后续纠正为本地未命中、需联网兜底。
   - 触发压缩后再次问该菜。
   - 期望不再按本地菜谱回答。

3. 长对话后远历史可追溯：
   - 摘要中只保留工具证据引用。
   - 完整工具结果仍在 `chat_messages.rag_trace_json`。

4. 压缩失败不阻塞：
   - mock 压缩模型失败。
   - 本轮仍正常进入 agent 回答。
   - DB 中有 `source=fallback_rules`。

## 分阶段实施

### Phase 1: 规则触发 + DB 字段 + fallback 摘要

目标：

- 完成字段迁移。
- 能估算上下文 token。
- 达到 93% 时触发。
- 即使 LLM 压缩未接入，也能写 fallback 摘要并推进游标。

验收：

- 单元测试覆盖触发、窗口选择和 fallback。
- 不影响现有多轮菜谱测试。

### Phase 2: LLM 结构化压缩

目标：

- 接入 content-only 当前 LLM。
- 输出 JSON 校验。
- 失败时降级 fallback。
- 成功时注入 `conversation_summary`。

验收：

- 长对话测试中能保留关键意图、纠错结论、工具证据引用。
- 摘要不会复制大段工具结果。

### Phase 3: 前端轻提示与 trace 可观测

目标：

- trace 面板显示“已压缩较早对话”。
- 显示触发比例、折叠消息数、摘要来源。
- 默认不展示完整摘要。

验收：

- 用户可以知道发生过压缩。
- 调试时可以从后端日志或 DB 追溯摘要内容。

## 风险与约束

- 93% 触发较晚，token 估算必须保守，并且要预留输出 tokens。
- 压缩 LLM 如果输出非法 JSON，必须严格失败并走 fallback，不能把半截文本注入上下文。
- conversation summary 只代表当前 session，不能写进跨会话偏好。
- 摘要不能覆盖最新工具结果；所有 prompt 都要强调“最新工具结果优先”。
- 工具证据只保留引用，否则摘要会再次膨胀。
- fallback 推进游标会牺牲部分远历史细节，但这是避免每轮反复压缩失败的必要代价。

## 推荐结论

第一版不要移植 Zleap-Agent 完整 event graph，也不要做跨会话长程压缩。

先做一个当前 session 内的轻量 compaction：

```text
接近 token 上限
  -> 折叠远历史
  -> 写 conversation_summary_json
  -> 保留最近 6 轮 + 最近 2 组工具结果
  -> runtime memory 注入摘要
  -> 本轮继续正常回答
```

这能补上当前项目最大的上下文缺口：长对话不再只靠最近 12 条硬裁剪，而是有一个可控、可追溯、不会跨会话污染的压缩层。
