"""M1 本地数据装载与标准化（PRD §6.1 M1）。

- CSV/XLSX/PDF loader 可替换：本类实现 XLSX；`Loader` 协议约束替换位（B 轨仅换 loader，计算零改动）。
- 96 点入库；省间滚撮 24→96 展开（小时值复制 4 份）。
- 三类标注（披露/推断/补齐 + 来源日）；运行日不可披露项（非市场化/水电/地方燃煤）最近日法。
- 缺数补齐顺序：人工填数 → 相似日 → 均值/插值；有据标 `补齐(方式+依据)`，无据标 `缺输入`。
- 核电预测 = min(最新披露运行日出力, 检修计划出力上限)；检修缺 → 未检修默认开机 + 假设标注。
- 意向挂牌/摘牌价（24 点）与开机参数由 store 持久化，非本模块职责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import openpyxl

from ..config import A_DAY, D_DAY, DA_SHEETS, DA_XLSX, ROLL_AUCTION_XLSX, RT_SHEETS, RT_XLSX
from ..mock.roll_auction import synth_roll_auction_24

N = 96
# 运行日（D 日）不可披露项 → 最近日法（PRD D-5；数据中虽含 08-31 行，按运行日口径以最近披露日替代）
UNDISCLOSED_KEYS = ("非市场化", "水电", "地方燃煤")


@dataclass
class Series:
    """一条 96/24 点序列 + 逐点标注（kind: 披露/推断/补齐/缺输入/意向/计算）。"""
    values: list[float | None]
    kinds: list[str] = field(default_factory=list)
    src_days: list[str | None] = field(default_factory=list)
    notes: list[str | None] = field(default_factory=list)

    @staticmethod
    def make(values: list[float | None], kind: str = "披露", src_day: str | None = None, note: str | None = None) -> "Series":
        n = len(values)
        return Series(values, [kind] * n, [src_day] * n, [note] * n)

    def annotate(self, i: int, kind: str, src_day: str | None = None, note: str | None = None) -> None:
        self.kinds[i] = kind
        self.src_days[i] = src_day
        self.notes[i] = note

    def to_json(self) -> dict:
        return {"values": self.values, "kinds": self.kinds, "srcDays": self.src_days, "notes": self.notes}


@dataclass
class LoadedData:
    """全量装载结果：逐日边界 + 省间滚撮 + 实时侧。"""
    days: list[str]                                   # 升序历史日（含 D 日）
    day_ahead: dict[str, dict[str, Series]]           # day -> sheet -> Series(96)
    realtime: dict[str, dict[str, Series]]            # day -> sheet -> Series
    roll_auction: dict[str, dict[str, list[float]]]   # day -> {volume24, price24}（真实文件或合成占位）
    roll_source: str                                  # 真实文件路径 / 合成占位说明
    warnings: list[str] = field(default_factory=list)


class Loader(Protocol):
    """loader 可替换接口（B 轨外部通道仅替换本协议实现）。"""

    def load(self) -> LoadedData: ...


def read_sheet(path: Path, sheet: str) -> dict[str, list[float | None]]:
    """读取 32×97（或 32×25）sheet → {date: 值列表}；None 保留为缺数。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            return {}
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            return {}
        n_vals = len(header) - 1
        out: dict[str, list[float | None]] = {}
        for r in rows:
            if r is None or r[0] is None or not hasattr(r[0], "year"):
                continue
            key = f"{r[0].year:04d}-{r[0].month:02d}-{r[0].day:02d}"
            vals: list[float | None] = []
            for v in r[1:n_vals + 1]:
                vals.append(round(float(v), 4) if isinstance(v, (int, float)) else None)
            out[key] = vals
        return out
    finally:
        wb.close()


def fill_missing(values: list[float | None]) -> tuple[list[float | None], list[str], list[str | None], list[str | None]]:
    """缺数补齐：均值/插值兜底（人工填数/相似日由修订链与 M2 承担）。
    返回 (values, kinds, src_days, notes)；缺数点有邻可插 → 补齐(线性插值)；无邻 → 缺输入。"""
    n = len(values)
    out = list(values)
    kinds = ["披露" if v is not None else "" for v in values]
    src_days: list[str | None] = [None] * n
    notes: list[str | None] = [None] * n
    idx_known = [i for i, v in enumerate(values) if v is not None]
    for i, v in enumerate(values):
        if v is not None:
            continue
        prev = max((j for j in idx_known if j < i), default=None)
        nxt = min((j for j in idx_known if j > i), default=None)
        if prev is not None and nxt is not None:
            w = (i - prev) / (nxt - prev)
            out[i] = round(values[prev] + w * (values[nxt] - values[prev]), 4)   # type: ignore[index]
            kinds[i] = "补齐"
            notes[i] = "▣补齐(线性插值)"
        elif idx_known:
            avg = sum(values[j] for j in idx_known) / len(idx_known)              # type: ignore[misc]
            out[i] = round(avg, 4)
            kinds[i] = "补齐"
            notes[i] = "▣补齐(全日均值)"
        else:
            kinds[i] = "缺输入"
            notes[i] = "无据可补"
    return out, kinds, src_days, notes


