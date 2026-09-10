"""LLM 层单测：fake LLM client（脚本化 tool_calls）驱动工具循环断言（不依赖真实 key）；
含"改数据必须带理由"拒绝分支；API 层 TestClient 冒烟（无 key 引导 / 修订 / 导出 / 配置遮蔽）。"""
from __future__ import annotations


import json
from pathlib import Path

import pytest



@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    """独立 Pipeline（临时库 + 独立 DB 路径），避免污染默认库。"""
    monkeypatch.setattr("backend.config.DB_PATH", tmp_path / "t.db")
    from backend.pipeline import Pipeline
    from backend.store import Store
    return Pipeline(store=Store(tmp_path / "t.db"))


# ---------- 工具注册表：改数据必须带理由 ----------

def test_modify_boundary_requires_reason(pipe):
    from backend.llm.tools import build_registry
    pipe.loaded()
    get_result = lambda: pipe.run_all()  # noqa: E731
    reg = build_registry(pipe, get_result)

    # 理由为空 → 拒绝（不落修订、不重算；2026-09-10 起不限字数）
    out = reg["modify_boundary"]({"boundary": "负荷", "period": 10,
                                  "points": [{"t": 37, "value": 46000}], "reason": ""})
    assert out["ok"] is False and "理由" in out["error"]
    assert pipe.store.all_revisions() == []
    # 短理由（非空）现被接受
    out2 = reg["modify_boundary"]({"boundary": "负荷", "period": 10,
                                   "points": [{"t": 37, "value": 46000}], "reason": "调"})
    assert out2["ok"] is True

    # 合法理由 → 修订追加 + 联动重算，返回新预测价
    out = reg["modify_boundary"]({"boundary": "负荷", "period": 10,
                                  "points": [{"t": 37, "value": 46000}],
                                  "reason": "午间负荷上调"})
    assert out["ok"] is True and out["rev_ids"]
    assert out["price_24_period"] is not None

    # 非法边界名 → 拒绝
    out = reg["modify_boundary"]({"boundary": "天气", "period": 1,
                                  "points": [{"t": 1, "value": 1}], "reason": "非法边界测试"})
    assert out["ok"] is False


def test_set_params_validates(pipe):
    from backend.llm.tools import build_registry
    pipe.loaded()
    reg = build_registry(pipe, lambda: pipe.run_all())
    bad = reg["set_params"]({"params": {"区间宽度": -5}, "reason": "非法参数测试"})
    assert bad["ok"] is False
    good = reg["set_params"]({"params": {"现货上限": 1200}, "reason": "现货上限政策调整"})
    assert good["ok"] is True and good["params"]["现货上限"] == 1200


def test_explain_period_contains_coefficients(pipe):
    from backend.llm.tools import build_registry
    r = pipe.run_all()
    reg = build_registry(pipe, lambda: r)
    out = reg["explain_period"]({"period": 10})
    assert out["ok"] is True
    assert "M1" in out["reasoning"] and "临界空间" in out["reasoning"]


# ---------- fake LLM：脚本化 tool_calls 驱动循环 ----------

def test_tool_loop_with_fake_client(pipe, monkeypatch):
    from backend.llm import client
    pipe.loaded()
    base = pipe.run_all()

    script = [
        # 第 1 轮：先尝试无理由修改（被拒），再带理由修改（成功）
        {"tool_calls": [{"id": "c1", "function": {"name": "modify_boundary",
                                                  "arguments": json.dumps({
                                                      "boundary": "负荷", "period": 10,
                                                      "points": [{"t": 37, "value": 46000}],
                                                      "reason": ""})}},   # 空理由 → 拒绝分支
                        {"id": "c2", "function": {"name": "modify_boundary",
                                                  "arguments": json.dumps({
                                                      "boundary": "负荷", "period": 10,
                                                      "points": [{"t": 37, "value": 46000}],
                                                      "reason": "午间负荷上调测试"})}}]},
        # 第 2 轮：最终回答（引用工具数值）
        {"content": "已修改：时段 10 预测电价 X 元/MWh（引用工具结果）。"},
    ]
    calls = {"n": 0}

    def fake_post(base_url, api_key, model, messages, tools=None):
        msg = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        return msg

    monkeypatch.setattr(client, "_post_chat", fake_post)
    monkeypatch.setattr(client, "_client_config", lambda: ("http://fake", "sk-test", "fake-model"))
    reply, trace = client.chat_with_tools("把 10 时段省调负荷调到 46000", period=10,
                                          pipe=pipe, get_result=lambda: base)
    assert reply.startswith("已修改")
    names = [t["name"] for t in trace]
    assert names.count("modify_boundary") == 2
    assert '"ok": false' in trace[0]["result"]           # 拒绝分支
    assert '"ok": true' in trace[1]["result"]            # 成功分支
    assert pipe.store.all_revisions()                    # 修订已落库


