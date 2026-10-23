"""复盘数据对象 D-A1~D-A10 的 append-only 存取（同一 workbench.db，新表不碰一阶段表）。

表 ↔ 对象映射与唯一键见 docu/复盘架构设计-skills-mcp.md §4。
硬规则：写入只 INSERT（D-A6 软生效切 effective 标记除外——PRD §1.7.7 S2 明确允许，
旧快照不删）；缺行=未登记≠未成交；时间统一本地无时区 ISO（秒）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS retro_actual (            -- D-A5 实况数据集
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, item TEXT NOT NULL, t INTEGER NOT NULL,
  value REAL, missing INTEGER NOT NULL DEFAULT 0, load_seq INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'xlsx', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_actual
  ON retro_actual(run_date, item, t, load_seq);

CREATE TABLE IF NOT EXISTS calc_snapshot (           -- D-A6 预测输出快照
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, trade_date TEXT NOT NULL, seq INTEGER NOT NULL,
  item TEXT NOT NULL, t INTEGER NOT NULL, value REAL,
  used_pred2 INTEGER, k REAL, m1 REAL, c1 REAL, m2 REAL, c2 REAL,
  params_json TEXT, code_version TEXT, source TEXT NOT NULL DEFAULT 'live',
  effective INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_calc_snapshot
  ON calc_snapshot(run_date, trade_date, seq, item, t);
CREATE INDEX IF NOT EXISTS ix_calc_snapshot_eff ON calc_snapshot(run_date, effective);

CREATE TABLE IF NOT EXISTS retro_deviation (         -- D-A1 偏差记录
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, item TEXT NOT NULL, t INTEGER NOT NULL,
  v_pred REAL, pred_source TEXT, v_sys REAL, v_rev REAL, v_act REAL,
  delta REAL, missing INTEGER NOT NULL DEFAULT 0, trader_id TEXT NOT NULL DEFAULT 'default',
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_deviation
  ON retro_deviation(run_date, item, t, version);

CREATE TABLE IF NOT EXISTS retro_snapshot (          -- D-A2 归因快照
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL, run_date TEXT NOT NULL, period INTEGER,
  pack_version TEXT NOT NULL, pack_json TEXT NOT NULL, ai_text TEXT,
  model TEXT, params_json TEXT, check_result TEXT, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_snapshot
  ON retro_snapshot(owner, run_date, period, pack_version);

CREATE TABLE IF NOT EXISTS retro_habit (             -- D-A3 习惯画像
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL, scope TEXT NOT NULL,
  window_start TEXT NOT NULL, window_end TEXT NOT NULL, n_days INTEGER NOT NULL,
  h_json TEXT NOT NULL, divergence TEXT, data_version TEXT,
  version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_habit
  ON retro_habit(owner, scope, window_start, window_end, version);

CREATE TABLE IF NOT EXISTS retro_threshold (         -- D-A7 按日阈值
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, theta_high REAL NOT NULL, theta_jump REAL NOT NULL,
  theta_delta REAL NOT NULL, source TEXT NOT NULL, set_by TEXT, set_at TEXT,
  reason TEXT, revised INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_threshold ON retro_threshold(run_date, version);

CREATE TABLE IF NOT EXISTS retro_report (            -- D-A8 报告留档
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id TEXT NOT NULL, rtype TEXT NOT NULL, d_start TEXT NOT NULL, d_end TEXT NOT NULL,
  n_eff INTEGER NOT NULL, template_id TEXT NOT NULL, template_version TEXT NOT NULL,
  content_json TEXT NOT NULL, fmt TEXT NOT NULL, file_name TEXT, sha256 TEXT,
  generated_by TEXT NOT NULL DEFAULT 'default', generated_at TEXT NOT NULL,
  superseded_by TEXT, created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_report ON retro_report(report_id);

CREATE TABLE IF NOT EXISTS retro_trade_register (    -- D-A9 成交登记
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, t INTEGER NOT NULL, result_type TEXT NOT NULL,
  price REAL NOT NULL, volume_mwh REAL NOT NULL, direction TEXT NOT NULL,
  trader_id TEXT NOT NULL DEFAULT 'default', entered_at TEXT, note TEXT,
  source TEXT NOT NULL DEFAULT '自报', confidence TEXT,
  snapshot_trade_date TEXT, settle_points TEXT, pnl REAL,
  version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_trade_register
  ON retro_trade_register(run_date, t, result_type, trader_id, version);

CREATE TABLE IF NOT EXISTS retro_owner_comp (        -- D-A10 偏差归属与补偿记录
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL, t INTEGER NOT NULL, boundary TEXT NOT NULL,
  v_sys REAL, v_rev REAL, v_act REAL,
  owner_type TEXT NOT NULL, system_tier TEXT, split_part TEXT NOT NULL DEFAULT '—',
  e_before REAL, e_after REAL, gain REAL, comp_result TEXT,
  disclosure_induced INTEGER NOT NULL DEFAULT 0, params_snapshot_id TEXT,
  snapshot_trade_date TEXT, version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_retro_owner_comp
  ON retro_owner_comp(run_date, t, boundary, split_part, version);

CREATE TABLE IF NOT EXISTS skill_run (               -- skill 执行留痕
  run_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, skill_version TEXT NOT NULL,
  inputs_json TEXT NOT NULL, steps_json TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS skill_audit (             -- skill 生命周期/热加载审计
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_id TEXT NOT NULL, event TEXT NOT NULL, detail_json TEXT, created_at TEXT NOT NULL
);
"""

