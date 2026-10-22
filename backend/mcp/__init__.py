"""连接层 MCP：基于 MCP 协议（stdio 传输，JSON-RPC 2.0 换行分隔）的原子工具底座。

- server.py：服务端基类（工具注册 + 握手 + tools/list + tools/call）
- client.py：客户端（常驻子进程、按 id 匹配、可 CLI 直连）
- data_server.py：核心数据类（SQLite D-A* 读写，独立进程，WAL）
- retro_server.py：计算类（复盘原子计算，独立进程，import 复用既有链）

MCP 层只做原子操作，不承载跨环节业务逻辑——编排只在 skill SOP。
"""
