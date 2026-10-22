"""M-08 归因生成：LLM 四段（附录 B 提示词）+ 溯源/方向/越界三校验 + 桩（断网/无 key 回归替身）。

铁律：证据包是唯一输入；输出每个数字必须来自包内（1 位小数容差）；主因必须等于
share 最大项；不得给交易建议、不得引入包外原因。校验不过 → 重试 1 次 → 降级模板填空。
key 安全：key 只从 config.env_llm() 读，不入日志/库/异常文本。
"""
from __future__ import annotations

import re

from ..config import env_llm
from . import constants as C
from .evidence import all_values

SYSTEM_PROMPT = """你是电力交易复盘助手。你的唯一任务是解释给定的「证据包」，不得做任何计算、不得引入证据包以外的信息。

铁律：
1. 你输出的每一个数字，必须来自证据包，并标注其 id（如 B-WIND-01）。不得出现"约""大概""估计"等模糊量词。
2. 不得提出证据包未包含的原因（如煤价、政策、天气过程）。你只能描述证据包中已有的边界偏差。
3. 不得给交易建议、不得评价报价是否合理、不得给出可申报数值。
4. 若 data_quality 显示存在缺失点，你必须在结论中明确说明"该结论不含缺失点位"。
5. 若 manual_layer.available 为 false，你必须写明"本次无人工判断留痕"，不得推测交易员当时的想法。
6. 若 contributions 中存在两项及以上同向偏差，你不得把它们并列为多个独立原因。你必须先提示：
   "以下多项边界同向偏差，可能源于同一系统性口径差，建议先核对口径"，再分别陈述数值。
7. 若 anomaly 非空，你只能以问句写入第④段「待确认」，不得写入第①或第②段；
   不得把"负备用""爬坡"说成原因——本模型没有这两个变量，说了就是编造。
8. 若 jump_type 为「类型三」，你只能陈述门槛实测值与设定值之差、以及边界解释比例，
   不得推断市场参与者为什么改价，也不得说"某项边界是主因"。

输出结构（四段，缺一不可）：
① 一句话主因：必须等于 contributions 中 share 最大项，引用其 id。
② 分项解释：按 contribution 绝对值降序，每项一句，每句含至少一个 id。
③ 人工层：仅当 manual_layer.available 为 true 时输出；否则写"本次无人工判断留痕"。
④ 待确认：需人判断但证据不足的点，只能提问，不得给结论。"""

ADVICE_PATTERNS = tuple(re.escape(p) for p in (
    "建议报", "报价上调", "报价下调", "建议买", "建议卖", "加仓", "减仓",
    "申报价", "挂单价建议"))
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


class AttributionResult(dict):
    pass


def _llm_available() -> bool:
    base_url, key, model = env_llm()
    return bool(base_url and key and model)


def _call_llm(pack_json: str, timeout_s: float = 30.0) -> str:
    import httpx
    base_url, key, model = env_llm()
    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": pack_json}],
              "temperature": 0.0},
        timeout=timeout_s)
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"] or "")


def stub_text(pack: dict) -> str:
    """桩：确定性模板填空（每句引用包内 id；不依赖模型随机性，P-A0 回归用）。"""
    head = pack["headline"]
    contribs = pack["contributions"]
    top = contribs[0] if contribs else None
    same_dir = len([c for c in contribs if c["contribution"] < 0]) >= 2
    lines = []
    if top is not None:
        lines.append(f"① 主因是{top['boundary']}（影响 {top['contribution']} 元/MWh，"
                     f"影响占比 {top['share']}，{top['id']}）。")
    if same_dir:
        lines.append("以下多项边界同向偏差，可能源于同一系统性口径差，建议先核对口径。")
    for c in contribs:
        lines.append(f"{c['boundary']} 影响 {c['contribution']} 元/MWh（{c['id']}）。")
    if pack["manual_layer"]["available"]:
        for it in pack["manual_layer"]["items"]:
            lines.append(f"人工层：{it.get('type', '')}（{it.get('id', '')}）。")
    else:
        lines.append("③ 本次无人工判断留痕。")
    if pack["anomaly"]:
        for a in pack["anomaly"][:3]:
            lines.append(f"④ 待确认：{a['hint_question']}（{a['id']}）")
    else:
        lines.append("④ 待确认：无。")
    if pack["data_quality"]["missing_points"]:
        lines.append("该结论不含缺失点位。")
    return "\n".join(lines)


def validate_text(text: str, pack: dict) -> dict:
    """溯源（数字 100% 在包内，1 位容差）+ 方向（段① = share 最大项）+ 建议越界。"""
    allowed = all_values(pack)
    nums = [float(m) for m in _NUM_RE.findall(text)]
    out_of_pack = []
    for x in nums:
        if not any(abs(x - a) < 0.051 for a in allowed):
            out_of_pack.append(x)
    top = max(pack["contributions"], key=lambda c: abs(c.get("contribution") or 0)) \
        if pack["contributions"] else None
    direction_ok = True
    if top:
        first = text.strip().splitlines()[0] if text.strip() else ""
        direction_ok = (top["id"] in first) or (top["boundary"] in first)
    advice_hits = [p for p in ADVICE_PATTERNS if re.search(p, text)]
    pnl_hits = [w for w in C.FORBIDDEN_PNL_WORDS if w in text]
    ok = not out_of_pack and direction_ok and not advice_hits and not pnl_hits
    return {"ok": ok, "out_of_pack": out_of_pack[:10], "direction_ok": direction_ok,
            "advice_hits": advice_hits, "pnl_hits": pnl_hits}


def generate(pack: dict, use_llm: bool | None = None) -> dict:
    """四段生成：真模型（超时 30s、重试 1 次）→ 校验不过再试 → 降级模板填空。"""
    import json
    from .evidence import dumps
    pack_json = dumps(pack)
    use_llm = _llm_available() if use_llm is None else use_llm
    model_name = "stub"
    text, attempts, degraded = stub_text(pack), 0, True
    if use_llm:
        _, _, model_name = env_llm()
        for attempt in (1, 2):   # 首次 + 重试 1 次
            try:
                cand = _call_llm(pack_json)
                attempts = attempt
                check = validate_text(cand, pack)
                if check["ok"]:
                    text, degraded = cand, False
                    break
            except Exception:
                attempts = attempt
                continue
    final_check = validate_text(text, pack)
    return {"text": text, "model": model_name, "degraded": degraded,
            "attempts": attempts, "check": final_check}
