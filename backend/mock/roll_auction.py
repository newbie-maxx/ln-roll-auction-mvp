"""省间滚撮 24 点量价确定性合成（占位；真实 A-0 文件到位后 loader 自动优先读取，本模块弃用）。

合成口径（与 scripts/export_mock_data.py 保持一致）：联络线 24 点合成值 × 8%~12% 时段形状调制。
"""
from __future__ import annotations

import math


def synth_roll_auction_24(tie96: list[float | None]) -> tuple[list[float], list[float]]:
    """输入某日 96 点日前联络线 → 输出 24 点（量 MWh、价 元/MWh）确定性合成。"""
    vol24: list[float] = []
    price24: list[float] = []
    for h in range(24):
        seg = [x for x in tie96[h * 4:h * 4 + 4] if x is not None]
        tie = sum(seg) / len(seg) if seg else 0.0
        shape = 0.5 + 0.5 * math.sin(2 * math.pi * (h + 3) / 24.0)      # 0..1
        factor = 0.08 + 0.04 * shape                                    # 8%~12%
        vol24.append(round(tie * factor, 1))
        price24.append(round(300 + 120 * (0.5 + 0.5 * math.sin(2 * math.pi * (h - 6) / 24.0))
                             + 15 * math.cos(2 * math.pi * h / 12.0), 2))
    return vol24, price24
