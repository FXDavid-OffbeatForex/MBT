#!/usr/bin/env python3
"""VWAP_RSI_EA tester checks and two-regime selection (system python; MT5 runs go through macd_sweep.run).
Quit the MT5 GUI first (MT5 is single-instance).

  python3 scripts/vwap_rsi_eval.py selfcheck                      # SL/TP geometry + sizing, H1 2025-01..07, guards off
  python3 scripts/vwap_rsi_eval.py stage-b --tf H1 --anchor 0     # 32-pass grid on 2019-21 and 2022-24, joint plateau pick
  python3 scripts/vwap_rsi_eval.py report reports/vr_a_*_trades.csv   # PF, net R by regime, top entry hours per log

Stage A and Stage C are plain commands (results land in reports/<name>_<stamp>.json + _trades.csv):
  for tf in "M5 5" "M15 15" "H1 16385" "H4 16388"; do set -- $tf; for a in 0 1; do
    ~/.local/bin/mbt-wine-py scripts/macd_tester.py single --expert VWAP_RSI_EA --name vr_a_$1_anc$a --period $1 \\
      --set InpTimeframe=$2 --set InpAnchor=$a --model 1min_ohlc --from 2019-01-01 --to 2026-10-01 --timeout 1200
  done; done
  python3 scripts/prop_mc.py reports/vr_a_H1_anc0_*_trades.csv --bt-risk 1.0 --sims 200      # by-year table
  ~/.local/bin/mbt-wine-py scripts/walkforward.py --tag vwap_H1 --ea VWAP_RSI_EA --period H1 ... (see plan)
"""
import argparse
import collections
import json
import os
import statistics
import subprocess
import sys

