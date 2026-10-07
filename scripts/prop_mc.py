#!/usr/bin/env python3
"""Trade-list analysis + prop-firm Monte Carlo (roadmap Phase 1 tooling / Phase 4 risk calibration).

Reads closed trades from an MT5 Strategy Tester .htm report (Deals table) or a CSV, normalizes each
trade to R (return on balance at entry / backtest risk per trade), then reports:
  1. by-year table: trades, win %, PF, net R, max DD in R
  2. reshuffle Monte Carlo of the whole trade list -> max-drawdown percentiles per risk level
  3. challenge Monte Carlo: resample whole historical trading days and replay them under the firm's
     rules (profit target, daily loss vs. start-of-day balance, max loss) -> P(pass), P(breach),
     days to pass, per risk level and per phase

Pure numpy + stdlib: runs on the Mac's native python3, no Wine/MT5 needed.

  python3 scripts/prop_mc.py reports/val_final_2024-2026_20261002_195527.htm --bt-risk 1.0
  python3 scripts/prop_mc.py reports/val_final_2022_*.htm reports/val_final_2023_*.htm   # merged OOS years
  python3 scripts/prop_mc.py trades.csv --risk 0.25,0.5 --targets 10,5 --daily-loss 5 --max-loss 10

CSV input: a close_time (or time) column plus one of: r | return_pct | profit + balance
(balance after the close).

Limits: closed-trade P&L only. Firms measure daily loss on *equity* (floating P&L included), so real
intraday dips are deeper than simulated here -- keep the EA's own daily stop well inside the limit.
Days are bucketed by close time in the report's (server) timezone.
"""
import argparse, csv, html, json, re, sys
from collections import defaultdict
from datetime import datetime

import numpy as np


# ------------------------------------------------------------------------------------ loading

def _num(s):
    s = s.replace("\xa0", "").replace(" ", "")
    return float(s) if s else 0.0


def _dt(s):
    s = s.strip()
    if re.match(r"\d{4}\.\d{2}\.\d{2}", s):  # MT5 style 2024.01.03 06:00:00
        s = s[:10].replace(".", "-") + s[10:]
    return datetime.fromisoformat(s)


def read_mt5_report(path):
    """Closed trades from a tester report's Deals table -> [(close_dt, net, balance_before_entry)].

    Entries are matched to exits FIFO per symbol+side, so partial closes and several open positions
    work; entry commission is allocated pro rata to the exits that consume the entry's volume."""
    raw = open(path, "rb").read()
    txt = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    i = txt.rfind("<b>Deals</b>")
    if i < 0:
        sys.exit(f"{path}: no Deals table -- pass a single-run Strategy Tester report")
    open_pos = defaultdict(list)  # (symbol, side) -> FIFO of [volume_left, volume, commission, balance_before]
    trades = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", txt[i:], re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) != 13 or cells[3] not in ("buy", "sell"):
            continue  # header, balance/credit rows, totals
        t, sym, side, direction = _dt(cells[0]), cells[2], cells[3], cells[4]
        vol, comm, swap, profit, bal = (_num(cells[k]) for k in (5, 8, 9, 10, 11))
        if direction == "in":
            open_pos[(sym, side)].append([vol, vol, comm, bal - comm])
            continue
        fifo = open_pos[(sym, "sell" if side == "buy" else "buy")]
        close_vol = vol if direction == "out" else sum(e[0] for e in fifo)  # in/out = reversal
        need, entry_comm, first = close_vol, 0.0, None
        while need > 1e-9 and fifo:
            e = fifo[0]
            take = min(need, e[0])
            entry_comm += e[2] * take / e[1]
            first = first or e
            e[0] -= take
            need -= take
            if e[0] <= 1e-9:
                fifo.pop(0)
        out_comm = comm * close_vol / vol if vol else comm
        net = profit + swap + out_comm + entry_comm
        trades.append((t, net, first[3] if first else bal - net))
        if direction == "in/out" and vol - close_vol > 1e-9:
            rest = vol - close_vol
            open_pos[(sym, side)].append([rest, rest, comm - out_comm, bal])
    return trades


