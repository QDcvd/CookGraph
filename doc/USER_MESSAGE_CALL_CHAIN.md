# 用户发消息后的调用链

本文说明 MiniCookingAgent-Demo 当前版本在用户发送一条消息后，从前端、会话持久化、runtime memory、确定性菜谱路由、Agent 工具循环，到 Query Understanding、菜谱混合召回、知识图谱查询、联网兜底、最终回答约束、SSE 回传和 SQLite 落库的完整链路。

## 当前关键变化

- 前端仍通过 `session_id` 维持当前对话；历史会话从 `/sessions/{session_id}` 恢复消息和 `rag_trace`。
- 后端 `memory_store` 是运行期缓存，`data/memory.sqlite3` 是进程重启后的事实来源。
- 用户消息写入后，会立即抽取长期偏好并写入 `preference_memory`。
- 每轮请求都会构造 Zleap-lite runtime memory，注入长期偏好和当前 session 菜谱上下文。
- `stream_search_agent()` 现在先跑 `_preflight_recipe_action()`，对无菜名属性追问、明确菜名覆盖旧上下文等高风险问题做确定性路由，再决定是否进入模型工具循环。
- `recipe_query_tool` 仍是唯一菜谱工具；Query Understanding、反向结构化查询、菜名语义召回、别名、TF-IDF、dense embedding、RRF、知识图谱查询都藏在这个工具内部。
- 反向查询如“牛肉怎么做”“花甲”“川菜”“香辣味”“蒸制”会先产出结构化 `QueryIntent(reverse_query)`，再直接查图谱节点和边，不进入旧自然语言 parser。
- 反向查询的向量化对象是图谱节点名词（Ingredient/Technique/Taste/Cuisine 等），不是菜谱正文；低置信度或多类型歧义时追问用户。
- 无明确菜名的属性追问如“火力要怎么控制”会先要求用户补充菜名，避免被历史上下文或模型常识带偏。
- 本地图谱明确允许联网兜底且未命中时，工具循环自动补一次 `web_search_tool`。
- 最终回答阶段先尝试确定性 grounded answer：反向查询、本地菜谱命中、联网兜底、歧义追问都有专门约束；只有无法确定性整理时才调用 content-only 模型。
- assistant 最终回答和本轮 `rag_trace` 会一起持久化；随后根据 trace 更新当前 session 的 `recipe_context_json`。

## 0. 总流程图

