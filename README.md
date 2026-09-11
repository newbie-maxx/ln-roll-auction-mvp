# 辽宁电力滚搓交易智能体（MVP）

面向辽宁电力现货**省间滚动撮合 + 省内竞价**的交易员决策支持系统：本地 xlsx 边界数据驱动 M1→M8 全链计算（装载 → 分布式推断 → 相似日 → 联络线基线 → 省间 → 实时联络线预测 → 11 步开机推演 → 电价预测/落点概率/灰度量价），SQLite 修订留痕（append-only 可回退），FastAPI 后端 + React 三栏决策工作台，右栏助手为真实 LLM（OpenAI 兼容协议）通过 function calling 读写工作台——**确定性算术永远在 Python 工具内执行，LLM 不做算术、只评审不代拟**。

> 计算口径唯一来源 = `docu/MVP_PRD-1.8.md`（v1.8 全签署）；人读摘要见 `docu/交易员计算流程与逻辑.md`；工具契约见 `docu/智能体工具与技能设计.md`。改动前先读 `AGENTS.md`。

## 功能速览

- **一屏三栏工作台**（§5.4a）：左 24 时段导航（预测价/意向价/负荷率）｜中 可改边界 96 点曲线+数值格、实时联络线预测、火电开机（11 步推演）、负荷率 96+24、预测电价 96+24（最终/预测1/预测2）；点击时段弹面板（该小时边界可改 + 落点概率/期望/灰度量价 + 意向录入草稿点「确认并计算」才重算）｜右 智能助手
- **边界修订留痕**：修改必填理由，原值/修订曲线并存（绿），逐条回退（回退本身也留痕）
- **滚撮日可选**：任选库内 8 边界齐全的日期作预测对象，历史范围/A 日/A-1 回溯自动收窄至该日之前
- **数据表上传**：日前边界表/实时边界表/省间滚撮表（xlsx，按日期合并、后传覆盖同日），滚撮表支持长表（行=日期+小时）与宽表两种布局，`data/` 下含「成交量」sheet 的文件自动发现
- **实时联络线预测** = 联络线基线 + 交易员预测省间交易总量（**正=买入/受入，负=卖出/送出**；24 点输入自动展开 96 点）；运行日火电竞价空间按此计算
- **11 步开机推演**（双模式：系统推演/人工自填）与 M8 电价四步（A 日放缩拟合/A-1 不缩放回溯/钳制/步骤四临界判定）逐条实现，带推导理由
- **LLM 助手**：10 个注册工具（查状态/跑链/改数据/解释依据/导出等），无 key 时返回配置引导；每轮对话与工具序列落 `chat_log`

## 环境要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.12 | 后端 |
| Node.js | ≥ 20（开发期用 24） | 前端构建 |
| git LFS 不需要 | | 数据文件 ≤ 600KB/个，直接入库 |

## 快速开始（单机完整形态，约 5 分钟）

```bash
git clone git@github.com:newbie-maxx/ln-roll-auction-mvp.git
cd ln-roll-auction-mvp

# 1. 后端依赖（独立 venv）
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install pytest          # 跑测试用

# 2. 前端构建（产物由后端静态托管）
cd web && npm install && npm run build && cd ..

# 3.（可选）配置 LLM 助手 —— 不配也能跑，右栏会提示引导
cat > .env <<'EOF'
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_API_KEY=你的key
LLM_MODEL=glm-4.6
EOF
# 任意 OpenAI 兼容端点均可（OpenAI/DeepSeek/GLM/Qwen/内网网关）

# 4. 启动（本仓库自带 8 月边界数据 + 滚撮表，开箱即算）
.venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000** —— 即完整工作台（实时计算模式）。LLM 配置也可在界面右栏「LLM 设置」里填（运行时写入 `.env`，key 不回显）。

> 后端未启动时前端自动降级 demo 模式（顶部橙色横幅区分），demo 用 `web/src/mock/` 的导出数据 + 前端简化计算（开机为常量，11 步只在后端实现）。

## 开发模式（前后端分离热更）

```bash
# 终端 1：后端
.venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000 --reload

# 终端 2：前端 dev server（自动代理到 8000，可用 VITE_API_BASE 覆盖）
cd web && npm run dev        # http://localhost:5173
```

## 测试与冒烟

```bash
# 后端口径用例（41 个：空间手算/24↔96 可逆/M3 成员一致性/M4 基线/M6 恒等式/M7 十一步钳制与半日取 max/
# M8 四步双分支/落档 Σ=1/现货恒等式/修订回退/上传合并/滚撮日收窄/实时联络线符号…）
.venv/bin/python -m pytest backend/tests -q

