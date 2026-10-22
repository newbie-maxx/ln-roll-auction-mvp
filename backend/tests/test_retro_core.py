"""复盘口径用例（一）：登记/逐笔收益/词表/聚合/习惯/证据包/归因校验——纯函数口径，不跑全链。

口径来源：复盘PRD-AI归因版 §0.1.2.1（逐点结算）、附录 A/C、§E-A7.7；
复盘MVP_PRD §3.3 P-1（手算样例必须含正负混合小时）。
"""
from __future__ import annotations

import json

from backend.retro import aggregate, attribute, evidence, habit, words
from backend.retro import register as reg
from backend.retro.constants import FACTORS, VALUE_TYPE_OWNER


# ---------------- 登记校验 6 条 ----------------

def test_register_validate_rules():
    base = {"run_date": "2026-08-14", "t": 15, "result_type": "成交",
            "direction": "卖", "price": 283.0, "volume_mwh": 80.0}
    assert reg.validate(base) == []
    assert any("运行日" in e for e in reg.validate({**base, "run_date": "8/14"}))
    assert any("时段" in e for e in reg.validate({**base, "t": 24}))
    assert any("时段" in e for e in reg.validate({**base, "t": None}))
    assert any("结果" in e for e in reg.validate({**base, "result_type": "挂单"}))
    assert any("方向" in e for e in reg.validate({**base, "direction": "买入"}))
    assert any("价格" in e for e in reg.validate({**base, "price": 2000}))
    assert any("价格" in e for e in reg.validate({**base, "price": -2000}))
    assert any("电量" in e for e in reg.validate({**base, "volume_mwh": 0}))
    assert any("电量" in e for e in reg.validate({**base, "volume_mwh": 99999}))


# ---------------- 逐点结算手算样例（P-1 ④：必须能解释清差异来源） ----------------

def test_settle_mixed_sign_hour():
    # 8-14 15 时现货 [295, −50, −48, 294]，卖 283 × 80 MWh → 逐点 +12,820
    s = reg.settle(283.0, 80.0, "卖", 15, _spot(15, [295, -50, -48, 294]))
    assert s["diffs"] == [-12.0, 333.0, 331.0, -11.0]
    assert s["pnl"] == 12820.0            # (-12+333+331-11) × 20
    # 代表价口径对照：均值 123 → +12,800；首点 295 → −960——均与逐点不同（口径证据，非实现）

def test_settle_all_positive_hour():
    # 10 时 [348,348,348,294]，卖 342 × 120 → (+48−6−6−6)×30 = +900
    s = reg.settle(342.0, 120.0, "卖", 10, _spot(10, [348, 348, 348, 294]))
    assert s["pnl"] == 900.0

def test_settle_unfilled_opportunity_cost():
    # 18 时挂 396 未成，卖 160 MWh，现货 [375,375,375,384] → (21+21+21+12)×40 = +3,000
    s = reg.settle(396.0, 160.0, "卖", 18, _spot(18, [375, 375, 375, 384]))
    assert s["pnl"] == 3000.0

def test_settle_buy_direction_and_missing():
    buy = reg.settle(300.0, 40.0, "买", 1, _spot(1, [320, 280, 300, 305]))
    assert buy["diffs"] == [20.0, -20.0, 0.0, 5.0]
    assert buy["pnl"] == 5.0 * 10          # (20−20+0+5)×10
    miss = reg.settle(300.0, 40.0, "买", 1, _spot(1, [320, None, 300, 305]))
    assert miss["pnl"] is None             # 缺任一点不出金额，禁止替代


def _spot(hour: int, vals: list[float]) -> list[float | None]:
    out: list[float | None] = [100.0] * 96
    for i, v in enumerate(vals):
        out[hour * 4 + i] = v
    return out


# ---------------- 词表 / 建议越界 ----------------

def test_words_scan_gates():
    ok = words.scan("已成交溢价合计 +13,720 元；机会成本估算（溢价未实现）合计 +3,000 元。")
    assert ok["ok"], ok["hits"]
    for bad in ("本期赚了 5 万元", "净收益 +12,820", "少赚了 2240 元", "利润 3 万"):
        hit = words.scan(bad)
        assert not hit["ok"] and hit["hits"][0]["kind"].startswith("净额"), bad
    assert not words.scan("c_b 与 share_b 见表").get("ok")   # 技术术语拦截
    assert not words.scan("你的老毛病又犯了").get("ok")       # 口语拦截

def test_advice_boundary():
    assert words.advice_line_ok("建议先核对光伏出力预测（依据：三）")
    for bad in ("建议 10 点报价上调 30 元", "建议明日买入 500 MW", "应在 17 点报 320 元/MWh"):
        assert not words.advice_line_ok(bad), bad


# ---------------- 聚合口径 ----------------

