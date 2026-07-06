# MiniCookingAgent-Demo — 迷你烹饪问答机器人

这是一个迷你烹饪问答机器人项目，基于 FastAPI + Vue + 本地模型工具循环实现，面向菜谱、食材、菜单文件和项目资料检索等中文问答场景。

## 能做什么

- 直接回答烹饪、食材、菜谱、菜单相关问题。
- 使用 `recipe_query_tool` 查询本地菜谱知识图谱（38814 个节点、15 万条关系），支持正向属性查询、反向关系查询、完整档案查询。
- 使用 `web_search_tool` 联网搜索公开网页资料。
- 可自动通过 SSH 隧道连接远端 LM Studio 的 OpenAI 兼容 API。

## 项目结构

```text
miniCookingAgent-Demo/
├── backend/
│   ├── app.py                              # FastAPI 主应用
│   ├── context_manager.py                  # 对话上下文组装
│   ├── agent_adapter_local_LLM_harness.py  # 推荐适配器（工具循环）
│   ├── agent_adapter_local_LLM.py          # 本地 vLLM 基础版
│   ├── agent_adapter.py                    # DeepSeek API 适配器
│   ├── agent_tools.py                      # 工具定义（@tool 装饰器）
│   ├── tool_calling.py                     # 工具调用解析/执行/trace
│   ├── recipe_query_adapter.py             # 菜谱查询适配器
│   ├── recipe_semantic_retriever.py        # 语义召回改写层
│   └── 4-V1菜谱查询recipe_query-查询火力.py  # 菜谱知识图谱查询系统
├── config/
│   ├── chem+recipe_kg_updated_fire.pkl     # 菜谱知识图谱（约 16MB）
│   ├── recepi/                             # 实体/关系/属性配置文件
│   └── recipe_aliases.json                 # 菜名同义词典
├── frontend/
│   ├── src/
│   └── package.json
├── test/
│   ├── recipe_test_data.py                 # 100 条测试用例数据
│   └── run_recall_test.py                  # 召回率测试运行器
├── reference/
├── .env.example
├── .gitignore
├── setup.sh
└── start.py
```

## 快速启动

推荐在 Git Bash 或 Anaconda Prompt 中运行：

```bash
cd /g/miniCookingAgent-Demo
python start.py
```

默认会启动：

- 后端：`http://localhost:8000`
- 前端：`http://localhost:5173`
- 默认适配器：`agent_adapter_local_LLM_harness`

调试大模型返回值：

```bash
python start.py --debug-llm
```

禁用远端 LM Studio SSH 隧道：

```bash
python start.py --no-llm-tunnel
```

手动指定适配器：

```bash
python start.py --adapter agent_adapter_local_LLM_harness
python start.py --adapter agent_adapter_local_LLM
python start.py --adapter agent_adapter
```

## 首次安装

```bash
bash setup.sh
```

脚本会创建 `minicook` conda 环境，并安装前端依赖和后端依赖。

手动安装方式：

```bash
conda create -n minicook python=3.11 -y
conda activate minicook
pip install "fastapi[standard]" uvicorn langchain langchain-openai langgraph python-dotenv ddgs paramiko networkx
cd frontend
npm install
cd ..
python start.py
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

```bash
conda activate minicook
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
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

为了保护远端模型服务，工具循环有三层限制：

- `MAX_TOOL_TURNS`：最多模型工具回合数。
- `MAX_TOTAL_TOOL_CALLS`：本轮总工具调用上限。
- `MAX_CONSECUTIVE_TOOL_CALLS`：同一个工具最多连续调用次数。

达到限制后，后端会基于已掌握的工具结果生成阶段性总结，而不是继续无限调用。

## 调试与测试

### 单独测试菜谱知识图谱查询

```bash
# Git Bash（推荐）
PYTHONIOENCODING=utf-8 /d/anaconda3/envs/bigdog/python.exe "backend/4-V1菜谱查询recipe_query-查询火力.py" -k "config/chem+recipe_kg_updated_fire.pkl" "小炒黄牛肉的做法"

# PowerShell
$env:PYTHONIOENCODING='utf-8'; python "backend/4-V1菜谱查询recipe_query-查询火力.py" -k "config/chem+recipe_kg_updated_fire.pkl" "小炒黄牛肉的做法"
```

如果不加 `PYTHONIOENCODING=utf-8`，Windows 控制台（GBK 编码）会输出乱码。

支持多种查询类型：

| 查询类型 | 示例 |
|---------|------|
| 正向属性查询（做法/火力/备菜） | `"小炒黄牛肉的烹饪过程"` |
| 正向属性查询（配料） | `"小炒黄牛肉的配料有哪些"` |
| 完整档案查询 | `"西红柿炒鸡蛋"` |
| 反向查询（哪些菜用了某技法） | `"哪些菜用了炝炒技法"` |
| 反向查询（哪些菜用了某食材） | `"主要食材包含黄牛肉的菜"` |

### 验证 adapter 包装

```bash
# 通过 adapter 调用（自动处理 KG 路径、编码、异常兜底）
PYTHONIOENCODING=utf-8 python -c "
from backend.recipe_query_adapter import query_recipe_kg
print(query_recipe_kg('小炒黄牛肉的做法')[:2000])
"
```

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

### 编译检查

```bash
python -m compileall backend start.py
```

### 知识图谱文件

菜谱知识图谱位于 `config/chem+recipe_kg_updated_fire.pkl`（约 16MB），包含 **38814 个节点、147352 条关系**。依赖 `networkx` 库反序列化。

## 召回率测试

项目内置了 **100 条测试用例** 的召回率测试框架，覆盖正向查询、反向查询、口语化、场景化、边界情况等 8 个维度。

### 运行测试

```bash
# 第一阶段（核心 55 条）
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 1

# 第二阶段（扩展 45 条）
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase 2

# 全量 100 条
PYTHONIOENCODING=utf-8 python test/run_recall_test.py --phase all
```

### 测试输出

- **`test/test_results.json`** — 每条用例的详细结果（含耗时、命中状态）
- **`test/test_report.md`** — 召回率报告（总体 + 各维度召回率 + 失败用例列表）

### 测试用例分布

| 维度 | 条数 | 说明 |
|------|------|------|
| 正向-属性 | 1-20 | 精确菜名+属性查询 |
| 正向-档案 | 21-26 | 纯菜名完整档案 |
| 反向查询 | 27-40 | 技法/味道/菜系/食材反查 |
| 模糊/口语化 | 41-55 | 别名、缩写、泛称 |
| 场景化对话 | 56-70 | 日常做饭场景 |
| 边界情况 | 71-84 | 不存在的菜、空输入、闲聊 |
| 特色数据 | 85-92 | 火力/备菜/下锅专项 |
| 交叉查询 | 93-100 | 技法+菜系+食材组合 |
