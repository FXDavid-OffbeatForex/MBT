#!/usr/bin/env python3
"""Icarus_EA checks and the pre-registered Stage A (docs/superpowers/specs/2026-10-06-icarus-prereg.md).
System python; tester runs go through vwap_rsi_eval.run, one agent at a time. Quit the MT5 GUI first.

  python3 scripts/icarus_eval.py selfcheck   # 6 real-tick runs on EURUSD, Jan-Apr 2024
  python3 scripts/icarus_eval.py stage-a     # 6 FX symbols, author defaults, 1-min OHLC, to end-2024 (resumable)
  python3 scripts/icarus_eval.py report      # Gate A table from the Stage A results
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics
import sys

import holo_eval as H
import macd_sweep as M
import tradelog_checks as K
import vwap_rsi_eval as E

EXPERT = "Icarus_EA"
SC_WIN = ("2024-01-01", "2024-04-01")
OFF = {"InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
NEUTRAL = dict(OFF, InpEquityWarning="0", InpAccountRisk="1")   # no %-of-account rule fires: P&L scales with lots
DEPOSIT = 100_000.0
DD_CAP = 0.08 * DEPOSIT              # Gate A: scale the lots so the worst equity drawdown is 8% of the account ...
TARGET_PER_YEAR = 0.10 * DEPOSIT     # ... and require +10% a year in both regimes at that scale
STAGE_A = {"EURUSD": "2018-01-01", "USDJPY": "2019-01-01", "GBPUSD": "2019-01-01", "EURJPY": "2019-01-01",
           "USDCHF": "2024-01-01", "EURGBP": "2024-01-01"}
STAGE_A_TO = "2025-01-01"
REGIMES = (("R18/19-21", range(2018, 2022)), ("R22-24", range(2022, 2025)))
FIB = [1, 1, 2, 3, 5, 8]
PIP = 0.0001                          # EURUSD, self-check only
LINE = re.compile(r"ICARUS (OPEN|ADD|CLOSE|RED|GREEN|STOP_ALL)\b([^\n]*)")
KV = re.compile(r"(\w+)=(-?[\d.]+)")


# ---------------------------------------------------------------- pure helpers (tests/test_icarus_eval.py)
def baskets(rows, reason="basket_tp"):
    """Legs of one side closed in the same tick, oldest leg first: {(side, close_time): [rows]}."""
    out = collections.defaultdict(list)
    for r in rows:
        if r["exit_reason"] == reason:
            out[(r["side"], r["close_time"])].append(r)
    for legs in out.values():
        legs.sort(key=lambda r: (r["open_time"], r["volume"]))
    return dict(out)


def regime_pnl(rows, regimes=REGIMES):
    """Net money per regime by close year."""
    return {name: sum(r["profit"] for r in rows if r["close_time"].year in years) for name, years in regimes}


def regime_years(from_year, to_year, regimes=REGIMES):
    """Calendar years of each regime inside [from_year, to_year)."""
    return {name: len([y for y in years if from_year <= y < to_year]) for name, years in regimes}


def gate(net, years, max_dd):
    """Gate A: k = DD_CAP / max_dd; pass if net x k >= TARGET_PER_YEAR x years in every regime with data."""
    k = DD_CAP / max_dd if max_dd > 0 else float("inf")
    per_year = {r: (net[r] * k / years[r] if years[r] else None) for r in net}
    if any(v is None for v in per_year.values()):
        verdict = "n/a"
    else:
        verdict = "PASS" if all(v >= TARGET_PER_YEAR for v in per_year.values()) else "fail"
    return k, per_year, verdict


def events(text):
    """ICARUS log lines -> [(kind, side, {k: v})] in log order."""
    out = []
    for m in LINE.finditer(text):
        rest = m.group(2)
        side = "buy" if rest.startswith(" BUY") else "sell" if rest.startswith(" SELL") else ""
        out.append((m.group(1), side, {k: float(v) for k, v in KV.findall(rest)}))
    return out


def leg_gaps(bask, max_hold_h=None):
    """Adverse distance (pips) between consecutive legs, keyed by the leg index before the add (1 = 1st->2nd).
    max_hold_h keeps only adds made within that many hours of the previous leg (swap pulls the money trigger
    closer by about a pip a day, as in the MQ4)."""
    gaps = collections.defaultdict(list)
    for (side, _), legs in bask.items():
        for i in range(1, len(legs)):
            if max_hold_h is not None and (legs[i]["open_time"] - legs[i - 1]["open_time"]).total_seconds() > 3600 * max_hold_h:
                continue
            d = legs[i - 1]["open_price"] - legs[i]["open_price"] if side == "buy" else legs[i]["open_price"] - legs[i - 1]["open_price"]
            gaps[i].append(round(d / PIP, 2))
    return gaps


def gap_factor(n, mode):
    if n <= 1:
        return 1
    return {0: 1, 1: n, 2: 2 ** (n - 1), 3: FIB[n - 1]}[mode]


def lots_prefix_ok(legs, seq, step=0.01):
    vols = [r["volume"] for r in legs]
    return len(vols) <= len(seq) and all(abs(v - s * step) < step / 2 for v, s in zip(vols, seq))


# ---------------------------------------------------------------- tester runs
def run(name, sets, frm=SC_WIN[0], to=SC_WIN[1], model="real_ticks", symbol="EURUSD", timeout=1800):
    return E.run(name, sets, period="H1", frm=frm, to=to, timeout=timeout, expert=EXPERT, model=model, symbol=symbol)


def selfcheck():
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    runs = {}
    for key, sets in (("base", {}), ("gs0", {"InpGridProgression": "0"}), ("lots0", {"InpLotProgression": "0"}),
                      ("lots2", {"InpLotProgression": "2"}),
                      ("kill", {"InpMinLots": "1.0", "InpAccountRisk": "0.01"}),
                      ("red", {"InpMinLots": "1.0", "InpEquityWarning": "0.01"})):
        mark = H.agent_log_size()
        d, rows = run(f"isc_{key}", dict(NEUTRAL, **sets))
        runs[key] = (d, rows, events(H.agent_log_since(mark)))

    d, rows, ev = runs["base"]
    n = int(d["metrics"]["trades"])
    reasons = {r["exit_reason"] for r in rows}
    check("1 log complete, exits only by basket lock or end of test", n > 0 and len(rows) == n and reasons <= {"basket_tp", "end_of_test"},
          f"{n} trades, {len(rows)} rows, reasons {sorted(reasons)}")
    first = {s: min(r["open_time"] for r in rows if r["side"] == s) for s in ("buy", "sell")}
    check("2 buy and sell grids start in the same tick", first["buy"] == first["sell"], f"{first}")
    bask = baskets(rows)
    sizes = [len(v) for v in bask.values()]
    badlots = [k for k, v in bask.items() if not lots_prefix_ok(v, FIB)]
    check("3 Fibonacci lots 1,1,2,3,5,8 x 0.01 and at most 6 legs", bask and not badlots and max(sizes) <= 6 and max(sizes) >= 3,
          f"{len(bask)} baskets, legs max {max(sizes) if sizes else 0}, {len(badlots)} off-sequence")
    adds = [e for e in ev if e[0] == "ADD"]
    bad_trig = [e for e in adds if abs(e[2]["trigger"] + e[2]["gap"] * FIB[int(e[2]["n"]) - 2] * 0.10) > 0.011 or
                e[2]["gap"] != gap_factor(int(e[2]["n"]) - 1, 3) * 20]
    gaps = leg_gaps(bask, max_hold_h=24)
    med = {i: statistics.median(g) for i, g in gaps.items() if len(g) >= 3}
    want = {i: gap_factor(i, 3) * 20 for i in med}
    off = {i: round(med[i], 1) for i in med if abs(med[i] - want[i]) > 2.0}
    # a triple-swap rollover (-0.25 on 0.01 lot = 2.5 pips) or a news spread spike shortens single Ask-to-Ask gaps
    low = [x for i, g in gaps.items() for x in g if x < gap_factor(i, 3) * 20 - 5]
    check("4 grid triggers -gap x pip value of the last leg, gaps 20/20/40/60 pips (same-day adds: medians within 2 pips, none 5 short)",
          adds and not bad_trig and med and 3 in med and not off and not low,
          f"{len(bad_trig)} wrong triggers; medians {dict((i, round(m, 1)) for i, m in med.items())} want {want}, {len(low)} short")
    pnl = [sum(r["profit"] for r in v) for v in bask.values()]
    pos = sum(1 for p in pnl if p > 0) / len(pnl) if pnl else 0
    check("5 baskets close in profit (>= 95%), median >= lock x take profit", pnl and pos >= 0.95 and statistics.median(pnl) >= 0.3 * 20 * 0.10,
          f"{100 * pos:.0f}% positive, median {statistics.median(pnl) if pnl else 0:.2f}")
    restart = []
    for (side, ct), legs in bask.items():
        nxt = [r["open_time"] for r in rows if r["side"] == side and r["open_time"] >= ct]   # same second is common on real ticks
        restart.append(bool(nxt) and (min(nxt) - ct).total_seconds() <= 60)
    check("6 a new cycle starts right after a basket closes (>= 95%)", restart and sum(restart) / len(restart) >= 0.95,
          f"{sum(restart)}/{len(restart)}")
    opens = [e for e in ev if e[0] == "OPEN"]
    closes = [e for e in ev if e[0] == "CLOSE"]
    bad_add = [e for e in adds if not (e[2]["profit"] <= e[2]["trigger"] < 0)]
    bad_close = [e for e in closes if not (e[2]["max"] > e[2]["target"] and e[2]["total"] <= e[2]["lock"] + 0.005 and   # 2-decimal prints
                                           abs(e[2]["lock"] - 0.3 * e[2]["max"]) < 0.02)]
    check("7 log arithmetic: adds at profit <= trigger, closes with peak > target and total < lock = 0.3 x peak; counts match",
          adds and closes and not bad_add and not bad_close and len(closes) == len(bask) and len(opens) + len(adds) == len(rows),
          f"{len(opens)} opens + {len(adds)} adds vs {len(rows)} rows, {len(closes)} closes vs {len(bask)} baskets, bad {len(bad_add)}/{len(bad_close)}")

    _, rows0, _ = runs["gs0"]
    g0 = {i: statistics.median(g) for i, g in leg_gaps(baskets(rows0)).items() if len(g) >= 3}
    check("8 A/B gs_progression 0: every gap ~20 pips (default 3: 20/20/40/60)", g0 and all(abs(m - 20) <= 2.0 for m in g0.values()) and 3 in g0,
          f"medians {dict((i, round(m, 1)) for i, m in g0.items())}")
    _, rowsL0, _ = runs["lots0"]
    _, rowsL2, _ = runs["lots2"]
    flat = all(abs(r["volume"] - 0.01) < 0.005 for r in rowsL0)
    dbl = baskets(rowsL2)
    baddbl = [k for k, v in dbl.items() if not lots_prefix_ok(v, [1, 2, 4, 8, 16, 32])]
    check("9 A/B progression 0: all legs 0.01; progression 2: lots double", flat and dbl and not baddbl and max(len(v) for v in dbl.values()) >= 3,
          f"flat {flat}, {len(dbl)} martingale baskets, {len(baddbl)} off-sequence")
    _, rowsK, evK = runs["kill"]
    killed = sorted(r["close_time"] for r in rowsK if r["exit_reason"] == "account_risk")
    after = [r for r in rowsK if killed and r["open_time"] > killed[0]]
    stops = [e for e in evK if e[0] == "STOP_ALL"]
    check("10 account_risk 1%: closes everything once and never trades again", killed and not after and len(stops) == 1,
          f"{len(killed)} account_risk rows, {len(after)} opened after, {len(stops)} STOP_ALL lines")
    _, _, evR = runs["red"]
    red, viol, reds = False, 0, 0
    for kind, _, _ in evR:
        if kind == "RED":
            red, reds = True, reds + 1
        elif kind == "GREEN":
            red = False
        elif kind in ("OPEN", "ADD") and red:
            viol += 1
    check("11 red alert blocks new legs and cycles while equity is below the warning", reds > 0 and viol == 0, f"{reds} red periods, {viol} legs while red")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


def stage_a():
    out_path = os.path.join(M.OUTDIR, "stage_a.json")
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for sym, frm in STAGE_A.items():
        if sym in out:
            continue
        d, _ = run(f"ica_a_{sym}", NEUTRAL, frm=frm, to=STAGE_A_TO, model="1min_ohlc", symbol=sym, timeout=10800)
        out[sym] = {"frm": frm, "to": STAGE_A_TO, "trade_log": d["trade_log"], "metrics": d["metrics"]}
        json.dump(out, open(out_path, "w"), indent=1)
    report()


def report():
    out = json.load(open(os.path.join(M.OUTDIR, "stage_a.json")))
    print(f"{'symbol':7} {'from':5} | {'net $':>9} {'PF':>5} {'legs':>5} {'bskt':>5} {'maxL':>4} {'eot $':>8} {'maxDD $':>8} {'k':>6} | "
          f"{'R18/19-21 %/yr':>14} {'R22-24 %/yr':>12} | {'longest':>8} | gate A")
    for sym, d in out.items():
        rows = K.load_rows(os.path.join(E.REPORTS, d["trade_log"]))
        m = d["metrics"]
        years = regime_years(int(d["frm"][:4]), int(d["to"][:4]))
        net = regime_pnl(rows)
        dd = m.get("equity_dd_max") or m["equity_dd_max_pct"] * DEPOSIT / 100.0
        k, per_year, verdict = gate(net, years, dd)
        if any(r["exit_reason"] == "stop_out" for r in rows):
            verdict = "STOP-OUT"
        bask = baskets(rows)
        eot = sum(r["profit"] for r in rows if r["exit_reason"] == "end_of_test")
        legs = [len(v) for v in bask.values()] + [sum(1 for r in rows if r["exit_reason"] == "end_of_test" and r["side"] == s) for s in ("buy", "sell")]
        longest = max((max(r["close_time"] for r in v) - min(r["open_time"] for r in v) for v in bask.values()), default=dt.timedelta(0))
        pct = {r: (f"{100 * v / DEPOSIT:+.1f}" if v is not None else "  n/a") for r, v in per_year.items()}
        print(f"{sym:7} {d['frm'][:4]:5} | {m['net_profit']:+9.0f} {m['profit_factor']:5.2f} {len(rows):5d} {len(bask):5d} {max(legs):4d} {eot:+8.0f} "
              f"{dd:8.0f} {k:6.2f} | {pct['R18/19-21']:>14} {pct['R22-24']:>12} | {longest.days:6d} d | {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-a", "report"])
    args = ap.parse_args()
    if args.cmd != "report" and E.mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(E.REPORTS, "icarus")
    {"selfcheck": selfcheck, "stage-a": stage_a, "report": report}[args.cmd]()


if __name__ == "__main__":
    main()
