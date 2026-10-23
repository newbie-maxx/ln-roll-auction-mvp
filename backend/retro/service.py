"""复盘编排服务：单日上下文组装（供 MCP 原子工具与报告编排复用）+ 报告生成 + 导出留档。

生成流程（§8.13）：校验起止日显式指定 → 算 N_eff → 选 R1/R2 → 组装各环节 → 渲染 →
词表扫描（命中拒导）→ 数字回查 100% → 出 .md/.csv + PNG → sha256 → 落 D-A8。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from ..config import EXPORT_DIR
from ..store import Store
from . import aggregate, attribute, delta as delta_mod, decomp, evidence, focus, jump, portrait
from . import register as register_mod
from . import report as report_mod
from .actual import actual_day, availability
from .snapshot import get_or_replay
from .store import RetroStore, now_iso
from .words import scan


def build_day_context(run_date: str, retro: RetroStore, real_store: Store,
                      tmpdir: str | Path, engine: decomp.DecompEngine | None = None) -> dict:
    """组装单日全量上下文（A1/B/C/D/X-9/G 各环节一次跑齐）。"""
    theta = retro.threshold_of(run_date)
    delta_result = delta_mod.compute(run_date, retro, real_store)
    eng = engine or decomp.DecompEngine(tmpdir)
    own_engine = engine is None
    try:
        dec = eng.run(run_date)
        inter96 = delta_result["inter_actual_96"]

        def verify(factors: list[str]):
            r = eng.verify_combo(run_date, factors, inter96)
            return list(r.m8["final_96"])

        jump_result = jump.classify(run_date, dec, delta_result, theta, verify_fn=verify)
    finally:
        if own_engine:
            eng.close()
    focus_result = focus.build(
        run_date, delta_result, jump_result, theta,
        revisions=[r for r in real_store.all_revisions() if r.get("boundary_id") == run_date],
        register_ts=[r["t"] for r in register_mod.day_records(run_date, retro)])
    split = portrait.split(run_date, delta_result, dec, real_store)
    revs = [r for r in real_store.all_revisions() if r.get("boundary_id") == run_date]
    comp = portrait.compensate(run_date, delta_result, revs, dec)
    comp["items"] = portrait.disclosure_induced_mark(comp["items"], retro)
    snap = get_or_replay(run_date, retro)
    da_on = _da_on96(run_date)
    act_tie_base = [(a - b) if (a is not None and b is not None) else None
                    for a, b in zip(actual_day(run_date)["联络线"]["values"], inter96)]
    st = register_mod.settle_day(run_date, retro)
    bands: dict[int, str] = {}
    for r in st["rows"]:
        cur = bands.get(r["t"])
        bands[r["t"]] = ("filled" if r["result_type"] == "成交" else
                         ("unfilled" if cur != "filled" else cur))
    # 交易日（场次）口径（§1.7.7）：live 快照场次 ∪ 登记关联场次；多场次时给版本线数据（S7）
    live_sessions = retro.snapshot_sessions(run_date, source="live")
    reg_sessions = sorted({r["snapshot_trade_date"] for r in st["rows"] if r.get("snapshot_trade_date")})
    all_sessions = sorted(set(live_sessions) | set(reg_sessions))
    session_lines = []
    if len(live_sessions) > 1:
        for td in live_sessions:
            ln = retro.snapshot_by_session(run_date, td, "final", source="live")
            if ln:
                session_lines.append({"trade_date": td, "final": ln["values"]})
    return {
        "run_date": run_date, "theta": theta, "delta": delta_result, "dec": dec,
        "jump": jump_result, "focus": focus_result, "portrait": split,
        "live_sessions": live_sessions, "sessions": all_sessions,
        "session_lines": session_lines,
        "base_trade_date": snap["_meta"].get("trade_date"),
        "compensate": comp, "register": st, "register_ts": sorted(bands),
        "bands_by_hour": bands, "revisions": revs,
        "snapshot": {k: (snap[k]["values"] if snap.get(k) else [None] * 96)
                     for k in ("final", "pred1", "pred2", "M6联络线", "M7开机", "M7负荷率")},
        "snapshot_meta": snap["_meta"], "da_on_96": da_on, "act_tie_base_96": act_tie_base,
        "disclosure_quality": portrait.disclosure_quality(run_date, retro),
        "generated_at": now_iso(),
    }


def _da_on96(run_date: str) -> list[float | None]:
    from ._pipeline_util import base_data
    da = base_data().day_ahead.get(run_date, {})
    s = da.get("日前开机")
    if s is None:
        return [None] * 96
    vals = list(s.values)
    return vals if len(vals) >= 96 else aggregate.expand24(vals)


def generate(start: str, end: str, retro: RetroStore, real_store: Store,
             owner: str = "default", use_llm: bool | None = None) -> dict:
    """报告生成主入口（skill retro_day / retro_range 调此原子工具）。"""
    if not start or not end:
        raise ValueError("起止日必须显式指定（不预选区间）")
    if start > end:
        raise ValueError("起止日须满足 start ≤ end")
    days = availability(start, end)
    if not days:
        raise ValueError("区间内无有效日（实况 8 类不齐或无实时电价）")
    if len(days) > 180:
        raise ValueError("区间超上限 180 天，请改用明细导出")
    rtype = "R1" if len(days) == 1 else "R2"
    report_id = f"retro_{rtype}_{start.replace('-', '')}_{end.replace('-', '')}_{now_iso().replace(':', '').replace('-', '').replace('T', '').replace('+', '_')}"
    out_dir = Path(EXPORT_DIR) / "retro" / report_id
    fig_dir = out_dir / "figs"
    with tempfile.TemporaryDirectory() as td:
        contexts: list[dict] = []
        failed_days: list[dict] = []
        for d in days:
            try:
                contexts.append(build_day_context(d, retro, real_store, td))
            except Exception as e:   # noqa: BLE001 —— M-05：该日标分解失败，不中断整份报告
                failed_days.append({"date": d, "error": f"{type(e).__name__}: {e}"})
    if not contexts:
        raise ValueError(f"区间内全部运行日分解失败：{failed_days[:2]}")
    pack = None
    ai_text = ""
    if rtype == "R1":
        day = contexts[0]
        pack = evidence.build(
            day["run_date"], day["dec"], day["jump"], day["delta"],
            manual_items=[{"id": f"M-REV-{i+1:02d}", "type": "边界修订", "boundary": r["boundary_type"],
                           "revised": r["revised_value"], "reason": r["reason"]}
                          for i, r in enumerate(day["revisions"])]
            + ([{"id": f"M-INTENT-01", "type": "省间交易预测"}] if day["delta"]["view"]["联络线"]["inter_revised"][0]
               else []),
            params_meta={"theta": day["theta"]})
        att = attribute.generate(pack, use_llm=use_llm)
        ai_text = att["text"] if not att["degraded"] else att["text"]
        day["ai_text"] = ai_text
        day["sample_hint"] = None
        rendered = report_mod.render_single(day, fig_dir, ai_text=ai_text,
                                        register_values=evidence.all_values(pack))
        n_eff = 1
    else:
        rendered = report_mod.render_range(contexts, start, end, fig_dir, n_eff=len(contexts))
        n_eff = len(contexts)
    # 词表扫描（命中即拒导）
    word_check = scan(rendered["md"])
    if not word_check["ok"]:
        raise ValueError("词表扫描命中，拒绝导出：" +
                         json.dumps(word_check["hits"], ensure_ascii=False)[:500])
    # 数字回查 100%
    unmatched = rendered["registry"].check(rendered["md"])
    if unmatched:
        raise ValueError(f"数字一致性校验未过（{len(unmatched)} 个数字不可回查），拒绝导出：{unmatched[:10]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{report_id}.md"
    md_path.write_text(rendered["md"], encoding="utf-8")
    csv_path = _write_csv(out_dir, report_id, contexts, rtype)
    sha = hashlib.sha256(md_path.read_bytes()).hexdigest()
    retro.append("retro_report", {
        "report_id": report_id, "rtype": rtype, "d_start": start, "d_end": end,
        "n_eff": n_eff, "template_id": f"retro_{rtype}", "template_version": "v2.3",
        "content_json": json.dumps(
            {"pred_source": contexts[0]["delta"]["pred_source"],
             "four_rows": [c["portrait"]["four_rows_price"] for c in contexts[:1]],
             "pack": pack}, ensure_ascii=False, sort_keys=True)[:200000],
        "fmt": "md+csv+png", "file_name": md_path.name, "sha256": sha,
        "generated_by": owner, "generated_at": now_iso()})
    if pack is not None:
        retro.append("retro_snapshot", {
            "owner": owner, "run_date": contexts[0]["run_date"], "period": None,
            "pack_version": "v1", "pack_json": evidence.dumps(pack),
            "ai_text": ai_text, "model": "stub-or-llm",
            "params_json": json.dumps(contexts[0]["theta"], ensure_ascii=False),
            "check_result": json.dumps({"numbers": "100%", "words": "pass"}, ensure_ascii=False)})
    return {"report_id": report_id, "rtype": rtype, "n_eff": n_eff,
            "files": [str(md_path), str(csv_path)],
            "figs": sorted(str(p) for p in fig_dir.glob("*.png")),
            "sha256": sha, "dir": str(out_dir)}


def _write_csv(out_dir: Path, report_id: str, contexts: list[dict], rtype: str) -> Path:
    import csv
    path = out_dir / f"{report_id}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["日期", "点号", "时刻", "实际出清价", "当时预测价", "价差", "异常标记"])
        for day in contexts:
            tb = day["delta"]["table"]["电价"]
            for i in range(96):
                mark = day["jump"]["points"][i]["jump_type"]
                w.writerow([day["run_date"], i + 1, f"{i // 4:02d}:{(i % 4) * 15:02d}",
                            tb["v_act"][i], tb["v_rev"][i], tb["delta"][i], mark])
    return path
