"""核心数据类 MCP（独立进程）：SQLite D-A* 原子读写 + 阈值 + skill 审计。仅原子操作。"""
from __future__ import annotations

import json
import os

from ..retro.store import RetroStore
from .server import McpServer, ToolError

app = McpServer("retro-data", "1.0.0")
_store: RetroStore | None = None


def _db() -> RetroStore:
    global _store
    if _store is None:
        _store = RetroStore(os.environ.get("WB_RETRO_DB"))
    return _store


_STR = {"type": "string"}


@app.tool("db.append", "append-only 写一行到复盘表（D-A*/skill_run/skill_audit 白名单）",
          {"type": "object", "properties": {
              "table": _STR, "row": {"type": "object"}}, "required": ["table", "row"]})
def db_append(table: str, row: dict) -> dict:
    return {"id": _db().append(table, row)}


@app.tool("db.append_batch", "append-only 批量写", {"type": "object", "properties": {
    "table": _STR, "rows": {"type": "array", "items": {"type": "object"}}},
    "required": ["table", "rows"]})
def db_append_batch(table: str, rows: list[dict]) -> dict:
    return {"n": _db().append_batch(table, rows)}


@app.tool("db.query", "只读查询（固定排序；where 等值）", {"type": "object", "properties": {
    "table": _STR, "where": {"type": "object"}, "order": _STR, "limit": {"type": "integer"}}})
def db_query(table: str, where: dict | None = None, order: str = "id",
             limit: int | None = None) -> list[dict]:
    return _db().query(table, where, order, limit)


@app.tool("db.latest", "取业务唯一键最新 version 行", {"type": "object", "properties": {
    "table": _STR, "key_cols": {"type": "array", "items": _STR}, "key_vals": {"type": "object"}},
    "required": ["table", "key_cols", "key_vals"]})
def db_latest(table: str, key_cols: list[str], key_vals: dict) -> dict | None:
    return _db().latest(table, key_cols, key_vals)


@app.tool("threshold.get", "当日生效阈值（无记录补写 default）", {"type": "object", "properties": {
    "run_date": _STR}, "required": ["run_date"]})
def threshold_get(run_date: str) -> dict:
    return _db().threshold_of(run_date)


@app.tool("threshold.set", "按日阈值覆盖（append-only version 链）", {"type": "object", "properties": {
    "run_date": _STR, "theta_high": {"type": "number"}, "set_by": _STR, "reason": _STR},
    "required": ["run_date", "theta_high", "set_by", "reason"]})
def threshold_set(run_date: str, theta_high: float, set_by: str, reason: str) -> dict:
    return _db().set_threshold(run_date, theta_high, set_by, reason)


@app.tool("snapshot.effect", "取某 item 生效快照 96 点（live/replay）", {"type": "object", "properties": {
    "run_date": _STR, "item": _STR, "source": _STR}, "required": ["run_date", "item"]})
def snapshot_effect(run_date: str, item: str, source: str = "live") -> dict | None:
    return _db().snapshot_effective(run_date, item, source)


@app.tool("skill.audit", "skill 生命周期/热加载事件落审计", {"type": "object", "properties": {
    "event": {"type": "object"}}, "required": ["event"]})
def skill_audit(event: dict) -> dict:
    _db().log_skill_audit(event)
    return {"ok": True}


@app.tool("skill.run_log", "skill 执行留痕落库", {"type": "object", "properties": {
    "record": {"type": "object"}}, "required": ["record"]})
def skill_run_log(record: dict) -> dict:
    _db().log_skill_run({**record,
                         "steps": record.get("steps", []),
                         "inputs": record.get("inputs", {})})
    return {"ok": True}


@app.tool("db.ping", "连通性检查", {})
def ping() -> dict:
    _db().conn.execute("SELECT 1")
    return {"ok": True, "tables": [r["name"] for r in _db().conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'retro%' "
        "OR name IN ('calc_snapshot','skill_run','skill_audit') ORDER BY name")]}


if __name__ == "__main__":
    app.serve()
