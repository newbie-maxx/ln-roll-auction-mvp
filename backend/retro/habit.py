"""M-11 习惯统计：H1–H13（附录 C 公式）+ 显著性三门槛 + §E-A6.2 剔除规则 + G/G⁻ 比对。

主体范围：owner == 当前账号 且 value_type ∈ {修订, 意向}；时间 ≤ D_end（全局窗口 G）。
显著性（同时满足才叫「习惯」）：≥20 天 + 符号一致率 ≥70% + 符号检验 p<0.05（双侧）。
剔除：类型三日整日不进；披露诱导点级不进；跟随披露修正计中性照进。
"""
from __future__ import annotations

import math
from datetime import datetime


def binom_p_two_sided(k: int, n: int, p0: float = 0.5) -> float | None:
    """精确二项检验（双侧，H0: p=0.5）。"""
    if n <= 0:
        return None
    pk = math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k)
    p_le = sum(math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(0, k + 1))
    p_ge = sum(math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(k, n + 1))
    return min(1.0, 2 * min(p_le, p_ge) - pk if pk else 2 * min(p_le, p_ge))


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else round((ys[n // 2 - 1] + ys[n // 2]) / 2, 4)


def _iqr(xs: list[float]) -> float | None:
    if len(xs) < 4:
        return None
    ys = sorted(xs)
    def q(f):
        i = f * (len(ys) - 1)
        lo, hi = int(math.floor(i)), int(math.ceil(i))
        return ys[lo] + (ys[hi] - ys[lo]) * (i - lo)
    return round(q(0.75) - q(0.25), 4)


def _days(d0: str, d1: str) -> int:
    return (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days + 1


def _sign(v: float) -> int:
    return 1 if v > 0 else (-1 if v < 0 else 0)


def compute_habits(day_stats: list[dict], d_start: str, d_end: str,
                   exclusions: dict[str, str] | None = None) -> dict:
    """day_stats：[{date, deltas{item: 日均Δ}, day_type, main_factor, rev_points[], inter_pred_delta, op_times[]}]
    exclusions：{date: 原因}（类型三日/披露诱导日不进样本；点级剔除由调用方先行过滤）。
    返回 H1–H13（不达门槛只标统计值，不删行）。
    """
    excl = exclusions or {}
    valid = [d for d in day_stats if d["date"] <= d_end and d["date"] not in excl]
    n_days = len(valid)
    out: dict[str, dict] = {"_meta": {"n_days": n_days, "n_excluded": len(excl),
                                      "window": [d_start, d_end]}}

    items = sorted({k for d in valid for k in d.get("deltas", {})})
    for item in items:
        xs = [d["deltas"][item] for d in valid if item in d["deltas"]
              and d["deltas"][item] is not None]
        if not xs:
            continue
        med = _median(xs) or 0.0
        same = sum(1 for x in xs if _sign(x) == _sign(med) and med != 0)
        rate = round(same / len(xs), 4) if xs and med != 0 else None
        p = binom_p_two_sided(same, len(xs)) if same is not None and rate is not None else None
        out[f"H1:{item}"] = {"n": len(xs), "sign_rate": rate, "p_value": p,
                             "significant": bool(rate is not None and p is not None
                                                 and len(xs) >= 20 and rate >= 0.70 and p < 0.05)}
        out[f"H2:{item}"] = {"mean": round(sum(xs) / len(xs), 2), "median": _median(xs)}
        out[f"H3:{item}"] = {"iqr": _iqr(xs)}
        # H5 分时段热力（24 段均值）
        heat = {}
        for h in range(24):
            vals = [d.get("deltas_by_hour", {}).get(f"{item}:{h}")
                    for d in valid if d.get("deltas_by_hour")]
            vals = [v for v in vals if v is not None]
            heat[str(h)] = round(sum(vals) / len(vals), 2) if vals else None
        out[f"H5:{item}"] = {"heat_24": heat}
    # H6 分场景（风电高/低 × 价差）
    hi = [d for d in valid if (d.get("wind_level") == "high")]
    lo = [d for d in valid if (d.get("wind_level") == "low")]
    for name, grp in (("风电高", hi), ("风电低", lo)):
        xs = [d["deltas"].get("电价") for d in grp if d["deltas"].get("电价") is not None]
        out[f"H6:{name}"] = {"n": len(grp), "mean_price_delta": round(sum(xs) / len(xs), 2) if xs else None}
    # H7 修订方向一致率 / H8 幅度 / H11 时点
    revs = [r for d in valid for r in d.get("rev_points", [])]
    if revs:
        agree = sum(1 for r in revs if r.get("dir_agree"))
        out["H7"] = {"n": len(revs), "agree_rate": round(agree / len(revs), 4)}
        out["H8"] = {"mean_gap": round(
            sum(r["rev_amt"] - r["act_gap"] for r in revs if r.get("rev_amt") is not None
                and r.get("act_gap") is not None) / max(1, len(revs)), 3)}
    inters = [d["inter_pred_delta"] for d in valid if d.get("inter_pred_delta") is not None]
    if inters:
        out["H9"] = {"mean": round(sum(inters) / len(inters), 2)}   # 正=报高
    # H10 需回单（本期不评估）
    out["H10"] = {"note": "需成交回单，本期不可评估"}
    ops = [datetime.fromisoformat(t).hour * 60 + datetime.fromisoformat(t).minute
           for d in valid for t in d.get("op_times", [])]
    if ops:
        out["H11"] = {"n": len(ops), "median_min_after_1330": (_median(
            [m - (13 * 60 + 30) for m in ops]))}
    # H12 主因分布（剔除类型三日由 exclusions 处理；主因单列：跃变日仍进 H1–H9 但不进 H12）
    mains: dict[str, int] = {}
    for d in valid:
        if d.get("is_jump_day"):
            continue
        if d.get("main_factor"):
            mains[d["main_factor"]] = mains.get(d["main_factor"], 0) + 1
    out["H12"] = {"counts": dict(sorted(mains.items())),
                  "note": "跃变日主因单列，不进排名"}
    jt = {"类型一": 0, "类型二": 0, "类型三": 0}
    for d in valid:
        for k in jt:
            if d.get("day_type") == k:
                jt[k] += 1
    out["H13"] = {"jump_day_counts": jt, "n_days": n_days}
    return out


def divergence(current: dict, baseline: dict, g_minus_days: int) -> dict:
    """§E-A6.3 本期 vs 长期（基准 G⁻；|G⁻|<20 退化用 G 且标注偏松）。五种结果。"""
    results: list[dict] = []
    degenerate = g_minus_days < 20
    base_src = "G（退化，基准已含本期数据，比对结果偏松）" if degenerate else "G⁻"
    cur_items = {k.split(":", 1)[1]: v for k, v in current.items() if k.startswith("H1:")}
    base_items = {k.split(":", 1)[1]: v for k, v in baseline.items() if k.startswith("H1:")}
    for item in sorted(set(cur_items) & set(base_items)):
        c, b = cur_items[item], base_items[item]
        if not b.get("significant"):
            if c.get("significant"):
                results.append({"item": item, "result": "本期新出现",
                                "detail": "本期出现新倾向，此前未形成稳定习惯，需继续观察"})
            continue
        c_med = (current.get(f"H2:{item}") or {}).get("median")
        b_med = (baseline.get(f"H2:{item}") or {}).get("median")
        if c_med is None or b_med is None:
            continue
        same_dir = _sign(c_med) == _sign(b_med)
        if not same_dir:
            results.append({"item": item, "result": "方向相反",
                            "detail": f"过去{'偏高' if b_med > 0 else '偏低'}，本期{'偏高' if c_med > 0 else '偏低'}",
                            "baseline": b_med, "current": c_med, "base_src": base_src})
        elif abs(c_med) > abs(b_med) * 1.5:
            results.append({"item": item, "result": "同向超常",
                            "detail": "方向一致，但本期幅度明显大于往常",
                            "baseline": b_med, "current": c_med, "base_src": base_src})
        elif c.get("significant"):
            results.append({"item": item, "result": "一致",
                            "detail": "本期符合你的一贯倾向", "baseline": b_med,
                            "current": c_med, "base_src": base_src})
    # 结果 5：长期偏差本期未出现
    for item, b in base_items.items():
        if b.get("significant") and item in cur_items and not cur_items[item].get("significant"):
            results.append({"item": item, "result": "长期偏差本期未出现",
                            "detail": "本期未出现该长期偏差"})
    return {"degenerate": degenerate, "base_src": base_src, "results": results}
