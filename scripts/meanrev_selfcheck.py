#!/usr/bin/env python3
"""MeanRev_EA acceptance checks in MT5's tester (spec 4.6 + Review Focus). Quit the MT5 GUI first.

  python3 scripts/meanrev_selfcheck.py --mode bb
  python3 scripts/meanrev_selfcheck.py --mode rsi2
Exit code 0 only if every check passes.
"""
import argparse
import os
import subprocess
import sys

import macd_sweep as M
import tradelog_checks as K

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
WIN = ("2025-01-01", "2025-07-01")
OFF = {"InpMaxSpreadPoints": "0", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
MODES = {"bb": {"InpEntryMode": "0", "InpSLATR": "1.5", "InpMaxBars": "12", "InpRSILow": "30", "InpADXMax": "100"},
         "rsi2": {"InpEntryMode": "1", "InpSLATR": "3.0", "InpMaxBars": "24", "InpRSI2Entry": "10",
                  "InpRSI2Exit": "70", "InpTrendPeriod": "200"}}


def mt5_running():
    return subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0


def run(name, sets):
    for attempt in (1, 2):
        d = M.run("single", name, sets=sets, model="1min_ohlc", frm=WIN[0], to=WIN[1], timeout=900,
                  expert="MeanRev_EA")
        if d.get("metrics") and d.get("trade_log"):
            return d, K.load_rows(os.path.join(REPORTS, d["trade_log"]))
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    sys.exit(f"[{name}] no report or trade log after 2 attempts: {d.get('error')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    mode = ap.parse_args().mode
    if mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(REPORTS, "meanrev_selfcheck")
    os.makedirs(M.OUTDIR, exist_ok=True)
    base = dict(MODES[mode], **OFF)
    results = []

    def check(label, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    a, rows_a = run(f"sc_{mode}_det1", base)
    b, _ = run(f"sc_{mode}_det2", base)
    ma, mb = a["metrics"], b["metrics"]
    check("determinism", (ma["net_profit"], ma["trades"]) == (mb["net_profit"], mb["trades"]),
          f"{ma['net_profit']} / {ma['trades']} vs {mb['net_profit']} / {mb['trades']}")
    check("enough trades in window", ma["trades"] >= 20, f"{ma['trades']}")
    check("trade log rows == trades", len(rows_a) == int(ma["trades"]), f"{len(rows_a)} vs {ma['trades']}")
    r = K.median_sl_r(rows_a)
    check("1% sizing: median R of sl exits near -1", r is not None and -1.3 <= r <= -0.8, f"{r}")
    check("time limit honoured", not K.time_limit_violations(rows_a, int(base["InpMaxBars"])),
          f"{len(K.time_limit_violations(rows_a, int(base['InpMaxBars'])))} violations")
    if mode == "bb":
        check("BB take-profit on profit side", not K.tp_side_violations(rows_a),
              f"{len(K.tp_side_violations(rows_a))} violations")
    else:
        check("RSI2 exits happen", K.count_reason(rows_a, "rsi_exit") >= 1, f"{K.count_reason(rows_a, 'rsi_exit')}")

    _, rows = run(f"sc_{mode}_bars1", dict(base, InpMaxBars="1"))
    check("forced time limit fires", K.count_reason(rows, "time_limit") >= 1, f"{K.count_reason(rows, 'time_limit')}")
    check("forced time limit bounds", not K.time_limit_violations(rows, 1), f"{len(K.time_limit_violations(rows, 1))}")

    _, rows = run(f"sc_{mode}_daily", dict(base, InpDailyLossPct="0.3"))
    check("forced daily stop closes positions", K.count_reason(rows, "daily_stop") >= 1,
          f"{K.count_reason(rows, 'daily_stop')}")

    _, rows = run(f"sc_{mode}_monthly", dict(base, InpMonthlyLossPct="2.0"))
    breaches, bad = K.monthly_stop_check(rows, 2.0)
    check("forced monthly stop blocks entries", breaches >= 1 and not bad, f"breaches {breaches}, violations {len(bad)}")

    _, rows = run(f"sc_{mode}_spread", dict(base, InpMaxSpreadPoints="20"))
    check("spread guard: no entry above 20 pts", K.max_spread(rows) <= 20, f"max {K.max_spread(rows)}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
