"""M-10 三画像分流与补偿：四行分账（按层不按净值）、联络线拆两段、风/光整体归交易中心、
修正有效性 A~E 五类（CA1–CA3 才写「节奏正确」且必须同段给系统侧建议）、两把尺子（数责分离）。

口径：owner_type 只由 value_type 推导（constants.VALUE_TYPE_OWNER，集中一处）；
(V_rev−V_sys)+(V_act−V_rev) = V_act−V_sys 逐点恒等（T-A20，<1MW）；
兜底三项同向只提示不定责；四行（元级）= 归属映射后的 Σc_b，未归属行 = 残差。
"""
from __future__ import annotations

from ..loaders.xlsx_loader import expand24to96
from . import constants as C
from .actual import actual_day
from .delta import roll_actual_24

OWNER_ROWS = ("系统", "交易中心", "交易员", "未归属")
# 因子 → 四行（元级）归属（联络线经基线/省间两段拆行；风/光不拆整体归交易中心）
FACTOR_OWNER = {"核电": "系统", "水电": "系统", "地方燃煤": "系统", "非市场化": "系统",
                "负荷": "交易中心", "风电": "交易中心", "光伏": "交易中心",
                "联络线基线": "系统", "省间交易总量": "交易员"}


def _mean(xs: list[float | None]) -> float | None:
    ys = [x for x in xs if x is not None]
    return round(sum(ys) / len(ys), 2) if ys else None


def split(run_date: str, delta_result: dict, dec: dict, real_store) -> dict:
    """逐点归属 → D-A10 行（append 由调用方持久化）+ 四行账（MW 恒等 + 元级）。"""
    view = delta_result["view"]
    act = actual_day(run_date)
    inter_act96 = delta_result["inter_actual_96"]
    rows: list[dict] = []
    tier_hint = False   # 兜底三项同向提示

    def owner_of_kind(kind: str) -> str:
        return C.VALUE_TYPE_OWNER.get(kind, "未归属")

    for item in C.BOUNDARIES8:
        tb = delta_result["table"].get(item)
        if tb is None:
            continue
        if item == "联络线":
            base = view["联络线"]
            act_base = [(a - b) if (a is not None and b is not None) else None
                        for a, b in zip(act["联络线"]["values"], inter_act96)]
            for split_part, boundary, (v_sys, v_rev, v_act), owner, tier in (
                    ("基线", "联络线",
                     (base["base_v_sys"], base["base_v_rev"], act_base), "系统", "可改进"),
                    ("省间", "省间交易总量",
                     (base["inter_v_sys"], base["inter_v"], inter_act96), "交易员", None)):
                for i in range(96):
                    rows.append(_row(run_date, i, boundary, v_sys[i], v_rev[i], v_act[i],
                                     owner, tier, split_part))
            continue
        for i in range(96):
            kind = view[item]["kinds"][i]
            rows.append(_row(run_date, i, item, tb["v_sys"][i], tb["v_rev"][i], tb["v_act"][i],
                             owner_of_kind(kind) if kind != "缺输入" else "未归属",
                             C.SYSTEM_TIER.get(item), "—"))

    # 兜底三项同向检测（只提示不定责）
    und = [delta_result["table"][k]["delta"] for k in C.UNDISCLOSED_KEYS
           if k in delta_result["table"]]
    if und:
        means = [_mean(d) for d in und]
        means = [m for m in means if m is not None]
        if len(means) >= 2 and (all(m < -50 for m in means) or all(m > 50 for m in means)):
            tier_hint = True

    # MW 恒等校验（T-A20）
    identity_max = 0.0
    for r in rows:
        if None not in (r["v_sys"], r["v_rev"], r["v_act"]):
            lhs = (r["v_rev"] - r["v_sys"]) + (r["v_act"] - r["v_rev"])
            identity_max = max(identity_max, abs(lhs - (r["v_act"] - r["v_sys"])))

    # 联络线两段之和 = 该项总偏差（逐点 |Σ两段(v_act−v_rev) − (实况联络线−预测联络线)| 最大）
    tie_rows = [r for r in rows if r["split_part"] in ("基线", "省间")]
    tie_sum = None
    if tie_rows:
        tie_tb = delta_result["table"]["联络线"]
        per_t: dict[int, float] = {}
        for r in tie_rows:
            if r["v_act"] is not None and r["v_rev"] is not None:
                per_t[r["t"]] = per_t.get(r["t"], 0.0) + (r["v_act"] - r["v_rev"])
        diffs = [abs(per_t.get(i + 1, 0.0) - (tie_tb["delta"][i] or 0.0))
                 for i in range(96) if tie_tb["delta"][i] is not None]
        tie_sum = round(max(diffs), 4) if diffs else 0.0

    # 四行（元级）：因子归属映射后的 Σc_b；未归属 = 残差；交易员加修订价格影响
    rows_price = {o: 0.0 for o in OWNER_ROWS}
    for f, contrib in dec["contributions"].items():
        owner = FACTOR_OWNER.get(f, "未归属")
        if contrib is not None:
            rows_price[owner] += contrib
    resid_mean = _mean(dec["residual"]) or 0.0
    rows_price["未归属"] += resid_mean
    dp_mean = _mean(dec["dp_total"])
    closure = None
    if dp_mean is not None:
        closure = round(sum(rows_price.values()) - dp_mean, 4)

    return {"run_date": run_date, "rows": rows, "identity_max_mw": round(identity_max, 4),
            "tie_split_max": tie_sum, "tier_hint": tier_hint,
            "four_rows_price": {o: round(v, 2) for o, v in rows_price.items()},
            "dp_total_mean": dp_mean, "four_row_closure": closure}