def test_no_key_raises_structured_error(pipe, monkeypatch):
    from backend.llm import client
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(client.LlmError) as ei:
        client.chat_with_tools("hi", pipe=pipe, get_result=lambda: None)
    assert "LLM_API_KEY" in str(ei.value) or "配置" in str(ei.value)


# ---------- API 层（TestClient） ----------

@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.DB_PATH", tmp_path / "api.db")
    from backend import api
    from backend.pipeline import Pipeline
    from backend.store import Store
    api.PIPE = Pipeline(store=Store(tmp_path / "api.db"))
    api.RESULT = None
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_state_and_boundary_flow(api_client):
    st = api_client.get("/api/state", params={"period": 10})
    assert st.status_code == 200
    body = st.json()
    assert body["m8"]["final_24"] and len(body["m8"]["final_24"]) == 24
    assert "period_detail" in body and body["period_detail"]["landing"]["n"] >= 0

    # 修订（理由为空 → 422；短理由现被接受）
    bad = api_client.post("/api/boundary", json={
        "boundary": "负荷", "period": 10, "points": [{"t": 37, "value": 46000}], "reason": "  "})
    assert bad.status_code == 422
    short_ok = api_client.post("/api/boundary", json={
        "boundary": "负荷", "period": 11, "points": [{"t": 41, "value": 45500}], "reason": "调"})
    assert short_ok.status_code == 200
    # 合法修订 → 联动重算
    ok = api_client.post("/api/boundary", json={
        "boundary": "负荷", "period": 10,
        "points": [{"t": 37, "value": 46000}, {"t": 38, "value": 46000},
                   {"t": 39, "value": 46000}, {"t": 40, "value": 46000}],
        "reason": "午间负荷上调测试"})
    assert ok.status_code == 200 and ok.json()["ok"] is True

    # 回退：state 里能找到 rev_id → rollback → 原值恢复（effective 回原值）
    revs = api_client.get("/api/state").json()["revisions"]
    assert revs, "修订应已落库"
    rb = api_client.post("/api/revision/rollback", json={"rev_id": revs[0]["rev_id"]})
    assert rb.status_code == 200

    # 参数修改 + 钳制联动
    p = api_client.post("/api/params", json={"params": {"现货上限": 1200}, "reason": "现货上限政策调整"})
    assert p.status_code == 200
    state = api_client.get("/api/state").json()
    assert all(v is None or v <= 1200 for v in state["m8"]["final_24"])


def test_api_export_produces_csv(api_client):
    r = api_client.post("/api/export")
    assert r.status_code == 200
    path = Path(r.json()["path"])
    assert path.exists()
    head = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "时点t" in head and "标注" in head


def test_api_chat_without_key_returns_guidance(api_client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    r = api_client.post("/api/chat", json={"message": "解释 10 时段", "period": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "LLM_BASE_URL" in body["error"]


def test_api_llm_config_masked(api_client, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.llm.config.ENV_PATH", tmp_path / ".env")
    api_client.post("/api/llm/config", json={"base_url": "http://x", "api_key": "sk-1234567890", "model": "m"})
    cfg = api_client.get("/api/llm/config").json()
    assert cfg["configured"] is True
    assert "sk-****" in cfg["api_key_masked"] and "1234567890" not in cfg["api_key_masked"]
    env_text = (tmp_path / ".env").read_text()
    assert "sk-1234567890" in env_text           # 只存 .env（gitignored）
