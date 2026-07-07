# Zleap-lite Memory Migration Plan

## 背景

本项目当前的记忆能力主要是会话内上下文投影：

- `backend/memory_store.py` 使用进程内 dict 保存 session，重启后丢失。
- `backend/context_manager.py` 保留最近 12 条消息，并把 `rag_trace` 展开为历史工具上下文。
- `backend/agent_adapter_local_LLM_harness.py` 每轮只接收当前 history，没有长期记忆注入。

`E:\Zleap-Agent` 的记忆系统更完整：A 线 people notes、B 线 core event graph、runtime `listMemory` 注入、pgvector/FTS/entity/graph/RRF 混合召回、压缩前事件抽取。完整移植对 miniCookingAgent 过重，因此本项目第一阶段采用 Zleap-lite。

## 已确认边界

第一版目标：

- 先解决“多轮菜谱上下文记忆 + 用户偏好记忆”。
- 不一开始做完整事件图。
- 用户偏好跨会话保留。
- 菜谱上下文只保留在当前 session 内。
- 偏好写入采用半自动策略：明确偏好自动写，高置信、低风险句式才写；其他情况不写。
- 支持显式删除/修改偏好，但先做文本级软删除/覆盖，不做复杂冲突图。
- session 内菜谱上下文只记结构化槽位，不存完整工具输出。
- 第一版以 runtime 注入为主，后续再补工具。

## 不移植的 Zleap 能力

第一版暂不移植：

- Postgres + pgvector 依赖。
- `source/event/entity/event_entity` 完整事件图。
- LLM 抽取器和 LLM reranker。
- 压缩前 compaction extraction。
- 跨 workspace / space / tenant / actor role 的复杂作用域。
- 经验记忆的脱敏、归并、替换旧事件流程。

这些能力适合后续产品化阶段，而不是当前轻量 demo 的第一步。

## 目标架构

Zleap-lite 分两层：

1. Preference memory
   - 跨会话持久化。
   - 保存用户饮食偏好、禁忌、厨房条件、默认口味。
   - 每轮自动注入 prompt。

2. Session recipe context
   - 仅当前 session 生效。
   - 保存最近一道菜、最近菜谱工具摘要、最近联网兜底摘要。
   - 用于解决“刚才那道菜 / 它 / 这个火候”等指代。

模型每轮看到一个类似 Zleap `listMemory` 的 runtime 注入块，但不要求模型主动调用记忆工具。

## 数据设计

### Preference Memory

建议新增 SQLite 文件：

```text
data/memory.sqlite3
```

建议表：

```sql
CREATE TABLE IF NOT EXISTS preference_memory (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL,
  memory TEXT NOT NULL,
  normalized_key TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  confidence REAL NOT NULL DEFAULT 1.0,
  source_session_id TEXT,
  source_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS preference_memory_active_idx
  ON preference_memory (user_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS preference_memory_key_idx
  ON preference_memory (user_id, normalized_key, status);
```

`kind` 第一版可取：

- `dietary_restriction`: 不能吃辣、不吃香菜、过敏等。
- `taste_preference`: 喜欢清淡、少油、偏甜等。
- `equipment`: 没有烤箱、只有空气炸锅等。
- `cooking_goal`: 减脂、低盐、快速晚餐等。

### Session Recipe Context

可以继续放在现有 session dict 中，不必第一版持久化。

建议结构：

```python
{
    "last_dish": "清蒸鲈鱼",
    "last_query": "清蒸鲈鱼怎么做",
    "last_recipe_tool_result_summary": "蒸制时间约8分钟，汤汁倒掉去腥，淋热油激香。",
    "last_web_fallback_query": "北京烤鸭怎么做",
    "last_web_fallback_summary": "联网资料摘要...",
    "last_tool_names": ["recipe_query_tool", "web_search_tool"],
    "updated_at": "2026-07-07T..."
}
```

## Runtime 注入

新增一个记忆注入块，放进工具循环模型消息里，位置建议在系统 prompt 后、历史消息前。

示例文本：

```text
<runtime_memory>
用户长期偏好：
- [dietary_restriction] 用户不能吃辣。
- [taste_preference] 用户偏好少油。

当前会话菜谱上下文：
- 最近菜品：清蒸鲈鱼
- 最近问题：清蒸鲈鱼怎么做
- 最近菜谱摘要：蒸制时间约8分钟，汤汁倒掉去腥，淋热油激香。

使用规则：
- 用户说“它/这道菜/刚才那道菜/这个火候”时，优先指向当前会话最近菜品。
- 用户偏好是长期约束，推荐做法时必须主动考虑。
- 当前会话菜谱上下文只用于本 session，不代表用户长期偏好。
</runtime_memory>
```

接入点：

- 在 `backend/context_manager.py` 新增 `build_runtime_memory_context(...)`。
- 在 `backend/agent_adapter_local_LLM_harness.py` 的 `_build_tool_loop_messages(...)` 中注入。
- 在 direct chat prompt 中也注入偏好，避免非工具回答忽略长期偏好。

## 偏好写入策略

第一版使用规则提取，不依赖 LLM。

自动写入的高置信句式：

- `我不能吃X`
- `我不吃X`
- `我对X过敏`
- `我喜欢X口味`
- `我偏好X`
- `我家没有X`
- `以后...尽量...`

不自动写入：

- 包含“今天、这次、这顿、临时、现在想”的短期约束。
- 第三方偏好，比如“我朋友不吃香菜”。
- 泛泛表达，比如“清淡点比较健康”。
- 单次任务要求，比如“帮我做不辣版本”。

显式删除/覆盖：

