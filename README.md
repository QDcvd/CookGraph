# MiniCookingAgent-Demo — 迷你烹饪问答机器人

这是一个迷你烹饪问答机器人项目，基于 FastAPI + Vue + OpenAI 兼容本地/远端模型工具循环实现，面向菜谱、食材和烹饪技法等中文问答场景。

## 能做什么

- 直接回答烹饪、食材、菜谱、菜单相关问题。
- 使用 `recipe_query_tool` 查询本地菜谱知识图谱（38814 个节点、15 万条关系），支持正向属性查询、反向关系查询、完整档案查询。
- **Query Understanding**：反向查询先经过结构化意图识别，区分食材、技法、口味、菜系；不确定时追问，不靠模型硬猜。
- **图谱节点名词召回**：反向查询的向量化对象是图谱节点名词（如 `牛肉`、`香辣味`、`川菜`、`蒸制`），不是菜谱正文。
- 使用 `web_search_tool` 联网搜索公开网页资料（本地图谱未命中时自动兜底）。
- 可自动通过 SSH 隧道连接远端 LM Studio 的 OpenAI 兼容 API。
- **对话持久化**：后端重启后用同一个 `session_id` 恢复对话历史和菜谱上下文。
- **偏好记忆**：跨会话记住用户偏好（通过 SQLite 持久化）。

## 项目结构

```text
miniCookingAgent-Demo/
├── backend/
│   ├── app.py                              # FastAPI 主应用
│   ├── context_manager.py                  # 对话上下文组装（Zleap 风格）
│   ├── agent_adapter_local_LLM_harness.py  # 推荐适配器（工具循环）
│   ├── agent_adapter_local_LLM.py          # 本地 vLLM 基础版
│   ├── agent_adapter.py                    # DeepSeek API 适配器
│   ├── agent_tools.py                      # 工具定义（@tool 装饰器）
│   ├── tool_calling.py                     # 工具调用解析/执行/trace
│   ├── recipe_query_adapter.py             # 菜谱查询适配器
│   ├── query_understanding.py              # 查询意图识别（正向/反向/歧义/非菜谱）
│   ├── recipe_semantic_retriever.py        # 语义召回改写层
│   ├── memory_store.py                     # 会话内存缓存 + SQLite 持久化
│   ├── chat_persistence.py                 # SQLite 读写层（chat_sessions / chat_messages）
│   ├── preference_memory.py                # 用户偏好记忆存储
│   ├── session_recipe_context.py           # 当前会话菜谱上下文管理
│   └── 4-V1菜谱查询recipe_query-查询火力.py  # 菜谱知识图谱查询系统
├── config/
│   ├── chem+recipe_kg_updated_fire.pkl     # 菜谱知识图谱（约 16MB）
│   ├── recepi/                             # 实体/关系/属性配置文件
│   ├── recipe_aliases.json                 # 菜名同义词典
│   └── reverse_entity_aliases.json         # 反向查询实体归并配置
├── frontend/
│   ├── src/
│   └── package.json
├── test/
│   ├── recipe_test_data.py                   # 150 条单轮测试用例数据
│   ├── run_recall_test.py                    # 召回率测试运行器（可联网兜底）
│   ├── multiturn_test_data.py                # 20 个多轮对话测试 case
│   ├── run_multiturn_dialogue_test.py        # 多轮对话测试运行器（真实 agent 链路）
│   ├── test_query_understanding.py           # Query Understanding 单元测试
│   ├── test_chat_persistence.py              # 对话持久化单元测试
│   └── test_zleap_lite_memory.py             # Zleap-lite 记忆系统测试
├── doc/
│   ├── zleap_lite_chat_persistence_plan.md   # 持久化设计方案
│   └── memory_zleap_lite_plan.md             # 记忆系统设计
├── Dockerfile                                # 依赖环境 Docker 镜像
├── docker/
│   └── docker-entrypoint.sh
├── deploy_uv.sh                              # uv 一键部署脚本
├── start_docker.sh                           # Docker 一键构建并启动脚本（Ubuntu/WSL/Linux）
├── .env.example
├── requirements.txt
└── start.py
```