def read_csv(path, bt_risk):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{path}: no rows")
    key = {c.strip().lower(): c for c in rows[0]}
    tcol = key.get("close_time") or key.get("time")
    if not tcol:
        sys.exit(f"{path}: needs a close_time (or time) column")
    out = []
    for row in rows:
        if "r" in key:
            r = float(row[key["r"]])
        elif "return_pct" in key:
            r = float(row[key["return_pct"]]) / bt_risk
        elif "profit" in key and "balance" in key:
            p, b = float(row[key["profit"]]), float(row[key["balance"]])
            r = p / (b - p) * 100 / bt_risk
        else:
            sys.exit(f"{path}: needs an r, return_pct, or profit+balance column")
        out.append((_dt(row[tcol]), r))
    return out


def load_trades(paths, bt_risk):
    """Merge trades from one or more sources (e.g. separate per-year tester reports), sorted by close."""
    pairs = []
    for path in paths:
        if path.lower().endswith((".htm", ".html")):
            got = [(t, net / bal * 100 / bt_risk) for t, net, bal in read_mt5_report(path)]
        else:
            got = read_csv(path, bt_risk)
        if not got:
            sys.exit(f"{path}: no closed trades found")
        pairs += got
    pairs.sort(key=lambda p: p[0])
    return [p[0] for p in pairs], np.array([p[1] for p in pairs])


# ------------------------------------------------------------------------------------ stats

def profit_factor(r):
    loss = -r[r < 0].sum()
    return float(r[r > 0].sum() / loss) if loss else float("inf")


def max_dd_r(r):
    cum = np.concatenate([[0.0], np.cumsum(r)])
    return float((np.maximum.accumulate(cum) - cum).max())


def longest_losing_streak(r):
    best = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def group(times, R, keyfn):
    g = defaultdict(list)
    for t, r in zip(times, R):
        g[keyfn(t)].append(r)
    return {k: np.array(v) for k, v in sorted(g.items())}


def by_year(times, R):
    rows = []
    for k, r in [*group(times, R, lambda t: t.year).items(), ("all", R)]:
        rows.append({"year": k, "trades": len(r), "win_pct": 100 * float((r > 0).mean()),
                     "pf": profit_factor(r), "net_r": float(r.sum()), "avg_r": float(r.mean()),
                     "max_dd_r": max_dd_r(r)})
    return rows


# ------------------------------------------------------------------------------------ Monte Carlo

def compounded_dd(paths, f):
    eq = np.cumprod(1 + paths * f, axis=1)
    eq = np.concatenate([np.ones((len(eq), 1)), eq], axis=1)
    return (1 - eq / np.maximum.accumulate(eq, axis=1)).max(axis=1) * 100


def shuffle_dd(R, risk, n, rng, chunk=2000):
    """Max DD (%) of n random reorderings of the full trade list at `risk` % per trade."""
    out = []
    for m in range(0, n, chunk):
        k = min(chunk, n - m)
        out.append(compounded_dd(rng.permuted(np.tile(R, (k, 1)), axis=1), risk / 100))
    return np.concatenate(out)


def challenge(day_R, risk, target, daily, max_loss, min_days, horizon, trailing, n, rng):
    """Replay randomly drawn historical trading days until pass, breach, or `horizon` active days.

    Balances are in units of the initial balance. Daily breach: equity after a close falls more than
    `daily` % of initial below the day's starting balance. Max-loss floor: initial (or the highest
    closed balance with `trailing`) minus `max_loss` %. Pass: target reached on/after min_days."""
    k = max(len(d) for d in day_R)
    D, M = np.zeros((len(day_R), k)), np.zeros((len(day_R), k), bool)
    for i, d in enumerate(day_R):
        D[i, :len(d)], M[i, :len(d)] = d, True
    f = risk / 100
    eq, peak = np.ones(n), np.ones(n)
    state = np.zeros(n, np.int8)  # 0 running, 1 passed, 2 daily breach, 3 max-loss breach
    days = np.zeros(n, int)
    for day in range(1, horizon + 1):
        if not (state == 0).any():
            break
        idx = rng.integers(len(day_R), size=n)
        start = eq.copy()
        for j in range(k):
            on = (state == 0) & M[idx, j]
            eq[on] *= 1 + D[idx[on], j] * f
            state[on & (eq <= start - daily / 100)] = 2
            state[on & (state == 0) & (eq <= (peak if trailing else 1.0) - max_loss / 100)] = 3
            if trailing:
                np.maximum(peak, eq, out=peak)
            hit = on & (state == 0) & (eq >= 1 + target / 100) & (day >= min_days)
            state[hit], days[hit] = 1, day
    passed = days[state == 1]
    return {"p_pass": float((state == 1).mean()), "p_daily_breach": float((state == 2).mean()),
            "p_max_loss_breach": float((state == 3).mean()), "p_no_result": float((state == 0).mean()),
            "active_days_p50": float(np.median(passed)) if len(passed) else None,
            "active_days_p90": float(np.percentile(passed, 90)) if len(passed) else None}


