"""pngchart —— 纯标准库 PNG 图表渲染器（复盘报告 `.md` 内嵌位图，PRD §E-A7.4）。

- 只用标准库（zlib + struct 手写 PNG 编码），不引 matplotlib / Pillow（守住 ADR-007）。
- 图内**不渲染任何文字**：标题/图例/量程由调用方写在图外。
- 色带（bands）先铺实底矩形再画曲线（无 alpha 混合，顺序不能反）；缺数点折线断开、不插值。
- 确定性：同一输入两次渲染字节完全相同（PNG 内无随机量、无时间戳、无环境差异）。

PNG 形态：8-bit RGB（color type 2）、非交错、行滤波器全 0（None filter）、
zlib level 9 压缩，仅 IHDR / IDAT / IEND 三个块。
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Series", "Band", "Bar",
    "render_line_chart", "render_line_bar_chart",
    "map_points", "line_segments", "cell_span", "plot_rect", "split_layout",
    "compute_y_range", "compute_bar_range",
]

MARGIN = 24                 # 绘图区四边留白（px）
LINE_AREA_RATIO = 0.62      # 组合图：上半折线区占绘图区高度比例（下半 38% 为柱区）
FRAME_COLOR = (80, 80, 80)  # 绘图区 1px 边框 / 柱区零线（深灰）
VLINE_COLOR = (200, 60, 60) # vlines 固定色
WHITE = (255, 255, 255)
POINT_RADIUS = 2            # 数据点实心圆半径（px）
LINE_THICKNESS = 3          # 折线线宽（px）
VLINE_THICKNESS = 2         # vline 竖线宽（px）
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------- 输入数据 ----------

@dataclass
class Series:
    name: str
    color: tuple[int, int, int]   # 0-255 RGB
    points: list[float | None]    # 长度 N（96 或 24）；None = 缺数断线


@dataclass
class Band:
    start: int                    # x 起始索引（含）
    end: int                      # x 结束索引（含）
    color: tuple[int, int, int]


@dataclass
class Bar:
    name: str
    color: tuple[int, int, int]
    values: list[float | None]    # 与折线同长度 N；None = 留空不画柱。
                                  # 调用方保证基准系列不进 bars（基准 0 偏差本就不画柱），渲染器不重复处理。


# ---------- 坐标 / 几何（纯函数，供渲染与测试共用） ----------

def plot_rect(width: int, height: int) -> tuple[int, int, int, int]:
    """整幅图的绘图区矩形 (left, top, right, bottom)，含端点；1px 边框画在矩形四边上。"""
    return (MARGIN, MARGIN, width - MARGIN - 1, height - MARGIN - 1)


def split_layout(width: int, height: int) -> tuple[tuple[int, int, int, int],
                                                   tuple[int, int, int, int]]:
    """组合图布局：返回（折线区矩形, 柱区矩形）。

    上半 LINE_AREA_RATIO 为折线区、其余为零基线柱区，两区上下相邻、共用同一 x 轴
    （left/right 完全一致，柱与折线点严格同列）。
    """
    left, top, right, bottom = plot_rect(width, height)
    line_h = round((bottom - top + 1) * LINE_AREA_RATIO)
    return (left, top, right, top + line_h - 1), (left, top + line_h, right, bottom)


def _x_of_index(i: int, n: int, left: int, right: int) -> int:
    """索引 i 的像素 x：索引 0..N-1 均匀铺满 [left, right]。"""
    if n <= 1:
        return round(left)
    return round(left + i * (right - left) / (n - 1))


def map_points(points: list[float | None], xlim: tuple[int, int],
               ylim: tuple[float, float, int, int]) -> list[tuple[int, int] | None]:
    """数据点 → 绘图区像素坐标（纯函数）。

    points: list[float | None]，None 表示缺数，映射结果同为 None。
    xlim:   (px_left, px_right) —— 索引 0 与 N-1 落在的像素 x（均匀铺满）。
    ylim:   (v_min, v_max, px_top, px_bottom) —— 值域两端对应的像素 y
            （v_max → px_top，v_min → px_bottom；像素 y 向下为正）。
    返回与 points 等长的 list[tuple[int, int] | None]。
    """
    left, right = xlim
    v_lo, v_hi, px_top, px_bottom = ylim
    if v_hi == v_lo:
        raise ValueError("y 值域退化（v_min == v_max）；应先经 compute_y_range / compute_bar_range 归一")
    n = len(points)
    out: list[tuple[int, int] | None] = []
    for i, v in enumerate(points):
        if v is None:
            out.append(None)
            continue
        x = _x_of_index(i, n, left, right)
        y = round(px_bottom + (v - v_lo) * (px_top - px_bottom) / (v_hi - v_lo))
        out.append((x, y))
    return out


def line_segments(pts: list[tuple[int, int] | None]) -> list[tuple[tuple[int, int],
                                                                   tuple[int, int]]]:
    """相邻非 None 点连线段（纯函数）；None 处断开，绝不跨越缺数点插值连线。"""
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    prev: tuple[int, int] | None = None
    for p in pts:
        if p is not None and prev is not None:
            segs.append((prev, p))
        prev = p
    return segs


def cell_span(left: int, right: int, n: int, index: int) -> tuple[int, int]:
    """索引 index 的格子像素范围 [lo, hi]（含端点）。

    格子以相邻两点的中点为界；首格左沿、末格右沿取绘图区边沿——
    色带「占满该时段的 x 格」、柱按格排布都用它。
    """
    if not 0 <= index < n:
        raise ValueError(f"索引越界: {index}，n={n}")
    step = (right - left) / (n - 1) if n > 1 else 0.0
    lo = left if index == 0 else round(left + (index - 0.5) * step)
    hi = right if index == n - 1 else round(left + (index + 0.5) * step)
    return lo, hi


# ---------- 值域（纯函数） ----------

def compute_y_range(values: list[float | None], y_min: float | None = None,
                    y_max: float | None = None) -> tuple[float, float]:
    """折线 y 值域：显式给定（必须成对）则用；否则取非 None 值 min/max 各留 5% 余量；
    min==max 时上下各加 1，保证返回 lo < hi 不退化。无有效数据且未显式给定 → ValueError。
    """
    if (y_min is None) != (y_max is None):
        raise ValueError("y_min 与 y_max 必须成对给定")
    if y_min is not None:
        lo, hi = float(y_min), float(y_max)
    else:
        vals = [v for v in values if v is not None]
        if not vals:
            raise ValueError("无有效数据点且未显式给定 y 范围")
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.05
        lo -= pad
        hi += pad
    if hi == lo:
        lo -= 1.0
        hi += 1.0
    return lo, hi


def compute_bar_range(values: list[float | None], bar_min: float | None = None,
                      bar_max: float | None = None) -> tuple[float, float]:
    """柱值域：显式给定（必须成对）则用；否则对称化使零线居中——
    bound = max(|min|, |max|)，bar_min=-bound、bar_max=bound；bound==0 时上下各加 1。
    """
    if (bar_min is None) != (bar_max is None):
        raise ValueError("bar_min 与 bar_max 必须成对给定")
    if bar_min is not None:
        lo, hi = float(bar_min), float(bar_max)
    else:
        vals = [v for v in values if v is not None]
        if not vals:
            raise ValueError("无有效柱值且未显式给定 bar 范围")
        bound = max(abs(min(vals)), abs(max(vals)))
        lo, hi = -bound, bound
    if hi == lo:
        lo -= 1.0
        hi += 1.0
    return lo, hi


# ---------- 画布与图元 ----------

class _Canvas:
    """RGB 画布：row-major bytearray，白色初始，坐标 (x, y)、y 向下为正。"""

    __slots__ = ("w", "h", "buf")

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self.buf = bytearray(b"\xff") * (w * h * 3)

    def set(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            off = (y * self.w + x) * 3
            self.buf[off:off + 3] = bytes(color)

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int,
                  color: tuple[int, int, int]) -> None:
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, self.w - 1), min(y1, self.h - 1)
        if x1 < x0 or y1 < y0:
            return
        row = bytes(color) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            off = (y * self.w + x0) * 3
            self.buf[off:off + len(row)] = row


def _fill_circle(canvas: _Canvas, cx: int, cy: int, r: int,
                 color: tuple[int, int, int]) -> None:
    r2 = r * r
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r2:
                canvas.set(cx + dx, cy + dy, color)


def _stamp_square(canvas: _Canvas, x: int, y: int, thickness: int,
                  color: tuple[int, int, int]) -> None:
    lo = -(thickness // 2)
    hi = thickness - 1 - thickness // 2
    canvas.fill_rect(x + lo, y + lo, x + hi, y + hi, color)


def _draw_line(canvas: _Canvas, x0: int, y0: int, x1: int, y1: int,
               color: tuple[int, int, int], thickness: int = 1) -> None:
    """整数 Bresenham 直线，逐点盖 thickness 见方章（确定性，无浮点累积）。"""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _stamp_square(canvas, x, y, thickness, color)
        if x == x1 and y == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _draw_frame(canvas: _Canvas, left: int, top: int, right: int, bottom: int) -> None:
    canvas.fill_rect(left, top, right, top, FRAME_COLOR)
    canvas.fill_rect(left, bottom, right, bottom, FRAME_COLOR)
    canvas.fill_rect(left, top, left, bottom, FRAME_COLOR)
    canvas.fill_rect(right, top, right, bottom, FRAME_COLOR)


def _draw_polyline(canvas: _Canvas, points: list[float | None], color: tuple[int, int, int],
                   left: int, top: int, right: int, bottom: int,
                   v_lo: float, v_hi: float) -> None:
    """一条折线：缺数断开的线段（3px）+ 非缺失点的实心圆点（半径 2px）。"""
    pts = map_points(points, (left, right), (v_lo, v_hi, top, bottom))
    for (x0, y0), (x1, y1) in line_segments(pts):
        _draw_line(canvas, x0, y0, x1, y1, color, LINE_THICKNESS)
    for x, y in (p for p in pts if p is not None):
        _fill_circle(canvas, x, y, POINT_RADIUS, color)


# ---------- 渲染入口 ----------

def _common_len(series: list[Series], bars: list[Bar]) -> int:
    ns = {len(s.points) for s in series} | {len(b.values) for b in bars}
    if len(ns) > 1:
        raise ValueError("各 series / bars 的点数必须一致")
    return next(iter(ns)) if ns else 0


def _validate_indices(bands: list[Band] | None, vlines: list[int] | None, n: int) -> None:
    for b in bands or []:
        if not 0 <= b.start <= b.end < n:
            raise ValueError(f"band 越界: [{b.start}, {b.end}]，n={n}")
    for v in vlines or []:
        if not 0 <= v < n:
            raise ValueError(f"vline 越界: {v}，n={n}")


def render_line_chart(path: str | Path, series: list[Series], *, y_min: float | None = None,
                      y_max: float | None = None, width: int = 1280, height: int = 480,
                      bands: list[Band] | None = None,
                      vlines: list[int] | None = None) -> Path:
    """单日 96 点（或 24 点）折线图。

    画序：背景 → 1px 边框 → bands（实底矩形，曲线之下）→ 曲线点线 → vlines（曲线之上）。
    无网格、无文字、无 alpha；缺数点断线不插值。
    """
    n = _common_len(series, [])
    if n == 0 and (bands or vlines):
        raise ValueError("无数据点时不能给 bands / vlines")
    _validate_indices(bands, vlines, n)

    canvas = _Canvas(width, height)
    canvas.fill_rect(0, 0, width - 1, height - 1, WHITE)
    left, top, right, bottom = plot_rect(width, height)
    _draw_frame(canvas, left, top, right, bottom)

    for band in bands or []:
        # 色带：从绘图区顶到底的实底矩形，覆盖 x∈[start, end] 的格子；画在曲线之下
        x_lo = cell_span(left, right, n, band.start)[0]
        x_hi = cell_span(left, right, n, band.end)[1]
        canvas.fill_rect(x_lo, top, x_hi, bottom, band.color)

    v_lo, v_hi = compute_y_range([v for s in series for v in s.points], y_min, y_max)
    for s in series:
        _draw_polyline(canvas, s.points, s.color, left, top, right, bottom, v_lo, v_hi)

    for i in vlines or []:
        x = _x_of_index(i, n, left, right)
        x0 = x - VLINE_THICKNESS // 2
        canvas.fill_rect(x0, top, x0 + VLINE_THICKNESS - 1, bottom, VLINE_COLOR)

    return _write_png(path, canvas)


def render_line_bar_chart(path: str | Path, series: list[Series], bars: list[Bar], *,
                          width: int = 1280, height: int = 640, y_min: float | None = None,
                          y_max: float | None = None, bar_min: float | None = None,
                          bar_max: float | None = None) -> Path:
    """多日对比图：上半 62% 折线区（规则同 render_line_chart，无 bands/vlines），
    下半 38% 零基线柱区，两区共用同一 x 轴（柱与折线点严格同列）；柱区中央 1px 深灰零线。
    """
    n = _common_len(series, bars)
    canvas = _Canvas(width, height)
    canvas.fill_rect(0, 0, width - 1, height - 1, WHITE)
    left, top, right, bottom = plot_rect(width, height)
    _draw_frame(canvas, left, top, right, bottom)
    line_rect, bar_rect = split_layout(width, height)

    # 上半：折线区
    v_lo, v_hi = compute_y_range([v for s in series for v in s.points], y_min, y_max)
    for s in series:
        _draw_polyline(canvas, s.points, s.color, left, line_rect[1], right, line_rect[3],
                       v_lo, v_hi)

    # 下半：零基线柱区
    b_lo, b_hi = compute_bar_range([v for b in bars for v in b.values], bar_min, bar_max)
    bar_top, bar_bottom = bar_rect[1], bar_rect[3]
    y_zero = (bar_top + bar_bottom) // 2
    n_bars = len(bars)
    for j, bar in enumerate(bars):
        for i, v in enumerate(bar.values):
            if v is None:
                continue                    # 缺数留空不画柱
            cx_lo, cx_hi = cell_span(left, right, n, i)
            bw = (cx_hi - cx_lo + 1) // n_bars if n_bars else 0
            if bw <= 0:
                continue
            bx0 = cx_lo + j * bw            # 按 bars 顺序左到右排
            py = round(bar_bottom + (v - b_lo) * (bar_top - bar_bottom) / (b_hi - b_lo))
            py = min(max(py, bar_top), bar_bottom)
            if py == y_zero:
                continue                    # 零值柱高度为 0，不画
            canvas.fill_rect(bx0, min(py, y_zero), bx0 + bw - 1, max(py, y_zero), bar.color)

    # 零线画在柱之上，柱区中央整行深灰
    canvas.fill_rect(left, y_zero, right, y_zero, FRAME_COLOR)
    return _write_png(path, canvas)


# ---------- PNG 编码（手写 IHDR / IDAT / IEND） ----------

def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _encode_png(canvas: _Canvas) -> bytes:
    w, h = canvas.w, canvas.h
    stride = w * 3
    buf = canvas.buf
    # 每行前置滤波器字节 0（None filter）
    raw = b"".join(b"\x00" + bytes(buf[y * stride:(y + 1) * stride]) for y in range(h))
    # 8-bit 深度、color type 2 (truecolor RGB)、压缩 0、滤波 0、非交错
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return (PNG_MAGIC + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))


def _write_png(path: str | Path, canvas: _Canvas) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_encode_png(canvas))
    return out
