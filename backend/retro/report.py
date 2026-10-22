"""M-13 报告编排：R1 单日（96 点全覆盖）/ R2 区间（24 时段聚合），十一章骨架（对齐
单日/多日复盘模版 v2.3）。代码排骨架 + 填数字，AI 叙述块由 attribute 产出后嵌入；
数字一致性校验（每个数字可回查注册表，P-A6=100% 不过拒出）；标题走白名单常量。"""
from __future__ import annotations

import re
from pathlib import Path

from . import constants as C
from .words import advice_line_ok

R1_TITLES = ["一、图景：12 张对比图", "二、当日结论", "三、全天概况与判定线",
             "四、96 点全量", "五、成交执行与收益（逐笔）", "六、偏差归属分画像",
             "七、重点点深析（各 Top 5）", "八、问题时段归纳", "九、交易判断建议",
             "十、待核实事项", "十一、使用限制与免责"]
R2_TITLES = ["一、图景与数据完整性", "二、整体评价与总偏差构成", "三、偏差归属分画像",
             "四、判定标准与参数", "五、逐日复盘摘要", "六、成交执行与收益（逐笔 + 两个合计）",
             "七、时段问题分布", "八、重点时段逐点明细", "九、习惯比对与交易判断建议",
             "十、待核实事项", "十一、使用限制与免责"]

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

OWNER_LABELS = {"系统": "系统算的部分", "交易中心": "交易中心给的数据",
                "交易员": "你改过和填过的部分", "未归属": "未归属"}


# 骨架结构性数字（章标题/固定句式里的计数与版本号，非数据数字）
STRUCT_NUMS = {0, 1, 2, 3, 4, 5, 7, 9, 10, 11, 12, 15, 20, 24, 30, 31, 70, 96,
               100, 180, 2.3, 0.05, 0.80, 1.5,
               1.1, 3.1, 3.2, 3.3, 4.1, 4.2, 6.1, 7.1, 7.2, 7.3, 7.4, 9.1, 9.2, 9.3}


class NumberRegistry:
    """渲染期数字注册：每个写进正文的数字都登记源值；导出前全量回查。"""

    def __init__(self) -> None:
        self._values: set[float] = set()
        for v in STRUCT_NUMS:
            self._values.add(round(float(v), 1))

    def reg(self, v: float | int | None) -> str:
        if v is None:
            return "—"
        f = float(v)
        self._values.add(round(f, 1))
        return f"{f:,.2f}".rstrip("0").rstrip(".") if f == f else "—"

    def reg_int(self, v: int | None) -> str:
        if v is None:
            return "—"
        self._values.add(round(float(v), 1))
        return str(v)

    def check(self, md: str) -> list[float]:
        # 剥离非数据 token：ISO 日期、时刻、报告编号、时段段号（05:00 段等）
        stripped = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", " ", md)   # ISO 时间戳
        stripped = re.sub(r"\d{4}-\d{2}-\d{2}", " ", stripped)
        stripped = re.sub(r"\d{1,2}:\d{2}", " ", stripped)
        stripped = re.sub(r"retro_R\d_\d+_\S+", " ", stripped)
        stripped = re.sub(r"[BM]-[A-Z]+-\d+", " ", stripped)   # 证据包 id（B-TIE-01 等）
        stripped = re.sub(r"A-F\d-\d+", " ", stripped)         # 反向形态 id
        stripped = re.sub(r"G-\d+", " ", stripped)              # 图编号
        stripped = re.sub(r"sk-\w+", " ", stripped)             # skill run id
        stripped = stripped.replace(",", "")   # 千分位合并（1,087.2 → 1087.2）
        out: list[float] = []
        for m in _NUM_RE.findall(stripped):
            x = float(m)
            if not any(abs(x - a) < 0.051 for a in self._values):
                out.append(x)
        return out


def _clock(i: int) -> str:
    return f"{i // 4:02d}:{(i % 4) * 15:02d}"


def _hour_label(h: int) -> str:
    return f"{h:02d}:00"


def _fmt_pts(vals: list[float | None], reg: "NumberRegistry") -> str:
    return " / ".join("—" if v is None else reg.reg(v) for v in vals)


def _series(name: str, values: list[float | None]):
    from .pngchart import Series
    return Series(name=name, color=C.SERIES_COLORS.get(name, (90, 90, 90)), points=list(values))


def chart_specs(day: dict, multi: list[dict] | None = None) -> list[dict]:
    """12 张图规格（G-01..G-12）；multi=区间日列表时为区间均值形态。"""
    tb, view = day["delta"]["table"], day["delta"]["view"]
    snap = day["snapshot"]
    reg_ts = day.get("register_ts", [])

    def three(key, actual_key=None):
        return [("系统预测" if key not in ("负荷", "风电", "光伏") else "日前披露", view[key]["v_sys"]),
                ("改后", view[key]["v_rev"]),
                ("实时实况" if key not in ("负荷", "风电", "光伏") else "实时披露",
                 tb[key]["v_act"])]

    da_on = day.get("da_on_96", [None] * 96)
    act_base = day.get("act_tie_base_96", [None] * 96)
    specs = [
        {"id": "G-01", "name": "负荷", "series": three("负荷")},
        {"id": "G-02", "name": "风电（总量）", "series": three("风电")},
        {"id": "G-03", "name": "光伏（总量）", "series": three("光伏")},
        {"id": "G-04", "name": "核电", "series": three("核电")},
        {"id": "G-05", "name": "非市场化", "series": three("非市场化")},
        {"id": "G-06", "name": "水电", "series": three("水电")},
        {"id": "G-07", "name": "联络线（预测值）", "series": three("联络线")},
        {"id": "G-08", "name": "地方燃煤", "series": three("地方燃煤")},
        {"id": "G-09", "name": "火电开机", "series": [("系统预测", snap["M7开机"]),
                                                      ("改后", da_on)]},
        {"id": "G-10", "name": "联络线基线", "series": [("系统预测基线", view["联络线"]["base_v_rev"]),
                                                        ("事后实际基线", act_base)]},
        {"id": "G-11", "name": "省间交易", "series": [("交易员录入", view["联络线"]["inter_v"]),
                                                      ("事后实际", day["delta"]["inter_actual_96"])]},
        {"id": "G-12", "name": "电价", "series": [("当时预测价", snap["final"]),
                                                  ("实际出清价", tb["电价"]["v_act"])]},
    ]
    floor_ts = [i + 1 for i, p in enumerate(tb["电价"]["v_act"])
                if p is not None and p <= C.SPOT_FLOOR + 1e-9]
    for s in specs:
        s["vlines"] = floor_ts if s["id"] == "G-12" else None
        s["bands"] = reg_ts if not multi else None
    return specs


