"""M-09 措辞词表扫描（A/B 双表 + 收益硬闸）：命中即拒绝导出；CSV 公式注入转义。"""
from __future__ import annotations

import re

from . import constants as C

# B 类口语（标题白名单之外的口语；正文括注允许一次，扫描只拦标题由报告层保证，这里拦正文滥用）
_COLLOQUIAL = ("老毛病", "砸到底", "冲上天", "看走眼", "说得清", "跟你反着来了", "对得上")

TECH_RE = re.compile("|".join(re.escape(w) for w in C.FORBIDDEN_TECH_WORDS))
PNL_RE = re.compile("|".join(re.escape(w) for w in C.FORBIDDEN_PNL_WORDS))
# 「盈亏/损益」与数字伴随 = 净额表述（免责句「不含真实盈亏」无数字，不拦）
PNL_NUM_RE = re.compile(r"(盈亏|损益)[^。\n]{0,12}-?\d|(-?\d[^。\n]{0,12}(盈亏|损益))")
COLLOQ_RE = re.compile("|".join(re.escape(w) for w in _COLLOQUIAL))


def scan(text: str) -> dict:
    """返回 {ok, hits: [{kind, word, context}]}；ok=False → 拒绝导出并列出命中词与位置。"""
    hits: list[dict] = []

    def _find(kind: str, regex) -> None:
        for m in regex.finditer(text):
            ctx = text[max(0, m.start() - 12):m.end() + 12].replace("\n", " ")
            hits.append({"kind": kind, "word": m.group(0), "context": ctx})

    _find("净额表述（收益硬闸）", PNL_RE)
    _find("净额表述（收益硬闸）", PNL_NUM_RE)
    _find("技术术语", TECH_RE)
    _find("口语", COLLOQ_RE)
    return {"ok": not hits, "hits": hits}


def advice_line_ok(line: str) -> bool:
    """建议行校验：不得出现报价数值/电量/买卖方向（模式来自 R21/§E-A7.3.3）。"""
    if any(h in line for h in C.ADVICE_FORBIDDEN_HINTS):
        return False
    # 方向词 + 带单位数量（「买入 500 MW」「减仓 2 万千瓦时」）
    if re.search(r"(买入|卖出|买进|卖掉|加仓|减仓)[^。]*\d+\s*(MW|MWh|万千瓦时?|元)", line):
        return False
    if re.search(r"\d+\s*(MW|MWh|万千瓦时?)[^。]*(买入|卖出|买进|加仓|减仓)", line):
        return False
    if re.search(r"\d+\s*元", line):        # 建议行不得出现任何元计价数值（MW 偏差数值允许）
        return False
    return True

def csv_escape(value: str) -> str:
    """公式注入防护：= + - @ 开头加 ' 前缀。"""
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value