## 新机器部署

推荐使用 uv 部署脚本。它会安装后端依赖、前端依赖，并默认下载 `gte-large-zh` embedding 模型到 `models/gte-large-zh`：

```bash
bash deploy_uv.sh
```

常用选项：

```bash
# 跳过模型下载
bash deploy_uv.sh --skip-model

# 跳过前端依赖，只安装后端依赖并下载 embedding 模型
bash deploy_uv.sh --skip-frontend

# 部署完成后直接启动
bash deploy_uv.sh --start

# 网络或 CI 环境下顺序安装，便于定位失败日志
bash deploy_uv.sh --no-parallel
```

脚本默认使用国内镜像源：

```ini
UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
NPM_REGISTRY=https://registry.npmmirror.com
MODEL_SOURCE=modelscope
MODELSCOPE_MODEL_ID=AI-ModelScope/gte-large-zh
UV_CONCURRENT_DOWNLOADS=8
UV_CONCURRENT_BUILDS=4
```

如果 HuggingFace 镜像报 `SSL: UNEXPECTED_EOF_WHILE_READING`，优先使用默认的 `MODEL_SOURCE=modelscope`。

Windows 建议：

- 推荐在 Git Bash 里运行 `bash deploy_uv.sh`。
- 如果系统里的 `bash` 是 `C:\Windows\System32\bash.exe`，它会进入 WSL。不要用 WSL bash 混跑 Windows `.venv`。

## 快速启动

PowerShell：

```powershell
cd E:\miniCookingAgent-Demo
.\.venv\Scripts\python.exe start.py
```

Git Bash：

```bash
cd /e/miniCookingAgent-Demo
.venv/Scripts/python.exe start.py
```

默认会启动：

- 后端：`http://localhost:8000`
- 前端：`http://localhost:5173`
- 默认适配器：`agent_adapter_local_LLM_harness`

调试大模型返回值：

```bash
.venv/Scripts/python.exe start.py --debug-llm
```

禁用远端 LM Studio SSH 隧道：

```bash
.venv/Scripts/python.exe start.py --no-llm-tunnel
```

手动指定适配器：

```bash
.venv/Scripts/python.exe start.py --adapter agent_adapter_local_LLM_harness
.venv/Scripts/python.exe start.py --adapter agent_adapter_local_LLM
.venv/Scripts/python.exe start.py --adapter agent_adapter
```

## Docker 一键启动

项目提供 Docker 一键启动脚本。镜像会在构建时安装 Python/前端依赖，并下载 `gte-large-zh` embedding 模型到 `/opt/minicook/models/gte-large-zh`；项目源码和 `.env` 仍通过 volume 从本机读取。

`start_docker.sh` **只支持在 Ubuntu / WSL / Linux shell 内运行**。Windows 用户请先进入 WSL/Ubuntu，再进入项目目录执行脚本；不要在 PowerShell、CMD 或 Windows Git Bash 中直接运行这个脚本。

WSL/Ubuntu 示例：

```bash
cd /mnt/e/miniCookingAgent-Demo
bash start_docker.sh
```

如果项目在其他盘符，例如你在 WSL 中看到的路径是 `/mnt/g/miniCookingAgent-Demo`，就进入对应目录后再执行：

```bash
cd /mnt/g/miniCookingAgent-Demo
bash start_docker.sh
```

运行前确认 WSL 内可用 Docker：

```bash
docker --version
docker ps
```

如果出现 `set: pipefail: invalid option name` 或类似 `^M` 报错，说明脚本在当前副本里被保存成了 Windows CRLF 换行。先修正换行：

```bash
sed -i 's/\r$//' start_docker.sh
```

脚本会自动：