def test_aggregate_24_96_rules():
    v24 = [float(h) for h in range(24)]
    e96 = aggregate.expand24(v24)
    assert len(e96) == 96
    for h in range(24):                       # 只复制不插值：相邻 4 点同值（阶梯）
        assert len(set(e96[h * 4:h * 4 + 4])) == 1
    assert aggregate.to24(e96) == v24
    mixed = [1.0, None, 3.0, 5.0] + [None] * 92
    t = aggregate.to24(mixed)                 # 部分缺点按实有点均值
    assert t[0] == 3.0 and t[1] is None


# ---------------- 习惯统计口径 ----------------

def test_binomial_and_significance_gate():
    assert habit.binom_p_two_sided(20, 20) < 0.000001
    assert abs(habit.binom_p_two_sided(5, 10) - 1.0) < 1e-9
    days = []
    for i in range(25):                       # 25 天风电 Δ 全负 → 显著习惯
        days.append({"date": f"2026-08-{i+1:02d}", "deltas": {"风电": -100.0 - i},
                     "day_type": "无", "main_factor": None, "rev_points": []})
    h = habit.compute_habits(days, "2026-08-01", "2026-08-25")
    h1 = h["H1:风电"]
    assert h1["n"] == 25 and h1["sign_rate"] == 1.0 and h1["significant"] is True
    few = habit.compute_habits(days[:5], "2026-08-01", "2026-08-05")
    assert few["H1:风电"]["significant"] is False        # <20 天只标统计值
    excl = {d["date"]: "类型三" for d in days}
    h2 = habit.compute_habits(days, "2026-08-01", "2026-08-25", exclusions=excl)
    assert h2["_meta"]["n_days"] == 0 and h2["_meta"]["n_excluded"] == 25  # 类型三日整日剔除

def test_divergence_degenerate_flag():
    cur = {"H1:风电": {"significant": True, "sign_rate": 0.9, "n": 25},
           "H2:风电": {"median": -120.0}}
    base = {"H1:风电": {"significant": True, "sign_rate": 0.8, "n": 25},
            "H2:风电": {"median": -100.0}}
    d = habit.divergence(cur, base, g_minus_days=10)
    assert d["degenerate"] is True and "偏松" in d["base_src"]


# ---------------- 证据包确定性与结构 ----------------

def _mini_ctx():
    dec = {"contributions": {f: -1.0 - i for i, f in enumerate(FACTORS)},
           "shares": {f: round(abs(-1.0 - i) / 20, 4) for i, f in enumerate(FACTORS)},
           "residual": [0.5] * 96, "explained_ratio": 0.85}
    delta = {"table": {"电价": {"v_act": [300.0] * 96, "v_rev": [340.0] * 96,
                                "delta": [-40.0] * 96, "missing": [False] * 96},
                       **{f: {"delta": [1.0] * 96, "missing": [False] * 96} for f in FACTORS}},
             "pred_source": "重放值，非当时值", "is_replay": True}
    jump = {"jump_points": [], "points": [{"t": 1, "jump_type": "无", "detail": {}}] * 96,
            "day_type2": False, "day_type3": False, "anomaly": [], "p_set_count": 0,
            "p_set_ratio": 0.0}
    return dec, delta, jump


def test_evidence_pack_structure_and_determinism():
    dec, delta, jump = _mini_ctx()
    p1 = evidence.build("2026-08-14", dec, jump, delta)
    p2 = evidence.build("2026-08-14", dec, jump, delta)
    assert evidence.dumps(p1) == evidence.dumps(p2)          # P-6 逐字段复现
    assert len(p1["contributions"]) == 5 and p1["near_zero"]  # Top5 + 反向证据必须存在
    assert p1["data_quality"]["coverage_rate"] == 1.0
    assert p1["manual_layer"]["available"] is False
    assert all("id" in c for c in p1["contributions"])


# ---------------- 归因校验 ----------------

def test_attribute_validation_rules():
    dec, delta, jump = _mini_ctx()
    pack = evidence.build("2026-08-14", dec, jump, delta)
    stub = attribute.stub_text(pack)
    assert attribute.validate_text(stub, pack)["ok"]         # 桩文本必须过三校验
    bad = stub + "\n另外大约 320 元的误差可能来自煤价。"        # 包外数字 + 包外原因
    check = attribute.validate_text(bad, pack)
    assert not check["ok"]
    assert check["out_of_pack"] or not check["direction_ok"]
    advise = stub.replace("①", "① 建议报 405 元卖出；")
    assert not attribute.validate_text(advise, pack)["ok"]


def test_owner_mapping_covers_all_value_types():
    assert VALUE_TYPE_OWNER["披露"] == "交易中心"
    assert VALUE_TYPE_OWNER["推断"] == VALUE_TYPE_OWNER["补齐"] == VALUE_TYPE_OWNER["计算"] == "系统"
    assert VALUE_TYPE_OWNER["修订"] == VALUE_TYPE_OWNER["意向"] == "交易员"
    assert VALUE_TYPE_OWNER["缺输入"] == "未归属"
    assert set(FACTORS) == set(
        ("负荷", "风电", "光伏", "水电", "核电", "地方燃煤", "联络线", "非市场化", "省间交易总量"))
