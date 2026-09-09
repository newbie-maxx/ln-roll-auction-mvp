"""SQLite 留痕存储（附录 D）：boundary_master / boundary_revision / intent_input / calc_run / chat_log。
append-only：原值永不物理 UPDATE；读时合并（最新有效修订 ?? 主表原值）；回退记新修订。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS boundary_master (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL, run_id TEXT NOT NULL,
  period INTEGER NOT NULL, boundary_type TEXT NOT NULL,
  value_type TEXT NOT NULL, src_day TEXT, calc_version TEXT,
  value REAL, created_at TEXT NOT NULL,
  UNIQUE(boundary_id, run_id, period, boundary_type)
);
CREATE TABLE IF NOT EXISTS boundary_revision (
  rev_id TEXT PRIMARY KEY, boundary_id TEXT NOT NULL, run_id TEXT NOT NULL,
  period INTEGER, boundary_type TEXT NOT NULL, t INTEGER,
  ref_master_version TEXT, revised_value REAL, reason TEXT NOT NULL,
  evidence TEXT, operator TEXT, op_time TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '有效', prev_rev_id TEXT
);
CREATE TABLE IF NOT EXISTS intent_input (
  item_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, period INTEGER,
  item_type TEXT NOT NULL, value REAL, revised_flag INTEGER DEFAULT 0,
  ref_master TEXT, reason TEXT, operator TEXT, op_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calc_run (
  run_id TEXT PRIMARY KEY, date TEXT NOT NULL, calc_mode TEXT NOT NULL,
  params_snapshot TEXT, m7_mode TEXT, created_at TEXT NOT NULL, status TEXT
);
CREATE TABLE IF NOT EXISTS chat_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, run_id TEXT,
  period INTEGER, user_message TEXT, tool_calls TEXT, assistant_reply TEXT,
  version TEXT
);
CREATE INDEX IF NOT EXISTS idx_rev_boundary ON boundary_revision(boundary_id, t, status);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class Store:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- 计算版本 ----------
    def new_run(self, date: str, params_snapshot: dict, m7_mode: str = "系统开机推演",
                run_id: str | None = None) -> str:
        """run_id 缺省自动生成；显式传入稳定键（数据版本:滚撮日）时幂等复用——
        切换滚撮日再切回，修订链按同一键继续生效（不丢）。"""
        run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT OR REPLACE INTO calc_run(run_id, date, calc_mode, params_snapshot, m7_mode, created_at, status) VALUES(?,?,?,?,?,?,?)",
            (run_id, date, "系统开机推演", json.dumps(params_snapshot, ensure_ascii=False), m7_mode, _now(), "有效"),
        )
        self.conn.commit()
        return run_id

    # ---------- 主表（系统原值，只 INSERT/REPLACE-by-key，不 UPDATE 数值语义） ----------
    def upsert_master(self, run_id: str, boundary_id: str, boundary_type: str, period: int,
                      value: float | None, value_type: str = "披露", src_day: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO boundary_master(boundary_id, run_id, period, boundary_type, value_type, src_day, calc_version, value, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (boundary_id, run_id, period, boundary_type, value_type, src_day, run_id, value, _now()),
        )

    # ---------- 修订链（append-only） ----------
    def append_revision(self, boundary_id: str, boundary_type: str, t: int, revised_value: float,
                        reason: str, operator: str = "交易员", run_id: str = "", period: int | None = None,
                        evidence: str | None = None) -> str:
        if len(reason.strip()) < 5:
            raise ValueError("修改理由须 ≥5 字")
        prev = self.conn.execute(
            "SELECT rev_id FROM boundary_revision WHERE boundary_id=? AND boundary_type=? AND t=? AND status='有效' "
            "ORDER BY op_time DESC LIMIT 1", (boundary_id, boundary_type, t),
        ).fetchone()
        rev_id = f"rev-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO boundary_revision(rev_id, boundary_id, run_id, period, boundary_type, t, ref_master_version, revised_value, reason, evidence, operator, op_time, status, prev_rev_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, boundary_id, run_id, period, boundary_type, t, run_id, revised_value, reason, evidence, operator, _now(), "有效", prev["rev_id"] if prev else None),
        )
        self.conn.commit()
        return rev_id

    def effective_value(self, boundary_id: str, boundary_type: str, t: int, run_id: str | None = None) -> float | None:
        """读时合并：最新有效修订 → 主表原值。传 run_id 时仅取该数据版本/滚撮日的修订与主表
        （数据底账更换或滚撮日切换后，旧修订保留可审计但不再作用于新口径）。"""
        if run_id:
            # 严格 run 隔离：数据版本/滚撮日之外的修订与主表一律不可见（None = 无修订，回到内存原值）
            rev = self.conn.execute(
                "SELECT revised_value FROM boundary_revision WHERE boundary_id=? AND boundary_type=? AND t=? AND run_id=? AND status='有效' "
                "ORDER BY op_time DESC LIMIT 1", (boundary_id, boundary_type, t, run_id),
            ).fetchone()
            return rev["revised_value"] if rev is not None else None
        rev = self.conn.execute(
            "SELECT revised_value FROM boundary_revision WHERE boundary_id=? AND boundary_type=? AND t=? AND status='有效' "
            "ORDER BY op_time DESC LIMIT 1", (boundary_id, boundary_type, t),
        ).fetchone()
        if rev is not None:
            return rev["revised_value"]
        row = self.conn.execute(
            "SELECT value FROM boundary_master WHERE boundary_id=? AND boundary_type=? AND period=? ORDER BY created_at DESC LIMIT 1",
            (boundary_id, boundary_type, t),
        ).fetchone()
        return row["value"] if row is not None else None

    def rollback_revision(self, rev_id: str, operator: str = "交易员") -> str:
        """回退 = 该修订标'已回退' + 追加恢复原值的反向修订（回退本身记新修订，不物理删除）。"""
        row = self.conn.execute("SELECT * FROM boundary_revision WHERE rev_id=?", (rev_id,)).fetchone()
        if row is None:
            raise ValueError(f"修订 {rev_id} 不存在")
        if row["status"] != "有效":
            raise ValueError(f"修订 {rev_id} 已非有效状态")
        master = self.conn.execute(
            "SELECT value FROM boundary_master WHERE boundary_id=? AND boundary_type=? AND period=? "
            "ORDER BY created_at DESC LIMIT 1", (row["boundary_id"], row["boundary_type"], row["t"]),
        ).fetchone()
        original = master["value"] if master is not None else None
        self.conn.execute("UPDATE boundary_revision SET status='已回退' WHERE rev_id=?", (rev_id,))
        return self.append_revision(
            boundary_id=row["boundary_id"], boundary_type=row["boundary_type"], t=row["t"],
            revised_value=original if original is not None else 0.0,
            reason=f"回退修订 {rev_id}，恢复原值", operator=operator, run_id=row["run_id"],
            period=row["period"], evidence=f"rollback of {rev_id}",
        )

    def revision_chain(self, boundary_id: str, boundary_type: str, t: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM boundary_revision WHERE boundary_id=? AND boundary_type=? AND t=? ORDER BY op_time",
            (boundary_id, boundary_type, t),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_revisions(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM boundary_revision ORDER BY op_time").fetchall()
        return [dict(r) for r in rows]

    # ---------- 意向输入 ----------
    def set_intent(self, run_id: str, period: int, item_type: str, value: float, reason: str = "") -> str:
        if value is not None and value <= 0:
            raise ValueError(f"{item_type} 须 >0")
        item_id = f"intent-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO intent_input(item_id, run_id, period, item_type, value, revised_flag, reason, operator, op_time) "
            "VALUES(?,?,?,?,?,1,?,?,?)",
            (item_id, run_id, period, item_type, value, reason or "录入", "交易员", _now()),
        )
        self.conn.commit()
        return item_id

    def latest_intents(self) -> dict[tuple[int, str], float]:
        rows = self.conn.execute(
            "SELECT period, item_type, value, op_time FROM intent_input ORDER BY op_time"
        ).fetchall()
        out: dict[tuple[int, str], float] = {}
        for r in rows:                      # 逐条覆盖 → 每组保留最新
            out[(r["period"], r["item_type"])] = r["value"]
        return out

    # ---------- 助手留痕 ----------
    def log_chat(self, user_message: str, tool_calls: list[dict], assistant_reply: str,
                 run_id: str = "", period: int | None = None, version: str = "v1") -> None:
        self.conn.execute(
            "INSERT INTO chat_log(ts, run_id, period, user_message, tool_calls, assistant_reply, version) VALUES(?,?,?,?,?,?,?)",
            (_now(), run_id, period, user_message, json.dumps(tool_calls, ensure_ascii=False), assistant_reply, version),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
