# 用户发消息后的调用链

本文说明 MiniCookingAgent-Demo 在用户发送一条消息后，从前端、后端、Agent 工具循环，到本地向量召回、菜谱知识图谱精确检索、联网兜底、最终回答回传的完整链路。

当前重要变化：

- `recipe_query_tool` 仍然是暴露给大模型的唯一菜谱工具。
- 向量召回层不暴露为 Agent 工具，而是藏在 `recipe_query_tool` 内部。
- 菜谱类问题会先用本地 `gte-large-zh` 做语义召回，把自然说法归一到图谱标准菜名。
- 归一成功后，再调用原来的 NetworkX 菜谱知识图谱精确查询。
- 本地图谱未命中时，后端仍可自动补一次 `web_search_tool`。

## 0. 总流程图

```mermaid
flowchart TD
    A["用户发送消息<br/>例如：我要吃西红柿炒鸡蛋"] --> B["ChatInput.vue<br/>onSend()"]
    B --> C["chatStore.handleSend()"]
    C --> D["fetch('/chat/stream')"]
    D --> E["Vite proxy<br/>/chat -> localhost:8000"]
    E --> F["FastAPI<br/>backend.app.chat_stream()"]
    F --> G["保存用户消息<br/>add_message(..., human, ...)"]
    G --> H["构造历史上下文<br/>build_agent_history()"]
    H --> I["SSE event_generator()"]
    I --> J["Agent 入口<br/>stream_search_agent(user_text, history)"]
    J --> K["构造工具循环消息<br/>_build_tool_loop_messages()"]
    K --> L["绑定工具模型<br/>get_tool_bound_model()"]
    L --> M["模型决定是否调用工具<br/>model.ainvoke(messages)"]

    M --> N{"模型返回工具调用?"}
    N -->|结构化 tool_calls| O["执行工具<br/>_execute_tool_call()"]
    N -->|文本式工具调用| P["解析文本工具调用<br/>_parse_textual_tool_call()"]
    P --> O
    N -->|无工具调用| Q["直接整理模型文本回答"]

    O --> R{"工具名"}
    R -->|recipe_query_tool| S["菜谱工具入口<br/>query_recipe_kg(query)"]
    R -->|web_search_tool| T["联网搜索<br/>DDGS().text()"]

    S --> U["本地语义召回<br/>semantic_match_recipe()"]
    U --> V["gte-large-zh embedding<br/>models/gte-large-zh"]
    V --> W{"高置信匹配到标准菜名?"}
    W -->|是| X["改写 query<br/>西红柿炒鸡蛋 -> 番茄炒蛋"]
    W -->|否| Y["保留原 query"]
    X --> Z["图谱精确查询<br/>RecipeQuerySystem.query(effective_query)"]
    Y --> Z

    Z --> AA{"图谱命中?"}
    AA -->|命中| AB["记录工具结果<br/>_append_tool_result_to_trace()"]
    AA -->|未命中| AC["自动联网兜底<br/>_execute_web_fallback_after_recipe()"]
    AC --> T
    T --> AB

    AB --> AD["普通 content-only 模型整理最终回答<br/>_emit_final_answer_from_tool_context()"]
    Q --> AE["SSE 返回 content"]
    AD --> AE
    AD -->|最终回答失败| AF["工具结果兜底摘要<br/>_build_partial_tool_answer()"]
    AF --> AE
    AE --> AG["前端解析 SSE<br/>rag_step / trace / content / error"]
    AG --> AH["更新回答、检索过程、参考文献"]
    AE --> AI["后端保存 AI 回复和 rag_trace"]
```

## 0.1 Agent 工具决策流程图

```mermaid
flowchart TD
    A["stream_search_agent()"] --> B["_get_tools()"]
    B --> C["当前注册工具<br/>recipe_query_tool<br/>web_search_tool"]
    C --> D["系统提示词<br/>菜谱问题优先 recipe_query_tool"]
    D --> E["模型返回 AIMessage"]
    E --> F{"有 tool_calls?"}

    F -->|有| G["逐个执行 tool_call"]
    F -->|没有| H["读取模型文本 raw_output"]
    H --> I{"文本像工具调用?"}
    I -->|是| J["_parse_textual_tool_call()<br/>转成真实工具调用"]
    I -->|否| K["普通回答"]
    J --> G

    G --> L{"工具名"}
    L -->|recipe_query_tool| M["进入菜谱工具内部链路"]
    L -->|web_search_tool| N["联网搜索"]

    M --> O{"recipe 结果是否未命中?"}
    O -->|否| P["进入最终回答整理"]
    O -->|是| Q["自动补一次 web_search_tool"]
    Q --> N
    N --> P
    P --> R["普通模型生成最终回答<br/>不再绑定 tools"]
    R --> S["SSE content 返回"]
    K --> S
```

