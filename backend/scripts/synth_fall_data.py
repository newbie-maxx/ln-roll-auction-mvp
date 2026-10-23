"""合成 9 月—10 月边界数据（测试用；以 8 月真实数据为基准做迎峰度夏尾季的温和衰减 + 噪声）。

只读 8 月基线（data/*.xlsx），产出三个"上传契约"合规的 xlsx 到指定目录（默认 /tmp/synth）：
  day_ahead.xlsx（18 sheets，96 点；24点平均日前负荷率为 24 点）
  realtime.xlsx（12 sheets，96 点）
  roll.xlsx（成交量/价格，24 点）
随后经 /api/data/upload 上传（落 backend/data/uploads/，不触碰 data/）。

合成规则（确定性，同输入同输出）：
- 基准日 = 8 月第 (k mod 31) 天的同名序列（回放季节形态）
- 季节因子（k = 距 9-01 的天数）：负荷 −0.26%/日、光伏 −0.35%/日、风电 +0.20%/日、电价中枢 −0.40%/日
- 逐点噪声 ±2%（边界）/±5%（价格，保留 8 月负价形态、钳制 [-100,1500]）
- 实时侧相对日前再叠加 ±3%（披露差异）；实时开机 = 日前开机（与 8 月同口径：系统输出复制品）

用法：
  python3 backend/scripts/synth_fall_data.py --start 2026-09-01 --end 2026-10-27 --rt-end 2026-10-22
  （DA/滚撮含未来交付日，支持选择 D+2..D+4 滚撮；实时实况只到 rt-end）
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import openpyxl  # noqa: E402

from backend.config import DA_SHEETS, DA_XLSX, RT_XLSX, find_roll_file  # noqa: E402
from backend.loaders.roll_reader import read_roll_file  # noqa: E402
from backend.loaders.xlsx_loader import read_sheet  # noqa: E402

RT_SYNTH_SHEETS = ("实时电价", "联络线", "负荷", "风电", "光伏", "水电", "核电",
                   "地方燃煤", "非市场化", "24点平均日前负荷率", "实时开机")
# 24 点原生 sheet（其余 96 点）
SHEETS_24 = {"24点平均日前负荷率"}


def _noise(key: str, t: int, amp: float) -> float:
    rng = random.Random(f"{key}:{t}")
    return 1.0 + rng.uniform(-amp, amp)


def _season(sheet: str, k: int) -> float:
    if sheet in ("负荷", "检修容量", "日前开机", "实时开机", "24点平均日前负荷率", "核电", "水电"):
        return max(0.80, 1.0 - 0.0026 * k)
    if sheet in ("光伏", "集中式光伏", "分布式光伏", "抽蓄水电"):
        return max(0.75, 1.0 - 0.0035 * k)
    if sheet in ("风电", "集中式风电", "分散式风电"):
        return min(1.25, 1.0 + 0.0020 * k)
    if sheet in ("地方燃煤", "非市场化", "联络线"):
        return max(0.85, 1.0 - 0.0012 * k)
    return max(0.75, 1.0 - 0.0040 * k)   # 电价类


def _scale(vals: list[float | None], sheet: str, k: int, tag: str,
           amp: float, lo: float | None = None, hi: float | None = None) -> list[float | None]:
    f = _season(sheet, k)
    out: list[float | None] = []
    for t, v in enumerate(vals):
        if v is None:
            out.append(None)
            continue
        x = v * f * _noise(f"{tag}:{sheet}", t, amp)
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        out.append(round(x, 4))
    return out


def _price_from(base: list[float | None], k: int, tag: str, spread: float) -> list[float | None]:
    """电价：保留 8 月的负价/尖峰形态，只做中枢衰减 + 小幅波动，钳制现货界。"""
    f = _season("价格", k)
    out: list[float | None] = []
    for t, v in enumerate(base):
        if v is None:
            out.append(None)
            continue
        x = v * f + (v - 0) * 0.0 + random.Random(f"{tag}:p:{t}").uniform(-spread, spread)
        out.append(round(max(-100.0, min(1500.0, x)), 2))
    return out


def _write_sheet(ws, header: list[str], days: list[date], rows: list[list[float | None]]) -> None:
    ws.append(header)
    for d, vals in zip(days, rows):
        ws.append([d] + list(vals))


def build(out_dir: Path, start: str, end: str, rt_end: str | None = None) -> dict:
    """DA/滚撮覆盖 [start, end]（含未来交付日的前瞻披露）；RT 只覆盖 [start, rt_end]（实况仅过去日）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    s0, e0 = date.fromisoformat(start), date.fromisoformat(end)
    days = [s0 + timedelta(days=i) for i in range((e0 - s0).days + 1)]
    rt_days = [d for d in days if d <= date.fromisoformat(rt_end)] if rt_end else list(days)
    aug_days = sorted(read_sheet(DA_XLSX, "负荷").keys())
    if len(aug_days) < 31:
        raise RuntimeError("8 月基线不足 31 天")
    roll, _layout = read_roll_file(find_roll_file())  # data/省间滚撮统计8月.xlsx → {day: {volume24, price24}}

    da_rows: dict[str, list[list[float | None]]] = {s: [] for s in DA_SHEETS}
    rt_rows: dict[str, list[list[float | None]]] = {s: [] for s in RT_SYNTH_SHEETS}
    roll_vol: list[list[float | None]] = []
    roll_price: list[list[float | None]] = []

    for k, d in enumerate(days):
        iso = d.isoformat()
        base_day = aug_days[k % len(aug_days)]
        for sheet in DA_SHEETS:
            src = read_sheet(DA_XLSX, sheet).get(base_day) or []
            if sheet == "日前电价":
                vals = _price_from(src, k, f"da:{iso}", 18.0)
            elif sheet in SHEETS_24:
                vals = _scale(src, sheet, k, f"da:{iso}", 0.02, lo=0.0, hi=1.0)
            else:
                vals = _scale(src, sheet, k, f"da:{iso}", 0.02, lo=0.0)
            da_rows[sheet].append(vals)

        rt_has_day = d <= (rt_days[-1] if rt_days else e0)   # 未来交付日：无实时实况（RT 行不写）
        for sheet in (RT_SYNTH_SHEETS if rt_has_day else ()):
            src = read_sheet(RT_XLSX, sheet).get(base_day) or []
            if sheet == "实时电价":
                # 以 8 月「实时电价」基准日形态演化：保留负价/尖峰结构（日前价与之独立演化）
                vals = _price_from(src, k, f"rt:{iso}", 12.0)
            elif sheet == "实时开机":
                vals = list(da_rows.get("日前开机", [[]])[-1]) or [None] * 96
            elif sheet == "24点平均日前负荷率":
                vals = list(da_rows["24点平均日前负荷率"][-1])
            elif sheet in ("负荷", "风电", "光伏", "水电", "核电", "地方燃煤", "非市场化", "联络线"):
                da_vals = da_rows.get(sheet, [[]])[-1] or [None] * 96
                vals = _scale(da_vals, sheet, 0, f"rt:{iso}", 0.03, lo=0.0)
            else:
                vals = _scale(src, sheet, k, f"rt:{iso}", 0.02, lo=0.0)
            rt_rows[sheet].append(vals)

        rb = roll.get(base_day)
        if not rb or not rb.get("volume24"):
            roll_vol.append([None] * 24)
            roll_price.append([None] * 24)
        else:
            rng = random.Random(f"roll:{iso}")
            roll_vol.append([None if v is None else round(v * rng.uniform(0.85, 1.15), 4)
                             for v in rb["volume24"]])
            p24 = [None if v is None else v for v in (rb.get("price24") or [None] * 24)]
            roll_price.append([None if v is None else round(v + rng.uniform(-8.0, 8.0), 2) for v in p24])

    def _save(path: Path, sheets: dict[str, list[list[float | None]]], ncol: dict[str, int],
              days_override: list[date] | None = None) -> Path:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        use_days = days_override or days
        for sheet, rows in sheets.items():
            ws = wb.create_sheet(sheet)
            n = ncol.get(sheet, 96)
            _write_sheet(ws, ["日期"] + [f"t{i+1}" for i in range(n)], use_days, rows)
        wb.save(path)
        return path

    da_path = _save(out_dir / "day_ahead.xlsx", da_rows, {s: 24 for s in SHEETS_24})
    # 实时表只含实况已终态日（跳过未来交付日）
    rt_days_list = [d for d in days if d <= (rt_days[-1] if rt_days else e0)]
    rt_trimmed = {k: v[:len(rt_days_list)] for k, v in rt_rows.items()}
    rt_path = _save(out_dir / "realtime.xlsx", rt_trimmed, {s: 24 for s in SHEETS_24},
                    days_override=rt_days_list)
    roll_path = _save(out_dir / "roll.xlsx",
                      {"成交量": roll_vol, "价格": roll_price}, {"成交量": 24, "价格": 24})
    return {"days": [d.isoformat() for d in days], "files": [str(da_path), str(rt_path), str(roll_path)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/synth")
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-10-27", help="日前/滚撮截止日（含未来交付日）")
    ap.add_argument("--rt-end", default="2026-10-22", help="实时实况截止日（不含未来日）")
    a = ap.parse_args()
    res = build(Path(a.out), a.start, a.end, a.rt_end)
    print(f"合成 {len(res['days'])} 天（{res['days'][0]} ~ {res['days'][-1]}；实况截至 {a.rt_end}）：")
    for f in res["files"]:
        print(" ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
