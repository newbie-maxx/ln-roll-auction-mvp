"""M-06 跃变判定：五步定性（命中即停）+ 缺口倒推 + 上临界 U1/U2/U3 + 类型三 C1/C2/C3 + F1/F2/F3。

判定顺序（§E-A3.2）：① 下临界 → ② 上临界 → ③ 模式切换 → ④ 行为跃变 → ⑤ 未定性。
排序键 = 因子对火电空间的影响（负荷 +、联络线受入 −、新能源与必发 −），不是偏差绝对值。
组合不唯一是正常结论（combo_unique=false → 报告写「多项叠加，主次不可分」）。
"""
from __future__ import annotations

from ..config import DEFAULT_PARAMS
from . import constants as C

# 因子 → 对火电空间的影响符号（space = load − Σ其余）
SPACE_SIGN = {"负荷": 1.0, "联络线": -1.0, "风电": -1.0, "光伏": -1.0, "水电": -1.0,
              "核电": -1.0, "地方燃煤": -1.0, "非市场化": -1.0, "省间交易总量": -1.0}


def _pct(values: list[float | None], q: float) -> float | None:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[idx]


def _fit_linear(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0
    return m, my - m * mx


def classify(run_date: str, dec: dict, delta_result: dict, theta: dict,
             verify_fn=None) -> dict:
    """逐点跃变判定 + 日级类型（类型二/三）。verify_fn(factors) → final96 重算验证。"""
    spot = delta_result["table"]["电价"]["v_act"]
    dp = delta_result["table"]["电价"]["delta"]
    resid = dec["residual"]
    space = dec["actual_m7"]["space_96"]
    lr = dec["actual_m7"]["load_rate_96"]
    pos_res = dec["actual_m7"]["pos_surplus_96"]
    critical = dec.get("actual_critical", dec["base_m8"]["critical_space_96"])
    p70, p30, p80 = _pct(spot, 0.70), _pct(spot, 0.30), _pct(spot, 0.80)

    theta_high, theta_jump = float(theta["theta_high"]), float(theta["theta_jump"])

    # ---- 日级：类型二（开关翻转）----
    type2 = dec["base_used_pred2"] != dec["actual_used_pred2"]

    # ---- 日级：类型三（C1/C2/C3 同时）----
    floor_pts = [(i, lr[i]) for i in range(96)
                 if spot[i] is not None and spot[i] <= C.SPOT_FLOOR + 1e-9 and lr[i] is not None]
    c1 = c2 = c3 = False
    theta_act = max((v for _, v in floor_pts), default=None)
    if theta_act is not None and abs(theta_act - C.THETA_SET) >= 0.05:
        c1 = True
    pairs = [(s, p) for s, p in zip(space, spot) if s is not None and p is not None]
    if pairs and dec["base_m8"]["M1"]:
        xs = [s for s, _ in pairs]
        ys = [p for _, p in pairs]
        m, c0 = _fit_linear(xs, ys)
        m0 = dec["base_m8"]["M1"]
        resid_d = [y - (m * x + c0) for x, y in pairs]
        med = sorted(resid_d)[len(resid_d) // 2]
        same = sum(1 for r in resid_d if (r >= 0) == (med >= 0)) / len(resid_d)
        if (m0 != 0 and abs(m - m0) / abs(m0) >= 0.30) or (abs(med) >= 100 and same >= 0.70):
            c2 = True
    if dec["explained_ratio"] is not None and dec["explained_ratio"] < 0.20:
        c3 = True
    type3 = c1 and c2 and c3

    points: list[dict] = []
    for i in range(96):
        p = spot[i]
        if p is None:
            points.append({"t": i + 1, "jump_type": "缺数", "detail": {}})
            continue
        prev = spot[i - 1] if i > 0 else None

        # ① 下临界：实际价=现货下限 且 空间 ≤ 临界空间（critical 为日级标量：m8 步骤一）
        sp_i = space[i]
        if p <= C.SPOT_FLOOR + 1e-9 and sp_i is not None and critical is not None \
                and sp_i <= critical:
            points.append({"t": i + 1, "jump_type": "类型一", "jump_side": "down",
                           "detail": {"space": sp_i, "critical": critical, **_gap_combo(
                               i, dec, delta_result, "down", verify_fn)}})
            continue

        # ② 上临界：U1+U2+U3 同时满足
        u1 = p >= theta_high
        u2 = prev is not None and (p - prev) >= theta_jump
        u3 = (lr[i] is not None and lr[i] >= C.U3_LOAD_RATE) or (
            pos_res[i] is not None and pos_res[i] < 0)
        if u1 and u2 and u3:
            push = _push_items(i, delta_result, up=True)
            points.append({"t": i + 1, "jump_type": "类型一", "jump_side": "up",
                           "detail": {
                               "u1": bool(u1), "u2": bool(u2), "u3": bool(u3),
                               "price_before": prev, "price_after": p,
                               "jump_amp": round(p - prev, 2) if prev is not None else None,
                               "lr_act": lr[i], "res_pos_reserve": pos_res[i],
                               "tight_signal": ("两者" if (lr[i] is not None and lr[i] >= C.U3_LOAD_RATE)
                                                and (pos_res[i] is not None and pos_res[i] < 0)
                                                else ("负荷率贴顶" if lr[i] is not None and lr[i] >= C.U3_LOAD_RATE
                                                      else "正备用为负")),
                               "push_items": push, "note": "模型无上临界规则，不反算缺口"}})
            continue

        # ③ 类型二 / ④ 类型三（日级）挂点
        if type2:
            points.append({"t": i + 1, "jump_type": "类型二", "detail": {
                "used_pred2_base": dec["base_used_pred2"],
                "used_pred2_actual": dec["actual_used_pred2"],
                "min_pred1": dec["base_m8"]["min_pred1_96"]}})
            continue
        if type3:
            points.append({"t": i + 1, "jump_type": "类型三", "detail": {
                "theta_set": C.THETA_SET, "theta_act": theta_act,
                "explained_ratio": dec["explained_ratio"]}})
            continue

        # ⑤ 残差显著 → 未定性；否则无
        r_i = resid[i]
        if r_i is not None and abs(r_i) >= C.RESIDUAL_JUMP_YUAN:
            points.append({"t": i + 1, "jump_type": "未定性", "detail": {
                "residual": r_i, "hint": "疑似口径差，只问不归因"}})
        else:
            points.append({"t": i + 1, "jump_type": "无", "detail": {}})

    # ---- F1/F2/F3 反向形态标注（只进 anomaly，不得进主因） ----
    anomaly: list[dict] = []
    for i in range(96):
        p, s = spot[i], space[i]
        if s is not None and p is not None and p70 is not None and p30 is not None and p80 is not None:
            if s >= p70 and p <= 0:
                anomaly.append({"id": f"A-F1-{i+1:02d}", "type": "F1", "t": i + 1,
                                "space_pct": round(s, 1), "price": p,
                                "hint_question": f"{i//4:02d}:{i%4*15:02d} 空间处于高位却触底，是否与负备用不足有关？"})
            if s <= p30 and p >= p80:
                prev_s = space[i - 1] if i > 0 else None
                anomaly.append({"id": f"A-F2-{i+1:02d}", "type": "F2", "t": i + 1,
                                "space_pct": round(s, 1), "price": p,
                                "space_change": round(s - prev_s, 1) if prev_s is not None else None,
                                "hint_question": "空间小却高价，是否受爬坡限制？"})
        if i > 0 and space[i] is not None and space[i - 1] is not None \
                and spot[i] is not None and spot[i - 1] is not None:
            ds, dp_ = space[i] - space[i - 1], spot[i] - spot[i - 1]
            if ds * dp_ < 0:
                anomaly.append({"id": f"A-F3-{i+1:02d}", "type": "F3", "t": i + 1,
                                "space_change": round(ds, 1), "price_change": round(dp_, 1),
                                "hint_question": "跨时段反向，建议核对机组开机方式"})

    jump_points = [pt for pt in points if pt["jump_type"] not in ("无", "缺数")]
    return {"run_date": run_date, "points": points, "jump_points": jump_points,
            "day_type2": type2, "day_type3": type3,
            "c1": c1, "c2": c2, "c3": c3, "theta_act": theta_act,
            "anomaly": anomaly, "theta": theta,
            "p_set_count": sum(1 for p in spot if p is not None and p >= theta_high),
            "p_set_ratio": round(sum(1 for p in spot if p is not None and p >= theta_high)
                                 / max(1, sum(1 for p in spot if p is not None)), 4)}


def _push_items(i: int, delta_result: dict, up: bool) -> list[dict]:
    """把空间往上（供不应求方向）推的因子，按影响绝对值降序。"""
    items = []
    for f in C.FACTORS:
        d = delta_result["table"].get(f, {}).get("delta")
        if not d or d[i] is None:
            continue
        influence = SPACE_SIGN[f] * d[i]
        if (up and influence > 0) or (not up and influence < 0):
            items.append({"factor": f, "delta": d[i], "influence": round(influence, 1)})
    items.sort(key=lambda x: -abs(x["influence"]))
    return items


def _gap_combo(i: int, dec: dict, delta_result: dict, side: str,
               verify_fn) -> dict:
    """缺口倒推（仅下临界）：按空间影响降序累加，累计首次覆盖缺口的最小前缀 = 触发组合。"""
    sp = dec["actual_m7"]["space_96"][i]
    cr = dec.get("actual_critical", dec["base_m8"]["critical_space_96"])
    if sp is None or cr is None:
        return {"gap_mw": None, "combo": [], "combo_unique": None, "verify_runs": 0}
    gap = round(max(0.0, cr - sp), 1) if side == "down" else None
    push = _push_items(i, delta_result, up=False)   # 下临界：把空间往下推的项
    combo: list[str] = []
    cum = 0.0
    for it in push:
        combo.append(it["factor"])
        cum += abs(it["influence"])
        if cum >= (gap or 0):
            break
    # 唯一性：去掉任一项后不再覆盖 → 唯一
    unique = None
    if combo and gap is not None:
        infl = {it["factor"]: abs(it["influence"]) for it in push if it["factor"] in combo}
        total = sum(infl.values())
        unique = all(total - v < gap for v in infl.values())
    verified = None
    verify_runs = 0
    if verify_fn is not None and combo:
        r = verify_fn(combo)
        verified = r is not None
        verify_runs = 1
    return {"gap_mw": gap, "combo": combo, "combo_unique": unique,
            "verify_runs": verify_runs, "verified": verified}
