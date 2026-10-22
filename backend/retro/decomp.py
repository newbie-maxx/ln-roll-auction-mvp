"""M-05 落差分解（8+2 次全链重算，直接复用 Pipeline.run_all，不另写分解公式）。

运行清单（每运行日）：
  基线 P_pred（无替换，含当日修订读时合并——工作库副本提供修订留痕）
  全实况 P_actual（8 类边界全部替换为实况；省间交易总量=交易员项不替换）
  单因子 P_b（9 个输入因子逐项替换；无价格影响的因子 c_b=0 自然落入 near_zero）
恒等式（构造成立）：r(t) = ΔP_total(t) − Σ_b c_b(t)，Σc_b + r = ΔP_total 精确闭合。
"""
from __future__ import annotations

from pathlib import Path

from ..loaders.xlsx_loader import expand24to96
from ..pipeline import Pipeline
from ..store import Store
from . import constants as C
from ._pipeline_util import SubLoader, base_data, work_store_copy
from .actual import actual_day
from .delta import roll_actual_24

TOLERANCE = 0.01   # Σc_b + r 与 ΔP_total 的浮点容差（元/MWh）


class DecompEngine:
    """单日分解引擎；一个实例完成该日全部重算（共享同一工作库副本连接）。"""

    def __init__(self, tmpdir: str | Path) -> None:
        self._store = work_store_copy(tmpdir)
        self._base = base_data()

    def close(self) -> None:
        self._store.close()

    def _run(self, run_date: str, subs: dict[str, list[float | None]] | None):
        loader = SubLoader(self._base, run_date, subs or {})
        pipe = Pipeline(loader=loader, store=self._store)
        pipe.set_target_day(run_date)
        return pipe.run_all()

    def run(self, run_date: str) -> dict:
        act = actual_day(run_date)
        inter24 = roll_actual_24(run_date)
        actual_subs = {b: act[b]["values"] for b in C.BOUNDARIES8}
        base_r = self._run(run_date, None)
        act_r = self._run(run_date, actual_subs)

        def final(r) -> list[float | None]:
            return list(r.m8["final_96"])

        p_pred, p_actual = final(base_r), final(act_r)
        c: dict[str, list[float | None]] = {}
        for factor in C.FACTORS:
            if factor == "省间交易总量":
                if inter24 is None:
                    c[factor] = [None] * 96
                    continue
                subs = {factor: expand24to96(inter24)}
            else:
                subs = {factor: act[factor]["values"]}
            r = self._run(run_date, subs)
            c[factor] = [None if (pb is None or pp is None) else round(pb - pp, 6)
                         for pb, pp in zip(final(r), p_pred)]

        dp = [None if (a is None or p is None) else round(a - p, 6)
              for a, p in zip(p_actual, p_pred)]
        resid = []
        for i in range(96):
            if dp[i] is None:
                resid.append(None)
                continue
            s = sum(v[i] for v in c.values() if v[i] is not None)
            resid.append(round(dp[i] - s, 6))

        # 日级聚合（证据包口径：contribution=日均值，share=|c|/Σ|c|）
        contributions: dict[str, float] = {}
        for f, vals in c.items():
            xs = [v for v in vals if v is not None]
            contributions[f] = round(sum(xs) / len(xs), 4) if xs else None   # type: ignore[assignment]
        abs_sum = sum(abs(v) for v in contributions.values() if v is not None)
        shares = {f: (round(abs(v) / abs_sum, 4) if abs_sum > 0 and v is not None else None)
                  for f, v in contributions.items()}
        res_xs = [abs(v) for v in resid if v is not None]
        dp_xs = [abs(v) for v in dp if v is not None]
        explained = round(1 - sum(res_xs) / sum(dp_xs), 4) if (res_xs and dp_xs and sum(dp_xs) > 0) else None

        # 浮点闭合校验：|Σc_b(t) + r(t) − ΔP_total(t)| 逐点最大（构造上恒等，验证浮点误差）
        closure = None
        if all(v is not None for v in dp) and all(
                v is not None for vals in c.values() for v in vals):
            closure = round(max(
                abs(sum(vals[i] for vals in c.values()) + resid[i] - dp[i])
                for i in range(96)), 8)

        return {
            "run_date": run_date,
            "p_pred": p_pred, "p_actual": p_actual, "dp_total": dp,
            "c": c, "residual": resid,
            "contributions": contributions, "shares": shares,
            "explained_ratio": explained, "closure_max_abs": closure,
            "base_m8": {k: base_r.m8[k] for k in
                        ("k", "M1", "C1", "M2", "C2", "used_pred2_96", "min_pred1_96",
                         "critical_space_96", "a_day", "a1_day")},
            "actual_critical": act_r.m8["critical_space_96"],   # 全实况 run 自洽临界（下临界判定用）
            "actual_m7": {k: act_r.m7[k] for k in
                          ("space_96", "load_rate_96", "pos_surplus_96", "final_on_96")},
            "base_used_pred2": bool(base_r.m8["used_pred2_96"]),
            "actual_used_pred2": bool(act_r.m8["used_pred2_96"]),
        }

    def verify_combo(self, run_date: str, factors: list[str], inter96: list | None = None):
        """缺口倒推验证：组合内因子替换为实况后重算一次。"""
        act = actual_day(run_date)
        subs: dict[str, list[float | None]] = {}
        for f in factors:
            if f == "省间交易总量" and inter96 is not None:
                subs[f] = inter96
            else:
                subs[f] = act[f]["values"]
        return self._run(run_date, subs)
