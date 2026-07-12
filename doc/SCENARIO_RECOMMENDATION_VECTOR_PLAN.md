# B 用途：反向推荐 / 场景推荐向量化施工单

本文档面向执行能力较弱的 agent。请严格按文件清单、函数清单、执行顺序和验收命令实现，不要自行改动工具暴露方式，不要新增 Agent 工具。

当前已完成 A 用途：具体菜的做法、火候、备菜、下锅过程等关键关系文本向量补召回。B 用途是下一阶段：回答“我有什么原材料，可以做什么菜”和“天气热适合吃什么菜”这类开放推荐问题。

## 0. 总原则

1. B 第一版只做本地 12K 菜谱知识图谱内推荐。
2. 不联网补菜，不让大模型凭常识推荐图谱外菜。
3. 不新增 Agent 工具，仍然藏在 `recipe_query_tool` 内部。
4. 推荐向量索引必须离线显式构建，不允许用户请求时全量构建。
5. 推荐索引必须同时保存向量和结构化字段数组。
6. 推荐别名表必须基于当前 `config/2kg_chem+recipe_fire_12K.pkl` 重新生成和校验，不要直接相信旧配置。
7. 向量召回只负责候选召回和排序信号，最终推荐必须能说明图谱依据。

## 1. 范围拆分

### Phase B1：原材料搭配推荐

优先实现。

目标问题：

```text
我有辣椒和牛肉，可以做什么菜？
家里有鸡蛋、番茄、青椒，推荐几道菜。
牛肉和土豆能做什么？
```

核心规则：

```text
多食材同时命中 > 主料命中 > 辅料命中 > 调料弱命中 > 向量语义相似
```

不要把盐、油、生抽、老抽、糖、料酒这类万能调料当成主要召回条件。

### Phase B2：场景推荐

B1 通过后再实现。

目标问题：

```text
今天天气热适合吃什么菜？
想吃清爽一点的，有什么推荐？
想吃下饭、快手、少油的菜。
```

第一版只用确定性规则表生成/匹配弱标签，不调用 LLM 批量打标签。

### Phase B3：组合推荐

B1、B2 都通过后再实现。

目标问题：

```text
天气热，我有牛肉和青椒，可以做什么？
想吃下饭的，用鸡蛋能做什么？
```

组合排序必须同时考虑食材命中和场景标签命中。

## 2. 必须新增的文件

### 2.1 `scripts/build_recommendation_aliases.py`

职责：从当前 12K 图谱重新生成并校验推荐别名表。

输入：

- `config/2kg_chem+recipe_fire_12K.pkl`
- `config/recipe_aliases.json`
- `config/reverse_entity_aliases.json`

输出：

- `config/recommendation_aliases.json`
- `config/recommendation_aliases.rejected.json`

必须实现的函数：

```python
def load_graph_nodes(kg_path: Path) -> dict[str, set[str]]:
    """返回当前图谱节点名，至少包含 ingredient/seasoning/taste/cuisine/technique。"""

def load_existing_alias_sources(project_root: Path) -> dict[str, dict[str, list[str]]]:
    """读取旧 alias 配置，转换成推荐别名表结构。"""

def build_default_alias_groups() -> dict[str, dict[str, list[str]]]:
    """返回默认厨房常见同义词规则。"""

def validate_aliases(
    aliases: dict[str, dict[str, list[str]]],
    graph_nodes: dict[str, set[str]],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, list[str]]]]:
    """只保留 canonical 或 alias 至少一个存在于当前图谱节点中的条目。"""

def main() -> None:
    """生成 recommendation_aliases.json 和 rejected.json。"""
```

推荐别名表结构：

```json
{
  "ingredient": {
    "辣椒": ["辣椒", "青椒", "尖椒", "小米辣", "泡椒"],
    "牛肉": ["牛肉", "黄牛肉", "牛里脊", "牛里脊肉", "肥牛", "肥牛卷"]
  },
  "seasoning": {
    "酱油": ["酱油", "生抽", "老抽"]
  },
  "scenario": {
    "天气热": ["天气热", "夏天", "热天", "清爽", "开胃"]
  }
}
```

默认同义词至少包含：