_APPEND_ONLY_TABLES = {
    "retro_actual", "calc_snapshot", "retro_deviation", "retro_snapshot", "retro_habit",
    "retro_threshold", "retro_report", "retro_trade_register", "retro_owner_comp",
    "skill_run", "skill_audit",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


_SCHEMA_READY: set[str] = set()          # 进程内：同一库建表只做一次（避免每次连接抢写锁）
_SCHEMA_LOCK = threading.Lock()


class RetroStore:
    """复盘表存取：建表幂等；查询固定排序保证顺序确定。

    并发约束：API/MCP 多进程多线程会同时建连，`PRAGMA journal_mode=WAL` 需短暂独占锁——
    先设 busy_timeout 重试，争用失败不致命（WAL 是文件级持久设置，先到连接已生效）。
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        import os
        default = os.environ.get("WB_RETRO_DB") or DB_PATH   # 与 MCP data server 同库
        self.db_path = Path(db_path) if db_path else Path(default)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=15000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass   # 并发建连争用：WAL 已由先到连接持久化，本连接继续可用
        key = str(self.db_path.resolve())
        if key not in _SCHEMA_READY:
            with _SCHEMA_LOCK:
                if key not in _SCHEMA_READY:
                    # 只读探测（不拿写锁）：表齐则跳过 DDL——避免多进程首连同时抢写锁
                    try:
                        names = {r["name"] for r in self.conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")}
                    except sqlite3.OperationalError:
                        names = set()
                    wanted = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
                    if not wanted.issubset(names):
                        self.conn.executescript(SCHEMA)
                    _SCHEMA_READY.add(key)
        self.conn.commit()

    # ---------- 通用 ----------
    def append(self, table: str, row: dict) -> int:
        if table not in _APPEND_ONLY_TABLES:
            raise ValueError(f"复盘层禁止写表 {table}")
        row = dict(row)
        row.setdefault("created_at", now_iso())
        cols = sorted(row.keys())
        sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})")
        cur = self.conn.execute(sql, [row[c] for c in cols])
        self.conn.commit()
        return cur.lastrowid or 0

    def append_batch(self, table: str, rows: list[dict]) -> int:
        if table not in _APPEND_ONLY_TABLES:
            raise ValueError(f"复盘层禁止写表 {table}")
        n = 0
        with self.conn:
            for r in rows:
                row = dict(r)
                row.setdefault("created_at", now_iso())
                cols = sorted(row.keys())
                self.conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                    [row[c] for c in cols])
                n += 1
        return n

    def query(self, table: str, where: dict | None = None, order: str = "id",
              limit: int | None = None) -> list[dict]:
        sql, args = f"SELECT * FROM {table}", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k} = ?" for k in sorted(where.keys()))
            args = [where[k] for k in sorted(where.keys())]
        sql += f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def next_version(self, table: str, key_cols: list[str], key_vals: dict) -> int:
        cond = " AND ".join(f"{c} = ?" for c in key_cols)
        row = self.conn.execute(
            f"SELECT MAX(version) AS v FROM {table} WHERE {cond}",
            [key_vals[c] for c in key_cols]).fetchone()
        return (row["v"] or 0) + 1

    def latest(self, table: str, key_cols: list[str], key_vals: dict) -> dict | None:
        cond = " AND ".join(f"{c} = ?" for c in key_cols)
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE {cond} ORDER BY version DESC, id DESC LIMIT 1",
            [key_vals[c] for c in key_cols]).fetchone()
        return dict(row) if row else None

    # ---------- D-A6 快照 ----------
    def snapshot_capture(self, run_date: str, trade_date: str,
                         items: dict[str, list[float | None]], meta: dict, params_json: str,
                         code_version: str, source: str = "live") -> int:
        """一次落全部 item 的 96 点快照（同 seq 同事务）。

        软生效（S2）：同 (run_date, trade_date, source) 旧 effective 置 0、本次置 1，
        旧快照行不删（append-only 不破）。返回本次 seq（同场次第几次算，递增不覆盖）。
        """
        seq = (self.conn.execute(
            "SELECT MAX(seq) AS s FROM calc_snapshot WHERE run_date = ? AND trade_date = ?",
            (run_date, trade_date)).fetchone()["s"] or 0) + 1
        rows = [{
            "run_date": run_date, "trade_date": trade_date, "seq": seq, "item": item,
            "t": t + 1, "value": v, "used_pred2": meta.get("used_pred2"),
            "k": meta.get("k"), "m1": meta.get("M1"), "c1": meta.get("C1"),
            "m2": meta.get("M2"), "c2": meta.get("C2"),
            "params_json": params_json, "code_version": code_version, "source": source,
        } for item in sorted(items) for t, v in enumerate(items[item])]
        with self.conn:
            self.conn.execute(
                "UPDATE calc_snapshot SET effective = 0 "
                "WHERE run_date = ? AND trade_date = ? AND source = ?",
                (run_date, trade_date, source))
            self.conn.executemany(
                "INSERT INTO calc_snapshot (run_date,trade_date,seq,item,t,value,used_pred2,"
                "k,m1,c1,m2,c2,params_json,code_version,source,effective,created_at) "
                "VALUES (:run_date,:trade_date,:seq,:item,:t,:value,:used_pred2,:k,:m1,:c1,"
                ":m2,:c2,:params_json,:code_version,:source,1,:created_at)",
                [{**r, "created_at": now_iso()} for r in rows])
        return seq

    def snapshot_effective(self, run_date: str, item: str, source: str = "live") -> dict | None:
        """取「离运行日最近的交易日」的生效快照该 item 的 96 点（S4 总账基准）。

        返回 {trade_date, seq, values, used_pred2, k, M1, C1, M2, C2, params_json,
        code_version, source}；无则 None。
        """
        row = self.conn.execute(
            "SELECT * FROM calc_snapshot WHERE run_date = ? AND item = ? AND source = ? "
            "AND effective = 1 ORDER BY trade_date DESC, seq DESC, t LIMIT 1",
            (run_date, item, source)).fetchone()
        if row is None:
            return None
        trade_date, seq = row["trade_date"], row["seq"]
        pts = self.conn.execute(
            "SELECT t, value FROM calc_snapshot WHERE run_date = ? AND trade_date = ? "
            "AND seq = ? AND item = ? AND effective = 1 ORDER BY t",
            (run_date, trade_date, seq, item)).fetchall()
        return {
            "trade_date": trade_date, "seq": seq,
            "values": [p["value"] for p in pts],
            "used_pred2": bool(row["used_pred2"]) if row["used_pred2"] is not None else None,
            "k": row["k"], "M1": row["m1"], "C1": row["c1"], "M2": row["m2"], "C2": row["c2"],
            "params_json": row["params_json"], "code_version": row["code_version"],
            "source": row["source"],
        }

    def snapshot_by_session(self, run_date: str, trade_date: str, item: str,
                            source: str = "live") -> dict | None:
        """取指定场次（trade_date）生效快照该 item 的 96 点（S5 逐笔解释/版本线用）。"""
        row = self.conn.execute(
            "SELECT * FROM calc_snapshot WHERE run_date = ? AND trade_date = ? AND item = ? "
            "AND source = ? AND effective = 1 ORDER BY seq DESC, t LIMIT 1",
            (run_date, trade_date, item, source)).fetchone()
        if row is None:
            return None
        pts = self.conn.execute(
            "SELECT t, value FROM calc_snapshot WHERE run_date = ? AND trade_date = ? "
            "AND item = ? AND source = ? AND effective = 1 ORDER BY seq DESC, t",
            (run_date, trade_date, item, source)).fetchall()
        seen: dict[int, float | None] = {}
        for p_ in pts:
            seen.setdefault(p_["t"], p_["value"])
        return {"trade_date": trade_date, "seq": row["seq"],
                "values": [seen.get(t) for t in range(1, 97)],
                "params_json": row["params_json"], "code_version": row["code_version"]}

    def snapshot_sessions(self, run_date: str, source: str = "live") -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT trade_date FROM calc_snapshot WHERE run_date = ? AND source = ? "
            "AND effective = 1 ORDER BY trade_date", (run_date, source)).fetchall()
        return [r["trade_date"] for r in rows]

    def latest_effective_trade_date(self, run_date: str, source: str = "live") -> str | None:
        """登记保存时自动带的 snapshot_trade_date：该运行日最新生效场次（live 优先）。"""
        rows = self.conn.execute(
            "SELECT DISTINCT trade_date FROM calc_snapshot WHERE run_date = ? AND effective = 1 "
            "ORDER BY source DESC, trade_date DESC LIMIT 1", (run_date,)).fetchall()
        return rows[0]["trade_date"] if rows else None

    # ---------- D-A7 阈值 ----------
    def threshold_of(self, run_date: str) -> dict:
        """当日生效阈值；无记录用默认并即时补写 source=default（§9 D-A7 硬规则）。"""
        row = self.conn.execute(
            "SELECT * FROM retro_threshold WHERE run_date = ? "
            "ORDER BY version DESC LIMIT 1", (run_date,)).fetchone()
        if row is None:
            from .constants import THETA_DEFAULTS
            self.append("retro_threshold", {
                "run_date": run_date, **THETA_DEFAULTS, "source": "default",
                "set_by": "system", "set_at": now_iso(), "reason": "无记录，默认值兜底",
                "revised": 0, "version": 1})
            return {"run_date": run_date, **THETA_DEFAULTS, "source": "default", "version": 1}
        return dict(row)

    def set_threshold(self, run_date: str, theta_high: float, set_by: str, reason: str) -> dict:
        cur = self.threshold_of(run_date)
        version = self.next_version("retro_threshold", ["run_date"], {"run_date": run_date})
        revised = int(abs(float(theta_high) - float(cur["theta_high"])) > 1e-9)
        self.append("retro_threshold", {
            "run_date": run_date, "theta_high": float(theta_high),
            "theta_jump": cur["theta_jump"], "theta_delta": cur["theta_delta"],
            "source": "manual", "set_by": set_by, "set_at": now_iso(), "reason": reason,
            "revised": revised, "version": version})
        return self.threshold_of(run_date)

    # ---------- 留档 ----------
    def log_skill_run(self, record: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO skill_run (run_id, skill_id, skill_version, inputs_json,"
                " steps_json, status, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                (record.get("run_id"), record.get("skill_id"), record.get("skill_version"),
                 _dumps(record.get("inputs", {})), _dumps(record.get("steps", [])),
                 record.get("status"), record.get("started_at"), record.get("finished_at")))

    def log_skill_audit(self, event: dict) -> None:
        self.append("skill_audit", {
            "skill_id": event.get("skill_id", ""), "event": event.get("event", ""),
            "detail_json": _dumps({k: v for k, v in event.items() if k not in ("skill_id", "event")})})

    def close(self) -> None:
        self.conn.close()
