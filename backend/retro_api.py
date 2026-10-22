"""复盘 REST API（B2：到 API 层为止；前端与助手接入另立 goal）。

全部业务入口走 skill SOP（逻辑层配置驱动）+ MCP 原子工具（连接层）；
本文件只做参数解析与 HTTP 语义。挂在既有 api.py（include_router，不动既有路由）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .retro import register as register_mod
from .retro import service
from .retro.skill_runner import get_runner

router = APIRouter(prefix="/api/retro", tags=["retro"])


class ReportBody(BaseModel):
    start: str
    end: str
    use_llm: bool | None = None


class RegisterBody(BaseModel):
    run_date: str
    t: int
    result_type: str
    direction: str
    price: float
    volume_mwh: float
    note: str | None = None


class CsvBody(BaseModel):
    text: str


class SnapshotBody(BaseModel):
    run_date: str
    trade_date: str | None = None


class StatusBody(BaseModel):
    status: str
    reason: str = ""


def _run_skill(skill_id: str, inputs: dict) -> dict:
    runner = get_runner()
    result = runner.run(skill_id, {k: v for k, v in inputs.items() if v is not None})
    if result.get("status") != "ok":
        err = result.get("error") or {}
        raise HTTPException(status_code=422, detail={
            "code": err.get("code", "SKILL_FAILED"),
            "message": err.get("message", str(result)), "step": err.get("step")})
    return result


@router.post("/report")
def retro_report(body: ReportBody) -> dict:
    """生成复盘报告（N_eff=1 → R1；>1 → R2；skill 编排）。"""
    if not body.start or not body.end:
        raise HTTPException(status_code=422, detail="起止日必须显式指定（不预选区间）")
    skill = "retro_day" if body.start == body.end else "retro_range"
    result = _run_skill(skill, {"start": body.start, "end": body.end,
                                "use_llm": body.use_llm})
    return result["outputs"]["report"]


@router.post("/register")
def retro_register(body: RegisterBody) -> dict:
    """成交登记（单笔，走 trade_register SOP：校验→保存→逐笔结算）。"""
    result = _run_skill("trade_register", body.model_dump())
    return {"saved": result["outputs"]["saved"], "settlement": result["outputs"]["settlement"]}


@router.post("/register/csv")
def retro_register_csv(body: CsvBody) -> dict:
    """CSV 批量导入（整批拒绝）。"""
    runner = get_runner()
    out = runner._tool_caller("register.import_csv", {"text": body.text})
    if not out.get("ok"):
        raise HTTPException(status_code=422, detail=out)
    return out

@router.get("/register/day")
def retro_register_day(run_date: str) -> list[dict]:
    """当日登记清单（各键最新 version；只读，登记页「当日已登记高亮」数据源）。"""
    runner = get_runner()
    return runner._tool_caller("register.day_list", {"run_date": run_date})


@router.post("/register/demo")
def retro_register_demo(body: ReportBody) -> dict:
    """模拟成交登记生成器（四类日 + 多场次；幂等）。"""
    runner = get_runner()
    return runner._tool_caller("register.demo_seed", {"start": body.start, "end": body.end})


@router.post("/snapshot")
def retro_snapshot(body: SnapshotBody) -> dict:
    """交易结束：全链重算 + D-A6 落档（软生效；skill 编排）。"""
    result = _run_skill("snapshot_capture",
                        {"run_date": body.run_date, "trade_date": body.trade_date})
    return result["outputs"]["captured"]


@router.get("/status")
def retro_status(start: str = "", end: str = "") -> dict:
    """数据就绪度（可用日清单/登记/快照概况）。"""
    if not start or not end:
        raise HTTPException(status_code=422, detail="需带 start/end 查询参数")
    runner = get_runner()
    return runner._tool_caller("retro.status", {"start": start, "end": end})


@router.get("/reports")
def retro_reports() -> list[dict]:
    from .retro.store import RetroStore
    store = RetroStore()
    try:
        return store.query("retro_report", order="id DESC", limit=100)
    finally:
        store.close()


@router.get("/reports/{report_id}")
def retro_report_detail(report_id: str) -> dict:
    from .retro.store import RetroStore
    store = RetroStore()
    try:
        rows = store.query("retro_report", where={"report_id": report_id})
    finally:
        store.close()
    if not rows:
        raise HTTPException(status_code=404, detail="报告不存在")
    row = rows[0]
    path = row.get("file_name")
    content = None
    if path:
        from .config import EXPORT_DIR
        p = EXPORT_DIR / "retro" / report_id / path
        if p.exists():
            content = p.read_text(encoding="utf-8")
    return {**row, "content": content}


# ---- skill 生命周期与热加载（skills 管理面） ----
@router.get("/skills")
def skills_list() -> list[dict]:
    return get_runner().list_skills()


@router.get("/skills/audit")
def skills_audit() -> list[dict]:
    from .retro.store import RetroStore
    store = RetroStore()
    try:
        return store.query("skill_audit", order="id DESC", limit=200)
    finally:
        store.close()


@router.post("/skills/{skill_id}/status")
def skills_set_status(skill_id: str, body: StatusBody) -> dict:
    try:
        return get_runner().set_status(skill_id, body.status, body.reason)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/skills/reload")
def skills_reload() -> dict:
    return get_runner().reload()


@router.get("/mcp/status")
def mcp_status() -> dict:
    return get_runner().mcp_status()
