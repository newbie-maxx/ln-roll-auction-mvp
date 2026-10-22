# 复盘架构设计（skills + MCP，B2：到 REST API 层）

| 项 | 内容 |
| --- | --- |
| 上游口径 | `复盘MVP_PRD.md` v1.2（R-AI 路线）→ 唯一口径来源 `复盘PRD-AI归因版.md` v1.8.4 |
| 范围 | B2：复盘全部功能跑在 skills+MCP 新架构，暴露 REST API；前端（M-14/X-22）与 LLM 助手接入另立 goal |
| 禁区 | `data/`、既有 M1–M8 模块与 pipeline 既有行为、`web/`；`api.py` 仅新增路由挂载 |
| 本文 | 逻辑抽象（SOP 拆解、原子工具清单、数据模型、skill 资产规范、确定性保障、验证方案） |

## 1. 三层架构

```
REST API（backend/retro_api.py，挂在 api.py）
    │  入参：区间 / 登记记录 / 快照指令
    ▼
逻辑层 skills（backend/skills/*.json，纯配置 SOP 资产，无业务代码）
    │  由 skill_runtime 解析、校验、编排、热加载、生命周期管控
    ▼
连接层 MCP（backend/mcp/，JSON-RPC stdio，原子工具）
    ├─ mcp_data   核心数据类：SQLite D-A* 读写（独立常驻进程，WAL）
    └─ mcp_retro  计算类：复盘原子计算（独立常驻进程，import 复用 Pipeline/loaders）
```

- **skill = 一个完整业务场景的 SOP**：JSON 配置（YAML 解析器不在依赖白名单），含元数据、生命周期状态、输入约束、步骤序列（工具调用 + 条件/循环/断言）。**内部不含可执行代码**；表达式仅支持白名单运算（比较、算术、与或非、索引、len）。
- **MCP 工具 = 原子操作**：一次调用做一件事（读一日实况、算一张 Δ 表、判一次跃变、写一行登记……），不承载跨环节业务逻辑；跨环节编排只存在于 skill SOP。
- **热加载**：registry 每次执行前按文件 mtime+sha256 比对，变更即重载并记审计；服务不重启。
- **生命周期**：`draft → active ↔ suspended → deprecated → offline`；仅 `active` 可执行；状态迁移写 `skill_audit`（append-only）。

## 2. 复盘逻辑抽象：SOP 拆解

对应 PRD 九环节（A1/A2/B/C/D/E/F/G/H），拆为 4 个 skill：

### 2.1 `retro_day`（单日复盘，R1）与 `retro_range`（多日复盘，R2）

步骤序列（R1/R2 共用主干，粒度差异在聚合与报告类型）：

| # | 环节 | 步骤（MCP 工具） | 说明 |
| --- | --- | --- | --- |
| 1 | A1 | `actual.load_day(run_date)` ×N | 8 类边界 + 实时电价 + 负荷率，96 点，缺数标缺不插值，落 D-A5 |
| 2 | 前置 | `actual.availability(start,end)` | 可用日清单 → `N_eff`；`=1` 走 R1，`>1` 走 R2 |
| 3 | B | `snapshot.get_or_replay(run_date, item)` | 优先读 D-A6 生效快照；缺则用当时参数重放并标「重放值，非当时值」 |
| 4 | B | `delta.compute(run_date)` | Δ 表 96×8+电价：Δ=实况−当时预测；缺数标缺；修订点双值 `V_sys`/`V_rev`，落 D-A1 |
| 5 | C | `decomp.run(run_date)` | 8 次全链（1 全实况 + 7 单因子）→ `c_b`、`share_b`、逐点残差 `r(t)`、`explained_ratio` |
| 6 | D | `jump.classify(run_date)` | 五步判定（下临界→上临界→模式切换→行为跃变→未定性）；缺口倒推+1~3 次验证；F1/F2/F3 |
| 7 | X-9 | `focus.build(run_date)` | P/T/D/S 集 + 2×2 分类 + 优先级（深析上限 5） |
| 8 | E | `evidence.build(run_date)` | 证据包：编号、Top-5、near_zero、anomaly、manual_layer、data_quality；逐字段可复现 |
| 9 | F | `attribute.generate(evidence_pack)` | LLM 四段（断网/无 key 走桩）；数字溯源 100%、方向一致、词表 0 命中；不过重试 1 次再降级 |
| 10 | G | `portrait.split(run_date)` | 四行分账（value_type→owner_type；联络线拆两段；风/光整体归交易中心）、两把尺子、披露诱导标记 |
| 11 | G | `portrait.compensate(run_date)` | 逐点 `E_before/E_after/G` → A~E 五类；CA1–CA3 才写「节奏正确」；双结论 |
| 12 | A2 | `register.settle_day(run_date)` | D-A9 逐笔收益：4 点现货逐点相减、电量 1/4 均摊；缺任一点不出金额 |
| 13 | G | `habit.stats(owner, d_end)` | H1–H13 + 显著性（≥20 天+70%+p<0.05）+ 剔除规则 + G/G⁻ 五种比对；落 D-A3 |
| 14 | H | `report.render(start,end)` | R1：96 点全覆盖十章；R2：24 时段聚合十章；12 张图；数字校验 100% |
| 15 | H | `archive.export(report_id)` | 词表扫描（命中拒导）→ md/csv + PNG → sha256 → D-A8 |

