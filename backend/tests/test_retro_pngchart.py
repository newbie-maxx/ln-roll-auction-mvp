"""pngchart 纯标准库 PNG 渲染器测试（全部确定性，PRD §E-A7.4）：
字节级确定性与 IHDR 结构、None 断线分段、y 值域不退化、色带在曲线下层、
vline 在曲线之上、组合图零线 / 缺柱留空 / 上下两区 x 对齐、多柱左到右排布。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from backend.retro.pngchart import (Band, Bar, Series, cell_span, compute_bar_range,
                                    compute_y_range, line_segments, map_points, plot_rect,
                                    render_line_bar_chart, render_line_chart, split_layout)

MAGIC = b"\x89PNG\r\n\x1a\n"
FRAME = (80, 80, 80)
VLINE = (200, 60, 60)
LINE_COLOR = (31, 78, 121)
BAND_COLOR = (232, 160, 160)     # PRD 已成交深红 #E8A0A0
BAR_COLOR = (120, 120, 220)


# ---------- helpers：最小 PNG 解码（zlib + struct，仅支持 filter 0） ----------

def decode_png(path: str | Path) -> tuple[int, int, list[bytes]]:
    """解码本渲染器产出的 PNG：返回 (width, height, rows)，rows[y] 为该行 w*3 字节原始 RGB。"""
    data = Path(path).read_bytes()
    assert data[:8] == MAGIC
    pos = 8
    tags: list[bytes] = []
    ihdr = idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"{tag} CRC 不符"
        tags.append(tag)
        if tag == b"IHDR":
            ihdr = payload
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + length
        if tag == b"IEND":
            break
    assert tags == [b"IHDR", b"IDAT", b"IEND"]      # 手写三块、顺序固定
    assert pos == len(data)                          # IEND 后无尾随字节
    w, h, depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    assert depth == 8 and color_type == 2 and interlace == 0   # 8-bit RGB 非交错
    raw = zlib.decompress(idat)
    stride = w * 3
    assert len(raw) == h * (stride + 1)
    rows = []
    for y in range(h):
        row = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert row[0] == 0, "行滤波器必须是 0（None）"
        rows.append(row[1:])
    return w, h, rows


def px(rows: list[bytes], x: int, y: int) -> tuple[int, ...]:
    off = x * 3
    return (rows[y][off], rows[y][off + 1], rows[y][off + 2])


def column_pixels(rows: list[bytes], x: int, y0: int, y1: int) -> list[tuple[int, ...]]:
    return [px(rows, x, y) for y in range(y0, y1 + 1)]


def make96() -> list[float | None]:
    vals = [10.0 + (i * 37) % 23 for i in range(96)]
    vals[7] = None
    vals[40] = None
    return vals


# ---------- 1. 确定性与 PNG 结构 ----------

def test_same_input_renders_identical_bytes_and_valid_ihdr(tmp_path: Path):
    def make(p: Path) -> Path:
        return render_line_chart(
            p,
            [Series("实况", LINE_COLOR, make96()),
             Series("日前", (200, 120, 0), [float(i % 19) for i in range(96)])],
            bands=[Band(10, 13, BAND_COLOR)],
            vlines=[50],
        )

    b1 = make(tmp_path / "a.png").read_bytes()
    b2 = make(tmp_path / "b.png").read_bytes()
    assert b1 == b2, "同一输入两次渲染必须字节相同"
    assert b1[:8] == MAGIC
    w, h, _ = decode_png(tmp_path / "a.png")     # decode_png 内断言 IHDR 宽高字段之外的结构
    assert (w, h) == (1280, 480)                 # IHDR 宽高正确（解码 IHDR 字段）


def test_accepts_str_path_and_returns_path(tmp_path: Path):
    out = render_line_chart(str(tmp_path / "sub" / "g.png"),
                            [Series("x", LINE_COLOR, [1.0, 2.0, 3.0])])
    assert isinstance(out, Path) and out.exists()
    assert isinstance(render_line_bar_chart(
        tmp_path / "c.png", [Series("x", LINE_COLOR, [1.0, 2.0, 3.0])],
        [Bar("y", BAR_COLOR, [1.0, None, -1.0])]), Path)


# ---------- 2. 几何单元测试 ----------

def test_map_points_none_breaks_line_into_disjoint_segments():
    # [1, None, 3]：None 两侧都不连线（不跨越缺数点插值），只余两个孤立点
    pts = map_points([1.0, None, 3.0], (0, 100), (0.0, 4.0, 0, 100))
    assert pts == [(0, 75), None, (100, 25)]     # 值域线性映射、首尾 x 落 xlim 两端
    assert line_segments(pts) == []

    # 两个缺口把折线分成互不相连的两段
    pts2 = map_points([1.0, 2.0, None, 4.0, 5.0], (0, 400), (0.0, 6.0, 0, 200))
    segs = line_segments(pts2)
    assert len(segs) == 2
    (a0, a1), (b0, b1) = segs
    assert a1[0] < b0[0] and b1[0] <= 400        # 两段 x 区间不相交 → 不相连
    assert pts2[2] is None


def test_line_segments_edge_cases():
    assert line_segments([]) == []
    assert line_segments([None, None]) == []
    assert line_segments([(1, 1)]) == []
    assert line_segments([(1, 1), None, (2, 2), (3, 3)]) == [((2, 2), (3, 3))]


def test_y_range_not_degenerate_when_min_equals_max():
    lo, hi = compute_y_range([5.0, 5.0, 5.0])
    assert (lo, hi) == (4.0, 6.0) and lo < hi    # min==max → 上下各加 1，不退化
    lo2, hi2 = compute_y_range([0.0, 100.0])     # 常规：各留 5% 余量
    assert lo2 == pytest.approx(-5.0) and hi2 == pytest.approx(105.0)
    assert compute_y_range([0.0, 100.0], 10.0, 20.0) == (10.0, 20.0)   # 显式给定优先
    with pytest.raises(ValueError):
        compute_y_range([1.0], y_min=0.0)        # 必须成对给定
    with pytest.raises(ValueError):
        compute_y_range([None, None])            # 无有效数据且未显式给定


def test_bar_range_symmetric_zero_centered():
    assert compute_bar_range([-30.0, 10.0, None]) == (-30.0, 30.0)   # 对称化，零线居中
    assert compute_bar_range([0.0, 0.0]) == (-1.0, 1.0)              # bound==0 不退化
    assert compute_bar_range([5.0], -100.0, 100.0) == (-100.0, 100.0)
    with pytest.raises(ValueError):
        compute_bar_range([None], bar_min=0.0)


def test_cell_span_geometry():
    left, right, n = 0, 230, 24
    step = (right - left) / (n - 1)
    assert cell_span(left, right, n, 0) == (left, round(0.5 * step))          # 首格取左沿
    assert cell_span(left, right, n, n - 1)[1] == right                       # 末格取右沿
    lo, hi = cell_span(left, right, n, 12)
    assert lo == round(11.5 * step) and hi == round(12.5 * step)              # 中点分格
    assert cell_span(5, 9, 1, 0) == (5, 9)                                    # 单点整宽
    with pytest.raises(ValueError):
        cell_span(left, right, n, 24)


# ---------- 3. 色带在曲线下层、vline 在曲线上层 ----------

def test_bands_under_curves_and_vline_on_top(tmp_path: Path):
    n = 24
    vals = [10.0 + (i * 37) % 23 for i in range(n)]
    path = render_line_chart(tmp_path / "g.png", [Series("x", LINE_COLOR, vals)],
                             bands=[Band(4, 7, BAND_COLOR)], vlines=[12])
    w, h, rows = decode_png(path)
    left, top, right, bottom = plot_rect(w, h)
    v_lo, v_hi = compute_y_range(vals)

    # 色带列范围 = 索引 4 格左沿 → 索引 7 格右沿
    b_lo, b_hi = cell_span(left, right, n, 4)[0], cell_span(left, right, n, 7)[1]
    mid_col = (b_lo + b_hi) // 2
    hits = sum(1 for c in column_pixels(rows, mid_col, top, bottom) if c == BAND_COLOR)
    assert hits >= (bottom - top) - 60, "色带覆盖列必须几乎整列都是色带色（曲线只占少数行）"

    # 色带不越界：带外列不应出现任何色带色
    assert all(c != BAND_COLOR for c in column_pixels(rows, b_hi + 30, top, bottom))

    # 曲线色仍在：带内某点的圆心像素是曲线色，而同列上方仍是色带色 → 色带在下、不盖线
    pt = map_points(vals, (left, right), (v_lo, v_hi, top, bottom))[5]
    assert b_lo <= pt[0] <= b_hi
    assert px(rows, pt[0], pt[1]) == LINE_COLOR
    assert px(rows, pt[0], top + 5) == BAND_COLOR

    # vline：2px、固定色、贯穿绘图区、画在曲线之上（该列圆点被 vline 色覆盖）
    x12 = map_points(vals, (left, right), (v_lo, v_hi, top, bottom))[12][0]
    for x in (x12 - 1, x12):
        assert px(rows, x, (top + bottom) // 2) == VLINE
        assert px(rows, x, top + 3) == VLINE
        assert px(rows, x, bottom - 3) == VLINE
    assert px(rows, x12 - 2, top + 3) != VLINE and px(rows, x12 + 1, top + 3) != VLINE


# ---------- 4. 组合图（折线 + 零基线柱） ----------

def test_line_bar_chart_zero_line_none_gap_and_x_alignment(tmp_path: Path):
    n = 24
    line_vals = [50.0 + i for i in range(n)]
    bar_vals = [float(i % 4 - 1.5) * 40.0 for i in range(n)]   # -60/-20/20/60 循环，无零值
    bar_vals[6] = None                                          # 缺数柱
    path = render_line_bar_chart(tmp_path / "c.png",
                                 [Series("价", LINE_COLOR, line_vals)],
                                 [Bar("偏差", BAR_COLOR, bar_vals)])
    w, h, rows = decode_png(path)
    assert (w, h) == (1280, 640)
    left, top, right, bottom = plot_rect(w, h)
    line_rect, bar_rect = split_layout(w, h)
    assert line_rect[0] == bar_rect[0] == left and line_rect[2] == bar_rect[2] == right
    assert bar_rect[1] == line_rect[3] + 1                      # 两区上下相邻、共用 x 轴
    assert line_rect[3] - line_rect[1] + 1 == round((bottom - top + 1) * 0.62)

    # 零线：柱区中央 1px、深灰、贯穿整个绘图区宽
    y_zero = (bar_rect[1] + bar_rect[3]) // 2
    assert bar_rect[1] <= y_zero <= bar_rect[3]
    assert all(px(rows, x, y_zero) == FRAME for x in range(left, right + 1))

    # None 柱列整格无柱色（开区间内部：相邻格的柱可落在共享分界列上）；相邻有值柱整格有柱色
    cx0, cx1 = cell_span(left, right, n, 6)
    assert all(c != BAR_COLOR for c in
               (px(rows, x, y) for x in range(cx0 + 1, cx1)
                for y in range(bar_rect[1], bar_rect[3] + 1)))
    dx0, dx1 = cell_span(left, right, n, 7)
    assert any(px(rows, x, y) == BAR_COLOR for x in range(dx0, dx1 + 1)
               for y in range(bar_rect[1], bar_rect[3] + 1))

    # 两区 x 对齐：同一索引的折线点 x 落在该索引柱的实际像素范围内（单柱占满格子）
    v_lo, v_hi = compute_y_range(line_vals)
    mapped = map_points(line_vals, (left, right), (v_lo, v_hi, line_rect[1], line_rect[3]))
    for i in (2, 11, 20):
        pt = mapped[i]
        col_hits = [x for x in range(cell_span(left, right, n, i)[0],
                                     cell_span(left, right, n, i)[1] + 1)
                    if any(px(rows, x, y) == BAR_COLOR
                           for y in range(bar_rect[1], bar_rect[3] + 1))]
        assert col_hits, f"索引 {i} 应有柱"
        assert col_hits[0] <= pt[0] <= col_hits[-1], "折线点 x 与柱必须严格同列"


def test_two_bars_left_to_right_in_one_cell(tmp_path: Path):
    n = 24
    c1, c2 = (200, 60, 60), (60, 120, 200)
    path = render_line_bar_chart(
        tmp_path / "d.png", [Series("价", LINE_COLOR, [100.0] * n)],
        [Bar("a", c1, [40.0] * n), Bar("b", c2, [-40.0] * n)],
        width=640, height=640)
    w, h, rows = decode_png(path)
    left, top, right, bottom = plot_rect(w, h)
    _, bar_rect = split_layout(w, h)
    y_zero = (bar_rect[1] + bar_rect[3]) // 2
    cx0, cx1 = cell_span(left, right, n, 10)
    bw = (cx1 - cx0 + 1) // 2                                   # 柱宽 = 每格宽 / len(bars) 向下取整
    xs1 = [x for x in range(cx0, cx1) if px(rows, x, y_zero - 2) == c1]   # 窗口去掉与右邻共享的分界列
    xs2 = [x for x in range(cx0, cx1) if px(rows, x, y_zero + 2) == c2]
    assert xs1 == list(range(cx0, cx0 + bw)), "第一根柱必须从格左沿起、恰好 bw 列宽"
    assert xs2 == list(range(cx0 + bw, cx0 + 2 * bw)), "第二根柱紧随其后、各占一半"
