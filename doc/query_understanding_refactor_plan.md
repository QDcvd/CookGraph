# Query Understanding 重构方案

## 背景

当前菜谱查询链路把自然语言解析、图谱实体归一、查询执行和兜底策略混在 `recipe_query_adapter.py` 与旧版 `QueryParser.parse()` 中。为了修复具体坏例子，系统已经加入了多组正则和前置补丁，但这类做法会持续积累复杂度，并引入新的误判。

典型问题：

- `哪些菜用了牛肉` 被旧 parser 误判为查询 `Technique=牛肉`。
- `牛肉可以用来做什么菜` 曾被宽松正则截成 `牛肉可以用来`。
- `有什么川菜推荐` 被误拆成食材 `川`。
- `哪些菜是香辣味的`、`有哪些菜是蒸制的` 这类反向查询缺少统一结构化入口。
- `蒜蓉` 可能同时指辅料、食材或技法，如果硬拆会产生误导。

重构目标不是继续增加临时正则，而是建立一个清晰的 Query Understanding 层，先产出结构化意图，再决定是否进入本地图谱查询、追问或旧正向 parser。

## 目标

1. 明确区分正向查询和反向查询。
2. 反向查询绕过旧自然语言 parser，使用结构化图谱查询。
3. 正向查询保持现状，继续复用当前高命中率路径。
4. 不确定时追问用户，不靠硬拆、不靠常识补全、不自动联网。
5. LLM 从主路由降级为低置信度追问文案生成器。
6. 上位词归并只允许发生在本地图谱实体范围内，并且输出必须展示归并值。

## 非目标

- 不重写正向查询执行链路。
- 不废弃旧 `QueryParser.parse()` 的正向能力。
- 不引入完整事件图或复杂长期记忆机制。
- 不让 LLM 决定是否联网、是否正向/反向、实体属于哪种图谱类型。

## 新模块

建议新增：

```text
backend/query_understanding.py
```

核心职责：

- 判断是否明确非菜谱问题。
- 判断是否直接命中标准菜名或菜名别名。
- 判断是否为反向查询。
- 抽取反向查询槽位。
- 识别多类型歧义，并返回追问意图。
- 其余输入交还旧正向 parser。

## Intent 类型

建议定义：

```python
@dataclass
class QueryIntent:
    intent: Literal[
        "forward_recipe_query",
        "reverse_query",
        "non_recipe_query",
        "ambiguous_query",
        "legacy_forward_parser",
    ]
    target_type: str | None = None
    target_text: str | None = None
    normalized_text: str | None = None
    relation: str | None = None
    dish_name: str | None = None
    attribute: str | None = None
    confidence: float = 0.0
    reason: str = ""
    candidates: list[dict] | None = None
```

说明：

- `forward_recipe_query`：直接命中标准菜名或菜名别名，应走正向查询。
- `reverse_query`：食材、技法、口味、菜系等反向检索。
- `non_recipe_query`：你好、天气、模型身份等明显非菜谱问题。
- `ambiguous_query`：多类型命中或无法稳定判断，需要追问。
- `legacy_forward_parser`：没有明确反向特征，也没有歧义，交给旧正向 parser。

## 优先级规则

最终识别优先级：

```text
1. 明确非菜谱问题 -> non_recipe_query
2. 直接命中标准菜名 -> forward_recipe_query
3. 直接命中标准菜名别名 -> forward_recipe_query
4. 明确反向模式 -> reverse_query
5. 短词命中食材/技法/口味/菜系 -> reverse_query
6. 多类型命中且无法判断 -> ambiguous_query
7. 其余 -> legacy_forward_parser
```

关键边界：

