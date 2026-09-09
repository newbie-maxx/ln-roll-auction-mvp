"""M2 分布式推断（PRD §6.2 M2）：分布式光伏/分散式风电各挑集中式风光最相近历史日取值，标 `▣取自 M-D`。"""
from __future__ import annotations

from ..config import D_DAY
from ..loaders.xlsx_loader import LoadedData


def _profile_distance(a: list[float | None], b: list[float | None]) -> float:
    """96 点曲线欧氏距离（缺数点跳过；全缺 → inf）。"""
    s = 0.0
    n = 0
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        s += (x - y) ** 2
        n += 1
    return s ** 0.5 if n else float("inf")


def infer_distributed(data: LoadedData, d_day: str = D_DAY) -> dict:
    """返回 D 日分布式光伏/分散式风电 96 点（推断值），及推导理由（来源日 + 距离）。

    D 日总 风电 = 集中式风电(披露) + 分散式风电(推断)；光伏同理（数据验证：sheet 风电/光伏 = 集中式 + 分散式）。
    """
    hist = [d for d in data.days if d < d_day]   # 历史范围自动收窄至 D 日之前
    out: dict = {}
    for cent_key, dist_key in (("集中式光伏", "分布式光伏"), ("集中式风电", "分散式风电")):
        d_cent = data.day_ahead[D_DAY][cent_key].values
        best_day, best_dist = None, float("inf")
        for day in hist:
            dist = _profile_distance(d_cent, data.day_ahead[day][cent_key].values)
            if dist < best_dist:
                best_day, best_dist = day, dist
        if best_day is None:
            out[dist_key] = {"values": [None] * 96, "src_day": None, "distance": None,
                             "note": "无历史日可比，标缺"}
            continue
        src = data.day_ahead[best_day][dist_key]
        out[dist_key] = {
            "values": list(src.values),
            "src_day": best_day,
            "distance": round(best_dist, 2),
            "note": f"▣取自 {best_day}（{cent_key} 最相近历史日，欧氏距离 {best_dist:.1f}）",
        }
    return out