## 0.2 recipe_query_tool 内部流程图

```mermaid
flowchart TD
    A["recipe_query_tool(query)"] --> B["query_recipe_kg(query)"]
    B --> C{"query 是否为空?"}
    C -->|是| C1["返回 query 不能为空"]
    C -->|否| D["检查 KG 文件<br/>config/chem+recipe_kg_updated_fire.pkl"]
    D --> E{"KG 文件存在?"}
    E -->|否| E1["返回知识图谱文件不存在"]
    E -->|是| F["加载或复用 RecipeQuerySystem<br/>_get_recipe_system()"]

    F --> G{"是否为反向查询?<br/>如：哪些菜用了包菜"}
    G -->|是| H["跳过语义菜名改写<br/>保留原 query"]
    G -->|否| I["语义召回标准菜名<br/>semantic_match_recipe()"]

    I --> J["读取或构建向量索引<br/>backend/.cache/recipe_semantic_index.npz"]
    J --> K["加载本地 embedding 模型<br/>models/gte-large-zh"]
    K --> L["用户 query 编码成向量"]
    L --> M["与菜谱向量做 cosine 相似度"]
    M --> N{"top1 分数和 margin 达标?"}
    N -->|是| O["生成 effective_query<br/>例如：番茄炒蛋"]
    N -->|否| H

    O --> P["system.query(effective_query)"]
    H --> P
    P --> Q["QueryParser.parse()"]
    Q --> R["QueryExecutor.execute()"]
    R --> S{"查询类型"}
    S -->|summary| T["_query_summary()"]
    S -->|forward_attr| U["_query_forward_attribute()"]
    S -->|forward_rel| V["_query_forward_relation()"]
    S -->|reverse| W["_query_reverse()"]
    T --> X["result dict"]
    U --> X
    V --> X
    W --> X
    X --> Y["提取 human_readable"]
    Y --> Z["附加结构化摘要<br/>success / query_type / match_mode"]
    Z --> ZA["附加语义召回摘要<br/>标准菜名 / score / margin / 候选"]
    ZA --> ZB["返回字符串给 Agent"]
```

## 0.3 向量召回层流程图

```mermaid
flowchart TD
    A["semantic_match_recipe(query)"] --> B["读取 doc/菜谱.xlsx"]
    B --> C["拼接每道菜的召回文本"]
    C --> D["菜名 + 别名 + 食材 + 配料 + 调味 + 技法 + 口味 + 做法摘要"]
    D --> E{"缓存是否可用?"}
    E -->|是| F["读取 backend/.cache/recipe_semantic_index.npz"]
    E -->|否| G["加载 SentenceTransformer<br/>models/gte-large-zh"]
    G --> H["批量 encode 菜谱文本"]
    H --> I["保存向量缓存 npz"]
    F --> J["加载菜谱向量矩阵"]
    I --> J
    J --> K["encode 用户 query"]
    K --> L["矩阵点积计算 cosine 相似度"]
    L --> M["取 top_k 候选"]
    M --> N{"score >= 0.62 且 margin >= 0.04?"}
    N -->|是| O["accepted=True<br/>返回标准菜名和改写 query"]
    N -->|否| P["accepted=False<br/>不改写，保留原 query"]
```

## 0.4 SSE 回传与前端渲染流程图

```mermaid
flowchart TD
    A["后端 event_generator()"] --> B{"Agent event.type"}
    B -->|rag_step| C["发送检索步骤"]
    B -->|trace| D["发送完整 rag_trace"]
    B -->|thinking| E["发送思考过程"]
    B -->|content| F["发送最终回答片段"]
    B -->|error| G["发送错误信息"]
    B -->|DONE| H["结束 SSE"]

    C --> I["前端 chatStore.handleSend() 解析 SSE"]
    D --> I
    E --> I
    F --> I
    G --> I
    I --> J{"前端按 type 分发"}
    J -->|rag_step| K["追加到 msg.ragSteps"]
    J -->|trace| L["写入 msg.ragTrace"]
    J -->|thinking| M["作为过程折叠展示"]
    J -->|content| N["追加到机器人消息正文"]
    J -->|error| O["追加 Error 文本"]
    H --> P["isLoading=false<br/>abortController=null"]
```

## 1. 前端输入与发送

