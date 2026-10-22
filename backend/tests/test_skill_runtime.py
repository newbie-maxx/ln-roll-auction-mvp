"""Skill 运行时引擎测试（切片 B）：
SOP 正常流程与步骤留痕、表达式白名单与缺失键 null、while/max_steps 防护、
assert/fail/ToolError 错误码透传、热加载（新版生效 / 坏文件保旧+审计 / 未加载拒执行）、
生命周期门与迁移、输入校验、同输入确定性复现。
"""
from __future__ import annotations

import json

import pytest

from backend.skill_runtime import (ExprError, SkillNotFoundError, SkillRuntime,
                                   SkillStatusError, ToolError, compile_expr, evaluate)


# ---------- helpers ----------

class FakePersister:
    def __init__(self):
        self.runs = []
        self.audits = []

    def log_run(self, record):
        self.runs.append(record)

    def log_audit(self, entry):
        self.audits.append(entry)


def make_caller(calls, results=None):
    def caller(name, args):
        calls.append((name, dict(args)))
        if results and name in results:
            return results[name]
        return {"ok": True}

    return caller


def write_skill(skills_dir, filename, data):
    path = skills_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def base_skill(**over):
    skill = {
        "id": "s", "name": "示例", "version": "1.0.0", "status": "active", "owner": "retro",
        "inputs": {}, "returns": [], "steps": [],
    }
    skill.update(over)
    return skill


RESULTS = {
    "actual.load_day": {"available": True, "missing_count": 0, "points": [1, 2, 3]},
    "bonus.calc": {"value": 10},
}


def flow_skill():
    return base_skill(
        id="flow", name="正常流",
        inputs={"run_date": {"type": "string", "required": True},
                "flag": {"type": "boolean", "required": False}},
        returns=["summary"],
        steps=[
            {"assign": {"acc": 0}},
            {"tool": "actual.load_day", "args": {"run_date": "${run_date}"}, "output": "actual"},
            {"if": "${flag}",
             "then": [{"tool": "bonus.calc", "args": {"base": 1, "tag": "then"}, "output": "bonus"}],
             "else": [{"assign": {"bonus": {"value": 0}}}]},
            {"foreach": "${actual.points}", "as": "p", "body": [{"assign": {"acc": "${acc + p}"}}]},
            {"assign": {"summary": "${acc + bonus.value}"}},
        ],
    )


# ---------- 1. 正常流程 ----------

def test_01_normal_flow_trace_and_outputs(tmp_path):
    calls = []
    persister = FakePersister()
    rt = SkillRuntime(tmp_path, make_caller(calls, RESULTS), persister=persister)
    write_skill(tmp_path, "flow.json", flow_skill())

    rec = rt.run("flow", {"run_date": "2026-08-14", "flag": True})

    assert rec["status"] == "ok"
    assert rec["outputs"] == {"summary": 16}
    assert rec["run_id"].startswith("sk-") and len(rec["run_id"]) == len("sk-") + 8
    assert rec["skill_id"] == "flow" and rec["skill_version"] == "1.0.0"
    assert calls[0] == ("actual.load_day", {"run_date": "2026-08-14"})
    assert calls[1] == ("bonus.calc", {"base": 1, "tag": "then"})

    # 步骤留痕：类型序列、全局索引、实参快照
    kinds = [s["kind"] for s in rec["steps"]]
    assert kinds == ["assign", "tool", "if", "tool", "foreach",
                     "assign", "assign", "assign", "assign"]
    assert [s["index"] for s in rec["steps"]] == list(range(9))
    assert all(s["ok"] for s in rec["steps"])
    tool_steps = [s for s in rec["steps"] if s["kind"] == "tool"]
    assert tool_steps[0]["args_resolved"] == {"run_date": "2026-08-14"}
    assert tool_steps[1]["args_resolved"] == {"base": 1, "tag": "then"}

    # run 记录完整；时间戳仅入 run 记录，不进 inputs/outputs
    assert {"run_id", "skill_id", "skill_version", "inputs", "steps", "status",
            "started_at", "finished_at"} <= set(rec)
    assert rec["inputs"] == {"run_date": "2026-08-14", "flag": True}
    assert rec["outputs"] == {"summary": 16}
    assert "started_at" not in rec["outputs"] and "finished_at" not in rec["inputs"]
    assert len(persister.runs) == 1 and persister.runs[0]["run_id"] == rec["run_id"]


# ---------- 2. 表达式安全 ----------

