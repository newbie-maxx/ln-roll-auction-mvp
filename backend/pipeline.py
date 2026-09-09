"""M1→M8 编排 + 单点联动重算。

- 装载一次（xlsx 慢），**原值快照**缓存在进程内且永不改动；boundary_master 只写装载原值。
- 每次重算在原值快照的内存合并副本上进行（修订读时合并：生效值 = 最新有效修订 ?? 原值）。
- 全部输出带推导理由字段（样本成员/算式/系数/中间值）；耗时逐模块记录（§8：全量 ≤30s、单点 ≤1s）。
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

from .config import D_DAY, DEFAULT_PARAMS, Params
from .loaders.xlsx_loader import LoadedData, XlsxLoader
from .modules import (m2_distributed, m3_similar, m4_baseline, m5_interprovincial, m6_tieline,
                      m7_thermal, m8_pricing)
from .store import Store

# 可改边界白名单（docu/智能体工具与技能设计.md §5；火电开机走 M7 双模式不在此列）
BOUNDARY_WHITELIST = ("负荷", "风电", "光伏", "水电", "核电", "地方燃煤", "非市场化", "联络线")


@dataclass
class ChainResult:
    params: Params
    m1: dict
    m2: dict
    m3: dict
    m4: dict
    m5: dict
    m6: dict
    m7: dict
    m8: dict
    landing: list = field(default_factory=list)     # 24 时段
    grey: list = field(default_factory=list)        # 24 时段
    timings_ms: dict = field(default_factory=dict)
    run_id: str = ""

    def to_json(self, period: int | None = None) -> dict:
        d = {
            "dates": {"D": D_DAY},
            "params": self.params.as_dict(),
            "run_id": self.run_id,
            "m1": {"roll_source": self.m1["roll_source"], "warnings": self.m1["warnings"]},
            "m7": {
                "mode": self.m7["mode"], "final_on_96": self.m7["final_on_96"],
                "space_96": self.m7["space_96"],
                "load_rate_96": self.m7["load_rate_96"], "load_rate_24": self.m7["load_rate_24"],
                "note_step6": self.m7.get("note_step6"),
            },
            "m8": self.m8,
            "landing_24": self.landing,
            "grey_24": self.grey,
            "timings_ms": self.timings_ms,
        }
        if period is not None and 1 <= period <= 24:
            d["period_detail"] = {
                "space_4p": self.m7["space_96"][(period - 1) * 4:period * 4],
                "load_rate_4p": self.m7["load_rate_96"][(period - 1) * 4:period * 4],
                "load_rate_24": self.m7["load_rate_24"][period - 1],
                "price_4p": self.m8["final_96"][(period - 1) * 4:period * 4],
                "price_24": self.m8["final_24"][period - 1],
                "landing": self.landing[period - 1],
                "grey": self.grey[period - 1],
            }
        return d


class Pipeline:
    def __init__(self, loader: XlsxLoader | None = None, store: Store | None = None) -> None:
        self.loader = loader or XlsxLoader()
        self.store = store or Store()
        self._pristine: LoadedData | None = None        # 装载原值快照，永不改动
        self._pristine_run_id: str = ""
        self._last_params: Params = DEFAULT_PARAMS
        self._m7_manual_on: list[float] | None = None   # M7 人工自填开机（与系统版并列）
        self._intents: dict[int, dict] = {}             # period -> {listPrice, liftPrice, volume}

    # ---------- 数据 ----------
    def loaded(self) -> LoadedData:
        """装载一次；原值快照入 boundary_master（只 INSERT/REPLACE-by-key，数值语义只写原值）。"""
        if self._pristine is None:
            self._pristine = self.loader.load()
            self._pristine_run_id = self.store.new_run(self._pristine.days[-1], DEFAULT_PARAMS.as_dict())
            da = self._pristine.day_ahead[D_DAY]
            for boundary in BOUNDARY_WHITELIST:
                if boundary in da:
                    for idx in range(96):
                        self.store.upsert_master(self._pristine_run_id, D_DAY, boundary, idx + 1,
                                                 da[boundary].values[idx],
                                                 value_type=da[boundary].kinds[idx],
                                                 src_day=da[boundary].src_days[idx])
        return self._pristine

    def set_params(self, patch: dict) -> Params:
        candidate = self._last_params.with_overrides(patch)
        errs = candidate.validate()
        if errs:
            raise ValueError("；".join(errs))
        self._last_params = candidate
        return self._last_params

    @property
    def params(self) -> Params:
        return self._last_params

    def set_manual_on(self, on_96: list[float] | None) -> None:
        """M7 双模式：录入/清除人工自填开机（与系统版并列留痕，切换不丢失）。"""
        if on_96 is not None and len(on_96) != 96:
            raise ValueError("人工自填开机须为 96 点")
        self._m7_manual_on = on_96

    def set_intent(self, period: int, **kv) -> None:
        if not 1 <= period <= 24:
            raise ValueError("period 须 ∈ 1..24")
        cur = dict(self._intents.get(period, {"listPrice": None, "liftPrice": None, "volume": None}))
        cur.update({k: v for k, v in kv.items() if v is not None})
        for key in ("listPrice", "liftPrice", "volume"):
            if cur[key] is not None and cur[key] <= 0:      # type: ignore[operator]
                raise ValueError(f"{key} 须 >0")
        self._intents[period] = cur

    def intents(self) -> dict[int, dict]:
        return self._intents

    # ---------- 全链 ----------
    def run_all(self) -> ChainResult:
        """M1→M8 全链：在原值快照的合并副本上计算（修订读时合并）。"""
        t0 = time.perf_counter()
        pristine = self.loaded()
        data = copy.deepcopy(pristine)
        params = self._last_params
        timings: dict[str, float] = {}

        def lap(name: str, t: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - t) * 1000, 2)
            return now

        # M1（装载口径汇总）
        t = time.perf_counter()
        m1 = {"roll_source": pristine.roll_source, "warnings": list(pristine.warnings),
              "days": pristine.days, "ready": True}
        t = lap("m1", t)

        # 修订读时合并 → D 日生效边界（原值仍在 boundary_master / 修订链）
        da = data.day_ahead[D_DAY]
        merged_points = 0
        for boundary in BOUNDARY_WHITELIST:
            if boundary not in da:
                continue
            for idx in range(96):
                eff = self.store.effective_value(D_DAY, boundary, idx + 1)
                if eff is not None:
                    da[boundary].values[idx] = eff
                    da[boundary].annotate(idx, "计算", note="人工修订生效（原值与修订链可查）")
                    merged_points += 1
        t = lap("merge_revisions", t)
        m1["merged_points"] = merged_points

        m2 = m2_distributed.infer_distributed(data)
        t = lap("m2", t)
        m3 = m3_similar.run_m3(data, params)
        t = lap("m3", t)
        m4 = m4_baseline.run_m4(data, params, m3)
        t = lap("m4", t)
        m5 = m5_interprovincial.run_m5(data, params)
        t = lap("m5", t)
        m6 = m6_tieline.run_m6(data, params, m4, m5)
        t = lap("m6", t)

        # M7：系统推演（默认）+ 人工自填并列（互不覆盖）
        thermal_in = m7_thermal.thermal_input_from(data, m6["predicted_96"])
        m7 = m7_thermal.system_mode(thermal_in, params)
        if self._m7_manual_on is not None:
            m7["manual"] = m7_thermal.manual_mode(self._m7_manual_on, m7["space_96"])
        t = lap("m7", t)

        # M8：四步 + 24 时段落点/灰度
        m8 = m8_pricing.run_pricing(data, params, m7["space_96"], m7["final_on_96"], m6["predicted_96"])
        t = lap("m8", t)
        landing = [m8_pricing.landing_stats(data, params, m7["load_rate_24"], p) for p in range(1, 25)]
        grey = [
            m8_pricing.grey_evaluate(
                (self._intents.get(p) or {}).get("listPrice"),
                (self._intents.get(p) or {}).get("volume"),
                landing[p - 1], params,
            )
            for p in range(1, 25)
        ]
        lap("landing_grey", t)
        timings["total"] = round((time.perf_counter() - t0) * 1000, 2)

        return ChainResult(params=params, m1=m1, m2=m2, m3=m3, m4=m4, m5=m5, m6=m6, m7=m7,
                           m8=m8, landing=landing, grey=grey, timings_ms=timings,
                           run_id=self._pristine_run_id)

    # ---------- 单点联动 ----------
    def recalc_point(self) -> ChainResult:
        """单点/单时段联动重算（§8 ≤1s）：装载走缓存 + 原值快照副本，链条全量重算（口径一致性优先于增量）。"""
        return self.run_all()

    # ---------- 修订 ----------
    def modify_boundary(self, boundary: str, period: int, points: list[dict], reason: str) -> list[str]:
        """修改可改边界：reason ≥5 字强制；修订链追加；返回修订 id 列表（下游重算由调用方触发）。"""
        if boundary not in BOUNDARY_WHITELIST:
            raise ValueError(f"非法边界 {boundary}（白名单：{'/'.join(BOUNDARY_WHITELIST)}）")
        if not 1 <= period <= 24:
            raise ValueError("period 须 ∈ 1..24")
        if len(reason.strip()) < 5:
            raise ValueError("修改理由须 ≥5 字")
        rev_ids = []
        for pt in points:
            t, value = int(pt["t"]), float(pt["value"])
            if not 1 <= t <= 96 or value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"非法点位 t={t} value={pt.get('value')}")
            rev_ids.append(self.store.append_revision(D_DAY, boundary, t, value, reason,
                                                      run_id=self._pristine_run_id or "", period=period))
        return rev_ids
