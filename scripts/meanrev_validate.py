#!/usr/bin/env python3
"""MeanRev_EA shoot-out (spec section 4). System python; MT5 runs go through macd_sweep.run.

Quit the MT5 GUI first. Steps can be rerun individually; each writes reports/meanrev_shootout/<step>.json.
  python3 scripts/meanrev_validate.py stage-a --mode bb      # grids on 2024-26, 1-min OHLC
  python3 scripts/meanrev_validate.py stage-a --mode rsi2
  python3 scripts/meanrev_validate.py stage-b --mode bb      # real ticks per year + 2024-26 + 2022-26
  python3 scripts/meanrev_validate.py stage-b --mode rsi2
  python3 scripts/meanrev_validate.py macd                   # MACD v1.42 reference, real ticks 2022-26
  python3 scripts/meanrev_validate.py decide                 # gates, winner, combined risk check
"""
import argparse
import json
import os
import subprocess
import sys

import combine_eas as C
import macd_sweep as M
import selection as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
OUT = os.path.join(REPORTS, "meanrev_shootout")
IS_FROM, IS_TO = "2024-01-01", "2026-10-01"
MODE_SETS = {"bb": {"InpEntryMode": "0"}, "rsi2": {"InpEntryMode": "1"}}
GRIDS = {
    "bb": {"InpBBDev": ["2.0", "2.5"], "InpRSILow": ["25", "30", "50"], "InpADXMax": ["15", "20", "25", "100"],
           "InpSLATR": ["1.0", "1.5", "2.0", "3.0"], "InpMaxBars": ["6", "12", "24"]},
    "rsi2": {"InpRSI2Entry": ["5", "10", "15"], "InpRSI2Exit": ["50", "70"], "InpTrendPeriod": ["100", "200"],
             "InpSLATR": ["1.5", "2.0", "3.0", "4.0"], "InpMaxBars": ["12", "24", "48"]},
}
YEARS = {"2022": ("2022-01-01", "2023-01-01"), "2023": ("2023-01-01", "2024-01-01"),
         "2024": ("2024-01-01", "2025-01-01"), "2025": ("2025-01-01", "2026-01-01"),
         "2026": ("2026-01-01", "2026-10-01")}
WINDOWS = dict(YEARS, **{"2024-2026": (IS_FROM, IS_TO), "2022-2026": ("2022-01-01", IS_TO)})


def save(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


def load(name):
    with open(os.path.join(OUT, name + ".json")) as f:
        return json.load(f)


def guarded_run(*args, **kw):
    """macd_sweep.run with one retry after killing a hung Wine terminal."""
    for attempt in (1, 2):
        d = M.run(*args, **kw)
        if d.get("metrics") or d.get("passes") is not None and d.get("passes"):
            return d
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    return d


def stage_a(mode):
    passes = []
    for i, (fixed, ranges) in enumerate(S.plan_launches(GRIDS[mode])):
        d = guarded_run("opt", f"a_{mode}_{i:02d}", sets=dict(MODE_SETS[mode], **fixed), ranges=ranges,
                        model="1min_ohlc", frm=IS_FROM, to=IS_TO, timeout=600, expert="MeanRev_EA")
        for p in d.get("passes") or []:
            for k, v in fixed.items():
                p[k] = float(v)
            passes.append(p)
    want = 1
    for vals in GRIDS[mode].values():
        want *= len(vals)
    pick = S.pick_plateau(passes, GRIDS[mode])
    M.log(f"[stage-a {mode}] {len(passes)}/{want} passes, pick: {pick}")
    save(f"stage_a_{mode}", {"passes": passes, "expected": want, "pick": pick})


def stage_b(mode):
    a = load(f"stage_a_{mode}")
    if not a["pick"]:
        save(f"stage_b_{mode}", {"error": "no Stage A survivor"})
        M.log(f"[stage-b {mode}] skipped: no Stage A survivor")
        return
    sets = dict(MODE_SETS[mode], **{k: M.fmt(float(a["pick"][k])) for k in GRIDS[mode]})
    out = {"sets": sets, "runs": {}}
    for label, (frm, to) in WINDOWS.items():
        d = guarded_run("single", f"b_{mode}_{label}", sets=sets, model="real_ticks", frm=frm, to=to,
                        timeout=900, expert="MeanRev_EA")
        out["runs"][label] = {"metrics": d.get("metrics"), "trade_log": d.get("trade_log"), "error": d.get("error")}
    save(f"stage_b_{mode}", out)


def macd():
    d = guarded_run("single", "b_macd_2022-2026", sets={}, model="real_ticks", frm="2022-01-01", to=IS_TO,
                    timeout=900, expert="MACD_Cross_EA")
    save("macd", {"metrics": d.get("metrics"), "trade_log": d.get("trade_log"), "error": d.get("error")})


def decide():
    m = load("macd")
    macd_trades = C.load_trades(os.path.join(REPORTS, m["trade_log"]))
    macd_alone = C.summarize(macd_trades)
    results = {}
    for mode in GRIDS:
        try:
            b = load(f"stage_b_{mode}")
        except FileNotFoundError:
            continue
        if "runs" not in b or any(not b["runs"][w]["metrics"] for w in WINDOWS):
            results[mode] = {"error": b.get("error", "missing Stage B runs")}
            continue
        mr = C.load_trades(os.path.join(REPORTS, b["runs"]["2022-2026"]["trade_log"]))
        corr = C.monthly_correlation(macd_trades, mr)
        combined = C.summarize(macd_trades, mr)
        years = {y: b["runs"][y]["metrics"] for y in YEARS}
        gates = S.evaluate_gates(years, b["runs"]["2024-2026"]["metrics"], corr, combined, macd_alone)
        results[mode] = {"sets": b["sets"], "gates": gates, "corr": corr, "combined": combined,
                         "alone": C.summarize(mr)}
    scored = {k: v for k, v in results.items() if "gates" in v}
    winner = S.choose_winner(scored) if scored else None
    risk = None
    if winner:
        c = scored[winner]["combined"]
        factor = min(1.0, 3.0 / abs(c["worst_day_pct"]) if c["worst_day_pct"] < 0 else 1.0,
                     20.0 / c["max_dd_pct"] if c["max_dd_pct"] > 0 else 1.0)
        risk = {"worst_day_pct": c["worst_day_pct"], "max_dd_pct": c["max_dd_pct"],
                "scale_needed": factor < 1.0, "suggested_risk_pct": round(int(factor * 20) / 20.0, 2)}
    save("decision", {"macd_alone": macd_alone, "modes": results, "winner": winner, "risk_check": risk})
    print(json.dumps({"winner": winner, "risk_check": risk,
                      "gates": {k: v.get("gates") for k, v in results.items()}}, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["stage-a", "stage-b", "macd", "decide"])
    ap.add_argument("--mode", choices=sorted(GRIDS))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    M.OUTDIR = OUT
    if args.step in ("stage-a", "stage-b", "macd") and \
            subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0:
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    if args.step in ("stage-a", "stage-b") and not args.mode:
        sys.exit("--mode is required for stage-a / stage-b")
    {"stage-a": lambda: stage_a(args.mode), "stage-b": lambda: stage_b(args.mode),
     "macd": macd, "decide": decide}[args.step]()


if __name__ == "__main__":
    main()