class XlsxLoader:
    """A 轨实现：本地 xlsx → LoadedData（M1 全量口径）。"""

    def __init__(self, da_path: Path = DA_XLSX, rt_path: Path = RT_XLSX, roll_path: Path | None = None) -> None:
        self.da_path = da_path
        self.rt_path = rt_path
        self.roll_path = roll_path if roll_path is not None else ROLL_AUCTION_XLSX

    def load(self) -> LoadedData:
        warnings: list[str] = []
        raw_da = {s: read_sheet(self.da_path, s) for s in DA_SHEETS}
        raw_rt = {s: read_sheet(self.rt_path, s) for s in RT_SHEETS}

        days = sorted(raw_da["负荷"].keys())
        if not days:
            raise RuntimeError(f"日前边界缺负荷数据: {self.da_path}")
        if days[-1] != D_DAY:
            warnings.append(f"最新日 {days[-1]} ≠ 配置 D 日 {D_DAY}，按数据实际日期继续")

        # ---- 日前边界逐日入库（补齐 + 标注）----
        day_ahead: dict[str, dict[str, Series]] = {}
        for day in days:
            day_ahead[day] = {}
            for sheet, by_day in raw_da.items():
                if not by_day:
                    continue
                n_raw = len(by_day[day]) if day in by_day else 0
                if day not in by_day or n_raw == 0:
                    day_ahead[day][sheet] = Series.make([None] * N, "缺输入", note="当日缺文件/缺行")
                    continue
                vals = (by_day[day] + [None] * N)[:N]
                if n_raw == 24:      # 24 点原生 sheet（如 24点平均日前负荷率）保持 24 点
                    vals = (by_day[day] + [None] * 24)[:24]
                vals, kinds, srcs, notes = fill_missing(vals)
                day_ahead[day][sheet] = Series(vals, kinds, srcs, notes)

        # ---- 运行日（D 日）不可披露项：最近日法（取 A 日披露近似替代 + 标注来源日）----
        for key in UNDISCLOSED_KEYS:
            if D_DAY in day_ahead and key in day_ahead[D_DAY]:
                src = day_ahead[A_DAY][key]
                tgt = day_ahead[D_DAY][key]
                for i in range(min(len(tgt.values), len(src.values))):
                    tgt.values[i] = src.values[i]
                    tgt.annotate(i, "推断", A_DAY, f"运行日不披露，最近日法取自 {A_DAY}")

        # ---- 核电预测 = min(最新披露运行日出力, 检修计划出力上限)；检修缺 → 假设标注 ----
        nuc = day_ahead[D_DAY].get("核电")
        if nuc is not None:
            for i in range(N):
                if nuc.values[i] is not None:
                    nuc.annotate(i, "推断", D_DAY, "核电预测 = min(最新披露运行日出力, 检修上限=∞（检修计划缺，未检修默认开机）)")

        # ---- 实时侧 ----
        realtime: dict[str, dict[str, Series]] = {}
        for sheet, by_day in raw_rt.items():
            if not by_day:
                continue
            for day, vals_raw in by_day.items():
                vals = (vals_raw + [None] * N)[:N]
                vals, kinds, srcs, notes = fill_missing(vals)
                realtime.setdefault(day, {})[sheet] = Series(vals, kinds, srcs, notes)

        # ---- 省间滚撮：真实文件优先（A-0 替换位），否则确定性合成占位 ----
        roll: dict[str, dict[str, list[float]]] = {}
        if self.roll_path.exists():
            roll_source = f"真实文件 {self.roll_path}"
            vol_by_day = read_sheet(self.roll_path, "成交量")
            price_by_day = read_sheet(self.roll_path, "价格")
            for day in days:
                v24 = (vol_by_day.get(day, []) + [0.0] * 24)[:24]
                p24 = (price_by_day.get(day, []) + [0.0] * 24)[:24]
                roll[day] = {"volume24": v24, "price24": p24}
        else:
            roll_source = "确定性合成占位（省间滚撮 24 点本地暂缺，A-0 待业务方提供）"
            for day in days:
                tie = day_ahead[day]["联络线"].values
                v24, p24 = synth_roll_auction_24(tie)
                roll[day] = {"volume24": v24, "price24": p24}
            warnings.append(roll_source)

        return LoadedData(days=days, day_ahead=day_ahead, realtime=realtime,
                          roll_auction=roll, roll_source=roll_source, warnings=warnings)


def expand24to96(v24: list[float | None]) -> list[float | None]:
    """24→96 展开：小时值复制 4 份（业务方确认按复制实现）。"""
    out: list[float | None] = []
    for v in v24:
        out.extend([v] * 4)
    return out


def avg96to24(v96: list[float | None]) -> list[float | None]:
    """96→24 合成：每 4 点等权算术平均（缺数跳过；组内全缺 → None）。"""
    out: list[float | None] = []
    for i in range(24):
        seg = [x for x in v96[i * 4:i * 4 + 4] if x is not None]
        out.append(round(sum(seg) / len(seg), 6) if seg else None)
    return out
