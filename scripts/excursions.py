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
             "mfe_r": float(e["mfe_r"]), "reached": {}, "returned": {}, "run": {}}
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
