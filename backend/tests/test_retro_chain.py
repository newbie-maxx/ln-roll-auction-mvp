"""复盘口径用例（二）：全链集成（真实 8 月数据）——偏差/分解闭合/跃变/四行可加性/
快照软生效/登记结算/报告 R1·R2/证据包复现/MCP 独立调用/skill 端到端。

隔离：全部走 tmp 库；config.DB_PATH 动态读取（_pipeline_util.work_store_copy）。
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from backend.retro import delta as delta_mod
from backend.retro import decomp, evidence, service
from backend.retro import register as reg_mod
from backend.retro.snapshot import capture, get_or_replay
from backend.retro.store import RetroStore
from backend.store import Store

D = "2026-08-14"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DB_PATH", tmp_path / "wb.db")
    retro = RetroStore(tmp_path / "retro.db")
    real = Store(str(tmp_path / "wb.db"))
    yield {"retro": retro, "real": real, "tmp": tmp_path}
    retro.close()
    real.close()


def test_availability_august():
    from backend.retro.actual import availability
    days = availability("2026-08-01", "2026-08-31")
    assert len(days) == 31 and days[0] == "2026-08-01" and days[-1] == "2026-08-31"


def test_delta_replay_marker_and_no_interpolation(env):
    d = delta_mod.compute(D, env["retro"], env["real"])
    assert d["is_replay"] is True                      # 8 月历史日：重放值，非当时值
    assert "重放值" in d["pred_source"]
    price = d["table"]["电价"]
    assert all(v is not None for v in price["delta"])  # 电价 96 点全量
    assert all(not m for m in price["missing"])
    i = 0
    while price["v_act"][i] is None or price["v_rev"][i] is None:
        i += 1
    assert abs(price["delta"][i] - round(price["v_act"][i] - price["v_rev"][i], 4)) < 1e-9


def test_decomposition_closure(env):
    with tempfile.TemporaryDirectory() as td:
        eng = decomp.DecompEngine(td)
        try:
            dec = eng.run(D)
        finally:
            eng.close()
    assert len(dec["c"]) == 9                          # 8 边界 + 省间（零贡献自然 near_zero）
    assert dec["closure_max_abs"] is not None and dec["closure_max_abs"] <= 0.01
    assert dec["explained_ratio"] is not None
    for i in (0, 47, 95):                              # 残差恒等：r = ΔP − Σc
        s = sum(v[i] for v in dec["c"].values() if v[i] is not None)
        assert abs(dec["dp_total"][i] - s - dec["residual"][i]) < 1e-6


def test_jump_floor_points_and_order(env, tmp_path):
    d = service.build_day_context(D, env["retro"], env["real"], tmp_path)
    downs = [p for p in d["jump"]["jump_points"]
             if p["jump_type"] == "类型一" and p.get("jump_side") == "down"]
    assert downs, "8-14 应存在触及现货下限的下临界点（22:45—23:45）"
    assert all(p["detail"].get("gap_mw") is not None for p in downs)
    types = {p["jump_type"] for p in d["jump"]["points"]}
    assert types <= {"无", "缺数", "类型一", "类型二", "类型三", "未定性"}
    assert all(a["type"] in ("F1", "F2", "F3") for a in d["jump"]["anomaly"])


def test_portrait_identity_and_four_rows(env, tmp_path):
    d = service.build_day_context(D, env["retro"], env["real"], tmp_path)
    p = d["portrait"]
    assert p["identity_max_mw"] < 1.0                  # T-A20 恒等
    assert p["tie_split_max"] is None or p["tie_split_max"] < 1.0
    assert set(p["four_rows_price"]) == {"系统", "交易中心", "交易员", "未归属"}
    assert abs(p["four_row_closure"]) <= 0.01          # 四行相加 = 总偏差
    rows = p["rows"]
    assert all(r["owner_type"] in ("系统", "交易中心", "交易员", "未归属") for r in rows)
    assert any(r["split_part"] == "基线" for r in rows) and \
        any(r["split_part"] == "省间" for r in rows)   # 联络线拆两段


def test_snapshot_capture_soft_effective(env):
    r1 = capture(D, env["retro"], trade_date="2026-08-13", work_db=env["tmp"] / "cap.db")
    r2 = capture(D, env["retro"], trade_date="2026-08-13", work_db=env["tmp"] / "cap.db")
    assert r2["seq"] == r1["seq"] + 1                  # 同场次多次落档 seq 递增不覆盖
    n_all = env["retro"].conn.execute(
        "SELECT COUNT(*) AS n FROM calc_snapshot WHERE run_date = ?", (D,)).fetchone()["n"]
    assert n_all == 576 * 2                            # 6 items × 96 点 × 2 次，旧快照不删
    eff = env["retro"].conn.execute(
        "SELECT COUNT(*) AS n FROM calc_snapshot WHERE run_date = ? AND effective = 1",
        (D,)).fetchone()["n"]
    assert eff == 576                                  # 仅最新 seq 生效
    snap = get_or_replay(D, env["retro"])
    assert snap["_meta"]["is_replay"] is False         # live 优先
    assert snap["_meta"]["trade_date"] == "2026-08-13"


def test_register_versions_and_csv_whole_batch_reject(env):
    rec = {"run_date": D, "t": 15, "result_type": "成交", "direction": "卖",
           "price": 283.0, "volume_mwh": 80.0}
    r1 = reg_mod.save(rec, env["retro"])
    assert r1["ok"] and r1["version"] == 1
    r2 = reg_mod.save({**rec, "price": 290.0}, env["retro"])
    assert r2["version"] == 2 and r2["is_revision"]    # 修订 = version+1，不双写
    st = reg_mod.settle_day(D, env["retro"])
    row = [r for r in st["rows"] if r["t"] == 15][0]
    assert row["price"] == 290.0                       # 取最新 version
    assert st["totals"]["day_execution_premium"] is not None
    csv_text = ("运行日,时段,结果,方向,价格,电量\n"
                f"{D},10,成交,卖,342,120\n"
                f"{D},24,成交,卖,342,120\n")
    out = reg_mod.import_csv(csv_text, env["retro"])
    assert not out["ok"] and out["n_rejected"] == 1    # 部分非法 → 整批拒绝


def test_report_r1_full_coverage_and_gates(env):
    reg_mod.save({"run_date": D, "t": 10, "result_type": "成交", "direction": "卖",
                  "price": 342.0, "volume_mwh": 120.0}, env["retro"])
    reg_mod.save({"run_date": D, "t": 18, "result_type": "未成交", "direction": "卖",
                  "price": 396.0, "volume_mwh": 160.0}, env["retro"])
    out = service.generate(D, D, env["retro"], env["real"], use_llm=False)
    assert out["rtype"] == "R1" and out["n_eff"] == 1
    md = Path(out["files"][0]).read_text(encoding="utf-8")
    for title in ("一、图景", "五、成交执行与收益", "六、偏差归属分画像", "九、交易判断建议",
                  "十一、使用限制与免责"):
        assert title in md
    for t in range(1, 97):                             # 4.1 逐点表 96 行一行不缺
        assert f"\n| {t} | " in md
    assert "两者相加＝当日执行溢价" in md               # 仅单日版出这一行
    assert "已成交溢价合计" in md and "机会成本估算" in md
    assert len(out["figs"]) == 12                      # 12 张图一张不少
    assert hashlib.sha256(Path(out["files"][0]).read_bytes()).hexdigest() == out["sha256"]


def test_report_r2_range(env):
    out = service.generate("2026-08-13", "2026-08-15", env["retro"], env["real"], use_llm=False)
    assert out["rtype"] == "R2" and out["n_eff"] == 3
    md = Path(out["files"][0]).read_text(encoding="utf-8")
    assert "两者相加＝当日执行溢价" not in md          # 多日版不出执行溢价行
    assert "区间均值" in md and "逐日复盘摘要" in md


def test_report_range_all_days_degraded_raises(env):
    # 8 月初无更早日前电价/A-1 拟合日 → 全部运行日分解失败时显式报错（缺日不补零）
    with pytest.raises(ValueError, match="分解失败"):
        service.generate("2026-08-01", "2026-08-03", env["retro"], env["real"], use_llm=False)


def test_evidence_pack_reproducible(env, tmp_path):
    d1 = service.build_day_context(D, env["retro"], env["real"], tmp_path / "a")
    p1 = evidence.build(D, d1["dec"], d1["jump"], d1["delta"])
    d2 = service.build_day_context(D, env["retro"], env["real"], tmp_path / "b")
    p2 = evidence.build(D, d2["dec"], d2["jump"], d2["delta"])
    assert evidence.dumps(p1) == evidence.dumps(p2)    # P-6：同一输入逐字段相同


# ---------------- MCP 独立调用 + skill 端到端 ----------------

def test_mcp_direct_tool_calls(tmp_path, monkeypatch):
    from backend.mcp.client import McpClient
    monkeypatch.setenv("WB_RETRO_DB", str(tmp_path / "retro.db"))
    monkeypatch.setenv("WB_DB", str(tmp_path / "wb.db"))
    data = McpClient("data")
    retro = McpClient("retro")
    try:
        assert data.call("db.ping")["ok"]
        tools = {t["name"] for t in retro.list_tools()}
        assert {"actual.availability", "delta.compute", "jump.classify",
                "report.render", "register.save"} <= tools
        days = retro.call("actual.availability", {"start": "2026-08-01", "end": "2026-08-31"})
        assert len(days) == 31                         # 绕过 skill 层直连原子工具
        assert retro.call("aggregate.expand24", {"values24": [1.0] * 24}) == [1.0] * 96
        assert retro.call("aggregate.to24", {"values96": [1, 2, 3, 4] * 24}) == [2.5] * 24
        assert retro.call("words.scan", {"text": "净收益 100 元"})["ok"] is False
    finally:
        data.close()
        retro.close()


def test_skill_runner_end_to_end(tmp_path, monkeypatch):
    from backend.retro.skill_runner import SkillRunner
    monkeypatch.setenv("WB_RETRO_DB", str(tmp_path / "retro.db"))
    monkeypatch.setenv("WB_DB", str(tmp_path / "wb.db"))
    runner = SkillRunner(skills_dir=Path(__file__).resolve().parent.parent / "skills")
    try:
        names = {s["id"] for s in runner.list_skills()}
        assert {"retro_day", "retro_range", "trade_register", "snapshot_capture"} <= names
        out = runner.run("trade_register", {
            "run_date": D, "t": 15, "result_type": "成交", "direction": "卖",
            "price": 283.0, "volume_mwh": 80.0})
        assert out["status"] == "ok", out.get("error")
        assert out["outputs"]["saved"]["ok"]
        rep = runner.run("retro_day", {"start": D, "end": D, "use_llm": False})
        assert rep["status"] == "ok", rep.get("error")
        report = rep["outputs"]["report"]
        assert report["rtype"] == "R1" and len(report["figs"]) == 12
        assert Path(report["files"][0]).exists()
        bad = runner.run("retro_day", {"start": D, "end": "2026-08-20"})
        assert bad["status"] == "failed"
        assert bad["error"]["code"] == "ASSERT_FAILED"
    finally:
        runner.close()
