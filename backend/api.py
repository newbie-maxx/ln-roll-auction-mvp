"""FastAPI：工作台 API（GET /api/state；POST /api/recalc /api/params /api/boundary /api/intent
/api/revision/rollback /api/export /api/llm/config /api/chat）+ 静态托管 web/dist（单机交付形态）。

LLM 端点在 Step 7 接入（llm/ 包）；此处先注册路由骨架保证契约完整。
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import EXPORT_DIR, env_llm
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
    list_volume: float | None = None
    lift_price: float | None = None
    lift_volume: float | None = None


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
    d_day = PIPE.target_day()
    out = r.to_json(period=period)
    out["day_info"] = PIPE.day_info()
    from .pipeline import BOUNDARY_LABELS
    out["boundaries"] = {b: PIPE.loaded().day_ahead[d_day][b].to_json()
                         for b in BOUNDARY_WHITELIST
                         if b in PIPE.loaded().day_ahead[d_day]}
    out["boundary_labels"] = dict(BOUNDARY_LABELS)
    out["intents"] = {str(p): v for p, v in PIPE.intents().items()}
    out["revisions"] = PIPE.store.all_revisions()
    out["m1_ready"] = {"roll_source": r.m1["roll_source"], "warnings": r.m1["warnings"]}
    return out


@app.get("/api/days")
def get_days() -> dict:
    """可选滚撮日清单 + 当前 D/A/历史范围（历史自动收窄至 D 日之前）。"""
    return PIPE.day_info()


class TargetDayBody(BaseModel):
    date: str


@app.post("/api/target-day")
def set_target_day(body: TargetDayBody) -> dict:
    """选择滚撮日（D 日）→ 新 run（修订按日隔离）→ 全链重算。"""
    global RESULT
    try:
        info = PIPE.set_target_day(body.date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    RESULT = PIPE.run_all()
    return {"ok": True, "day_info": info, "m8": {
        "a_day": RESULT.m8["a_day"], "a1_day": RESULT.m8["a1_day"],
        "final_24": RESULT.m8["final_24"]}}


@app.post("/api/data/upload")
async def upload_data(file: UploadFile = File(...)) -> dict:
    """上传数据表（xlsx；按 sheet 签名自动识别：日前边界表/实时边界表/省间滚撮表）。
    校验通过 → 持久化 backend/data/uploads/ → 重建数据底账（按日期合并，后传覆盖同日）→ 全链重算。"""
    global RESULT
    from tempfile import NamedTemporaryFile
    from .loaders.upload_manager import validate_upload
    payload = await file.read()
    if len(payload) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="文件超过 50MB 上限")
    with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        report = validate_upload(tmp_path)
        if not report["ok"]:
            raise HTTPException(status_code=422, detail={"message": "格式校验未通过", "report": report})
        kind = report["kind"]
        saved = PIPE.uploads.save(kind, file.filename or f"{kind}.xlsx", payload)
        reload_info = PIPE.reload_dataset()
        RESULT = PIPE.run_all()
        return {"ok": True, "kind": kind, "saved": str(saved), "report": report,
                "dataset": reload_info, "day_info": PIPE.day_info(),
                "m8": {"a_day": RESULT.m8["a_day"], "a1_day": RESULT.m8["a1_day"]}}
    finally:
        tmp_path.unlink(missing_ok=True)


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
    # 只转发客户端显式提交的字段：null = 清空，未提交 = 不变（pydantic model_fields_set 区分二者）
    fields = {"list_price": "listPrice", "list_volume": "listVolume",
              "lift_price": "liftPrice", "lift_volume": "liftVolume"}
    provided = {fields[k]: getattr(body, k) for k in fields if k in body.model_fields_set}
    try:
        PIPE.set_intent(body.period, **provided)
        labels = {"list_price": "意向挂牌价", "list_volume": "意向挂牌量",
                  "lift_price": "意向摘牌价", "lift_volume": "意向摘牌量"}
        for snake, label in labels.items():
            if provided.get(fields[snake]):
                PIPE.store.set_intent(PIPE._run_id, body.period, label, getattr(body, snake))
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
        err = "未配置 LLM API key：请在设置面板填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（或写入 .env）后重试"
        PIPE.store.log_chat(body.message, [], f"[未配置 key] {err}", period=body.period)
        return {"ok": False, "error": err, "reply": None}
    try:
        reply, tool_calls = chat_with_tools(body.message, period=body.period)
    except Exception as e:  # noqa: BLE001
        PIPE.store.log_chat(body.message, [], f"[调用失败] {e}", period=body.period)
        return {"ok": False, "error": f"LLM 调用失败：{e}", "reply": None}
    PIPE.store.log_chat(body.message, tool_calls, reply, run_id=PIPE._run_id, period=body.period)
    return {"ok": True, "reply": reply, "tool_calls": tool_calls}


# ---------- 静态托管 web/dist（单机交付形态：uvicorn api:app 即完整服务） ----------
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(DIST / "index.html")
