# 用户发消息后的调用链

本文说明 MiniCookingAgent-Demo 当前版本在用户发送一条消息后，从前端、会话持久化、runtime memory、Agent 工具循环，到菜谱混合召回、知识图谱查询、联网兜底、SSE 回传和 SQLite 落库的完整链路。

## 0. 总流程图

![总流程图](./call_chain_mermaid_svg/diagram-1.svg)

## 0.1 会话恢复与持久化链路

![会话恢复与持久化链路](./call_chain_mermaid_svg/diagram-2.svg)

## 0.2 Agent 工具决策流程图

![Agent 工具决策流程图](./call_chain_mermaid_svg/diagram-3.svg)

## 0.3 runtime memory 注入链路

![runtime memory 注入链路](./call_chain_mermaid_svg/diagram-4.svg)

## 0.4 `recipe_query_tool` 内部链路

![recipe_query_tool 内部链路](./call_chain_mermaid_svg/diagram-5.svg)

## 0.5 SSE 回传与前端渲染

![SSE 回传与前端渲染](./call_chain_mermaid_svg/diagram-6.svg)
