#!/usr/bin/env python3
"""HoLo_EA tester checks and the Stage A timeframe matrix (system python; runs go through vwap_rsi_eval.run).
Quit the MT5 GUI first. Real ticks throughout: entries are exact touches of a price level.

  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py selfcheck     # H1 levels, M15 trigger, 2025-01..04, guards off
  MBT_FREE_PORTS=1 python3 scripts/holo_eval.py stage-a       # levels H1/H4/D1 x trigger M30/M15/M5, 2019-01..2026-10
  python3 scripts/vwap_rsi_eval.py report reports/ho_a_*_trades.csv
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys

import macd_sweep as M
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-a"])
    args = ap.parse_args()
    if E.mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(E.REPORTS, "holo")
    selfcheck() if args.cmd == "selfcheck" else stage_a()


if __name__ == "__main__":
    main()
