#!/usr/bin/env python3
"""Blessing_3 checks and the pre-registered Stage A (docs/superpowers/specs/2026-10-07-blessing-prereg.md).
System python; the EA writes no trade log, so trades are rebuilt from each report's Deals table. Quit the MT5 GUI first.

  python3 scripts/blessing_eval.py selfcheck   # 3 real-tick runs on EURUSD 2023, H4 chart
  python3 scripts/blessing_eval.py stage-a     # 6 FX symbols, author defaults, 1-min OHLC, to end-2024 (resumable)
  python3 scripts/blessing_eval.py report      # Gate A table from the Stage A results
"""
import argparse
import collections
import datetime as dt
import glob
import html
import json
import os
import re
import statistics
import subprocess
import sys

import macd_sweep as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
EXPERT, PERIOD, LEVERAGE = "Blessing_3", "H4", 30
DEPOSIT = 100_000.0
DD_CAP_PCT, TARGET_PCT_PER_YEAR = 8.0, 10.0
OFF = {"StopTradePercent": "100"}            # the permanent kill switch (10% balance loss ends the run)
SC_WIN = ("2023-01-01", "2024-01-01")
STAGE_A = {"EURUSD": "2018-01-01", "USDJPY": "2019-01-01", "GBPUSD": "2019-01-01", "EURJPY": "2019-01-01",
           "USDCHF": "2024-01-01", "EURGBP": "2024-01-01"}
STAGE_A_TO = "2025-01-01"
REGIMES = (("R18/19-21", range(2018, 2022)), ("R22-24", range(2022, 2025)))
TIME_FMT = "%Y.%m.%d %H:%M:%S"


# ---------------------------------------------------------------- pure helpers (tests/test_blessing_eval.py)
def ladder(multiplier=1.4, step=0.01, n=15):
    """Blessing's lot ladder: Lots[i] = round(max(Lots[i-1] * Multiplier, Lots[i-1] + step), 2) from 0.01."""
    out = [step]
    for _ in range(n - 1):
        out.append(round(max(out[-1] * multiplier, out[-1] + step), 2))
    return out


def _num(x):
    x = x.replace(" ", "").replace("\xa0", "")
    return float(x) if x not in ("", "-") else 0.0


def _cells(section):
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", section, flags=re.S):
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if c and re.match(r"\d{4}\.\d\d\.\d\d", c[0]):
            yield c


def deals_trades(htm_path):
    """Closed trades from an MT5 report's Deals table on a hedging account: each 'out' deal is matched to the
    oldest open leg of the opposite type with the same volume (else the oldest). Each leg carries the kind of
    order that opened it from the Orders table: 'market' (a SmartGrid add) or 'pending' (an entry stop/limit or
    a grid limit). Returns (trades, max_open)."""
    raw = open(htm_path, "rb").read()
    s = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
    i, j = s.rfind("<b>Orders</b>"), s.rfind("<b>Deals</b>")
    otype = {c[1]: c[3] for c in _cells(s[i:j]) if len(c) >= 6} if 0 <= i < j else {}
    trades, book, max_open = [], {"buy": [], "sell": []}, 0
    for c in _cells(s[j:]):
        if len(c) < 12 or c[3] not in ("buy", "sell"):
            continue
        t, typ, direction, vol, px = dt.datetime.strptime(c[0], TIME_FMT), c[3], c[4], _num(c[5]), _num(c[6])
        if direction == "in":
            book[typ].append({"open_time": t, "volume": vol, "open_price": px, "fee": _num(c[8]), "open_balance": _num(c[11]),
                              "kind": "pending" if otype.get(c[7], typ) != typ else "market"})
            max_open = max(max_open, len(book["buy"]) + len(book["sell"]))
            continue
        side = "sell" if typ == "buy" else "buy"
        if not book[side]:
            continue
        i = next((k for k, L in enumerate(book[side]) if abs(L["volume"] - vol) < 0.005), 0)
        L = book[side].pop(i)
        trades.append({"side": side, "kind": L["kind"], "open_time": L["open_time"], "close_time": t, "volume": L["volume"],
                       "open_price": L["open_price"], "close_price": px, "open_balance": L["open_balance"],
                       "profit": _num(c[8]) + _num(c[9]) + _num(c[10]) + L["fee"], "balance": _num(c[11])})
    return trades, max_open


def baskets(trades):
    """Legs of one side closed in the same second, oldest leg first: {(side, close_time): [trades]}."""
    out = collections.defaultdict(list)
    for t in trades:
        out[(t["side"], t["close_time"])].append(t)
    for legs in out.values():
        legs.sort(key=lambda t: (t["open_time"], t["volume"]))
    return dict(out)


def regime_pnl(trades, regimes=REGIMES):
    return {name: sum(t["profit"] for t in trades if t["close_time"].year in years) for name, years in regimes}


def regime_years(from_year, to_year, regimes=REGIMES):
    return {name: len([y for y in years if from_year <= y < to_year]) for name, years in regimes}


