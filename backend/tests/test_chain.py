"""pytest 口径边界用例（Verification 4）：
空间单点手算、24↔96 可逆、M3 成员一致性、M4 基线算式、M6 恒等式、M7 钳制与上/下半日取 max、
M8 步骤四双分支/钳制/A-1 回溯、落档 Σ=1、现货恒等式、修订 append-only 回退后原值可查。
"""
from __future__ import annotations

import math

import pytest

from backend.config import A_DAY, D_DAY, Params
from backend.loaders.xlsx_loader import LoadedData, Series, avg96to24, expand24to96
from backend.modules import m5_interprovincial, m8_pricing
from backend.modules.m3_similar import a_dimension, run_m3
from backend.modules.m4_baseline import day_baseline, run_m4
from backend.modules.m6_tieline import run_m6
from backend.modules.m7_thermal import (ThermalInput, load_rate, step1_space, step10_verified_on,
                                        step11_final_on, system_mode)
from backend.store import Store


# ---------- helpers ----------

def make_series(values, kind="披露"):
    if isinstance(values, Series):        # 已是 Series → 复制并统一标注
        n = len(values.values)
        return Series(list(values.values), [kind] * n, [None] * n, [None] * n)
    return Series.make(list(values), kind)


def flat(v, n=96):
    return [v] * n


def synth_loaded(days_boundaries: dict[str, dict[str, list]], realtime: dict | None = None,
                 roll: dict | None = None) -> LoadedData:
    """构造合成 LoadedData：days_boundaries[day][sheet] = 96 点列表（或 Series）。"""
    day_ahead = {d: {k: make_series(v) for k, v in sheets.items()} for d, sheets in days_boundaries.items()}
    rt = realtime or {}
    return LoadedData(
        days=sorted(days_boundaries.keys()),
        day_ahead=day_ahead,
        realtime={d: {k: make_series(v) for k, v in sheets.items()} for d, sheets in rt.items()},
        roll_auction=roll or {d: {"volume24": [10.0] * 24, "price24": [350.0] * 24} for d in days_boundaries},
        roll_source="合成（测试）",
        warnings=[],
    )


# ---------- 空间公式 ----------

def test_space_hand_computation():
    """手算：46000 − 2000 − 5000 − 3000 − 4000 − 1000 − 6000 − 2500 = 22500。"""
    x = ThermalInput(load=flat(46000), hydro=flat(2000), nuclear=flat(5000), coal=flat(3000),
                     wind=flat(4000), solar=flat(1000), tieline=flat(6000), non_market=flat(2500))
    assert step1_space(x)[0] == 22500


def test_space_missing_term_is_none():
    x = ThermalInput(load=flat(1), hydro=[None] * 96, nuclear=flat(1), coal=flat(1),
                     wind=flat(1), solar=flat(1), tieline=flat(1), non_market=flat(1))
    assert step1_space(x)[0] is None


# ---------- 24↔96 ----------

def test_expand_and_avg_roundtrip():
    s24 = [100 + i for i in range(24)]
    e = expand24to96(s24)
    assert len(e) == 96 and e[0] == 100 and e[95] == 123 and e[3] == 100
    assert avg96to24(e) == [float(v) for v in s24]


def test_avg_skips_missing():
    assert avg96to24([1, 2, 3, 4, None, 8, 8, 8])[:2] == [2.5, 8.0]


# ---------- M3 ----------

def test_m3_membership_consistency():
    """同一容差下成员与手算一致（逐点检索，A(t) 六项算式）。"""
    params = Params(m3_tol_mw=300.0, m3_scope_days=10)
    hist_days = [f"2026-08-{d:02d}" for d in range(1, 11)] + [A_DAY, D_DAY]
    boundaries: dict[str, dict[str, list]] = {}
    for i, d in enumerate(hist_days):
        boundaries[d] = {
            "负荷": flat(40000), "风电": flat(2000 + 100.0 * i), "光伏": flat(800),
            "核电": flat(3000), "水电": flat(5000), "非市场化": flat(1200), "地方燃煤": flat(800),
        }
    data = synth_loaded(boundaries)
    res = run_m3(data, params)
    # D 日（i=11）A(t) 手算 = 40000 − (2000+1100) − 800 − 3000 − 5000 − 1200 − 800
    expect_a = 40000 - (2000 + 100.0 * 11) - 800 - 3000 - 5000 - 1200 - 800
    assert a_dimension(data, D_DAY)[0] == pytest.approx(expect_a)
    # t=0 成员 = |A_hist(i) − A_D(11)| ≤ 300 且经放宽（默认范围 10 日内 i=2..10 与 11 差 ≤300 → ≥3 成员，不触发放宽）
    members = res["a_dimension_groups"][0]["members"]
    idx_of = {d: i for i, d in enumerate(hist_days)}
    manual = [d for d in hist_days[:-1] if abs(100.0 * idx_of[d] - 100.0 * 11) <= 300]
    assert sorted(members) == sorted(manual)


