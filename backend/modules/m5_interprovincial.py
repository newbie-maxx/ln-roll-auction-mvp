"""M5 省间交易总数（PRD §6.1 M5）= 滚撮量(24→96 展开) + 省间现货。
省间现货恒等式自算（不爬取）：历史日现货 = 日前联络线 − 实时联络线（compute_spot），
"历史日自算省间现货"作为边界展示项；运行日现货为辅助推测（预测层，带把握说明）。
滚撮缺点位 → 仅现货项 + 黄色警示（不把 0 成交量当已成交 0）。"""
from __future__ import annotations

from ..config import A_DAY, D_DAY, Params
from ..loaders.xlsx_loader import LoadedData, expand24to96


def compute_spot(data: LoadedData, day: str) -> list[float | None]:
    """省间现货恒等式：日前联络线 − 实时联络线（任一缺 → None）。"""
    da_tie = data.day_ahead.get(day, {}).get("联络线")
    rt_tie = data.realtime.get(day, {}).get("联络线")
    out: list[float | None] = []
    for t in range(96):
        a = da_tie.values[t] if da_tie else None
        b = rt_tie.values[t] if rt_tie else None
        out.append(a - b if a is not None and b is not None else None)   # type: ignore[operator]
    return out


def run_m5(data: LoadedData, params: Params, d_day: str = D_DAY) -> dict:
    roll24 = data.roll_auction[d_day]["volume24"]
    roll96 = expand24to96(list(roll24))
    spot96 = compute_spot(data, d_day)
    total96: list[float | None] = []
    warnings: list[str] = []
    for t in range(96):
        r, s = roll96[t], spot96[t]
        if r is None:
            warnings.append(f"t={t + 1} 滚撮缺数 → 仅现货项（黄警示，不把 0 成交量当已成交 0）")
            total96.append(s)
        elif s is None:
            total96.append(r)
        else:
            total96.append(r + s)

    # 历史日自算省间现货（边界展示项，逐日）
    hist_spot = {d: compute_spot(data, d) for d in data.days if d < d_day}
    return {
        "roll_volume_96": roll96,
        "roll_price_24": data.roll_auction[D_DAY]["price24"],
        "spot_96": spot96,                      # 运行日现货 = 辅助推测（恒等式，预测层口径）
        "total_96": total96,
        "warnings": warnings,
        "history_spot_96": hist_spot,           # 边界展示项
        "roll_source": data.roll_source,
        "d_day": d_day, "formula": "省间交易总数(96) = 滚撮量(24→96 展开) + 省间现货；现货恒等式 = 日前联络线 − 实时联络线",
        "spot_note": f"运行日现货为辅助推测（带把握说明）；A 日 {A_DAY} 同口径",
    }


def hist_total_96(data: LoadedData, day: str) -> list[float | None]:
    """历史日省间交易总数（滚撮展开 + 自算现货），供对照/展示。"""
    roll96 = expand24to96(list(data.roll_auction.get(day, {}).get("volume24", [])))
    spot = compute_spot(data, day)
    return [(r + s if r is not None and s is not None else None) for r, s in zip(roll96, spot)]  # type: ignore[operator]
