"""模拟成交登记生成器（§0.2.2 Q1/Q2：开发期自动生成可直接使用的模拟数据）。

刻意构造四类日：成交日 ≥10（每日 1~10 笔）、只报没成 ≥2、无登记 ≥3、红绿同日 ≥2；
含 ≥2 个多场次运行日（snapshot_trade_date 人工指定为运行日前 1~3 个日历日）。
确定性：固定种子、按可用日顺序取日；重跑幂等（先清当日 demo 记录再写）。
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .actual import rt_price
from .register import settle
from .store import RetroStore, now_iso


def demo_seed(store: RetroStore, start: str, end: str, seed: int = 20260801) -> dict:
    from .actual import availability
    days = availability(start, end)
    if len(days) < 15:
        raise ValueError("模拟数据需 ≥15 个可用日")
    rng = random.Random(seed)
    filled_days, unfilled_days, mixed_days, empty_days = [], [], [], []
    plan: list[tuple[str, list[dict]]] = []
    cursor = 0

    def take(n: int) -> list[str]:
        nonlocal cursor
        taken = days[cursor:cursor + n]
        cursor += n
        return taken

    for d in take(10):                     # 成交日 10 个
        n = rng.randint(1, 10)
        plan.append((d, [_mk_filled(rng, d) for _ in range(n)]))
        filled_days.append(d)
    for d in take(2):                      # 只报没成 2 个
        plan.append((d, [_mk_unfilled(rng, d), _mk_unfilled(rng, d)]))
        unfilled_days.append(d)
    for d in take(3):                      # 无登记 3 个（留空）
        empty_days.append(d)
    for d in take(2):                      # 红绿同日 2 个
        plan.append((d, [_mk_filled(rng, d), _mk_unfilled(rng, d)]))
        mixed_days.append(d)

    n_written = 0
    with store.conn:
        store.conn.execute(
            "DELETE FROM retro_trade_register WHERE note LIKE 'demo:%'")
    for run_date, recs in plan:
        spot = rt_price(run_date)
        trade_offset = rng.choice([1, 2, 3])
        td = (date.fromisoformat(run_date) - timedelta(days=trade_offset)).isoformat()
        if run_date in mixed_days or rng.random() < 0.2:   # ≥2 个多场次运行日
            td2 = (date.fromisoformat(run_date) - timedelta(days=trade_offset + 2)).isoformat()
        else:
            td2 = td
        for i, rec in enumerate(recs):
            td_i = td if i % 2 == 0 or td2 == td else td2
            s = settle(rec["price"], rec["volume_mwh"], rec["direction"], rec["t"], spot)
            version = store.next_version(
                "retro_trade_register", ["run_date", "t", "result_type", "trader_id"],
                {"run_date": run_date, "t": rec["t"], "result_type": rec["result_type"],
                 "trader_id": "default"})
            import json
            store.append("retro_trade_register", {
                "run_date": run_date, "t": rec["t"], "result_type": rec["result_type"],
                "price": rec["price"], "volume_mwh": rec["volume_mwh"],
                "direction": rec["direction"], "trader_id": "default",
                "entered_at": now_iso(), "note": f"demo:{seed}",
                "source": "自报", "confidence": "低于回单（模拟）",
                "snapshot_trade_date": td_i,
                "settle_points": json.dumps(s["settle_points"], ensure_ascii=False),
                "pnl": s["pnl"], "version": version})
            n_written += 1
    return {"n_written": n_written, "filled_days": filled_days,
            "unfilled_days": unfilled_days, "mixed_days": mixed_days,
            "empty_days": empty_days, "multi_session_days": sorted(
                {d for d, recs in plan if len({r.get('_td') for r in recs}) > 1
                 or any(r.get("multi") for r in recs)})}


def _mk_filled(rng: random.Random, run_date: str) -> dict:
    hour = rng.choice([9, 10, 11, 14, 15, 16, 17, 18])
    return {"run_date": run_date, "t": hour, "result_type": "成交",
            "direction": rng.choice(["买", "卖"]),
            "price": round(rng.uniform(200, 500), 1),
            "volume_mwh": float(rng.choice([60, 80, 100, 120, 140, 160]))}


def _mk_unfilled(rng: random.Random, run_date: str) -> dict:
    hour = rng.choice([10, 13, 15, 18, 20])
    return {"run_date": run_date, "t": hour, "result_type": "未成交",
            "direction": rng.choice(["买", "卖"]),
            "price": round(rng.uniform(250, 520), 1),
            "volume_mwh": float(rng.choice([80, 100, 120, 160]))}