# 全链冒烟：本地数据 M1→M8 全输出 + 耗时（全量 ≤30s、单点 ≤1s）
.venv/bin/python -m backend.scripts.smoke

# 前端用例（11 个口径边界）
cd web && npx vitest run

# mock 数据幂等导出（重跑输出与已提交 JSON 字节一致）
python3 scripts/export_mock_data.py
```

## 数据说明

| 文件 | 说明 |
|---|---|
| `data/辽宁省8月日前边界.xlsx` | 24 sheets，行=日期、列=96 点（00:15…24:00） |
| `data/辽宁省8月实时边界.xlsx` | 16 sheets，同布局 |
| `data/省间滚撮统计8月.xlsx` | 长表：单 sheet「成交量」，行=日期+小时（每日 24 行）；小时按 h:00–h+1:00 计时段；放任意含「成交量」sheet 的 xlsx 到 `data/` 即自动发现 |

**上传契约**（界面「数据管理」或直接放文件）：日前边界表必需 sheets `负荷/水电/核电/地方燃煤/风电/光伏/联络线/非市场化/日前电价`；实时边界表必需 `实时电价/联络线`；滚撮表必需 `成交量`（`价格` 可选）。布局：首行表头、首列日期、其后数值列（96 点或滚撮 24 点/长表）。校验失败逐项报错不入库；上传即重建底账并全链重算（~1s）。

## 目录结构

```
docu/                        PRD（口径唯一来源，只读）、口径摘要、工具与技能设计
data/                        边界/滚撮 xlsx（业务数据，只读）
.agents/skills/              5 个 roll-auction-* 开发技能（模块算式速查）
vendor/ui-ux-pro-max-skill/  UI/UX 设计 skill（MIT，离线可用）
backend/
  config.py                  参数默认表（PRD §7）+ .env 加载 + 滚撮文件自动发现
  loaders/                   xlsx_loader（M1 装载/合并/滚撮日策略）、upload_manager（上传校验）、roll_reader（长/宽表适配）
  modules/                   m2 分布式 … m7 十一步开机（一步一函数）、m8 四步+落点+灰度
  pipeline.py                M1→M8 编排、滚撮日/数据版本稳定 run 键、修订读时合并
  store.py                   SQLite 五表（boundary_master/revision/intent/calc_run/chat_log），append-only
  api.py                     FastAPI 全部端点 + 静态托管 web/dist
  llm/                       client（工具调用循环 ≤8 次）/tools（10 工具注册表）/prompts/config
  tests/  scripts/smoke.py
web/
  src/calc/                  demo 计算（TS，后端在线时被真实计算替代）
  src/components/            三栏工作台组件
  src/api/client.ts          后端客户端（自动降级 demo）
  src/store.ts               zustand 状态（live/demo 双模式）
scripts/export_mock_data.py  xlsx → web/src/mock/boundaries.json（幂等）
```

## LLM 助手能做什么

- **问答**：读工作台状态、查模块输出与推导理由（样本成员/拟合系数 M1·C1、M2·C2/临界空间/开机中间值）
- **改数据**：改边界/参数/意向（会先复述改动并要求你给理由——必填、不限字数），改完全链重算并回报新预测价
- **评审**：对量价思路给风险提示——**只评审不代拟，不产可申报数值**（护栏 G4）
- 全部对话与工具调用序列落 `chat_log` 快照留痕

## 常见问题

- **`python` 命令不存在**：统一用 `python3`（本机 venv 在 `.venv/`）
- **助手报"未配置 key"**：填 `.env` 或界面右栏「LLM 设置」，重启后端生效（后端启动时加载 `.env`）
- **端口被占**：换 `--port 8001`，前端 dev 模式用 `VITE_API_BASE=http://127.0.0.1:8001 npm run dev`
- **上传后想撤销**：删除 `backend/data/uploads/` 对应文件并重启（上传按时间戳叠加，无删除 UI）
- **想清空修订/对话留痕**：删除 `backend/data/workbench.db*` 重启即得干净库（业务数据与上传不受影响）

## 口径备忘（易错点）

- 火电竞价空间 = 负荷 − 水电 − 核电 − 地方燃煤 − 风电 − 光伏 − **实时联络线预测** − 非市场化（运行日）
- 实时联络线预测 = 联络线基线 **+** 省间交易总量（正=买入/受入；2026-09-10 锁定）
- 历史基线 = 日前联络线 **−** 滚撮量（同符号口径）
- M7 步骤⑥不减受阻系数；⑦⑨增减机 = |盈余| ÷ (1−受阻)（2026-09-10 业务更正）
- 现货恒等式：省间现货 = 日前联络线 − 实时联络线（自算不爬取）
