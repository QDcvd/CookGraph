# 测试目录

## 测试列表

| 测试 | 文件 | 用例数 | 说明 |
| ---- | ---- | ------ | ---- |
| 单轮召回率测试 | [run_recall_test.py](run_recall_test.py) + [recipe_test_data.py](recipe_test_data.py) | 100 条 | 菜谱知识图谱查询召回率测试，覆盖正向/反向/模糊/边界/联网兜底 |
| 多轮对话测试 | [run_multiturn_dialogue_test.py](run_multiturn_dialogue_test.py) + [multiturn_test_data.py](multiturn_test_data.py) | 10 个 case | 真实 agent 多轮对话测试，覆盖记忆/抗干扰/逻辑自洽三类能力 |
| 持久化测试 | [test_chat_persistence.py](test_chat_persistence.py) | 6 项 | SQLite 会话持久化单元测试，验证 round-trip / hydrate / archive |
| Zleap-lite 记忆测试 | [test_zleap_lite_memory.py](test_zleap_lite_memory.py) | 9 项 | 偏好记忆提取/持久化/归档 + 菜谱上下文更新 + runtime memory 渲染 |

回放脚本生成的 JSON 只写入 `test/.artifacts/`，不作为源码或固定测试样本提交。
