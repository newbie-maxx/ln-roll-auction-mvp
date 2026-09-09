"""LLM 配置读写：key 只存 .env / 进程内存；回显遮蔽；不写日志。"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


def save_llm_config(base_url: str, api_key: str, model: str) -> None:
    """运行时写入 .env（不存在则创建；.env 已 gitignore）并同步进程环境。"""
    lines: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                lines[k.strip()] = v
    if base_url:
        lines["LLM_BASE_URL"] = base_url
    if api_key:
        lines["LLM_API_KEY"] = api_key          # 只写入 .env/内存，绝不打日志
    if model:
        lines["LLM_MODEL"] = model
    ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n", encoding="utf-8")
    for k, v in lines.items():
        os.environ[k] = v


def mask_key(key: str) -> str:
    return ("sk-****" + key[-4:]) if len(key) > 8 else ("****" if key else "")
