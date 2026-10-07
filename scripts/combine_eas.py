#!/usr/bin/env python3
"""Combine EA closed-trade CSV logs (RiskGuard.mqh / EACore.mqh columns) into one account view.

Each EA was backtested on its own 100k balance. A trade's effect is its return on the balance before
it closed. Merging those returns by close time on one shared balance models equal-weight risk (each
EA keeps risking the same % of the shared balance). Floating P&L, shared-guard interaction and
cross-EA compounding of position size are ignored -- the spec documents this approximation.

  python3 scripts/combine_eas.py reports/macd_trades.csv reports/meanrev_trades.csv
"""
import csv
import datetime as dt
import json
import math
import statistics
import sys
from dataclasses import dataclass

TIME_FMT = "%Y.%m.%d %H:%M:%S"
CONTRACT = {"XAUUSD": 100.0}
CALIB_DECISIONS = 25
CALIB_MIN_DAYS = 15


@dataclass(frozen=True)
class Trade:
    open_time: dt.datetime
    close_time: dt.datetime
    symbol: str
    volume: float
    open_price: float
    profit: float
    balance_after: float

    @property
    def balance_before(self):
        return self.balance_after - self.profit

    @property
    def ret(self):
        return self.profit / self.balance_before if self.balance_before > 0 else 0.0


def load_trades(path):
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Trade(open_time=dt.datetime.strptime(row["open_time"], TIME_FMT),
                             close_time=dt.datetime.strptime(row["close_time"], TIME_FMT),
                             symbol=row["symbol"], volume=float(row["volume"]),
                             open_price=float(row["open_price"]), profit=float(row["profit"]),
                             balance_after=float(row["balance"])))
    return out


def merged_returns(*lists):
    rets = [(t.close_time, t.ret) for lst in lists for t in lst]
    return sorted(rets, key=lambda x: x[0])


def equity_curve(rets):
    eq, curve = 1.0, []
    for when, r in rets:
        eq *= 1.0 + r
        curve.append((when, eq))
    return curve


def max_drawdown_pct(curve):
    peak, worst = 1.0, 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        worst = max(worst, 1.0 - eq / peak)
    return 100.0 * worst


def period_returns(rets, fmt):
    acc = {}
    for when, r in rets:
        key = when.strftime(fmt)
        acc[key] = acc.get(key, 1.0) * (1.0 + r)
    return {k: 100.0 * (v - 1.0) for k, v in acc.items()}


def monthly_correlation(a, b):
    ma = period_returns(merged_returns(a), "%Y-%m")
    mb = period_returns(merged_returns(b), "%Y-%m")
    months = sorted(set(ma) | set(mb))
    xa = [ma.get(m, 0.0) for m in months]
    xb = [mb.get(m, 0.0) for m in months]
    if len(months) < 2 or statistics.pstdev(xa) == 0.0 or statistics.pstdev(xb) == 0.0:
        return 0.0
    return statistics.correlation(xa, xb)


def _exposure(t, contract):
    hours = max((t.close_time - t.open_time).total_seconds() / 3600.0, 0.1)
    lev = t.volume * contract.get(t.symbol, 1.0) * t.open_price / max(t.balance_before, 1.0)
    return lev * math.sqrt(hours)


def calibration_days(trades, contract=CONTRACT):
    """Days from each month start until 25 risk-equivalent decisions over >= 15 trading days.

    Darwinex weighting: exposure = leverage x sqrt(hours held); a trade counts
    sqrt(exposure / largest exposure so far). Months that never finish are omitted.
    """
    trades = sorted(trades, key=lambda t: t.open_time)
    starts = sorted({dt.datetime(t.open_time.year, t.open_time.month, 1) for t in trades})
    out = []
    for start in starts:
        sub = [t for t in trades if t.open_time >= start]
        exps, days, mx = [], set(), 0.0
        for t in sub:
            e = _exposure(t, contract)
            exps.append(e)
            mx = max(mx, e)
            days.update({t.open_time.date(), t.close_time.date()})
            decisions = sum(math.sqrt(x / mx) for x in exps) if mx > 0 else 0.0
            if decisions >= CALIB_DECISIONS and len(days) >= CALIB_MIN_DAYS:
                out.append((t.close_time - start).days)
                break
    return out


def summarize(*lists):
    rets = merged_returns(*lists)
    curve = equity_curve(rets)
    net = 100.0 * (curve[-1][1] - 1.0) if curve else 0.0
    dd = max_drawdown_pct(curve)
    days = period_returns(rets, "%Y-%m-%d")
    months = period_returns(rets, "%Y-%m")
    calib = calibration_days([t for lst in lists for t in lst])
    return {"net_return_pct": net, "max_dd_pct": dd, "ret_dd": (net / dd) if dd > 0 else float("inf"),
            "worst_day_pct": min(days.values()) if days else 0.0,
            "worst_month_pct": min(months.values()) if months else 0.0,
            "calib_median_days": statistics.median(calib) if calib else None,
            "trades": len(rets)}


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: combine_eas.py <ea_a_trades.csv> <ea_b_trades.csv>")
    a, b = load_trades(argv[1]), load_trades(argv[2])
    out = {"a_alone": summarize(a), "b_alone": summarize(b), "combined": summarize(a, b),
           "monthly_correlation": monthly_correlation(a, b)}
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv)