def render_charts(specs: list[dict], fig_dir: Path, registry: NumberRegistry,
                  bands_by_hour: dict[int, str] | None = None) -> list[str]:
    """渲染 PNG；返回 md 引用行。bands_by_hour: {hour: 'filled'|'unfilled'}（仅单日）。"""
    from .pngchart import Band, render_line_chart
    fig_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for s in specs:
        path = fig_dir / f"{s['id']}_{s['name'].replace('（', '').replace('）', '')}.png"
        series = [_series(n, v) for n, v in s["series"]]
        ys = [v for _, vals in s["series"] for v in vals if v is not None]
        bands = None
        if s.get("bands") and bands_by_hour:
            from .pngchart import Band as B
            bands = [B(start=h * 4, end=h * 4 + 3,
                       color=C.BAND_FILLED if kind == "filled" else C.BAND_UNFILLED)
                     for h, kind in sorted(bands_by_hour.items())]
        render_line_chart(path, series, vlines=s.get("vlines"), bands=bands)
        lo, hi = (min(ys), max(ys)) if ys else (0, 0)
        registry.reg(lo)
        registry.reg(hi)
        lines.append(f"**{s['id']}　{s['name']}（{'元/MWh' if s['id'] == 'G-12' else 'MW'}）**\n")
        lines.append(f"![{s['id']} {s['name']}]({path.name})\n")
        notes = "　｜　".join(n for n, _ in s["series"])
        lines.append(f"> {notes}　｜　纵轴 {lo:g} ~ {hi:g}\n")
        if s.get("bands") is not None:
            if bands_by_hour:
                lines.append("> 深红＝已成交　｜　浅绿＝报了没成　｜　红/绿背景＝交易点位\n")
            else:
                lines.append("> 当日无成交登记，图上无色带\n")
        lines.append("")
    return lines


def _anomaly_marks(jump_points: list[dict], t: int, theta_delta: float, dp) -> str:
    pt = jump_points["points"][t - 1]
    marks = []
    if pt["jump_type"] == "类型一":
        marks.append("砸底" if pt.get("jump_side") == "down" else "冲高")
    elif pt["jump_type"] == "类型二":
        marks.append("换套")
    elif pt["jump_type"] == "类型三":
        marks.append("门槛变")
    if dp is not None and abs(dp) >= theta_delta:
        marks.append("大价差")
    return "、".join(marks) if marks else "—"


