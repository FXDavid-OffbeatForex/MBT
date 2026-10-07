"""Offline exit-rule evaluation from an EA excursion log (HoLo_EA InpExcursionLog). Pure functions.

The excursion run trades with no take-profit and no break-even, so each trade ends at its initial stop or at the
session flat. Per level x (in R) the log says whether price reached x before that exit (hit), whether it then came
back to the entry (back) and the best R it reached after x before coming back (run). That is enough to replay
"take profit at x" and "half off at x, stop to break-even, rest to a second target or the session flat" exactly,
for the same set of trades (an earlier exit could free the slot for an extra trade; check finalists in the tester).
"""
import csv
import datetime as dt

TIME_FMT = "%Y.%m.%d %H:%M:%S"


def load(exc_path, log_path):
    log = {r["position"]: r for r in csv.DictReader(open(log_path, newline=""))}
    out = []
    for e in csv.DictReader(open(exc_path, newline="")):
        lg = log.get(e["position"])
        if lg is None:
            continue
        risk = float(e["risk_money"])
        t = {"side": e["side"], "open_time": dt.datetime.strptime(lg["open_time"], TIME_FMT),
             "close_r": float(lg["gross"]) / risk,
             "cost_r": (float(lg["commission"]) + float(lg["swap"])) / risk,
             "mfe_r": float(e["mfe_r"]), "risk_price": float(e["risk_price"]),
             "reached": {}, "returned": {}, "run": {}}
        for k in ("trend_h4", "trend_h12", "aoi_atr", "atr_trig"):          # enriched log (improvement screen)
            if k in e:
                t[k] = float(e[k])
        if "path" in e:
            t["path"] = [tuple(float(v) for v in pt.split(":")) for pt in e["path"].split("|") if pt]
        for k in e:
            if k.startswith("hit_"):
                x = float(k[4:])
                t["reached"][x] = e[k] == "1"
                t["returned"][x] = e["back_" + k[4:]] == "1"
                t["run"][x] = float(e["run_" + k[4:]])
        out.append(t)
    return out


def variant_r(t, mode, x, y=None):
    """Net R of one trade under an exit rule: mode 'tp' (all off at x) or 'partial' (half at x, stop to entry,
    rest at y, or at the session flat when y is None)."""
    if not t["reached"][x]:
        return t["close_r"] + t["cost_r"]
    if mode == "tp":
        return x + t["cost_r"]
    if y is not None and t["run"][x] >= y:
        rest = y
    else:
        rest = 0.0 if t["returned"][x] else t["close_r"]
    return 0.5 * x + 0.5 * rest + t["cost_r"]


def summary(rs):
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    return {"n": len(rs), "net_r": sum(rs), "r_per_trade": sum(rs) / len(rs) if rs else 0.0,
            "pf": gain / loss if loss else float("inf"), "win_pct": 100.0 * sum(1 for r in rs if r > 0) / len(rs) if rs else 0.0}


def mae_before(path, f):
    """Worst adverse price move before the favourable move first reached f. path: [(mfe so far, mae so far)]
    logged each time the adverse excursion grew, so an event with mfe < f happened before f was reached."""
    return max((a for m, a in path if m < f), default=0.0)


def max_mae(path):
    return max((a for _, a in path), default=0.0)


def stop_target_r(t, s, x, slip_price=0.0):
    """Net R with a stop s (price distance, at most the logged stop) and a target of x R of that stop. Trades that
    reach neither exit at the session flat. Lots scale with 1/s, so the cost in R scales with d/s. Stop-outs keep
    the real fill at the original stop; a tighter stop is charged slip_price (average price slippage beyond a stop)."""
    d = t["risk_price"]
    cost = t["cost_r"] * d / s
    target = x * s
    if t["mfe_r"] * d >= target - 1e-9 and mae_before(t["path"], target) < s:
        return x + cost
    # the exit tick itself is never seen by the EA (the position is already closed), so the exit price is the last
    # adverse point: a trade stopped at its original stop went at least d against the entry
    if max(max_mae(t["path"]), -t["close_r"] * d) >= s:
        if s >= d - 1e-9 and t["close_r"] < -0.9:
            return t["close_r"] + cost
        return -1.0 - slip_price / s + cost
    return t["close_r"] * d / s + cost


def terciles(values):
    v = sorted(values)
    return v[len(v) // 3], v[2 * len(v) // 3]


def stop_slippage(trades):
    """Average price distance stop-outs filled beyond the stop (positive = worse), from trades stopped at their stop."""
    slips = [max(0.0, -(t["close_r"] + 1.0) * t["risk_price"]) for t in trades if t["close_r"] < -0.9]
    return sum(slips) / len(slips) if slips else 0.0
