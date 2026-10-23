"""M-03 成交登记（D-A9）与逐笔收益（X-A34 逐点结算口径，写死不得改）。

结算口径：滚撮价是一小时的价，该小时 4 个 15 分钟点全部按此价结算；与 96 点现货
逐点相减再累加（卖 Σ(价−该点现货)×该点电量、买 Σ(该点现货−价)×该点电量），
电量按 1/4 均摊。禁止取小时均值/中位数/首点等「代表价」；缺任一点现货 → 该笔不出金额。
已成交溢价与机会成本估算（溢价未实现）两行分开，不合并净额；单日版可加
「两者相加＝当日执行溢价」一行，多日版不出。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date

from . import constants as C
from .actual import rt_price
from .store import RetroStore, now_iso

CSV_COLUMNS = ("运行日", "时段", "结果", "方向", "价格", "电量")


def _valid_date(s: str) -> bool:
    try:
        date.fromisoformat(str(s))
        return True
    except ValueError:
        return False


def validate(record: dict) -> list[str]:
    """录入口径校验（6 条 + 可选交易日场次）；返回错误列表（空=通过）。"""
    errs: list[str] = []
    td = record.get("trade_date")
    if td is not None:
        if not _valid_date(str(td)):
            errs.append("交易日（场次）须为 YYYY-MM-DD")
        elif str(td) > date.today().isoformat():
            errs.append("交易日（场次）不能晚于今天（交易日是已发生的日历日）")
        elif _valid_date(str(record.get("run_date", ""))) and str(td) >= str(record["run_date"]):
            errs.append("交易日（场次）须早于运行日（交付日在交易之后）")
    if not _valid_date(str(record.get("run_date", ""))):
        errs.append("运行日无效（须 YYYY-MM-DD）")
    t = record.get("t")
    if not isinstance(t, int) or not (0 <= t <= 23):
        errs.append("时段必填且须为 0–23 的整数（小时）")
    rt = record.get("result_type")
    if rt not in C.RESULT_TYPES:
        errs.append(f"结果类型只能是 {'/'.join(C.RESULT_TYPES)} 其一")
    d = record.get("direction")
    if d not in C.DIRECTIONS:
        errs.append("方向必填且只能是 买/卖")
    p = record.get("price")
    if not isinstance(p, (int, float)) or not (C.PRICE_RANGE[0] <= p <= C.PRICE_RANGE[1]):
        errs.append(f"价格须在 {C.PRICE_RANGE[0]}~{C.PRICE_RANGE[1]} 元/MWh")
    v = record.get("volume_mwh")
    if not isinstance(v, (int, float)) or not (C.VOLUME_RANGE[0] < v <= C.VOLUME_RANGE[1]):
        errs.append(f"电量须大于 0 且不超过 {C.VOLUME_RANGE[1]} MWh")
    return errs


def settle(price: float, volume_mwh: float, direction: str, hour: int,
           spot96: list[float | None]) -> dict:
    """逐点结算：返回 {settle_points, diffs, pnl(缺任一点为 None), per_point_qty}。"""
    pts = [spot96[hour * 4 + i] for i in range(4)]
    per = volume_mwh / 4.0
    if any(p is None for p in pts):
        return {"settle_points": pts,
                "diffs": [None if p is None else round(p - price if direction == "买" else price - p, 4)
                          for p in pts],
                "pnl": None, "per_point_qty": per}
    diffs = [(p - price) if direction == "买" else (price - p) for p in pts]   # type: ignore[operator]
    pnl = round(sum(d * per for d in diffs), 2)
    return {"settle_points": pts, "diffs": [round(d, 4) for d in diffs],
            "pnl": pnl, "per_point_qty": round(per, 4)}


def save(record: dict, store: RetroStore, trader_id: str = "default") -> dict:
    """落 D-A9（append-only；重复键=修订走 version+1，不双写）；派生 settle_points/pnl。"""
    errs = validate(record)
    if errs:
        return {"ok": False, "errors": errs}
    run_date, t = str(record["run_date"]), int(record["t"])
    spot = rt_price(run_date)
    s = settle(float(record["price"]), float(record["volume_mwh"]), record["direction"], t, spot)
    version = store.next_version(
        "retro_trade_register", ["run_date", "t", "result_type", "trader_id"],
        {"run_date": run_date, "t": t, "result_type": record["result_type"], "trader_id": trader_id})
    store.append("retro_trade_register", {
        "run_date": run_date, "t": t, "result_type": record["result_type"],
        "price": float(record["price"]), "volume_mwh": float(record["volume_mwh"]),
        "direction": record["direction"], "trader_id": trader_id,
        "entered_at": record.get("entered_at") or now_iso(), "note": record.get("note"),
        "source": record.get("source", "自报"),
        "confidence": record.get("confidence", "低于回单"),
        "snapshot_trade_date": record.get("trade_date") or store.latest_effective_trade_date(run_date),
        "settle_points": json.dumps(s["settle_points"], ensure_ascii=False),
        "pnl": s["pnl"], "version": version})
    return {"ok": True, "version": version, "pnl": s["pnl"],
            "is_revision": version > 1,
            "snapshot_trade_date": record.get("trade_date") or store.latest_effective_trade_date(run_date)}


def import_csv(text: str, store: RetroStore, trader_id: str = "default") -> dict:
    """CSV 批量（utf-8-sig）；部分行非法 → 整批拒绝并列出全部非法行。"""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None or any(c not in reader.fieldnames for c in CSV_COLUMNS):
        return {"ok": False, "errors": [f"CSV 缺列，需含 {','.join(CSV_COLUMNS)}"]}
    rows, errs = [], []
    for i, r in enumerate(reader, start=2):   # 第 1 行表头
        rec = {"run_date": (r.get("运行日") or "").strip(), "t": r.get("时段"),
               "result_type": (r.get("结果") or "").strip(), "direction": (r.get("方向") or "").strip()}
        try:
            rec["price"] = float(r.get("价格"))
            rec["volume_mwh"] = float(r.get("电量"))
            rec["t"] = int(float(rec["t"]))
        except (TypeError, ValueError):
            errs.append(f"第 {i} 行：价格/电量/时段须为数字")
            continue
        bad = validate(rec)
        if bad:
            errs.append(f"第 {i} 行：{'；'.join(bad)}")
        else:
            rows.append(rec)
    if errs:
        return {"ok": False, "errors": errs, "n_rejected": len(errs)}
    results = [save(rec, store, trader_id) for rec in rows]
    return {"ok": True, "n_saved": len(results), "results": results}


def day_records(run_date: str, store: RetroStore,
                trader_id: str = "default") -> list[dict]:
    """当日登记（各键最新 version），按 t、result_type 排序。"""
    rows = store.query("retro_trade_register", where={"run_date": run_date}, order="t, id")
    latest: dict[tuple, dict] = {}
    for r in rows:
        if trader_id and r["trader_id"] != trader_id:
            continue
        key = (r["t"], r["result_type"])
        prev = latest.get(key)
        if prev is None or r["version"] > prev["version"]:
            latest[key] = r
    return [latest[k] for k in sorted(latest.keys())]


def range_records(d_start: str, d_end: str, store: RetroStore,
                  trader_id: str = "default") -> list[dict]:
    rows = store.query("retro_trade_register", order="run_date, t, id")
    latest: dict[tuple, dict] = {}
    for r in rows:
        if not (d_start <= r["run_date"] <= d_end):
            continue
        if trader_id and r["trader_id"] != trader_id:
            continue
        key = (r["run_date"], r["t"], r["result_type"])
        prev = latest.get(key)
        if prev is None or r["version"] > prev["version"]:
            latest[key] = r
    return [latest[k] for k in sorted(latest.keys())]


def _explain(rec: dict, s: dict) -> str:
    if s["pnl"] is None:
        return "现货价缺点，该笔不出金额"
    if s["pnl"] >= 0:
        return ("点在区间内，四点全赚" if all(d >= 0 for d in s["diffs"] if d is not None)
                else "正负价混杂")
    return "负值照实呈现（成交价劣于该点现货）" if rec["result_type"] == "成交" \
        else "未成交反而更好（负值不取绝对值）"


def settle_day(run_date: str, store: RetroStore, trader_id: str = "default") -> dict:
    """单日逐笔收益表 + 两行小计（+单日执行溢价行）。"""
    recs = day_records(run_date, store, trader_id)
    spot = rt_price(run_date)
    rows = []
    for rec in recs:
        s = settle(rec["price"], rec["volume_mwh"], rec["direction"], rec["t"], spot)
        rows.append({
            "t": rec["t"], "snapshot_trade_date": rec["snapshot_trade_date"],
            "result_type": rec["result_type"], "direction": rec["direction"],
            "price": rec["price"], "settle_points": s["settle_points"],
            "diffs": s["diffs"], "volume_mwh": rec["volume_mwh"], "pnl": s["pnl"],
            "note": _explain(rec, s)})
    filled = [r for r in rows if r["result_type"] == "成交" and r["pnl"] is not None]
    unfill = [r for r in rows if r["result_type"] == "未成交" and r["pnl"] is not None]

    def _sum(rows_: list[dict]) -> dict:
        return {"n": len(rows_),
                "n_positive": sum(1 for r in rows_ if (r["pnl"] or 0) > 0),
                "volume": round(sum(r["volume_mwh"] for r in rows_), 2),
                "amount": round(sum(r["pnl"] or 0 for r in rows_), 2)}

    total = {"filled": _sum(filled), "unfilled": _sum(unfill)}
    total["day_execution_premium"] = (round(total["filled"]["amount"] + total["unfilled"]["amount"], 2)
                                      if rows else None)   # 仅单日版呈现
    return {"run_date": run_date, "rows": rows, "totals": total,
            "has_any": bool(rows),
            "disclaimer": "依据手工登记，未经回单核对"}
