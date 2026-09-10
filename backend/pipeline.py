"""M1→M8 编排 + 单点联动重算 + 数据底账/滚撮日动态化。

- 装载一次（xlsx 慢），**原值快照**缓存在进程内且永不改动；boundary_master 只写装载原值。
- 每次重算在原值快照的内存合并副本上进行（修订读时合并：生效值 = 最新有效修订 ?? 原值，
  按 run 隔离——数据底账更换或滚撮日切换后，旧修订保留可审计但不再作用于新口径）。
- **滚撮日（D 日）可选**：候选 = 库内 8 边界齐全的日期；历史范围/A 日/A-1 回溯自动收窄至该日之前。
- 全部输出带推导理由字段（样本成员/算式/系数/中间值）；耗时逐模块记录（§8：全量 ≤30s、单点 ≤1s）。
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import D_DAY as DEFAULT_D_DAY
from .config import DEFAULT_PARAMS, Params
from .loaders.upload_manager import UploadManager
from .loaders.xlsx_loader import (LoadedData, XlsxLoader, apply_run_day_policy, resolve_a_day,
                                  valid_target_days)
from .modules import (m2_distributed, m3_similar, m4_baseline, m5_interprovincial, m6_tieline,
                      m7_thermal, m8_pricing)
from .store import Store

# 可改边界白名单（docu/智能体工具与技能设计.md §5；火电开机走 M7 双模式不在此列）。
# 联络线 = 联络线基线（显示名）；省间交易总量 = 交易员预测（省间滚搓+省间现货），24 点输入
# 自动展开 96 点（功率值，不除以 4），默认全 0 待交易员更新。
BOUNDARY_WHITELIST = ("负荷", "风电", "光伏", "水电", "核电", "地方燃煤", "非市场化", "联络线", "省间交易总量")
BOUNDARY_LABELS = {"联络线": "联络线基线"}
HOUR_EXPAND_KEYS = ("省间交易总量",)   # 该边界按小时语义编辑：改 1 点即整小时 4 点同值


def _inject_inter_total_default(data) -> None:
    """省间交易总量（交易员预测）：库内每日默认 0（96 点 = 24 点 0 展开），待交易员更新。"""
    from .loaders.xlsx_loader import Series
    for day in data.days:
        da = data.day_ahead.setdefault(day, {})
        if "省间交易总量" not in da:
            da["省间交易总量"] = Series.make([0.0] * 96, kind="意向",
                                           note="默认 0（24 点输入自动展开 96 点，功率值不除以 4），待交易员更新")


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
    d_day: str = ""
    a_day: str = ""

    def to_json(self, period: int | None = None) -> dict:
        d = {
            "dates": {"D": self.d_day, "A": self.a_day, "A1": self.m8.get("a1_day", ""),
                      "historyEnd": self.m8.get("a_day", "")},
            "params": self.params.as_dict(),
            "run_id": self.run_id,
            "m1": {"roll_source": self.m1["roll_source"], "warnings": self.m1["warnings"]},
            "m6": {"predicted_96": self.m6["predicted_96"], "kind": self.m6["kind"],
                   "realtime_tie_96": self.m6.get("realtime_tie_96"),
                   "realtime_formula": self.m6.get("realtime_formula")},
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
    def __init__(self, loader: XlsxLoader | None = None, store: Store | None = None,
                 uploads: UploadManager | None = None) -> None:
        self.uploads = uploads or UploadManager()
        self._base_loader = loader
        self.store = store or Store()
        self._pristine: LoadedData | None = None        # 数据底账原值快照（与滚撮日无关），永不改动
        self._dataset_version: int = 0                  # 上传/底账变更 → 递增 → 新 run
        self._target_day: str = ""                      # 滚撮日（D 日），空 = 未定（取候选最大日）
        self._a_day: str = ""
        self._run_id: str = ""
        self._last_params: Params = DEFAULT_PARAMS
        self._m7_manual_on: list[float] | None = None   # M7 人工自填开机（与系统版并列）
        self._intents: dict[int, dict] = {}             # period -> {listPrice, liftPrice, volume}

    # ---------- 数据底账 ----------
    def _loader(self) -> XlsxLoader:
        if self._base_loader is not None and self._dataset_version == 0:
            return self._base_loader
        return XlsxLoader(extra_da=tuple(self.uploads.sources("day_ahead")),
                          extra_rt=tuple(self.uploads.sources("realtime")),
                          extra_roll=tuple(self.uploads.sources("roll")))

    def loaded(self) -> LoadedData:
        """装载一次（含上传合并）；原值入 boundary_master（按 run 键控，只写原值）。"""
        if self._pristine is None:
            self._pristine = self._loader().load()
            _inject_inter_total_default(self._pristine)
            self._ensure_run()
            da = self._pristine.day_ahead[self.target_day()]
            for boundary in BOUNDARY_WHITELIST:
                if boundary in da:
                    for idx in range(96):
                        self.store.upsert_master(self._run_id, self.target_day(), boundary, idx + 1,
                                                 da[boundary].values[idx],
                                                 value_type=da[boundary].kinds[idx],
                                                 src_day=da[boundary].src_days[idx])
        return self._pristine

    def reload_dataset(self) -> dict:
        """上传落地后重建底账（新版本 run；旧修订/意向保留可审计但不再作用）。"""
        self._pristine = None
        self._dataset_version += 1
        self._target_day = ""
        data = self.loaded()
        return {"dataset_version": self._dataset_version, "days": len(data.days),
                "target_day": self.target_day(), "a_day": self._a_day}

    # ---------- 滚撮日 ----------
    def target_day(self) -> str:
        """当前滚撮日：显式选择 or 候选最大日（默认 = 最新可预测日）。"""
        if self._target_day:
            return self._target_day
        data = self._pristine
        if data is None:
            data = self._loader().load()
            self._pristine = data
        candidates = valid_target_days(data)
        if not candidates:
            raise ValueError("库内无 8 边界齐全的日期，无法确定滚撮日（请上传日前边界表）")
        return max(candidates)

    def day_info(self) -> dict:
        data = self.loaded()
        candidates = valid_target_days(data)
        cur = self.target_day()
        return {
            "target_day": cur,
            "a_day": resolve_a_day(data, cur),
            "available_days": candidates,
            "all_days": data.days,
            "history_count": sum(1 for d in data.days if d < cur),
            "dataset_version": self._dataset_version,
            "roll_source": data.roll_source,
        }

    def set_target_day(self, date: str) -> dict:
        candidates = valid_target_days(self.loaded())
        if date not in candidates:
            raise ValueError(f"{date} 不可选（需 8 边界齐全；候选：{candidates[-1] if candidates else '无'}）")
        if date != self._target_day:
            self._target_day = date
            self._ensure_run()          # 切换滚撮日 → 新 run（修订按日隔离）
        return self.day_info()

    def _ensure_run(self) -> None:
        """稳定 run 键 = (数据版本, 滚撮日)：同键幂等复用（切回即恢复修订链），换键即隔离。"""
        d_day = self.target_day()
        self._a_day = resolve_a_day(self._pristine, d_day) if self._pristine else ""
        self._run_id = self.store.new_run(d_day, DEFAULT_PARAMS.as_dict(),
                                          run_id=f"ds{self._dataset_version}:{d_day}")
        if self._pristine is not None:
            da = self._pristine.day_ahead.get(d_day, {})
            for boundary in BOUNDARY_WHITELIST:
                if boundary in da:
                    for idx in range(96):
                        self.store.upsert_master(self._run_id, d_day, boundary, idx + 1,
                                                 da[boundary].values[idx],
                                                 value_type=da[boundary].kinds[idx],
                                                 src_day=da[boundary].src_days[idx])

    # ---------- 参数/模式/意向 ----------
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
        """M1→M8 全链：在原值快照的合并副本上计算（修订读时合并，按当前 run 隔离）。"""
        t0 = time.perf_counter()
        pristine = self.loaded()
        data = copy.deepcopy(pristine)
        params = self._last_params
        d_day = self.target_day()
        a_day = resolve_a_day(pristine, d_day)
        if a_day != self._a_day:
            self._a_day = a_day
        timings: dict[str, float] = {}

        def lap(name: str, t: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - t) * 1000, 2)
            return now

        # M1（装载口径汇总）+ 运行日政策（最近日法/核电 min 标注，按所选 D 日）
        t = time.perf_counter()
        policy_warnings = apply_run_day_policy(data, d_day, a_day)
        m1 = {"roll_source": pristine.roll_source,
              "warnings": list(pristine.warnings) + policy_warnings,
              "days": pristine.days, "ready": True, "d_day": d_day, "a_day": a_day}
        t = lap("m1", t)

        # 修订读时合并 → D 日生效边界（原值仍在 boundary_master / 修订链）
        da = data.day_ahead[d_day]
        merged_points = 0
        for boundary in BOUNDARY_WHITELIST:
            if boundary not in da:
                continue
            for idx in range(96):
                eff = self.store.effective_value(d_day, boundary, idx + 1, run_id=self._run_id)
                if eff is not None:
                    da[boundary].values[idx] = eff
                    da[boundary].annotate(idx, "计算", note="人工修订生效（原值与修订链可查）")
                    merged_points += 1
        t = lap("merge_revisions", t)
        m1["merged_points"] = merged_points

        m2 = m2_distributed.infer_distributed(data, d_day)
        t = lap("m2", t)
        m3 = m3_similar.run_m3(data, params, d_day)
        t = lap("m3", t)
        m4 = m4_baseline.run_m4(data, params, m3, d_day)
        t = lap("m4", t)
        m5 = m5_interprovincial.run_m5(data, params, d_day)
        t = lap("m5", t)
        m6 = m6_tieline.run_m6(data, params, m4, m5)
        t = lap("m6", t)

        # 实时联络线预测(96) = 联络线基线(96 生效值) − 交易员预测省间交易总量(96 生效值)
        # （省间交易总量为 24 点输入按小时展开 96 点，功率值不除以 4）
        tie_base = da.get("联络线")
        inter_total = da.get("省间交易总量")
        realtime_tie: list[float | None] = []
        for i in range(96):
            b = tie_base.values[i] if tie_base is not None else None
            v = inter_total.values[i] if inter_total is not None else None
            realtime_tie.append(b - v if b is not None and v is not None else None)   # type: ignore[operator]
        m6["realtime_tie_96"] = realtime_tie
        m6["realtime_formula"] = "实时联络线预测 = 联络线基线 − 交易员预测省间交易总量（省间滚搓+省间现货）"
        t = lap("realtime_tie", t)

        # M7：系统推演（默认）+ 人工自填并列（互不覆盖）——运行日空间用【实时联络线预测】
        thermal_in = m7_thermal.thermal_input_from(data, realtime_tie, d_day)
        m7 = m7_thermal.system_mode(thermal_in, params)
        if self._m7_manual_on is not None:
            m7["manual"] = m7_thermal.manual_mode(self._m7_manual_on, m7["space_96"])
        t = lap("m7", t)

        # M8：四步 + 24 时段落点/灰度（历史范围 = D 日之前）
        m8 = m8_pricing.run_pricing(data, params, m7["space_96"], m7["final_on_96"], m6["predicted_96"],
                                    d_day=d_day, a_day=a_day)
        t = lap("m8", t)
        landing = [m8_pricing.landing_stats(data, params, m7["load_rate_24"], p, d_day) for p in range(1, 25)]
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
                           run_id=self._run_id, d_day=d_day, a_day=a_day)

    # ---------- 单点联动 ----------
    def recalc_point(self) -> ChainResult:
        """单点/单时段联动重算（§8 ≤1s）：装载走缓存 + 原值快照副本，链条全量重算（口径一致性优先于增量）。"""
        return self.run_all()

    # ---------- 修订 ----------
    def modify_boundary(self, boundary: str, period: int, points: list[dict], reason: str) -> list[str]:
        """修改可改边界：reason ≥5 字强制；修订链追加（挂当前 run）；下游重算由调用方触发。"""
        if boundary not in BOUNDARY_WHITELIST:
            raise ValueError(f"非法边界 {boundary}（白名单：{'/'.join(BOUNDARY_WHITELIST)}）")
        if not 1 <= period <= 24:
            raise ValueError("period 须 ∈ 1..24")
        if not reason.strip():
            raise ValueError("修改理由必填（不再强制 ≥5 字）")
        expanded: list[dict] = []
        for pt in points:
            t, value = int(pt["t"]), float(pt["value"])
            if not 1 <= t <= 96 or value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"非法点位 t={t} value={pt.get('value')}")
            if boundary in HOUR_EXPAND_KEYS:
                hour_start = ((t - 1) // 4) * 4 + 1        # 24 点输入语义：整小时 4 点同值
                expanded.extend({"t": tt, "value": value} for tt in range(hour_start, hour_start + 4))
            else:
                expanded.append({"t": t, "value": value})
        rev_ids = []
        for pt in expanded:
            rev_ids.append(self.store.append_revision(self.target_day(), boundary, pt["t"], pt["value"], reason,
                                                      run_id=self._run_id or "", period=period))
        return rev_ids
