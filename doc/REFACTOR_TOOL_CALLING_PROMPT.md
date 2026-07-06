# 任务提示词：把菜谱查询脚本接入 agent 工具系统

你是一个代码执行 agent。请在当前项目 `G:\miniCookingAgent-Demo` 中执行一次小范围功能接入：把 `backend/4-V1菜谱查询recipe_query-查询火力.py` 包装成一个 LangChain 工具，并接入现有 `agent_adapter_local_LLM_harness.py` 使用的工具列表。

## 背景

当前项目已经把工具定义拆到了：

```text
backend/agent_tools.py
```

当前工具列表在：

```python
def _get_tools() -> list[Any]:
    return [find_tool, read_file_tool, web_search_tool]
```

`agent_adapter_local_LLM_harness.py` 会通过 `_get_tools()` 自动拿到工具列表，所以接入新工具时原则上不要修改 harness 主循环。

新脚本：

```text
backend/4-V1菜谱查询recipe_query-查询火力.py
```

脚本主调用链：

```text
main()
  -> argparse
  -> RecipeQuerySystem(kg_path)
      -> ConfigLoader(...)
      -> _load_graph()
      -> QueryParser(config)
      -> QueryExecutor(graph, config)
  -> system.query(query)
      -> QueryParser.parse(query)
      -> QueryExecutor.execute(parsed)
      -> return result
```

最适合封装成工具的入口是：

```python
RecipeQuerySystem.query(query_str)
```

## 目标

新增一个工具：

```python
recipe_query_tool(query: str) -> str
```

这个工具用于查询本地菜谱知识图谱。用户问菜谱、做法、食材、火力、技法、菜系、某菜完整档案等问题时，模型可以调用这个工具。

## 绝对约束

1. 不要把新脚本的大段逻辑复制进 `agent_adapter_local_LLM_harness.py`。
2. 不要改 `stream_search_agent(user_text, history)` 的函数名、参数、返回事件格式。
3. 不要改 `backend/app.py` 的适配器加载方式。
4. 不要破坏现有 3 个工具：
   - `find_tool`
   - `read_file_tool`
   - `web_search_tool`
5. 不要把 `backend/4-V1菜谱查询recipe_query-查询火力.py` 重命名。
6. 不要让工具调用触发交互模式。
7. 不要让工具直接 `sys.exit()` 终止后端进程。
8. 工具返回值必须是字符串，适合给大模型总结。
9. 尽量小改动，只新增 adapter 和工具注册。

## 已知风险，必须处理

### 1. 原脚本文件名不是合法模块名

文件名是：

```text
4-V1菜谱查询recipe_query-查询火力.py
```

不能普通写：

```python
import backend.4-V1菜谱查询recipe_query-查询火力
```

必须用 `importlib.util.spec_from_file_location` 动态加载。

### 2. 默认知识图谱路径不匹配

原脚本默认：

```python
DEFAULT_KG_PATH = 'output/2kg_chem+recipe/chem+recipe_kg_updated_fire.pkl'
```

当前项目实际存在：

```text
config/chem+recipe_kg_updated_fire.pkl
```

adapter 中必须优先使用这个实际路径。

### 3. 原脚本会 print 很多内容

`RecipeQuerySystem.query()` 会打印调试信息和结果。作为工具接入时，不应该把这些 print 直接刷到后端终端。

必须在 adapter 中用 `contextlib.redirect_stdout` 捕获 stdout。

### 4. 原脚本可能 sys.exit

`_load_graph()` 找不到 KG 文件时会 `sys.exit(1)`。adapter 必须捕获 `SystemExit`，并返回友好的错误字符串。

### 5. 依赖 networkx

pickle 反序列化知识图谱需要 `networkx`。如果环境缺少，工具必须返回：

```text
菜谱查询失败：缺少 networkx，请运行 pip install networkx
```

不要让 ImportError 崩掉后端。

### 6. 不要每次查询都重新加载图谱

知识图谱 pkl 约 16MB。adapter 必须缓存 `RecipeQuerySystem` 实例。

## 要新建的文件

### `backend/recipe_query_adapter.py`

新建这个文件，职责是：

1. 动态加载 `backend/4-V1菜谱查询recipe_query-查询火力.py`
2. 创建并缓存 `RecipeQuerySystem`
3. 调用 `system.query(query)`
4. 捕获 stdout、SystemExit、ImportError、其它异常
5. 返回适合 agent 使用的字符串

建议代码结构：

```python
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(__file__).resolve().parent / "4-V1菜谱查询recipe_query-查询火力.py"
DEFAULT_RECIPE_KG_PATH = PROJECT_ROOT / "config" / "chem+recipe_kg_updated_fire.pkl"

_recipe_module = None
_recipe_system = None


def _load_recipe_module():
    ...


def _get_recipe_system(kg_path: str | None = None):
    ...


def query_recipe_kg(query: str, kg_path: str | None = None) -> str:
    ...
```

### `_load_recipe_module()` 要求

- 如果 `_recipe_module` 已存在，直接返回。
- 使用 `importlib.util.spec_from_file_location` 动态加载脚本。
- 如果脚本不存在，抛出 `FileNotFoundError`。
- 加载后返回 module。

### `_get_recipe_system()` 要求

- 如果 `_recipe_system` 已存在，直接返回。
- 默认 kg path 使用：

```python
DEFAULT_RECIPE_KG_PATH
```

- 如果传入 `kg_path`，使用传入路径。
- 调用模块里的：

```python
RecipeQuerySystem(str(resolved_kg_path))
```

- 初始化期间也要捕获 stdout，避免刷屏。