```mermaid
flowchart TD
    A["用户输入消息"] --> B["ChatInput.vue<br/>onSend()"]
    B --> C["chatStore.handleSend()"]
    C --> C1["前端追加用户消息<br/>创建 assistant 占位消息"]
    C1 --> D["fetch('/chat/stream')<br/>message + session_id"]
    D --> E["Vite proxy<br/>/chat -> localhost:8000"]
    E --> F["FastAPI<br/>backend.app.chat_stream()"]

    F --> G{"get_session(session_id)"}
    G -->|内存命中| H["返回内存 session"]
    G -->|内存未命中| I["SQLite hydrate<br/>chat_sessions + chat_messages"]
    I --> H
    G -->|不存在| J["create_session()<br/>upsert chat_sessions"]
    J --> H

    H --> K["add_message(..., human, ...)<br/>写内存 + chat_messages"]
    K --> L["apply_preference_actions()<br/>抽取长期偏好"]
    L --> M["get_messages()"]
    M --> N["build_agent_history(all_msgs[:-1])<br/>消息 + 历史 tool_call/tool_result"]
    N --> O["build_runtime_memory_context()<br/>长期偏好 + session 菜谱上下文"]
    O --> P["history.insert(runtime_memory)"]
    P --> Q["SSE event_generator()"]

    Q --> R["stream_search_agent(user_text, history)"]
    R --> S{"_preflight_recipe_action()"}
    S -->|确定性工具路由| T["_execute_forced_tool_call()<br/>recipe_query_tool"]
    S -->|确定性澄清| U["直接 SSE content<br/>要求补充菜名"]
    S -->|不命中 preflight| V["模型工具循环<br/>recipe_query_tool + web_search_tool"]

    T --> W{"recipe 结果是否需要联网兜底?"}
    W -->|是| X["_execute_web_fallback_after_recipe()<br/>web_search_tool"]
    W -->|否| Y["进入最终回答整理"]
    X --> Y

    V --> Z{"模型是否调用工具?"}
    Z -->|结构化 tool_calls| AA["_execute_tool_call()"]
    Z -->|文本式工具调用| AB["_parse_textual_tool_call()<br/>转真实工具调用"]
    AB --> AA
    Z -->|直接回答| AC["SSE content"]

    AA --> AD{"工具名"}
    AD -->|recipe_query_tool| AE["query_recipe_kg(query)"]
    AD -->|web_search_tool| AF["DDGS().text()"]
    AE --> AG{"recipe 结果是否需要联网兜底?"}
    AG -->|是| X
    AG -->|否| Y
    AF --> Y

    Y --> AH["_emit_final_answer_from_tool_context()"]
    AH --> AI{"grounded answer 可用?"}
    AI -->|反向查询| AJ["_build_grounded_reverse_answer()"]
    AI -->|联网兜底| AK["_build_grounded_web_fallback_answer()"]
    AI -->|本地菜谱| AL["_build_grounded_recipe_answer()"]
    AI -->|不可确定整理| AM["_stream_model_answer()<br/>content-only 模型"]
    AJ --> AN["SSE trace / token_usage / content"]
    AK --> AN
    AL --> AN
    AM --> AN
    U --> AN
    AC --> AN

    AN --> AO["前端解析 SSE<br/>更新文本、trace、检索过程、token"]
    Q --> AP["流结束后收集 full_response + rag_trace"]
    AP --> AQ["add_message(..., ai, rag_trace)<br/>写内存 + chat_messages.rag_trace_json"]
    AQ --> AR["update_context_from_trace()<br/>更新最近菜品/联网摘要"]
    AR --> AS["update_recipe_context()<br/>写 chat_sessions.recipe_context_json"]
    AS --> AT["SSE [DONE]"]
```

## 0.1 会话恢复与持久化链路

```mermaid
flowchart TD
    A["前端加载聊天列表"] --> B["GET /sessions"]
    B --> C["memory_store.list_sessions()"]
    C --> D["chat_persistence.list_chat_sessions()"]
    D --> E["SQLite chat_sessions<br/>status='active'"]
    E --> F["返回 session_id/title/message_count/updated_at"]

    G["用户点击历史会话"] --> H["chatStore.loadSession(session_id)"]
    H --> I["GET /sessions/{session_id}"]
    I --> J["memory_store.get_session(session_id)"]
    J -->|缓存未命中| K["load_chat_session()<br/>读取 chat_messages"]
    K --> L["hydrate 内存 session"]
    J -->|缓存命中| L
    L --> M["返回 messages"]
    M --> N["前端恢复 text/isUser/ragTrace"]

    O["删除会话"] --> P["DELETE /sessions/{session_id}"]
    P --> Q["delete_session()"]
    Q --> R["archive_chat_session()<br/>status='archived'"]
```

持久化表：

```text
data/memory.sqlite3
  ├─ chat_sessions
  │   ├─ id
  │   ├─ title
  │   ├─ status
  │   ├─ recipe_context_json
  │   ├─ created_at / updated_at
  │   └─ archived_at
  ├─ chat_messages
  │   ├─ id
  │   ├─ session_id
  │   ├─ role: human / ai
  │   ├─ content
  │   ├─ rag_trace_json
  │   └─ created_at / deleted_at
  └─ preference_memory
      └─ 跨会话用户偏好
```

## 0.2 Agent 工具决策流程图

