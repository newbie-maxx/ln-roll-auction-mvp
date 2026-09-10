"""M7 火电开机 11 步推演与负荷率（PRD §6b，一步一函数、注释逐条对应 §6b.2）。

火电竞价空间(t) = 负荷 − 水电 − 核电 − 地方燃煤 − 风电 − 光伏 − 联络线 − 非市场化出力
（运行日"联络线"项 = 实时联络线预测 = 联络线基线 − 交易员预测省间交易总量，2026-09-10 口径）
双模式（§6b.3）：系统计算（11 步）/ 人工自填（96 点或上/下半日恒值），两版并列留痕互不覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import D_DAY, Params
from ..loaders.xlsx_loader import LoadedData, avg96to24

N = 96


@dataclass
class ThermalInput:
    load: list[float | None]          # 负荷 96
    hydro: list[float | None]         # 水电
    nuclear: list[float | None]       # 核电
    coal: list[float | None]          # 地方燃煤
    wind: list[float | None]          # 风电（总）
    solar: list[float | None]         # 光伏（总）
    tieline: list[float | None]       # 联络线（M6 预测联络线）
    non_market: list[float | None]    # 非市场化


def step1_space(x: ThermalInput) -> list[float | None]:
    """§6b.2 ① 96 点每一时刻火电竞价空间（未折算）。"""
    out: list[float | None] = []
    for t in range(N):
        terms = [x.load[t], x.hydro[t], x.nuclear[t], x.coal[t], x.wind[t], x.solar[t], x.tieline[t], x.non_market[t]]
        out.append(sum(terms[1:]) * -1 + terms[0] if all(v is not None for v in terms) else None)  # type: ignore[operator]
    return out


def step2_folded_space(x: ThermalInput, params: Params) -> list[float | None]:
    """§6b.2 ② 折算新能源系数空间：平衡时段 风电×系数、光伏×系数；非平衡同 ①。"""
    beta = params.balance_flag_96()
    k = params.新能源平衡系数
    out: list[float | None] = []
    for t in range(N):
        terms = [x.load[t], x.hydro[t], x.nuclear[t], x.coal[t], x.tieline[t], x.non_market[t]]
        wf, pv = x.wind[t], x.solar[t]
        if any(v is None for v in terms) or wf is None or pv is None:
            out.append(None)
            continue
        if beta[t]:
            out.append(terms[0] - terms[1] - terms[2] - terms[3] - wf * k - pv * k - terms[4] - terms[5])  # type: ignore[operator]
        else:
            out.append(terms[0] - terms[1] - terms[2] - terms[3] - wf - pv - terms[4] - terms[5])          # type: ignore[operator]
    return out


def step3_reserve_headroom(params: Params) -> float:
    """§6b.2 ③ 正备用+调频所需开机 = (正备用 + 调频容量) / (1 − 受阻系数)。"""
    return (params.正备用 + params.调频容量) / (1 - params.受阻系数)


def step4_required_on(folded: list[float | None], headroom: float) -> list[float | None]:
    """§6b.2 ④ 增加备用后火电所需开机(t) = 折算空间(t) + ③（逐点相加）。"""
    return [v + headroom if v is not None else None for v in folded]  # type: ignore[operator]


def step5_tentative_on(required: list[float | None]) -> float:
    """§6b.2 ⑤ 未校验当日最大开机 = max(④)，作为暂定全天 96 点开机（一次开停 + 全天恒开机粗口径）。"""
    vals = [v for v in required if v is not None]
    if not vals:
        raise ValueError("折算空间全缺，无法推开机（缺输入）")
    return max(vals)


def step6_pos_reserve_surplus(tentative: float, space: list[float | None], params: Params) -> list[float | None]:
    """§6b.2 ⑥ 验证正备用（用未折算空间——扩大安全性）：
    剩余正备用(t) = 暂定开机 × (1 − 受阻系数) − 未折算空间(t)   【业务方 2026-09-10 更正：原 PRD 尾部
    "− 受阻系数"为笔误（量纲 MW vs 百分数），不再减】；盈余(t) = 剩余 − 定义正备用。<0 → 需增机。"""
    out: list[float | None] = []
    for t in range(N):
        s = space[t]
        if s is None:
            out.append(None)
            continue
        remain = tentative * (1 - params.受阻系数) - s
        out.append(remain - params.正备用)
    return out


def step7_pos_add(pos_surplus: list[float | None], params: Params) -> list[float]:
    """§6b.2 ⑦ 正备用盈余不足部分增添开机 = |盈余| ÷ (1 − 受阻系数)（仅盈余为负的时段）。
    【业务方 2026-09-10 更正：原"× (1−受阻)"为笔误——补充 X MW 可用备用需开机 X/(1−受阻)】"""
    return [abs(v) / (1 - params.受阻系数) if v is not None and v < 0 else 0.0 for v in pos_surplus]  # type: ignore[operator]


def step8_neg_reserve_surplus(tentative: float, space: list[float | None], params: Params) -> list[float | None]:
    """§6b.2 ⑧ 验证负备用：剩余负备用(t) = 未折算空间(t) − (暂定开机 × 零价负荷率)；
    盈余 = 剩余 − 定义负备用。<0 → 需减机（v1.7 纠正原条文笔误）。"""
    out: list[float | None] = []
    for t in range(N):
        s = space[t]
        if s is None:
            out.append(None)
            continue
        remain = s - tentative * params.零价点负荷率
        out.append(remain - params.负备用)
    return out


def step9_neg_cut(neg_surplus: list[float | None], params: Params) -> list[float]:
    """§6b.2 ⑨ 负备用盈余不足部分减少开机 = |盈余| ÷ (1 − 受阻系数)。
    【业务方 2026-09-10 更正：原"× (1−受阻)"为笔误——与⑦同口径】"""
    return [abs(v) / (1 - params.受阻系数) if v is not None and v < 0 else 0.0 for v in neg_surplus]  # type: ignore[operator]


def step10_verified_on(tentative: float, add: list[float], cut: list[float], params: Params) -> list[float]:
    """§6b.2 ⑩ 验证后开机(t) = ⑤ + ⑦ − ⑨；逐点钳制 [最小开机方式, 装机 − 检修]（硬上限）。"""
    cap = params.辽宁装机 - params.检修计划
    out: list[float] = []
    for t in range(N):
        v = tentative + add[t] - cut[t]
        if v >= cap:
            v = cap
        if v <= params.最小开机方式:
            v = params.最小开机方式
        out.append(round(v, 4))
    return out


def step11_final_on(verified: list[float]) -> tuple[list[float], float, float]:
    """§6b.2 ⑪ 最终开机：上半日第 1–51 点 / 下半日第 52–96 点各取段内 max 为该段恒定开机
    （火电不能随意启停、优先正备用；负备用不足经负电价回收消化）。"""
    am = max(verified[0:51])
    pm = max(verified[51:96])
    return [am] * 51 + [pm] * 45, am, pm


def load_rate(space: list[float | None], on: list[float]) -> list[float | None]:
    """负荷率(t) = 该点【未折算】火电竞价空间(t) ÷ 该点最终开机(t)（分子未折算、分母最终开机；
    可 >100% 属预警信号不硬拦）。"""
    return [s / on[t] if s is not None and on[t] > 0 else None for t, s in enumerate(space)]  # type: ignore[operator]


def system_mode(x: ThermalInput, params: Params) -> dict:
    """系统计算模式：11 步推演全量（中间值全部保留供追溯/对照）。"""
    space = step1_space(x)
    folded = step2_folded_space(x, params)
    headroom = step3_reserve_headroom(params)
    required = step4_required_on(folded, headroom)
    tentative = step5_tentative_on(required)
    pos_surplus = step6_pos_reserve_surplus(tentative, space, params)
    pos_add = step7_pos_add(pos_surplus, params)
    neg_surplus = step8_neg_reserve_surplus(tentative, space, params)
    neg_cut = step9_neg_cut(neg_surplus, params)
    verified = step10_verified_on(tentative, pos_add, neg_cut, params)
    final, am, pm = step11_final_on(verified)
    lr96 = load_rate(space, final)
    return {
        "mode": "系统开机推演",
        "space_96": space,
        "folded_space_96": folded,
        "reserve_headroom": headroom,
        "required_on_96": required,
        "tentative_on": tentative,
        "pos_surplus_96": pos_surplus,
        "pos_add_96": pos_add,
        "neg_surplus_96": neg_surplus,
        "neg_cut_96": neg_cut,
        "verified_on_96": verified,
        "final_on_96": final,
        "final_on_am": am,
        "final_on_pm": pm,
        "load_rate_96": lr96,
        "load_rate_24": avg96to24(lr96),
        "hard_cap": params.辽宁装机 - params.检修计划,
        "note_step6": "⑥⑦⑨ 已按业务方 2026-09-10 更正：⑥ 不减受阻系数；⑦⑨ 增减机 = |盈余| ÷ (1 − 受阻系数)",
    }


def manual_mode(on_96: list[float], space: list[float | None]) -> dict:
    """人工自填模式：交易员直录 96 点（或上/下半日恒值）；系统版与人工版并列留痕。"""
    if len(on_96) != N:
        raise ValueError(f"人工自填须为 {N} 点，收到 {len(on_96)}")
    lr96 = load_rate(space, on_96)
    return {
        "mode": "人工自填开机",
        "final_on_96": list(on_96),
        "load_rate_96": lr96,
        "load_rate_24": avg96to24(lr96),
    }


def thermal_input_from(data: LoadedData, tieline_96: list[float | None], d_day: str = D_DAY) -> ThermalInput:
    """从装载结果组装所选滚撮日的 M7 输入（联络线 = 实时联络线预测：基线 − 交易员省间交易总量）。"""
    da = data.day_ahead[d_day]
    return ThermalInput(
        load=da["负荷"].values, hydro=da["水电"].values, nuclear=da["核电"].values,
        coal=da["地方燃煤"].values, wind=da["风电"].values, solar=da["光伏"].values,
        tieline=tieline_96, non_market=da["非市场化"].values,
    )
