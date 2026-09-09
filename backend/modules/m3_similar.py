"""M3 相近样本组检索（PRD §6.1 M3，v1.4 A 维度算式）。

A(t) = 省调负荷 − 新能源出力 − 核电出力 − 水电出力 − 非市场化出力 − 地方燃煤出力
     （= 火电竞价空间 + 联络线；核电采用 M1 预测 min 口径；六项独立不重叠，地方燃煤必减）。

逐 96 点检索；默认范围 = 上一周（m3_scope_days），N<3 自动放宽至全部历史日并标注"已放宽+统计范围"；
N≥3 给分布（中位/P25/P75）；A 维度 + 新能源维度各一版。新能源维度 = 风电+光伏（t 点距离）。
"""
from __future__ import annotations

from ..config import D_DAY, Params
from ..loaders.xlsx_loader import LoadedData


def _re(l: list[float | None], i: int) -> float | None:
    return l[i] if 0 <= i < len(l) else None


def a_dimension(data: LoadedData, day: str) -> list[float | None]:
    """A(t) 96 点（任一项缺 → None，不产猜测值）。"""
    da = data.day_ahead[day]
    out: list[float | None] = []
    for t in range(96):
        terms = [da.get(k) for k in ("负荷", "风电", "光伏", "核电", "水电", "非市场化", "地方燃煤")]
        vals = [_re(s.values if s else [], t) for s in terms]
        if any(v is None for v in vals):
            out.append(None)
        else:
            load, wf, pv, nuc, hyd, nonm, coal = vals            # type: ignore[misc]
            out.append(load - wf - pv - nuc - hyd - nonm - coal)  # type: ignore[operator]
    return out


def new_energy(data: LoadedData, day: str) -> list[float | None]:
    """新能源出力 96 点 = 风电 + 光伏。"""
    da = data.day_ahead[day]
    out: list[float | None] = []
    for t in range(96):
        wf = _re(da["风电"].values, t) if "风电" in da else None
        pv = _re(da["光伏"].values, t) if "光伏" in da else None
        out.append(wf + pv if wf is not None and pv is not None else None)  # type: ignore[operator]
    return out


def _pctl(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = q * (len(sorted_vals) - 1)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def retrieve(data: LoadedData, params: Params, metric_fn, target: list[float | None], t: int) -> dict:
    """单点位检索：默认上一周 |Δ|≤容差；N<3 自动放宽至全部历史日并标注。"""
    hist = [d for d in data.days if d != D_DAY]
    recent = hist[-params.m3_scope_days:]
    tgt = target[t]
    if tgt is None:
        return {"t": t, "n": 0, "members": [], "relaxed": False, "scope": "目标点缺输入",
                "distribution": None}

    def match(days: list[str]) -> list[str]:
        out = []
        for day in days:
            v = metric_fn(day)[t]
            if v is not None and abs(v - tgt) <= params.m3_tol_mw:
                out.append(day)
        return out

    members = match(recent)
    relaxed = False
    scope = f"上一周（{recent[0]}…{recent[-1]}，{len(recent)} 日，容差 ±{params.m3_tol_mw:.0f} MW）" if recent else "无历史日"
    if len(members) < 3:
        relaxed = True                       # N<3 → 自动放宽并标注（PRD §6.1 M3）
        members = match(hist)
        scope = f"已放宽：全部历史日（{hist[0]}…{hist[-1]}，{len(hist)} 日，容差 ±{params.m3_tol_mw:.0f} MW）"
    dist = None
    if len(members) >= 3:
        vals = sorted(metric_fn(m)[t] for m in members)          # type: ignore[index]
        dist = {"median": _pctl(vals, 0.5), "p25": _pctl(vals, 0.25), "p75": _pctl(vals, 0.75)}
    return {"t": t, "n": len(members), "members": members, "relaxed": relaxed, "scope": scope,
            "distribution": dist}


def run_m3(data: LoadedData, params: Params) -> dict:
    """输出双维度各 96 点样本组。"""
    a_hist = {d: a_dimension(data, d) for d in data.days}
    ne_hist = {d: new_energy(data, d) for d in data.days}
    a_groups = [retrieve(data, params, lambda d: a_hist[d], a_hist[D_DAY], t) for t in range(96)]
    ne_groups = [retrieve(data, params, lambda d: ne_hist[d], ne_hist[D_DAY], t) for t in range(96)]
    return {
        "a_series_D": a_hist[D_DAY],
        "a_dimension_groups": a_groups,
        "new_energy_groups": ne_groups,
        "formula": "A(t) = 省调负荷 − 新能源出力 − 核电出力 − 水电出力 − 非市场化出力 − 地方燃煤出力",
    }