```mermaid
flowchart TD
    A["stream_search_agent(user_text, history)"] --> B["_runtime_memory_from_history()"]
    A --> C["_build_tool_loop_messages()"]
    B --> D["_preflight_recipe_action(user_text, history)"]
    C --> D

    D --> E{"preflight 命中类型"}
    E -->|反向食材查询| F["强制 recipe_query_tool<br/>原因：只用本地图谱"]
    E -->|当前输入含明确菜名| G["强制 recipe_query_tool<br/>原因：最新问题覆盖旧上下文"]
    E -->|裸菜式短语| H["强制 recipe_query_tool<br/>本地先查"]
    E -->|上下文属性追问| I["用最近菜品补全 query<br/>再强制 recipe_query_tool"]
    E -->|无菜名属性追问| J["直接澄清<br/>请用户指定菜名"]
    E -->|未命中| K["进入模型工具循环"]

    F --> L["_execute_forced_tool_call()"]
    G --> L
    H --> L
    I --> L
    L --> M{"recipe_query_tool 是否需要联网兜底?"}
    M -->|是| N["_execute_web_fallback_after_recipe()"]
    M -->|否| O["_emit_final_answer_from_tool_context()"]
    N --> O
    J --> P["SSE content"]

    K --> Q["_get_tools()<br/>web_search_tool + recipe_query_tool"]
    Q --> R["_build_tool_loop_system_prompt()"]
    R --> S["模型工具回合<br/>model.ainvoke(messages)"]
    S --> T{"AIMessage 有 tool_calls?"}
    T -->|有| U["逐个执行 tool_call"]
    T -->|没有| V["读取 raw_output"]
    V --> W{"像文本式工具调用?"}
    W -->|是| X["_parse_textual_tool_call()<br/>转换为真实工具调用"]
    W -->|否| Y["普通文本回答"]
    X --> U
    U --> Z{"工具名"}
    Z -->|recipe_query_tool| AA["本地菜谱工具"]
    Z -->|web_search_tool| AB["联网搜索工具"]
    AA --> AC{"recipe 结果是否需要联网兜底?"}
    AC -->|是| N
    AC -->|否| O
    AB --> O
    Y --> P
    O --> P
```

当前 preflight 边界：

- “牛肉可以用来做什么菜”：强制 `recipe_query_tool`，只查本地图谱主食材关系。
- “小炒鸡的具体做法”：如果当前输入包含图谱菜名，强制 `recipe_query_tool`，覆盖旧上下文。
- “藤条焖猪肉”：裸菜式短语先查本地；本地未命中且允许兜底时再联网。
- “它蒸多久”：若 session 最近菜品是“清蒸鲈鱼”，补全为“清蒸鲈鱼蒸多久/火力信息”后查工具。
- “火力要怎么控制”：没有明确菜名且不能可靠指代最近菜品时，直接要求补充菜名。

## 0.3 runtime memory 注入链路

```mermaid
flowchart TD
    A["用户消息入库后"] --> B["get_messages(session_id)"]
    B --> C["build_agent_history(all_msgs[:-1])"]
    C --> D["保留最近 12 条消息"]
    D --> E["AI 消息的 rag_trace 展开为<br/>assistant_tool_call + tool result"]

    A --> F["list_preferences()"]
    A --> G["get_recipe_context(session_id)"]
    F --> H["render_preferences_for_memory()"]
    G --> I["render_recipe_context()"]
    H --> J["build_runtime_memory_context()"]
    I --> J
    J --> K["history.insert({role:'runtime_memory'})"]
    K --> L["preflight、工具循环、最终回答都能看到 runtime memory"]
```

runtime memory 包含：

- 用户长期偏好：例如不能吃辣、偏好清淡、没有烤箱。
- 当前 session 菜谱上下文：最近菜品、最近问题、最近菜谱摘要、最近联网兜底摘要。
- 使用规则：用户说“它/这道菜/刚才那道菜/这个火候”时，优先指向当前 session 最近菜品。
- 冲突规则：如果最新工具结果与 runtime memory 冲突，以最新工具结果为准并纠正旧上下文。

## 0.4 `recipe_query_tool` 内部链路

