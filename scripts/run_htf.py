"""
Higher-timeframe trend-alignment filter on top of ADX>35, honest train/test.

Rule: take a signal only if its direction agrees with the DAILY trend
(daily close vs daily SMA50), using only completed daily bars strictly BEFORE
the signal's day (no look-ahead). SMA period fixed at 50 (not tuned).
"""
import csv
from datetime import datetime
import MetaTrader5 as mt5
from core.connection import load_config, signal_file_path
from core.signals import load_signals
from core.backtest import run_backtest, report_to_dict

ADX_MIN = 35.0
SMA_PER = 50
SYMBOL  = "GOLD"

cfg = load_config()
mt5.initialize(path=cfg["mt5_path"])
mt5.symbol_select(SYMBOL, True)
d1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_D1, 0, 4000)
mt5.shutdown()

# daily date -> trend_up (close > SMA50), using that day's own close/SMA
ddates, dclose = [], []
for r in d1:
    ddates.append(datetime.fromtimestamp(int(r["time"])).date())
    dclose.append(float(r["close"]))

trend_up_by_date = {}
for i in range(len(dclose)):
    if i < SMA_PER: continue
    sma = sum(dclose[i-SMA_PER+1:i+1]) / SMA_PER
    trend_up_by_date[ddates[i]] = dclose[i] > sma

sorted_dates = sorted(trend_up_by_date)

def htf_up_before(sig_date):
    """Daily trend from the last completed day strictly before sig_date."""
    import bisect
    j = bisect.bisect_left(sorted_dates, sig_date) - 1
    if j < 0: return None
    return trend_up_by_date[sorted_dates[j]]

# adx per signal
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
split = int(len(signals) * 0.70)
train, test = signals[:split], signals[split:]

def aligned(s):
    up = htf_up_before(s.time.date())
    if up is None: return False
    return (s.direction == "LONG" and up) or (s.direction == "SHORT" and not up)

def show(label, sigs):
    if not sigs:
        print(f"{label:<34} (none)"); return
    d = report_to_dict(run_backtest(signals=sigs))
    print(f"{label:<34} trades={d['trades_taken']:>4} WR={d['win_rate_pct']:>5}% "
          f"PF={d['profit_factor']:>5} net={d['net_r']:>7}R maxDD={d['max_drawdown_r']:>6}R")

print(f"Daily SMA{SMA_PER} trend alignment on top of ADX>{ADX_MIN:.0f}\n")
for label, subset in [("TRAIN", train), ("TEST", test)]:
    print(f"=== {label} ===")
    adx = [s for s in subset if adx_by_time.get(s.time,0) > ADX_MIN]
    adx_htf = [s for s in adx if aligned(s)]
    show("  ADX>35 (current best)", adx)
    show("  ADX>35 + daily-trend", adx_htf)
    print()
