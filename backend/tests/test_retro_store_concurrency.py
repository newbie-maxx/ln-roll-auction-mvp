"""复盘存储并发/健壮性用例：多线程多连接同时建连+写+读不得 database is locked；
skill 留痕落库失败（MCP 子类 ToolError）不得穿透阻断业务。"""
from __future__ import annotations

import threading


def test_concurrent_retro_store_open_and_write(tmp_path):
    from backend.retro.store import RetroStore
    db = tmp_path / "conc.db"
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            st = RetroStore(db)
            st.append("skill_audit",
                      {"skill_id": f"w{i}", "event": "probe", "detail_json": "{}"})
            rows = st.query("skill_audit", where={"skill_id": f"w{i}"})
            assert rows and rows[0]["event"] == "probe"
            st.close()
        except Exception as e:   # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:2]
    st = RetroStore(db)
    assert len(st.query("skill_audit")) == 8
    st.close()


def test_log_run_swallows_mcp_tool_error(tmp_path, monkeypatch):
    """MCP 客户端抛 mcp.server.ToolError（与 skill_runtime.ToolError 不同类）也不得穿透。"""
    monkeypatch.setenv("WB_RETRO_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("WB_DB", str(tmp_path / "w.db"))
    monkeypatch.setattr("backend.config.DB_PATH", tmp_path / "w.db")
    import backend.retro.skill_runner as sr
    if sr._RUNNER is not None:
        sr._RUNNER.close()
        sr._RUNNER = None
    runner = sr.SkillRunner()
    try:
        from backend.mcp.server import ToolError as McpToolError

        def boom(*a, **k):
            raise McpToolError("database is locked")

        runner._data.call = boom               # 模拟 data server 落库失败
        runner.log_run({"run_id": "sk-x", "skill_id": "s", "status": "ok"})
        runner.log_audit({"skill_id": "s", "event": "reload"})
        # 业务不受影响：skill 仍可执行（retro_status 走 retro server，不依赖 data server）
        out = runner.run("retro_day", {"start": "2026-08-14", "end": "2026-08-20"})
        assert out["status"] == "failed"       # 业务失败原因是区间断言，而非留痕异常
        assert out["error"]["code"] == "ASSERT_FAILED"
    finally:
        runner.close()
        sr._RUNNER = None
