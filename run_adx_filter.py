"""
Validate the ADX>35 (strong-trend-only) filter, train/test honest split.
Compares baseline vs filtered on BOTH halves. The filter is a fixed structural
rule (only trade strong trends), not a tuned number.
"""
import csv
from datetime import datetime
from core.connection import signal_file_path
from core.signals import load_signals
from core.backtest import run_backtest, report_to_dict

ADX_MIN = 35.0

# adx by signal time
COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
adx_by_time = {}
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try: dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError: continue
        adx_by_time[dt] = float(d["adx"])

signals = sorted(load_signals(), key=lambda s: s.time)
split   = int(len(signals) * 0.70)
train, test = signals[:split], signals[split:]
print(f"train: {train[0].time.date()} .. {train[-1].time.date()}  ({len(train)} signals)")
print(f"test : {test[0].time.date()} .. {test[-1].time.date()}  ({len(test)} signals)\n")

def show(label, sigs):
    if not sigs:
        print(f"{label:<26} (no signals)"); return
    rep = run_backtest(signals=sigs)
    d = report_to_dict(rep)
    print(f"{label:<26} trades={d['trades_taken']:>4}  WR={d['win_rate_pct']:>5}%  "
          f"PF={d['profit_factor']:>5}  net={d['net_r']:>7}R  "
          f"maxDD={d['max_drawdown_r']:>6}R  ann={d['annual_return_pct']:>5}%")

for label, subset in [("TRAIN", train), ("TEST", test)]:
    print(f"=== {label} ===")
    show(f"  baseline (all)", subset)
    show(f"  ADX>{ADX_MIN:.0f} only", [s for s in subset if adx_by_time.get(s.time, 0) > ADX_MIN])
    print()