def gate(net, years, dd_pct):
    """k = 8 / maxDD%; pass if (net / deposit / years) x k >= 10%/yr in every regime with data (n/a otherwise)."""
    k = DD_CAP_PCT / dd_pct if dd_pct > 0 else float("inf")
    per_year = {r: (100.0 * net[r] / DEPOSIT / years[r] * k if years[r] else None) for r in net}
    if any(v is None for v in per_year.values()):
        verdict = "n/a"
    else:
        verdict = "PASS*" if all(v >= TARGET_PCT_PER_YEAR for v in per_year.values()) else "fail"
    return k, per_year, verdict


def grid_pips(open_legs, sets=(4, 4), grid=(25, 50, 100)):
    """GridSetArray block for the next add when open_legs are open: 25 pips for legs 2-5, 50 for 6-9, then 100."""
    n, edge = open_legs, 0
    for count, g in zip(sets, grid):
        edge += count
        if n <= edge:
            return g
    return grid[-1]


def leg_gaps(bask, pip, kind=None):
    """Blessing places the next level g2 pips beyond the LAST opened leg (OPbL / OPsL), skipping whole levels
    when price is already further (MathRound((OPbL - ASK) / g2) + 1). [(adverse distance in pips from the
    previous leg, legs open before the add, g2)] for legs after the first, optionally only those opened by
    `kind` ('market' SmartGrid adds or 'pending' grid limits)."""
    out = []
    for (side, _), legs in bask.items():
        for i in range(1, len(legs)):
            if kind and legs[i]["kind"] != kind:
                continue
            d = legs[i - 1]["open_price"] - legs[i]["open_price"] if side == "buy" else legs[i]["open_price"] - legs[i - 1]["open_price"]
            out.append((round(d / pip, 2), i, grid_pips(i)))
    return out


def ladder_violations(bask, lad=None):
    """Legs whose volume is not Lots[k] x LotMult (LotMult = first leg / 0.01): k = legs open when a market add
    is sent; an entry pending was sized with no legs open (k = 0), and both entry pendings can fill on H4."""
    lad = lad or ladder()
    bad = []
    for key, legs in bask.items():
        mult = round(legs[0]["volume"] / 0.01)
        for i, t in enumerate(legs):
            k = 0 if (t["kind"] == "pending" and i <= 1) else i
            if k >= len(lad) or abs(t["volume"] - round(lad[k] * mult, 2)) > 0.006:   # ND(Lots[k], 2) x LotMult is exact
                bad.append((key, i, t["volume"]))
    return bad


def lotmult_expected(balance, laf=0.5, level=7, multiplier=1.4):
    """Blessing MM: LotMult = floor(LAF x PortionBalance / 10000 / (1 + Factor)), Factor = (M^Level - M) / (M - 1)."""
    factor = (multiplier ** level - multiplier) / (multiplier - 1)
    return max(int(laf * balance / 10000.0 / (1.0 + factor) / 0.01), 1)


# ---------------------------------------------------------------- tester runs
def local(path):
    return re.sub(r"^[A-Za-z]:", "", path).replace("\\", "/")


def run(name, sets, frm, to, model, symbol, timeout=3600):
    for attempt in (1, 2):
        d = M.run("single", name, sets=dict(OFF, **sets), model=model, frm=frm, to=to, timeout=timeout,
                  expert=EXPERT, symbol=symbol, period=PERIOD, leverage=LEVERAGE)
        if d.get("metrics") and d.get("reports"):
            trades, max_open = deals_trades(local(d["reports"][0]))
            return d, trades, max_open
        subprocess.run(["pkill", "-KILL", "-f", "metatester64.exe"])
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    sys.exit(f"[{name}] no result after 2 attempts: {d.get('error')}")


