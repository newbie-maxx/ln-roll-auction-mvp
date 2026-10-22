"""M-02 预测输出落档（D-A6）与取数：只读快照、永不重算；历史日重放并标「重放值」。

capture = 「交易结束」动作：调既有 Pipeline 全链（import 复用，不改 pipeline.py），
带实际生效参数与真版本号落档；同 (run_date, trade_date) 再点走软生效（旧快照不删）。
get_or_replay = 复盘取数：live 生效快照优先；8 月历史日无快照 → 重放落库标 source=replay。
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from ..pipeline import Pipeline
from ..store import Store
from . import constants as C
from .store import RetroStore

SNAPSHOT_ITEMS = ("pred1", "pred2", "final", "M6联络线", "M7开机", "M7负荷率")


def _items_from_result(r) -> dict[str, list[float | None]]:
    return {
        "pred1": list(r.m8["pred1_96"]),
        "pred2": list(r.m8["pred2_96"]),
        "final": list(r.m8["final_96"]),
        "M6联络线": list(r.m6["predicted_96"]),
        "M7开机": list(r.m7["final_on_96"]),
        "M7负荷率": list(r.m7["load_rate_96"]),
    }


def _meta_from_result(r) -> dict:
    return {"used_pred2": int(r.m8["used_pred2_96"]), "k": r.m8["k"], "M1": r.m8["M1"],
            "C1": r.m8["C1"], "M2": r.m8["M2"], "C2": r.m8["C2"]}


def capture(run_date: str, store: RetroStore, trade_date: str | None = None,
            work_db: Path | None = None) -> dict:
    """交易结束：全链重算 + 落档（live）。work_db 允许把 boundary_master 落到指定库。"""
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    pipe = Pipeline(store=Store(str(work_db)) if work_db else Store())
    try:
        pipe.set_target_day(run_date)
        r = pipe.run_all()
    finally:
        pipe.store.close()
    items, meta = _items_from_result(r), _meta_from_result(r)
    params_json = r.params.as_dict()
    seq = store.snapshot_capture(
        run_date, trade_date, items, meta,
        json.dumps(params_json, ensure_ascii=False, sort_keys=True),
        C.RETRO_CODE_VERSION, source="live")
    return {"run_date": run_date, "trade_date": trade_date, "seq": seq,
            "params": params_json, "code_version": C.RETRO_CODE_VERSION}


def _replay(run_date: str, store: RetroStore) -> dict:
    """历史日无 live 快照：报告生成时自动重放，落库标 source=replay（非当时值）。"""
    with tempfile.TemporaryDirectory() as td:
        pipe = Pipeline(store=Store(str(Path(td) / "replay.db")))
        try:
            pipe.set_target_day(run_date)
            r = pipe.run_all()
        finally:
            pipe.store.close()
    items, meta = _items_from_result(r), _meta_from_result(r)
    params_json = r.params.as_dict()
    store.snapshot_capture(
        run_date, run_date, items, meta,
        json.dumps(params_json, ensure_ascii=False, sort_keys=True),
        C.RETRO_CODE_VERSION, source="replay")
    return {item: store.snapshot_effective(run_date, item, source="replay")
            for item in SNAPSHOT_ITEMS}


def get_or_replay(run_date: str, store: RetroStore,
                  items: tuple[str, ...] = SNAPSHOT_ITEMS) -> dict:
    """总账基准取数（S4）：live 生效快照优先；缺则重放（标 source=replay）。"""
    out: dict[str, dict | None] = {}
    need_replay = False
    for item in items:
        snap = store.snapshot_effective(run_date, item, source="live")
        if snap is None:
            need_replay = True
            break
        out[item] = snap
    if need_replay:
        replayed = _replay(run_date, store)
        for item in items:
            out[item] = replayed.get(item)
    out["_meta"] = {
        "source": out.get("final", {}).get("source", "live") if out.get("final") else "live",
        "is_replay": out.get("final", {}).get("source") == "replay" if out.get("final") else False,
        "trade_date": out.get("final", {}).get("trade_date") if out.get("final") else None,
    }
    return out


def replay_marker() -> str:
    return "重放值，非当时值"