import macd_sweep as M
import selection as S
import tradelog_checks as K

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
EXPERT = "VWAP_RSI_EA"
TF = {"M5": "5", "M15": "15", "H1": "16385", "H4": "16388"}
OFF = {"InpMaxSpreadPoints": "0", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
SC_WIN = ("2025-01-01", "2025-07-01")
REGIMES = {"1921": ("2019-01-01", "2022-01-01"), "2224": ("2022-01-01", "2025-01-01")}
REGIME_YEARS = {"R19-21": range(2019, 2022), "R22-24": range(2022, 2025), "R25-26": range(2025, 2027)}
GRID = {"InpRR": ["1.0", "1.5", "2.0", "2.5"], "InpSLBufferATR": ["0.0", "0.25", "0.5", "0.75"],
        "InpRSIPeriod": ["14", "21"]}


def mt5_running():
    return subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0


def free_agent_ports():
    """MT5 pins its local tester agents to 127.0.0.1:3000-3011; a dev server on 3000 makes every single run fail
    with 'tester agent authorization error'. Kill whatever listens there (user-approved on this machine)."""
    pids = subprocess.run(["lsof", "-nP", "-tiTCP:3000-3011", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
    if pids:
        subprocess.run(["kill", "-9", *pids])
        M.log(f"freed MT5 agent ports: killed pid(s) {' '.join(pids)}")
        subprocess.run(["sleep", "2"])


def run(name, sets, period="H1", mode="single", ranges=None, frm=SC_WIN[0], to=SC_WIN[1], timeout=900):
    """macd_sweep.run with one retry after killing a hung Wine terminal; singles also return the trade-log rows."""
    for attempt in (1, 2):
        free_agent_ports()
        d = M.run(mode, name, sets=sets, ranges=ranges, model="1min_ohlc", frm=frm, to=to, timeout=timeout,
                  expert=EXPERT, period=period)
        if mode == "opt" and d.get("passes"):
            return d, None
        if mode == "single" and d.get("metrics") and d.get("trade_log"):
            return d, K.load_rows(os.path.join(REPORTS, d["trade_log"]))
        # a stale tester agent from the previous run makes the next terminal fail with "agent authorization error"
        subprocess.run(["pkill", "-KILL", "-f", "metatester64.exe"])
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    sys.exit(f"[{name}] no result after 2 attempts: {d.get('error')}")


def selfcheck():
    base = dict(OFF, InpTimeframe=TF["H1"], InpRR="1.5")      # RR != 1 so a swapped SL/TP formula cannot pass by symmetry
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    a, rows = run("sc_base", base)
    m = a["metrics"]
    check("enough trades in window", m["trades"] >= 20, f"{m['trades']}")
    check("trade log rows == trades", len(rows) == int(m["trades"]), f"{len(rows)} vs {m['trades']}")
    check("take-profit on the profit side", not K.tp_side_violations(rows), f"{len(K.tp_side_violations(rows))} violations")
    bad_sl = [r for r in rows if (r["side"] == "buy" and r["sl"] >= r["open_price"]) or
              (r["side"] == "sell" and r["sl"] <= r["open_price"])]
    check("stop on the loss side", not bad_sl, f"{len(bad_sl)} violations")
    check("TP = 1.5 x stop distance", not K.rr_violations(rows, 1.5), f"{len(K.rr_violations(rows, 1.5))} violations")
    r_sl, r_tp = K.median_r(rows, "sl"), K.median_r(rows, "tp")
    check("1% sizing: median R of sl exits near -1", r_sl is not None and -1.3 <= r_sl <= -0.8, f"{r_sl}")
    check("median R of tp exits near +1.5", r_tp is not None and 1.2 <= r_tp <= 1.8, f"{r_tp}")

    def med_stop(rs):
        return statistics.median(abs(r["open_price"] - r["sl"]) for r in rs)
    _, rows_b = run("sc_buffer", dict(base, InpSLBufferATR="0.5"))
    check("ATR buffer widens the stop, RR intact", med_stop(rows_b) > med_stop(rows) and not K.rr_violations(rows_b, 1.5),
          f"median stop {med_stop(rows):.2f} -> {med_stop(rows_b):.2f}")
    c, _ = run("sc_every", dict(base, InpReentryMode="1"))
    check("fresh-only filters entries", c["metrics"]["trades"] > m["trades"], f"{m['trades']} vs {c['metrics']['trades']}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


def stage_b(tf, anchor, extra):
    base = {"InpTimeframe": TF[tf], "InpAnchor": str(anchor)}
    base.update(extra)
    per = {}
    for reg, (frm, to) in REGIMES.items():
        passes = []
        for i, (fixed, ranges) in enumerate(S.plan_launches(GRID)):
            d, _ = run(f"b_{tf}_a{anchor}_{reg}_{i:02d}", dict(base, **fixed), period=tf, mode="opt", ranges=ranges,
                       frm=frm, to=to, timeout=3000)
            for p in d.get("passes") or []:
                for k, v in fixed.items():
                    p[k] = float(v)
                passes.append(p)
        passes, failed = S.drop_failed_passes(passes)
        M.log(f"[stage-b {tf} anchor {anchor}] {reg}: {len(passes)} passes, {failed} failed OnInit")
        per[reg] = passes
    min_trades = 100 if tf == "H4" else 150
    pick = S.joint_pick(per["1921"], per["2224"], GRID, min_trades=min_trades)
    if pick:
        pa, pb = pick
        M.log(f"[stage-b {tf} anchor {anchor}] joint pick {{{', '.join(f'{k}={pa[k]}' for k in GRID)}}} "
              f"PF {pa['Profit Factor']:.2f}/{pb['Profit Factor']:.2f} trades {pa['Trades']:.0f}/{pb['Trades']:.0f} "
              f"DD {pa['Equity DD %']:.1f}/{pb['Equity DD %']:.1f}")
    else:
        M.log(f"[stage-b {tf} anchor {anchor}] joint pick: None")
    with open(os.path.join(M.OUTDIR, f"stage_b_{tf}_a{anchor}.json"), "w") as f:
        json.dump({"base": base, "grid": GRID, "passes": per, "pick": pick}, f, indent=1, default=str)


def report(paths):
    """One line per trade log: PF, net R, R per trade, win %, net R per regime (by close year), top entry hours."""
    print(f"{'log':30} {'n':>5} {'PF':>5} {'netR':>7} {'R/tr':>6} {'win%':>5} | "
          + " ".join(f"{k:>7}" for k in REGIME_YEARS) + " | top entry hours (server)")
    for path in paths:
        rows = K.load_rows(path)
        if not rows:
            print(f"{os.path.basename(path)[:30]:30} empty")
            continue
        gp = sum(r["profit"] for r in rows if r["profit"] > 0)
        gl = -sum(r["profit"] for r in rows if r["profit"] < 0)
        rs = [r["r_multiple"] or 0.0 for r in rows]
        reg = {k: sum(r["r_multiple"] or 0.0 for r in rows if r["close_time"].year in yrs) for k, yrs in REGIME_YEARS.items()}
        hours = collections.Counter(r["open_time"].hour for r in rows).most_common(3)
        name = os.path.basename(path).replace("_trades.csv", "")
        name = name[:name.rfind("_", 0, name.rfind("_"))] if name.count("_") >= 2 else name   # drop the _date_time stamp
        print(f"{name[:30]:30} {len(rows):5d} {gp / gl if gl else 0:5.2f} {sum(rs):7.1f} {sum(rs) / len(rs):6.3f} "
              f"{100 * sum(1 for r in rows if r['profit'] > 0) / len(rows):5.1f} | "
              + " ".join(f"{reg[k]:7.1f}" for k in REGIME_YEARS) + " | "
              + ", ".join(f"{h:02d}h {100 * c / len(rows):.0f}%" for h, c in hours))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-b", "report"])
    ap.add_argument("paths", nargs="*", help="trade CSVs for report")
    ap.add_argument("--tf", choices=sorted(TF), default="H1")
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--extra", action="append", default=[], help="Name=value fixed input for stage-b")
    args = ap.parse_args()
    if args.cmd == "report":
        report(args.paths)
        return
    if mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(REPORTS, "vwap_rsi")
    os.makedirs(M.OUTDIR, exist_ok=True)
    if args.cmd == "selfcheck":
        selfcheck()
    else:
        stage_b(args.tf, args.anchor, dict(e.split("=", 1) for e in args.extra))


if __name__ == "__main__":
    main()