- “忘掉我不吃辣”
- “别记我不能吃辣了”
- “我现在可以吃辣了”
- “把我的偏好改成少油少盐”

第一版处理方式：

- 匹配到相同 `normalized_key` 时 archive 旧 active 记录，再写新记录。
- 删除时 archive 命中的 active 记录。
- 注入时只注入 active 记录，最多 20 条。

## 菜谱上下文更新策略

每轮工具调用结束后，根据 `rag_trace.tool_calls` 更新 session recipe context。

更新规则：

- 如果 `recipe_query_tool` 成功命中标准菜名，更新 `last_dish`。
- 如果 `recipe_query_tool` 未命中但触发 `web_search_tool`，保留 `last_web_fallback_query` 和摘要。
- 如果用户只是问天气、身份、闲聊，不更新 `last_dish`。
- 如果用户追问“它/刚才那道菜”，且本轮没有新菜名，则沿用上一轮 `last_dish`。

摘要来源第一版可以用确定性截断：

- 优先取 `rag_trace.hybrid_retrieval.standard_dish`。
- 工具 `output_preview` 截断到 300-500 字。
- web fallback 截断到 300-500 字。

后续可以再用 LLM 做摘要压缩。

## 建议文件改动

新增：

- `backend/preference_memory.py`
  - SQLite 初始化。
  - `list_preferences(user_id)`
  - `remember_preference(...)`
  - `archive_preference(...)`
  - `extract_preference_actions(user_text)`

- `backend/session_recipe_context.py`
  - `update_recipe_context_from_trace(session_id, user_text, rag_trace)`
  - `get_recipe_context(session_id)`
  - `render_recipe_context(...)`

修改：

- `backend/memory_store.py`
  - session dict 增加 `recipe_context` 字段。

- `backend/context_manager.py`
  - 增加 runtime memory 渲染函数。

- `backend/agent_adapter_local_LLM_harness.py`
  - 工具循环 prompt 注入 runtime memory。
  - direct chat prompt 注入偏好。

- `backend/app.py`
  - 用户消息入库后提取偏好。
  - agent 返回后根据 `rag_trace` 更新 session recipe context。

可选修改：

- `frontend/src/components/Chat/RetrievalTraceDetails.vue`
  - 后续展示“本轮使用了哪些记忆”。

## 测试计划

### 单元测试

偏好提取：

- “我不能吃辣”写入 `dietary_restriction`。
- “今天不想吃辣”不写入。
- “我朋友不吃香菜”不写入。
- “我现在可以吃辣了”覆盖或归档旧偏好。

session 菜谱上下文：

- recipe 工具命中后更新 `last_dish`。
- 闲聊轮不覆盖 `last_dish`。
- web fallback 只更新 fallback 字段。

runtime 注入：

- active 偏好会出现在 prompt。
- archived 偏好不会出现。
- 当前 session 的 `last_dish` 会出现在 prompt。

### 真实 agent 多轮测试

新增或扩展现有 `test/run_multiturn_dialogue_test.py`：

1. 当前 session 指代
   - 用户：“清蒸鲈鱼怎么做”
   - 用户：“它蒸多久”
   - 期望仍指向清蒸鲈鱼。

2. 抗干扰
   - 用户：“辣椒炒肉怎么做”
   - 用户：“今天天气怎么样”
   - 用户：“刚才那道菜火候呢”
   - 期望仍指向辣椒炒肉。

3. 偏好注入
   - 用户：“我不能吃辣”
   - 新 session 用户：“推荐一道晚餐”
   - 期望回答主动避开辣味或说明会按不辣推荐。

4. 偏好删除
   - 用户：“我不能吃辣”
   - 用户：“别记我不能吃辣了”
   - 新 session 用户：“推荐一道晚餐”
   - 期望不再把不能吃辣当作长期约束。

## 分阶段实施

### Phase 1: 持久偏好 + session 菜谱槽位

目标：

- SQLite 偏好表可读写。
- session 内可维护 `last_dish`。
- prompt 注入 runtime memory。

验收：

- 单元测试通过。
- 多轮指代 case 通过。
- 跨 session 偏好 case 通过。

### Phase 2: 偏好覆盖/删除 + 可视化 trace

目标：

- 支持“忘掉/改成/现在可以”的软删除或覆盖。
- trace 面板显示本轮注入的偏好和 session 菜谱上下文。

验收：

- 删除/覆盖测试通过。
- UI 能看出 memory 是否参与。

### Phase 3: 轻量召回

目标：

- 如果偏好数量变多，加入本地 embedding/关键词召回。
- 复用已有 `models/gte-large-zh`。
- 仍不引入 Postgres。

验收：

- 只注入和当前问题相关的偏好。
- 召回结果在 trace 中可解释。

## 风险

- 自动写偏好容易误记短期约束，所以第一版必须保守。
- runtime 注入过长会污染菜谱回答，所以偏好条数和摘要长度要有上限。
- session `last_dish` 如果被错误更新，会导致后续追问漂移，因此只有 recipe 工具明确命中时才更新。
- 当前 session 存储仍是进程内 dict，菜谱上下文随服务重启丢失，这是第一版可接受边界。

## 推荐结论

先实现 Zleap-lite，不完整移植 Zleap-Agent。

最小可落地切片是：

1. SQLite 偏好记忆。
2. session 菜谱上下文槽位。
3. 每轮 runtime memory 注入。
4. 保守规则提取偏好。
5. 多轮真实 agent 测试验证。

这能直接解决当前项目最痛的“多轮指代、抗干扰、用户偏好”问题，同时避免把轻量 demo 变成完整 memory platform。