### `query_recipe_kg()` 要求

输入：

```python
query: str
kg_path: str | None = None
```

输出：

```python
str
```

逻辑：

1. 去掉 query 首尾空白。
2. query 为空时返回：`菜谱查询失败：query 不能为空。`
3. 如果 KG 文件不存在，返回：`菜谱查询失败：知识图谱文件不存在：xxx`
4. 调用 `_get_recipe_system()` 获取系统实例。
5. 用 `contextlib.redirect_stdout(io.StringIO())` 包住 `system.query(query)`。
6. `system.query(query)` 返回 dict。
7. 优先取：

```python
result.get("human_readable")
```

8. 同时可以附加少量结构化信息，例如：

```text
结构化摘要：
success: ...
query_type: ...
match_mode: ...
```

9. 如果 `human_readable` 为空，就返回 `json.dumps(result, ensure_ascii=False, indent=2)`，但要限制长度。
10. 返回字符串最长建议限制到 4000 字符，超出追加 `...(truncated)`。
11. 捕获 `ModuleNotFoundError`：
    - 如果模块名是 `networkx`，返回缺少 networkx 的友好提示。
    - 其它模块也返回缺少依赖的友好提示。
12. 捕获 `SystemExit`，返回：`菜谱查询失败：查询脚本尝试退出进程，请检查知识图谱路径或配置。`
13. 捕获其它 Exception，返回：`菜谱查询失败：ExceptionType: message`

## 要修改的文件

### `backend/agent_tools.py`

新增 import：

```python
from backend.recipe_query_adapter import query_recipe_kg
```

新增工具函数：

```python
@tool
def recipe_query_tool(query: str) -> str:
    """查询本地菜谱知识图谱；query 应是自然语言菜谱问题，例如“西红柿炒鸡蛋怎么做”“小炒黄牛肉的火力调节过程”“哪些菜用了炝炒技法”。"""
    return query_recipe_kg(query)
```

修改 `_get_tools()`：

```python
return [find_tool, read_file_tool, web_search_tool, recipe_query_tool]
```

## 是否需要修改 harness

原则上不需要。

因为 harness 通过 `_get_tools()` 自动获得工具列表，`recipe_query_tool` 加入 `_get_tools()` 后会自动进入：

1. `model.bind_tools(_get_tools())`
2. `_build_tool_inventory_prompt(tools)`
3. 文本式工具调用解析 `_parse_textual_tool_call(...)`
4. 工具执行 `_execute_tool_call(...)`

只有一种情况需要小改 harness：

如果系统提示词没有明显告诉模型“菜谱问题优先用 recipe_query_tool”，可以在 `_build_tool_loop_system_prompt()` 的工具规则里补一句：

```text
- 如果用户询问菜谱、做法、食材、火力、技法、菜系或本地菜谱知识图谱，优先调用 recipe_query_tool。
```

这属于允许的小改。

## 依赖处理

当前 `bigdog` 环境可能没有 `networkx`。

先检查：

```bash
conda activate bigdog
python -c "import networkx; print(networkx.__version__)"
```

如果缺少，安装：

```bash
pip install networkx
```

同时更新 `setup.sh` 的 pip 安装列表，加入：

```text
networkx
```

如果 README 或 `.env.example` 有依赖说明，也可以补一句，但不是必须。

## 验证命令

在项目根目录执行。

### 1. 编译检查

```bash
conda activate bigdog
python -m compileall backend start.py
```

### 2. 验证 adapter 直接调用

```bash
python -c "from backend.recipe_query_adapter import query_recipe_kg; print(query_recipe_kg('小炒黄牛肉的火力调节过程')[:1000])"
```

期望：

- 不崩溃。
- 返回中文查询结果或明确的友好错误。
- 如果缺少 networkx，要返回缺少 networkx 的提示。

### 3. 验证工具列表

```bash
python -c "from backend.agent_tools import _get_tools; print([t.name for t in _get_tools()])"
```

期望包含：

```text
recipe_query_tool
```

### 4. 验证文本式工具调用解析

```bash
python -c "from backend.tool_calling import _parse_textual_tool_call; print(_parse_textual_tool_call('recipe_query_tool(\"西红柿炒鸡蛋怎么做\")'))"
```

期望输出类似：

```python
{'name': 'recipe_query_tool', 'args': {'query': '西红柿炒鸡蛋怎么做'}}
```

### 5. 验证 harness 能导入

```bash
python -c "from backend.agent_adapter_local_LLM_harness import stream_search_agent; print(stream_search_agent)"
```

### 6. 前端构建

```bash
cd frontend
npm run build
cd ..
```

## 验收标准

完成后必须满足：

1. 新增 `backend/recipe_query_adapter.py`。
2. `backend/agent_tools.py` 新增 `recipe_query_tool`。
3. `_get_tools()` 返回 4 个工具：
   - `find_tool`
   - `read_file_tool`
   - `web_search_tool`
   - `recipe_query_tool`
4. `agent_adapter_local_LLM_harness.py` 不塞入新脚本大段逻辑。
5. `recipe_query_tool` 查询失败时返回友好错误字符串，不让后端崩溃。
6. `query_recipe_kg()` 缓存 `RecipeQuerySystem`，不要每次查询重复加载 pkl。
7. `python -m compileall backend start.py` 通过。
8. `npm run build` 通过。

## 最终汇报格式

完成后请用中文简短汇报：

- 新增了哪些文件。
- 修改了哪些文件。
- 菜谱查询工具的调用链是什么。
- 跑了哪些验证命令，结果如何。
- 如果有失败项，说明失败原因和下一步建议。
