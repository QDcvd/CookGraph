# 用户发消息后的调用链

本文说明 MiniCookingAgent-Demo 当前版本在用户发送一条消息后，从前端、会话持久化、runtime memory、确定性菜谱路由、Agent 工具循环、Clarification Gate、Context Followup Gate、Query Understanding、结构化反向查询、菜谱混合召回、12K 菜谱知识图谱查询、联网兜底、最终回答约束、SSE 回传和 SQLite 落库的完整链路。

## 当前关键变化

- 前端仍通过 `session_id` 维持当前对话；历史会话从 `/sessions/{session_id}` 恢复消息和 `rag_trace`。
- 后端 `memory_store` 是运行期缓存，`data/memory.sqlite3` 是进程重启后的事实来源。
- 用户消息写入后，会立即抽取长期偏好并写入 `preference_memory`。
- 每轮请求都会构造 Zleap-lite runtime memory，注入长期偏好和当前 session 菜谱上下文。
- `stream_search_agent()` 先跑 `route_query(user_text, history)`，返回 `content` / `direct_chat` / `tool` / `fallback_tool_loop` 四类动作。
- `route_query()` 当前顺序是：图谱统计 → 当前菜品属性追问补全 → `decide_clarification()` → `classify_intent()` → 必要时退回模型工具循环。
- `decide_clarification()` 会处理明确联网、已知菜名、缺菜名属性问题、疑似错字菜名、口味+食材的推荐/单菜歧义，并在需要时返回 ChoicePromptCard。
- `recipe_query_tool` 仍是唯一菜谱工具。Query Understanding、结构化反向查询、菜谱混合召回、别名改写都藏在这个工具内部。
- 默认知识图谱已经切到 `config/2kg_chem+recipe_fire_12K.pkl`：约 7.5 万节点、35.8 万条关系、13214 道菜；旧小图 `chem+recipe_kg_updated_fire.pkl` 仅作为备份。
- `query_recipe_kg()` 现在有三层分流：
  1. **Query Plan 层**（`query_plan.py`）：处理实体查找和组合推荐
  2. **Query Understanding 层**（`query_understanding.py`）：分类意图
  3. **旧链路**：标准菜名短路 + 别名改写 + 混合召回 + 旧 parser 正向查询
