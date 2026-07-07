# Zleap-lite Chat Persistence Plan

## 背景

当前项目已经有一版 Zleap-lite memory：

- 用户偏好记忆通过 SQLite 跨会话保留。
- 菜谱上下文保存在当前 session 的内存结构里。
- 每轮 prompt 会注入 runtime memory，让 agent 能处理多轮菜谱指代和用户偏好。

但当前 session 本身仍主要依赖进程内存。后端进程结束后，当前对话的消息、菜谱上下文和 trace 展示状态会丢失。这个方案补齐“进程重启后保留本对话”的轻量持久化能力。

## 目标

第一版目标是消息级持久化，而不是完整复制 Zleap-Agent 的事件图系统。

需要支持：

- 后端重启后，使用同一个 `session_id` 可以恢复本对话。
- 前端聊天列表可以从后端持久化数据恢复。
- 恢复后的会话能继续多轮追问，例如“刚才那道菜蒸多久”。
- 每轮 assistant 消息对应的 `rag_trace` 可以恢复，用于 trace 面板展示和测试复盘。
- 数据库保存全量消息，但模型上下文继续使用现有截断策略，避免长对话污染 prompt。

## 非目标

第一版不做：

- Zleap 的 `parent_entry_id` / `current_leaf` 分支模型。
- 编辑历史消息后重新分叉执行。
- 正在流式输出中的半成品状态恢复。
- 工具调用事件级持久化表。
- compaction entry、artifact entry、workspace pane 等完整 Zleap 能力。
- Postgres、pgvector 或事件图迁移。

## 设计结论

采用消息级持久化方案：

- 存储：继续使用 `data/memory.sqlite3`。
- session id：沿用现有前端传入的 `session_id`。
- 消息：新增顺序消息表，按 `created_at` / `id` 恢复。
- 菜谱上下文：作为 session 快照字段持久化。
- trace：存到 assistant 消息的 `rag_trace_json`。
- 内存 store：保留现有 `memory_store` API，内部做 SQLite hydrate + 写入。
- 删除：session 软删除，默认列表只展示 active session。
- 聊天列表：从 SQLite 恢复。

## 数据表

### `chat_sessions`

用于恢复聊天列表和当前会话快照。

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  recipe_context_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);

CREATE INDEX IF NOT EXISTS chat_sessions_status_updated_idx
  ON chat_sessions (status, updated_at DESC);
```

字段说明：

- `id`: 现有 `session_id`。
- `title`: 聊天列表标题，沿用当前 session title 逻辑。
- `status`: `active` 或 `archived`。
- `recipe_context_json`: 当前 session 菜谱上下文快照。
- `created_at` / `updated_at`: 用于列表排序和恢复。
- `archived_at`: 软删除时间。

### `chat_messages`

用于恢复对话消息和每轮 trace。

```sql
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  rag_trace_json TEXT,
  created_at TEXT NOT NULL,
  deleted_at TEXT,
  FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx
  ON chat_messages (session_id, created_at);
```

字段说明：

- `role`: 复用现有角色，至少包含 `human` / `ai`。
- `content`: 消息正文。
- `rag_trace_json`: 只在 assistant 消息上保存本轮 trace；用户消息一般为空。
- `deleted_at`: 预留软删除能力，第一版列表恢复时过滤 deleted 消息。

## 运行时读写流程

### 创建或打开 session

```text
frontend sends session_id
  -> backend get_session(session_id)
  -> memory cache hit: return cache
  -> memory cache miss: load chat_sessions + chat_messages from SQLite
  -> hydrate in-memory session
  -> return session
```

首次创建 session 时：

- 写入 `chat_sessions`。
- 初始化空消息列表。
- 初始化空 `recipe_context`。

### 写入用户消息

用户消息进入后：

1. 调用现有 `add_message(session_id, "human", content)`。
2. 更新内存 session。
3. 同步写入 `chat_messages`。
4. 更新 `chat_sessions.updated_at`。

用户偏好提取逻辑保持现状，继续写 `preference_memory`。

### 写入 assistant 消息

agent 返回最终回答后：

1. 调用 `add_message(session_id, "ai", answer, rag_trace=trace)`。
2. 更新内存 session。
3. 写入 `chat_messages.rag_trace_json`。
4. 根据 `rag_trace` 更新 session recipe context。
5. 将新的 `recipe_context` 写回 `chat_sessions.recipe_context_json`。
6. 更新 `chat_sessions.updated_at`。

### 更新菜谱上下文

第一版不从历史 trace 重放恢复菜谱上下文，而是把当前快照直接持久化。

优点：

- 重启恢复快。
- 不依赖旧 trace 格式。
- 避免恢复逻辑和业务更新逻辑重复。

## 恢复模型上下文

数据库保存全量消息，但模型不读取全量。

恢复后仍沿用当前逻辑：

```text
all persisted messages
  -> build_agent_history(messages)
  -> keep recent MAX_HISTORY_MESSAGES
  -> inject runtime memory
  -> call agent
