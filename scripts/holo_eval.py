#!/usr/bin/env python3
"""HoLo_EA tester checks and the Stage A timeframe matrix (system python; runs go through vwap_rsi_eval.run).
Quit the MT5 GUI first. Real ticks throughout: entries are exact touches of a price level.

  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py selfcheck     # H1 levels, M15 trigger, 2025-01..04, guards off
  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py stage-a       # levels H1/H4/D1 x trigger M30/M15/M5, 2019-01..2026-10
  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py stage-b H1_M30 H4_M15 D1_M5   # RR x break-even grid, 2019-21 and 2022-24
  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py excursions H1_M30 H4_M15 D1_M5  # one run each, exit rules replayed offline
  python3 scripts/vwap_rsi_eval.py report reports/ho_a_*_trades.csv
"""
import argparse
import shutil
import glob
import json
import os
import re
import statistics
import sys
import time

import ea_profiles as P
import excursions as X
import macd_sweep as M
import selection as S
import tradelog_checks as K
import vwap_rsi_eval as E

EXPERT = "HoLo_EA"
SC_WIN = ("2025-01-01", "2025-04-01")
OFF = {"InpMaxSpreadPoints": "0", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
NY = dict(offset_h=7, start_min=480, end_min=1015)
AGENT_LOG = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/"
                               "Program Files/MetaTrader 5/Tester/Agent-127.0.0.1-3000/logs/")
ENTRY = re.compile(r"HOLO (SELL|BUY): (?:bid|ask) ([\d.]+) at (?:HO|LO) ([\d.]+)")


def agent_log_size():
    f = sorted(glob.glob(AGENT_LOG + "*.log"))
    return (f[-1], os.path.getsize(f[-1])) if f else (None, 0)


