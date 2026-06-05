"""
Test the Kaufman Efficiency Ratio filter on top of ADX>35, honest train/test.

ER over N bars = |close[t] - close[t-N]| / sum(|close[i]-close[i-1]|).
~1 = clean trend, ~0 = chop. Cutoff derived from TRAIN only, applied to TEST.
ER is computed from price (signals unchanged), matched to each signal's bar by
price (robust to the server/UTC time offset).
"""
import csv
from datetime import datetime
import MetaTrader5 as mt5
from core.connection import load_config, signal_file_path
from core.signals import load_signals
from core.backtest import run_backtest, report_to_dict

ER_PERIOD = 20
ADX_MIN   = 35.0
SYMBOL    = "GOLD"

cfg = load_config()
mt5.initialize(path=cfg["mt5_path"])
mt5.symbol_select(SYMBOL, True)
rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 60000)
closes = [float(r["close"]) for r in rates]
mt5.shutdown()

# map close (rounded to cent) -> list of indices, to locate each signal's bar
from collections import defaultdict
idx_by_close = defaultdict(list)
for i, c in enumerate(closes):
    idx_by_close[round(c, 2)].append(i)

def efficiency_at(entry_price):
    """ER over the ER_PERIOD bars ending at the bar whose close == entry."""
    cands = idx_by_close.get(round(entry_price, 2), [])
    if not cands:
        return None
    idx = cands[len(cands)//2]  # any matching bar; ER is local, exact bar ~equivalent
    if idx < ER_PERIOD:
        return None
    net = abs(closes[idx] - closes[idx - ER_PERIOD])
    path = sum(abs(closes[j] - closes[j-1]) for j in range(idx - ER_PERIOD + 1, idx + 1))
    return (net / path) if path > 0 else 0.0

# adx + er per signal time
COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
adx_by_time, er_by_time = {}, {}
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try: dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError: continue
        adx_by_time[dt] = float(d["adx"])
        er_by_time[dt]  = efficiency_at(float(d["entry"]))

signals = sorted(load_signals(), key=lambda s: s.time)
split = int(len(signals) * 0.70)
train, test = signals[:split], signals[split:]

# ER cutoff from TRAIN signals that pass ADX>35 (median = above-typical efficiency)
train_ers = sorted(er_by_time[s.time] for s in train
                   if adx_by_time.get(s.time,0) > ADX_MIN and er_by_time.get(s.time) is not None)
cutoff = train_ers[len(train_ers)//2]
print(f"ER period={ER_PERIOD}, ADX>{ADX_MIN:.0f}; ER cutoff (train median) = {cutoff:.3f}\n")

def show(label, sigs):
    if not sigs:
        print(f"{label:<34} (none)"); return
    d = report_to_dict(run_backtest(signals=sigs))
    print(f"{label:<34} trades={d['trades_taken']:>4} WR={d['win_rate_pct']:>5}% "
          f"PF={d['profit_factor']:>5} net={d['net_r']:>7}R maxDD={d['max_drawdown_r']:>6}R")

for label, subset in [("TRAIN", train), ("TEST", test)]:
    print(f"=== {label} ===")
    adx = [s for s in subset if adx_by_time.get(s.time,0) > ADX_MIN]
    adx_er = [s for s in adx if er_by_time.get(s.time) is not None and er_by_time[s.time] > cutoff]
    show("  ADX>35 (current best)", adx)
    show("  ADX>35 + ER>cutoff", adx_er)
    print()
