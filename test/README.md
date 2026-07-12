# 测试目录

当前测试分为三层：默认单元回归、专项评测和真实会话回放。

## 默认验证

在项目根目录运行：

```bash
conda run -n bigdog python -m pytest -q
```

当前基线为 93 项，覆盖：

- QueryFrame JSON 解析、属性字段合同和上下文追问
- `QueryFrame -> plan -> recipe_query_tool(plan)` 路由契约
- 实体别名、推荐别名和推荐向量召回
- 菜谱工具结果协议、食材清单和完整菜谱渲染
- 联网降级、工具入参、重复调用保护
- SQLite 持久化、会话菜谱上下文和偏好记忆

## 专项评测

| 类型 | 入口 | 说明 |
| --- | --- | --- |
| 单轮召回率 | `python test/run_recall_test.py --phase all` | 使用 `recipe_test_data.py`，覆盖正向、反向、模糊、边界和联网兜底 |
| 多轮对话 | `python test/run_multiturn_dialogue_test.py --all` | 使用 `multiturn_test_data.py`，覆盖记忆、抗干扰和逻辑一致性 |
| 全量入口 | `python test/run_all_tests.py` | 依次运行 pytest、单轮召回率和多轮专项，并把报告写入 `test/.artifacts/` |

多轮专项可以配置 `DEEPSEEK_API_KEY` 使用外部裁判；没有配置时仍会运行规则断言，但不会把裁判不可用误报为模型通过。

## 真实会话回放

复放数据库中最新活跃 session 的用户多轮输入：

```bash
conda run -n bigdog python test/replay_session.py --max-turns 3
```

也可以指定 session：

```bash
conda run -n bigdog python test/replay_session.py --session-id <session_id>
```

该脚本调用真实的 `stream_search_agent()`，不是 mock。回放结果写入 `test/.artifacts/`，不提交生成的 JSON。

## 目录约定

- `test_*.py`：pytest 自动收集的稳定回归测试。
- `recipe_test_data.py`、`multiturn_test_data.py`：专项测试数据，不参与 pytest 自动收集。
- `run_*.py`：需要显式执行的专项评测或汇总入口。
- `test/.artifacts/`：运行生成的报告和回放结果，已加入 `.gitignore`。

编译检查统一使用：

```bash
conda run -n bigdog python -m py_compile backend/*.py
```