def test_02_expression_whitelist_and_null(tmp_path):
    for bad in ["open('x')", "a.__class__", "f(x)", "__import__('os')", "a.__dict__",
                "lambda: 1", "[1, 2]", "x if y else z", "1 // 2", "x in xs"]:
        with pytest.raises(ExprError):
            compile_expr(bad)

    # 缺失属性/下标越界 → null，可用于业务判断
    assert evaluate("a.missing == null", {"a": {}}) is True
    assert evaluate("a.missing == null", {"a": {"missing": 0}}) is False
    assert evaluate("len(xs) > 0 and xs[0] == 1 and xs[-1] == 3", {"xs": [1, 2, 3]}) is True
    assert evaluate("not flag and -n == -2", {"flag": False, "n": 2}) is True
    with pytest.raises(ExprError):
        evaluate("1 / 0", {})
    with pytest.raises(ExprError):
        evaluate("n + 1", {"n": None})

    # skill 层：越权表达式 → 加载拒绝；混合模板 → 加载拒绝
    rt = SkillRuntime(tmp_path, make_caller([]))
    write_skill(tmp_path, "b1.json",
                base_skill(id="b1", steps=[{"tool": "t", "args": {"x": "${open('x')}"}}]))
    write_skill(tmp_path, "b2.json", base_skill(id="b2", steps=[{"assert": "${a.__class__}"}]))
    write_skill(tmp_path, "b3.json",
                base_skill(id="b3", steps=[{"tool": "t", "args": {"x": "prefix${x}"}}]))
    items = {it["id"]: it for it in rt.list_skills()}
    assert items["b1"]["error"] and "len" in items["b1"]["error"]
    assert items["b2"]["error"] and "下划线" in items["b2"]["error"]
    assert items["b3"]["error"] and "模板" in items["b3"]["error"]
    for sid in ("b1", "b2", "b3"):
        with pytest.raises(SkillNotFoundError):
            rt.run(sid, {})

    # 正常资产：缺失属性判 null
    write_skill(tmp_path, "ok.json", base_skill(
        id="ok", returns=["x"], steps=[{"assign": {"x": "${a.missing == null}"}}]))
    rec = rt.run("ok", {"a": {}})
    assert rec["status"] == "ok" and rec["outputs"] == {"x": True}


# ---------- 3. while / max_steps ----------

def test_03_while_max_iter_and_max_steps(tmp_path):
    rt = SkillRuntime(tmp_path, make_caller([]))
    write_skill(tmp_path, "wh1.json", base_skill(id="wh1", returns=["out"], steps=[
        {"assign": {"n": 0}},
        {"while": "${n < 100}", "max_iter": 3, "body": [{"assign": {"n": "${n + 1}"}}]},
        {"assign": {"out": "${n}"}},
    ]))
    rec = rt.run("wh1", {})
    assert rec["status"] == "failed"
    assert rec["error"]["code"] == "WHILE_MAX_ITER"
    assert rec["error"]["step"] == 1

    write_skill(tmp_path, "wh2.json", base_skill(id="wh2", returns=["out"], steps=[
        {"assign": {"n": 0}},
        {"while": "${n < 2}", "max_iter": 5, "body": [{"assign": {"n": "${n + 1}"}}]},
        {"assign": {"out": "${n}"}},
    ]))
    rec = rt.run("wh2", {})
    assert rec["status"] == "ok" and rec["outputs"] == {"out": 2}

    # 运行期表达式错误
    write_skill(tmp_path, "ex.json", base_skill(
        id="ex", steps=[{"assign": {"x": "${missing + 1}"}}]))
    rec = rt.run("ex", {})
    assert rec["error"]["code"] == "EXPR_ERROR" and rec["error"]["step"] == 0

    # 总步数上限
    rt2 = SkillRuntime(tmp_path / "tight", make_caller([]), max_steps=3)
    write_skill(tmp_path / "tight", "many.json", base_skill(id="many", steps=[
        {"assign": {"a": 1}}, {"assign": {"b": 2}}, {"assign": {"c": 3}},
        {"assign": {"d": 4}}, {"assign": {"e": 5}},
    ]))
    rec = rt2.run("many", {})
    assert rec["status"] == "failed"
    assert rec["error"]["code"] == "MAX_STEPS_EXCEEDED"
    assert len(rec["steps"]) == 3


# ---------- 4. assert / fail / ToolError ----------