```text
西红柿 <-> 番茄
蒜苔 <-> 蒜薹
土豆 <-> 马铃薯
包菜 <-> 卷心菜 <-> 圆白菜
花甲 <-> 蛤蜊
辣椒 <-> 青椒 / 尖椒 / 小米辣 / 泡椒
牛肉 <-> 黄牛肉 / 牛里脊 / 牛里脊肉 / 肥牛 / 肥牛卷
酱油 <-> 生抽 / 老抽
```

禁止行为：

- 不要把“肉”自动展开成猪肉、牛肉、鸡肉。遇到泛词应让运行时澄清或降权。
- 不要保留完全不在当前图谱里的别名组。

### 2.2 `scripts/build_recommendation_vector_index.py`

职责：离线构建推荐文档向量索引。

输入：

- 当前运行图谱 `config/2kg_chem+recipe_fire_12K.pkl`
- 推荐别名表 `config/recommendation_aliases.json`
- 本地 embedding 模型 `models/gte-large-zh` 或环境变量 `MINICOOK_EMBEDDING_MODEL_DIR`

输出：

- `backend/.cache/recipe_recommendation_vector_index.npz`

必须实现的函数：

```python
def extract_recipe_records(kg_path: Path) -> list[dict]:
    """从 Dish 节点和出边抽取每道菜的结构化推荐字段。"""

def generate_scenario_tags(record: dict) -> list[str]:
    """根据技法、口味、食材、步骤长度等确定性规则生成弱标签。"""

def build_recommendation_document(record: dict) -> str:
    """拼接用于 embedding 的推荐文档文本。"""

def build_index(records: list[dict], model_dir: Path, output_path: Path) -> None:
    """编码 documents 并保存 npz。"""

def main() -> None:
    """构建 recipe_recommendation_vector_index.npz。"""
```

推荐文档模板：

```text
菜名：小炒黄牛肉
主料：黄牛肉
辅料：小米辣、泡椒、蒜苗、姜、蒜
调料：生抽、老抽、蚝油、盐、白糖
口味：香辣味
菜系：湘菜
技法：爆炒
适合时段：午餐、晚餐
场景弱标签：下饭、快手、香辣、热菜、重口味
推荐理由：有牛肉和辣椒时可优先考虑，适合想吃香辣下饭菜。
做法摘要：牛肉腌制后大火快炒，配辣椒和蒜苗快速出锅。
```

`.npz` 必须包含以下字段，所有数组长度必须与 `dish_names` 对齐：

```text
version
dish_names
documents
embeddings
main_ingredients_json
auxiliary_ingredients_json
seasonings_json
tastes_json
cuisines_json
techniques_json
meal_times_json
scenario_tags_json
recommendation_reasons
```

`*_json` 字段保存 JSON list 字符串。

禁止行为：

- 不要在用户请求时构建这个全量索引。
- 不要只保存 document 和 embedding，必须保存结构化字段数组。
- 不要把所有图谱边无差别拼进去，只抽取推荐相关字段。

### 2.3 `backend/recipe_recommendation_vector_retriever.py`

职责：运行时加载离线索引，执行 B1/B2 推荐召回和排序。

必须实现的数据类型：

```python
@dataclass(frozen=True)
class RecommendationQuery:
    original_query: str
    core_ingredients: tuple[str, ...] = ()
    weak_ingredients: tuple[str, ...] = ()
    seasonings: tuple[str, ...] = ()
    scenario_tags: tuple[str, ...] = ()
    cuisines: tuple[str, ...] = ()
    tastes: tuple[str, ...] = ()
    techniques: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecommendationCandidate:
    dish_name: str
    score: float
    graph_reasons: tuple[str, ...]
    vector_score: float
    matched_core_ingredients: tuple[str, ...] = ()
    matched_weak_ingredients: tuple[str, ...] = ()
    matched_scenario_tags: tuple[str, ...] = ()
```

必须实现的函数：

```python
def load_recommendation_index(path: Path | None = None) -> dict:
    """加载 backend/.cache/recipe_recommendation_vector_index.npz；不存在时抛出清晰异常。"""

def normalize_recommendation_query(query: str, aliases: dict) -> RecommendationQuery:
    """把用户原材料/场景词归一成结构化查询。"""

def retrieve_recommendations(query: RecommendationQuery, top_k: int = 5) -> list[RecommendationCandidate]:
    """返回排序后的本地图谱推荐候选。"""

def format_recommendation_answer(query: RecommendationQuery, candidates: list[RecommendationCandidate]) -> str:
    """格式化为 recipe_query_tool 可直接返回的文本。"""
```

