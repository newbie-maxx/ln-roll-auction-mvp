"""M-04 偏差引擎（D-A1）：Δ = 实况 − 当时预测；符号统一（Δ>0 = 实况高于预测）；
缺数标缺不插值；修订点双值 V_sys/V_rev；预测来源标记（快照/重放/修订）。"""
from __future__ import annotations

import copy

from ..loaders.xlsx_loader import apply_run_day_policy, expand24to96, resolve_a_day
from . import constants as C
from ._pipeline_util import base_data
from .actual import actual_day
from .snapshot import get_or_replay
from .store import RetroStore

# Δ 表口径项：8 类边界 + 省间交易总量（交易员项，联络线拆段用）+ 电价
DELTA_ITEMS = C.BOUNDARIES8 + ("省间交易总量", "电价")


def boundary_view(run_date: str, real_store) -> dict:
    """D 日生效边界视图：{item: {v_sys, v_rev, kinds, revised(96 逐点 bool)}}。

    v_sys = 日前原值（运行日政策后：不披露项最近日法、核电 min——与链内口径一致）；
    v_rev = 修订合并后的采用值（读时合并，未改 = v_sys）。
    联络线特殊：v_sys/v_rev 是「预测联络线 = 基线生效值 + 省间生效值」逐点（M7 实际输入口径），
    另附 base_v_sys/base_v_rev（基线两段）与 inter_v（省间段）供 D-A10 拆段。
    """
    data = copy.deepcopy(base_data())
    a_day = resolve_a_day(data, run_date)
    apply_run_day_policy(data, run_date, a_day)
    da = data.day_ahead[run_date]
    out: dict[str, dict] = {}
    for item in C.BOUNDARIES8:
        s = da.get(item)
        if s is None:
            out[item] = {"v_sys": [None] * 96, "v_rev": [None] * 96,
                         "kinds": ["缺输入"] * 96, "revised": [False] * 96}
            continue
        v_sys, v_rev, rev = list(s.values), list(s.values), [False] * 96
        for i in range(96):
            eff = real_store.effective_value(run_date, item, i + 1)
            if eff is not None:
                v_rev[i] = eff
                rev[i] = True
        out[item] = {"v_sys": v_sys, "v_rev": v_rev, "kinds": list(s.kinds), "revised": rev}

    # 联络线：M7 实际输入 = 基线(日前联络线 sheet) + 省间交易总量（实时口径，正=受入）
    inter = da.get("省间交易总量")
    inter_sys = list(inter.values) if inter is not None else [0.0] * 96
    inter_rev, inter_revised = list(inter_sys), [False] * 96
    for i in range(96):
        eff = real_store.effective_value(run_date, "省间交易总量", i + 1)
        if eff is not None:
            inter_rev[i] = eff
            inter_revised[i] = True
    tie = out["联络线"]
    out["联络线"] = {
        "v_sys": [a + b if a is not None and b is not None else None
                  for a, b in zip(tie["v_sys"], inter_sys)],
        "v_rev": [a + b if a is not None and b is not None else None
                  for a, b in zip(tie["v_rev"], inter_rev)],
        "kinds": tie["kinds"], "revised": tie["revised"],
        "base_v_sys": tie["v_sys"], "base_v_rev": tie["v_rev"],
        "inter_v": inter_rev, "inter_v_sys": inter_sys, "inter_revised": inter_revised}
    out["省间交易总量"] = {
        "v_sys": inter_sys, "v_rev": inter_rev,
        "kinds": ["意向"] * 96, "revised": inter_revised}
    out["_a_day"] = a_day
    return out


def roll_actual_24(run_date: str) -> list[float | None] | None:
    """实况省间（滚撮统计实际成交量，24 点）。"""
    ra = base_data().roll_auction.get(run_date) or {}
    return ra.get("volume24")


def compute(run_date: str, store: RetroStore, real_store) -> dict:
    """生成并落 D-A1；返回逐点偏差（items × 96）+ 概况。"""
    act = actual_day(run_date)
    view = boundary_view(run_date, real_store)
    snap = get_or_replay(run_date, store, items=("final",))
    final = snap["final"]["values"] if snap.get("final") else [None] * 96
    pred_source = ("重放值，非当时值" if snap["_meta"]["is_replay"]
                   else f"快照 trade_date={snap['_meta']['trade_date']}")
    inter_act24 = roll_actual_24(run_date)
    inter_act96 = expand24to96(inter_act24) if inter_act24 else [None] * 96

    table: dict[str, dict] = {}
    for item in DELTA_ITEMS:
        if item == "电价":
            v_act, v_rev, kinds = act["实时电价"]["values"], final, ["计算"] * 96
        elif item == "省间交易总量":
            v_act, v_rev, kinds = inter_act96, view[item]["v_rev"], ["意向"] * 96
        else:
            v_act, v_rev = act[item]["values"], view[item]["v_rev"]
            kinds = view[item]["kinds"]
        deltas = [None if (a is None or r is None) else round(a - r, 4)
                  for a, r in zip(v_act, v_rev)]
        table[item] = {"v_act": v_act, "v_rev": v_rev, "v_sys": view.get(item, {}).get("v_sys"),
                       "kinds": kinds, "delta": deltas,
                       "missing": [a is None or r is None for a, r in zip(v_act, v_rev)]}

    batch = (store.conn.execute(
        "SELECT MAX(version) AS v FROM retro_deviation WHERE run_date = ?",
        (run_date,)).fetchone()["v"] or 0) + 1
    rows: list[dict] = []
    for item in DELTA_ITEMS:
        tb = table[item]
        for i in range(96):
            revised_here = (tb["v_sys"] is not None and tb["v_sys"][i] is not None
                            and tb["v_rev"][i] is not None
                            and tb["v_rev"][i] != tb["v_sys"][i])
            rows.append({
                "run_date": run_date, "item": item, "t": i + 1,
                "v_pred": tb["v_rev"][i],
                "pred_source": pred_source if item == "电价" else ("修订" if revised_here else None),
                "v_sys": tb["v_sys"][i] if tb["v_sys"] else None,
                "v_rev": tb["v_rev"][i], "v_act": tb["v_act"][i], "delta": tb["delta"][i],
                "missing": int(tb["missing"][i]), "trader_id": "default", "version": batch})
    store.append_batch("retro_deviation", rows)
    return {"run_date": run_date, "table": table, "pred_source": pred_source,
            "is_replay": snap["_meta"]["is_replay"], "batch": batch,
            "view": view, "inter_actual_96": inter_act96}
