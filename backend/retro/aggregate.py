"""M-12 时段聚合与点集归集：96→24 区间均值（按实有天数）；24→96 只复制不插值（阶梯）。"""
from __future__ import annotations

from ..loaders.xlsx_loader import expand24to96


def to24(values96: list[float | None]) -> list[float | None]:
    """96 → 24：每 4 点等权平均；部分缺点按实有点均值；全缺点为 None。"""
    out: list[float | None] = []
    for h in range(24):
        pts = values96[h * 4:(h + 1) * 4]
        xs = [v for v in pts if v is not None]
        out.append(round(sum(xs) / len(xs), 4) if xs else None)
    return out


def interval_mean(days: list[list[float | None]]) -> list[float | None]:
    """多日区间均值（逐点，分母=实有数据天数；全缺为 None）——图注写「区间均值（N 天）」，
    N = 有值天数（各点可能不同，图注统一取区间有效日数，逐点 N 在数据完整性披露）。"""
    out: list[float | None] = []
    for i in range(len(days[0]) if days else 0):
        xs = [d[i] for d in days if i < len(d) and d[i] is not None]
        out.append(round(sum(xs) / len(xs), 4) if xs else None)
    return out


def expand24(v24: list[float | None]) -> list[float | None]:
    """24→96 只复制（第 h 点 → 4h+1..4h+4），阶梯，不插值。"""
    return expand24to96(list(v24))


def period_stats(values96: list[float | None]) -> dict:
    """R2 第六章统计单元：24 时段聚合素材。"""
    v24 = to24(values96)
    xs = [v for v in v24 if v is not None]
    return {"v24": v24, "mean": round(sum(xs) / len(xs), 2) if xs else None,
            "min": min(xs) if xs else None, "max": max(xs) if xs else None}