def agent_log_since(mark):
    path, size = mark
    f = sorted(glob.glob(AGENT_LOG + "*.log"))
    if not f:
        return ""
    with open(f[-1], "rb") as h:
        h.seek(size if f[-1] == path else 0)
        data = h.read()
    if data[:2] == b"\xff\xfe":
        data = data[2:]
    return data[:len(data) // 2 * 2].decode("utf-16-le", errors="ignore")


def run(name, sets, frm=SC_WIN[0], to=SC_WIN[1], period="M15", timeout=900):
    return E.run(name, sets, period=period, frm=frm, to=to, timeout=timeout, expert=EXPERT, model="real_ticks")


def selfcheck():
    base = dict(OFF, InpRR="1.5")
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    mark = agent_log_size()
    a, rows = run("hsc_base", base)
    log = agent_log_since(mark)
    m = a["metrics"]
    check("enough trades in window", m["trades"] >= 20, f"{m['trades']}")
    check("trade log rows == trades", len(rows) == int(m["trades"]), f"{len(rows)} vs {m['trades']}")
    bad = K.ny_session_violations(rows, **NY)
    check("entries 08:00-16:55 NY, flat by 16:57 NY, never across 17:00", not bad, f"{len(bad)} violations")
    check("session flat fires", K.count_reason(rows, "session_end") >= 1, f"{K.count_reason(rows, 'session_end')}")
    check("take-profit on the profit side", not K.tp_side_violations(rows), f"{len(K.tp_side_violations(rows))}")
    unmoved = K.unmoved_stop_rows(rows)
    check("TP = 1.5 x initial stop distance", not K.rr_violations(unmoved, 1.5), f"{len(K.rr_violations(unmoved, 1.5))} of {len(unmoved)}")
    full = [r for r in unmoved if r["exit_reason"] == "sl"]
    r_sl = statistics.median(r["r_multiple"] for r in full) if full else None
    check("1% sizing: median R of initial-stop exits near -1", r_sl is not None and -1.3 <= r_sl <= -0.8, f"{r_sl}")
    moved = [r for r in rows if r not in unmoved]
    be_loss = [r for r in moved if r["exit_reason"] == "sl" and (r["r_multiple"] or 0) < -0.15]
    check("break-even moves happen and never lose a full R", moved and not be_loss, f"{len(moved)} moved, {len(be_loss)} big losses")
    ent = ENTRY.findall(log)
    late = [e for e in ent if not (0 <= (float(e[2]) - float(e[1]) if e[0] == "SELL" else float(e[1]) - float(e[2]))
                                   <= 0.0001 * float(e[2]) + 1e-9)]
    check("entries exactly at the level (<= 0.01% late)", ent and not late, f"{len(ent)} entries, {len(late)} late")
    check("one entry line per trade", len(ent) == len(rows), f"{len(ent)} vs {len(rows)}")

    b, _ = run("hsc_nofilter", dict(base, InpBreakoutFilter="false"))
    check("breakout filter removes setups", b["metrics"]["trades"] > m["trades"], f"{m['trades']} vs {b['metrics']['trades']} unfiltered")
    _, rows_c = run("hsc_nobe", dict(base, InpBETriggerPct="0"))
    check("BE off: every stop stays at its initial level", len(K.unmoved_stop_rows(rows_c)) == len(rows_c), f"{len(rows_c)} trades")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


def stage_a():
    out_path = os.path.join(M.OUTDIR, "stage_a.json")
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for level in ("H1", "H4", "D1"):
        for trig in ("M30", "M15", "M5"):
            key = f"{level}_{trig}"
            if key in out:
                continue
            d, _ = run(f"ho_a_{key}", {"InpLevelTF": E.TF[level], "InpTimeframe": E.TF[trig]},
                       frm="2019-01-01", to="2026-10-01", period=trig, timeout=3600)
            out[key] = {"metrics": d["metrics"], "trade_log": d["trade_log"]}
            json.dump(out, open(out_path, "w"), indent=1)
    E.report([os.path.join(E.REPORTS, v["trade_log"]) for v in out.values()])


GRID = {"InpRR": ["1.0", "1.5", "2.0", "2.5", "3.0"], "InpBETriggerPct": ["0.0", "0.075", "0.15"]}
REGIMES = {"1921": ("2019-01-01", "2022-01-01"), "2224": ("2022-01-01", "2025-01-01")}


def stage_b(configs):
    """Exit grid per (level, trigger) config on two regimes; the 2025-26 holdout stays untouched."""
    for cfg in configs:
        level, trig = cfg.split("_")
        base = {"InpLevelTF": E.TF[level], "InpTimeframe": E.TF[trig]}
        per = {}
        for reg, (frm, to) in REGIMES.items():
            passes = []
            for i, (fixed, ranges) in enumerate(S.plan_launches(GRID)):
                d, _ = E.run(f"ho_b_{cfg}_{reg}_{i}", dict(base, **fixed), period=trig, mode="opt", ranges=ranges,
                             frm=frm, to=to, timeout=5400, expert=EXPERT, model="real_ticks")
                passes += d.get("passes") or []
            per[reg], failed = S.drop_failed_passes(passes)
            M.log(f"[stage-b {cfg}] {reg}: {len(per[reg])} passes, {failed} failed OnInit")
        print(f"\n{cfg}: PF 2019-21 / 2022-24 (trades)")
        print("  RR \\ BE  " + "".join(f"{be:>22}" for be in GRID["InpBETriggerPct"]))
        for rr in GRID["InpRR"]:
            cells = []
            for be in GRID["InpBETriggerPct"]:
                q = [next((p for p in per[r] if abs(p["InpRR"] - float(rr)) < 1e-9 and
                           abs(p["InpBETriggerPct"] - float(be)) < 1e-9), None) for r in REGIMES]
                cells.append("  ".join("   n/a" if p is None else f"{p['Profit Factor']:.2f} ({p['Trades']:.0f})" for p in q))
            print(f"  {rr:>7}  " + "".join(f"{c:>22}" for c in cells))
        pick = S.joint_pick(per["1921"], per["2224"], GRID)
        print(f"  joint plateau pick (PF >= 1.15 both regimes): {None if not pick else {k: pick[0][k] for k in GRID}}")
        json.dump({"base": base, "grid": GRID, "passes": per, "pick": pick},
                  open(os.path.join(M.OUTDIR, f"stage_b_{cfg}.json"), "w"), indent=1, default=str)


COMMON = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/"
                            "lyudmilnikodimov/AppData/Roaming/MetaQuotes/Terminal/Common/Files/")
EXC_WIN = ("2019-01-01", "2025-01-01")                       # the 2025-26 holdout stays untouched
REG_YEARS = {"2019-21": range(2019, 2022), "2022-24": range(2022, 2025)}
PARTIAL_X = (0.3, 0.5, 0.75, 1.0)
PARTIAL_Y = (None, 1.0, 1.5, 2.0, 3.0)


