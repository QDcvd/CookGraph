# V2 参数化工具调用重构施工单

## 背景

当前项目已经存在三层：

- `backend/query_understanding.py`：意图识别层
- `backend/query_router.py`：路由层
- `backend/recipe_query_adapter.py`：菜谱工具适配层

但自然语言理解逻辑分散在多个文件中，导致同一个用户输入会被不同层重复猜测。例如：

- `告诉我牛肉配芥兰能做什么菜` 被当成一个整体食材 `告诉我牛肉配芥兰`
- `芥兰炒牛肉，菜谱有收录吗` 没有稳定识别成菜品存在性/相似菜查询
- `有没有完整的菜谱` 没有继承上一轮菜品上下文

本次重构目标是彻底切换为：

```text
用户自然语言
  -> query_understanding.py 生成 QueryFrame
  -> entity_resolver.py 归一实体
  -> query_router.py 生成 V2-style plan
  -> recipe_query_tool(plan)
  -> recipe_query_adapter.py 执行 plan
  -> recipe_query_v2.py 查询图谱
```

## 硬性原则

1. `query_understanding.py` 的结构化输出是唯一事实来源。
2. `recipe_query_tool` 只接收 `plan`，完全删除 `query` 入参。
3. `recipe_query_adapter.py` 不再判断用户自然语言意图，只执行结构化 plan。
4. 实体归一化必须单独放在 `entity_resolver.py`，executor 只保留极少兜底。
5. V2 执行器以 `G:\testGraph\5-V2菜谱查询-参数驱动版.py` 为基础，搬进项目并改名。
6. 删除旧 V1 脚本 `backend/4-V1菜谱查询recipe_query-查询火力.py`。
7. 不允许继续用补丁式正则在 adapter 里判断“这是推荐/这是反向/这是完整菜谱”。

## 文件级目标

### 删除

```text
backend/4-V1菜谱查询recipe_query-查询火力.py
```

### 新增

```text
backend/recipe_query_v2.py
backend/entity_resolver.py
```

### 修改

```text
backend/query_understanding.py
backend/query_router.py
backend/agent_tools.py
backend/recipe_query_adapter.py
backend/tool_calling.py
backend/agent_adapter_local_LLM_harness.py
test/replay_session.py
README.md
doc/USER_MESSAGE_CALL_CHAIN.md
```

## QueryFrame Schema

在 `backend/query_understanding.py` 中升级当前 `QueryIntent`，可以保留旧类名兼容，但主字段必须支持下面结构。

建议新增 dataclass：

```python
@dataclass
class EntitySlot:
    raw: str
    canonical: str | None = None
    entity_type: str | None = None
    match_mode: str = "unresolved"  # exact | alias | fuzzy | vector | missing | unresolved
    confidence: float = 0.0


@dataclass
class QueryFrame:
    intent: str
    source_text: str
    mode: str | None = None

    dish_text: str | None = None
    dish: EntitySlot | None = None
    dish_candidates: list[EntitySlot] = field(default_factory=list)

    ingredients: list[EntitySlot] = field(default_factory=list)
    techniques: list[EntitySlot] = field(default_factory=list)
    tastes: list[EntitySlot] = field(default_factory=list)
    cuisines: list[EntitySlot] = field(default_factory=list)
    scenario_tags: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)

    attribute: str | None = None
    resolved_query: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = 0.0
    reason: str = ""
```

### intent 枚举

必须至少支持：

```text
graph_meta_query
dish_existence_query
dish_detail_query
ingredient_combo_query
scenario_recommendation_query
reverse_entity_query
recipe_followup_query
missing_ingredients_query
non_recipe_query
ambiguous_query
greeting
```

### attribute 枚举

必须至少支持：

```text
full_recipe
method
prep
cooking_process
fire
tips
ingredients
seasonings
techniques
existence
count
```

## LLM Prompt 要求

`query_understanding.py` 继续 LLM-first，但必须强约束 JSON。

LLM 只负责：