def _row(run_date, i, boundary, v_sys, v_rev, v_act, owner, tier, split_part) -> dict:
    return {"run_date": run_date, "t": i + 1, "boundary": boundary,
            "v_sys": v_sys, "v_rev": v_rev, "v_act": v_act,
            "owner_type": owner, "system_tier": tier, "split_part": split_part,
            "e_before": round(abs(v_sys - v_act), 3) if None not in (v_sys, v_act) else None,
            "e_after": round(abs(v_rev - v_act), 3) if None not in (v_rev, v_act) else None,
            "gain": (round(abs(v_sys - v_act) - abs(v_rev - v_act), 3)
                     if None not in (v_sys, v_rev, v_act) else None),
            "comp_result": None, "disclosure_induced": 0,
            "snapshot_trade_date": None, "version": 1}


# ---------------- 补偿五类 ----------------

def comp_threshold(boundary: str, run_date: str, window_days: int = 30) -> float:
    """显著门槛 = max(50 MW, 该项近 30 日 |Δ| 日均绝对值的 P50)。"""
    from .delta import boundary_view
    from ..store import Store
    xs: list[float] = []
    days = sorted(actual_days_before(run_date, window_days))
    real_store = Store()
    try:
        for d in days:
            v = boundary_view(d, real_store).get(boundary)
            if not v:
                continue
            ds = [abs(a - s) for a, s in zip(actual_day(d).get(boundary, {}).get("values", [None] * 96),
                                             v["v_sys"]) if a is not None and s is not None]
            if ds:
                xs.append(sum(ds) / len(ds))
    finally:
        real_store.close()
    if not xs:
        return C.COMP_GATE_MW
    xs.sort()
    p50 = xs[len(xs) // 2]
    return max(C.COMP_GATE_MW, p50)


def actual_days_before(run_date: str, n: int) -> list[str]:
    from ._pipeline_util import base_data
    return [d for d in base_data().days if d < run_date][-n:]


def compensate(run_date: str, delta_result: dict, revisions: list[dict],
               dec: dict | None = None) -> dict:
    """逐修订点 A~E 五类 + 双结论素材。revisions 来自 boundary_revision（当日）。"""
    view = delta_result["view"]
    act = actual_day(run_date)
    items: list[dict] = []
    thresholds: dict[str, float] = {}
    for rev in revisions or []:
        boundary = rev.get("boundary_type")
        if boundary not in C.BOUNDARIES8:
            continue
        base_key = "联络线" if boundary == "联络线" else boundary
        v = view.get(base_key)
        if not v:
            continue
        t = int(rev["t"])
        i = t - 1
        v_sys, v_rev = v["v_sys"][i], v["v_rev"][i]
        v_act = act[boundary]["values"][i] if boundary in act else None
        if boundary == "联络线":
            v_act = act["联络线"]["values"][i]
        if None in (v_sys, v_rev, v_act):
            continue
        e_b, e_a = abs(v_sys - v_act), abs(v_rev - v_act)
        g = e_b - e_a
        gate = thresholds.get(boundary) or comp_threshold(boundary, run_date)
        thresholds[boundary] = gate
        sys_bias = v_act - v_sys          # 系统偏差方向（>0 系统偏低）
        rev_dir = v_rev - v_sys           # 修订方向
        crossed = (v_rev - v_act) * (v_sys - v_act) < 0   # 误差方向翻转
        if abs(g) < gate:
            cls = "D 无影响"
        elif g > 0 and crossed:
            cls = "B 改过头"
        elif g > 0 and rev_dir * sys_bias < 0:
            cls = "A 有效修正"
        elif g < 0 and rev_dir * sys_bias > 0:
            cls = "E 放大误差"
        else:
            cls = "C 改反了"
        items.append({"run_date": run_date, "t": t, "boundary": boundary,
                      "v_sys": v_sys, "v_rev": v_rev, "v_act": v_act,
                      "e_before": round(e_b, 1), "e_after": round(e_a, 1),
                      "gain": round(g, 1), "gate": round(gate, 1),
                      "comp_result": cls,
                      "snapshot_trade_date": (rev.get("op_time") or "")[:10] or None,
                      "disclosure_induced": 0})
    counts: dict[str, int] = {}
    for it in items:
        counts[it["comp_result"]] = counts.get(it["comp_result"], 0) + 1
    a_items = [it for it in items if it["comp_result"].startswith("A")]

    # CA1–CA3（措辞门槛，非准入）
    by_key: dict[tuple, list] = {}
    for it in a_items:
        by_key.setdefault((it["boundary"], it["t"] // 4), []).append(it)
    ca1 = any(len(v) >= C.CA_STABLE_N for v in by_key.values())
    ratios = [round(it["gain"] / it["e_before"], 4) for it in a_items if it["e_before"] > 0]
    ratios.sort()
    med = ratios[len(ratios) // 2] if ratios else None
    ca3 = None
    if ratios:
        ex = sorted(ratios[:-1]) if len(ratios) > 1 else []
        ex_med = ex[len(ex) // 2] if ex else None
        ca3 = bool(med is not None and med >= C.CA_RATIO_MED
                   and (ex_med is None or ex_med >= C.CA_RATIO_EX_MAX))
    steady = bool(ca1 and (ca3 is True))
    return {"run_date": run_date, "items": items, "counts": counts,
            "a_items": a_items, "ca1": ca1, "ca3": ca3, "steady_rhythm": steady,
            "comp_ratio_median": med,
            "system_note": "该项系统值多次同向偏离，建议校准参数，而不是长期靠人工补"
            if a_items else None}


# ---------------- 两把尺子 + 披露质量 ----------------

def disclosure_induced_mark(items: list[dict], store) -> list[dict]:
    """R1：依据成交场次披露方向判对的调整 → 置 disclosure_induced，不进五类/习惯样本。

    场次披露值取该场次（trade_date）live 快照推导不出的输入侧未落档时，维持 0 标记——
    B2 范围内只有 D-A6 输出侧快照，输入侧场次披露属后续数据需求（设计文档已记录）。
    """
    for it in items:
        td = it.get("snapshot_trade_date")
        if not td:
            continue
        snap = store.snapshot_effective(it["run_date"], "M6联络线", source="live")
        if snap and snap.get("trade_date") == td:
            # 输入场次披露未落档：无法判定方向 → 不置位（诚实缺省，不猜）
            it["disclosure_induced"] = 0
    return items


def disclosure_quality(run_date: str, store) -> dict:
    """跨场次披露质量统计表（不进四行账）。场次输入侧披露未落档时显式说明。"""
    sessions = store.snapshot_sessions(run_date, source="live")
    if not sessions:
        return {"run_date": run_date, "sessions": [], "note": "该日无场次快照，披露质量统计未落档"}
    act_tie = actual_day(run_date)["联络线"]["values"]
    rows = []
    for td in sessions:
        snap = store.snapshot_effective(run_date, "M6联络线", source="live")
        if snap and snap.get("trade_date") == td:
            pred = snap["values"]
            devs = [a - p for a, p in zip(act_tie, pred) if a is not None and p is not None]
            rows.append({
                "trade_date": td, "item": "联络线（预测，M6 输出侧）",
                "disclosure_mean": _mean(pred), "actual_mean": _mean(act_tie),
                "deviation": _mean(devs),
                "direction": "披露偏低" if devs and sum(devs) > 0 else "披露偏高" if devs else "—",
                "vs_prev": "—"})
    return {"run_date": run_date, "sessions": rows,
            "note": "负荷/风电/光伏的场次输入侧披露未落档，本期仅联络线（输出侧）可统计"}