- 反向查询通过 `execute_reverse_query()` 直接查图谱节点和边关系。
- 正向菜谱查询如果已经包含图谱标准菜名（如“小炒黄牛肉”），会跳过语义召回，直接进入 `RecipeQuerySystem.query()`，避免别名把标准菜名误改写成别的菜。
- 菜名召回使用 alias、字符 TF-IDF、`gte-large-zh` dense embedding 和 RRF 融合；索引缓存位于 `backend/.cache/recipe_semantic_index.npz`，菜名列表或 Excel 变化后会自动重建。
- `_emit_final_answer_from_tool_context()` 新增 Choice Prompt 集成（web_search_choice_prompt），前端展示选择卡片。
- assistant 最终回答和本轮 `rag_trace`（含 `token_usage` 和 `choice_prompt`）一起持久化。

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
    R --> R1["route_query(user_text, history)"]

    R1 --> R1A{"router action"}
    R1A -->|content| R1B["直接返回澄清文本<br/>可携带 ChoicePromptCard"]
    R1A -->|direct_chat| R1C["打招呼/非菜谱<br/>直接回答"]
    R1A -->|tool| R1D["前置路由指定工具<br/>recipe_query_tool / web_search_tool"]
    R1A -->|fallback_tool_loop| S["进入模型工具循环"]

    R1B --> AH
    R1C --> AH
    R1D --> U

    S --> T{"模型是否调用工具?"}
    T -->|结构化 tool_calls| U["_execute_tool_call()"]
    T -->|文本式工具调用| V["_parse_textual_tool_call()<br/>转真实工具调用"]
    V --> U
    T -->|直接回答| W["SSE content"]

    U --> X{"工具名"}
    X -->|recipe_query_tool| Y["query_recipe_kg(query)"]
    X -->|web_search_tool| Z1["DDGS().text()"]

    Y --> Y0["_get_recipe_system()<br/>加载 2kg_chem+recipe_fire_12K.pkl"]
    Y0 --> YA["build_query_plan()"]
    YA -->|plan 被支持| YB["execute_query_plan()+compose_plan_result()"]
    YA -->|plan 不被支持| YC["classify_intent()"]

    YC --> YD{"intent 类型"}
    YD -->|forward| YE{"已包含图谱标准菜名?"}
    YD -->|reverse| YF["execute_reverse_query()"]
    YD -->|ambiguous| YG["format_ambiguous_query()"]
    YD -->|non_recipe| YH["format_non_recipe()"]
    YD -->|forward_unknown| YI["旧 parser 正向"]

    YF --> Z; YG --> Z; YH --> Z
    YE -->|是| YE1["跳过语义召回<br/>保留用户原 query"]
    YE -->|否| YE2["别名改写 + 混合召回<br/>alias + TF-IDF + gte-large-zh + RRF"]
    YE1 --> YK["RecipeQuerySystem.query()<br/>NetworkX 精确查询"]
    YE2 --> YK
    YI --> YK
    YK --> YL{"未命中且允许联网?"}
    YL -->|是| YM["_execute_web_fallback_after_recipe()"]
    YM --> Z1; YL -->|否| Z; Z1 --> Z

    Z --> AA["_emit_final_answer_from_tool_context()"]
    AA --> AA1["提取 web_choice_prompt"]
    AA1 --> AB{"grounded answer 命中?"}
    AB -->|联网兜底| AC["_build_grounded_web_fallback_answer()"]
    AB -->|联网提议| AD["_build_grounded_web_search_offer_answer()"]
    AB -->|反向查询| AE["_build_grounded_reverse_answer()"]
    AB -->|菜谱命中| AF["_build_grounded_recipe_answer()"]
    AB -->|都不命中| AG["_stream_model_answer()"]

    AC --> AH["SSE trace / rag_step / content / choice_prompt"]
    AD --> AH; AE --> AH; AF --> AH; AG --> AH; W --> AH
    AH --> AI["前端解析 SSE<br/>ChoicePromptCard / TokenUsageBadge"]

    Q --> AJ["流结束后收集 full_response + rag_trace + token_usage"]
    AJ --> AK["add_message(..., ai, rag_trace)"]
    AK --> AL["update_context_from_trace()"]
    AL --> AM["update_recipe_context()"]
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
    J -->|缓存未命中| K["load_chat_session()"]
    K --> L["hydrate 内存 session"]
    J -->|缓存命中| L
    L --> M["返回 messages"]
    M --> N["前端恢复 text/isUser/ragTrace"]

    O["删除会话"] --> P["DELETE /sessions/{session_id}"]
    P --> Q["delete_session()"]
    Q --> R["archive_chat_session()"]
```

## 0.2 前置查询路由 + Clarification Gate

```mermaid
flowchart TD
    A["route_query(user_text, history)"] --> B["kg_dish_names()<br/>读取当前图谱菜名"]
    B --> C{"_looks_like_graph_dish_count_query?"}
    C -->|是| C1["action=tool<br/>recipe_query_tool"]
    C -->|否| D["_recipe_context_from_history()<br/>提取当前菜品"]

    D --> E{"_contextual_attribute_query?"}
    E -->|是| E1["补全当前菜名<br/>action=tool: recipe_query_tool"]
    E -->|否| F["decide_clarification()<br/>clarification_gate.py"]

    F --> G{"clarification.action"}
    G -->|ask| G1["action=content<br/>pending_clarification + choice_prompt"]
    G -->|execute| G2["action=tool<br/>recipe_query_tool / web_search_tool"]
    G -->|none| H["classify_intent()"]

    H --> I{"intent"}
    I -->|greeting/non_recipe| I1["action=direct_chat"]
    I -->|ambiguous| I2["action=content"]
    I -->|recipe_followup + resolved_query| I3["action=tool<br/>recipe_query_tool"]
    I -->|forward/reverse/unknown recipe| I4["action=tool<br/>recipe_query_tool"]
    I -->|未覆盖| I5["action=fallback_tool_loop<br/>进入模型工具循环"]