- 识别 intent
- 抽取原始槽位
- 判断是否上下文追问
- 给 confidence 和 reason

LLM 不负责：

- 判断图谱里是否真的存在
- 生成最终回答
- 执行图谱查询
- 编造菜谱

LLM 输出示例：

```json
{
  "intent": "ingredient_combo_query",
  "source_text": "告诉我牛肉配芥兰能做什么菜",
  "raw_slots": {
    "dish_text": null,
    "ingredients": ["牛肉", "芥兰"],
    "techniques": [],
    "tastes": [],
    "cuisines": [],
    "scenario_tags": [],
    "exclusions": [],
    "attribute": null
  },
  "followup": {
    "is_followup": false,
    "requires_context": false
  },
  "confidence": 0.9,
  "reason": "用户询问两个食材能组合做什么菜"
}
```

上下文追问示例：

```json
{
  "intent": "recipe_followup_query",
  "source_text": "有没有完整的菜谱",
  "raw_slots": {
    "dish_text": null,
    "ingredients": [],
    "attribute": "full_recipe"
  },
  "followup": {
    "is_followup": true,
    "requires_context": true
  },
  "confidence": 0.88,
  "reason": "用户省略菜名，追问上一轮菜品完整菜谱"
}
```

## 后处理校验

LLM JSON 解析后必须做 Python 校验：

- intent 不在枚举里 -> `ambiguous_query`
- list 字段不是 list -> 置空
- 字符串长度过长 -> 丢弃或降级
- confidence < 0.55 -> `ambiguous_query`
- 追问类 intent 但无上下文 -> `needs_clarification=True`
- LLM 输出的实体不直接当真，必须交给 `entity_resolver.py`

## entity_resolver.py 要求

新增 `backend/entity_resolver.py`，负责把 raw 槽位归一成图谱实体。

输入：

```python
QueryFrame(raw slots)
kg_system or graph node names
alias configs
```

输出：

```python
QueryFrame(resolved EntitySlot)
```

必须支持：

- exact：图谱节点精确命中
- alias：配置别名命中
- fuzzy：短文本相似度兜底
- vector：可选，用已有 embedding/节点向量兜底
- missing：未命中

关键例子：

```text
芥兰 -> 芥蓝
西红柿 -> 番茄
洋芋 -> 土豆
蒜薹 -> 蒜苔
黄牛肉/肥牛/牛里脊 -> 牛肉 或图谱中实际可用实体
```

注意：

- executor 不能再各自实现一套实体归一化。
- executor 只允许在 canonical 为空时做局部兜底模糊匹配。
- 所有归一化结果要写入 trace，方便解释。

## Plan Schema

`query_router.py` 根据 resolved `QueryFrame` 生成 V2-style plan。

核心 mode 必须贴近 V2：

```text
dish
ingredients
combo
missing
```

统一 plan 示例：

```json
{
  "intent": "ingredient_combo_query",
  "mode": "combo",
  "source_text": "告诉我牛肉配芥兰能做什么菜",
  "dish": null,
  "field": null,
  "ingredients": ["牛肉", "芥蓝"],
  "technique": null,
  "taste": null,
  "cuisine": null,
  "exclude": [],
  "limit": 10,
  "confidence": 0.91,
  "resolution": {
    "ingredients": [
      {"raw": "牛肉", "canonical": "牛肉", "match_mode": "exact", "confidence": 1.0},
      {"raw": "芥兰", "canonical": "芥蓝", "match_mode": "alias", "confidence": 0.98}
    ]
  }
}
```

单菜完整菜谱：

```json
{
  "intent": "dish_detail_query",
  "mode": "dish",
  "source_text": "有没有完整的菜谱",
  "dish": "蛋炒饭",
  "field": null,
  "show_all": true,
  "attribute": "full_recipe",
  "limit": 10,
  "confidence": 0.88,
  "resolution": {
    "followup_source": "上一轮菜品"
  }
}
```

菜品是否收录：

