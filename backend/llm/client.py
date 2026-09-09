"""OpenAI 兼容 LLM 客户端：/chat/completions + function-calling 循环（单轮 ≤8 次工具调用）。

- httpx 同步调用，携带 tools（llm/tools.py 注册表）；工具结果以 role=tool 回填后继续，
  直到产出最终回答或达上限。
- 无 key / 网络错 → 结构化错误（api 层转为前端提示），不抛裸异常。
- key 只经请求头传输，不写日志。
"""
from __future__ import annotations

import json

import httpx

from ..config import env_llm
from .prompts import SYSTEM_PROMPT, context_prompt
from .tools import TOOLS_SPEC, build_registry, execute

MAX_TOOL_CALLS = 8
TIMEOUT_S = 120.0


class LlmError(RuntimeError):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def _client_config() -> tuple[str, str, str]:
    cfg = env_llm()
    if not cfg["api_key"]:
        raise LlmError("未配置 LLM_API_KEY", "请在设置面板或 .env 配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
    if not cfg["base_url"] or not cfg["model"]:
        raise LlmError("LLM 配置不完整", "需同时配置 LLM_BASE_URL 与 LLM_MODEL")
    return cfg["base_url"].rstrip("/"), cfg["api_key"], cfg["model"]


def _post_chat(base_url: str, api_key: str, model: str, messages: list[dict],
               tools: list[dict] | None = None) -> dict:
    payload: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        resp = httpx.post(f"{base_url}/chat/completions", json=payload, timeout=TIMEOUT_S,
                          headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as e:
        raise LlmError(f"网络错误：{e.__class__.__name__}", "检查 LLM_BASE_URL 可达性（内网网关或公网代理）") from e
    if resp.status_code != 200:
        raise LlmError(f"LLM 服务返回 {resp.status_code}", resp.text[:300])
    data = resp.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LlmError("LLM 返回格式异常", json.dumps(data, ensure_ascii=False)[:300]) from e


def chat_with_tools(user_message: str, period: int | None = None,
                    pipe=None, get_result=None) -> tuple[str, list[dict]]:
    """主入口：返回 (最终回答, 工具调用留痕列表)。pipe/get_result 缺省时延迟导入 api 的全局实例。"""
    if pipe is None or get_result is None:
        from ..api import PIPE, _result
        pipe, get_result = PIPE, _result

    base_url, api_key, model = _client_config()
    registry = build_registry(pipe, get_result)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n## 当前上下文\n" + context_prompt(period)},
        {"role": "user", "content": user_message},
    ]
    tool_trace: list[dict] = []

    for _ in range(MAX_TOOL_CALLS + 1):
        msg = _post_chat(base_url, api_key, model, messages, tools=TOOLS_SPEC)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", tool_trace
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments", "")
            result = execute(registry, name, args)
            tool_trace.append({"name": name, "arguments": args, "result": result[:2000]})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if len(tool_trace) >= MAX_TOOL_CALLS:
            messages.append({"role": "user",
                             "content": "（系统提示：已达单轮工具调用上限，请基于以上信息给出最终回答）"})
    final = _post_chat(base_url, api_key, model, messages, tools=None)
    return final.get("content") or "", tool_trace