def test_04_assert_fail_and_tool_error(tmp_path):
    calls = []
    rt = SkillRuntime(tmp_path, make_caller(calls, RESULTS))

    write_skill(tmp_path, "as.json", base_skill(id="as", steps=[
        {"tool": "actual.load_day", "args": {}, "output": "actual"},
        {"assert": "${actual.missing_count >= 0}", "message": "装载结果必须带缺失计数"},
        {"assert": "${1 > 2}", "message": "不应到达"},
    ]))
    rec = rt.run("as", {})
    assert rec["status"] == "failed"
    assert rec["error"]["code"] == "ASSERT_FAILED"
    assert rec["error"]["message"] == "不应到达"
    assert rec["error"]["step"] == 2

    write_skill(tmp_path, "fl.json", base_skill(id="fl", steps=[
        {"if": "${available != true}",
         "then": [{"fail": {"code": "NO_ACTUAL", "message": "该日无实况"}}]},
    ]))
    rec = rt.run("fl", {})
    assert rec["error"]["code"] == "NO_ACTUAL"
    assert rec["error"]["message"] == "该日无实况"
    assert rec["error"]["step"] == 1  # fail 步骤自身索引（if 为 0）

    def boom(name, args):
        raise ToolError("下游超时")

    rt2 = SkillRuntime(tmp_path, boom)
    write_skill(tmp_path, "te.json", base_skill(id="te", steps=[
        {"tool": "delta.compute", "args": {"run_date": "d"}, "output": "delta"},
    ]))
    rec = rt2.run("te", {})
    assert rec["status"] == "failed"
    assert rec["error"]["code"] == "TOOL_ERROR"
    assert rec["error"]["step"] == 0
    assert "delta.compute" in rec["error"]["message"]
    assert "下游超时" in rec["error"]["message"]


# ---------- 5. 热加载 ----------

def test_05_hot_reload(tmp_path):
    persister = FakePersister()
    rt = SkillRuntime(tmp_path, make_caller([]), persister=persister)
    data = base_skill(id="hot", returns=["msg"], steps=[{"assign": {"msg": "旧消息"}}])
    write_skill(tmp_path, "hot.json", data)
    assert rt.run("hot", {})["outputs"] == {"msg": "旧消息"}
    sha_v1 = {it["id"]: it for it in rt.list_skills()}["hot"]["sha256"]

    data["steps"][0]["assign"]["msg"] = "新消息"
    write_skill(tmp_path, "hot.json", data)
    assert rt.run("hot", {})["outputs"] == {"msg": "新消息"}  # 新版生效
    assert {it["id"]: it for it in rt.list_skills()}["hot"]["sha256"] != sha_v1

    # 写坏 JSON → 旧版仍可用 + 审计 reload_rejected
    (tmp_path / "hot.json").write_text("{broken json", encoding="utf-8")
    rec = rt.run("hot", {})
    assert rec["status"] == "ok" and rec["outputs"] == {"msg": "新消息"}
    assert any(a["event"] == "reload_rejected" and a["skill_id"] == "hot"
               and "JSON" in a["error"] for a in persister.audits)

    # 从未成功加载的新文件：list 可见（status 取自文件），run 拒绝
    write_skill(tmp_path, "fresh.json",
                base_skill(id="fresh", steps=[{"foreach": "${xs}", "as": "i"}]))
    items = {it["id"]: it for it in rt.list_skills()}
    assert items["fresh"]["status"] == "active"
    assert items["fresh"]["error"] and items["fresh"]["loaded_at"] is None
    with pytest.raises(SkillNotFoundError):
        rt.run("fresh", {})

    # 修好 + reload 强制重扫 → 可用
    write_skill(tmp_path, "fresh.json",
                base_skill(id="fresh", returns=["m"], steps=[{"assign": {"m": "ok"}}]))
    report = rt.reload()
    assert "fresh" in report["loaded"]
    assert rt.run("fresh", {})["outputs"] == {"m": "ok"}


# ---------- 6. status 门与生命周期 ----------

def test_06_status_gate_and_lifecycle(tmp_path):
    persister = FakePersister()
    rt = SkillRuntime(tmp_path, make_caller([]), persister=persister)
    data = base_skill(id="ctl", inputs={"d": {"type": "string", "required": True}},
                      returns=["echo"], steps=[{"assign": {"echo": "${d}"}}])
    data["status"] = "draft"
    write_skill(tmp_path, "ctl.json", data)

    rec = rt.run("ctl", {"d": "x"})
    assert rec["status"] == "failed"
    assert rec["error"]["code"] == "SKILL_NOT_ACTIVE"
    assert "draft" in rec["error"]["message"]

    info = rt.set_status("ctl", "active", reason="上线")
    assert info == {"skill_id": "ctl", "from": "draft", "to": "active", "reason": "上线"}
    expected = dict(data)
    expected["status"] = "active"
    assert (tmp_path / "ctl.json").read_text(encoding="utf-8") == \
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n"  # 保序重写、缩进 2
    assert any(a["event"] == "status_changed" and a["skill_id"] == "ctl"
               and a["from"] == "draft" and a["to"] == "active" and a["reason"] == "上线"
               for a in persister.audits)
    assert rt.run("ctl", {"d": "x"})["status"] == "ok"

    rt.set_status("ctl", "suspended")
    rec = rt.run("ctl", {"d": "x"})
    assert rec["error"]["code"] == "SKILL_NOT_ACTIVE"
    assert "suspended" in rec["error"]["message"]
    rt.set_status("ctl", "active")  # suspended → active 合法

    data2 = base_skill(id="d2")
    data2["status"] = "draft"
    write_skill(tmp_path, "d2.json", data2)
    with pytest.raises(SkillStatusError):
        rt.set_status("d2", "deprecated")  # draft → deprecated 非法
    with pytest.raises(SkillStatusError):
        rt.set_status("d2", "offline")
    with pytest.raises(SkillStatusError):
        rt.set_status("ctl", "bogus")
    with pytest.raises(SkillNotFoundError):
        rt.set_status("nope", "active")

    rt.set_status("ctl", "deprecated")
    rt.set_status("ctl", "offline")
    items = {it["id"]: it for it in rt.list_skills()}
    assert items["ctl"]["status"] == "offline"

    assert [r["status"] for r in persister.runs] == ["failed", "ok", "failed"]  # 成败都留痕