```json
{
  "intent": "dish_existence_query",
  "mode": "dish",
  "source_text": "芥兰炒牛肉，菜谱有收录吗",
  "dish": "芥蓝牛肉",
  "field": null,
  "attribute": "existence",
  "limit": 10,
  "confidence": 0.84,
  "resolution": {
    "dish_candidates": ["芥蓝牛肉", "蚝油芥兰牛肉"]
  }
}
```

## recipe_query_tool 改造

在 `backend/agent_tools.py` 中：

旧接口必须删除：

```python
recipe_query_tool(query: str)
```

新接口：

```python
recipe_query_tool(plan: dict) -> str
```

不允许保留 `query` 入参。

如果收到非 dict plan：

```text
返回结构化错误，不要尝试把它当自然语言执行。
```

错误返回必须包含：

```text
success: False
query_type: invalid_plan
web_fallback_allowed: False
```

## V2 脚本迁移

从：

```text
G:\testGraph\5-V2菜谱查询-参数驱动版.py
```

复制到：

```text
G:\miniCookingAgent-Demo\backend\recipe_query_v2.py
```

然后必须后端化清理：

1. 删除或隔离 CLI：
   - `argparse`
   - `main()`
   - 命令行 print
2. 不允许 `sys.exit()`。
3. 默认图谱路径改为：

```text
config/2kg_chem+recipe_fire_12K.pkl
```

4. 关系名兼容：

```text
USES_AUXILIARY
USES_AUXILIARY_INGREDIENT
```

5. 返回 dict，不依赖 stdout。
6. 不使用 emoji 作为核心状态。
7. 保留 V2 的四种执行能力：
   - `query_dish`
   - `query_ingredients`
   - `query_combo`
   - `query_missing`

## recipe_query_adapter.py 改造

目标：

```python
def query_recipe_plan(plan: dict, kg_path: str | None = None) -> str:
    ...
```

`query_recipe_kg(query: str)` 旧自然语言入口应删除或仅测试临时保留。根据本次原则，推荐删除主路径引用。

adapter 不允许：

- 判断用户是不是推荐问题
- 判断用户是不是反向查询
- 判断用户是不是完整菜谱追问
- 从自然语言里拆食材

adapter 只允许：

- 校验 plan
- 调用 `recipe_query_v2.py`
- 格式化工具返回
- 限制输出长度
- 附带结构化摘要

## query_router.py 改造

`route_query()` 应改为：

```text
读取 user_text/history
  -> classify_intent 得到 QueryFrame
  -> entity_resolver 归一化
  -> build_plan
  -> QueryAction(tool_name="recipe_query_tool", plan=plan)
```

不再把自然语言 query 直接交给 recipe 工具。

`QueryAction` 需要新增：

```python
plan: dict | None = None
```

trace 中必须记录：

```json
{
  "query_frame": {...},
  "plan": {...}
}
```

## tool_calling.py / agent_adapter 改造

必须检查所有调用：

```text
{"query": "..."}
```

全部改成：

```text
{"plan": {...}}
```

主路径应由后端 pre-router 直接构造 plan。模型自由 tool_call 只作为兜底。

如果模型自由调用 `recipe_query_tool` 且没有 plan：

- 不要 repair 成 query
- 返回 invalid_plan 错误
- 或退回 `route_query()` 用用户原文重新生成 plan

## Replay 验收用例

必须使用：

```text
python test/replay_session.py
```

复放最新 session，并检查真实 agent 回复。

关键用例：

### 1. 菜谱数量

用户：

```text
菜谱一共收录了多少菜品
```

期望：

```text
本地菜谱知识图谱当前收录 13214 道菜
```

不能联网。

### 2. 食材组合

用户：

```text
告诉我牛肉配芥兰能做什么菜
```

期望：

- 识别为 `ingredient_combo_query`
- `ingredients = ["牛肉", "芥蓝"]`
- 返回本地图谱候选，例如：
  - `芥蓝牛肉`
  - `蚝油芥兰牛肉`

