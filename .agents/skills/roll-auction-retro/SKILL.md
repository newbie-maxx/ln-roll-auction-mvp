---
name: roll-auction-retro
description: 辽宁滚搓交易复盘（R-AI 路线，skills+MCP 新架构）技能。涉及实况装载、D-A6 预测快照/重放、偏差表、8+2 次全链分解、跃变五步判定、证据包、AI 归因、四行分账、补偿五类、成交登记与逐笔收益、H1–H13 习惯统计、R1/R2 报告、词表与数字回查、skill SOP/MCP 原子工具时使用。触发词：复盘、实况、快照、重放、偏差、分解、跃变、证据包、归因、四行、补偿、登记、逐笔收益、习惯、报告、导出、skill、MCP。
---

# roll-auction-retro — 复盘（R-AI · skills+MCP 新架构）

口径全文：`docu/交易员计算流程与逻辑.md` §14；`docu/复盘架构设计-skills-mcp.md`；
口径唯一来源 `docu/复盘MVP_PRD.md` v1.2 → `docu/复盘PRD-AI归因版.md` v1.8.4。

## 分层（改任何一层前先认清归属）

- **逻辑层 skills**：`backend/skills/*.json`（纯配置 SOP；热加载 + 生命周期管控；无业务代码）。改流程 = 改 JSON，不重启生效；留痕 `skill_run`/`skill_audit`。
- **连接层 MCP**：`backend/mcp/`（`data_server` 核心数据类 / `retro_server` 计算类，两个独立进程，stdio JSON-RPC）。仅原子操作；新增能力 = 注册新 tool。
- **业务层**：`backend/retro/`（actual/snapshot/register/delta/decomp/jump/focus/evidence/attribute/portrait/habit/aggregate/words/report/service/demo）。
- **API**：`backend/retro_api.py`（挂在 api.py，仅新增路由）。

## 硬口径（改代码前必读）

1. **取数只读快照、永不重算**；8 月历史日 = 重放并标「重放值，非当时值」（calc_snapshot.source=replay）。
2. **分解直接调 `Pipeline.run_all()`**（SubLoader 注入替换输入 + 工作库副本保修订），不另写公式；Σc_b + r = ΔP_total 恒等 ≤0.01。单因子按 9 因子全量实现（PRD 写 7，零贡献自然 near_zero，见设计文档偏差记录）。
3. **下临界判定用全实况 run 自洽临界**（dec["actual_critical"]），不混用基线临界。
4. **owner_type 只由 value_type 推导**（constants.VALUE_TYPE_OWNER 集中一处）；联络线拆基线/省间两段；风/光总量整体归交易中心；四行（元级）未归属行 = 残差。
5. **逐笔收益逐点结算写死**：4 点各自相减 × 1/4 电量；禁代表价；缺任一点不出金额；两行分开不合并；净额表述命中即拒导。
6. **词表扫描 + 数字回查 100%** 双闸不过拒出报告；建议行不得含元计价数值/电量/买卖方向。
7. `data/` 只读；既有 M1–M8/pipeline/store/api 既有行为不动（复用只 import）。

## 常用命令

```bash
cd backend && ../.venv/bin/python -m pytest -q                          # 全量口径用例
../.venv/bin/python scripts/retro_smoke.py                              # 全链+MCP+热加载冒烟
../.venv/bin/python -m backend.mcp.client --server retro --list         # MCP 工具清单
../.venv/bin/python -m backend.mcp.client --server retro --tool actual.availability --args '{"start":"2026-08-01","end":"2026-08-31"}'
../.venv/bin/python -m uvicorn api:app --reload                          # API（/api/retro/*）
```
