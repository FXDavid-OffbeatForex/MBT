"""
Per-year performance breakdown — is the edge broad, or just the 2024-26 trend?
Uses the baseline backtest (each signal's logged 2.5/3.5 exits) with costs,
one-trade-at-a-time, grouped by calendar year.
"""
from collections import defaultdict
from core.backtest import run_backtest

rep = run_backtest()
by_year = defaultdict(list)
for t in rep.trades:
    if t.outcome in ("WIN", "LOSS"):
        by_year[t.time.year].append(t)

print(f"{rep.symbol} {rep.timeframe} — baseline 2.5/3.5, costs in, per year\n")
print(f"{'year':>6}{'trades':>8}{'WR%':>7}{'PF':>7}{'net_R':>9}{'equity_R':>10}")
print("-" * 47)
cum = 0.0
for y in sorted(by_year):
    ts = by_year[y]
    wins = [t for t in ts if t.outcome == "WIN"]
    gw = sum(t.r for t in wins)
    gl = abs(sum(t.r for t in ts if t.outcome == "LOSS"))
    net = sum(t.r for t in ts)
    cum += net
    pf = (gw / gl) if gl > 0 else float("inf")
    wr = len(wins) / len(ts) * 100 if ts else 0
    print(f"{y:>6}{len(ts):>8}{wr:>7.1f}{pf:>7.2f}{net:>9.1f}{cum:>10.1f}")

pos = sum(1 for y in by_year if sum(t.r for t in by_year[y]) > 0)
print(f"\nprofitable years: {pos}/{len(by_year)}")
