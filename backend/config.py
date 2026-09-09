"""全局参数默认表（PRD §7）+ 环境变量 + 路径与日期角色。

口径唯一来源 docu/MVP_PRD-1.8.md；摘要 docu/交易员计算流程与逻辑.md §11。
参数校验规则 = docu/智能体工具与技能设计.md §5。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"
DA_XLSX = DATA_DIR / "辽宁省8月日前边界.xlsx"
RT_XLSX = DATA_DIR / "辽宁省8月实时边界.xlsx"
DB_PATH = Path(os.environ.get("WB_DB", BACKEND_DIR / "data" / "workbench.db"))
EXPORT_DIR = BACKEND_DIR / "data" / "exports"

# 省间滚撮真实文件替换位：文件出现于该路径时 loader 自动优先读取（A-0 到位后零改动切换）
ROLL_AUCTION_XLSX = Path(os.environ.get("WB_ROLL_AUCTION", DATA_DIR / "省间滚撮.xlsx"))

# 日期角色（已确认）：D=08-31（预测对象）、A=08-30（最新日前出清价日）、A-1=08-29（回溯 08-28…）
D_DAY = os.environ.get("WB_D_DAY", "2026-08-31")
A_DAY = os.environ.get("WB_A_DAY", "2026-08-30")

# 日前/实时边界 sheet 消费清单（Sheet1 为杂项不消费）
DA_SHEETS = ["负荷", "水电", "核电", "地方燃煤", "风电", "光伏", "联络线", "非市场化",
             "检修容量", "日前电价", "日前开机",
             "集中式风电", "集中式光伏", "分散式风电", "分布式光伏", "地方水电", "抽蓄水电",
             "24点平均日前负荷率"]
RT_SHEETS = ["实时电价", "联络线", "24点平均日前负荷率", "实时开机", "开机容量"]

# M7 平衡时段（建议默认 10–12/14–16/19–21，PRD §6b.1）
DEFAULT_BALANCE_HOURS: list[tuple[int, int]] = [(10, 12), (14, 16), (19, 21)]


@dataclass(frozen=True)
class Params:
    调频容量: float = 1500.0
    正备用: float = 3000.0
    负备用: float = 400.0
    受阻系数: float = 0.10
    新能源平衡系数: float = 0.8
    平衡时段: tuple[tuple[int, int], ...] = tuple(DEFAULT_BALANCE_HOURS)
    零价点负荷率: float = 0.5
    最小开机方式: float = 15000.0
    辽宁装机: float = 28715.0
    检修计划: float = 2000.0
    现货上限: float = 1500.0
    现货下限: float = -100.0
    区间宽度: int = 100
    相似日阈值: float = 0.05          # ±5%（落点检索；M3 A 维度阈值见 m3_tol）
    m3_tol_mw: float = 300.0           # M3 A(t) 距离容差（MW，实现口径待业务校准）
    m3_scope_days: int = 7             # M3 默认检索范围 = 上一周；N<3 放宽至全部历史
    现货恒等式: str = "日前联络线 − 实时联络线"

    def with_overrides(self, patch: dict) -> "Params":
        """应用参数覆盖（含平衡时段特殊解析），返回新实例；非法值抛 ValueError。"""
        clean = {k: v for k, v in patch.items() if v is not None and k in self.__dataclass_fields__}
        if "平衡时段" in clean:
            raw = clean.pop("平衡时段")
            clean["平衡时段"] = tuple((int(a), int(b)) for a, b in raw)
        try:
            return replace(self, **clean)
        except TypeError as e:
            raise ValueError(f"未知参数: {e}") from e

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.调频容量 < 0: errs.append("调频容量须为 ≥0 数值")
        if self.正备用 < 0: errs.append("正备用须为 ≥0 数值")
        if self.负备用 < 0: errs.append("负备用须为 ≥0 数值")
        if not 0 <= self.受阻系数 < 1: errs.append("受阻系数须 ∈ [0,1)")
        if not 0 < self.新能源平衡系数 <= 1: errs.append("新能源平衡系数须 ∈ (0,1]")
        if not 0 < self.零价点负荷率 <= 2: errs.append("零价点负荷率须 ∈ (0,2]")
        if self.最小开机方式 < 0: errs.append("最小开机方式须为 ≥0 数值")
        if self.辽宁装机 <= 0: errs.append("辽宁装机须为 >0 数值")
        if self.检修计划 < 0: errs.append("检修计划须为 ≥0 数值")
        if self.现货上限 <= self.现货下限: errs.append("现货上限须 > 现货下限")
        if not (isinstance(self.区间宽度, int) and self.区间宽度 > 0): errs.append("区间宽度须为正整数")
        if not 0 < self.相似日阈值 <= 1: errs.append("相似日阈值须 ∈ (0,1]")
        if self.最小开机方式 > self.辽宁装机 - self.检修计划: errs.append("最小开机方式不得大于 装机 − 检修（开机硬上限）")
        if self.m3_tol_mw <= 0: errs.append("M3 容差须 >0")
        return errs

    def balance_flag_96(self) -> list[bool]:
        """96 点是否处于平衡时段（小时区间 [起,止) 前闭后开，按 0:15–24:00 点位归属小时）。"""
        flags = []
        for t in range(96):
            hour = (t + 1) // 4          # 点位 t（0 基）的整点小时 0..23（t=0 → 0:15 属 0 时段）
            hour = min(hour, 23)
            flags.append(any(a <= hour < b for a, b in self.平衡时段))
        return flags

    def as_dict(self) -> dict:
        d = asdict(self)
        d["平衡时段"] = [list(x) for x in self.平衡时段]
        return d


DEFAULT_PARAMS = Params()


def env_llm() -> dict:
    """LLM 三要素（key 只从 .env/环境读取，绝不回显完整值）。"""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", ""),
    }