运行时如果索引不存在，返回明确错误，不要现场全量构建：

```text
推荐向量索引不存在，请先运行：python scripts/build_recommendation_vector_index.py
```

### 2.4 `config/recommendation_aliases.json`

由 `scripts/build_recommendation_aliases.py` 生成。可以人工补充，但补充后要再次运行测试。

### 2.5 `test/test_recommendation_aliases.py`

至少测试：

- 输出文件存在。
- `辣椒` 能归一到青椒/尖椒/小米辣/泡椒相关组。
- `牛肉` 能归一到黄牛肉/牛里脊/肥牛相关组。
- `肉` 不会自动展开成所有肉类。
- rejected 文件存在且是合法 JSON。

### 2.6 `test/test_recommendation_vector_retriever.py`

至少测试：

- 索引不存在时返回明确错误或抛出明确异常。
- `我有辣椒和牛肉，可以做什么菜` 优先返回同时命中牛肉和辣椒类食材的菜。
- `家里有鸡蛋和番茄，推荐一道` 能返回番茄炒蛋或图谱内相关菜。
- `今天天气热适合吃什么菜` 返回含清爽/凉拌/少油/开胃标签的图谱菜。
- `想吃川味牛肉，有没有推荐` 不被场景推荐误导，继续走结构化图谱约束推荐。

## 3. 必须修改的文件

### 3.1 `backend/recipe_query_adapter.py`

接入 B 用途，但不新增工具。

建议新增内部函数：

```python
def _looks_like_ingredient_recommendation(query: str) -> bool:
    """识别我有 X 和 Y 可以做什么菜。"""

def _looks_like_scenario_recommendation(query: str) -> bool:
    """识别天气热/清爽/下饭/快手/少油等场景推荐。"""

def _execute_recommendation_query(query: str) -> str | None:
    """调用 recipe_recommendation_vector_retriever；不命中则返回 None。"""
```

接入位置：

```text
query_recipe_kg()
  -> 加载 system 后
  -> 图谱数量查询之后
  -> build_query_plan / Query Understanding 之前
  -> 先尝试 B1/B2 推荐意图
```

原因：推荐问题不应被旧的“做法/菜名”解析误吃掉。

返回文本必须包含：

```text
结构化摘要：
success: True
query_type: recommendation
match_mode: hybrid_recommendation
web_fallback_allowed: False
```

如果索引缺失：

```text
推荐向量索引不存在，请先运行：python scripts/build_recommendation_vector_index.py

结构化摘要：
success: False
query_type: recommendation
match_mode: index_missing
web_fallback_allowed: False
```

### 3.2 `backend/query_understanding.py` 或 `backend/query_router.py`

如果已有确定性路由能在 `recipe_query_adapter.py` 内解决，可以不改。

如果需要加 intent，新增：

```text
ingredient_recommendation
scenario_recommendation
```

但不要让模型决定排序细节。排序细节必须在 `recipe_recommendation_vector_retriever.py` 中确定性执行。

### 3.3 `README.md`

补充：

- 推荐别名生成命令。
- 推荐向量索引构建命令。
- B1/B2 支持范围。
- 索引缺失时如何处理。

## 4. 排序规则

### 4.1 B1 原材料搭配推荐

必须区分：

```text
core_ingredients：核心食材，例如牛肉、鸡蛋、番茄、青椒
weak_ingredients：弱食材或泛词，例如葱姜蒜、少量辅料
seasonings：调味品，例如盐、生抽、老抽、糖、料酒
```

排序优先级：

```text
1. 命中全部 core_ingredients 的菜
2. 命中多个 core_ingredients 的菜
3. 命中 core_ingredients 作为主料的菜
4. 命中 core_ingredients 作为辅料的菜
5. seasonings 只作为弱加分，不得单独拉高排名
6. vector_rank 只在同一档内排序，或作为 RRF 的一个 ranking source
```

推荐使用 RRF，而不是直接相加不同量纲原始分数。

### 4.2 B2 场景推荐

场景规则表第一版写死在代码里：

```python
SCENARIO_TAG_RULES = {
    "天气热": ["清爽", "开胃", "少油", "凉拌", "酸辣", "快手"],
    "夏天": ["清爽", "开胃", "少油", "凉拌"],
    "下饭": ["香辣", "麻辣", "爆炒", "重口味"],
    "清淡": ["清蒸", "白灼", "少油", "清淡"],
    "快手": ["快手", "步骤少", "短时间"],
}
```

