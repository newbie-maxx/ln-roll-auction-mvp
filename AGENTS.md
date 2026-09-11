# AGENTS.md — 辽宁电力滚搓交易智能体（MVP）

## 项目定位

面向辽宁电力现货滚搓交易（省间滚撮 + 省内竞价）的交易员决策支持智能体。MVP 为 A 轨口径：本地 xlsx 边界数据驱动 M1–M8 全链计算（装载→分布式推断→相似日→基线→省间→预测联络线→开机与负荷率→电价预测/落点/灰度），SQLite 修订留痕（append-only 可回退），FastAPI 后端 + React 三栏决策工作台前端，右栏助手为真实 LLM（OpenAI 兼容协议）通过 function calling 调用 Python 工具读写工作台状态——确定性算术永远在 Python 工具内执行，LLM 不做算术、只评审不代拟。

## 计算口径（唯一来源）

**所有计算口径的唯一来源 = `docu/MVP_PRD-1.8.md`（v1.8 全签署）。任何文档、代码、注释与其冲突时，以 PRD 为准，禁止自创口径。**

人读权威摘要见 `docu/交易员计算流程与逻辑.md`（M1–M9 全量公式）；工具调用契约见 `docu/智能体工具与技能设计.md`。

## 硬规则

1. **每次大功能更改后必须 `git commit`**，提交信息格式 `类型: 中文摘要`，类型限定 `chore` / `docs` / `feat` / `fix` / `refactor`。
2. 修改计算代码前**必读** `docu/交易员计算流程与逻辑.md`，模块归属见下方"技能/工具映射表"；公式存疑时回查 PRD 锚点（§5.4a UI、§6.1 M8、§6b.2 十一步、§7 参数表、附录 D 存储、§5.4b 助手护栏）。
3. `data/` 下 xlsx **只读**（原始业务数据，禁止改动/重命名）；派生数据只写 `backend/data/`（gitignored）或 `web/src/mock/`。
4. LLM key 安全：key 只存 `.env` 或运行时内存，**严禁写入代码、日志、数据库、git 提交**；接口回显时遮蔽。
5. 计算逻辑变更必须同步更新 `docu/交易员计算流程与逻辑.md` 与对应 skill，再 commit。

## 目录结构

| 路径 | 说明 |
|---|---|
| `docu/MVP_PRD-1.8.md` | PRD v1.8（唯一口径来源，只读） |
| `docu/交易员计算流程与逻辑.md` | M1–M9 全量公式人读权威摘要 |
| `docu/智能体工具与技能设计.md` | 工具注册表（10 工具）+ 5 skill 拆分 + 交互模式 |
| `data/*.xlsx` | 8 月日前/实时边界（96 点，只读） |
| `.agents/skills/roll-auction-*` | 5 个计算技能（开发层，供 omp agent 加载） |
| `vendor/ui-ux-pro-max-skill/` | vendored UI/UX skill（MIT，离线可用） |
| `vendor/ppt-master-skill/` | vendored ppt-master skill（MIT，AI 生成/重建/模板填充可编辑 PPTX；依赖 `skills/ppt-master/requirements.txt`） |
| `.omp/config.yml` | omp 项目配置（skills.customDirectories） |
| `scripts/export_mock_data.py` | xlsx → `web/src/mock/boundaries.json` 幂等导出 |
| `web/` | React+TS+Vite+Tailwind v4+ECharts 前端 |
| `web/src/calc/` | demo 计算（TS，Step 8 由后端替换） |
| `web/src/mock/` | mock 边界数据（导出生成） |
| `backend/config.py` | 参数默认表（PRD §7）+ env |
| `backend/loaders/xlsx_loader.py` | M1 装载（多 sheet、24→96、标注、补齐、可替换接口） |
| `backend/modules/m2..m8` | 计算模块（每模块对应 PRD 章节） |
| `backend/store.py` | SQLite 留痕（附录 D：boundary_master/boundary_revision/intent_input/calc_run/chat_log） |
| `backend/pipeline.py` | M1→M8 编排 + 单点联动重算 |
| `backend/mock/roll_auction.py` | 省间滚撮确定性合成（真实 A-0 文件到位后弃用） |
| `backend/api.py` | FastAPI（/api/state、recalc、params、boundary、intent、revision/rollback、export、llm/config、chat、静态托管 web/dist） |
| `backend/llm/` | client（OpenAI 兼容 + function-calling 循环）/ tools / prompts |
| `backend/scripts/smoke.py` | 全链耗时与输出完整性冒烟 |
| `backend/tests/` | pytest（口径边界用例） |

## 技能/工具映射表

| Skill（`.agents/skills/`） | 计算模块（`backend/`） | LLM 工具（`backend/llm/tools.py`） | PRD 锚点 |
|---|---|---|---|
| `roll-auction-data` | `loaders/xlsx_loader.py`、`mock/roll_auction.py`、`config.py` | `get_state`、`set_params`、`export_report` | §5.4a、§7 |
| `roll-auction-tieline` | `modules/m2_distributed.py`–`m6_tieline.py` | `search_similar_days`、`get_module(m2..m6)` | §6b.1 |
| `roll-auction-thermal` | `modules/m7_thermal.py` | `get_module(m7)` | §6b.2 |
| `roll-auction-pricing` | `modules/m8_pricing.py` | `explain_period`、`get_module(m8)` | §6.1 |
| `roll-auction-agent-ops` | `llm/tools.py`、`llm/prompts.py`、`api.py` | `run_chain`、`modify_boundary`、`set_intent`、`rollback_revision` | §5.4b、附录 D |

改某个模块的计算逻辑 → 先读对应 skill 与 Step 3 口径文档 → 改代码 → 同步 skill 与文档 → commit。

## LLM key 安全规则

- 三要素配置：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（OpenAI 兼容 `/chat/completions`）。
- key 只存 `.env`（gitignored）或进程内存；`POST /api/llm/config` 运行时写入，回显永远遮蔽（`sk-****`）。
- 日志、chat_log、异常栈中禁止出现完整 key；写入前过滤。

## 常用命令

```bash
# web/
cd web && npm run dev          # Vite dev (5173)
cd web && npm run build        # 产 web/dist
cd web && npx vitest run       # 前端口径用例

# backend/
cd backend && uvicorn api:app --reload   # API (8000, 托管 web/dist)
cd backend && python -m pytest -q        # 后端口径用例（环境无 python 别名时用 python3）
cd backend && python scripts/smoke.py    # 全链冒烟（全量 ≤30s、单点 ≤1s）

# 根
python3 scripts/export_mock_data.py      # 幂等导出 mock（重跑须与已提交 JSON 一致）
```

## 注记
- `vendor/ui-ux-pro-max-skill` 与 `vendor/ppt-master-skill`（注册路径 `vendor/ppt-master-skill/skills`）均挂 `.omp/config.yml` 的 `skills.customDirectories`；`skill://` 解析需要新会话。
- `python` 命令在本机不存在时统一用 `python3`。
