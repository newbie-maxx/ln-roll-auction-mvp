"""skill 运行时桥接：SOP 工具调用路由到 MCP 原子工具（data/retro 两个独立进程），
skill 执行留痕/审计经 MCP data server 落库——逻辑层只配 skill，连接层只有原子操作。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from ..mcp.client import McpClient
from ..skill_runtime import SkillRuntime, ToolError

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_DATA_PREFIX = ("db.", "threshold.", "skill.")


class SkillRunner:
    """进程内单例：持有两个 MCP 长连接客户端 + SkillRuntime。"""

    def __init__(self, skills_dir: Path = SKILLS_DIR) -> None:
        self._data = McpClient("data")
        self._retro = McpClient("retro")
        self._lock = threading.Lock()
        self._runtime = SkillRuntime(
            skills_dir, self._tool_caller,
            known_tools=self._known_tools(),
            persister=self)
        self.start()

    # ---- tool_caller（连接层路由） ----
    def _tool_caller(self, name: str, args: dict):
        """路由到对应 MCP server；list/dict 原样进上下文（${days[0]} 等索引可用）。"""
        server = self._data if name.startswith(_DATA_PREFIX) else self._retro
        return server.call(name, args)

    def _known_tools(self) -> set[str]:
        names = {t["name"] for t in self._data.list_tools()}
        names |= {t["name"] for t in self._retro.list_tools()}
        return names

    # ---- persister（留痕经 data server 原子工具落库） ----
    def log_run(self, record: dict) -> None:
        try:
            self._data.call("skill.run_log", {"record": record})
        except ToolError:
            pass   # 留痕失败不阻断业务，审计可事后补

    def log_audit(self, event: dict) -> None:
        try:
            self._data.call("skill.audit", {"event": event})
        except ToolError:
            pass

    # ---- 对外 API ----
    def start(self) -> dict:
        return {"skills": self._runtime.list_skills()}

    def run(self, skill_id: str, inputs: dict) -> dict:
        with self._lock:      # 复盘重算非线程安全（底账缓存/临时目录），串行执行
            return self._runtime.run(skill_id, inputs)

    def list_skills(self) -> list[dict]:
        return self._runtime.list_skills()

    def set_status(self, skill_id: str, status: str, reason: str = "") -> dict:
        return self._runtime.set_status(skill_id, status, reason)

    def reload(self) -> dict:
        return self._runtime.reload()

    def mcp_status(self) -> dict:
        return {"data": self._data.call("db.ping"),
                "retro_tools": len(self._retro.list_tools())}

    def close(self) -> None:
        self._data.close()
        self._retro.close()


_RUNNER: SkillRunner | None = None
_RUNNER_LOCK = threading.Lock()


def get_runner() -> SkillRunner:
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            _RUNNER = SkillRunner()
        return _RUNNER
