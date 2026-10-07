#!/usr/bin/env python3
"""EuroRiddle_EA checks and the pre-registered Stage A (docs/superpowers/specs/2026-10-06-euroriddle-prereg.md).
System python; tester runs go through vwap_rsi_eval.run, one agent at a time. Quit the MT5 GUI first.

  python3 scripts/eurid_eval.py selfcheck   # 5 real-tick runs, Jan-Apr 2024
  python3 scripts/eurid_eval.py stage-a     # 3 versions x 3 filters x {2015-18, 2019-22}, 1-min OHLC
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

import holo_eval as H
import macd_sweep as M
import prop_mc as PM
import tradelog_checks as K
import vwap_rsi_eval as E

EXPERT, SYMBOL = "EuroRiddle_EA", "EURUSD"
SC_WIN = ("2024-01-01", "2024-04-01")
OFF = {"InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
MODES = {"A": "0", "B": "1", "C": "2"}
FILTERS = {"none": "0", "RSI": "1", "MACD": "2"}
PERIODS = {"2015-18": ("2015-01-01", "2019-01-01"), "2019-22": ("2019-01-01", "2023-01-01")}
ENTRY = re.compile(r"EURID (SELL|BUY): (.*?), entry ([\d.]+)")


def run(name, sets, frm=SC_WIN[0], to=SC_WIN[1], model="real_ticks", timeout=1800):
    return E.run(name, sets, period="H1", frm=frm, to=to, timeout=timeout, expert=EXPERT, model=model, symbol=SYMBOL)


def selfcheck():
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    logs = {}
    for v in ("A", "B", "C"):
        mark = H.agent_log_size()
        d, rows = run(f"esc_{v}", dict(OFF, InpMode=MODES[v]))
        logs[v] = (d, rows, ENTRY.findall(H.agent_log_since(mark)))
    for v, (d, rows, ent) in logs.items():
        n = int(d["metrics"]["trades"])
        check(f"{v}: trades in window and log complete", n >= 15 and len(rows) == n == len(ent), f"{n} trades, {len(rows)} rows, {len(ent)} entry lines")
        check(f"{v}: at most one entry per trading day", not K.entries_per_day_violations(rows), f"{len(K.entries_per_day_violations(rows))}")
        bad = K.fixed_stop_target_violations(rows, stop=0.0015, target=0.0025, tol=0.00003)
        check(f"{v}: stop 15 pips and target 25 pips", not bad, f"{len(bad)} violations")
        held = [r for r in rows if r["close_time"].date() != r["open_time"].date()]
        check(f"{v}: flat before the day ends (17:00 NY)", not held, f"{len(held)} held past midnight server")
    _, rowsA, entA = logs["A"]
    wrong = [e for e in entA if (e[0] == "SELL" and float(e[2]) < float(re.search(r"high ([\d.]+)", e[1]).group(1)) - 1e-9)
             or (e[0] == "BUY" and "low" not in e[1])]
    check("A: sells at or above the previous-day high, buys at the previous-day low", entA and not wrong, f"{len(wrong)} wrong")
    _, _, entB = logs["B"]
    short = [e for e in entB if float(re.search(r"range ([\d.]+) pips >= ([\d.]+) x ADR ([\d.]+)", e[1]).group(1)) <
             float(re.search(r"ADR ([\d.]+)", e[1]).group(1)) - 1e-9]
    check("B: every entry after the range reached ADR", entB and not short, f"{len(short)} early")
    _, rowsC, _ = logs["C"]
    early = [r for r in rowsC if r["open_time"].hour < 19]
    check("C: every entry from 12:00 New York (19:00 server)", rowsC and not early, f"{len(early)} early")
    for f, pat in (("RSI", r"RSI ([\d.]+)"), ("MACD", r"MACD histogram (-?[\d.]+) -> (-?[\d.]+)")):
        mark = H.agent_log_size()
        _, rows = run(f"esc_A_{f}", dict(OFF, InpMode="0", InpFilter=FILTERS[f]))
        ent = ENTRY.findall(H.agent_log_since(mark))
        bad = []
        for side, why, _ in ent:
            m = re.search(pat, why)
            if f == "RSI":
                ok = float(m.group(1)) >= 70 if side == "SELL" else float(m.group(1)) <= 30
            else:
                ok = float(m.group(2)) < float(m.group(1)) if side == "SELL" else float(m.group(2)) > float(m.group(1))
            bad += [] if ok else [why]
        check(f"A + {f}: every entry confirmed by the filter", ent and not bad and len(ent) == len(rows), f"{len(ent)} entries, {len(bad)} unconfirmed")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


def per_period(path):
    t, R = PM.load_trades([path], 1.0)
    R = [float(x) for x in R]
    g = sum(x for x in R if x > 0); l = -sum(x for x in R if x < 0)
    return {"n": len(R), "pf": g / l if l else 0.0, "net_r": sum(R), "win": 100.0 * sum(1 for x in R if x > 0) / len(R) if R else 0.0}


def stage_a():
    out_path = os.path.join(M.OUTDIR, "stage_a.json")
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for v, mode in MODES.items():
        for f, filt in FILTERS.items():
            for p, (frm, to) in PERIODS.items():
                key = f"{v}_{f}_{p}"
                if key in out:
                    continue
                d, _ = run(f"eur_a_{v}_{f}_{p}", {"InpMode": mode, "InpFilter": filt}, frm=frm, to=to, model="1min_ohlc")
                out[key] = dict(per_period(os.path.join(E.REPORTS, d["trade_log"])), trade_log=d["trade_log"],
                                dd=d["metrics"]["equity_dd_rel_pct"])
                json.dump(out, open(out_path, "w"), indent=1)
    print(f"{'version':22} {'filter':6} | {'2015-18: n   PF  win  net R':28} | {'2019-22: n   PF  win  net R':28} | gate A")
    for v, label in (("A", "A previous-day extreme"), ("B", "B range exhaustion"), ("C", "C late-day extreme")):
        for f in FILTERS:
            a, b = out[f"{v}_{f}_2015-18"], out[f"{v}_{f}_2019-22"]
            ok = all(x["pf"] >= 1.15 and x["n"] >= 100 for x in (a, b))
            print(f"{label:22} {f:6} | {a['n']:5d} {a['pf']:4.2f} {a['win']:4.0f}% {a['net_r']:+7.1f} | "
                  f"{b['n']:5d} {b['pf']:4.2f} {b['win']:4.0f}% {b['net_r']:+7.1f} | {'PASS' if ok else 'fail'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-a"])
    args = ap.parse_args()
    if E.mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(E.REPORTS, "euroriddle")
    {"selfcheck": selfcheck, "stage-a": stage_a}[args.cmd]()


if __name__ == "__main__":
    main()
