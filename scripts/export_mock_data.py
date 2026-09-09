#!/usr/bin/env python3
"""导出 xlsx 边界数据 → web/src/mock/boundaries.json（demo 前端数据源）。

确定性、幂等：相同 xlsx 输入重跑输出字节一致（round 定位数 + sort_keys）。
- 日期角色：D=08-31、A=08-30、A-1=08-29、历史日=08-01…08-30
- 日前边界逐日 96 点：负荷/水电/核电/地方燃煤/风电/光伏/联络线/非市场化/日前电价/日前开机/检修容量
- 实时边界：实时电价(96)、24点平均日前负荷率(24)
- 省间滚撮 24 点量价：确定性合成（联络线 24 点合成值 × 8%~12% 时段形状调制）
- 意向价 385/370 元/MWh、交易量 100 MWh/时段（默认，UI 可改）
"""
import json
import math
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DA = ROOT / "data" / "辽宁省8月日前边界.xlsx"
RT = ROOT / "data" / "辽宁省8月实时边界.xlsx"
OUT = ROOT / "web" / "src" / "mock" / "boundaries.json"

DA_SHEETS = ["负荷", "水电", "核电", "地方燃煤", "风电", "光伏", "联络线",
             "非市场化", "日前电价", "日前开机", "检修容量"]
RT_SHEETS_96 = ["实时电价"]
RT_SHEETS_24 = ["24点平均日前负荷率"]

D_DAY, A_DAY, A1_DAY = "2026-08-31", "2026-08-30", "2026-08-29"


def read_sheet(path: Path, sheet: str):
    """返回 {date_str: [values...]}；列数自适应（97 列=96 点，25 列=24 点）。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    n_vals = len(header) - 1
    out = {}
    for r in rows:
        if r is None or r[0] is None:
            continue
        d = r[0]
        if not hasattr(d, "year"):
            continue
        key = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
        vals = []
        for v in r[1:n_vals + 1]:
            vals.append(round(float(v), 3) if isinstance(v, (int, float)) else None)
        out[key] = vals
    wb.close()
    return out


def synth_roll_auction(tie96):
    """省间滚撮 24 点量价确定性合成：联络线 24 点合成值 8%~12% 时段形状调制。"""
    vol24, price24 = [], []
    for h in range(24):
        tie = sum(x for x in tie96[h * 4:h * 4 + 4] if x is not None) / 4.0
        # 8%~12% 时段形状调制（0.08 + 0.04×shape，shape∈[0,1] 由正弦确定）
        shape = 0.5 + 0.5 * math.sin(2 * math.pi * (h + 3) / 24.0)
        factor = 0.08 + 0.04 * shape
        vol24.append(round(tie * factor, 1))
        price24.append(round(300 + 120 * (0.5 + 0.5 * math.sin(2 * math.pi * (h - 6) / 24.0))
                             + 15 * math.cos(2 * math.pi * h / 12.0), 2))
    return vol24, price24


def main():
    day_ahead = {s: read_sheet(DA, s) for s in DA_SHEETS}
    realtime = {s: read_sheet(RT, s) for s in RT_SHEETS_96 + RT_SHEETS_24}
    days = sorted(day_ahead["负荷"].keys())
    assert days[0] == "2026-08-01" and days[-1] == D_DAY and len(days) == 31, days

    # 24点平均日前负荷率：实时文件 96 列则每 4 点平均（缺数跳过）；25 列原生直取
    lr24 = {}
    src = realtime["24点平均日前负荷率"]
    for d, vals in src.items():
        if len(vals) == 96:
            lr24[d] = []
            for i in range(24):
                seg = [x for x in vals[i * 4:i * 4 + 4] if x is not None]
                lr24[d].append(round(sum(seg) / len(seg), 5) if seg else None)
        else:
            lr24[d] = [round(v, 5) if v is not None else None for v in vals[:24]]

    roll = {}
    for d, tie in day_ahead["联络线"].items():
        vol24, price24 = synth_roll_auction(tie)
        roll[d] = {"volume24": vol24, "price24": price24}

    payload = {
        "dates": {"D": D_DAY, "A": A_DAY, "A1": A1_DAY, "historyEnd": A_DAY},
        "days": days,
        "dayAhead": day_ahead,
        "realtime": {"实时电价": realtime["实时电价"], "24点平均日前负荷率": lr24},
        "rollAuction": roll,
        "intentDefault": {"listPrice": 385.0, "liftPrice": 370.0, "volume": 100.0},
        "source": "data/辽宁省8月日前边界.xlsx + data/辽宁省8月实时边界.xlsx（省间滚撮为确定性合成占位）",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text)} bytes, {len(days)} days)")


if __name__ == "__main__":
    main()
