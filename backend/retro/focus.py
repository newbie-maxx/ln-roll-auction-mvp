"""M-07 关注点集：P 集（高价）/ T 集（操作）/ D 集（大价差）/ S 集（单日重点）+ 2×2 分类 + 优先级。

优先级矩阵（§1.5.3）：①改过×价差大 最优先 → ②没改×价差大 → ③改过×价差小（正面样本）
→ ④正常；P 集为独立筛选轴，④中的 P 集点强制出结论（可以是「模型判断对了，不是问题」）。
深析上限 5（S1/S2/S3 各 5，|ΔP| 降序、跃变点优先）。
"""
from __future__ import annotations

from . import constants as C


def build(run_date: str, delta_result: dict, jump_result: dict, theta: dict,
          revisions: list[dict] | None = None, intents: list[dict] | None = None,
          register_ts: list[int] | None = None) -> dict:
    spot = delta_result["table"]["电价"]["v_act"]
    dp = delta_result["table"]["电价"]["delta"]
    theta_high, theta_delta = float(theta["theta_high"]), float(theta["theta_delta"])

    # T 集：当日修订/录入/意向调整所涉时段（点级）
    t_pts: set[int] = set()
    for rev in revisions or []:
        if rev.get("boundary_id") == run_date and rev.get("t"):
            for k in range(4):   # 修订按 96 点录入；小时级留痕扩到该小时 4 点
                base = ((int(rev["t"]) - 1) // 4) * 4
                t_pts.add(base + k + 1)
    for it in intents or []:
        if it:
            p = int(it)
            t_pts.update(range((p - 1) * 4 + 1, p * 4 + 1))

    p_set = [i + 1 for i in range(96) if spot[i] is not None and spot[i] >= theta_high]
    d_ranked = sorted((i for i in range(96) if dp[i] is not None),
                      key=lambda i: (-abs(dp[i]), i))[:10]
    d_set = [i + 1 for i in d_ranked if abs(dp[i]) >= theta_delta]   # Top10 ∪ ≥阈值
    for i in d_ranked:
        if abs(dp[i]) >= theta_delta and (i + 1) not in d_set:
            d_set.append(i + 1)
    jump_ts = [pt["t"] for pt in jump_result["jump_points"]]

    s1 = sorted(t_pts)[: C.DEEP_DIVE_MAX]                                   # 修订点 = T 集
    s2 = sorted(set(register_ts or []))[: C.DEEP_DIVE_MAX]                  # 报价/成交点
    s3_candidates = sorted(set(d_set) | set(p_set) | set(jump_ts),
                           key=lambda t: (-(abs(dp[t - 1]) if dp[t - 1] is not None else 0), t))
    s3 = s3_candidates[: C.DEEP_DIVE_MAX]

    def cls(t: int) -> dict:
        changed, big = t in t_pts, (dp[t - 1] is not None and abs(dp[t - 1]) >= theta_delta)
        if changed and big:
            quadrant = "①改过且价差大"
        elif big:
            quadrant = "②未改但价差大"
        elif changed:
            quadrant = "③改过但价差小"
        else:
            quadrant = "④正常"
        tags = []
        if t in p_set:
            tags.append("高价")
        if t in d_set:
            tags.append("大价差")
        if t in t_pts:
            tags.append("仅改过" if not big else "改过")
        return {"quadrant": quadrant, "tags": tags or ["常规"]}

    union = sorted(set(p_set) | set(d_set) | set(t_pts) | set(jump_ts))
    priority = sorted(
        union, key=lambda t: (0 if jump_result["points"][t - 1]["jump_type"] not in ("无", "缺数") else 1,
                              -abs(dp[t - 1]) if dp[t - 1] is not None else 0, t))[: C.DEEP_DIVE_MAX]
    return {
        "run_date": run_date,
        "p_set": p_set, "t_set": sorted(t_pts), "d_set": sorted(d_set),
        "jump_ts": jump_ts, "s1": s1, "s2": s2, "s3": s3,
        "union": union, "priority": priority,
        "class_of": {t: cls(t) for t in union},
        "p_set_ratio": jump_result["p_set_ratio"],
        "theta_low_hint": jump_result["p_set_ratio"] > 0.05,   # >5% 提示阈值可能偏低
    }
