# 用户发消息后的调用链

本文说明 MiniCookingAgent-Demo 当前版本在用户发送一条消息后，从前端、会话持久化、runtime memory、确定性菜谱路由、Agent 工具循环，到 Query Understanding、结构化反向查询、菜谱混合召回、知识图谱查询、联网兜底、最终回答约束、SSE 回传和 SQLite 落库的完整链路。

## 当前关键变化

- 前端仍通过 `session_id` 维持当前对话；历史会话从 `/sessions/{session_id}` 恢复消息和 `rag_trace`。
- 后端 `memory_store` 是运行期缓存，`data/memory.sqlite3` 是进程重启后的事实来源。
- 用户消息写入后，会立即抽取长期偏好并写入 `preference_memory`。
- 每轮请求都会构造 Zleap-lite runtime memory，注入长期偏好和当前 session 菜谱上下文。
- `stream_search_agent()` 先跑 `_preflight_recipe_action()`，对无菜名属性追问、明确菜名覆盖旧上下文等高风险问题做确定性路由，再决定是否进入模型工具循环。
- `recipe_query_tool` 仍是唯一菜谱工具。Query Understanding、结构化反向查询、菜谱混合召回、别名改写都藏在这个工具内部。
- `query_recipe_kg()` 现在有三层分流：
  1. **Query Plan 层**（`query_plan.py`）：处理实体查找和组合推荐
  2. **Query Understanding 层**（`query_understanding.py`）：分类意图为 forward/reverse/ambiguous/non-recipe/forward-unknown
  3. **旧链路**：别名改写 + 语义召回 + 旧 parser 正向查询
- 反向查询（如"牛肉怎么做""哪些菜用了牛肉"）会先产出结构化 `QueryIntent(reverse_query)`，由 `execute_reverse_query()` 直接查图谱，不进入旧 parser。
- 反向查询的归并值输出必须展示，如"归并食材：牛肉、黄牛肉、牛里脊、肥牛"。
- 多类型歧义词（如"蒜蓉"）返回 `ambiguous_query`，不硬拆。
- `_emit_final_answer_from_tool_context()` 有 4 级 grounded answer 兜底：
  1. 联网兜底结果 → `_build_grounded_web_fallback_answer()`
  2. 图谱未命中联网提议 → `_build_grounded_web_search_offer_answer()`
  3. 本地图谱反向查询结果 → `_build_grounded_reverse_answer()`
  4. 正向菜谱命中结果 → `_build_grounded_recipe_answer()`
  5. 以上都不满足时 → content-only 模型
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
    R --> R1["_preflight_recipe_action()<br/>确定性路由"]
    R1 -->|预检命中| R2["_execute_forced_tool_call()<br/>跳过模型工具循环"]
    R1 -->|预检未命中| S["工具循环模型<br/>recipe_query_tool + web_search_tool"]
    R2 --> Z["_append_tool_result_to_trace()"]

    S --> T{"模型是否调用工具?"}
    T -->|结构化 tool_calls| U["_execute_tool_call()"]
    T -->|文本式工具调用| V["_parse_textual_tool_call()<br/>转真实工具调用"]
    V --> U
    T -->|直接回答| W["SSE content"]

    U --> X{"工具名"}
    X -->|recipe_query_tool| Y["query_recipe_kg(query)"]
    X -->|web_search_tool| Z1["DDGS().text()"]

    Y --> YA["build_query_plan()<br/>实体查找/组合推荐"]
    YA -->|plan 被支持| YB["execute_query_plan()<br/>+ compose_plan_result()"]
    YA -->|plan 不被支持| YC["classify_intent()<br/>Query Understanding"]

    YC --> YD{"intent 类型"}
    YD -->|forward_recipe_query| YE["走别名改写 + 语义召回"]
    YD -->|reverse_query| YF["execute_reverse_query()<br/>直接查图谱节点和边"]
    YD -->|ambiguous_query| YG["format_ambiguous_query()<br/>返回结构化歧义"]
    YD -->|non_recipe_query| YH["format_non_recipe()<br/>拒答"]
    YD -->|forward_unknown_recipe_query| YI["走旧 parser 正向查询"]
    YD -->|legacy_forward_parser| YI

    YF --> Z
    YG --> Z
    YH --> Z

    YE --> YJ["_alias_rewrite_query()<br/>+ _semantic_rewrite_query()"]
    YJ --> YK["RecipeQuerySystem.query()<br/>NetworkX 知识图谱"]
    YI --> YK

    YK --> YL{"图谱是否未命中且允许联网兜底?"}
    YL -->|是| YM["_execute_web_fallback_after_recipe()"]
    YM --> Z1
    YL -->|否| Z
    Z1 --> Z

    Z --> AA["_emit_final_answer_from_tool_context()<br/>4 级 grounded answer + 模型"]
    AA --> AB{"grounded answer 命中?"}
    AB -->|联网兜底| AC["_build_grounded_web_fallback_answer()"]
    AB -->|联网提议| AD["_build_grounded_web_search_offer_answer()"]
    AB -->|反向查询| AE["_build_grounded_reverse_answer()"]
    AB -->|菜谱命中| AF["_build_grounded_recipe_answer()"]
    AB -->|都不命中| AG["_stream_model_answer()<br/>content-only 模型"]

    AC --> AH["SSE trace / rag_step / content / token_usage"]
    AD --> AH
    AE --> AH
    AF --> AH
    AG --> AH
    W --> AH
    AH --> AI["前端解析 SSE<br/>更新文本、trace、检索过程"]

    Q --> AJ["流结束后收集 full_response + rag_trace + token_usage"]
    AJ --> AK["add_message(..., ai, rag_trace)<br/>写内存 + chat_messages.rag_trace_json"]
    AK --> AL["update_context_from_trace()<br/>更新最近菜品/联网摘要"]
    AL --> AM["update_recipe_context()<br/>写 chat_sessions.recipe_context_json"]
    AM --> AN["SSE [DONE]"]
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
  │   ├─ rag_trace_json（含 token_usage）
  │   └─ created_at / deleted_at
  └─ preference_memory
      └─ 跨会话用户偏好
