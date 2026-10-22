"""复盘层复用既有 Pipeline 的共享工具（不改 pipeline.py/store.py）。

工作库副本：把 workbench.db 复制到临时目录供分解/重放的 Pipeline 实例使用——
既保留当日修订留痕（读时合并生效），又避免替换实况的 boundary_master 写入污染一阶段表。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..config import DB_PATH
from ..loaders.xlsx_loader import LoadedData, XlsxLoader
from ..store import Store

_BASE: LoadedData | None = None
_BASE_DIR: tempfile.TemporaryDirectory | None = None


def base_data() -> LoadedData:
    """全量底账（进程内缓存一次；data/ 只读）。"""
    global _BASE, _BASE_DIR
    if _BASE is None:
        loader = XlsxLoader()
        data = loader.load()
        # 与 Pipeline.loaded() 同口径：补省间交易总量默认项（复用 pipeline 的注入函数）
        from ..pipeline import _inject_inter_total_default
        _inject_inter_total_default(data)
        _BASE = data
    return _BASE


def reset_base_cache() -> None:
    """测试用：清底账缓存（上传/换库后）。"""
    global _BASE
    _BASE = None


def work_store_copy(tmpdir: str | Path) -> Store:
    """建工作库副本并返回连接（DB_PATH 动态读取，支持测试隔离）。
    真实库缺失时建空库（表结构齐全）。"""
    from .. import config
    tmp = Path(tmpdir)
    tmp.mkdir(parents=True, exist_ok=True)
    dst = tmp / "work_copy.db"
    src = Path(config.DB_PATH)
    if src.exists():
        shutil.copyfile(src, dst)
    return Store(str(dst))


class SubLoader:
    """Loader 协议：返回底账深拷贝，并把 D 日指定项替换为给定 96 点值（仅值，口径标注不动）。"""

    def __init__(self, base: LoadedData, d_day: str, subs: dict[str, list[float | None]]) -> None:
        self._base, self._d_day, self._subs = base, d_day, subs

    def load(self) -> LoadedData:
        import copy
        data = copy.deepcopy(self._base)
        da = data.day_ahead.get(self._d_day, {})
        for key, vals in self._subs.items():
            if key in da:
                old = da[key]
                da[key] = old.__class__.make(list(vals), kind=old.kinds[0] if old.kinds else "披露")
            else:
                from ..loaders.xlsx_loader import Series
                da[key] = Series.make(list(vals), kind="披露")
        return data
