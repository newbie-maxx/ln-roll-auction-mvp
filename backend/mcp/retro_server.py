"""计算类 MCP（独立进程）：复盘原子计算工具。仅原子操作；编排逻辑在 skill SOP。

每个工具自包含（需要的上下文内部构建），可绕过 skill 层独立调用验证。
"""
from __future__ import annotations

import json
import os
import tempfile

from ..retro import attribute, delta as delta_mod, decomp, evidence, focus, jump, portrait
from ..retro import aggregate, register as register_mod, service, words
from ..retro.actual import availability
from ..retro.demo import demo_seed
from ..retro.snapshot import capture as snap_capture, get_or_replay
from ..retro.store import RetroStore
from ..store import Store
from .server import McpServer

app = McpServer("retro-calc", "1.0.0")

_retro: RetroStore | None = None
_real = None


def _r() -> RetroStore:
    global _retro
    if _retro is None:
        _retro = RetroStore(os.environ.get("WB_RETRO_DB"))
    return _retro


def _s():
    global _real
    if _real is None:
        _real = Store()
    return _real


_STR = {"type": "string"}
_NUM = {"type": "number"}


@app.tool("actual.availability", "可用日清单（8 类边界齐全且有实时电价）", {
    "type": "object", "properties": {"start": _STR, "end": _STR},
    "required": ["start", "end"]})
def t_avail(start: str, end: str) -> list[str]:
    return availability(start, end)