1. 用户在 `frontend/src/components/Chat/ChatInput.vue` 输入消息。
2. `onSend()` 调用 `chatStore.handleSend()`。
3. `frontend/src/stores/chat.ts` 的 `handleSend()`：
   ```ts
   fetch('/chat/stream', {
     method: 'POST',
     body: JSON.stringify({
       message: text,
       session_id: this.sessionId,
     })
   })
   ```
4. 前端先创建用户消息和机器人占位消息，之后持续接收 SSE 更新。

## 2. Vite 代理到后端

开发模式下，`frontend/vite.config.ts` 将：

```text
/chat
```

代理到：

```text
http://localhost:8000
```

因此前端请求：

```text
POST /chat/stream
```

实际进入后端：

```text
POST http://localhost:8000/chat/stream
```

## 3. FastAPI 接收请求

后端入口在 `backend/app.py`：

```python
chat_stream(body: ChatRequest)
```

主要动作：

1. 创建或更新 session。
2. 保存用户消息：
   ```python
   add_message(body.session_id, "human", body.message)
   ```
3. 读取历史消息：
   ```python
   all_msgs = get_messages(body.session_id)
   ```
4. 构造 Agent 历史上下文：
   ```python
   history = build_agent_history(all_msgs[:-1])
   ```
5. 进入 SSE 生成器：
   ```python
   event_generator()
   ```

## 4. Agent 入口

SSE 生成器调用：

```python
stream_search_agent(body.message, history)
```

当前默认适配器来自 `.env`：

```text
AGENT_ADAPTER=agent_adapter_local_LLM_harness
```

实际实现文件：

```text
backend/agent_adapter_local_LLM_harness.py
```

Agent 会构造工具循环消息，然后绑定当前工具：

```python
_get_tools()
```

当前注册工具只有：

```text
recipe_query_tool
web_search_tool
```

`find_tool` 和 `read_file_tool` 代码仍在，但当前不注册。

## 5. 工具调用解析

模型可能返回两种工具调用形式。

第一种是标准结构化调用：

```json
{
  "name": "recipe_query_tool",
  "args": {"query": "我要吃西红柿炒鸡蛋"}
}
```

第二种是小模型常见的文本式调用：

```text
recipe_query_tool("我要吃西红柿炒鸡蛋")
```

后端兼容这两种形式：

```python
_parse_textual_tool_call(raw_output)
_execute_tool_call(call)
```

## 6. recipe_query_tool 现在的完整链路

当模型调用：

```python
recipe_query_tool("我要吃西红柿炒鸡蛋")
```

实际调用链是：

```text
backend/agent_tools.py
  -> recipe_query_tool(query)
  -> backend.recipe_query_adapter.query_recipe_kg(query)
  -> _get_recipe_system()
  -> _semantic_rewrite_query(query, system)
  -> backend.recipe_semantic_retriever.semantic_match_recipe(query)
  -> SentenceTransformer(models/gte-large-zh)
  -> backend/.cache/recipe_semantic_index.npz
  -> 得到标准菜名：番茄炒蛋
  -> 改写 effective_query：番茄炒蛋
  -> RecipeQuerySystem.query("番茄炒蛋")
  -> QueryParser.parse()
  -> QueryExecutor.execute()
  -> _query_summary()
  -> 返回 human_readable
```

具体例子：

```text
用户：我要吃西红柿炒鸡蛋
```

向量召回层结果：

```text
top1: 番茄炒蛋
score: 0.633
margin: 0.057
accepted: True
```

图谱精查实际收到：

```text
番茄炒蛋
```

图谱返回：

```text
match_mode: exact
query_type: summary
```

## 7. 火力类问题的链路

如果用户问：

```text
西红柿炒鸡蛋的火力怎么控制
```

向量召回层先归一菜名：

```text
西红柿炒鸡蛋 -> 番茄炒蛋
```

然后根据用户意图改写为：

```text
番茄炒蛋的火力调节过程
```

图谱执行的是属性查询：

```text
QueryParser.parse()
  -> type: forward_attr
  -> target_name: fire_control_process
```

最终返回：

```text
番茄炒蛋的 fire_control_process
match_mode: exact
```

## 8. 反向查询为什么跳过向量改写

像下面这种问题：

```text
哪些菜用了包菜
```

它不是“用户说了一道菜的别名”，而是“按食材反查菜品”。

因此 `query_recipe_kg()` 会先判断：

```python
_looks_like_reverse_recipe_query(query)
```

命中后跳过语义菜名改写，直接让原图谱解析器处理：

```text
QueryParser.parse("哪些菜用了包菜")
  -> reverse 查询
```

这样可以避免把“包菜”错误召回成某一道具体菜。

## 9. 向量缓存机制

向量索引文件：