```

这样 UI 可以展示完整历史，模型上下文仍然可控。

`recipe_context_json` 会在 hydrate 时恢复到内存 session 中，后续 `build_runtime_memory_context(...)` 可以继续注入：

- 最近菜品
- 最近问题
- 最近菜谱摘要
- 最近联网兜底摘要

因此重启后用户继续问“它蒸多久”，agent 仍能优先指向当前 session 的最近菜品。

## 聊天列表恢复

`list_sessions()` 改为从 SQLite 读取 active sessions。

返回字段至少包括：

- `id`
- `title`
- `updated_at`
- 最近一条消息摘要，可选

内存中存在但 SQLite 还未写入的 session 可以合并进列表，但 SQLite 是重启后的恢复来源。

默认过滤：

```sql
WHERE status = 'active'
ORDER BY updated_at DESC
```

## 删除和清空

第一版使用 session 软删除。

清空或删除会话时：

- `chat_sessions.status = 'archived'`
- `chat_sessions.archived_at = now`
- 默认列表不展示 archived session
- 不物理删除 `chat_messages`

这样方便测试复盘和 trace 排查。后续如需要，可以增加“彻底删除”接口。

## 建议文件改动

新增：

- `backend/chat_persistence.py`
  - SQLite 表初始化。
  - `upsert_chat_session(...)`
  - `load_chat_session(session_id)`
  - `list_chat_sessions(...)`
  - `append_chat_message(...)`
  - `update_chat_session_snapshot(...)`
  - `archive_chat_session(session_id)`

修改：

- `backend/memory_store.py`
  - 保持现有函数签名。
  - `get_session(...)` 支持 SQLite hydrate。
  - `create_session(...)` 写入 `chat_sessions`。
  - `add_message(...)` 写入 `chat_messages`。
  - `update_recipe_context(...)` 写入 `chat_sessions.recipe_context_json`。
  - `list_sessions(...)` 从 SQLite 恢复聊天列表。

- `backend/app.py`
  - 确保 assistant 最终消息写入时传入 `rag_trace`。
  - 删除或清空 session 时调用 soft archive。

- `backend/schemas.py`
  - 如现有响应结构不包含 trace 恢复字段，需要补充 message-level trace 字段。

可选：

- `frontend/src/components/Chat/*`
  - 如果当前前端只用内存消息，需要在打开 session 时使用后端返回的持久化 messages hydrate UI。

## 测试计划

### 单元测试

新增 `test/test_chat_persistence.py`：

1. 创建 session 后，SQLite 中存在 `chat_sessions` 记录。
2. 写入 human / ai 消息后，可以按顺序读取。
3. assistant 消息的 `rag_trace_json` 可以 round-trip。
4. `recipe_context_json` 更新后可以恢复。
5. archived session 不出现在默认列表。
6. `get_session(session_id)` 在内存为空时可以从 SQLite hydrate。

### 集成测试

新增或扩展现有多轮测试：

1. 第一轮问“清蒸鲈鱼怎么做”。
2. 确认生成 assistant 消息和 `recipe_context.last_dish`。
3. 模拟进程重启：清空内存 `_sessions`，不删 SQLite。
4. 用同一个 `session_id` 再问“它蒸多久”。
5. 期望 agent 仍指向清蒸鲈鱼。

### trace 恢复测试

1. 发起一个会触发 `recipe_query_tool` 的问题。
2. 写入 assistant 消息。
3. 清空内存并重新读取 session。
4. 期望恢复出的 assistant message 带有 `rag_trace.tool_calls`。

## 分阶段实施

### Phase 1: SQLite schema + memory_store hydrate

完成：

- 新增 `chat_persistence.py`。
- 创建 `chat_sessions` / `chat_messages`。
- `memory_store` 读写 SQLite。
- 单元测试覆盖 round-trip。

验收：

- 后端进程内存清空后，同一 `session_id` 可以恢复消息和 `recipe_context`。

### Phase 2: app.py 接入 trace 和 session snapshot

完成：

- assistant 最终消息写入 `rag_trace_json`。
- 每轮结束后持久化 `recipe_context_json`。
- 聊天列表从 SQLite 恢复。

验收：

- 重启后 UI 能看到历史消息。
- trace 面板能显示旧 assistant 消息的 trace。

### Phase 3: 真实 agent 重启恢复测试

完成：

- 增加真实 agent 多轮恢复 case。
- 验证重启后“它/刚才那道菜”仍然正确。

验收：

- 本地真实 LLM / 远端 LLM 测试至少通过一个多轮恢复 case。
- 测试报告能区分普通多轮记忆失败和持久化恢复失败。

## 风险和约束

- 如果前端不保留或不传原 `session_id`，后端无法知道要恢复哪条对话。
- 如果 `rag_trace` 很大，消息表会膨胀；第一版可以限制 trace JSON 长度或只保存结构化摘要。
- 如果 `add_message(...)` 被测试频繁调用，SQLite 写入需要支持测试隔离，建议继续使用环境变量覆盖 DB 路径。
- 实现时必须确认所有成功完成的模型回答都会进入最终 assistant 写入路径，否则该轮 `rag_trace` 不会持久化；重点检查 streaming 结束事件。

## 推荐实现边界

第一版只做足够恢复本对话的能力：

- `chat_sessions` 保存 session 元信息和菜谱上下文快照。
- `chat_messages` 保存顺序消息和 assistant trace。
- `memory_store` 作为兼容层，内存缓存不是事实来源。
- SQLite 是进程重启后的事实来源。

这比完整 Zleap 事件图轻很多，但已经能覆盖当前最重要的体验：后端重启后，对话、trace、当前菜谱指代都能继续。