- 构建或复用 `minicooking-agent-env` 镜像。
- 挂载当前项目到容器 `/workspace`。
- 映射后端 `8000` 和前端 `5173`。
- 启动 `python start.py --adapter agent_adapter_local_LLM_harness`。
- 通过 `MINICOOK_EMBEDDING_MODEL_DIR` 使用镜像内置 embedding 模型。
- 安装 `libgomp1`，供 `scikit-learn`/向量检索相关依赖在 slim 镜像内正常加载。

常用选项：

```bash
# 强制重新构建镜像
bash start_docker.sh --rebuild

# 改宿主机端口
bash start_docker.sh --backend-port 18000 --frontend-port 15173

# 切换模型下载来源
MODEL_SOURCE=huggingface bash start_docker.sh --rebuild
```

Docker 构建默认使用镜像源，并并行安装 Python 与前端依赖。可覆盖：

```ini
APT_MIRROR=mirrors.aliyun.com
UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
NPM_REGISTRY=https://registry.npmmirror.com
MODEL_SOURCE=modelscope
MODELSCOPE_MODEL_ID=AI-ModelScope/gte-large-zh
```

## 本地模型配置

`.env` 里已经按本地模型模式配置：

```ini
LLM_MODEL=qwen3-4b
LLM_BASE_URL=http://127.0.0.1:51234/v1
LLM_API_KEY=not-needed
LLM_MAX_TOKENS=2048
LLM_NO_THINK=1
MAX_MODEL_LEN=32768
MAX_TOOL_TURNS=10
MAX_TOTAL_TOOL_CALLS=16
MAX_CONSECUTIVE_TOOL_CALLS=5
```

如果远端 LM Studio 只监听 `127.0.0.1:1234`，可以开启 SSH 隧道：

```ini
LLM_SSH_TUNNEL=0
# LLM_REMOTE_HOST=your.server.com
# LLM_REMOTE_USER=ubuntu
# LLM_REMOTE_PASSWORD=your_password
# LLM_REMOTE_PORT=1234
# LLM_LOCAL_PORT=51234
```

启动器会把本地 `127.0.0.1:51234` 转发到远端 `127.0.0.1:1234`，后端统一通过 `LLM_BASE_URL=http://127.0.0.1:51234/v1` 调模型。

## 单独启动

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

前端：

```bash
cd frontend
npm run dev
```

## 工具循环说明

推荐适配器是 `backend/agent_adapter_local_LLM_harness.py`。它会把运行时实际注册的工具列表塞进中文系统提示词，并让模型通过结构化 `tool_call` 调用：

- `recipe_query_tool(query)`：查询本地菜谱知识图谱，支持菜品做法、备菜过程、烹饪过程、火力调节、食材、调料、技法、口味、菜系等查询。
- `web_search_tool(query)`：联网搜索公开网页信息（仅当菜谱知识图谱未命中且需要联网补充时使用）。

工具定义统一在 `backend/agent_tools.py` 中注册，`_get_tools()` 返回当前可用工具列表。新增工具时只需在 `agent_tools.py` 添加函数并用 `@tool` 装饰，然后在 `_get_tools()` 中返回即可，无需修改 harness 主循环。

`recipe_query_tool` 内部先进入 `backend/query_understanding.py`：

- 直接命中标准菜名或菜名别名时，继续走旧正向查询链路，保持高命中率。
- 反向查询会产出结构化 `QueryIntent(reverse_query)`，再由 `execute_reverse_query()` 直接查图谱边，不进入旧自然语言 parser。
- 短词如 `花甲`、`川菜`、`香辣味`、`蒸制` 会按图谱实体类型走反向查询。
- `牛肉怎么做`、`虾怎么做` 这类“食材 + 怎么做”归为反向食材查询，先列本地图谱明确命中的菜。
- `蒜蓉` 这类多类型歧义词会要求用户补充，不硬拆、不联网。

为了保护远端模型服务，工具循环有三层限制：

