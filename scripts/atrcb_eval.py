#!/usr/bin/env python3
"""ATR Candle Breakout EA on XAUUSD H1 with MACD_Cross_EA v1.42's test options; Darwinex Zero calibration fit.

Options as for MACD: real ticks, 100,000 USD, 1:20, years 2022..2026 + 2022-26, risk = 1% of the deposit
(this EA only supports a fixed money risk: InpRiskAmount = 1000, so no compounding).
Configs: A = the EA's saved settings; B = MACD-aligned where an equivalent input exists
(SL 0.5%, TP 3.75%, H4 EMA200 trend filter, trailing from +1%).
The EA writes no trade log, so trades are rebuilt from each MT5 report's Deals table.

  python3 scripts/atrcb_eval.py run      # MT5 runs (quit the MT5 GUI first)
  python3 scripts/atrcb_eval.py report   # analysis only
"""
import glob
import html
import json
import os
import re
import statistics
import subprocess
import sys

import combine_eas as C
import macd_sweep as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
OUT = os.path.join(REPORTS, "atrcb_eval")
EXPERT = "ATR Candle Breakout EA"
RISK = {"InpRiskAmount": "1000.0"}
CONFIGS = {
    "A_saved": dict(RISK),
    "B_macd_aligned": dict(RISK, InpSLPercent="0.5", InpTPPercent="3.75", InpUseTrendFilter="true",
                           InpTrendTF="16388", InpTrendMAPeriod="200", InpTrendMAMethod="1",
                           InpUseTrailing="true", InpTrailStartPct="1.0", InpTrailStepPct="1.0"),
}
WINDOWS = {"2022": ("2022-01-01", "2023-01-01"), "2023": ("2023-01-01", "2024-01-01"),
           "2024": ("2024-01-01", "2025-01-01"), "2025": ("2025-01-01", "2026-01-01"),
           "2026": ("2026-01-01", "2026-10-01"), "2022-2026": ("2022-01-01", "2026-10-01")}


def num(x):
    x = x.replace(" ", "").replace(" ", "")
    return float(x) if x not in ("", "-") else 0.0


def report_trades(htm_path):
    """Closed trades rebuilt from an MT5 tester report's Deals table.

    Hedging account and this EA can hold several positions at once (both directions), so a closing deal
    (direction 'out', type buy/sell) is matched FIFO to the oldest open position of the opposite type.
    Also returns the maximum number of simultaneously open positions.
    """
    raw = open(htm_path, "rb").read()
    s = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
    sec = s[s.find(">Deals<"):]
    trades, book, max_open = [], {"buy": [], "sell": []}, 0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", sec, flags=re.S):
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if len(c) < 12 or not re.match(r"\d{4}\.\d\d\.\d\d", c[0]):
            continue
        typ, direction = c[3].lower(), c[4].lower()
        if direction == "in" and typ in book:
            book[typ].append({"t": c[0], "vol": num(c[5]), "px": num(c[6]), "fee": num(c[8])})
            max_open = max(max_open, len(book["buy"]) + len(book["sell"]))
        elif "out" in direction and typ in book:
            side = "sell" if typ == "buy" else "buy"
            if not book[side]:
                continue
            op = book[side].pop(0)
            profit = num(c[8]) + num(c[9]) + num(c[10]) + op["fee"]
            trades.append(C.Trade(open_time=C.dt.datetime.strptime(op["t"], C.TIME_FMT),
                                  close_time=C.dt.datetime.strptime(c[0], C.TIME_FMT), symbol=c[2],
                                  volume=op["vol"], open_price=op["px"], profit=profit,
                                  balance_after=num(c[11])))
    return trades, max_open


def run():
    if subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0:
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    os.makedirs(OUT, exist_ok=True)
    M.OUTDIR = OUT
    for cfg, sets in CONFIGS.items():
        for label, (a, b) in WINDOWS.items():
            for attempt in (1, 2):
                d = M.run("single", f"atrcb_{cfg}_{label}", sets=sets, model="real_ticks", frm=a, to=b,
                          timeout=900, expert=EXPERT)
                if d.get("metrics"):
                    break
                subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
                subprocess.run(["sleep", "5"])


def latest(name):
    files = sorted(glob.glob(os.path.join(OUT, name + ".json")))
    return json.load(open(files[-1])) if files else None


def report():
    macd_log = sorted(glob.glob(os.path.join(REPORTS, "b_macd_2022-2026_*_trades.csv")))[-1]
    macd = C.load_trades(macd_log)
    out = {"macd_alone": C.summarize(macd)}
    for cfg in CONFIGS:
        rows = {}
        for label in WINDOWS:
            d = latest(f"atrcb_{cfg}_{label}")
            m = (d or {}).get("metrics") or {}
            rows[label] = {k: m.get(k) for k in ("net_profit", "profit_factor", "trades", "win_rate_pct",
                                                 "equity_dd_rel_pct", "recovery_factor")}
            if label == "2022-2026" and d and d.get("reports"):
                htm = os.path.join(REPORTS, d["reports"][0].replace("\\", "/").split("/")[-1])
                tr, max_open = report_trades(htm)
                calib = C.calibration_days(tr)
                rows["_analysis"] = {
                    "trades_rebuilt": len(tr), "net_rebuilt": round(sum(t.profit for t in tr), 2),
                    "max_open_positions": max_open,
                    "calib_median_days": statistics.median(calib) if calib else None,
                    "calib_range": [min(calib), max(calib)] if calib else None,
                    "monthly_corr_with_macd": C.monthly_correlation(macd, tr),
                    "alone": C.summarize(tr),
                    "with_macd": C.summarize(macd, tr),
                    "median_hold_hours": statistics.median((t.close_time - t.open_time).total_seconds() / 3600
                                                           for t in tr) if tr else None,
                }
        out[cfg] = rows
    json.dump(out, open(os.path.join(OUT, "summary.json"), "w"), indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    {"run": run, "report": report}[sys.argv[1]]()
