"""LLM 工具注册表（docu/智能体工具与技能设计.md §2 契约，OpenAI tools JSON Schema）+ 执行分发。

映射：pipeline（计算/重算）与 store（留痕/回退）。工具名与参数即 API/前端契约，改动须同步设计文档
与 .agents/skills/roll-auction-agent-ops。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..pipeline import BOUNDARY_WHITELIST, Pipeline

MODULES = ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8")

TOOLS_SPEC: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_state",
            "description": "获取当前工作台快照：边界/参数/意向/全链输出（可按时段裁剪）+ 数据就绪清单",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "integer", "minimum": 1, "maximum": 24,
                                          "description": "交易时段 1–24（可省）"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_chain",
            "description": "触发 M1→M8 全链重算，返回各模块状态与耗时",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_params",
            "description": "修改全局参数（校验 + 联动重算）。修改类操作：必须已有用户 ≥5 字理由方可执行",
            "parameters": {
                "type": "object",
                "properties": {
                    "params": {"type": "object", "description": "参数名→数值，如 {\"现货上限\": 1200}"},
                    "reason": {"type": "string", "description": "用户理由（≥5 字，必填）"},
                },
                "required": ["params", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_boundary",
            "description": "修改某边界 96 点数值（修订链追加 + 下游联动重算）。修改类操作：必须已有用户 ≥5 字理由",
            "parameters": {
                "type": "object",
                "properties": {
                    "boundary": {"type": "string", "enum": list(BOUNDARY_WHITELIST)},
                    "period": {"type": "integer", "minimum": 1, "maximum": 24},
                    "points": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"t": {"type": "integer", "minimum": 1, "maximum": 96},
                                       "value": {"type": "number"}},
                        "required": ["t", "value"]}},
                    "reason": {"type": "string", "description": "用户理由（≥5 字，必填）"},
                },
                "required": ["boundary", "period", "points", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_intent",
            "description": "录入某时段意向挂牌/摘牌价与交易量（>0；供灰度量价）",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "integer", "minimum": 1, "maximum": 24},
                    "list_price": {"type": "number", "description": "意向挂牌价（元/MWh，>0）"},
                    "lift_price": {"type": "number", "description": "意向摘牌价（元/MWh，>0）"},
                    "volume": {"type": "number", "description": "交易量（MWh，>0）"},
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_module",
            "description": "获取某模块输出与推导理由（样本成员/基线算式/开机中间值/拟合系数 M1C1·M2C2/临界空间）",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {"type": "string", "enum": list(MODULES)},
                    "period": {"type": "integer", "minimum": 1, "maximum": 24},
                },
                "required": ["module"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_similar_days",
            "description": "检索某时段 ±阈值 相似运行日：成员 + 负荷率对比 + N/分子/分母/统计范围",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "integer", "minimum": 1, "maximum": 24},
                    "threshold": {"type": "number", "description": "负荷率阈值（默认 0.05）"},
                },
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_period",
            "description": "输出该时段决策依据链：输入 → 中间值 → 结论（含落点概率/灰度）",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "integer", "minimum": 1, "maximum": 24}},
                "required": ["period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_revision",
            "description": "回退一条修订（回退本身记新修订）",
            "parameters": {
                "type": "object",
                "properties": {"rev_id": {"type": "string"}},
                "required": ["rev_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_report",
            "description": "导出表格/对照件（披露/测算标注 + 来源日），返回文件路径",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _num(v: float | None, nd: int = 2) -> float | None:
    return round(v, nd) if v is not None else None


def _sync_api_result(r) -> None:
    """工具驱动的重算同步到 api 层 RESULT 缓存（否则 /api/state 读到陈旧结果）。"""
    from .. import api as _api
    _api.RESULT = r


def build_registry(pipe: Pipeline, get_result: Callable[[], Any]) -> dict[str, Callable[[dict], dict]]:
    """构造 工具名 → 执行函数 的注册表（pipe/get_result 由 api 注入）。"""

    def get_state(args: dict) -> dict:
        r = get_result()
        period = args.get("period")
        out = r.to_json(period=period)
        out["intents"] = {str(p): v for p, v in pipe.intents().items()}
        return out

    def run_chain(args: dict) -> dict:                      # noqa: ARG001
        r = pipe.run_all()
        _sync_api_result(r)
        return {"ok": True, "timings_ms": r.timings_ms,
                "price_24_head": [_num(v) for v in r.m8["final_24"][:6]]}

    def set_params(args: dict) -> dict:
        reason = str(args.get("reason", ""))
        if not reason.strip():
            return {"ok": False, "error": "修改理由必填，请先向用户取得理由"}
        try:
            pipe.set_params(args.get("params", {}))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        r = pipe.recalc_point()
        _sync_api_result(r)
        return {"ok": True, "params": pipe.params.as_dict(),
                "price_24": [_num(v) for v in r.m8["final_24"]],
                "timings_ms": r.timings_ms}

    def modify_boundary(args: dict) -> dict:
        reason = str(args.get("reason", ""))
        if not reason.strip():
            return {"ok": False, "error": "修改理由必填，请先向用户取得理由"}
        try:
            rev_ids = pipe.modify_boundary(args["boundary"], int(args["period"]),
                                           args["points"], reason)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        r = pipe.recalc_point()
        _sync_api_result(r)
        p = int(args["period"])
        return {"ok": True, "rev_ids": rev_ids,
                "price_24_period": _num(r.m8["final_24"][p - 1]),
                "load_rate_24_period": _num(r.m7["load_rate_24"][p - 1], 4),
                "timings_ms": r.timings_ms}

    def set_intent(args: dict) -> dict:
        try:
            pipe.set_intent(int(args["period"]), listPrice=args.get("list_price"),
                            liftPrice=args.get("lift_price"), volume=args.get("volume"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        r = pipe.recalc_point()
        _sync_api_result(r)
        return {"ok": True, "grey": r.grey[int(args["period"]) - 1]}

    def get_module(args: dict) -> dict:
        r = get_result()
        m = args["module"]
        period = args.get("period")
        summary = {
            "m1": lambda: {"roll_source": r.m1["roll_source"], "warnings": r.m1["warnings"]},
            "m2": lambda: r.m2,
            "m3": lambda: {"formula": r.m3["formula"],
                           "groups_head": r.m3["a_dimension_groups"][:4]},
            "m4": lambda: {"formula": r.m4["formula"], "missing_days": r.m4["missing_days"],
                           "mean_96_head": [_num(v) for v in r.m4["mean_96"][:8]]},
            "m5": lambda: {"formula": r.m5["formula"], "roll_source": r.m5["roll_source"],
                           "warnings": r.m5["warnings"]},
            "m6": lambda: {"formula": r.m6["formula"],
                           "predicted_96_head": [_num(v) for v in r.m6["predicted_96"][:8]]},
            "m7": lambda: {"mode": r.m7["mode"], "final_on_am": _num(r.m7["final_on_am"]),
                           "final_on_pm": _num(r.m7["final_on_pm"]),
                           "load_rate_24": [_num(v, 4) for v in r.m7["load_rate_24"]],
                           "note_step6": r.m7.get("note_step6")},
            "m8": lambda: {"a_day": r.m8["a_day"], "a1_day": r.m8["a1_day"],
                           "k": _num(r.m8["k"], 4), "M1": _num(r.m8["M1"], 4), "C1": _num(r.m8["C1"]),
                           "M2": _num(r.m8["M2"], 4), "C2": _num(r.m8["C2"]),
                           "critical_space_96": _num(r.m8["critical_space_96"]),
                           "final_24": [_num(v) for v in r.m8["final_24"]],
                           "used_pred2_96": r.m8["used_pred2_96"]},
        }.get(m)
        if summary is None:
            return {"ok": False, "error": f"未知模块 {m}"}
        out = {"ok": True, "module": m, "summary": summary()}
        if period:
            out["period_detail"] = r.to_json(period=period).get("period_detail")
        return out

    def search_similar_days(args: dict) -> dict:
        r = get_result()
        period = int(args["period"])
        threshold = args.get("threshold")
        if threshold is not None:
            try:
                pipe.set_params({"相似日阈值": float(threshold)})
                r = pipe.recalc_point()
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        landing = r.landing[period - 1]
        return {"ok": True, "period": period, "members": landing["members"], "n": landing["n"],
                "enough": landing["enough"], "scope": landing["scope"],
                "expectation": _num(landing["expectation"]),
                "load_rate_24_period": _num(r.m7["load_rate_24"][period - 1], 4)}

    def explain_period(args: dict) -> dict:
        r = get_result()
        period = int(args["period"])
        d = r.to_json(period=period)["period_detail"]
        m8 = r.m8
        return {
            "ok": True, "period": period, "detail": d,
            "reasoning": (
                f"输入：空间4点={[_num(v) for v in d['space_4p']]} MW，开机={_num(r.m7['final_on_96'][(period - 1) * 4])} MW → "
                f"负荷率(24)={_num(d['load_rate_24'], 4)}；"
                f"临界空间={_num(m8['critical_space_96'])} MW；拟合 M1={_num(m8['M1'], 4)}/C1={_num(m8['C1'])}，"
                f"M2={_num(m8['M2'], 4)}/C2={_num(m8['C2'])}（A-1={m8['a1_day']}）→ "
                f"预测电价(24)={_num(d['price_24'])} 元/MWh；落点 N={d['landing']['n']}"
                f"（期望={_num(d['landing']['expectation'])}）"
            ),
        }

    def rollback_revision(args: dict) -> dict:
        try:
            pipe.store.rollback_revision(args["rev_id"])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        r = pipe.recalc_point()
        _sync_api_result(r)
        return {"ok": True, "revisions": pipe.store.all_revisions(),
                "price_24": [_num(v) for v in r.m8["final_24"]]}

    def export_report(args: dict) -> dict:                  # noqa: ARG001
        from ..api import export_report as api_export
        return api_export()

    return {
        "get_state": get_state,
        "run_chain": run_chain,
        "set_params": set_params,
        "modify_boundary": modify_boundary,
        "set_intent": set_intent,
        "get_module": get_module,
        "search_similar_days": search_similar_days,
        "explain_period": explain_period,
        "rollback_revision": rollback_revision,
        "export_report": export_report,
    }


def execute(registry: dict[str, Callable[[dict], dict]], name: str, args_json: str) -> str:
    fn = registry.get(name)
    if fn is None:
        return json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)
    try:
        args = json.loads(args_json) if args_json and args_json != "" else {}
        return json.dumps(fn(args), ensure_ascii=False, default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