# ---------- 7. inputs 校验 ----------

def test_07_inputs_validation(tmp_path):
    persister = FakePersister()
    rt = SkillRuntime(tmp_path, make_caller([]), persister=persister)
    write_skill(tmp_path, "in.json", base_skill(id="in", inputs={
        "run_date": {"type": "string", "required": True},
        "n": {"type": "number"},
        "flag": {"type": "boolean"},
    }))

    rec = rt.run("in", {})
    assert rec["error"]["code"] == "INVALID_INPUT"
    assert "run_date" in rec["error"]["message"]

    rec = rt.run("in", {"run_date": 20260814})  # string 不接受数字
    assert rec["error"]["code"] == "INVALID_INPUT"
    assert "类型不符" in rec["error"]["message"]

    rec = rt.run("in", {"run_date": "d", "n": True})  # bool 不是 number
    assert rec["error"]["code"] == "INVALID_INPUT"

    rec = rt.run("in", {"run_date": "d", "flag": 1})  # 1 不是 boolean
    assert rec["error"]["code"] == "INVALID_INPUT"

    rec = rt.run("in", {"run_date": "d", "n": 2.5, "flag": False})  # int/float 均可
    assert rec["status"] == "ok" and rec["outputs"] == {}

    assert [r["status"] for r in persister.runs] == ["failed"] * 4 + ["ok"]


# ---------- 8. 确定性 ----------

def test_08_determinism_same_input_same_output(tmp_path):
    calls = []
    rt = SkillRuntime(tmp_path, make_caller(calls, RESULTS))
    write_skill(tmp_path, "flow.json", flow_skill())
    inputs = {"run_date": "2026-08-14"}  # flag 缺省 → else 分支

    r1 = rt.run("flow", inputs)
    r2 = rt.run("flow", inputs)

    assert r1["status"] == r2["status"] == "ok"
    assert r1["outputs"] == r2["outputs"] == {"summary": 6}
    assert r1["steps"] == r2["steps"]
    assert len(calls) == 2 and calls[0] == calls[1]


# ---------- 补充：留痕截断 / 工具白名单 ----------

def test_09_long_args_truncated_in_trace(tmp_path):
    calls = []
    rt = SkillRuntime(tmp_path, make_caller(calls))
    big = "x" * 3000
    write_skill(tmp_path, "tr.json", base_skill(id="tr", steps=[
        {"tool": "t", "args": {"big": big, "n": 1}, "output": "r"}]))
    rec = rt.run("tr", {})
    assert rec["status"] == "ok"
    assert calls[0][1]["big"] == big  # 调用拿到完整实参
    snap = rec["steps"][0]["args_resolved"]
    assert snap["big"].startswith("x" * 2000) and len(snap["big"]) < 3000
    assert "截断" in snap["big"] and snap["n"] == 1


def test_10_known_tools_gate(tmp_path):
    rt = SkillRuntime(tmp_path, make_caller([]), known_tools={"actual.load_day"})
    write_skill(tmp_path, "u.json",
                base_skill(id="u", steps=[{"tool": "mystery.run", "args": {}}]))
    items = rt.list_skills()
    assert items[0]["error"] and "未知工具" in items[0]["error"]
    with pytest.raises(SkillNotFoundError):
        rt.run("u", {})

    # known_tools=None（默认）跳过该检查
    rt2 = SkillRuntime(tmp_path / "free", make_caller([]))
    write_skill(tmp_path / "free", "v.json",
                base_skill(id="v", steps=[{"tool": "mystery.run", "args": {}}]))
    assert rt2.run("v", {})["status"] == "ok"
