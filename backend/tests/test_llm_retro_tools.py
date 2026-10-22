"""LLM 复盘工具接入用例：注册表/护栏（登记缺字段拒执）、fake-LLM 工具循环（生成报告 + 登记）、
API /api/chat 无 key 降级与带 fake-LLM 的复盘工具调用冒烟。不依赖真实 key。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

D = "2026-08-14"


@pytest.fixture()
def retro_env(tmp_path, monkeypatch):
    """隔离复盘环境（MCP 子进程走 WB_RETRO_DB/WB_DB；skill_runner 单例重置）。"""
    monkeypatch.setattr("backend.config.DB_PATH", tmp_path / "wb.db")
    monkeypatch.setenv("WB_RETRO_DB", str(tmp_path / "retro.db"))
    monkeypatch.setenv("WB_DB", str(tmp_path / "wb.db"))
    import backend.retro.skill_runner as sr
    if sr._RUNNER is not None:
        sr._RUNNER.close()
        sr._RUNNER = None
    yield tmp_path
    if sr._RUNNER is not None:
        sr._RUNNER.close()
        sr._RUNNER = None


@pytest.fixture()
def registry(retro_env):
    from backend.llm.tools import build_registry
    from backend.pipeline import Pipeline
    from backend.store import Store
    pipe = Pipeline(store=Store(str(retro_env / "wb.db")))
    return build_registry(pipe, lambda: pipe.run_all())


def test_retro_tools_registered(registry):
    from backend.llm.tools import TOOLS_SPEC
    spec_names = {t["function"]["name"] for t in TOOLS_SPEC}
    assert {"retro_status", "retro_generate_report", "retro_register_trade",
            "retro_day_settlement"} <= spec_names
    assert set(registry) >= {"retro_status", "retro_generate_report",
                             "retro_register_trade", "retro_day_settlement"}


def test_retro_status_readonly(registry):
    out = registry["retro_status"]({"start": "2026-08-01", "end": "2026-08-31"})
    assert out["available_days"] == 31 and out["register_rows"] == 0


def test_register_trade_guardrail_missing_fields(registry):
    """护栏：字段不全（缺方向）→ 拒绝执行并要求逐项确认，不落任何登记。"""
    out = registry["retro_register_trade"]({
        "run_date": D, "t": 15, "result_type": "成交",
        "price": 283, "volume_mwh": 80})            # 缺 direction
    assert out["ok"] is False and "缺" in out["error"] and "direction" in out["error"]


def test_register_trade_ok_and_settlement(registry):
    out = registry["retro_register_trade"]({
        "run_date": D, "t": 15, "result_type": "成交", "direction": "卖",
        "price": 283, "volume_mwh": 80})
    assert out["ok"] is True and out["version"] == 1
    assert "未经回单核对" in out["note"]
    t = out["totals"]
    assert set(t) == {"filled", "unfilled", "day_execution_premium"}
    settle = registry["retro_day_settlement"]({"run_date": D})
    assert settle["has_any"] is True and len(settle["rows"]) >= 1


def test_generate_report_via_tool(registry):
    out = registry["retro_generate_report"]({"start": D, "end": D})
    assert out["ok"] is True and out["rtype"] == "R1" and out["figs"] == 12
    assert Path(out["files"][0]).exists() and len(out["sha256"]) == 64


def test_fake_llm_loop_retro_report(retro_env, monkeypatch):
    """fake LLM 脚本化 tool_calls：retro_status → retro_generate_report → 收尾转述。"""
    from backend.llm import client
    from backend.pipeline import Pipeline
    from backend.store import Store
    pipe = Pipeline(store=Store(str(retro_env / "wb.db")))
    base = pipe.run_all()
    script = [
        {"tool_calls": [
            {"id": "c1", "function": {"name": "retro_status",
                                      "arguments": json.dumps({"start": D, "end": D})}},
            {"id": "c2", "function": {"name": "retro_generate_report",
                                      "arguments": json.dumps({"start": D, "end": D})}}]},
        {"content": f"复盘报告已生成（转述工具返回，不自算数字）。"},
    ]
    calls = {"n": 0}

    def fake_post(base_url, api_key, model, messages, tools=None):
        msg = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        return msg

    monkeypatch.setattr(client, "_post_chat", fake_post)
    monkeypatch.setattr(client, "_client_config", lambda: ("http://fake", "sk-test", "fake-model"))
    reply, trace = client.chat_with_tools("帮我生成 8 月 14 日的复盘报告", period=None,
                                          pipe=pipe, get_result=lambda: base)
    names = [t["name"] for t in trace]
    assert names == ["retro_status", "retro_generate_report"]
    assert '"ok": true' in trace[1]["result"] and "report_id" in trace[1]["result"]
    assert reply.startswith("复盘报告已生成")


def test_api_chat_without_key_degrades(retro_env, monkeypatch):
    """无 key：/api/chat 返回结构化降级（不 500），提示配置引导。"""
    from fastapi.testclient import TestClient
    from backend import api as api_mod
    from backend.pipeline import Pipeline
    from backend.store import Store
    api_mod.PIPE = Pipeline(store=Store(str(retro_env / "wb.db")))
    api_mod.RESULT = None
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    r = TestClient(api_mod.app).post("/api/chat", json={"message": "生成 8 月 14 日复盘"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is False and body["reply"] is None
    assert "配置" in body["error"] or "key" in body["error"].lower()


def test_api_chat_with_fake_llm_calls_retro_tool(retro_env, monkeypatch):
    """带 fake-LLM：/api/chat 全链返回复盘工具调用与回复（验收判据 5）。"""
    from fastapi.testclient import TestClient
    from backend import api as api_mod
    from backend.llm import client as llm_client
    from backend.pipeline import Pipeline
    from backend.store import Store
    api_mod.PIPE = Pipeline(store=Store(str(retro_env / "wb.db")))
    api_mod.RESULT = None
    script = [
        {"tool_calls": [{"id": "c1", "function": {
            "name": "retro_status",
            "arguments": json.dumps({"start": D, "end": D})}}]},
        {"content": "8 月 14 日数据就绪（转述工具返回）。"},
    ]
    calls = {"n": 0}

    def fake_post(base_url, api_key, model, messages, tools=None):
        msg = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        return msg

    monkeypatch.setattr(llm_client, "_post_chat", fake_post)
    monkeypatch.setattr(llm_client, "_client_config",
                        lambda: ("http://fake", "sk-test", "fake-model"))
    r = TestClient(api_mod.app).post("/api/chat", json={"message": "8 月 14 日能复盘吗"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["tool_calls"] and body["tool_calls"][0]["name"] == "retro_status"
    assert "就绪" in body["reply"]