不能把 `告诉我牛肉配芥兰` 当成一个食材。

### 3. 菜品收录判断

用户：

```text
芥兰炒牛肉，菜谱有收录吗
```

期望：

- 识别为 `dish_existence_query`
- 说明精确菜名是否存在
- 如果精确名不存在，但有相近图谱菜，应说明：

```text
没有精确收录“芥兰炒牛肉”，但图谱中有相近菜：芥蓝牛肉、蚝油芥兰牛肉。
```

不能编造通用做法。

### 4. 单菜做法

用户：

```text
蛋炒饭怎么做
```

期望：

- 优先查图谱
- 如果图谱有相近菜，明确说明来源
- 如果没有精确收录，不要凭模型常识编完整步骤，除非明确走 web fallback 并标注来源

### 5. 上下文完整菜谱追问

上一轮用户问：

```text
蛋炒饭怎么做
```

下一轮用户问：

```text
有没有完整的菜谱
```

期望：

- 识别为 `recipe_followup_query`
- 继承上一轮菜品
- plan 变成 `mode=dish, attribute=full_recipe`

不能把“有没有完整的菜谱”当成菜名。
不能联网搜索“有没有完整的菜谱”。

## 必须新增/更新测试

建议新增：

```text
test/test_query_frame_schema.py
test/test_entity_resolver.py
test/test_plan_builder.py
test/test_recipe_query_tool_plan_only.py
test/test_v2_recipe_executor.py
```

必须覆盖：

- `牛肉配芥兰` -> combo plan
- `芥兰炒牛肉有收录吗` -> existence plan
- `有没有完整的菜谱` + history -> dish full_recipe plan
- 模型输出非法 JSON -> ambiguous
- `recipe_query_tool({"query": "..."})` -> invalid_plan
- V2 `USES_AUXILIARY` 兼容
- 删除 V1 后没有 import 残留

## 禁止事项

- 禁止在 `recipe_query_adapter.py` 里新增自然语言正则。
- 禁止保留 `recipe_query_tool(query=...)`。
- 禁止让 V2 后端模块调用 `sys.exit()`。
- 禁止最终回答在本地图谱未命中时编造菜谱。
- 禁止把 `G:\testGraph` 作为运行时依赖。
- 禁止通过 subprocess 调用 V2 脚本。

## 建议执行顺序

1. 复制 V2 到 `backend/recipe_query_v2.py`。
2. 删除 V1 文件。
3. 让 `recipe_query_v2.py` 能被 import，并通过最小 smoke test。
4. 新增 `EntitySlot / QueryFrame`。
5. 改 `query_understanding.py` prompt 和 JSON 后处理。
6. 新增 `entity_resolver.py`。
7. 改 `query_router.py` 生成 plan。
8. 改 `agent_tools.py`，`recipe_query_tool(plan)`。
9. 改 `recipe_query_adapter.py`，只执行 plan。
10. 改 `tool_calling.py` 和 `agent_adapter_local_LLM_harness.py` 的工具参数处理。
11. 更新测试。
12. 跑 replay_session。
13. 更新 README 和调用链文档。

## 最终验收命令

```bash
python -m py_compile backend/query_understanding.py backend/entity_resolver.py backend/query_router.py backend/agent_tools.py backend/recipe_query_adapter.py backend/recipe_query_v2.py
python -m pytest test/test_query_understanding.py test/test_query_router.py test/test_query_plan.py test/test_entity_resolver.py test/test_recipe_query_tool_plan_only.py test/test_v2_recipe_executor.py -q
python test/replay_session.py
```

如果默认 Python 缺依赖，可使用项目实际环境：

```bash
D:/anaconda3/envs/bigdog/python.exe test/replay_session.py
```

验收标准：

- 测试通过
- replay 最新 session 无明显上下文串话
- 工具 trace 中出现 plan，而不是 query
- 没有任何运行时引用 V1 文件
- 没有任何运行时依赖 `G:\testGraph`
