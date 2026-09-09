#!/usr/bin/env python3
"""全链冒烟：本地 xlsx 驱动 M1→M8，校验 96 点输出完整性与耗时（全量 ≤30s、单点 ≤1s，PRD §8 主指标）。"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.pipeline import Pipeline  # noqa: E402
from backend.store import Store  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "smoke.db"
        pipe = Pipeline(store=Store(db))
        t0 = time.perf_counter()
        result = pipe.run_all()
        full_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        pipe.recalc_point()
        point_ms = (time.perf_counter() - t1) * 1000

        checks: list[tuple[str, bool, str]] = []
        for name, series in [("M6 预测联络线", result.m6["predicted_96"]),
                             ("M7 空间", result.m7["space_96"]),
                             ("M7 开机", result.m7["final_on_96"]),
                             ("M7 负荷率", result.m7["load_rate_96"]),
                             ("M8 最终电价96", result.m8["final_96"]),
                             ("M8 最终电价24", result.m8["final_24"])]:
            ok = len(series) in (96, 24) and all(v is not None for v in series)
            checks.append((f"{name} 输出完整", ok, f"len={len(series)}, 缺={sum(v is None for v in series)}"))

        checks.append(("落点 24 时段", len(result.landing) == 24, f"n={len(result.landing)}"))
        checks.append(("灰度 24 时段", len(result.grey) == 24, f"n={len(result.grey)}"))
        checks.append(("全量 ≤30s", full_ms <= 30_000, f"{full_ms:.0f}ms"))
        checks.append(("单点 ≤1s", point_ms <= 1_000, f"{point_ms:.0f}ms"))
        checks.append(("M7 模式", result.m7["mode"] == "系统开机推演", result.m7["mode"]))
        checks.append(("M8 A-1 回溯日", result.m8["a1_day"] != "", result.m8["a1_day"]))

        print(f"滚撮来源: {result.m1['roll_source']}")
        print(f"M8: A={result.m8['a_day']} A-1={result.m8['a1_day']}（≤0 点 {result.m8['a1_non_pos_points']}）"
              f" k={result.m8['k']:.4f} M1={result.m8['M1']:.4f} C1={result.m8['C1']:.2f}"
              f" M2={result.m8['M2']:.4f} C2={result.m8['C2']:.2f}")
        print(f"M8 临界空间96={result.m8['critical_space_96']}, min预测1={result.m8['min_pred1_96']}, "
              f"整体取预测2={result.m8['used_pred2_96']}")
        print(f"M7: 上半日开机={result.m7['final_on_am']:.1f} 下半日={result.m7['final_on_pm']:.1f} "
              f"硬上限={result.m7['hard_cap']:.1f}")
        sample_lr24 = result.m7["load_rate_24"]
        print(f"M7 负荷率24 前3点: {sample_lr24[:3]}")
        print(f"电价24 前3点: {result.m8['final_24'][:3]}")
        print(f"落点 N 分布: {[l['n'] for l in result.landing]}")
        print(f"耗时: {result.timings_ms}")

        failed = 0
        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}（{detail}）")
            failed += 0 if ok else 1
        print(f"\n{len(checks) - failed}/{len(checks)} 项通过；全链 {full_ms:.0f}ms，单点 {point_ms:.0f}ms")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