def test_m3_relaxes_when_few():
    """默认范围 N<3 → 放宽至全部历史日并标注。"""
    params = Params(m3_tol_mw=1.0, m3_scope_days=3)
    hist_days = [f"2026-08-{d:02d}" for d in range(1, 6)] + [D_DAY]
    boundaries = {}
    for i, d in enumerate(hist_days):
        boundaries[d] = {"负荷": flat(40000 + 1000.0 * i), "风电": flat(1000), "光伏": flat(500),
                         "核电": flat(3000), "水电": flat(5000), "非市场化": flat(1200), "地方燃煤": flat(800)}
    data = synth_loaded(boundaries)
    res = run_m3(data, params)
    g = res["a_dimension_groups"][0]
    assert g["relaxed"] is True and "已放宽" in g["scope"]


# ---------- M4 ----------

def test_m4_baseline_formula():
    """基线 = 滚撮(24→96 展开) + 日前联络线；抽查算术可复核。"""
    roll = {"2026-08-01": {"volume24": [10.0] * 24, "price24": [350.0] * 24},
            "2026-08-02": {"volume24": [20.0] * 24, "price24": [350.0] * 24}}
    data = synth_loaded({
        "2026-08-01": {"联络线": flat(5000)},
        "2026-08-02": {"联络线": flat(7000)},
        D_DAY: {"联络线": flat(0)},
    }, roll=roll)
    b1 = day_baseline(data, "2026-08-01")
    b2 = day_baseline(data, "2026-08-02")
    # 符号口径（2026-09-10）：基线 = 日前联络线 − 滚撮量（正滚撮=买入 抬低基线）
    assert b1 is not None and b1[0] == 4990 and b1[4] == 4990
    assert b2 is not None and b2[95] == 6980
    # 缺联络线日 → None 不参与均值
    empty = synth_loaded({D_DAY: {}}, roll={D_DAY: {"volume24": [1.0] * 24, "price24": [1.0] * 24}})
    assert day_baseline(empty, D_DAY) is None


# ---------- M5 现货恒等式 ----------

def test_spot_identity():
    """历史日自算省间现货 = 日前联络线 − 实时联络线。"""
    data = synth_loaded(
        {"2026-08-01": {"联络线": flat(6000)}},
        realtime={"2026-08-01": {"联络线": flat(5500)}},
    )
    spot = m5_interprovincial.compute_spot(data, "2026-08-01")
    assert spot[0] == 500 and all(v == 500 for v in spot)


# ---------- M6 恒等式 ----------

def test_m6_identity():
    """预测联络线 = 基线均值 − 省间交易总数（逐点）。"""
    params = Params()
    hist = [f"2026-08-{d:02d}" for d in range(1, 6)] + [A_DAY, D_DAY]
    boundaries = {}
    for d in hist:
        boundaries[d] = {"负荷": flat(40000), "风电": flat(2000), "光伏": flat(800),
                         "核电": flat(3000), "水电": flat(5000), "非市场化": flat(1200),
                         "地方燃煤": flat(800), "联络线": flat(6000),
                         "日前电价": flat(380)}
    data = synth_loaded(boundaries)
    m3 = run_m3(data, params)
    m4 = run_m4(data, params, m3)
    m5 = m5_interprovincial.run_m5(data, params)
    m6 = run_m6(data, params, m4, m5)
    for t in (0, 37, 95):
        m, s = m4["mean_96"][t], m5["total_96"][t]
        if m is not None and s is not None:
            assert m6["predicted_96"][t] == pytest.approx(m - s)


# ---------- M7 ----------