- `小炒黄牛肉怎么做`、`番茄炒蛋怎么做`、`清蒸鲈鱼怎么做` 明确是正向查询，因为直接命中本地图谱菜名。
- `西红柿炒鸡蛋怎么做` 如果别名归一到 `番茄炒蛋`，也应走正向查询。
- `牛肉怎么做`、`虾怎么做`、`莲藕怎么做好吃` 不应硬选一道菜，应走反向食材查询。
- `花甲`、`肥牛`、`鸡蛋`、`莲藕`、`包菜` 等短词如果不是标准菜名，但命中食材实体或食材归并词，应走反向食材查询。
- `川菜`、`湘菜`、`香辣味`、`蒸制`、`爆炒` 等短词如果明确命中菜系、口味或技法实体，应走对应反向查询。
- `蒜蓉` 这类多类型命中词，不硬拆，进入追问。

## 反向查询范围

第一阶段只接管以下反向类型：

| target_type | relation | 图谱实体 |
| --- | --- | --- |
| ingredient | `USES_MAIN_INGREDIENT` | `Ingredient` |
| technique | `USES_TECHNIQUE` | `Technique` |
| taste | `HAS_TASTE` | `Taste` |
| cuisine | `BELONGS_TO_CUISINE` | `Cuisine` |

后续可以扩展：

| target_type | relation | 图谱实体 |
| --- | --- | --- |
| auxiliary | `USES_AUXILIARY` | `Ingredient` |
| seasoning | `USES_SEASONING` | `Seasoning` |
| method | `USES_METHOD` | `CookingMethod` |

## 上位词归并策略

允许图谱内上位词归并，但必须满足：

1. 只归并到本地图谱真实存在的实体节点。
2. 输出中必须展示归并值。
3. 不能补出图谱外实体。
4. 不能根据常识补菜。
5. 归并失败不能触发联网搜索。

示例：

```text
查询食材：牛肉
归并食材：牛肉、黄牛肉、牛里脊、牛里脊肉、肥牛、肥牛卷
本地图谱中明确命中的菜：
...
```

建议把归并配置从 adapter 中迁移到独立结构：

```text
config/reverse_entity_aliases.json
```

示例：

```json
{
  "ingredient": {
    "牛肉": ["牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"],
    "猪肉": ["猪肉", "猪里脊肉", "猪前腿肉", "猪排骨", "猪大肠"],
    "鸡肉": ["鸡肉", "三黄鸡", "鸡腿肉", "鸡胸肉", "鸡翅中"],
    "鱼": ["鱼", "鲈鱼"]
  }
}
```

加载时需要过滤掉当前图谱不存在的实体。

## 歧义追问策略

如果同一短词命中多个实体类型，返回 `ambiguous_query`，不要合并查询。

示例：

```text
蒜蓉
```

可能候选：

```json
[
  {"target_type": "ingredient", "target_text": "蒜蓉"},
  {"target_type": "technique", "target_text": "蒜蓉炒"}
]
```

追问：

```text
你是想查用了蒜蓉作为辅料的菜，还是查“蒜蓉炒”这种技法的菜？
```

LLM 只负责把结构化歧义原因改写成自然追问，不负责重新判断意图。

## 与旧正向 parser 的兼容

正向查询暂不重构。

兼容流程：

```text
用户输入
  ↓
Query Understanding
  ↓
non_recipe_query -> 拒答/闲聊响应
forward_recipe_query -> 继续走现有正向查询链路
reverse_query -> 新结构化 reverse executor
ambiguous_query -> 追问
legacy_forward_parser -> system.query(query_str)
```

注意：

- 旧 `system.query(query_str)` 仍可处理正向查询。
- 反向查询不再进入旧 `QueryParser.parse()`。
- 旧 parser 的反向能力可以保留，但不作为主路径。

## 结构化反向执行器

建议在 `recipe_query_adapter.py` 或新模块中提供：

```python
def execute_reverse_query(system, intent: QueryIntent) -> str:
    ...
```

执行逻辑：