### 2.2 `trade_register`（成交登记）

表单单笔 / CSV 批量 → 录入口径校验 6 条（时段必填、价 −1000~1500、量 0~10000、方向∈买卖、`result_type` 二选一互斥、同时段同 trader 同 result_type 不得双写→走修订）→ 自动带 `snapshot_trade_date` → append-only 落 D-A9（version 链）→ 派生 `settle_points`/`pnl`。

### 2.3 `snapshot_capture`（交易结束/预测落档）

调 Pipeline 全链（import 复用，不改 pipeline.py）→ 取实际生效参数与代码版本 → 按 `(run_date, trade_date, seq, item, t)` 落 D-A6 → 同 `(run_date, trade_date)` 重复调用走软生效（切换 `effective`，旧快照不删）。

> **与 PRD §0.1.3 的偏差（B2 边界所致，显式记录）**：PRD 要求挂钩既有 `recalc()`（改 pipeline.py）并修 `pipeline.py:184`/`store.py:76` 两个假字段。B2 禁改既有链路，故落档由本 SOP 承担（复盘平台的「交易结束」入口）；既有 `/api/recalc` 不落快照，其算得的旧表字段维持原样。快照行内 `params_snapshot`/`code_version` 由复盘层写真值，PH-6 的实质要求（当时值可取、只读快照永不重算）不受影响。

## 3. 原子工具清单（MCP）

**mcp_data（核心数据类，独立进程，SQLite WAL）**

| 工具 | 原子操作 |
| --- | --- |
| `db.append(table, row)` / `db.append_batch(table, rows)` | append-only 写（D-A1/A2/A3/A5/A6/A7/A8/A9/A10、skill_run、skill_audit） |
| `db.query(table, where, order, limit)` | 只读查询（固定白名单列排序，保证顺序确定） |
| `db.latest_version(table, keys)` | 取业务唯一键最新 version 行 |
| `skill.audit(event)` | 生命周期/热加载事件落审计 |

**mcp_retro（计算类，独立进程）**

| 工具 | 对应模块 | 原子操作 |
| --- | --- | --- |
| `actual.load_day` / `actual.availability` | M-01 | 实况装载/可用日清单（data/ 只读） |
| `snapshot.get_or_replay` / `snapshot.capture` | M-02 | 快照读取/重放标注；交易结束落档 |
| `register.validate` / `register.save` / `register.import_csv` / `register.settle_day` / `register.day_list` | M-03 | 登记校验/保存/批量/逐笔收益/清单 |
| `delta.compute` | M-04 | Δ 表 |
| `decomp.run` | M-05 | 8 次全链分解 + 残差 |
| `jump.classify` | M-06 | 跃变五步 + 缺口倒推 + 上临界标注 |
| `focus.build` | M-07 | P/T/D/S 集与优先级 |
| `evidence.build` | M-05 | 证据包组装（封闭数字集） |
| `attribute.generate` / `attribute.validate_text` | M-08 | LLM 四段 / 溯源·方向·越界校验（独立可调） |
| `portrait.split` / `portrait.compensate` / `portrait.disclosure_quality` | M-10 | 四行分账 / 五类补偿 / 跨场次披露质量统计表 |
| `habit.stats` | M-11 | H1–H13 + 显著性 + G/G⁻ |
| `aggregate.to24` / `aggregate.expand24to96` | M-12 | 96→24 区间均值 / 24→96 只复制不插值 |
| `report.render` / `report.check_numbers` | M-13 | 报告骨架渲染 / 数字回查 100% |
| `words.scan` / `archive.export` | M-09 | 词表扫描（命中即拒）/ 导出+sha256+留档 |
| `chart.render` | X-18 | pngchart 出 PNG（12 张图规格） |