@app.tool("actual.load_day", "装载并落一日实况（D-A5，幂等）", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_load(run_date: str) -> dict:
    from ..retro import actual
    return actual.load_day(run_date, _r())


@app.tool("snapshot.capture", "交易结束：全链重算 + D-A6 落档（软生效）", {
    "type": "object", "properties": {"run_date": _STR, "trade_date": _STR},
    "required": ["run_date"]})
def t_capture(run_date: str, trade_date: str | None = None) -> dict:
    return snap_capture(run_date, _r(), trade_date)


@app.tool("snapshot.get_or_replay", "总账基准取数（live 优先，缺则重放标注）", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_get_or_replay(run_date: str) -> dict:
    snap = get_or_replay(run_date, _r())
    meta = snap.pop("_meta")
    return {"meta": meta,
            "items": {k: (v or {}).get("values") for k, v in snap.items()}}


@app.tool("register.validate", "成交登记录入口径校验（6 条）", {
    "type": "object", "properties": {"record": {"type": "object"}}, "required": ["record"]})
def t_reg_validate(record: dict) -> dict:
    return {"errors": register_mod.validate(record)}


@app.tool("register.save", "登记保存（append-only；重复键=修订 version+1；自动带场次）", {
    "type": "object", "properties": {"record": {"type": "object"}, "trader_id": _STR},
    "required": ["record"]})
def t_reg_save(record: dict, trader_id: str = "default") -> dict:
    return register_mod.save(record, _r(), trader_id)


@app.tool("register.import_csv", "CSV 批量导入（整批拒绝）", {
    "type": "object", "properties": {"text": _STR, "trader_id": _STR}, "required": ["text"]})
def t_reg_csv(text: str, trader_id: str = "default") -> dict:
    return register_mod.import_csv(text, _r(), trader_id)


@app.tool("register.settle_day", "单日逐笔收益（逐点结算）+ 两行小计", {
    "type": "object", "properties": {"run_date": _STR, "trader_id": _STR},
    "required": ["run_date"]})
def t_settle(run_date: str, trader_id: str = "default") -> dict:
    return register_mod.settle_day(run_date, _r(), trader_id)


@app.tool("register.day_list", "当日登记清单（最新 version）", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_day_list(run_date: str) -> list[dict]:
    return register_mod.day_records(run_date, _r())


@app.tool("register.demo_seed", "模拟成交登记生成器（四类日 + 多场次，幂等）", {
    "type": "object", "properties": {"start": _STR, "end": _STR, "seed": {"type": "integer"}},
    "required": ["start", "end"]})
def t_demo(start: str, end: str, seed: int = 20260801) -> dict:
    return demo_seed(_r(), start, end, seed)


@app.tool("delta.compute", "偏差表（96×8+省间+电价，落 D-A1）", {
    "type": "object", "properties": {"run_date": _STR, "detail": {"type": "boolean"}},
    "required": ["run_date"]})
def t_delta(run_date: str, detail: bool = False) -> dict:
    res = delta_mod.compute(run_date, _r(), _s())
    if not detail:
        table = res.pop("table")
        view = res.pop("view")
        res["items"] = sorted(table.keys())
        res["day_mean_delta"] = {k: _mean(v["delta"]) for k, v in table.items()}
        res["missing_total"] = sum(sum(tb["missing"]) for tb in table.values())
    return res


def _mean(xs) -> float | None:
    ys = [x for x in xs if x is not None]
    return round(sum(ys) / len(ys), 2) if ys else None


@app.tool("decomp.run", "8+2 次全链分解：c_b/share/残差/explained_ratio", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_decomp(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        eng = decomp.DecompEngine(td)
        try:
            res = eng.run(run_date)
        finally:
            eng.close()
    res.pop("c", None)      # 96×9 逐点矩阵过大，原子工具只回日级聚合（report 内部自取全量）
    return res


@app.tool("jump.classify", "跃变五步判定 + 缺口倒推 + 类型三 + F1/F2/F3", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_jump(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    return {"run_date": run_date, "points": d["jump"]["points"],
            "jump_points": d["jump"]["jump_points"], "day_type2": d["jump"]["day_type2"],
            "day_type3": d["jump"]["day_type3"], "anomaly": d["jump"]["anomaly"],
            "p_set_count": d["jump"]["p_set_count"], "p_set_ratio": d["jump"]["p_set_ratio"]}


@app.tool("focus.build", "P/T/D/S 集与优先级", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_focus(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    return d["focus"]


@app.tool("evidence.build", "证据包组装（封闭数字集，逐字段可复现）", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_evidence(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    revs = d["revisions"]
    pack = evidence.build(run_date, d["dec"], d["jump"], d["delta"],
                          manual_items=[{"id": f"M-REV-{i+1:02d}", "type": "边界修订",
                                         "boundary": r["boundary_type"],
                                         "revised": r["revised_value"], "reason": r["reason"]}
                                        for i, r in enumerate(revs)])
    _r().append("retro_snapshot", {
        "owner": "default", "run_date": run_date, "period": None, "pack_version": "v1",
        "pack_json": evidence.dumps(pack), "ai_text": None, "model": "none",
        "params_json": json.dumps(d["theta"], ensure_ascii=False),
        "check_result": json.dumps({"numbers": "100%", "words": "pass"}, ensure_ascii=False)})
    return pack


@app.tool("attribute.generate", "AI 归因四段（真模型→校验→桩降级）", {
    "type": "object", "properties": {"run_date": _STR, "use_llm": {"type": "boolean"}},
    "required": ["run_date"]})
def t_attr(run_date: str, use_llm: bool | None = None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    pack = evidence.build(run_date, d["dec"], d["jump"], d["delta"])
    return attribute.generate(pack, use_llm=use_llm)


@app.tool("attribute.validate_text", "溯源/方向/越界三校验（独立可调）", {
    "type": "object", "properties": {"text": _STR, "pack": {"type": "object"}},
    "required": ["text", "pack"]})
def t_attr_check(text: str, pack: dict) -> dict:
    return attribute.validate_text(text, pack)


@app.tool("portrait.split", "四行分账 + 联络 = 两段 + 可加性校验", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_split(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    return {k: v for k, v in d["portrait"].items() if k != "rows"} | {
        "n_rows": len(d["portrait"]["rows"])}


@app.tool("portrait.compensate", "修正有效性 A~E 五类 + CA1–CA3", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_comp(run_date: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        d = service.build_day_context(run_date, _r(), _s(), td)
    return d["compensate"]


@app.tool("portrait.disclosure_quality", "跨场次披露质量统计（不进四行账）", {
    "type": "object", "properties": {"run_date": _STR}, "required": ["run_date"]})
def t_dq(run_date: str) -> dict:
    return portrait.disclosure_quality(run_date, _r())


@app.tool("aggregate.to24", "96→24 区间均值（按实有数据）", {
    "type": "object", "properties": {"values96": {"type": "array", "items": {}}},
    "required": ["values96"]})
def t_to24(values96: list) -> list:
    return aggregate.to24([None if v is None else float(v) for v in values96])


@app.tool("aggregate.expand24", "24→96 只复制不插值（阶梯）", {
    "type": "object", "properties": {"values24": {"type": "array", "items": {}}},
    "required": ["values24"]})
def t_expand(values24: list) -> list:
    return aggregate.expand24([None if v is None else float(v) for v in values24])


@app.tool("words.scan", "措辞词表扫描（命中即拒导）", {
    "type": "object", "properties": {"text": _STR}, "required": ["text"]})
def t_scan(text: str) -> dict:
    return words.scan(text)


@app.tool("report.render", "报告生成主入口（R1/R2 + 词表 + 数字回查 + 导出留档）", {
    "type": "object", "properties": {"start": _STR, "end": _STR,
                                     "use_llm": {"type": "boolean"}},
    "required": ["start", "end"]})
def t_report(start: str, end: str, use_llm: bool | None = None) -> dict:
    return service.generate(start, end, _r(), _s(), use_llm=use_llm)


@app.tool("retro.status", "复盘数据就绪度（可用日数/登记概况/快照概况）", {
    "type": "object", "properties": {"start": _STR, "end": _STR},
    "required": ["start", "end"]})
def t_status(start: str, end: str) -> dict:
    days = availability(start, end)
    regs = _r().query("retro_trade_register", order="run_date")
    snaps = _r().conn.execute(
        "SELECT run_date, source, COUNT(*) AS n FROM calc_snapshot WHERE effective = 1 "
        "GROUP BY run_date, source ORDER BY run_date").fetchall()
    return {"available_days": len(days), "first": days[:1], "last": days[-1:],
            "register_rows": len(regs),
            "register_days": sorted({r["run_date"] for r in regs}),
            "snapshots": [dict(r) for r in snaps]}


if __name__ == "__main__":
    app.serve()