def exc_run(cfg, sets, tag):
    level, trig = cfg.split("_")
    name = f"ho_{tag}_{cfg}"
    meta = os.path.join(M.OUTDIR, name + ".meta.json")
    if not os.path.exists(meta):
        t0 = time.time()
        d, _ = run(name, dict({"InpLevelTF": E.TF[level], "InpTimeframe": E.TF[trig]}, **sets),
                   frm=EXC_WIN[0], to=EXC_WIN[1], period=trig, timeout=3600)
        exc = P.pick_fresh_log(os.path.join(COMMON, f"{EXPERT}_XAUUSD_261007_exc.csv"), t0)
        if sets.get("InpExcursionLog") == "true":
            if not exc:
                sys.exit(f"[{name}] no fresh excursion log")
            shutil.copyfile(exc, os.path.join(M.OUTDIR, name + "_exc.csv"))
        json.dump({"trade_log": d["trade_log"], "metrics": d["metrics"]}, open(meta, "w"))
    return json.load(open(meta)), os.path.join(M.OUTDIR, name + "_exc.csv")


def exc_table(cfg, trades):
    reg = {k: [t for t in trades if t["open_time"].year in yrs] for k, yrs in REG_YEARS.items()}
    def line(label, fn):
        s = {k: X.summary([fn(t) for t in ts]) for k, ts in reg.items()}
        a, b = s["2019-21"], s["2022-24"]
        return min(a["pf"], b["pf"]), (f"  {label:30} PF {a['pf']:4.2f} / {b['pf']:4.2f}   R/trade {a['r_per_trade']:+.3f} / "
                                        f"{b['r_per_trade']:+.3f}   win {a['win_pct']:3.0f}% / {b['win_pct']:3.0f}%")
    print(f"\n== {cfg}: {len(reg['2019-21'])} / {len(reg['2022-24'])} trades (2019-21 / 2022-24), after costs")
    print(line("no target, flat at session end", lambda t: t["close_r"] + t["cost_r"])[1])
    for x in sorted(trades[0]["reached"]):
        print(line(f"take profit {x:.2f}R", lambda t, x=x: X.variant_r(t, "tp", x))[1])
    rows = [line(f"half at {x:.2f}R, rest " + ("to flat" if y is None else f"at {y:.1f}R"),
                 lambda t, x=x, y=y: X.variant_r(t, "partial", x, y))
            for x in PARTIAL_X for y in PARTIAL_Y if y is None or y > x]
    print("  -- partials (stop to entry after the first half), best first --")
    for _, txt in sorted(rows, reverse=True):
        print(txt)


def excursions(configs):
    for cfg in configs:
        info, exc = exc_run(cfg, {"InpRR": "0", "InpBETriggerPct": "0", "InpExcursionLog": "true"}, "x")
        exc_table(cfg, X.load(exc, os.path.join(E.REPORTS, info["trade_log"])))
    cfg = "H1_M30"                                           # how far can the replay be trusted? (earlier exits -> extra trades)
    if cfg not in configs:
        return
    info, exc = exc_run(cfg, {"InpRR": "0", "InpBETriggerPct": "0", "InpExcursionLog": "true"}, "x")
    replay = X.summary([X.variant_r(t, "tp", 0.5) for t in X.load(exc, os.path.join(E.REPORTS, info["trade_log"]))])
    real, _ = exc_run(cfg, {"InpRR": "0.5", "InpBETriggerPct": "0"}, "xc")
    rows = K.load_rows(os.path.join(E.REPORTS, real["trade_log"]))
    actual = X.summary([r["r_multiple"] or 0.0 for r in rows])
    print(f"\n== cross-check {cfg}, take profit 0.5R, 2019-2024: replay n={replay['n']} PF {replay['pf']:.2f} "
          f"R/trade {replay['r_per_trade']:+.3f} | real EA n={actual['n']} PF {actual['pf']:.2f} R/trade {actual['r_per_trade']:+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-a", "stage-b", "excursions"])
    ap.add_argument("configs", nargs="*", help="stage-b: LEVEL_TRIGGER, e.g. H1_M30")
    args = ap.parse_args()
    if E.mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(E.REPORTS, "holo")
    if args.cmd in ("stage-b", "excursions"):
        {"stage-b": stage_b, "excursions": excursions}[args.cmd](args.configs)
    else:
        {"selfcheck": selfcheck, "stage-a": stage_a}[args.cmd]()


if __name__ == "__main__":
    main()