```

## 0.2 Agent 工具决策 + 预检路由

```mermaid
flowchart TD
    A["stream_search_agent(user_text, history)"] --> A1["_runtime_memory_from_history()"]
    A1 --> A2["_build_tool_loop_messages()"]
    A2 --> B["_preflight_recipe_action()<br/>确定性路由"]

    B --> B1{"是否匹配预检规则?"}
    B1 -->|反向食材查询| B2["_execute_forced_tool_call()<br/>recipe_query_tool"]
    B1 -->|明确新菜名覆盖| B2
    B1 -->|裸菜式短语| B2
    B1 -->|上下文属性追问| B3["补充菜名后调用工具"]
    B1 -->|缺菜名澄清| B4["直接生成澄清回答"]
    B1 -->|未匹配| C["进入模型工具循环"]

    B2 --> D["_append_tool_result_to_trace()"]
    B3 --> D
    D --> E["_emit_final_answer_from_tool_context()"]

    C --> F["get_tool_bound_model()<br/>model.ainvoke(messages)"]
    F --> G{"AIMessage 有 tool_calls?"}
    G -->|有| H["逐个执行 tool_call"]
    G -->|没有| I["读取 raw_output"]
    I --> J{"像文本式工具调用?"}
    J -->|是| K["_parse_textual_tool_call()"]
    J -->|否| L["普通文本回答 / SSE content"]
    K --> H
    H --> M["_append_tool_result_to_trace()"]
    M --> E