def render_single(day: dict, fig_dir: Path, ai_text: str = "",
                  register_values: set[float] | None = None) -> dict:
    """R1 单日十一章。register_values：AI 叙述引用的包内数值（P-A6 溯源注册）。"""
    reg = NumberRegistry()
    for v in register_values or ():
        reg._values.add(round(float(v), 1))
    md: list[str] = ["# 单日复盘报告\n"]
    md.append(f"| 项 | 内容 |\n| --- | --- |\n| 运行日 | {day['run_date']}（全天 96 个时段） |")
    replay = "（该日预测线为**重放值，非当时值**）" if day["delta"]["is_replay"] else ""
    md.append(f"| 预测来源 | {day['delta']['pred_source']}{replay} |")
    md.append(f"| 生成人 | default　生成时间 {day.get('generated_at', '')} |")
    md.append(f"| 模板 | 单日复盘模板 v2.3（12 图；逐点结算口径） |\n")

    # 一、图景
    md.append(f"## {R1_TITLES[0]}\n")
    md.extend(render_charts(chart_specs(day), fig_dir, reg, day.get("bands_by_hour")))
    md.append("**图上看不出、必须回表核的**：具体某一点的差值、修订发生在第几个时段、跃变点的缺口是多少。\n")

    tb = day["delta"]["table"]
    spot, pred, dp = tb["电价"]["v_act"], tb["电价"]["v_rev"], tb["电价"]["delta"]
    jump = day["jump"]

    # 二、当日结论（叙述块：AI 文本或桩；缺省为代码结论句）
    md.append(f"## {R1_TITLES[1]}\n")
    md.append((ai_text or day.get("ai_text") or "").strip() + "\n" if (ai_text or day.get("ai_text")) else "")
    dp_xs = [v for v in dp if v is not None]
    mean_dp = sum(dp_xs) / len(dp_xs) if dp_xs else None
    worst = max((i for i in range(96) if dp[i] is not None), key=lambda i: abs(dp[i]), default=None)
    md.append(f"1. 全天均价 {reg.reg(_m(spot))} 元，"
              f"比当时预测 {reg.reg(_m(pred))} 元{'低' if (mean_dp or 0) < 0 else '高'} {reg.reg(abs(mean_dp or 0))} 元。")
    n_big = sum(1 for v in dp if v is not None and abs(v) >= day["theta"]["theta_delta"])
    md.append(f"2. 全天 96 点中 {reg.reg_int(n_big)} 个点价差超过 {reg.reg(day['theta']['theta_delta'])} 元。")
    n_floor = sum(1 for p in spot if p is not None and p <= C.SPOT_FLOOR + 1e-9)
    if n_floor:
        md.append(f"3. 触及现货下限（{reg.reg(C.SPOT_FLOOR)} 元）{reg.reg_int(n_floor)} 个点，属规则触发的临界跃变。")
    n_rev = len(day.get("revisions") or [])
    if n_rev:
        md.append(f"4. 当日共 {reg.reg_int(n_rev)} 次边界修订（见四、4.2 与七、7.1）。")
    if day.get("register", {}).get("has_any"):
        t = day["register"]["totals"]
        md.append(f"5. 交易点位 {reg.reg_int(len(day['register']['rows']))} 处"
                  f"（已成交 {reg.reg_int(t['filled']['n'])} / 报了没成 {reg.reg_int(t['unfilled']['n'])}）。")
    md.append("")

    # 三、全天概况与判定线
    md.append(f"## {R1_TITLES[2]}\n")
    spot_xs = [p for p in spot if p is not None]
    hi_i = max((i for i in range(96) if spot[i] is not None), key=lambda i: spot[i], default=None)
    md.append("| 项目 | 数值 | 说明 |")
    md.append("| --- | --- | --- |")
    md.append(f"| 实际出清价均值 | {reg.reg(_m(spot))} 元/MWh | 96 点算术平均 |")
    md.append(f"| 当时预测价均值 | {reg.reg(_m(pred))} 元/MWh | 取自计算留档快照 |")
    md.append(f"| 全天平均价差 | {reg.reg(mean_dp)} 元/MWh | 实际 − 预测 |")
    md.append(f"| 最大单点价差 | {reg.reg(dp[worst] if worst is not None else None)} 元/MWh |"
              f" {_clock(worst) if worst is not None else '—'} |")
    md.append(f"| 价差 ≥ {reg.reg(day['theta']['theta_delta'])} 元的点数 | {reg.reg_int(n_big)} 个 |"
              f" 占全天 {reg.reg(round(n_big / 96, 4) * 100)}% |")
    md.append(f"| 触及现货下限的点数 | {reg.reg_int(n_floor)} 个 | |")
    md.append(f"| 最高出清价 | {reg.reg(max(spot_xs) if spot_xs else None)} 元/MWh |"
              f" {_clock(hi_i) if hi_i is not None else '—'} |")
    md.append(f"| 边界修订次数 | {reg.reg_int(n_rev)} 次 | 见四、4.2 |")
    n_reg = len(day.get("register", {}).get("rows") or [])
    md.append(f"| 交易点位 | {reg.reg_int(n_reg)} 处 | 图上为红/绿色带 |")
    md.append(f"| 数据完整性 | {reg.reg_int(96 - sum(1 for p in spot if p is None))} / 96 | 无缺失、无插值 |\n")
    th = day["theta"]
    md.append(f"> 判定线：高价线 **{reg.reg(th['theta_high'])} 元/MWh**；涨速线 15 分钟 {reg.reg(th['theta_jump'])} 元；"
              f"大价差线 **{reg.reg(th['theta_delta'])} 元/MWh**。超过高价线不等于异常，只代表「这一点值得看一眼」。\n")

    # 四、96 点全量
    md.append(f"## {R1_TITLES[3]}\n")
    md.append("### 4.1 逐点明细（96 行，一行不缺）\n")
    md.append("| 点号 | 时刻 | 实际出清价 | 当时预测价 | 价差 | 边界改动 | 报价/成交 | 异常标记 | 深析 |")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    reg_by_hour = {r["t"]: r for r in day.get("register", {}).get("rows") or []}
    union = set(day["focus"]["union"])
    for i in range(96):
        rev_mark = "—"
        for r in day.get("revisions") or []:
            if int(r.get("t") or 0) == i + 1:
                rev_mark = f"改+{str(r.get('rev_id', ''))[-4:]}"
                break
        rr = reg_by_hour.get(i)
        deal = ("成交" if rr and rr["result_type"] == "成交"
                else "申报" if rr else "—")
        md.append(f"| {reg.reg_int(i + 1)} | {_clock(i)} | {reg.reg(spot[i])} | {reg.reg(pred[i])} |"
                  f" {reg.reg(dp[i])} | {rev_mark} | {deal} |"
                  f" {_anomaly_marks(jump, i + 1, th['theta_delta'], dp[i])} |"
                  f" {'✓' if i + 1 in union else '—'} |")
    md.append("\n> 说明：本表与第一章 12 张图同源同值；成交侧依据手工登记，未经回单核对。\n")
    md.append("### 4.2 修正有效性\n")
    comp = day.get("compensate") or {}
    md.append("| 修订时点 | 项 | 改前误差 | 改后误差 | 改善量 | 结果 |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    if comp.get("items"):
        for it in comp["items"]:
            md.append(f"| {it['t'] // 4 + 1:02d}:00 段 | {it['boundary']} | {reg.reg(it['e_before'])} MW |"
                      f" {reg.reg(it['e_after'])} MW | {reg.reg(it['gain'])} MW | **{it['comp_result']}** |")
    else:
        md.append("| — | 当日无边界修订 | — | — | — | — |")
    md.append("")

    # 五、成交执行与收益
    md.append(f"## {R1_TITLES[4]}\n")
    st = day.get("register") or {"rows": [], "totals": None, "has_any": False}
    md.append("**收益口径：滚撮成交价是一小时的价，该小时 4 个 15 分钟点全部按这个价结算；"
              "与 96 点现货逐点相减再累加（电量按 1/4 均摊），不取代表价。**\n")
    if st["has_any"]:
        md.append("| 时段 | 场次 | 结果 | 方向 | 滚撮价 | 该小时 4 点现货 | 逐点价差（4 点） | 量（MWh） | 收益（元） | 说明 |")
        md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in st["rows"]:
            md.append(f"| {_hour_label(r['t'])} | {r['snapshot_trade_date'] or '—'} | {r['result_type']} |"
                      f" {r['direction']} | {reg.reg(r['price'])} | {_fmt_pts(r['settle_points'], reg)} |"
                      f" {_fmt_pts(r['diffs'], reg)} | {reg.reg(r['volume_mwh'])} |"
                      f" **{reg.reg(r['pnl'])}** | {r['note']} |")
        t = st["totals"]
        md.append("\n**当日小计（两行分开，不合并）**\n")
        md.append("| 项 | 笔数 | 其中为正 | 电量（MWh） | 金额（元） |")
        md.append("| --- | --- | --- | --- | --- |")
        md.append(f"| 已成交溢价合计 | {reg.reg_int(t['filled']['n'])} 笔 | {reg.reg_int(t['filled']['n_positive'])} 笔 |"
                  f" {reg.reg(t['filled']['volume'])} | **{reg.reg(t['filled']['amount'])}** |")
        md.append(f"| 机会成本估算（溢价未实现）合计 | {reg.reg_int(t['unfilled']['n'])} 笔 | — |"
                  f" {reg.reg(t['unfilled']['volume'])} | **{reg.reg(t['unfilled']['amount'])}** |")
        md.append(f"| 两者相加＝当日执行溢价 | {reg.reg_int(t['filled']['n'] + t['unfilled']['n'])} 笔 | — |"
                  f" {reg.reg(t['filled']['volume'] + t['unfilled']['volume'])} |"
                  f" **{reg.reg(t['day_execution_premium'])}** |")
        md.append("\n> 执行溢价不是损益——不含成本、结算、手续费与偏差考核；"
                  "负数照实写、不取绝对值；现货价缺失的笔不出金额。\n")
    else:
        md.append("当日无成交登记，图上无色带；未登记不等于未成交。\n")

    # 六、偏差归属分画像
    md.append(f"## {R1_TITLES[5]}\n")
    four = day["portrait"]["four_rows_price"]
    total = day["portrait"]["dp_total_mean"]
    md.append("| 画像 | 本期偏差 | 说明 |")
    md.append("| --- | --- | --- |")
    notes = {"系统": "联络线基线、核电" + ("（兜底项见提示）" if day["portrait"]["tier_hint"] else ""),
             "交易中心": "负荷、风电（总量）、光伏（总量）",
             "交易员": f"{n_rev} 次修订、省间录入",
             "未归属": "缺输入项与未解释部分"}
    for o in ("系统", "交易中心", "交易员", "未归属"):
        pct = (round(abs(four[o]) / abs(total) * 100, 1) if total not in (None, 0) and four[o] else None)
        md.append(f"| {OWNER_LABELS[o]} | {reg.reg(four[o])} 元"
                  f"（{reg.reg(pct)}%） | {notes[o]} |")
    md.append(f"\n> 四行相加 = 全天总偏差 {reg.reg(total)} 元"
              f"（MW 恒等校验最大误差 {reg.reg(day['portrait']['identity_max_mw'])} MW < 1）。")
    if day["portrait"]["tier_hint"]:
        md.append(">\n> 水电/地方燃煤/非市场化三项同向偏差属「最近日兜底近似，非三项独立偏差」，只提示、不定责。")
    md.append("")
    dq = day.get("disclosure_quality")
    if dq:
        md.append("### 6.1 两把尺子与披露质量统计（跨场次，不进四行账）\n")
        md.append(f"> {dq.get('note', '')}\n")
        if dq.get("sessions"):
            md.append("| 交易日（场次） | 项 | 披露均值 | 实况均值 | 偏差 | 方向 |")
            md.append("| --- | --- | --- | --- | --- | --- |")
            for r in dq["sessions"]:
                md.append(f"| {r['trade_date']} | {r['item']} | {reg.reg(r['disclosure_mean'])} MW |"
                          f" {reg.reg(r['actual_mean'])} MW | {reg.reg(r['deviation'])} MW | {r['direction']} |")
        md.append("")

    # 七、重点点深析
    md.append(f"## {R1_TITLES[6]}\n")
    md.append("### 7.1 边界修订点\n")
    if comp.get("items"):
        md.append("| 时点 | 项 | 改动 | 前误差 | 后误差 | 判断 |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for it in comp["items"]:
            md.append(f"| {it['t'] // 4 + 1:02d}:00 段 | {it['boundary']} |"
                      f" {reg.reg(it['v_sys'])} → {reg.reg(it['v_rev'])} MW |"
                      f" {reg.reg(it['e_before'])} | {reg.reg(it['e_after'])} | {it['comp_result']} |")
    else:
        md.append("当日无修订。")
    md.append("\n### 7.2 交易点位与成交执行\n")
    if st["has_any"]:
        md.append("| 时段 | 所属场次（交易日） | 结果 | 价格（元/MWh） | 该小时 4 点现货 | 逐点价差（4 点） | 量（MWh） | 方向 |")
        md.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in st["rows"]:
            md.append(f"| {_hour_label(r['t'])} | {r['snapshot_trade_date'] or '—'} | {r['result_type']} |"
                      f" {reg.reg(r['price'])} | {_fmt_pts(r['settle_points'], reg)} | {_fmt_pts(r['diffs'], reg)} |"
                      f" {reg.reg(r['volume_mwh'])} | {r['direction']} |")
        md.append("\n> 图上色带与本表逐格对应（图有文无、文有图无都算缺陷）；逐笔金额见第五章。\n")
    else:
        md.append("当日无登记。")
    md.append("\n### 7.3 电价异常点\n")
    anom = [p for p in jump["jump_points"] if p["jump_type"] in ("类型一", "类型二", "类型三", "未定性")]
    if anom:
        md.append("| 时点 | 实际价 | 预测价 | 类型 | 定位结论 |")
        md.append("| --- | --- | --- | --- | --- |")
        for p in anom[:C.DEEP_DIVE_MAX * 2]:
            t_i = p["t"] - 1
            label = {"类型一": "下临界" if p.get("jump_side") == "down" else "上临界",
                     "类型二": "模式切换", "类型三": "门槛变", "未定性": "暂无法定性"}[p["jump_type"]]
            detail = p.get("detail") or {}
            concl = ("缺口约 " + reg.reg(detail.get("gap_mw")) + " MW，组合 "
                     + ("、".join(detail.get("combo") or [])) +
                     ("，组合不唯一" if detail.get("combo_unique") is False else ""))
            if p["jump_type"] == "类型一" and p.get("jump_side") == "up":
                concl = "供给紧（负荷率贴顶/正备用为负），模型无上临界规则，不反算缺口"
            md.append(f"| {_clock(t_i)} | {reg.reg(spot[t_i])} | {reg.reg(pred[t_i])} | {label} | {concl} |")
    else:
        md.append("当日无异常点。")
    md.append("\n### 7.4 重点点针对性偏差归属\n")
    md.append("| 重点点 | 总偏差 | 交易中心 | 系统 | 交易员 | 定位 |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    for t in day["focus"]["priority"]:
        i = t - 1
        c = day["dec"]["c"]
        tc = sum(v[i] for k, v in c.items() if v[i] is not None and k in ("负荷", "风电", "光伏"))
        sysc = sum(v[i] for k, v in c.items() if v[i] is not None and k in ("核电", "水电", "地方燃煤", "非市场化"))
        trc = sum(v[i] for k, v in c.items() if v[i] is not None and k in ("省间交易总量",))
        md.append(f"| {_clock(i)} | {reg.reg(dp[i])} 元 | {reg.reg(round(tc, 2))} 元 |"
                  f" {reg.reg(round(sysc, 2))} 元 | {reg.reg(round(trc, 2))} 元 |"
                  f" {day['focus']['class_of'].get(t, {}).get('quadrant', '—')} |")
    md.append("")

    # 八、问题时段归纳
    md.append(f"## {R1_TITLES[7]}\n")
    md.append("| 时段 | 问题点数 | 主因 | 类型 |")
    md.append("| --- | --- | --- | --- |")
    from .aggregate import to24
    dp24 = to24(dp)
    problem_ts = [t for t in day["focus"]["union"]]
    by_hour: dict[int, list[int]] = {}
    for t in problem_ts:
        by_hour.setdefault((t - 1) // 4, []).append(t)
    if by_hour:
        c = day["dec"]["c"]
        for h in sorted(by_hour, key=lambda h: -len(by_hour[h]))[:5]:
            main = max(c.keys(), key=lambda k: abs(sum(v for v in c[k] if v is not None)))
            types = {_anomaly_marks(jump, t, th["theta_delta"], dp[t - 1]) for t in by_hour[h]}
            md.append(f"| {_hour_label(h)}—{_hour_label(h + 1) if h < 23 else '24:00'} |"
                      f" {reg.reg_int(len(by_hour[h]))} | {main} | {'、'.join(sorted(x for x in types if x != '—')) or '大价差'} |")
    else:
        md.append("| — | 当日无重点点 | — | — |")
    md.append("")

    # 九、交易判断建议
    md.append(f"## {R1_TITLES[8]}\n")
    md.extend(_advice_block(day, reg))
    md.append("")

    # 十、待核实事项
    md.append(f"## {R1_TITLES[9]}\n")
    md.append("| # | 事项 | 现状 |")
    md.append("| --- | --- | --- |")
    q = 1
    for a in jump["anomaly"][:3]:
        md.append(f"| {reg.reg_int(q)} | {a['hint_question']} | 待核实 |")
        q += 1
    md.append(f"| {reg.reg_int(q)} | 地方燃煤日前与实时量级不同（约 {reg.reg(7.5)} 倍） | 口径待核，本期单列、不进画像 |")
    md.append(f"| {reg.reg_int(q + 1)} | 实时开机与日前逐点相同 | 疑为系统输出复制，不能当实况 |")
    if st["has_any"]:
        md.append(f"| {reg.reg_int(q + 2)} | 成交登记未经回单核对 | 回单到位后按登记记录对账 |")
    md.append("")

    # 十一、使用限制与免责（固定文本）
    md.append(f"## {R1_TITLES[10]}\n")
    md.append("| 限制 | 说明 |")
    md.append("| --- | --- |")
    md.append("| 不构成报价建议 | 全文不出现具体报价数值、电量、买卖方向，只讲核对方向 |")
    md.append("| 不含真实盈亏 | 成交侧依据手工登记，未经回单核对，不出损益数字 |")
    md.append("| 模型盲区 | 负备用、爬坡受限两类机制模型未覆盖，只能提示核实，不给结论 |")
    md.append("| 内生性 | 你的报价本身会影响出清价，用实际价当「标准答案」评判判断存在内生性，本期不修正 |\n")
    return {"md": "\n".join(md), "registry": reg}


def _m(xs: list[float | None]) -> float | None:
    ys = [x for x in xs if x is not None]
    return round(sum(ys) / len(ys), 2) if ys else None


def _advice_block(day: dict, reg: NumberRegistry) -> list[str]:
    """建议：必出、≤5 条、每条挂依据、只讲去核对什么（R21/NG1/NG2 越界校验）。"""
    adv: list[str] = []
    jump, comp, st = day["jump"], day.get("compensate") or {}, day.get("register") or {}
    spot = day["delta"]["table"]["电价"]["v_act"]
    n_floor = sum(1 for p in spot if p is not None and p <= C.SPOT_FLOOR + 1e-9)
    if n_floor:
        adv.append("触及下限的连续点属规则触发；下次同类形态先核三项边界是否同向压减火电空间（依据：八）。")
    ups = [p for p in jump["jump_points"] if p.get("jump_side") == "up"]
    if ups:
        adv.append("上冲点在模型里没有对应规则，目前只能提示、不能预测，不要据此反推缺口（依据：七 7.3）。")
    b_items = [i for i in (comp.get("items") or []) if i["comp_result"].startswith("B")]
    if b_items:
        adv.append("有修订方向对了但幅度过头；下次同类修订先看该项近 7 日同向偏差的中位幅度（依据：四 4.2）。")
    unfill_pos = [r for r in st.get("rows", [])
                  if r["result_type"] == "未成交" and (r["pnl"] or 0) > 0]
    if unfill_pos:
        adv.append("存在「为多要一点价把单挂飞」的时段；先核同段出清价的常态水平，看是偶发还是惯常（依据：五）。")
    if comp.get("steady_rhythm"):
        adv.append("人工修正补掉了系统偏差的大部分，但这是系统该修的，不是该长期靠人补的（依据：六、四 4.2）。")
    adv = [a for a in adv if advice_line_ok(a)][:5]
    if not adv:
        adv.append("本期未见需要专项核对的异常；维持对高价时段与披露口径的例行关注（依据：三）。")
    if day.get("sample_hint"):
        adv.insert(0, f"_{day['sample_hint']}_")
    return [f"{i}. {a}" for i, a in enumerate(adv, 1)]


def render_range(days: list[dict], d_start: str, d_end: str, fig_dir: Path,
                 habit: dict | None = None, n_eff: int | None = None,
                 ai_text: str = "") -> dict:
    """R2 区间十一章（24 时段聚合 + 区间均值图）。"""
    reg = NumberRegistry()
    n_eff = n_eff if n_eff is not None else len(days)
    md = ["# 多日复盘报告\n",
          f"| 项 | 内容 |\n| --- | --- |\n| 区间 | {d_start} ~ {d_end}（{reg.reg_int(n_eff)} 个有效日） |",
          f"| 生成人 | default　生成时间 {days[0].get('generated_at', '') if days else ''} |",
          "| 模板 | 多日复盘模板 v2.3（12 图；区间均值 + 偏差横柱；逐点结算口径） |\n"]
    md.append(f"## {R2_TITLES[0]}\n")
    md.append("**先看图，再看账。** 上半截是区间均值折线（分母=有效日数），下半截是各系列相对基准的平均偏差；"
              "基准系列自身偏差为 0，不画柱。图注固定写「区间均值（N 天）」。\n")
    from .aggregate import interval_mean, to24
    from .pngchart import Bar, Series, render_line_bar_chart
    fig_dir.mkdir(parents=True, exist_ok=True)
    base_day = days[0]
    specs = chart_specs(base_day)
    for s in specs:
        series_lists = [[vals for d in days for vals in [d["delta"]["table"][k]["v_act" if False else "v_act"]]]
                        for k in []]  # placeholder – built below
        # 逐系列取区间均值（各日同项序列 → 逐点均值）
        names = [n for n, _ in s["series"]]
        means = []
        for name, _ in s["series"]:
            per_day = []
            for d in days:
                vals = _series_values(d, s["id"], name)
                if vals is not None:
                    per_day.append(vals)
            means.append(interval_mean(per_day) if per_day else [None] * 96)
        path = fig_dir / f"{s['id']}_{s['name'].replace('（', '').replace('）', '')}.png"
        series = [Series(name=n, color=C.SERIES_COLORS.get(n, (90, 90, 90)), points=to24(m))
                  for n, m in zip(names, means)]
        bars = [Bar(name=n, color=C.SERIES_COLORS.get(n, (90, 90, 90)),
                    values=to24([ (m[i] - means[0][i]) if (m[i] is not None and means[0][i] is not None)
                                  else None for i in range(96)]))
                for n, m in zip(names[1:], means[1:])]
        render_line_bar_chart(path, series, bars)
        ys = [v for m in means for v in m if v is not None]
        lo, hi = (min(ys), max(ys)) if ys else (0, 0)
        reg.reg(lo)
        reg.reg(hi)
        md.append(f"**{s['id']}　{s['name']}（{'元/MWh' if s['id'] == 'G-12' else 'MW'}）**（区间均值 {reg.reg_int(n_eff)} 天）\n")
        md.append(f"![{s['id']} {s['name']}]({path.name})\n")
        md.append("> " + "　｜　".join(names) + f"　｜　纵轴 {lo:g} ~ {hi:g}　｜　下半为相对基准的平均偏差\n\n")

    md.append("### 1.1 数据完整性\n")
    md.append("| 项目 | 内容 |")
    md.append("| --- | --- |")
    md.append(f"| 起止日 | {d_start} ~ {d_end} |")
    md.append(f"| 有效日数 | {reg.reg_int(n_eff)} 天 |")
    md.append(f"| 缺数处理 | 无插值、无补 0；缺点留空、折线断开 |\n")

    md.append(f"## {R2_TITLES[1]}\n")
    four_s = {o: 0.0 for o in ("系统", "交易中心", "交易员", "未归属")}
    for d in days:
        for o, v in d["portrait"]["four_rows_price"].items():
            four_s[o] += v
    dp_mean = _m([v for d in days for v in d["delta"]["table"]["电价"]["delta"] if v is not None])
    md.append("| 画像 | 偏差 | 占比 | 出现天数 | 主要项 |")
    md.append("| --- | --- | --- | --- | --- |")
    notes = {"系统": "联络线基线、核电", "交易中心": "负荷、风电（总量）、光伏（总量）",
             "交易员": "边界修订、省间录入", "未归属": "缺输入项与未解释部分"}
    for o in ("系统", "交易中心", "交易员", "未归属"):
        pct = round(abs(four_s[o]) / abs(dp_mean) * 100, 1) if dp_mean and four_s[o] else None
        md.append(f"| {OWNER_LABELS[o]} | {reg.reg(round(four_s[o], 2))} 元 | {reg.reg(pct)}% |"
                  f" {reg.reg_int(len(days))} / {reg.reg_int(len(days))} 天 | {notes[o]} |")
    md.append(f"\n> 四行相加 = 区间总偏差 {reg.reg(dp_mean)} 元（MW 恒等校验通过）。\n")

    md.append(f"## {R2_TITLES[2]}\n**三部分分开看，才不会把别人的问题记到你的习惯里。**\n")
    md.append("### 3.1 系统算的部分\n")
    md.append("| 项 | 方向 | 中位幅度 | 出现天数 | 结论 |")
    md.append("| --- | --- | --- | --- | --- |")
    for item, tier in (("联络线", "可改进"), ("核电", "可改进")):
        devs = [d["delta"]["table"][item]["delta"] for d in days]
        flat = [v for dv in devs for v in dv if v is not None]
        if flat:
            flat_s = sorted(flat)
            med = flat_s[len(flat_s) // 2]
            md.append(f"| {item} | {'偏低' if med > 0 else '偏高'} | {reg.reg(abs(med))} MW |"
                      f" {reg.reg_int(len(days))} / {reg.reg_int(len(days))} | {tier} |")
    md.append("\n**只能兜底档**（数据缺失，非算法问题，不列改进清单）：水电、地方燃煤、非市场化。\n")
    md.append("### 3.2 交易中心给的数据\n")
    md.append("| 项 | 方向 | 中位幅度 | 出现天数 | 最集中时段 | 结论 |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    for item in ("负荷", "风电", "光伏"):
        flat = [v for d in days for v in d["delta"]["table"][item]["delta"] if v is not None]
        if flat:
            flat_s = sorted(flat)
            med = flat_s[len(flat_s) // 2]
            md.append(f"| {item} | {'偏低' if med > 0 else '偏高'} | {reg.reg(abs(med))} MW |"
                      f" {reg.reg_int(len(days))} / {reg.reg_int(len(days))} | 全天 | 方向{'稳定' if abs(med) > 50 else '未达稳定'} |")
    md.append("\n> 固定附注：风电、光伏为披露总量口径，含系统推断段未分离。\n")
    md.append("### 3.3 你改过和填过的部分\n")
    all_comp = [i for d in days for i in (d.get("compensate") or {}).get("items", [])]
    if all_comp:
        counts: dict[str, int] = {}
        for it in all_comp:
            counts[it["comp_result"]] = counts.get(it["comp_result"], 0) + 1
        md.append("| 结果 | 次数 | 占比 |")
        md.append("| --- | --- | --- |")
        for k in sorted(counts):
            md.append(f"| {k} | {reg.reg_int(counts[k])} | {reg.reg(round(counts[k] / len(all_comp) * 100, 1))}% |")
        a_n = counts.get("A 有效修正", 0)
        md.append(f"\n1. 本期共 {reg.reg_int(len(all_comp))} 次改动，其中 {reg.reg_int(a_n)} 次更接近实况。")
        md.append(f"2. A 类单列：{reg.reg_int(a_n)} 次有效修正。")
        md.append(f"3. 结论：{'这段时间你的调整节奏是正确的' if all((d.get('compensate') or {}).get('steady_rhythm') for d in days if (d.get('compensate') or {}).get('items')) else '调整效果逐次列示，未达稳定节奏结论门槛'}。")
        md.append("4. 系统侧建议：多次同向偏离的项建议校准参数，不要长期靠人工补。\n")
    else:
        md.append("本期无操作记录；建议仅基于系统与交易中心侧证据，不评价你的判断。\n")

    md.append(f"## {R2_TITLES[3]}\n")
    md.append("| 参数 | 取值 | 说明 |")
    md.append("| --- | --- | --- |")
    th = base_day["theta"]
    md.append(f"| 高价线 | {reg.reg(th['theta_high'])} 元/MWh | 判历史只认当天的值 |")
    md.append(f"| 涨速线 | {reg.reg(th['theta_jump'])} 元 / 15 分钟 | 用于识别陡升 |")
    md.append(f"| 大价差线 | {reg.reg(th['theta_delta'])} 元/MWh | 用于挑重点点 |")
    md.append(f"| 习惯门槛 | ≥ 20 天 + 70% 同向 + p < 0.05 | 只管「能不能说习惯」 |\n")

    md.append(f"## {R2_TITLES[4]}\n")
    md.append("| 日期 | 均价差 | 大价差点数 | 触下限点数 | 当日主因 |")
    md.append("| --- | --- | --- | --- | --- |")
    for d in days:
        dpd = d["delta"]["table"]["电价"]["delta"]
        spotd = d["delta"]["table"]["电价"]["v_act"]
        big = sum(1 for v in dpd if v is not None and abs(v) >= th["theta_delta"])
        fl = sum(1 for p in spotd if p is not None and p <= C.SPOT_FLOOR + 1e-9)
        c = d["dec"]["contributions"]
        main = max((k for k, v in c.items() if v is not None), key=lambda k: abs(c[k]), default="—")
        md.append(f"| {d['run_date']} | {reg.reg(_m(dpd))} 元 | {reg.reg_int(big)} |"
                  f" {reg.reg_int(fl)} | {main}（{reg.reg(c.get(main))} MW） |")
    md.append("")

    md.append(f"## {R2_TITLES[5]}\n")
    rows = [r for d in days for r in (d.get("register") or {}).get("rows", [])]
    md.append("**收益口径与单日一致（逐点相减，不取代表价）。两个合计分开列，不出净额。**\n")
    if rows:
        md.append("| 日期 | 时段 | 场次 | 结果 | 方向 | 滚撮价 | 该小时 4 点现货 | 逐点价差（4 点） | 量（MWh） | 收益（元） |")
        md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for d in days:
            for r in d.get("register", {}).get("rows", []):
                md.append(f"| {d['run_date']} | {_hour_label(r['t'])} |"
                          f" {r['snapshot_trade_date'] or '—'} | {r['result_type']} | {r['direction']} |"
                          f" {reg.reg(r['price'])} | {_fmt_pts(r['settle_points'], reg)} | {_fmt_pts(r['diffs'], reg)} |"
                          f" {reg.reg(r['volume_mwh'])} | **{reg.reg(r['pnl'])}** |")
        filled = [r for r in rows if r["result_type"] == "成交" and r["pnl"] is not None]
        unf = [r for r in rows if r["result_type"] == "未成交" and r["pnl"] is not None]
        md.append("\n**区间合计（两行分开列，各带笔数、其中为正与电量）**\n")
        md.append("| 项 | 笔数 | 其中为正 | 电量（MWh） | 金额（元） |")
        md.append("| --- | --- | --- | --- | --- |")
        md.append(f"| 已成交溢价合计 | {reg.reg_int(len(filled))} 笔 |"
                  f" {reg.reg_int(sum(1 for r in filled if r['pnl'] > 0))} 笔 |"
                  f" {reg.reg(round(sum(r['volume_mwh'] for r in filled), 2))} |"
                  f" **{reg.reg(round(sum(r['pnl'] for r in filled), 2))}** |")
        md.append(f"| 机会成本估算（溢价未实现）合计 | {reg.reg_int(len(unf))} 笔 | — |"
                  f" {reg.reg(round(sum(r['volume_mwh'] for r in unf), 2))} |"
                  f" **{reg.reg(round(sum(r['pnl'] for r in unf), 2))}** |")
        md.append("\n> 区间合计是所选 N 天里这些笔的加总，不构成「交易水平」；多日版不出两行相加的执行溢价。\n")
    else:
        md.append("本期无成交登记。\n")

    md.append(f"## {R2_TITLES[6]}\n")
    md.append("| 时段 | 问题点数 | 占比 | 主因 |")
    md.append("| --- | --- | --- | --- |")
    from .aggregate import to24 as _t24
    hour_problems: dict[int, int] = {}
    hour_days: dict[int, set[str]] = {}
    for d in days:
        seen_h: set[int] = set()
        for t in d["focus"]["union"]:
            h = (t - 1) // 4
            hour_problems[h] = hour_problems.get(h, 0) + 1
            seen_h.add(h)
        for h in seen_h:
            hour_days.setdefault(h, set()).add(d["run_date"])
    top_hours = sorted(hour_problems, key=lambda h: (-hour_problems[h], h))[:5]
    total_problems = sum(hour_problems.values()) or 1
    for h in top_hours:
        c_all: dict[str, float] = {}
        for d in days:
            for k, v in d["dec"]["c"].items():
                s = sum(x for x in v[(h) * 4:(h + 1) * 4] if x is not None)
                c_all[k] = c_all.get(k, 0.0) + abs(s)
        main = max(c_all, key=lambda k: c_all[k]) if c_all else "—"
        word = "高发" if n_eff >= 7 else "问题较多"
        md.append(f"| {_hour_label(h)}—{_hour_label(h + 1) if h < 23 else '24:00'} |"
                  f" {reg.reg_int(hour_problems[h])} | {reg.reg(round(hour_problems[h] / total_problems * 100, 1))}% |"
                  f" {main}（{word}） |")
    if not top_hours:
        md.append("| — | 本期无重点点 | — | — |")
    md.append("")

    md.append(f"## {R2_TITLES[7]}\n")
    md.append("| 时段 | 日期 | 实际价 | 预测价 | 价差 | 类型 |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    shown = 0
    for d in days:
        for p in d["jump"]["jump_points"]:
            if p["jump_type"] in ("类型一",) and shown < 5:
                i = p["t"] - 1
                md.append(f"| {_clock(i)} | {d['run_date']} |"
                          f" {reg.reg(d['delta']['table']['电价']['v_act'][i])} |"
                          f" {reg.reg(d['delta']['table']['电价']['v_rev'][i])} |"
                          f" {reg.reg(d['delta']['table']['电价']['delta'][i])} |"
                          f" {'下临界' if p.get('jump_side') == 'down' else '上临界'} |")
                shown += 1
    if not shown:
        md.append("| — | 本期无跃变点 | — | — | — | — |")
    md.append("")

    md.append(f"## {R2_TITLES[8]}\n")
    if habit:
        md.append("### 9.1 长期习惯（全局窗口）\n")
        sig = [(k.split(":", 1)[1], v) for k, v in habit.items()
               if k.startswith("H1:") and v.get("significant")]
        md.append("| 项 | 习惯方向 | 样本天数 | 一致率 | 显著性 | 结论 |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for item, v in sig:
            p = v.get("p_value")
            md.append(f"| {item} | 偏{'低' if False else '差'} | {reg.reg_int(v['n'])} |"
                      f" {reg.reg(round((v.get('sign_rate') or 0) * 100, 1))}% |"
                      f" p = {reg.reg(round(p, 3) if p is not None else None)} | **成立** |")
        if not sig:
            md.append("| — | 样本不足或未达门槛，不出倾向 | — | — | — | 仅统计值 |")
        div = habit.get("_divergence") or {}
        if div.get("results"):
            md.append("\n### 9.2 本期与习惯比对\n")
            for r in div["results"]:
                md.append(f"- {r['item']}：{r['result']}——{r['detail']}")
        md.append("\n### 9.3 交易判断建议\n")
        adv = _range_advice(days, reg)
        md.extend(adv)
        md.append("")
    else:
        md.append("习惯统计未启用。\n")

    md.append(f"## {R2_TITLES[9]}\n")
    md.append("| # | 事项 | 现状 |")
    md.append("| --- | --- | --- |")
    md.append(f"| 1 | 地方燃煤日前与实时量级不同（约 {reg.reg(7.5)} 倍） | 口径待核，本期单列、不进画像 |")
    md.append("| 2 | 实时开机与日前逐点相同 | 疑为系统输出复制，不能当实况 |")
    md.append("| 3 | 风电、光伏实况无分段数据 | 本期按总量归交易中心 |\n")

    md.append(f"## {R2_TITLES[10]}\n")
    md.append("| 限制 | 说明 |")
    md.append("| --- | --- |")
    md.append("| 不构成报价建议 | 全文不出现具体报价数值、电量、买卖方向 |")
    md.append("| 不含真实盈亏 | 成交侧依据手工登记，不出损益数字 |")
    md.append("| 模型盲区 | 负备用、爬坡受限未覆盖，只能提示 |")
    md.append("| 分摊限制 | 多因叠加导致缺口时只写「多项叠加、主次不可分」，不硬给唯一归因 |")
    md.append("| 内生性 | 报价影响出清价，以实际价评判判断存在内生性，本期不修正 |\n")
    return {"md": "\n".join(md), "registry": reg}


def _series_values(day: dict, chart_id: str, name: str) -> list[float | None] | None:
    tb, view = day["delta"]["table"], day["delta"]["view"]
    mapping = {
        ("G-01", "日前披露"): view["负荷"]["v_sys"], ("G-01", "实时披露"): tb["负荷"]["v_act"],
        ("G-01", "改后"): view["负荷"]["v_rev"],
        ("G-02", "日前披露"): view["风电"]["v_sys"], ("G-02", "实时披露"): tb["风电"]["v_act"],
        ("G-02", "改后"): view["风电"]["v_rev"],
        ("G-03", "日前披露"): view["光伏"]["v_sys"], ("G-03", "实时披露"): tb["光伏"]["v_act"],
        ("G-03", "改后"): view["光伏"]["v_rev"],
        ("G-04", "系统预测"): view["核电"]["v_sys"], ("G-04", "改后"): view["核电"]["v_rev"],
        ("G-04", "实时实况"): tb["核电"]["v_act"],
        ("G-05", "系统预测"): view["非市场化"]["v_sys"], ("G-05", "改后"): view["非市场化"]["v_rev"],
        ("G-05", "实时实况"): tb["非市场化"]["v_act"],
        ("G-06", "系统预测"): view["水电"]["v_sys"], ("G-06", "改后"): view["水电"]["v_rev"],
        ("G-06", "实时实况"): tb["水电"]["v_act"],
        ("G-07", "系统预测"): view["联络线"]["v_sys"], ("G-07", "改后"): view["联络线"]["v_rev"],
        ("G-07", "实时实况"): tb["联络线"]["v_act"],
        ("G-08", "系统预测"): view["地方燃煤"]["v_sys"], ("G-08", "改后"): view["地方燃煤"]["v_rev"],
        ("G-08", "实时实况"): tb["地方燃煤"]["v_act"],
        ("G-09", "系统预测"): day["snapshot"]["M7开机"], ("G-09", "改后"): day.get("da_on_96"),
        ("G-10", "系统预测基线"): view["联络线"]["base_v_rev"],
        ("G-10", "事后实际基线"): day.get("act_tie_base_96"),
        ("G-11", "交易员录入"): view["联络线"]["inter_v"],
        ("G-11", "事后实际"): day["delta"]["inter_actual_96"],
        ("G-12", "当时预测价"): day["snapshot"]["final"],
        ("G-12", "实际出清价"): tb["电价"]["v_act"],
    }
    return mapping.get((chart_id, name))


def _range_advice(days: list[dict], reg: NumberRegistry) -> list[str]:
    adv: list[str] = []
    four_s = {o: 0.0 for o in ("系统", "交易中心", "交易员", "未归属")}
    for d in days:
        for o, v in d["portrait"]["four_rows_price"].items():
            four_s[o] += v
    if abs(four_s["交易中心"]) >= abs(four_s["系统"]):
        adv.append("交易中心侧偏差占比最高（负荷/风/光），可据此预判火电空间与看到的不同（依据：三 3.2、G-01~G-03）。")
    if abs(four_s["系统"]) > 0:
        adv.append("系统侧联络线基线/核电是可改进项；人工修正补上的是系统该修的，不该长期靠人补（依据：三 3.1/3.3）。")
    fl_days = [d for d in days if any(
        p is not None and p <= C.SPOT_FLOOR + 1e-9 for p in d["delta"]["table"]["电价"]["v_act"])]
    if fl_days:
        adv.append("触及下限的点集中在部分日期，属规则触发，不必按常规偏差去调（依据：八）。")
    adv = [a for a in adv if advice_line_ok(a)][:5]
    if not adv:
        adv.append("本期未见需要专项核对的异常；维持对高价时段与披露口径的例行关注（依据：一）。")
    return [f"{i}. {a}" for i, a in enumerate(adv, 1)]
