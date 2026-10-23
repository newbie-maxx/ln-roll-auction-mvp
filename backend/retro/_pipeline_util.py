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
_BASE_KEY: tuple | None = None


def _base_cache_key() -> tuple:
    """底账源文件 mtime 键：基线 DA/RT/滚撮 + 全部上传表（任一变化 → 重载）。"""
    from ..config import DA_XLSX, RT_XLSX, find_roll_file
    from ..loaders.upload_manager import UploadManager
    um = UploadManager()
    files = [DA_XLSX, RT_XLSX, find_roll_file(),
             *um.sources("day_ahead"), *um.sources("realtime"), *um.sources("roll")]
    return tuple((str(p), p.stat().st_mtime_ns) for p in files if p is not None and Path(p).exists())


def base_data() -> LoadedData:
    """全量底账（按源文件 mtime 键控缓存；上传新表自动重载，data/ 只读）。"""
    global _BASE, _BASE_KEY
    key = _base_cache_key()
    if _BASE is None or key != _BASE_KEY:
        # 与 Pipeline._loader() 同口径：基线 + 全部上传表（后传覆盖同日）
        from ..loaders.upload_manager import UploadManager
        um = UploadManager()
        loader = XlsxLoader(extra_da=tuple(um.sources("day_ahead")),
                            extra_rt=tuple(um.sources("realtime")),
                            extra_roll=tuple(um.sources("roll")))
        data = loader.load()
        # 与 Pipeline.loaded() 同口径：补省间交易总量默认项（复用 pipeline 的注入函数）
        from ..pipeline import _inject_inter_total_default
        _inject_inter_total_default(data)
        _BASE, _BASE_KEY = data, key
    return _BASE


def reset_base_cache() -> None:
    """测试用：清底账缓存（上传/换库后）。"""
    global _BASE, _BASE_KEY
    _BASE = None
    _BASE_KEY = None


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
