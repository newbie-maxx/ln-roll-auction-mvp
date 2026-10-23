"""M-01 实况接入：直读实时边界 xlsx 的 8 类边界 + 实时电价 + 负荷率（data/ 只读）。

口径：缺数显式标缺、不插值、不补 0；实时开机/开机容量不作实况（§E-A1）；
物理越界（负荷<0、电价越界）该点标异常缺失。装载幂等：同日数据未变不新增 load_seq。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import RT_XLSX
from ..loaders.xlsx_loader import expand24to96, read_sheet
from . import constants as C
from .store import RetroStore


def _rt_sources() -> tuple[Path, ...]:
    """实况源文件：基线 RT_XLSX + 全部上传实时表（后传覆盖同日）。"""
    from ..loaders.upload_manager import UploadManager
    return (RT_XLSX, *UploadManager().sources("realtime"))


def _rt_cache_key() -> tuple:
    return tuple((str(p), p.stat().st_mtime_ns) for p in _rt_sources() if p.exists())


@lru_cache(maxsize=8)
def _rt_sheets_cached(key: tuple) -> dict[str, dict[str, list[float | None]]]:
    out: dict[str, dict[str, list[float | None]]] = {}
    for sheet in C.RT_RETRO_SHEETS:
        data: dict[str, list[float | None]] = {}
        for p in _rt_sources():                 # 时间序合并：上传覆盖基线同日
            data = {**data, **read_sheet(p, sheet)}
        if data:
            out[sheet] = data
    return out


def _rt_sheets() -> dict[str, dict[str, list[float | None]]]:
    """按源文件 mtime 键控缓存（上传新表后自动重读，无需重启进程）。"""
    return _rt_sheets_cached(_rt_cache_key())


def _validate(item: str, v: float | None) -> tuple[float | None, bool]:
    """物理校验：非法值置 None 并标异常缺失。"""
    if v is None:
        return None, True
    if item == "实时电价" and not (C.SPOT_FLOOR * 15 <= v <= C.SPOT_CAP * 2):
        return None, True          # 电价远超物理界（下限*15 / 上限*2 容忍度）
    if item == "负荷" and v <= 0:
        return None, True
    if item in ("风电", "光伏") and v < -100:
        return None, True          # 允许少量负出力，深负视为异常
    return v, False


def actual_day(run_date: str) -> dict:
    """一日实况：{item: {values(96), missing_count, n_points}}；sheet 缺整项→missing_full。"""
    sheets = _rt_sheets()
    out: dict[str, dict] = {}
    for item in C.RT_RETRO_SHEETS:
        day = sheets.get(item, {}).get(run_date)
        if day is None:
            out[item] = {"values": [None] * 96, "missing_count": 96, "missing_full": True}
            continue
        raw = day if len(day) >= 96 else expand24to96(day)   # 负荷率 24 点复制展 96
        vals, miss = [], 0
        for v in raw[:96]:
            v2, bad = _validate(item, v)
            vals.append(v2)
            miss += int(v2 is None)
        out[item] = {"values": vals, "missing_count": miss, "missing_full": miss == 96}
    return out


def availability(start: str, end: str) -> list[str]:
    """可用日清单：8 类边界齐全（非全缺）且实时电价有值（valid 口径同 loaders）。"""
    sheets = _rt_sheets()
    price_days = {d for d, vs in sheets.get("实时电价", {}).items()
                  if any(v is not None for v in vs)}
    days: list[str] = []
    for d in sorted(price_days):
        if not (start <= d <= end):
            continue
        ok = all(
            any(v is not None for v in sheets.get(b, {}).get(d, []))
            for b in C.BOUNDARIES8)
        if ok:
            days.append(d)
    return days


def load_day(run_date: str, store: RetroStore) -> dict:
    """装载并落 D-A5（幂等：最新 load_seq 数据一致则复用，不新增版本）。"""
    day = actual_day(run_date)
    last_seq = store.conn.execute(
        "SELECT MAX(load_seq) AS s FROM retro_actual WHERE run_date = ?", (run_date,)).fetchone()["s"]
    if last_seq is not None:
        prev = store.conn.execute(
            "SELECT item, t, value, missing FROM retro_actual WHERE run_date = ? AND load_seq = ? "
            "ORDER BY item, t", (run_date, last_seq)).fetchall()
        cur = {(item, i + 1): (round(v["values"][i], 4) if v["values"][i] is not None else None,
                               int(v["values"][i] is None))
               for item, v in day.items() for i in range(96)}
        same = len(prev) == len(cur) and all(
            (r["item"], r["t"]) in cur and r["value"] == cur[(r["item"], r["t"])][0]
            and r["missing"] == cur[(r["item"], r["t"])][1] for r in prev)
        if same:
            return {"run_date": run_date, "load_seq": last_seq, "reused": True,
                    "items": sorted(day.keys()), "missing_count": sum(
                        v["missing_count"] for v in day.values())}
    seq = (last_seq or 0) + 1
    rows = [{"run_date": run_date, "item": item, "t": t + 1, "value": v["values"][t],
             "missing": int(v["values"][t] is None), "load_seq": seq, "source": "xlsx"}
            for item, v in sorted(day.items()) for t in range(96)]
    store.append_batch("retro_actual", rows)
    return {"run_date": run_date, "load_seq": seq, "reused": False,
            "items": sorted(day.keys()),
            "missing_count": sum(v["missing_count"] for v in day.values())}


def rt_price(run_date: str) -> list[float | None]:
    return actual_day(run_date)["实时电价"]["values"]


def actual_series(run_date: str, item: str) -> list[float | None]:
    return actual_day(run_date)[item]["values"]