## 4. 数据模型（同一 workbench.db，新表，全部 append-only）

| 表 | 对象 | 关键列 | 唯一键 |
| --- | --- | --- | --- |
| `retro_actual` | D-A5 | run_date, item, t, value, missing, source | (run_date, item, t, load_seq) |
| `calc_snapshot` | D-A6 | run_date, trade_date, seq, item, t, value, used_pred2, k, m1, c1, m2, c2, params_json, code_version, effective, created_at | (run_date, trade_date, seq, item, t) |
| `retro_deviation` | D-A1 | run_date, item, t, v_pred, pred_source, v_sys, v_rev, v_act, delta, missing, trader_id | (run_date, item, t, version) |
| `retro_snapshot` | D-A2 | owner, run_date, period, pack_version, pack_json, ai_text, model, params_json, check_result, created_at | (owner, run_date, period, pack_version) |
| `retro_habit` | D-A3 | owner, scope, window_start, window_end, n_days, h_json, divergence, version, created_at | (owner, scope, window_start, window_end, version) |
| `retro_threshold` | D-A7 | run_date, theta_high, theta_jump, theta_delta, source, set_by, set_at, reason, revised, version | (run_date, version) |
| `retro_report` | D-A8 | report_id, rtype, d_start, d_end, n_eff, template_id, template_version, content_json, fmt, file_name, sha256, generated_by, generated_at, superseded_by | report_id |
| `retro_trade_register` | D-A9 | run_date, t, result_type, price, volume_mwh, direction, trader_id, entered_at, source, confidence, snapshot_trade_date, settle_points, pnl, version, created_at | (run_date, t, result_type, trader_id, version) |
| `retro_owner_comp` | D-A10 | run_date, t, boundary, v_sys, v_rev, v_act, owner_type, system_tier, split_part, e_before, e_after, gain, comp_result, disclosure_induced, snapshot_trade_date, version | (run_date, t, boundary, split_part, version) |
| `skill_run` | — | run_id, skill_id, skill_version, inputs_json, steps_json, status, started_at, finished_at | run_id |
| `skill_audit` | — | id, skill_id, event, detail_json, created_at | id 自增 |

硬规则：`owner_type` 只由 `value_type` 推导（披露→交易中心；推断/补齐/计算→系统；修订/意向→交易员；缺输入→未归属），推导集中一处；缺行=未登记≠未成交；阈值判历史只读当日 D-A7（无则默认 600/300/100 并即时补写 `source=default`）。

## 5. skill 资产规范（JSON）

```json
{
  "id": "retro_day",
  "name": "单日复盘（R1）",
  "version": "1.0.0",
  "status": "active",            // draft|active|suspended|deprecated|offline
  "owner": "retro",
  "inputs": {"run_date": {"type": "string", "required": true}},
  "steps": [
    {"tool": "actual.load_day", "args": {"run_date": "${run_date}"}, "output": "actual"},
    {"assert": "${actual.missing_count} >= 0", "message": "装载结果必须带缺失计数"},
    {"if": "${actual.available} == false", "then": [{"fail": {"code": "NO_ACTUAL", "message": "该日无实况"}}]},
    {"tool": "delta.compute", "args": {"run_date": "${run_date}"}, "output": "delta"},
    {"foreach": "${days}", "as": "d", "body": [ ... ]},
    {"while": "${need_verify} and ${verify_runs} < 3", "max_iter": 3, "body": [ ... ]}
  ]
}
```