```text
backend/.cache/recipe_semantic_index.npz
```

缓存内容：

```text
version
names
documents
embeddings
```

缓存失效条件：

- `doc/菜谱.xlsx` 修改时间变化
- 菜名列表变化
- 菜谱召回文本变化
- embedding 模型路径变化

首次启动会慢一点，因为需要加载 `gte-large-zh` 并构建菜谱向量。后续会直接读取 `.npz` 缓存。

## 10. 本地图谱未命中后的联网兜底

如果执行 `recipe_query_tool` 后返回内容包含：

```text
success: False
未找到菜品
无法理解的查询格式
```

后端会判断：

```python
_recipe_query_needs_web_fallback(content)
```

如果需要兜底，会自动执行：

```python
_execute_web_fallback_after_recipe(...)
```

也就是补一次：

```text
web_search_tool
```

然后把本地图谱结果和联网搜索结果一起交给最终回答模型整理。

## 11. 最终回答生成

工具执行完成后，后端不会继续调用绑定 tools 的模型，而是切换到普通 content-only 模型：

```python
_emit_final_answer_from_tool_context()
```

它会构造最终回答提示词：

```python
_build_final_prompt(user_text, trace, tool_context)
```

再调用：

```python
_stream_model_answer()
```

如果最终回答模型失败，会走兜底摘要：

```python
_build_partial_tool_answer()
```

这能避免之前那种“工具已经有结果，但最终回答阶段又输出工具调用文本”的问题。

## 12. Trace 与前端展示

工具结果会写入：

```python
trace["tool_calls"]
trace["retrieved_chunks"]
```

其中：

- `recipe_query_tool` 结果写入 `retrieved_chunks`
- `web_search_tool` 结果写入 `retrieved_chunks`
- 语义召回摘要会附在 `recipe_query_tool` 返回文本里

前端会把这些内容展示到：

```text
检索过程
参考文献 / 检索详情
最终回答
```

## 13. 当前完整链路概览

```text
ChatInput.vue onSend()
  -> chatStore.handleSend()
  -> fetch('/chat/stream')
  -> Vite proxy /chat -> localhost:8000
  -> backend.app.chat_stream()
  -> add_message(..., "human", ...)
  -> build_agent_history()
  -> event_generator()
  -> stream_search_agent(user_text, history)
  -> _build_tool_loop_messages()
  -> get_tool_bound_model()
  -> model.ainvoke(messages)
  -> tool_calls 或文本式工具调用
  -> _execute_tool_call()
     -> recipe_query_tool(query)
        -> query_recipe_kg(query)
        -> _get_recipe_system()
        -> _semantic_rewrite_query(query, system)
           -> semantic_match_recipe(query)
           -> 加载/读取 recipe_semantic_index.npz
           -> SentenceTransformer(models/gte-large-zh)
           -> query embedding
           -> cosine 相似度 top_k
           -> 高置信时得到标准菜名
           -> 改写 effective_query
        -> RecipeQuerySystem.query(effective_query)
        -> QueryParser.parse()
        -> QueryExecutor.execute()
        -> _query_summary() / _query_forward_attribute() / _query_reverse()
        -> human_readable + 结构化摘要 + 语义召回摘要
     -> 或 web_search_tool(query)
        -> DDGS().text()
  -> _append_tool_result_to_trace()
  -> recipe 未命中时 _execute_web_fallback_after_recipe()
  -> _emit_final_answer_from_tool_context()
  -> _build_final_prompt()
  -> _stream_model_answer()
  -> SSE: trace / rag_step / content / error
  -> frontend chatStore.handleSend() 解析 SSE
  -> 更新消息、检索过程、参考文献
  -> backend 保存 AI 回复和 rag_trace
```

## 14. 一个真实例子

用户输入：

```text
我要吃西红柿炒鸡蛋
```

现在链路是：

```text
Agent 判断这是菜谱问题
  -> 调 recipe_query_tool
  -> query_recipe_kg("我要吃西红柿炒鸡蛋")
  -> semantic_match_recipe()
  -> gte-large-zh 召回 top1 = 番茄炒蛋
  -> score=0.633，margin=0.057，超过阈值
  -> effective_query = "番茄炒蛋"
  -> RecipeQuerySystem.query("番茄炒蛋")
  -> 图谱 exact 命中
  -> 返回番茄炒蛋完整档案
  -> 最终回答模型整理成自然语言做法
  -> SSE 推给前端展示
```

这就是现在加了向量之后的核心变化：**向量层负责把用户自然表达翻译成图谱标准菜名，图谱层负责精确返回结构化菜谱知识。**