- `MAX_TOOL_TURNS`：最多模型工具回合数（默认 10）。
- `MAX_TOTAL_TOOL_CALLS`：本轮总工具调用上限（默认 16）。
- `MAX_CONSECUTIVE_TOOL_CALLS`：同一个工具最多连续调用次数（默认 5）。

达到限制后，后端会基于已掌握的工具结果生成阶段性总结，而不是继续无限调用。

## 多轮记忆与持久化

项目实现了轻量级的 Zleap-lite 记忆系统：

1. **会话菜谱上下文**：当前会话的最近菜品、查询、菜谱摘要，注入到每轮 prompt 中，支持"它蒸多久""刚才那道菜"等指代追问。
2. **用户偏好记忆**：通过 SQLite 跨会话保存用户偏好（如口味偏好、常用食材）。
3. **对话持久化**：后端重启后用同一个 `session_id` 恢复完整对话历史、trace 和菜谱上下文。

### 存储结构

| 表 | 用途 |
| --- | --- |
| `chat_sessions` | 会话元信息 + 菜谱上下文快照 |
| `chat_messages` | 顺序消息 + assistant 的 `rag_trace_json` |

写入路径同时写内存缓存和 SQLite；`get_session()` 先查内存，未命中则从 SQLite hydrate。

## 调试与测试

### 编译检查

```bash
python -m compileall backend start.py
```

### 测试总览

| 测试类型 | 运行命令 | 用例数 | 覆盖 |
| ------- | ------- | ----- | ---- |
| Query Understanding | `python -m unittest test.test_query_understanding` | 17 项 | 正向/反向/歧义/非菜谱意图分类 |
| 单轮召回率 | `python test/run_recall_test.py --phase all` | 150 条 | 正向/反向/模糊/边界 + 联网兜底 |
| 多轮对话 | `python test/run_multiturn_dialogue_test.py --all` | 20 个 case | 记忆/抗干扰/逻辑自洽 + DeepSeek LLM 裁判 |
| 持久化 | `python test/test_chat_persistence.py` | 6 项 | SQLite round-trip / hydrate / archive |

### 多轮对话测试

测试 agent 行为的三大能力：

```bash
# 全部类别
python test/run_multiturn_dialogue_test.py --all

# 单独跑某一类
python test/run_multiturn_dialogue_test.py --category memory
python test/run_multiturn_dialogue_test.py --category distraction
python test/run_multiturn_dialogue_test.py --category contradiction
```

多轮测试使用**真实 agent 链路**（`stream_search_agent`），不走 mock。DeepSeek 作为 LLM 裁判，输出结构化 JSON 判定每个 case 是否通过。配置 `DEEPSEEK_API_KEY` 环境变量启用裁判。

### 召回率测试

```bash
# 第一阶段（核心 50 条）
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 1

# 第二阶段（扩展 100 条）
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 2

# 全量 150 条
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase all
```

### 测试输出

- `test/test_results.json` — 单轮测试详细结果
- `test/test_report.md` — 单轮测试报告
- `test/multiturn_test_results.json` — 多轮测试详细结果
- `test/multiturn_test_report.md` — 多轮测试报告

### 知识图谱文件

菜谱知识图谱位于 `config/chem+recipe_kg_updated_fire.pkl`（约 16MB），包含 **38814 个节点、147352 条关系**。依赖 `networkx` 库反序列化。

### 验证工具列表注册

```bash
python -c "from backend.agent_tools import _get_tools; print([t.name for t in _get_tools()])"
# 期望输出：['web_search_tool', 'recipe_query_tool']
```

### 验证文本式工具调用解析

```bash
python -c "from backend.tool_calling import _parse_textual_tool_call; print(_parse_textual_tool_call('recipe_query_tool(\"西红柿炒鸡蛋怎么做\")'))"
# 期望输出：{'name': 'recipe_query_tool', 'args': {'query': '西红柿炒鸡蛋怎么做'}}
```

### 持久化单元测试

```bash
PYTHONIOENCODING=utf-8 python test/test_chat_persistence.py
```