- 步骤类型：`tool`（MCP 调用）/ `if`（then/else）/ `foreach` / `while`（max_iter 必填）/ `assert` / `assign` / `fail`。
- 表达式：`${...}`，白名单：字面量、上下文引用、算术、比较、and/or/not、len()、索引；禁止任意函数调用。
- 执行留痕：每次运行把解析后的步骤序列（含实参快照）写入 `skill_run`，保证「同一输入可复现」可审计。
- 校验：加载时做 schema 校验（步骤类型、表达式可解析、工具名在注册表中存在、status 合法），不过则拒绝载入并保持旧版本生效 + 审计告警。

## 6. 确定性保障（P-6：证据包 100% 可复现）

1. 所有聚合固定遍历序：t 升序、边界按固定列表（BOUNDARY_WHITELIST 序）、字典序列化前键排序。
2. 浮点序列化统一 `round(x, 6)` 后输出；累加按固定顺序（先 t 后项）。
3. 缺数表示统一为 JSON `null`；时间统一 `Asia/Shanghai` 本地无时区 ISO 字符串。
4. 证据包不含时间戳以外的随机量；`generated_at` 单列、比对时排除。
5. MCP 调用参数与返回按 JSON 规范序列化（ensure_ascii=False, sort_keys=True）。

## 7. 验证方案

| 验收 | 命令/动作 |
| --- | --- |
| 口径用例 | `cd backend && python3 -m pytest -q`（新增 tests/retro/*，覆盖 §8 清单） |
| MCP 独立调用 | `python3 -m backend.mcp.client --server retro --tool delta.compute --args '{...}'` 直连核对 |
| 热加载 | 服务运行中修改 `backend/skills/*.json`（如改阈值断言）→ 重发请求 → 新 SOP 生效 + skill_audit 有记录 |
| API | `curl -X POST localhost:8000/api/retro/report -d '{"start":"2026-08-14","end":"2026-08-14"}'` 与用例期望比对 |
| 冒烟 | `python3 backend/scripts/retro_smoke.py`（全链 R1+R2+登记+落档+导出计时） |

## 8. 口径用例清单（pytest 覆盖点）

1. 登记校验 6 条 + 修订 version 链 + CSV 整批拒绝；逐笔收益手算样例（8-14 15 时 [295,−50,−48,294]×283×80MWh→+12,820；10 时→+900；18 时机会成本→+3,000）；缺任一点现货不出金额；「两者相加＝当日执行溢价」仅单日版出现。
2. Δ 表：符号统一（实况−预测）、缺数标缺不插值、修订点 `V_sys`/`V_rev` 双值。
3. 分解：Σ`c_b`+残差=总落差（容差 0.01）；`share_b` 分母 Σabs(c)；复现两次逐字段相同。
4. 跃变：五步顺序命中即停；下临界缺口倒推（同向降序累加首次 ≥G，排序键=对空间影响非偏差绝对值）；上临界 U1+U2+U3 三条同时（U2 才是闸门）；类型二开关翻转；类型三 C1/C2/C3 同时；F1/F2/F3 只进 anomaly。
5. 四行分账：`(V_rev−V_sys)+(V_act−V_rev) = V_act−V_sys` <1MW；联络线两段之和=该项总偏差；风/光总量整体归交易中心；兜底三项只提示不定责。
6. 补偿五类 A~E 判定与 CA1–CA3 措辞门槛；双结论两句同段。
7. 习惯：≥20 天+70%+p<0.05 三条同时；类型三日整日剔除；披露诱导点级剔除；G⁻<20 退化标注。
8. 聚合：24→96 只复制（阶梯）；96→24 区间均值按实有天数；图注「区间均值（N 天）」。
9. 词表：技术术语/口语/净额表述命中即拒导；建议含报价数值/电量/方向即删。
10. 快照：同日多次落档 seq 递增不覆盖；软生效旧版保留；历史日重放标「重放值，非当时值」。
11. skill 引擎：schema 校验拒绝坏配置；热加载生效；status 非 active 拒执行；表达式越权（函数调用）拒绝。
12. 报告：R1 96 行×9 列一行不缺；数字回查 100%；12 张图数据与证据包逐点一致。