# ------------------------------------------------------------------------------------ main

def _floats(s):
    return [float(x) for x in s.split(",") if x.strip()]


def _pct(x):
    return f"{100 * x:5.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="+", help="MT5 tester .htm report(s) or trades CSV(s); several are merged")
    ap.add_argument("--bt-risk", type=float, default=1.0, help="risk %% per trade used in the backtest (default 1.0)")
    ap.add_argument("--risk", type=_floats, default=[0.25, 0.5, 0.75, 1.0], help="risk %% levels to simulate")
    ap.add_argument("--targets", type=_floats, default=[10, 5], help="profit target %% per phase (FTMO 2-Step: 10,5)")
    ap.add_argument("--daily-loss", type=float, default=5.0, help="max daily loss %% of initial balance")
    ap.add_argument("--max-loss", type=float, default=10.0, help="max overall loss %% of initial balance")
    ap.add_argument("--trailing", action="store_true", help="max-loss floor trails the highest closed balance")
    ap.add_argument("--min-days", type=int, default=4, help="minimum trading days per phase")
    ap.add_argument("--horizon", type=int, default=365, help="calendar days to give each phase before 'no result'")
    ap.add_argument("--dd-cap", type=float, default=7.0, help="p95 MC drawdown %% to stay under when suggesting a risk")
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", help="also write all results to this JSON file")
    a = ap.parse_args()

    times, R = load_trades(a.source, a.bt_risk)
    rng = np.random.default_rng(a.seed)
    span_days = (times[-1].date() - times[0].date()).days + 1
    days = group(times, R, lambda t: t.date())
    months = group(times, R, lambda t: (t.year, t.month))
    day_R = list(days.values())
    day_sums = np.array([d.sum() for d in day_R])
    month_sums = np.array([m.sum() for m in months.values()])
    active_ratio = len(day_R) / span_days
    horizon = max(a.min_days, round(a.horizon * active_ratio))

    summary = {"source": a.source, "trades": len(R), "first": times[0].isoformat(), "last": times[-1].isoformat(),
               "bt_risk_pct": a.bt_risk, "expectancy_r": float(R.mean()), "pf": profit_factor(R),
               "win_pct": 100 * float((R > 0).mean()), "trades_per_month": len(R) / span_days * 30.44,
               "active_day_pct": 100 * active_ratio, "positive_month_pct": 100 * float((month_sums > 0).mean()),
               "worst_month_r": float(month_sums.min()), "worst_day_r": float(day_sums.min()),
               "longest_losing_streak": longest_losing_streak(R)}
    print(f"\n{', '.join(a.source)}\n{len(R)} trades {times[0]:%Y-%m-%d} -> {times[-1]:%Y-%m-%d}, "
          f"R = return / {a.bt_risk}% backtest risk\n"
          f"Expectancy {summary['expectancy_r']:+.3f}R/trade | PF {summary['pf']:.2f} | win {summary['win_pct']:.1f}% | "
          f"{summary['trades_per_month']:.1f} trades/month | trades on {summary['active_day_pct']:.0f}% of calendar days\n"
          f"Positive months {summary['positive_month_pct']:.0f}% | worst month {summary['worst_month_r']:+.1f}R | "
          f"worst day {summary['worst_day_r']:+.1f}R | longest losing streak {summary['longest_losing_streak']}")
    if len(R) < 200:
        print(f"WARNING: only {len(R)} trades -- too few for reliable statistics (aim for 200+).")

    years = by_year(times, R)
    print(f"\n{'year':>6} {'trades':>7} {'win%':>6} {'PF':>6} {'net R':>8} {'avg R':>7} {'maxDD R':>8}")
    for y in years:
        print(f"{y['year']!s:>6} {y['trades']:>7} {y['win_pct']:>6.1f} {y['pf']:>6.2f} {y['net_r']:>+8.1f} "
              f"{y['avg_r']:>+7.3f} {y['max_dd_r']:>8.1f}")

    print(f"\nDrawdown at each risk level ({a.sims} reshuffles of all {len(R)} trades, compounded)")
    print(f"{'risk':>6} {'hist DD':>8} {'MC p50':>7} {'MC p95':>7} {'MC p99':>7} {'worst day':>10}")
    risk_rows = []
    for risk in a.risk:
        dd = shuffle_dd(R, risk, a.sims, rng)
        row = {"risk_pct": risk, "hist_dd_pct": float(compounded_dd(R[None, :], risk / 100)[0]),
               "mc_dd_p50": float(np.percentile(dd, 50)), "mc_dd_p95": float(np.percentile(dd, 95)),
               "mc_dd_p99": float(np.percentile(dd, 99)), "worst_day_pct": float(day_sums.min() * risk),
               "phases": []}
        risk_rows.append(row)
        print(f"{risk:>5.2f}% {row['hist_dd_pct']:>7.1f}% {row['mc_dd_p50']:>6.1f}% {row['mc_dd_p95']:>6.1f}% "
              f"{row['mc_dd_p99']:>6.1f}% {row['worst_day_pct']:>+9.1f}%")

    floor = "trailing" if a.trailing else "static"
    print(f"\nChallenge simulation: daily loss {a.daily_loss}%, max loss {a.max_loss}% ({floor}), "
          f"min {a.min_days} days, {a.horizon} calendar days per phase, {a.sims} runs")
    print(f"{'risk':>6} {'phase':>8} {'pass':>7} {'daily':>7} {'maxloss':>8} {'none':>7} {'days p50':>9} {'days p90':>9}")
    for row in risk_rows:
        for p, target in enumerate(a.targets, 1):
            c = challenge(day_R, row["risk_pct"], target, a.daily_loss, a.max_loss, a.min_days, horizon,
                          a.trailing, a.sims, rng)
            for q in ("p50", "p90"):  # active trading days -> calendar days
                v = c.pop(f"active_days_{q}")
                c[f"calendar_days_{q}"] = v / active_ratio if v is not None else None
            c["target_pct"] = target
            row["phases"].append(c)
            d50, d90 = (f"{c[f'calendar_days_{q}']:.0f}" if c[f"calendar_days_{q}"] is not None else "-"
                        for q in ("p50", "p90"))
            print(f"{row['risk_pct']:>5.2f}% {f'{p}:+{target:g}%':>8} {_pct(c['p_pass']):>7} "
                  f"{_pct(c['p_daily_breach']):>7} {_pct(c['p_max_loss_breach']):>8} {_pct(c['p_no_result']):>7} "
                  f"{d50:>9} {d90:>9}")
        row["p_pass_all"] = float(np.prod([c["p_pass"] for c in row["phases"]]))
        print(f"{'':>6} {'all':>8} {_pct(row['p_pass_all']):>7}")

    ok = [r for r in risk_rows if r["mc_dd_p95"] <= a.dd_cap]
    if ok:
        best = max(ok, key=lambda r: r["risk_pct"])
        print(f"\nLargest tested risk with MC p95 drawdown <= {a.dd_cap}%: {best['risk_pct']}% per trade "
              f"-> P(pass all phases) {_pct(best['p_pass_all']).strip()}")
    else:
        print(f"\nNo tested risk keeps MC p95 drawdown <= {a.dd_cap}% -- try lower --risk values.")

    if a.json:
        with open(a.json, "w") as f:
            json.dump({"summary": summary, "by_year": years, "risk": risk_rows,
                       "rules": {"targets": a.targets, "daily_loss": a.daily_loss, "max_loss": a.max_loss,
                                 "trailing": a.trailing, "min_days": a.min_days, "horizon_days": a.horizon}},
                      f, indent=2, default=str)
        print(f"wrote {a.json}")


if __name__ == "__main__":
    main()