1. 根据 `intent.relation` 和 `intent.target_type` 确定图谱边和实体标签。
2. 根据 `target_text` 做图谱内实体归一。
3. 如果有 alias group，展开并过滤到图谱存在实体。
4. 遍历菜品节点出边或目标节点入边。
5. 只返回图谱中明确命中的菜。
6. 输出固定结构化摘要。

输出格式：

```text
【本地图谱反向查询结果】
查询维度：食材
查询值：牛肉
归并值：牛肉、黄牛肉、牛里脊、牛里脊肉、肥牛、肥牛卷
本地图谱中明确命中的菜（共N道）：
1. ...

说明：以上只来自本地菜谱图谱，未使用联网搜索，也未补充常识菜。

结构化摘要：
success: True
query_type: reverse
match_mode: exact
web_fallback_allowed: False
```

## 测试策略

新增测试分三层：

1. `query_understanding` 单元测试
   - 输入文本 -> 期望 intent。
   - 不加载 LLM。

2. 反向 executor 单元测试
   - intent -> 图谱结果。
   - 验证只列图谱明确命中的菜。

3. 端到端 guardrail
   - 用户原始输入 -> 最终工具结果/前端 trace。
   - 覆盖多轮上下文和 tool call 失败兜底。

必须覆盖：

```text
小炒黄牛肉怎么做 -> forward_recipe_query
番茄炒蛋怎么做 -> forward_recipe_query
清蒸鲈鱼怎么做 -> forward_recipe_query
西红柿炒鸡蛋怎么做 -> forward_recipe_query
牛肉怎么做 -> reverse_query / ingredient
虾怎么做 -> reverse_query / ingredient
莲藕怎么做好吃 -> reverse_query / ingredient
花甲 -> reverse_query / ingredient
肥牛 -> reverse_query / ingredient
川菜 -> reverse_query / cuisine
香辣味 -> reverse_query / taste
蒸制 -> reverse_query / technique
哪些菜用了牛肉 -> reverse_query / ingredient
哪些菜用了莲藕 -> reverse_query / ingredient
有什么川菜推荐 -> reverse_query / cuisine
哪些菜是香辣味的 -> reverse_query / taste
有哪些菜是蒸制的 -> reverse_query / technique
蒜蓉 -> ambiguous_query
火力怎么控制 -> ambiguous_query 或上下文补全
你好 -> non_recipe_query
今天天气怎么样 -> non_recipe_query
```

## 分阶段实施

### 阶段 1：抽出 Query Understanding

- 新建 `backend/query_understanding.py`。
- 定义 `QueryIntent`。
- 从 adapter 中迁移反向识别逻辑。
- 添加 intent 单元测试。
- 暂不改变正向查询行为。

### 阶段 2：结构化反向执行

- 新增或整理 `execute_reverse_query`。
- 反向查询不再进入旧 parser。
- 保留当前 adapter 返回格式。
- 添加反向 executor 单元测试。

### 阶段 3：歧义追问接入 agent

- adapter 返回 `ambiguous_query` 结构化摘要。
- agent final answer 层识别该摘要。
- LLM 只负责生成追问文案。

### 阶段 4：测试集重组

- 单轮召回数据集按 intent 分类。
- 新增反向查询专项集。
- 多轮测试增加“新问题覆盖旧上下文”和“歧义追问”用例。

## 验收标准

- 反向查询不再依赖旧 `QueryParser.parse()`。
- `牛肉可以用来做什么菜` 不会被截成 `牛肉可以用来`。
- `哪些菜用了牛肉` 不会被误查为 `Technique=牛肉`。
- `有什么川菜推荐` 不会被拆成食材 `川`。
- `小炒黄牛肉怎么做`、`番茄炒蛋怎么做`、`清蒸鲈鱼怎么做` 保持正向查询。
- 不确定时返回追问，不硬拆。
- 所有反向查询输出都包含 `web_fallback_allowed: False`。
- 反向结果只包含本地图谱明确命中的菜。