```mermaid
flowchart TD
    A["recipe_query_tool(query)"] --> B["query_recipe_kg(query)"]
    B --> C{"query 是否为空?"}
    C -->|是| C1["返回 query 不能为空"]
    C -->|否| D["检查 config/chem+recipe_kg_updated_fire.pkl"]
    D --> E{"KG 文件存在?"}
    E -->|否| E1["返回知识图谱文件不存在"]
    E -->|是| F["_get_recipe_system()<br/>加载或复用 RecipeQuerySystem"]

    F --> G["classify_intent(query, dish_names, kg_system)<br/>Query Understanding"]
    G --> H{"intent 类型"}
    H -->|non_recipe| H1["format_non_recipe()<br/>说明不是菜谱查询"]
    H -->|ambiguous| H2["format_ambiguous_query()<br/>要求用户补充实体类型"]
    H -->|reverse_query| I["execute_reverse_query(system, intent)"]
    I --> I1["按类型解析图谱节点<br/>exact / alias / lexical / dense node embedding"]
    I1 --> I2["扫描图谱边<br/>只返回本地图谱明确命中的菜"]
    I2 --> I3["web_fallback_allowed: False"]
    H -->|forward_query / legacy| L["semantic_match_recipe(query)"]

    L --> M["读取 doc/菜谱.xlsx"]
    M --> N["构建召回文本<br/>菜名/别名/食材/调料/技法/口味/摘要"]
    N --> O{"backend/.cache/recipe_semantic_index.npz 可用?"}
    O -->|是| P["读取缓存向量"]
    O -->|否| Q["加载 SentenceTransformer"]
    Q --> R["批量 encode 菜谱文本"]
    R --> S["保存 npz 缓存"]
    P --> T["alias + char ngram TF-IDF + dense"]
    S --> T
    T --> U["RRF 融合候选"]
    U --> V{"score 和 margin 达标?"}
    V -->|是| W["得到标准菜名<br/>生成 effective_query"]
    V -->|否| X

    W --> X["RecipeQuerySystem.query(effective_query 或原 query)"]
    X --> Y["QueryParser.parse()"]
    Y --> Z["QueryExecutor.execute()"]
    Z --> AA{"查询类型"}
    AA -->|summary| AB["_query_summary()"]
    AA -->|forward_attr| AC["_query_forward_attribute()"]
    AA -->|forward_rel| AD["_query_forward_relation()"]
    AA -->|reverse| AE["_query_reverse()"]
    AB --> AF{"属性命中但内容为空?"}
    AC --> AF
    AD --> AF
    AE --> AG["human_readable + 结构化摘要"]
    AF -->|是| AH["_fallback_to_summary_when_empty()<br/>退回完整菜谱档案"]
    AF -->|否| AG
    AH --> AG
    AG --> AI["附加 hybrid retrieval 摘要"]
    AI --> AJ["返回字符串给 Agent"]
```

embedding 模型路径：

```text
优先：MINICOOK_EMBEDDING_MODEL_DIR
本机默认：models/gte-large-zh
Docker 默认：/opt/minicook/models/gte-large-zh
```

向量缓存：

```text
backend/.cache/recipe_semantic_index.npz
```

缓存版本会纳入：

- `doc/菜谱.xlsx` 内容与修改时间。
- 菜名列表和召回文本。
- embedding 模型路径。

## 0.5 最终回答约束链路

```mermaid
flowchart TD
    A["_emit_final_answer_from_tool_context()"] --> B["先发送 trace"]
    B --> C["发送 rag_step: 正在整理最终回答"]
    C --> D{"是否可确定性整理?"}
    D -->|反向食材查询| E["_build_grounded_reverse_answer()"]
    D -->|本地未命中 + web 搜索| F["_build_grounded_web_fallback_answer()"]
    D -->|本地菜谱命中| G["_build_grounded_recipe_answer()"]
    D -->|都不可用| H["_build_final_prompt()"]
    H --> I["_stream_model_answer()<br/>不绑定 tools"]
    I --> J{"模型是否失败或超时?"}
    J -->|是| K["_build_partial_tool_answer()<br/>工具结果兜底摘要"]
    J -->|否| L["SSE content"]
    E --> L
    F --> L
    G --> L
    K --> L
```

约束重点：

- 反向查询答案只能来自工具返回的本地图谱命中列表。
- 本地菜谱命中时，最终回答必须基于图谱用料、步骤、火力、提示整理。
- 本地未命中但联网兜底时，必须明确说明“本地图谱未收录”，再列公开网页摘要；不能说成“根据本地菜谱图谱”。
- 如果搜索工具失败或无结果，回答必须说明无法凭常识编做法。

## 0.6 SSE 回传与前端渲染