def test_m7_clamp_and_halfday_max():
    """步骤 10 钳制 [最小开机, 装机−检修]；步骤 11 上/下半日各取段内 max。"""
    params = Params(辽宁装机=10000.0, 检修计划=1000.0, 最小开机方式=1000.0)
    verified = step10_verified_on(tentative=20000.0, add=[0.0] * 96, cut=[0.0] * 96, params=params)
    assert all(v == 9000.0 for v in verified)                 # 硬上限 = 装机 − 检修
    verified2 = step10_verified_on(tentative=100.0, add=[0.0] * 96, cut=[0.0] * 96, params=params)
    assert all(v == 1000.0 for v in verified2)                # 最小开机方式
    mixed = [5000.0] * 50 + [6000.0] + [4000.0] * 45          # idx50 属上半日（1–51 点）
    final, am, pm = step11_final_on(mixed)
    assert am == 6000 and pm == 4000
    assert final[0] == 6000 and final[50] == 6000 and final[51] == 4000 and final[95] == 4000
    x = ThermalInput(load=flat(40000), hydro=flat(5000), nuclear=flat(3000), coal=flat(800),
                     wind=flat(2000), solar=flat(800), tieline=flat(6000), non_market=flat(1200))
    lr = load_rate(step1_space(x), final)
    assert lr[0] == pytest.approx(step1_space(x)[0] / final[0])


def test_m7_full_chain_runs():
    params = Params(辽宁装机=28715.0, 检修计划=2000.0, 最小开机方式=15000.0)
    x = ThermalInput(load=flat(40000), hydro=flat(5000), nuclear=flat(3000), coal=flat(800),
                     wind=flat(2000), solar=flat(800), tieline=flat(6000), non_market=flat(1200))
    out = system_mode(x, params)
    cap = params.辽宁装机 - params.检修计划
    assert all(params.最小开机方式 <= v <= cap for v in out["final_on_96"])
    assert len(out["final_on_96"]) == 96 and len(out["load_rate_24"]) == 24


# ---------- M8 ----------

def m8_data(a_price_fn, a1_price_fn):
    hist = ["2026-08-28", "2026-08-29", A_DAY, D_DAY]
    boundaries = {}
    for i, d in enumerate(hist):
        load = [30000 + 100 * t + 1000 * i for t in range(96)]
        boundaries[d] = {"负荷": load, "风电": flat(2000), "光伏": flat(800),
                         "核电": flat(3000), "水电": flat(5000), "非市场化": flat(1200),
                         "地方燃煤": flat(800), "联络线": flat(6000),
                         "日前电价": flat(380)}
    boundaries[A_DAY]["日前电价"] = make_series(a_price_fn())
    boundaries["2026-08-29"]["日前电价"] = make_series(a1_price_fn())
    data = synth_loaded(boundaries)
    data.roll_auction = {d: {"volume24": [10.0] * 24, "price24": [350.0] * 24} for d in hist}
    return data


def run_m8_on(data, params=None):
    from backend.modules.m7_thermal import thermal_input_from
    params = params or Params()
    m3 = run_m3(data, params)
    m4 = run_m4(data, params, m3)
    m5 = m5_interprovincial.run_m5(data, params)
    m6 = run_m6(data, params, m4, m5)
    m7 = system_mode(thermal_input_from(data, m6["predicted_96"]), params)
    return m8_pricing.run_pricing(data, params, m7["space_96"], m7["final_on_96"], m6["predicted_96"])


def test_m8_branch_pred1():
    """分支 A：预测1 最小 ≥10 → 空间>临界的点取预测1。"""
    data = m8_data(a_price_fn=lambda: [0.05 * (30000 + 100 * t) + 200 for t in range(96)],
                   a1_price_fn=lambda: [0.03 * (30000 + 100 * t) + 100 for t in range(96)])
    out = run_m8_on(data)
    assert out["used_pred2_96"] is False
    assert any(v != out["clamp"][0] for v in out["final_96"])


def test_m8_branch_pred2_and_clamp():
    """分支 B：预测1 全序列最小 <10 → 整体取预测2；越界统一钳制。"""
    data = m8_data(a_price_fn=lambda: [-400.0] * 96,
                   a1_price_fn=lambda: [0.05 * (30000 + 100 * t) + 150 for t in range(96)])
    out = run_m8_on(data)
    assert out["used_pred2_96"] is True
    assert out["min_pred1_96"] == -100
    for v in out["pred1_96"] + out["pred2_96"] + out["final_96"]:
        assert v is None or -100 <= v <= 1500


