"""MCP 服务端基座：stdio JSON-RPC 2.0（换行分隔），initialize/tools/list/tools/call。"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"


class ToolError(Exception):
    """工具执行失败（MCP 规范：结果内 isError=true，不是协议错误）。"""


class McpServer:
    def __init__(self, name: str, version: str = "1.0.0") -> None:
        self.name, self.version = name, version
        self._tools: dict[str, dict] = {}

    def tool(self, name: str, description: str = "", schema: dict | None = None
             ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = {
                "name": name, "description": description,
                "inputSchema": schema or {"type": "object", "properties": {}},
                "fn": fn}
            return fn
        return deco

    def list_tools(self) -> list[dict]:
        return [{"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"]} for t in self._tools.values()]

    def call(self, name: str, arguments: dict) -> Any:
        if name not in self._tools:
            raise ToolError(f"未知工具 {name}；可用：{','.join(sorted(self._tools))}")
        return self._tools[name]["fn"](**arguments)

    # ---------- 协议 ----------
    def _result(self, payload: Any) -> dict:
        return {"content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False, default=str)}]}

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version}}}
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            try:
                payload = self.call(msg["params"]["name"], msg["params"].get("arguments") or {})
                return {"jsonrpc": "2.0", "id": mid,
                        "result": {**self._result(payload), "isError": False}}
            except ToolError as e:
                return {"jsonrpc": "2.0", "id": mid,
                        "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}}
            except Exception as e:   # noqa: BLE001 —— 工具内部异常也走 isError 通道
                return {"jsonrpc": "2.0", "id": mid,
                        "result": {"content": [{"type": "text",
                                                "text": f"{type(e).__name__}: {e}\n"
                                                        + traceback.format_exc()[-1500:]}],
                                   "isError": True}}
        if mid is not None:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    def serve(self) -> None:
        """主循环：stdin 逐行读请求，stdout 写响应（无 id 的通知不回）。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
                sys.stdout.flush()