```mermaid
flowchart TD
    A["后端 event_generator()"] --> B{"event.type"}
    B -->|rag_step| C["检索/工具步骤"]
    B -->|trace| D["完整 rag_trace"]
    B -->|thinking| E["模型思考片段"]
    B -->|content| F["最终回答片段"]
    B -->|token_usage| G["token 用量"]
    B -->|session_title| H["更新会话标题"]
    B -->|error| I["错误信息"]
    B -->|DONE| J["结束流"]

    C --> K["chatStore.handleSend()<br/>解析 SSE"]
    D --> K
    E --> K
    F --> K
    G --> K
    H --> K
    I --> K
    K --> L{"前端按 type 分发"}
    L -->|rag_step| M["追加 msg.ragSteps<br/>并分组折叠"]
    L -->|trace| N["写入 msg.ragTrace"]
    L -->|thinking| O["追加到过程展示"]
    L -->|content| P["追加 assistant 正文"]
    L -->|token_usage| Q["更新 TokenUsageBadge"]
    L -->|session_title| R["更新 sessions 列表"]
    L -->|error| S["展示 Error 文本"]
    J --> T["isLoading=false<br/>abortController=null"]
```

## 1. 前端发送消息

入口文件：

```text
frontend/src/stores/chat.ts
frontend/src/components/Chat/ChatInput.vue
```

`handleSend()` 的关键动作：

1. 将用户输入追加到前端 `messages`。
2. 如果是当前 session 第一条消息，先在前端会话列表里插入临时标题。
3. 创建 assistant 占位消息，用于接收 SSE 增量内容。
4. 请求后端：

```ts
fetch('/chat/stream', {
  method: 'POST',
  body: JSON.stringify({
    message: text,
    session_id: this.sessionId,
  }),
})
```

5. 循环读取 SSE，根据 `type` 更新正文、trace、检索过程、token 用量和标题。

历史会话恢复走：

```text
chatStore.loadSession(sessionId)
  -> GET /sessions/{session_id}
  -> messages[].rag_trace 恢复到前端 msg.ragTrace
```

## 2. FastAPI 接收请求

入口：

```text
backend/app.py
POST /chat/stream
```

关键步骤：

```python
session = get_session(body.session_id)
if not session:
    create_session(body.session_id)
    update_session_title(body.session_id, body.message[:20])

add_message(body.session_id, "human", body.message)
apply_preference_actions(body.message, source_session_id=body.session_id)

all_msgs = get_messages(body.session_id)
history = build_agent_history(all_msgs[:-1])
runtime_memory = build_runtime_memory_context(
    preferences=list_preferences(),
    recipe_context=get_recipe_context(body.session_id),
)
history.insert(0, {"role": "runtime_memory", "content": runtime_memory})
```

这一步有两个重要效果：

- 当前用户消息会立即持久化。
- 模型看到的上下文不是纯文本历史，而是“消息 + 历史工具结果 + runtime memory”。

## 3. 会话持久化

相关文件：

```text
backend/memory_store.py
backend/chat_persistence.py
```

写入路径：

```text
add_message()
  -> 写入内存 _sessions
  -> append_chat_message()
  -> 写入 chat_messages
```

恢复路径：

```text
get_session(session_id)
  -> 先查 _sessions
  -> 缓存未命中时 load_chat_session()
  -> 从 SQLite 读取 chat_sessions + chat_messages
  -> hydrate 回 _sessions
```

assistant 回复保存：

```text
stream 收集 full_response + rag_trace
  -> add_message(session_id, "ai", full_response, rag_trace)
  -> chat_messages.rag_trace_json
  -> update_context_from_trace()
  -> update_recipe_context()
  -> chat_sessions.recipe_context_json
```

因此后端重启后，只要前端继续使用同一个 `session_id`，就能恢复：

- 对话消息。
- 每轮 assistant 的 trace。
- 当前 session 的最近菜品和菜谱上下文。

## 4. Agent 工具循环

入口：

```text
backend/agent_adapter_local_LLM_harness.py
stream_search_agent(user_text, history)
```

`stream_search_agent()` 的当前顺序：

1. 初始化 `rag_trace` 和 `TokenUsageTracker`。
2. 从 history 解析 runtime memory。
3. 构造工具循环 messages。
4. 发送 `rag_step`: 正在装载工具上下文。
5. 先执行 `_preflight_recipe_action(user_text, history)`。
6. 如果 preflight 返回澄清文本，直接输出 content 并结束。
7. 如果 preflight 返回工具路由，强制执行 `recipe_query_tool`，必要时自动执行 `web_search_tool`，再进入最终回答整理。
8. 如果 preflight 未命中，才进入原来的模型工具循环。

