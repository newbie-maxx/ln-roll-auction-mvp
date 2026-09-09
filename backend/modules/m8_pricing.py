"""M8 电价预测（四步，09-08 确定性算法）+ 落点区间概率/期望 + 灰度量价。

口径：docu/交易员计算流程与逻辑.md §8–§10；PRD §6.1 M8、附录 E。
- A 日 = 最新日前出清价日（配置）；A-1 日 = 前一运行日，须"电价 ≤0 点 ≤48（96 点粒度）"否则向前回溯（仅约束拟合日 2）。
- 步骤一 -100 临界空间 = 负荷率 ≤ 零价点负荷率的点中火电竞价空间最大值。
- 步骤二 A 日放缩拟合 M1/C1（k = A 空间最大 ÷ D 空间最大）；A-1 日不缩放拟合 M2/C2。
- 步骤三 预测1/2 = M×D 空间 + C（96 与 24 各自独立，24 空间 = 每 4 点等权平均）；统一钳制 [下限, 上限]。
- 步骤四 空间 ≤ 临界 → 定价 = 现货下限参数；否则预测 1 全序列最小 < 10（固定）→ 空间 > 临界点整体取预测 2，否则预测 1。
- 落点：选中时段 ±5% 相似运行日 → 实时出清价按宽度分档统计概率（分子/分母/统计范围同显；N<3 样本不足）。
- 灰度：贴下限首档 [下限, 下限+宽度] / 贴上限末档 [上限−宽度, 上限] 与意向价相减 × 量。
"""
from __future__ import annotations

from ..config import A_DAY, D_DAY, Params
from ..loaders.xlsx_loader import LoadedData, avg96to24
from .m7_thermal import step1_space, thermal_input_from