def selfcheck():
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    pip = 0.0001
    d, tr, mx = run("bsc_base", {}, SC_WIN[0], SC_WIN[1], "real_ticks", "EURUSD")
    n = int(d["metrics"]["trades"])
    check("1 trades rebuilt from the Deals table == report trades", n >= 20 and len(tr) == n, f"{n} trades, {len(tr)} rebuilt, max open {mx}")
    bask = baskets(tr)
    multi = {s: sum(1 for (side, _), v in bask.items() if side == s and len(v) >= 2) for s in ("buy", "sell")}
    check("2 SmartGrid adds levels on both sides (shim fix)", multi["buy"] >= 1 and multi["sell"] >= 1 and mx >= 3,
          f"multi-leg baskets buy {multi['buy']} sell {multi['sell']}, max open {mx}")
    mm = [(round(v[0]["volume"] / 0.01), lotmult_expected(v[0]["open_balance"])) for v in bask.values()]
    offmm = [x for x in mm if abs(x[0] - x[1]) > 1]
    bad = ladder_violations(bask)
    check("3 balance-based lots: first leg = floor(LAF x balance / 10000 / 23.85) x 0.01 (0.20 at 100k), ladder x1,2,3,4,6,8,11,...",
          mm and not offmm and not bad, f"LotMult {min(x[0] for x in mm)}-{max(x[0] for x in mm)} vs expected, {len(offmm)} off, {len(bad)} off-ladder legs")
    gaps = leg_gaps(bask, pip, kind="market")
    short = [g for g in gaps if g[0] < g[2] - 1]
    early = [g[0] for g in gaps if g[1] <= 4]
    check("4 SmartGrid adds 25/50/100 pips beyond the last leg (first H4 open past the line: never short, median < 60 for levels 2-5)",
          gaps and not short and early and statistics.median(early) < 60, f"{len(gaps)} adds, {len(short)} short, median {statistics.median(early) if early else 0:.1f}")
    adds = [t for v in bask.values() for t in v[1:] if t["kind"] == "market"]
    pend = [t for v in bask.values() for t in v[1:] if t["kind"] == "pending"]
    offbar = [t for t in adds if t["open_time"].minute != 0 or t["open_time"].hour % 4 != 0]
    check("5 once per bar: every market add opens in the first minute of an H4 bar; pending entries fill intra-bar", adds and not offbar and pend,
          f"{len(adds)} market adds, {len(offbar)} off-bar; {len(pend)} second-entry pending fills")
    end = max(t["close_time"] for t in tr)
    closed = {k: v for k, v in bask.items() if k[1] < end - dt.timedelta(hours=1)}
    pos = sum(1 for v in closed.values() if sum(t["profit"] for t in v) > 0)
    check("6 baskets close as a whole at the virtual take profit (>= 90% positive)", closed and pos / len(closed) >= 0.9, f"{pos}/{len(closed)} positive")

    _, tr2, _ = run("bsc_nomm", {"UseMM": "false"}, SC_WIN[0], SC_WIN[1], "real_ticks", "EURUSD")
    b2 = baskets(tr2)
    check("7 A/B UseMM=false: every first leg is Lot = 0.01 and the ladder follows", b2 and all(abs(v[0]["volume"] - 0.01) < 0.005 for v in b2.values()) and not ladder_violations(b2),
          f"{len(b2)} baskets")
    _, tr3, _ = run("bsc_nosmart", {"UseSmartGrid": "false"}, SC_WIN[0], SC_WIN[1], "real_ticks", "EURUSD")
    g3 = [g for g in leg_gaps(baskets(tr3), pip, kind="pending") if g[1] >= 2]   # leg 2 may be the other entry pending
    exact = sum(1 for d, _, g2 in g3 if d >= g2 - 1.0 and abs(d / g2 - round(d / g2)) * g2 <= 1.0)   # whole levels, skips allowed
    check("8 A/B UseSmartGrid=false: grid limits fill a whole number of 25/50/100-pip levels beyond the last leg (>= 90% within 1 pip)",
          g3 and exact / len(g3) >= 0.9, f"{exact}/{len(g3)} exact, median {statistics.median(g[0] for g in g3) if g3 else 0:.1f}")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


def stage_a():
    out_path = os.path.join(M.OUTDIR, "stage_a.json")
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for sym, frm in STAGE_A.items():
        if sym in out:
            continue
        d, _, max_open = run(f"bls_a_{sym}", {}, frm, STAGE_A_TO, "1min_ohlc", sym)
        out[sym] = {"frm": frm, "to": STAGE_A_TO, "htm": local(d["reports"][0]), "metrics": d["metrics"], "max_open": max_open}
        json.dump(out, open(out_path, "w"), indent=1)
    report()


def report():
    out = json.load(open(os.path.join(M.OUTDIR, "stage_a.json")))
    print(f"{'symbol':7} {'from':5} | {'net $':>9} {'PF':>5} {'legs':>5} {'bskt':>5} {'maxL':>4} {'eot $':>8} {'maxDD %':>7} {'k':>6} | "
          f"{'R18/19-21 %/yr':>14} {'R22-24 %/yr':>12} | {'longest':>8} | gate A")
    for sym, d in out.items():
        tr, _ = deals_trades(d["htm"])
        m = d["metrics"]
        years = regime_years(int(d["frm"][:4]), int(d["to"][:4]))
        k, per_year, verdict = gate(regime_pnl(tr), years, m["equity_dd_max_pct"])
        bask = baskets(tr)
        end = max(t["close_time"] for t in tr) if tr else None
        eot = sum(t["profit"] for t in tr if end and t["close_time"] >= end - dt.timedelta(hours=1))
        longest = max((max(t["close_time"] for t in v) - min(t["open_time"] for t in v) for v in bask.values()), default=dt.timedelta(0))
        pct = {r: (f"{v:+.1f}" if v is not None else "  n/a") for r, v in per_year.items()}
        print(f"{sym:7} {d['frm'][:4]:5} | {m['net_profit']:+9.0f} {m['profit_factor']:5.2f} {len(tr):5d} {len(bask):5d} {d['max_open']:4d} {eot:+8.0f} "
              f"{m['equity_dd_max_pct']:7.1f} {k:6.2f} | {pct['R18/19-21']:>14} {pct['R22-24']:>12} | {longest.days:6d} d | {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selfcheck", "stage-a", "report"])
    args = ap.parse_args()
    if args.cmd != "report" and subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0:
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(REPORTS, "blessing")
    {"selfcheck": selfcheck, "stage-a": stage_a, "report": report}[args.cmd]()


if __name__ == "__main__":
    main()