菜品弱标签生成规则：

```text
技法=凉拌/拌 -> 清爽、少油
技法=白灼/清蒸 -> 清淡、少油
口味=酸辣味/酸甜味 -> 开胃
口味=香辣味/麻辣味 -> 下饭、重口味
技法=爆炒/小炒 -> 下饭、快手
cooking_process 步骤数少或文本较短 -> 快手
主料/辅料含黄瓜、番茄、生菜、苦瓜 -> 清爽、夏天
```

不要做医学、营养强断言。例如不要说“天气热必须吃某菜降火”。

## 5. 推荐回答格式

推荐回答必须短、可解释、可追溯。

示例：

```text
本地图谱里优先推荐这几道：

1. 小炒黄牛肉
   推荐理由：同时命中牛肉和辣椒类食材；口味偏香辣，下饭。
   图谱依据：主料=黄牛肉；辅料=小米辣、泡椒；技法=爆炒。

2. 青椒牛柳丝
   推荐理由：命中牛肉和青椒，适合想做快手炒菜。
   图谱依据：主料=牛里脊肉；辅料=青椒；技法=炒。

结构化摘要：
success: True
query_type: recommendation
match_mode: hybrid_recommendation
web_fallback_allowed: False
```

如果结果少：

```text
本地图谱里符合条件的菜较少，目前只找到 1 道。
```

如果用户只说“肉”：

```text
你说的“肉”范围比较大。请补充是猪肉、牛肉、鸡肉，还是其他肉类。

结构化摘要：
success: False
query_type: recommendation
match_mode: needs_clarification
web_fallback_allowed: False
```

## 6. 执行顺序

严格按顺序执行：

```bash
python scripts/build_recommendation_aliases.py
python scripts/build_recommendation_vector_index.py
python -m pytest test/test_recommendation_aliases.py test/test_recommendation_vector_retriever.py -q
python -m pytest test/test_recipe_query_adapter_guardrails.py -q
python -m pytest test/test_query_router.py test/test_query_understanding.py -q
```

如果本地缺少 embedding 依赖，先安装项目依赖，不要在代码里吞异常。

## 7. 验收样例

必须通过这些人工查询：

```text
我有辣椒和牛肉，可以做什么菜？
家里只有鸡蛋和番茄，推荐一道。
牛肉和土豆能做什么？
今天天气热适合吃什么菜？
想吃清爽一点的，有什么推荐？
想吃下饭、快手一点的菜。
天气热，我有牛肉和青椒，可以做什么？
想吃川味牛肉，有没有推荐？
牛肉有多少种做法？
菜谱一共收录了多少菜？
```

预期：

- 前 7 条走推荐链路或组合推荐链路。
- “想吃川味牛肉”继续走结构化图谱约束推荐。
- “牛肉有多少种做法”继续走反向图谱查询。
- “菜谱一共收录了多少菜”继续走图谱元信息查询。

## 8. 常见失败处理

### 索引不存在

不要现场构建，返回明确错误：

```text
推荐向量索引不存在，请先运行：python scripts/build_recommendation_vector_index.py
```

### 旧别名不匹配 12K 图谱

运行：

```bash
python scripts/build_recommendation_aliases.py
```

检查：

```text
config/recommendation_aliases.rejected.json
```

### 向量召回把无关菜排前面

先检查排序硬规则：

```text
多核心食材命中必须优先于 vector_rank。
```

不要通过调低 embedding 阈值解决。

### 万能调料污染召回

确认 `seasonings` 只作为弱加分，不作为主召回条件。

### 场景推荐过度常识化

确认输出菜名都来自 `dish_names`，不要输出图谱外菜。

## 9. 不允许做的事

- 不允许新增 `recipe_recommendation_tool`。
- 不允许请求时全量构建推荐向量索引。
- 不允许只靠 LLM 判断推荐结果。
- 不允许把“肉”自动展开成所有肉类。
- 不允许把盐、油、生抽等调味品作为核心食材。
- 不允许推荐本地图谱外菜。
- 不允许把 B 用途改成联网搜索优先。
# 方案历史版本说明

> 本文保留推荐向量方案的设计过程。当前工具执行已经迁移到结构化 `QueryFrame -> plan -> recipe_query_tool(plan)`，文中的旧自然语言入口和旧路由示例不再适用。
