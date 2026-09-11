---
name: roll-auction-agent-ops
description: 辽宁滚搓交易智能体工具调用与安全边界技能。涉及 LLM 工具注册表（10 工具）、function calling 循环、交互模式（问答/跑流程/改数据/评审）、修订与回退、护栏 G4、chat_log 留痕、API key 安全时使用。触发词：工具调用、助手、修订、回退、导出、护栏、留痕、API、key、function calling。
---

# roll-auction-agent-ops — 工具注册表与交互模式

契约全文：`docu/智能体工具与技能设计.md`（本文与其同步维护）；实现 `backend/llm/tools.py`、`llm/prompts.py`、`llm/client.py`、`api.py`。

## 工具注册表 ↔ 实现映射

| 工具 | 实现入口 | 模式 |
|---|---|---|
| `get_state(period?)` | store 快照 | 问答 |
| `run_chain()` | pipeline.run_all | 跑流程 |
| `set_params(params)` | config 校验 + 联动重算 | 改数据 |
| `modify_boundary(boundary, period, points, reason)` | store.append_revision + 重算 | 改数据（reason ≥5 字强制） |
| `set_intent(period, list_price?, list_volume?, lift_price?, lift_volume?)` | pipe.set_intent（缺参字段不变；HTTP 显式 null=清空；四字段=卖方挂牌两项+买方摘牌两项，2026-09-11） | 改数据（价/量 >0） |
| `get_module(module, period?)` | 各 m*_module 输出+理由 | 问答 |
| `search_similar_days(period, threshold?)` | m3/m8 落点检索 | 跑流程 |
| `explain_period(period)` | 依据链组装 | 问答 |
| `rollback_revision(rev_id)` | store.rollback（回退记新修订） | 改数据 |
| `export_report()` | CSV 导出 | 跑流程 |

## 交互模式（写进 system prompt）

问答数值取自工具返回；改数据必须复述改动 + 要求理由（必填，不限字数）；评审**只评审不代拟、不产可申报数值（护栏 G4）**；单轮 ≤8 次工具调用；每轮落 chat_log（快照留痕 D15）；当前时段上下文自动装载随切换更新。

## Key 安全

`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` 只存 `.env`/内存；不回显、不入库、不入 git、不打日志；无 key → 结构化配置引导错误。
