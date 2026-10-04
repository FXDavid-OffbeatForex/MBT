"""Behaviour checks on an EA closed-trade CSV (EACore.mqh / RiskGuard.mqh columns). Pure functions."""
import csv
import datetime as dt
import statistics

TIME_FMT = "%Y.%m.%d %H:%M:%S"
_FLOATS = ("volume", "open_price", "close_price", "sl", "tp", "profit", "balance")


def load_rows(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = {"open_time": dt.datetime.strptime(r["open_time"], TIME_FMT),
                 "close_time": dt.datetime.strptime(r["close_time"], TIME_FMT),
                 "side": r["side"], "exit_reason": r["exit_reason"]}
            for k in _FLOATS:
                d[k] = float(r[k])
            d["r_multiple"] = float(r["r_multiple"]) if r["r_multiple"] else None
            d["spread_pts"] = int(r["spread_pts"]) if r["spread_pts"] else None
            rows.append(d)
    return rows


def trading_hours(open_t, close_t):
    hours = (close_t - open_t).total_seconds() / 3600.0
    saturdays = sum(1 for i in range((close_t.date() - open_t.date()).days + 1)
                    if (open_t.date() + dt.timedelta(days=i)).weekday() == 5)
    return hours - 48.0 * saturdays


def tp_side_violations(rows):
    bad = []
    for r in rows:
        if r["tp"] <= 0:
            continue
        if (r["side"] == "buy" and r["tp"] <= r["open_price"]) or (r["side"] == "sell" and r["tp"] >= r["open_price"]):
            bad.append(r)
    return bad


def time_limit_violations(rows, max_bars, slack=2.5):
    """time_limit exits must hold between max_bars - 1 and max_bars + slack trading hours."""
    bad = []
    for r in rows:
        if r["exit_reason"] != "time_limit":
            continue
        h = trading_hours(r["open_time"], r["close_time"])
        if h < max_bars - 1 or h > max_bars + slack:
            bad.append(r)
    return bad


def monthly_stop_check(rows, pct):
    """After closed balance falls pct below the month-start balance, no trade may open later that month."""
    rows = sorted(rows, key=lambda r: r["close_time"])
    breaches, bad = 0, []
    month, start_bal, breach_at = None, None, None
    for r in rows:
        key = r["close_time"].strftime("%Y-%m")
        if key != month:
            month, start_bal, breach_at = key, r["balance"] - r["profit"], None
        if breach_at is not None and r["open_time"] > breach_at and r["open_time"].strftime("%Y-%m") == month:
            bad.append(r)
        if breach_at is None and r["balance"] <= start_bal * (1.0 - pct / 100.0):
            breach_at = r["close_time"]
            breaches += 1
    return breaches, bad


def count_reason(rows, reason):
    return sum(1 for r in rows if r["exit_reason"] == reason)


def max_spread(rows):
    vals = [r["spread_pts"] for r in rows if r["spread_pts"] is not None]
    return max(vals) if vals else 0


def median_sl_r(rows):
    vals = [r["r_multiple"] for r in rows if r["exit_reason"] == "sl" and r["r_multiple"] is not None]
    return statistics.median(vals) if vals else None