```

## 0.3 query_recipe_kg 内部链路

```mermaid
flowchart TD
    A["query_recipe_kg(query)"] --> B{"query 为空?"}
    B -->|是| B1["返回错误"]
    B -->|否| C["_get_recipe_system()<br/>默认 12K 大图"]

    C --> D1["build_query_plan()"]
    D1 --> D2{"plan.supported?"}
    D2 -->|是| D3["execute_query_plan()+compose_plan_result()"]
    D2 -->|否| E["classify_intent()"]

    E --> F{"intent"}
    F -->|non_recipe| G1["format_non_recipe()"]
    F -->|ambiguous| G2["format_ambiguous_query()"]
    F -->|reverse| G3["execute_reverse_query()"]
    F -->|forward| G4["正向链路"]
    F -->|forward_unknown| G4
    F -->|legacy| G4

    G3 --> G3a["确定 target_type + relation"]
    G3a --> G3b["图谱实体归一"]
    G3b --> G3c["遍历菜品边关系"]
    G3c --> G3d["返回结构化摘要"]
    G3d --> DONE
    G1 --> DONE; G2 --> DONE

    G4 --> H["旧反向兜底"]
    H --> I{"_contains_graph_dish_name()?"}
    I -->|是| I1["跳过语义召回<br/>避免标准菜名被误改写"]
    I -->|否| I2["别名改写 + 混合召回<br/>alias + TF-IDF + gte-large-zh + RRF"]
    I1 --> J["RecipeQuerySystem.query()<br/>pickle -> networkx.DiGraph"]
    I2 --> J
    J --> K{"未命中且允许联网?"}
    K -->|是| L["标记 fallback_needed"]
    K -->|否| M["格式化为 human_readable"]
    M --> DONE; D3 --> DONE
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
    A["_emit_final_answer_from_tool_context()"] --> A1["提取 web_choice_prompt"]
    A1 --> B["检查 tool_context"]

    B --> C{"有 web_search_tool 结果?"}
    C -->|是| D["_build_grounded_web_fallback_answer()"]
    C -->|否| E{"有 web_search_offer?"}
    E -->|是| F["_build_grounded_web_search_offer_answer()"]
    E -->|否| G{"仅反向查询?"}
    G -->|是| H["_build_grounded_reverse_answer()"]
    G -->|否| I{"正向菜谱结果?"}
    I -->|是| J["_build_grounded_recipe_answer()"]
    I -->|否| K["_stream_model_answer()"]
    K --> L{"超时或异常?"}
    L -->|是| M["_build_partial_tool_answer()"]
    D --> N["直接 yield content + token_usage"]
    F --> N; H --> N; J --> N; M --> N; L -->|否| O["SSE content"]
```

## 0.6 SSE 事件与前端渲染

```mermaid
flowchart TD
    A["后端 event_generator()"] --> B{"event.type"}
    B -->|rag_step| C["检索/工具步骤"]
    B -->|trace| D["完整 rag_trace"]
    B -->|thinking| E["模型思考片段"]
    B -->|content| F["最终回答片段"]
    B -->|token_usage| G["Token 用量"]
    B -->|choice_prompt| H["选择卡片"]
    B -->|session_title| I["更新会话标题"]
    B -->|error| J["错误信息"]
    B -->|DONE| K["结束流"]

    C --> L["chatStore.handleSend() 解析 SSE"]
    D --> L; E --> L; F --> L; G --> L; H --> L; I --> L; J --> L
    L --> M{"前端按 type 分发"}
    M -->|rag_step| N["追加 msg.ragSteps"]
    M -->|trace| O["写入 msg.ragTrace"]
    M -->|thinking| P["追加过程展示"]
    M -->|content| Q["追加 assistant 正文"]
    M -->|token_usage| R["更新 TokenUsageBadge"]
    M -->|choice_prompt| S["显示 ChoicePromptCard"]
    M -->|session_title| T["更新 sessions 列表"]
    M -->|error| U["展示 Error"]
    K --> V["isLoading=false"]
```

## 1. 关键文件

```text
frontend/src/stores/chat.ts
  → backend/app.py
    → backend/memory_store.py / chat_persistence.py
    → backend/context_manager.py
    → backend/session_recipe_context.py
    → backend/agent_adapter_local_LLM_harness.py
      → route_query()
        → clarification_gate.py / query_understanding.py
      → stream_search_agent()
        → recipe_query_tool
          → backend/recipe_query_adapter.py
            → backend/query_plan.py
            → backend/query_understanding.py
            → backend/query_executor.py / answer_composer.py
            → backend/recipe_semantic_retriever.py
        → web_search_tool
      → _emit_final_answer_from_tool_context()
        → grounded answer cascade
    → backend/token_usage_tracker.py
    → update_context_from_trace() / update_recipe_context()
```
