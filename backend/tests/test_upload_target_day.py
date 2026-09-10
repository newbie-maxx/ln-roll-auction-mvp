"""上传与滚撮日（MVP 增量）用例：
上传格式校验（识别/缺 sheet/列数）、按日期合并覆盖、滚撮日候选与历史范围自动收窄、
A 日 = D 日前最新有日前电价日、稳定 run 键下修订切换不丢、数据版本变更后旧修订隔离。"""
from __future__ import annotations

from pathlib import Path

import datetime as dt

import openpyxl
import pytest

from backend.config import DEFAULT_PARAMS
from backend.loaders.upload_manager import UploadManager, detect_kind, validate_upload
from backend.pipeline import Pipeline
from backend.store import Store


def make_xlsx(path: Path, sheets: dict[str, list[list]], days: list[str], n_points: int = 96) -> Path:
    """构造测试 xlsx：首行表头（日期 + n_points 列），每个 sheet 同一批日期行。"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(["日期"] + [i + 1 for i in range(n_points)])
        for d, row in zip(days, rows):
            y, m, dd = (int(x) for x in d.split("-"))
            ws.append([dt.date(y, m, dd)] + list(row))
    wb.save(path)
    return path


DA_SHEETS_OK = ["负荷", "水电", "核电", "地方燃煤", "风电", "光伏", "联络线", "非市场化", "日前电价"]
DAY = [30000.0 + i * 10 for i in range(96)]


def da_file(path: Path, days: list[str]) -> Path:
    return make_xlsx(path, {s: [DAY[:] for _ in days] for s in DA_SHEETS_OK}, days)


def rt_file(path: Path, days: list[str]) -> Path:
    return make_xlsx(path, {"实时电价": [[350.0] * 96 for _ in days], "联络线": [[5000.0] * 96 for _ in days]}, days)


# ---------- 上传校验 ----------

def test_detect_kind_by_sheet_signature(tmp_path):
    assert detect_kind(["成交量", "价格"]) == "roll"
    assert detect_kind(["实时电价", "联络线"]) == "realtime"
    assert detect_kind(DA_SHEETS_OK) == "day_ahead"
    assert detect_kind(["随便", "乱写"]) is None


def test_validate_upload_ok_and_missing_sheet(tmp_path):
    ok = validate_upload(da_file(tmp_path / "da.xlsx", ["2026-09-01"]))
    assert ok["ok"] and ok["kind"] == "day_ahead" and ok["days"] == ["2026-09-01"]
    bad = validate_upload(make_xlsx(
        tmp_path / "bad.xlsx",
        {s: [DAY[:]] for s in DA_SHEETS_OK if s != "日前电价"}, ["2026-09-01"]))
    assert not bad["ok"] and any("日前电价" in e for e in bad["errors"])


def test_validate_upload_column_count(tmp_path):
    short = make_xlsx(tmp_path / "short.xlsx", {"实时电价": [[1.0] * 10], "联络线": [[1.0] * 10]}, ["2026-09-01"], n_points=10)
    rep = validate_upload(short)
    assert not rep["ok"] and any("列数不足" in e for e in rep["errors"])


# ---------- 合并 + 滚撮日 ----------

@pytest.fixture()
def pipe(tmp_path):
    uploads = UploadManager(tmp_path / "uploads")
    p = Pipeline(store=Store(tmp_path / "t.db"), uploads=uploads)
    p.loaded()
    return p


def test_upload_merges_new_day_and_extends_range(pipe, tmp_path):
    f = da_file(tmp_path / "new.xlsx", ["2026-09-01"])
    pipe.uploads.save("day_ahead", "new.xlsx", f.read_bytes())
    pipe.uploads.save("realtime", "rt.xlsx", rt_file(tmp_path / "rt.xlsx", ["2026-09-01"]).read_bytes())
    info = pipe.reload_dataset()
    assert "2026-09-01" in pipe.loaded().days
    assert info["target_day"] == "2026-09-01"          # 候选最大日自动前移
    r = pipe.run_all()
    assert r.d_day == "2026-09-01"
    assert r.m8["a_day"] == "2026-08-31"                # A 日 = 新滚撮日之前最新有日前电价日


def test_upload_overrides_same_day(pipe, tmp_path):
    """后传覆盖同日：09-01 上传第二次（负荷全 45000）→ 该日生效值更换。"""
    pipe.uploads.save("day_ahead", "a1.xlsx", da_file(tmp_path / "a1.xlsx", ["2026-09-01"]).read_bytes())
    pipe.reload_dataset()
    f2 = make_xlsx(tmp_path / "a2.xlsx",
                   {**{s: [DAY[:]] for s in DA_SHEETS_OK if s != "负荷"}, "负荷": [[45000.0] * 96]}, ["2026-09-01"])
    pipe.uploads.save("day_ahead", "a2.xlsx", f2.read_bytes())
    pipe.reload_dataset()
    assert pipe.loaded().day_ahead["2026-09-01"]["负荷"].values[0] == 45000.0


def test_target_day_narrows_history_and_a_day(pipe):
    info = pipe.set_target_day("2026-08-15")
    assert info["target_day"] == "2026-08-15"
    assert info["history_count"] == 14                   # 08-01…08-14
    r = pipe.run_all()
    assert r.a_day == "2026-08-14"
    for g in r.m3["a_dimension_groups"]:                 # 样本组成员全部 < D 日
        assert all(d < "2026-08-15" for d in g["members"])
    assert all(d < "2026-08-15" for d in r.landing[9]["members"])


def test_target_day_rejects_incomplete_day(pipe):
    with pytest.raises(ValueError):
        pipe.set_target_day("2030-01-01")


def test_revision_survives_day_switch_and_dataset_change(pipe):
    pipe.set_target_day("2026-08-31")
    pipe.modify_boundary("负荷", 10, [{"t": 37, "value": 46000}], "午间负荷上调测试")
    assert pipe.store.effective_value("2026-08-31", "负荷", 37, run_id=pipe._run_id) == 46000.0
    # 切到 08-30：旧修订不作用于新日；08-30 原值正常
    pipe.set_target_day("2026-08-30")
    r2 = pipe.run_all()
    assert r2.d_day == "2026-08-30"
    assert pipe.store.effective_value("2026-08-31", "负荷", 37, run_id=pipe._run_id) != 46000.0
    # 切回 08-31：稳定 run 键 → 修订恢复生效
    pipe.set_target_day("2026-08-31")
    assert pipe.store.effective_value("2026-08-31", "负荷", 37, run_id=pipe._run_id) == 46000.0
    r3 = pipe.run_all()
    assert r3.d_day == "2026-08-31"


def test_default_params_unchanged():
    assert DEFAULT_PARAMS.现货上限 == 1500.0


# ---------- 真实滚撮文件（长表适配 + 自动发现） ----------

def test_real_roll_file_long_layout():
    """业务方 8 月滚撮统计表（长表：行=日期+小时）——读取、小时对齐、自动发现。"""
    from backend.config import DATA_DIR, find_roll_file
    from backend.loaders.roll_reader import read_roll_file
    src = DATA_DIR / "省间滚撮统计8月.xlsx"
    if not src.exists():                                 # 数据文件随仓库走，正常应在
        pytest.skip("真实滚撮表不在 data/")
    data, layout = read_roll_file(src)
    assert "长表" in layout
    assert len(data) == 31 and "2026-08-01" in data and "2026-08-31" in data
    # 小时对齐抽查：08-01 第 3/4/5 时（0 基 2/3/4）= -110/-115.1/-102
    v = data["2026-08-01"]["volume24"]
    assert (v[2], v[3], v[4]) == (-110.0, -115.1, -102.0)
    assert all(x["price24"] is None for x in data.values())   # 无价格 sheet
    # 自动发现指向该文件
    assert find_roll_file() == src


def test_pipeline_uses_real_roll(pipe):
    """自动发现生效：全链滚撮来源为真实文件，价格为展示用合成占位。"""
    data = pipe.loaded()
    assert "真实文件" in data.roll_source and "长表" in data.roll_source
    assert any("价格" in w for w in data.warnings)
    r = pipe.run_all()
    # M4 基线用了真实量（非 8%~12% 调制形状）：抽查某日基线 = 真实滚撮 + 日前联络线
    from backend.modules.m4_baseline import day_baseline
    b = day_baseline(data, "2026-08-01")
    assert b is not None
    # 96 点第 9 点（0 基 8）= 小时 2（0 基）展开 → 真实滚撮 + 日前联络线
    assert b[8] == round(data.roll_auction["2026-08-01"]["volume24"][2] + data.day_ahead["2026-08-01"]["联络线"].values[8], 6)  # noqa: E501