当前注册工具：

```text
recipe_query_tool
web_search_tool
```

工具循环输出的 `rag_trace` 会持续通过 SSE 发给前端，也会在最终 assistant 消息落库时保存。

## 5. 菜谱工具与混合召回

当前 `recipe_query_tool` 是一个组合工具：

```text
recipe_query_tool
  -> query_recipe_kg
  -> Query Understanding
     -> non_recipe: 直接说明不是菜谱查询
     -> ambiguous: 要求用户补充实体类型
     -> reverse_query: execute_reverse_query
        -> 图谱节点名 exact / alias / lexical / dense embedding
        -> NetworkX 边扫描，只列明确命中的菜
     -> forward_query / legacy:
        -> semantic_match_recipe
           -> alias
           -> char_ngram_tfidf
           -> dense gte-large-zh
           -> RRF fusion
        -> RecipeQuerySystem.query
        -> NetworkX 知识图谱精查
        -> 空属性结果退回完整菜谱档案
```

对于“辣椒炒肉怎么做”“我想吃清蒸鲈鱼”“小炒鸡具体做法”这类菜式问题，现在有两层保障：

1. preflight 识别明确菜名或裸菜式短语，直接强制 `recipe_query_tool`。
2. 未命中 preflight 时，系统提示词仍要求模型必须先调用 `recipe_query_tool`。

对于“牛肉可以用来做什么菜”“哪些菜用了蒜蓉这种做法”“有什么川菜推荐”“有哪些菜是蒸制的”这类反向查询，`query_recipe_kg()` 会先生成结构化 `QueryIntent(reverse_query)`，再根据实体类型查图谱节点和边。这里的向量化对象是图谱节点名词，不是菜谱正文；命中后只返回本地图谱明确关联的菜，不联网，不补常识。

## 6. 联网兜底

联网兜底不是所有失败都触发。

当前逻辑：

```text
recipe_query_tool 返回结果
  -> _recipe_query_needs_web_fallback(content)
  -> 如果 web_fallback_allowed: True 且 success: False
  -> 自动执行 web_search_tool(user_text)
```

边界：

- 本地图谱明确给出相似菜品时，不联网。
- 反向查询不联网，只列本地图谱明确命中的菜。
- 明显非菜谱问题，不因为历史上下文误触发菜谱工具。
- 只有本地图谱未命中且允许公共网页补充，或者用户明确要求联网，才使用 `web_search_tool`。

## 7. 最终回答生成

工具执行完后，不再继续让绑定 tools 的模型生成最终答案，而是进入：

```text
_emit_final_answer_from_tool_context()
```

当前优先级：

```text
_build_grounded_reverse_answer()
  -> _build_grounded_web_fallback_answer()
  -> _build_grounded_recipe_answer()
  -> _build_final_prompt()
  -> _stream_model_answer()
  -> 失败时 _build_partial_tool_answer()
```

这样可以避免几类问题：

- 工具已经命中，但最终回答说没找到。
- 本地未命中后，模型把联网摘要包装成“本地图谱菜谱”。
- 反向查询时模型补充本地图谱没有的菜。
- 最终回答阶段再次输出工具调用文本。

## 8. 一条完整链路概览