```

## 0.3 query_recipe_kg 内部链路

```mermaid
flowchart TD
    A["query_recipe_kg(query)"] --> B{"query 为空?"}
    B -->|是| B1["返回错误"]
    B -->|否| C{"KG 文件存在?"}
    C -->|否| C1["返回错误"]
    C -->|是| D["_get_recipe_system()"]

    D --> D1["build_query_plan()"]

    D1 --> D2{"plan.supported?"}
    D2 -->|是| D3["execute_query_plan()"]
    D3 --> D4["compose_plan_result()"]
    D4 --> DONE["返回结构化结果"]

    D2 -->|否| E["classify_intent()"]

    E --> F{"intent 类型"}
    F -->|non_recipe_query| G1["format_non_recipe()"]
    F -->|ambiguous_query| G2["format_ambiguous_query()"]
    F -->|reverse_query| G3["execute_reverse_query()"]
    F -->|forward_recipe_query| G4["进入正向链路"]
    F -->|forward_unknown_recipe_query| G4
    F -->|legacy_forward_parser| G4

    G3 --> G3a["确定 target_type + relation"]
    G3a --> G3b["图谱实体归一 + alias 展开"]
    G3b --> G3c["遍历菜品边关系"]
    G3c --> G3d["返回结构化摘要"]
    G3d --> DONE
    G1 --> DONE
    G2 --> DONE

    G4 --> H["旧反向兜底"]
    H -->|未命中| I["别名改写 + 语义召回"]
    I --> J["RecipeQuerySystem.query()"]
    J --> K{"图谱未命中且允许联网?"}
    K -->|是| L["标记 fallback_needed"]
    K -->|否| M["格式化为 human_readable"]
    M --> DONE
```

## 0.4 runtime memory 注入链路

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
    K --> L["工具循环和最终回答都能看到 runtime memory"]
```

## 0.5 最终回答约束链路

```mermaid
flowchart TD
    A["_emit_final_answer_from_tool_context()"] --> B["检查 tool_context"]

    B --> C{"有 web_search_tool 结果?"}
    C -->|是| D["_build_grounded_web_fallback_answer()"]
    D --> E["直接 yield content"]

    C -->|否| F{"有 web_search_offer?"}
    F -->|是| G["_build_grounded_web_search_offer_answer()"]
    G --> E

    F -->|否| H{"仅反向查询?"}
    H -->|是| I["_build_grounded_reverse_answer()"]
    I --> E

    H -->|否| J{"正向菜谱结果?"}
    J -->|是| K["_build_grounded_recipe_answer()"]
    K --> E

    J -->|否| L["_stream_model_answer()<br/>content-only 模型"]
    L --> M{"超时或异常?"}
    M -->|是| N["_build_partial_tool_answer()"]
    N --> E
    M -->|否| O["SSE content"]
```

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
    D --> K; E --> K; F --> K; G --> K; H --> K; I --> K
    K --> L{"前端按 type 分发"}
    L -->|rag_step| M["追加 msg.ragSteps"]
    L -->|trace| N["写入 msg.ragTrace"]
    L -->|thinking| O["追加过程展示"]
    L -->|content| P["追加 assistant 正文"]
    L -->|token_usage| Q["更新 TokenUsageBadge"]
    L -->|session_title| R["更新 sessions 列表"]
    L -->|error| S["展示 Error"]
    J --> T["isLoading=false"]
```

## 1. 关键文件

```text
frontend/src/stores/chat.ts           # handleSend() SSE 解析
  → backend/app.py                     # POST /chat/stream
    → backend/memory_store.py          # session 缓存 / SQLite 双写
    → backend/chat_persistence.py      # SQLite 层
    → backend/context_manager.py       # 历史 + runtime memory
    → backend/preference_memory.py     # 用户偏好
    → backend/session_recipe_context.py
    → backend/agent_adapter_local_LLM_harness.py
      → _preflight_recipe_action()
      → stream_search_agent()
        → recipe_query_tool
          → backend/recipe_query_adapter.py
            → backend/query_plan.py
            → backend/query_understanding.py
            → backend/query_executor.py
            → backend/answer_composer.py
            → backend/recipe_semantic_retriever.py
            → backend/4-V1菜谱查询recipe_query-查询火力.py
        → web_search_tool
      → _emit_final_answer_from_tool_context()
        → 4 级 grounded answer / _stream_model_answer()
    → backend/token_usage_tracker.py
    → update_context_from_trace()
    → update_recipe_context()
```