def fit_linear(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """最小二乘一次拟合 y = M·x + C。"""
    n = len(xs)
    if n < 2:
        raise ValueError("拟合样本不足（<2 点）")
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        raise ValueError("拟合自变量方差为 0")
    m = num / den
    return m, my - m * mx


def find_a1_day(days_asc: list[str], prices_by_day: dict[str, list[float | None]], a_day: str) -> tuple[str, int]:
    """A-1 回溯：自 A 日前一日向前，取第一个满足"电价 ≤0 点 ≤48（96 点粒度）"的日。"""
    idx = days_asc.index(a_day)
    for i in range(idx - 1, -1, -1):
        d = days_asc[i]
        prices = [v for v in prices_by_day.get(d, []) if v is not None]
        non_pos = sum(1 for v in prices if v <= 0)
        if non_pos <= 48:
            return d, non_pos
    raise ValueError("回溯至数据起点仍无满足「电价 ≤0 点 ≤48」的拟合日 2")


def _max_of(s: list[float | None]) -> float | None:
    vals = [v for v in s if v is not None]
    return max(vals) if vals else None


def _min_of(s: list[float | None]) -> float | None:
    vals = [v for v in s if v is not None]
    return min(vals) if vals else None


def _clamp(v: float | None, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    return min(hi, max(lo, v))


def _critical_space(space: list[float | None], on: list[float] | float, zero_lr: float) -> float | None:
    """步骤一：负荷率 ≤ 零价点负荷率的点中，空间最大值。"""
    best: float | None = None
    for t, v in enumerate(space):
        if v is None:
            continue
        unit = on[t] if isinstance(on, list) else on
        if unit and unit > 0 and v / unit <= zero_lr:
            best = v if best is None else max(best, v)
    return best


def _pairs(a: list[float | None], b: list[float | None]) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for x, y in zip(a, b):
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


def run_pricing(data: LoadedData, params: Params, d_space96: list[float | None],
                d_on96: list[float], m6_tie96: list[float | None],
                d_day: str = D_DAY, a_day: str = A_DAY) -> dict:
    """四步电价预测（96 与 24 两版各自独立判定）。A/A-1 日空间用各自披露边界 + 披露日前联络线；
    A 日 = D 日前最新有日前电价日（由调用方解析），A-1 回溯在 A 日之前的历史日内进行。"""
    price_days = [d for d in data.days if d < d_day and "日前电价" in data.day_ahead[d]
                  and any(v is not None for v in data.day_ahead[d]["日前电价"].values)]
    if a_day not in price_days:
        raise ValueError(f"A 日 {a_day} 不在 {d_day} 之前的日前电价数据范围内")
    prices_by_day = {d: data.day_ahead[d]["日前电价"].values for d in price_days}
    a1_day, a1_non_pos = find_a1_day(price_days, prices_by_day, a_day)

    a_space = step1_space(thermal_input_from(data, data.day_ahead[a_day]["联络线"].values, a_day))
    a1_space = step1_space(thermal_input_from(data, data.day_ahead[a1_day]["联络线"].values, a1_day))
    a_price = prices_by_day[a_day]
    a1_price = prices_by_day[a1_day]

    # ---- 步骤一 ----
    critical96 = _critical_space(d_space96, d_on96, params.零价点负荷率)
    d_space24 = avg96to24(d_space96)
    on24 = [sum(filter(None, d_on96[i * 4:i * 4 + 4])) / 4 for i in range(24)]
    critical24 = _critical_space(d_space24, on24, params.零价点负荷率)

    # ---- 步骤二 ----
    d_max, a_max = _max_of(d_space96), _max_of(a_space)
    if d_max in (None, 0) or a_max is None:
        raise ValueError("A/D 日空间最大值缺失，无法放缩拟合")
    k = a_max / d_max
    ax, ay = _pairs([v / k if v is not None else None for v in a_space], a_price)   # 归一空间 vs A 日电价
    m1, c1 = fit_linear(ax, ay)
    bx, by = _pairs(a1_space, a1_price)                                             # A-1 不缩放
    m2, c2 = fit_linear(bx, by)

    # ---- 步骤三 + 钳制 ----
    def apply(space: list[float | None], m: float, c: float) -> list[float | None]:
        return [_clamp(m * v + c if v is not None else None, params.现货下限, params.现货上限) for v in space]

    pred1_96 = apply(d_space96, m1, c1)
    pred2_96 = apply(d_space96, m2, c2)
    pred1_24 = apply(d_space24, m1, c1)
    pred2_24 = apply(d_space24, m2, c2)

    # ---- 步骤四 ----
    min_p1_96, min_p1_24 = _min_of(pred1_96), _min_of(pred1_24)
    use_p2_96 = min_p1_96 is not None and min_p1_96 < 10
    use_p2_24 = min_p1_24 is not None and min_p1_24 < 10

    def finalize(space, crit, use2, p1, p2):
        return [
            params.现货下限 if (crit is not None and v is not None and v <= crit) else (p2[i] if use2 else p1[i]) if v is not None else None
            for i, v in enumerate(space)
        ]

    return {
        "d_day": d_day, "a_day": a_day, "a1_day": a1_day, "a1_non_pos_points": a1_non_pos,
        "critical_space_96": critical96, "critical_space_24": critical24,
        "k": k, "M1": m1, "C1": c1, "M2": m2, "C2": c2,
        "pred1_96": pred1_96, "pred2_96": pred2_96, "final_96": finalize(d_space96, critical96, use_p2_96, pred1_96, pred2_96),
        "pred1_24": pred1_24, "pred2_24": pred2_24, "final_24": finalize(d_space24, critical24, use_p2_24, pred1_24, pred2_24),
        "min_pred1_96": min_p1_96, "used_pred2_96": use_p2_96, "used_pred2_24": use_p2_24,
        "clamp": [params.现货下限, params.现货上限],
        "formula": "步骤四：空间 ≤ 临界 → 现货下限参数；预测1 最小 <10（固定）→ 空间>临界点整体取预测2，否则预测1",
    }


def bin_prices(prices: list[float], floor: float, cap: float, width: int) -> dict:
    """[下限, 上限] 按宽度等宽分档（末档含上限）；越界丢弃计数。"""
    n_bins = int((cap - floor) // width)
    bins = [{"lo": floor + i * width, "hi": floor + (i + 1) * width,
             "mid": floor + (i + 0.5) * width, "count": 0, "prob": 0.0} for i in range(n_bins)]
    n, out_of_range = 0, 0
    for p in prices:
        if p < floor or p > cap:
            out_of_range += 1
            continue
        idx = min(n_bins - 1, int((p - floor) // width))
        bins[idx]["count"] += 1
        n += 1
    for b in bins:
        b["prob"] = b["count"] / n if n else 0.0
    return {"bins": bins, "n": n, "out_of_range": out_of_range}


def landing_stats(data: LoadedData, params: Params, d_lr24: list[float | None], period: int, d_day: str = D_DAY) -> dict:
    """落点区间：±阈值 检索历史日（日前口径负荷率 vs 计算负荷率）→ 实时出清价落档统计 + 期望。"""
    hist = [d for d in data.days if d < d_day]   # 历史范围自动收窄至 D 日之前
    target = d_lr24[period - 1] if 0 < period <= 24 else None
    members: list[str] = []
    if target is not None:
        for d in hist:
            lr = data.realtime.get(d, {}).get("24点平均日前负荷率")
            if lr is None:
                lr = data.day_ahead.get(d, {}).get("24点平均日前负荷率")
            if lr is None:
                continue
            vals = lr.values
            h = vals[period - 1] if period - 1 < len(vals) else None
            if h is not None and abs(h - target) <= params.相似日阈值:
                members.append(d)
    rt24_by_day: dict[str, list[float | None]] = {}
    for d in members:
        rt = data.realtime.get(d, {}).get("实时电价")
        rt24_by_day[d] = avg96to24(rt.values) if rt is not None else []
    prices = [rt24_by_day[d][period - 1] for d in members
              if rt24_by_day.get(d) and rt24_by_day[d][period - 1] is not None]
    stats = bin_prices(prices, params.现货下限, params.现货上限, params.区间宽度)
    enough = stats["n"] >= 3
    expectation = sum(b["mid"] * b["prob"] for b in stats["bins"]) if enough else None
    return {
        "period": period, "members": members, "n": stats["n"],
        "enough": enough, "bins": stats["bins"], "out_of_range": stats["out_of_range"],
        "expectation": expectation,
        "numerator_denominator": [(b["count"], stats["n"]) for b in stats["bins"] if b["count"]],
        "scope": f"历史日 {hist[0]}…{hist[-1]}（{len(hist)} 日），时段 {period}，阈值 ±{params.相似日阈值 * 100:.0f}%（分子/分母同显）",
    }


def grey_evaluate(list_price: float | None, volume: float | None, landing: dict,
                  params: Params) -> dict:
    """灰度量价：贴下限首档/贴上限末档与意向挂牌价相减 × 交易量（卖方视角；方向语义待业务校正）。"""
    floor, cap, width = params.现货下限, params.现货上限, params.区间宽度
    bins = landing["bins"]
    lowest = next((b for b in bins if b["lo"] == floor), {"lo": floor, "hi": floor + width, "prob": 0.0})
    highest = next((b for b in bins if b["hi"] == cap), {"lo": cap - width, "hi": cap, "prob": 0.0})
    result: dict = {
        "lowest_bin": lowest, "highest_bin": highest,
        "prob_gain": highest["prob"], "prob_risk": lowest["prob"],
        "evaluated": False, "reason": None,
        "max_gain_price": None, "max_risk_price": None,
        "max_gain_amount": None, "max_risk_amount": None,
        "direction_note": "卖方（挂牌）视角；方向语义待业务方按滚撮「价差撮合」口径校正（登记项）",
    }
    if list_price is None or list_price <= 0:
        result["reason"] = "未录意向挂牌价，不评估收益风险"
        return result
    if volume is None or volume <= 0:
        result["reason"] = "未录交易量，不评估收益风险"
        return result
    gain = (list_price - highest["hi"], list_price - highest["lo"])
    risk = (list_price - lowest["hi"], list_price - lowest["lo"])
    result.update({
        "evaluated": True,
        "max_gain_price": list(gain), "max_risk_price": list(risk),
        "max_gain_amount": [gain[0] * volume, gain[1] * volume],
        "max_risk_amount": [risk[0] * volume, risk[1] * volume],
    })
    return result
