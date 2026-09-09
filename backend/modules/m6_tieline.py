"""M6 预测联络线（PRD §6.1 M6）：预测联络线(96) = 基线均值 − 省间交易总数（考核量产出，恒等式两侧可复核）。
输出推断值 + 理由 + 样本来源；任一缺 → 该点"缺输入"。"""
from __future__ import annotations

from ..config import Params
from ..loaders.xlsx_loader import LoadedData, avg96to24


def run_m6(data: LoadedData, params: Params, m4: dict, m5: dict) -> dict:
    mean96 = m4["mean_96"]
    total96 = m5["total_96"]
    out96: list[float | None] = []
    for t in range(96):
        m, s = mean96[t], total96[t]
        out96.append(m - s if m is not None and s is not None else None)   # type: ignore[operator]
    reasons = [
        f"t={t + 1}: 基线均值 {mean96[t]:.1f} − 省间交易总数 {total96[t]:.1f} = {out96[t]:.1f}"
        if out96[t] is not None else f"t={t + 1}: 缺输入（基线或省间数缺）"
        for t in range(96)
    ]
    return {
        "predicted_96": out96,
        "predicted_24": avg96to24(out96),
        "reasons_96": reasons,
        "sample_sources_96": m4["mean_sources_96"],
        "kind": "推断值",
        "formula": "预测联络线(96) = 基线均值 − 省间交易总数",
    }