def test_m8_a1_backtrack():
    """A-1 回溯：电价 ≤0 点 >48 的日被跳过。"""
    days = ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30"]

    def mk(non_pos):
        return [0 if i < non_pos else 350 for i in range(96)]

    prices = {"2026-08-30": mk(0), "2026-08-29": mk(50), "2026-08-28": mk(10),
              "2026-08-27": mk(2), "2026-08-26": mk(0)}
    day, non_pos = m8_pricing.find_a1_day(days, prices, "2026-08-30")
    assert (day, non_pos) == ("2026-08-28", 10)


def test_bin_probs_sum_to_one():
    prices = [-50, 0, 120, 250, 260, 499, 900, 1499, 700, 710]
    stats = m8_pricing.bin_prices(prices, -100, 1500, 100)
    assert stats["n"] == len(prices) and stats["out_of_range"] == 0
    assert math.isclose(sum(b["prob"] for b in stats["bins"]), 1.0, abs_tol=1e-12)
    assert sum(b["count"] for b in stats["bins"]) == stats["n"]


def test_landing_insufficient_sample():
    data = m8_data(a_price_fn=lambda: [380.0] * 96, a1_price_fn=lambda: [370.0] * 96)
    params = Params(相似日阈值=0.001)          # 阈值极小 → 基本无相似日
    landing = m8_pricing.landing_stats(data, params, [0.5] * 24, 10)
    assert landing["n"] < 3


# ---------- 修订 append-only ----------

def test_revision_append_only_and_rollback(tmp_path):
    store = Store(tmp_path / "t.db")
    run_id = store.new_run("2026-08-31", {})
    store.upsert_master(run_id, D_DAY, "负荷", 37, 44000.0, value_type="披露")
    rev1 = store.append_revision(D_DAY, "负荷", 37, 46000.0, "午间负荷上调测试", run_id=run_id, period=10)
    assert store.effective_value(D_DAY, "负荷", 37) == 46000.0
    master = store.conn.execute(
        "SELECT value FROM boundary_master WHERE boundary_id=? AND boundary_type=? AND period=?",
        (D_DAY, "负荷", 37)).fetchone()
    assert master["value"] == 44000.0        # 原值未被覆盖
    rev2 = store.rollback_revision(rev1)
    assert store.effective_value(D_DAY, "负荷", 37) == 44000.0
    chain = store.revision_chain(D_DAY, "负荷", 37)
    assert len(chain) == 2
    assert chain[0]["status"] == "已回退" and chain[0]["rev_id"] == rev1
    assert chain[1]["rev_id"] == rev2 and chain[1]["revised_value"] == 44000.0
    with pytest.raises(ValueError):
        store.append_revision(D_DAY, "负荷", 1, 1.0, "  ")   # 空理由被拒（2026-09-10 起不限字数）
    store.append_revision(D_DAY, "负荷", 2, 46001.0, "短")   # 短理由（非空）被接受
    assert store.effective_value(D_DAY, "负荷", 2) == 46001.0
    store.close()


# ---------- M7 步骤⑥⑦⑨ 修正口径（业务方 2026-09-10 更正） ----------

def test_m7_step6_no_blocked_coeff_subtraction():
    """⑥ 剩余正备用 = 暂定开机 × (1−受阻) − 空间（不再减受阻系数）。"""
    from backend.modules.m7_thermal import step6_pos_reserve_surplus
    params = Params(受阻系数=0.10, 正备用=3000.0)
    x = ThermalInput(load=flat(40000), hydro=flat(5000), nuclear=flat(3000), coal=flat(800),
                     wind=flat(2000), solar=flat(800), tieline=flat(6000), non_market=flat(1200))
    space = step1_space(x)                     # 全天恒 40000−18800 = 21200
    tentative = 25000.0
    surplus = step6_pos_reserve_surplus(tentative, space, params)
    expect = 25000 * 0.9 - 21200 - 3000        # 22500 − 21200 − 3000 = −1700
    assert surplus[0] == pytest.approx(expect)
    # 旧口径（多减受阻系数 0.1）会得到 −1700.1；修正后无该尾差
    assert surplus[0] == pytest.approx(-1700.0)


