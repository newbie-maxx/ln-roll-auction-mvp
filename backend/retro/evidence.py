"""M-05 证据包组装（附录 A 结构；封闭数字集，AI 唯一输入，逐字段可复现）。

关键设计：near_zero 必须存在（防只讲显著项）；data_quality 必须随包（防在缺口上编结论）；
manual_layer.available=false 时 AI 必须明说；anomaly 只进第④段；所有数值带 id。
确定性：固定遍历序（t 升序、因子按 FACTORS 序）、round 4、键排序序列化。
"""
from __future__ import annotations

import json

from . import constants as C

_ID_PREFIX = {"风电": "B-WIND", "联络线": "B-TIE", "负荷": "B-LOAD", "地方燃煤": "B-THERM",
              "核电": "B-NUKE", "水电": "B-HYDRO", "非市场化": "B-NONMKT", "光伏": "B-SOLAR",
              "省间交易总量": "B-INTER"}


def _mean(xs: list[float | None]) -> float | None:
    ys = [x for x in xs if x is not None]
    return round(sum(ys) / len(ys), 2) if ys else None


def build(run_date: str, dec: dict, jump_result: dict, delta_result: dict,
          manual_items: list[dict] | None = None, params_meta: dict | None = None) -> dict:
    spot = delta_result["table"]["电价"]["v_act"]
    pred = delta_result["table"]["电价"]["v_rev"]
    contribs = sorted(
        ((f, v) for f, v in dec["contributions"].items() if v is not None),
        key=lambda kv: (-abs(kv[1]), kv[0]))
    top, near = contribs[:5], contribs[5:]

    down_pts = [p for p in jump_result["jump_points"]
                if p["jump_type"] == "类型一" and p.get("jump_side") == "down"]
    headline_combo = None
    if down_pts:
        d = down_pts[0]["detail"]
        headline_combo = {"gap_mw": d.get("gap_mw"), "items": d.get("combo"),
                          "combo_unique": d.get("combo_unique")}

    dominant = "无"
    if jump_result["day_type3"]:
        dominant = "类型三"
    elif jump_result["day_type2"]:
        dominant = "类型二"
    elif down_pts:
        dominant = "类型一"
    elif any(p["jump_type"] == "类型一" and p.get("jump_side") == "up"
             for p in jump_result["jump_points"]):
        dominant = "类型一"

    missing_points = [f"{item} t={i + 1}"
                      for item, tb in delta_result["table"].items()
                      for i, m in enumerate(tb["missing"]) if m]
    n_total = len(delta_result["table"]) * 96
    coverage = round(1 - len(missing_points) / n_total, 4) if n_total else 0.0

    pack = {
        "meta": {"run_date": run_date, "period": None,
                 "pack_version": "v1",
                 "params": params_meta or {},
                 "pred_source": delta_result["pred_source"]},
        "headline": {
            "price_pred": _mean(pred), "price_actual": _mean(spot),
            "delta_total": _mean(delta_result["table"]["电价"]["delta"]),
            "residual": _mean(dec["residual"]),
            "explained_ratio": dec["explained_ratio"],
            "jump_type": dominant, "jump_side": ("down" if down_pts else
                                                 "up" if dominant == "类型一" else None),
            "combo": headline_combo},
        "contributions": [
            {"id": f"{_ID_PREFIX.get(f, 'B-X')}-{i + 1:02d}", "boundary": f,
             "contribution": v, "share": dec["shares"].get(f),
             "delta_value": _mean(delta_result["table"][f]["delta"])}
            for i, (f, v) in enumerate(top)],
        "near_zero": [
            {"id": f"{_ID_PREFIX.get(f, 'B-X')}-{i + 1:02d}", "boundary": f, "contribution": v}
            for i, (f, v) in enumerate(near)],
        "anomaly": jump_result["anomaly"],
        "manual_layer": {"available": bool(manual_items), "items": manual_items or []},
        "data_quality": {
            "coverage_rate": coverage, "missing_points": missing_points,
            "source_flags": {
                "人工修订点数": sum(1 for tb in delta_result["table"].values()
                                  if tb.get("v_sys") and any(
                                      a is not None and b is not None and a != b
                                      for a, b in zip(tb["v_sys"], tb["v_rev"]))),
                "p_set_count": jump_result["p_set_count"],
                "p_set_ratio": jump_result["p_set_ratio"]}},
    }
    return pack


def dumps(pack: dict) -> str:
    """确定性序列化（键排序 + 固定精度）；P-6 复现比对用。"""
    return json.dumps(pack, ensure_ascii=False, sort_keys=True)


def all_values(pack: dict) -> set[float]:
    """包内全部数值集合（1 位小数容差基准）——溯源校验的回查库。"""
    vals: set[float] = set()

    def walk(o):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            vals.add(round(float(o), 1))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(pack)
    return vals
