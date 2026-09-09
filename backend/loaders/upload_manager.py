"""数据底账管理：本地基线文件 + 用户上传表合并（按日期，后传覆盖同日）+ 上传格式校验。

上传契约（定死，见 docu/交易员计算流程与逻辑.md §0）：
- 日前边界表：必需 sheets 负荷/水电/核电/地方燃煤/风电/光伏/联络线/非市场化/日前电价；
  可选 检修容量/日前开机/集中式风电/集中式光伏/分散式风电/分布式光伏/24点平均日前负荷率。
- 实时边界表：必需 实时电价/联络线；可选 24点平均日前负荷率/实时开机/开机容量 等。
- 省间滚撮表：sheets 成交量/价格，24 点列。
- 布局统一：首行表头，行=日期（升序无要求），数值列；日前/实时 96 点、滚撮 24 点。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import openpyxl

from ..config import BACKEND_DIR

UPLOAD_DIR = BACKEND_DIR / "data" / "uploads"

DA_REQUIRED = ["负荷", "水电", "核电", "地方燃煤", "风电", "光伏", "联络线", "非市场化", "日前电价"]
DA_OPTIONAL = ["检修容量", "日前开机", "集中式风电", "集中式光伏", "分散式风电", "分布式光伏",
               "地方水电", "抽蓄水电", "24点平均日前负荷率"]
RT_REQUIRED = ["实时电价", "联络线"]
RT_OPTIONAL = ["24点平均日前负荷率", "实时开机", "开机容量", "负荷", "水电", "核电", "地方燃煤",
               "风电", "光伏", "非市场化", "竞价空间"]
ROLL_REQUIRED = ["成交量", "价格"]

# 滚撮日候选所需完整边界（M7 空间公式八项）
D_DAY_REQUIRED_BOUNDARIES = ["负荷", "水电", "核电", "地方燃煤", "风电", "光伏", "联络线", "非市场化"]


def detect_kind(sheet_names: list[str]) -> str | None:
    """按 sheet 签名自动识别表类型：滚撮 > 实时 > 日前。"""
    names = set(sheet_names)
    if set(ROLL_REQUIRED) <= names:
        return "roll"
    if set(RT_REQUIRED) <= names:
        return "realtime"
    if set(DA_REQUIRED) <= names:
        return "day_ahead"
    return None


def validate_upload(path: Path) -> dict:
    """校验上传文件：返回 {ok, kind, sheets, days, errors[]}；格式不符给逐项错误。"""
    errors: list[str] = []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": None, "sheets": [], "days": [], "errors": [f"无法解析（仅支持 xlsx）：{e}"]}
    try:
        names = wb.sheetnames
        kind = detect_kind(names)
        if kind is None:
            have = "、".join(names[:8])
            errors.append(
                f"无法识别表类型（按 sheet 签名）：日前边界表需含 {('/'.join(DA_REQUIRED))}；"
                f"实时边界表需含 {('/'.join(RT_REQUIRED))}；滚撮表需含 {('/'.join(ROLL_REQUIRED))}。当前 sheets：{have}")
            return {"ok": False, "kind": None, "sheets": names, "days": [], "errors": errors}

        required = {"day_ahead": DA_REQUIRED, "realtime": RT_REQUIRED, "roll": ROLL_REQUIRED}[kind]
        expected_cols = 24 if kind == "roll" else 96
        days: list[str] = []
        for sheet in required:
            if sheet not in names:
                errors.append(f"缺必需 sheet「{sheet}」")
                continue
            ws = wb[sheet]
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if header is None or len(header) < expected_cols + 1:
                errors.append(f"sheet「{sheet}」列数不足：需 1 日期列 + {expected_cols} 数值列（首行表头）")
                continue
            sheet_days = 0
            for r in rows:
                if r is None or r[0] is None or not hasattr(r[0], "year"):
                    continue
                sheet_days += 1
                if sheet_days == 1:
                    days.append(f"{r[0].year:04d}-{r[0].month:02d}-{r[0].day:02d}")
            if sheet_days == 0:
                errors.append(f"sheet「{sheet}」无有效日期行（首列须为日期）")
        # 去重排序（跨 sheet 日期一致性抽查：各 sheet 首日不同不阻断，按逐 sheet 独立合并）
        return {"ok": not errors, "kind": kind, "sheets": names, "days": sorted(set(days)), "errors": errors}
    finally:
        wb.close()


class UploadManager:
    """持久化上传文件（backend/data/uploads/，gitignored）+ 合并视图（后传覆盖同日）。"""

    def __init__(self, upload_dir: Path = UPLOAD_DIR) -> None:
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def save(self, kind: str, filename: str, payload: bytes) -> Path:
        """按类型前缀持久化；时间戳保证新文件排序在后 = 后传覆盖同日。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        prefix = {"day_ahead": "da", "realtime": "rt", "roll": "roll"}[kind]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        dest = self.upload_dir / f"{prefix}-{stamp}-{safe}"
        if not dest.suffix:
            dest = dest.with_suffix(".xlsx")
        dest.write_bytes(payload)
        return dest

    def sources(self, kind: str) -> list[Path]:
        """该类型全部上传文件（基线文件之外），按时间升序 = 后传覆盖。"""
        prefix = {"day_ahead": "da", "realtime": "rt", "roll": "roll"}[kind]
        return sorted(self.upload_dir.glob(f"{prefix}-*.xlsx"))