```text
ChatInput.vue
  -> chatStore.handleSend()
  -> fetch('/chat/stream', { message, session_id })
  -> backend.app.chat_stream()
  -> get_session()
     -> memory cache hit
     -> 或 SQLite hydrate
  -> add_message(..., "human", ...)
     -> chat_messages
  -> apply_preference_actions()
     -> preference_memory
  -> build_agent_history(all_msgs[:-1])
     -> 最近消息
     -> 历史 rag_trace 展开为工具上下文
  -> build_runtime_memory_context()
     -> 长期偏好
     -> session recipe_context
  -> stream_search_agent(user_text, history)
  -> _runtime_memory_from_history()
  -> _build_tool_loop_messages()
  -> _preflight_recipe_action()
     -> 可能强制 recipe_query_tool
     -> 可能直接要求补充菜名
     -> 可能放行给模型工具循环
  -> recipe_query_tool(query)
     -> query_recipe_kg(query)
     -> Query Understanding
        -> non_recipe / ambiguous / reverse_query / forward_query
     -> reverse_query:
        -> execute_reverse_query()
        -> 图谱节点名 exact / alias / lexical / dense embedding
        -> NetworkX 边扫描
        -> human_readable + 结构化摘要 + hybrid retrieval 摘要
     -> forward_query / legacy:
        -> semantic_match_recipe()
           -> alias + TF-IDF + dense + RRF
           -> SentenceTransformer(MINICOOK_EMBEDDING_MODEL_DIR 或 models/gte-large-zh)
           -> backend/.cache/recipe_semantic_index.npz
           -> 标准菜名 / effective_query
        -> RecipeQuerySystem.query(effective_query)
        -> QueryParser.parse()
        -> QueryExecutor.execute()
        -> human_readable + 结构化摘要 + hybrid retrieval 摘要
        -> 必要时空属性结果退回完整档案
  -> 必要时 _execute_web_fallback_after_recipe()
     -> web_search_tool(user_text)
     -> DDGS().text()
  -> _emit_final_answer_from_tool_context()
     -> 优先 deterministic grounded answer
     -> 必要时 content-only 模型整理
  -> SSE: rag_step / trace / token_usage / content / error / DONE
  -> frontend 更新 assistant 消息、trace、检索过程、token 展示
  -> add_message(..., "ai", full_response, rag_trace)
     -> chat_messages.rag_trace_json
  -> update_context_from_trace()
  -> update_recipe_context()
     -> chat_sessions.recipe_context_json
```

## 9. 真实例子

用户输入：

```text
牛肉可以用来做什么菜
```

当前链路：

```text
stream_search_agent()
  -> _preflight_recipe_action()
  -> 命中反向食材查询
  -> 强制 recipe_query_tool
  -> query_recipe_kg()
  -> classify_intent() 得到 QueryIntent(reverse_query, entity_type=ingredient, entity=牛肉)
  -> execute_reverse_query()
  -> 解析图谱节点并扫描相关边
  -> 返回本地图谱明确命中的牛肉菜
  -> _build_grounded_reverse_answer()
  -> 最终回答只列本地图谱结果，不联网，不补常识
```

用户输入：

```text
有哪些菜是蒸制的
```

当前链路：

```text
stream_search_agent()
  -> 模型工具循环或 preflight 触发 recipe_query_tool
  -> query_recipe_kg()
  -> classify_intent() 得到 QueryIntent(reverse_query, entity_type=technique, entity=蒸制)
  -> execute_reverse_query()
  -> 在 Technique/做法类图谱节点中做 exact / alias / lexical / dense 匹配
  -> 返回图谱中明确关联“蒸制”的菜
  -> _build_grounded_reverse_answer()
  -> 不把未命中的菜或模型常识补进列表
```

用户输入：

```text
火力要怎么控制
```

当前链路：

```text
stream_search_agent()
  -> _preflight_recipe_action()
  -> 命中无菜名属性追问
  -> 直接回答：请先告诉我要查询哪道菜的火力控制
  -> 不调用 recipe_query_tool
  -> 不调用 web_search_tool
```

用户输入：

```text
小炒鸡的具体做法
```

当前链路：

```text
stream_search_agent()
  -> _preflight_recipe_action()
  -> 当前输入含明确菜名：小炒鸡
  -> 强制 recipe_query_tool，覆盖旧上下文
  -> query_recipe_kg("小炒鸡的具体做法")
  -> 属性结果若为空，退回完整档案查询
  -> _build_grounded_recipe_answer()
  -> 按本地图谱用料、步骤、火力整理答案
```

用户输入：

```text
藤条焖猪肉
```

当前链路：

```text
stream_search_agent()
  -> _preflight_recipe_action()
  -> 裸菜式短语先查本地
  -> recipe_query_tool 未命中，且 web_fallback_allowed: True
  -> 自动执行 web_search_tool
  -> _build_grounded_web_fallback_answer()
  -> 明确说明本地图谱未收录，再列联网摘要
  -> 不把它伪装成本地菜谱
```

这就是当前版本的核心变化：**菜谱问题不再完全依赖模型自觉 tool_call，而是先用后端确定性路由兜住关键边界，再用工具循环和 grounded 最终回答把“查到什么”和“能说什么”绑在一起。**
