"""MCP stdio 客户端：常驻子进程、握手、按 id 匹配调用；支持 CLI 直连（独立调用演示）。"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .server import PROTOCOL_VERSION, ToolError

SERVERS = {"data": "backend.mcp.data_server", "retro": "backend.mcp.retro_server"}
REPO_ROOT = str(Path(__file__).resolve().parents[2])   # 保证子进程 -m backend.* 可导入


class McpClient:
    def __init__(self, server: str, python: str = sys.executable) -> None:
        if server not in SERVERS:
            raise ValueError(f"未知 server {server}；可用：{','.join(SERVERS)}")
        self.server_name = server
        self._proc = subprocess.Popen(
            [python, "-m", SERVERS[server]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=REPO_ROOT)
        self._id = 0
        self._io_lock = threading.Lock()   # stdio 单管道：请求-响应对必须串行（线程池并发安全）
        self._initialize()

    def _send(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        with self._io_lock:
            self._id += 1
            msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if not notify:
                msg["id"] = self._id
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            if notify:
                return None
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    err = (self._proc.stderr.read() if self._proc.stderr else "") or ""
                    raise ToolError(f"MCP server {self.server_name} 无响应：{err[-500:]}")
                resp = json.loads(line)
                if resp.get("id") == self._id:
                    return resp

    def _initialize(self) -> None:
        resp = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {}, "clientInfo": {"name": "retro-skill-runtime", "version": "1.0"}})
        if "error" in resp:
            raise ToolError(f"握手失败：{resp['error']}")
        self._send("notifications/initialized", notify=True)

    def list_tools(self) -> list[dict]:
        resp = self._send("tools/list")
        return resp["result"]["tools"]

    def call(self, name: str, arguments: dict | None = None) -> Any:
        resp = self._send("tools/call", {"name": name, "arguments": arguments or {}})
        result = resp.get("result", {})
        if result.get("isError"):
            raise ToolError(result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:   # noqa: BLE001
            self._proc.kill()


def main() -> int:
    """CLI 直连：python -m backend.mcp.client --server retro --tool actual.availability \
    --args '{"start":"2026-08-01","end":"2026-08-31"}'"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, choices=sorted(SERVERS))
    ap.add_argument("--tool", default="")
    ap.add_argument("--args", default="{}")
    ap.add_argument("--list", action="store_true", help="仅列工具")
    a = ap.parse_args()
    cli = McpClient(a.server)
    try:
        if a.list:
            print(json.dumps(cli.list_tools(), ensure_ascii=False, indent=2))
            return 0
        out = cli.call(a.tool, json.loads(a.args))
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        cli.close()


if __name__ == "__main__":
    raise SystemExit(main())