def test_m7_step7_step9_divide_not_multiply():
    """⑦⑨ 增/减机 = |盈余| ÷ (1−受阻)（原 × 为笔误）。"""
    from backend.modules.m7_thermal import step7_pos_add, step9_neg_cut
    params = Params(受阻系数=0.10)
    surplus = [-2500.0, 500.0, None] * 32       # 负盈余 / 正常 / 缺
    add = step7_pos_add(surplus, params)
    cut = step9_neg_cut(surplus, params)
    assert add[0] == pytest.approx(2500 / 0.9)     # 除以
    assert add[1] == 0.0 and add[2] == 0.0
    assert cut[0] == pytest.approx(2500 / 0.9)


def test_intent_partial_update_and_explicit_clear(tmp_path):
    """意向录入语义（2026-09-11）：显式 None = 清空该字段，未传字段保持不变，≤0 拒绝且不落库。"""
    from backend.loaders.upload_manager import UploadManager
    from backend.pipeline import Pipeline

    p = Pipeline(store=Store(tmp_path / "t.db"), uploads=UploadManager(tmp_path / "up"))
    p.set_intent(5, listPrice=300.0, listVolume=100.0, liftPrice=280.0, liftVolume=80.0)
    assert p.intents()[5] == {"listPrice": 300.0, "listVolume": 100.0, "liftPrice": 280.0, "liftVolume": 80.0}

    p.set_intent(5, listPrice=310.0)                    # 部分更新：其余字段不变
    assert p.intents()[5] == {"listPrice": 310.0, "listVolume": 100.0, "liftPrice": 280.0, "liftVolume": 80.0}

    p.set_intent(5, listVolume=None)                    # 显式 None 清空
    assert p.intents()[5]["listVolume"] is None
    assert p.intents()[5]["listPrice"] == 310.0

    with pytest.raises(ValueError):                     # 非法值拒绝且原值保留
        p.set_intent(5, listPrice=0)
    assert p.intents()[5]["listPrice"] == 310.0


def test_grey_evaluate_seller_buyer_split():
    """灰度量价买卖分列口径（2026-09-11 业务锁定）：
    卖方 收益=(挂牌价−最小区间两端)×挂牌量(概率=首档)、亏损=(最大区间两端−挂牌价)×挂牌量(概率=末档)；
    买方 收益=(最大区间两端−摘牌价)×摘牌量(概率=末档)、亏损=(摘牌价−最小区间两端)×摘牌量(概率=首档)。"""
    from backend.config import Params
    from backend.modules.m8_pricing import grey_evaluate

    params = Params(现货下限=-100.0, 现货上限=1500.0, 区间宽度=100)
    landing = {"bins": [{"lo": -100.0, "hi": 0.0, "prob": 0.25}, {"lo": 1400.0, "hi": 1500.0, "prob": 0.125}]}
    g = grey_evaluate({"listPrice": 385.0, "listVolume": 100.0, "liftPrice": 700.0, "liftVolume": 50.0}, landing, params)
    s, b = g["seller"], g["buyer"]
    assert s["gain_price"] == [385 - 0, 385 - (-100)]           # 挂牌价 − 最小区间两端
    assert s["loss_price"] == [1400 - 385, 1500 - 385]          # 最大区间两端 − 挂牌价
    assert s["gain_amount"] == [(385 - 0) * 100, (385 + 100) * 100]
    assert s["loss_amount"] == [(1400 - 385) * 100, (1500 - 385) * 100]
    assert s["prob_gain"] == 0.25 and s["prob_loss"] == 0.125   # 收益贴首档、亏损贴末档
    assert b["gain_price"] == [1400 - 700, 1500 - 700]          # 最大区间两端 − 摘牌价
    assert b["loss_price"] == [700 - 0, 700 - (-100)]           # 摘牌价 − 最小区间两端
    assert b["gain_amount"] == [(1400 - 700) * 50, (1500 - 700) * 50]
    assert b["prob_gain"] == 0.125 and b["prob_loss"] == 0.25

    # 两侧独立：只填买方 → 卖方不评估，买方正常
    g2 = grey_evaluate({"liftPrice": 700.0, "liftVolume": 50.0}, landing, params)
    assert g2["seller"]["evaluated"] is False and "未录意向挂牌价" in g2["seller"]["reason"]
    assert g2["buyer"]["evaluated"] is True
    # 缺摘牌量 → 买方不评估
    g3 = grey_evaluate({"liftPrice": 700.0}, landing, params)
    assert g3["buyer"]["evaluated"] is False and "未录意向摘牌量" in g3["buyer"]["reason"]
