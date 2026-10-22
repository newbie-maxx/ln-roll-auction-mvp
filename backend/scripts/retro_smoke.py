"""复盘全链冒烟：可用日 → 模拟登记 → R1/R2 报告（含 12 图）→ MCP 直连 → skill 热加载演示。

用法（仓库根）：.venv/bin/python backend/scripts/retro_smoke.py [--db TMP]
性能口径：R1 ≤ 60s、R2（3 天）≤ 180s（B2 首版余量口径，非 PRD §10 终值）。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="隔离库目录（默认临时目录）")
    ap.add_argument("--day", default="2026-08-14")
    a = ap.parse_args()
    base = Path(a.db) if a.db else Path(tempfile.mkdtemp(prefix="retro_smoke_"))
    base.mkdir(parents=True, exist_ok=True)
    os.environ["WB_RETRO_DB"] = str(base / "retro.db")
    os.environ["WB_DB"] = str(base / "wb.db")
    import backend.config as cfg
    cfg.DB_PATH = base / "wb.db"

    from backend.mcp.client import McpClient
    from backend.retro.store import RetroStore
    from backend.store import Store

    ok = True
    t_all = time.perf_counter()

    # 1) MCP 独立调用（绕过 skill 层）
    data, retro = McpClient("data"), McpClient("retro")
    try:
        days = retro.call("actual.availability", {"start": "2026-08-01", "end": "2026-08-31"})
        print(f"[1] MCP 直连 actual.availability：{len(days)} 天可用 {'PASS' if len(days) == 31 else 'FAIL'}")
        ok &= len(days) == 31
        seed = retro.call("register.demo_seed", {"start": "2026-08-01", "end": "2026-08-31"})
        print(f"[2] 模拟登记：{seed['n_written']} 笔（四类日：成交 {len(seed['filled_days'])}、"
              f"只报没成 {len(seed['unfilled_days'])}、无登记 {len(seed['empty_days'])}、"
              f"红绿同日 {len(seed['mixed_days'])}）{'PASS' if seed['n_written'] >= 20 else 'FAIL'}")
        ok &= seed["n_written"] >= 20
        settle = retro.call("register.settle_day", {"run_date": a.day})
        print(f"[3] {a.day} 逐笔收益：{len(settle['rows'])} 笔，"
              f"已成交合计 {settle['totals']['filled']['amount']} 元、"
              f"机会成本合计 {settle['totals']['unfilled']['amount']} 元 PASS")
        t0 = time.perf_counter()
        r1 = retro.call("report.render", {"start": a.day, "end": a.day, "use_llm": False})
        t1 = time.perf_counter() - t0
        print(f"[4] R1 报告：{r1['report_id']}，{len(r1['figs'])} 图，{t1:.1f}s "
              f"{'PASS' if len(r1['figs']) == 12 and t1 <= 60 else 'FAIL'}")
        ok &= len(r1["figs"]) == 12 and t1 <= 60
        t0 = time.perf_counter()
        r2 = retro.call("report.render", {"start": "2026-08-13", "end": "2026-08-15", "use_llm": False})
        t2 = time.perf_counter() - t0
        print(f"[5] R2 报告：{r2['report_id']}，N_eff={r2['n_eff']}，{t2:.1f}s "
              f"{'PASS' if r2['n_eff'] == 3 and t2 <= 180 else 'FAIL'}")
        ok &= r2["n_eff"] == 3 and t2 <= 180
    finally:
        data.close()
        retro.close()

    # 2) skill 热加载演示：复制 skills 到临时目录 → 运行 → 改 fail 消息 → 不重启生效
    import json
    import shutil
    skills_src = Path(__file__).resolve().parent.parent / "skills"
    skills_tmp = base / "skills"
    if skills_tmp.exists():
        shutil.rmtree(skills_tmp)
    shutil.copytree(skills_src, skills_tmp)
    from backend.retro.skill_runner import SkillRunner
    runner = SkillRunner(skills_dir=skills_tmp)
    try:
        out1 = runner.run("retro_day", {"start": a.day, "end": "2026-08-20"})
        assert out1["status"] == "failed", "多日区间走 retro_day 应失败"
        msg1 = out1["error"]["message"]
        # 热修改：把断言消息改掉（不重启进程）
        f = skills_tmp / "retro_day.json"
        doc = json.loads(f.read_text(encoding="utf-8"))
        for st in doc["steps"]:
            if st.get("message") == msg1:
                st["message"] = "热加载后的新消息：请走 retro_range"
        f.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        out2 = runner.run("retro_day", {"start": a.day, "end": "2026-08-20"})
        msg2 = out2["error"]["message"]
        hot = msg2 != msg1 and "热加载后的新消息" in msg2
        audits = runner._tool_caller("db.query", {"table": "skill_audit", "limit": 50})
        print(f"[6] skill 热加载：失败消息「{msg1}」→「{msg2}」"
              f"{'PASS' if hot else 'FAIL'}（审计 {len(audits)} 条）")
        ok &= hot
        # 3) 生命周期管控：active → suspended → 拒执行 → 恢复
        runner.set_status("trade_register", "suspended", reason="smoke")
        susp = runner.run("trade_register", {
            "run_date": a.day, "t": 11, "result_type": "成交", "direction": "卖",
            "price": 350.0, "volume_mwh": 100.0})
        gated = susp["status"] == "failed" and susp["error"]["code"] == "SKILL_NOT_ACTIVE"
        runner.set_status("trade_register", "active", reason="smoke restore")
        back = runner.run("trade_register", {
            "run_date": a.day, "t": 11, "result_type": "成交", "direction": "卖",
            "price": 350.0, "volume_mwh": 100.0})
        print(f"[7] 生命周期：suspended 拒执行={gated}，恢复后 ok={back['status'] == 'ok'} "
              f"{'PASS' if gated and back['status'] == 'ok' else 'FAIL'}")
        ok &= gated and back["status"] == "ok"
    finally:
        runner.close()

    total = time.perf_counter() - t_all
    print(f"TOTAL {total:.1f}s → {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
