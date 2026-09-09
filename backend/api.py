"""FastAPI：工作台 API（GET /api/state；POST /api/recalc /api/params /api/boundary /api/intent
/api/revision/rollback /api/export /api/llm/config /api/chat）+ 静态托管 web/dist（单机交付形态）。

LLM 端点在 Step 7 接入（llm/ 包）；此处先注册路由骨架保证契约完整。
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import D_DAY, EXPORT_DIR, env_llm
from .pipeline import BOUNDARY_WHITELIST, ChainResult, Pipeline

app = FastAPI(title="辽宁电力滚搓交易智能体 MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PIPE = Pipeline()
RESULT: ChainResult | None = None


class ParamsBody(BaseModel):
    params: dict = Field(default_factory=dict)
    reason: str = ""


class BoundaryPoint(BaseModel):
    t: int
    value: float


class BoundaryBody(BaseModel):
    boundary: str
    period: int
    points: list[BoundaryPoint]
    reason: str


class IntentBody(BaseModel):
    period: int
    list_price: float | None = None
    lift_price: float | None = None
    volume: float | None = None


class RollbackBody(BaseModel):
    rev_id: str


def _result() -> ChainResult:
    global RESULT
    if RESULT is None:
        RESULT = PIPE.run_all()
    return RESULT


@app.get("/api/state")
def get_state(period: int | None = None) -> dict:
    """当前工作台快照：边界/参数/意向/全链输出（可按时段裁剪）+ 数据就绪清单（M1）。"""
    r = _result()
    out = r.to_json(period=period)
    out["boundaries"] = {b: PIPE.loaded().day_ahead[D_DAY][b].to_json()
                         for b in BOUNDARY_WHITELIST
                         if b in PIPE.loaded().day_ahead[D_DAY]}
    out["intents"] = {str(p): v for p, v in PIPE.intents().items()}
    out["revisions"] = PIPE.store.all_revisions()
    out["m1_ready"] = {"roll_source": r.m1["roll_source"], "warnings": r.m1["warnings"]}
    return out


@app.post("/api/recalc")
def recalc() -> dict:
    """触发 M1→M8 全链重算，返回各模块状态与耗时。"""
    global RESULT
    RESULT = PIPE.run_all()
    return {"ok": True, "timings_ms": RESULT.timings_ms}


@app.post("/api/params")
def set_params(body: ParamsBody) -> dict:
    """参数校验 + 联动重算（非法返回错误）。"""
    global RESULT
    try:
        PIPE.set_params(body.params)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    RESULT = PIPE.recalc_point()
    return {"ok": True, "params": PIPE.params.as_dict(), "timings_ms": RESULT.timings_ms}


@app.post("/api/boundary")
def modify_boundary(body: BoundaryBody) -> dict:
    """修订链追加（reason ≥5 字强制）+ 下游联动重算。"""
    global RESULT
    try:
        rev_ids = PIPE.modify_boundary(body.boundary, body.period,
                                       [p.model_dump() for p in body.points], body.reason)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    RESULT = PIPE.recalc_point()
    r = _result()
    return {"ok": True, "rev_ids": rev_ids, "price_24": r.m8["final_24"],
            "load_rate_24": r.m7["load_rate_24"], "timings_ms": r.timings_ms}


@app.post("/api/intent")
def set_intent(body: IntentBody) -> dict:
    global RESULT
    try:
        PIPE.set_intent(body.period, listPrice=body.list_price, liftPrice=body.lift_price, volume=body.volume)
        PIPE.store.set_intent(PIPE._pristine_run_id, body.period, "意向挂牌价",
                              body.list_price) if body.list_price else None
        PIPE.store.set_intent(PIPE._pristine_run_id, body.period, "交易量",
                              body.volume) if body.volume else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    RESULT = PIPE.recalc_point()
    r = _result()
    return {"ok": True, "grey": r.grey[body.period - 1]}


@app.post("/api/revision/rollback")
def rollback(body: RollbackBody) -> dict:
    """回退（回退本身记新修订）。"""
    global RESULT
    try:
        PIPE.store.rollback_revision(body.rev_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    RESULT = PIPE.recalc_point()
    return {"ok": True, "revisions": PIPE.store.all_revisions()}


@app.post("/api/export")
def export_report() -> dict:
    """表格导出（披露/测算标注 + 来源日），返回文件路径。"""
    r = _result()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"workbench-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    data = PIPE.loaded().day_ahead["2026-08-31"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["时点t", "边界", "生效值", "标注", "来源日", "备注",
                    "M6预测联络线", "M7开机", "M7负荷率", "M8最终电价96"])
        for t in range(96):
            for b in BOUNDARY_WHITELIST:
                if b not in data:
                    continue
                s = data[b]
                w.writerow([t + 1, b, s.values[t], s.kinds[t], s.src_days[t], s.notes[t],
                            r.m6["predicted_96"][t], r.m7["final_on_96"][t],
                            r.m7["load_rate_96"][t], r.m8["final_96"][t]])
    return {"ok": True, "path": str(path)}


# ---------- LLM 配置（Step 7 完整接入；先提供配置读写契约） ----------
class LlmConfigBody(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@app.get("/api/llm/config")
def get_llm_config() -> dict:
    cfg = env_llm()
    masked = "sk-****" + cfg["api_key"][-4:] if cfg["api_key"] else ""
    return {"base_url": cfg["base_url"], "model": cfg["model"], "api_key_masked": masked, "configured": bool(cfg["api_key"])}


@app.post("/api/llm/config")
def set_llm_config(body: LlmConfigBody) -> dict:
    from .llm.config import save_llm_config
    try:
        save_llm_config(body.base_url, body.api_key, body.model)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"写入 .env 失败: {e}") from e
    return {"ok": True}


class ChatBody(BaseModel):
    message: str
    period: int | None = None


@app.post("/api/chat")
def chat(body: ChatBody) -> dict:
    """右栏助手（Step 7 接真实 LLM + 工具调用循环；未配置 key 时返回明确引导）。"""
    from .llm.client import chat_with_tools
    cfg = env_llm()
    if not cfg["api_key"]:
        return {"ok": False,
                "error": "未配置 LLM API key：请在设置面板填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（或写入 .env）后重试",
                "reply": None}
    try:
        reply, tool_calls = chat_with_tools(body.message, period=body.period)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"LLM 调用失败：{e}", "reply": None}
    return {"ok": True, "reply": reply, "tool_calls": tool_calls}


# ---------- 静态托管 web/dist（单机交付形态：uvicorn api:app 即完整服务） ----------
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(DIST / "index.html")
