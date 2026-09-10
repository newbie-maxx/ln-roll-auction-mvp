"""M4 联络线基线（符号口径 2026-09-10 锁定：滚撮正=买入/受入、负=卖出/送出）：
各历史日基线(96) = 日前联络线(96) − 省间滚撮量(24→96 展开)；
缺数日标"缺"不参与均值。基线均值按 M3 A 维度样本组成员逐点取（样本组空 → 该点退化为全历史均值并标注）。"""
from __future__ import annotations

from ..config import D_DAY, Params
from ..loaders.xlsx_loader import LoadedData, avg96to24, expand24to96


def day_baseline(data: LoadedData, day: str) -> list[float | None] | None:
    """单日基线；滚撮或联络线任一缺 → None（该日标"缺"不参与均值）。"""
    roll24 = data.roll_auction.get(day, {}).get("volume24")
    tie = data.day_ahead.get(day, {}).get("联络线")
    if not roll24 or tie is None:
        return None
    roll96 = expand24to96(list(roll24))
    out: list[float | None] = []
    for t in range(96):
        r, v = roll96[t], tie.values[t]
        out.append(v - r if r is not None and v is not None else None)   # 基线 = 日前联络线 − 滚撮量
    return out if all(v is not None for v in out) else None


def run_m4(data: LoadedData, params: Params, m3_result: dict, d_day: str = D_DAY) -> dict:
    baselines: dict[str, list[float | None]] = {}
    missing_days: list[str] = []
    for day in data.days:
        if day >= d_day:          # 历史范围自动收窄：仅 D 日之前参与基线
            continue
        b = day_baseline(data, day)
        if b is None:
            missing_days.append(day)
        else:
            baselines[day] = b

    mean_96: list[float | None] = []
    mean_sources: list[list[str]] = []
    for t in range(96):
        members = m3_result["a_dimension_groups"][t]["members"]
        used = [d for d in members if d in baselines]
        note_relaxed = m3_result["a_dimension_groups"][t]["relaxed"]
        if not used:
            used = list(baselines.keys())
        vals = [baselines[d][t] for d in used]                          # type: ignore[index]
        vals = [v for v in vals if v is not None]
        mean_96.append(round(sum(vals) / len(vals), 4) if vals else None)
        mean_sources.append(used)
    return {
        "baselines": {d: b for d, b in baselines.items()},
        "missing_days": missing_days,           # 标"缺"不参与均值
        "mean_96": mean_96,
        "mean_sources_96": mean_sources,
        "mean_24": avg96to24(mean_96),
        "formula": "各日基线(96) = 日前联络线 − 滚撮量(24→96 展开)；均值按 M3 A 维度样本组成员逐点取",
        "relaxed_any": any(g["relaxed"] for g in m3_result["a_dimension_groups"]),
    }
