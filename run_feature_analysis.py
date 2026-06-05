"""
Feature-outcome analysis: do the indicator's own entry features separate
winners from losers? Uses the logged condition values (no recalculation) joined
to each signal's backtested outcome.

If a feature is predictive, win rate should trend across its buckets.
If win rate is flat across every feature, the entries have no edge to tune.
"""
import csv
from datetime import datetime
from core.connection import signal_file_path
from core.backtest import run_backtest

# 1. Outcomes per signal (independent mode = every signal evaluated, baseline exits)
rep = run_backtest(sequential=False)
outcome_by_time = {t.time.strftime("%Y-%m-%d %H:%M"): t.outcome for t in rep.trades}

# 2. Raw logged feature values
COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
rows = []
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12: continue
        if not r[0][:4].isdigit(): continue  # skip header
        d = dict(zip(COLS, r))
        try:
            t = datetime.strptime(d["time"], "%Y.%m.%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
        oc = outcome_by_time.get(t)
        if oc not in ("WIN","LOSS"): continue
        rows.append({
            "win": 1 if oc == "WIN" else 0,
            "direction": d["direction"],
            "adx": float(d["adx"]),
            "mom_fast": abs(float(d["mom_fast"])),   # magnitude in signal direction
            "mom_med": abs(float(d["mom_med"])),
            "mom_slow": abs(float(d["mom_slow"])),
            "vol_ratio": float(d["vol_ratio"]),
            "hour": int(d["time"][11:13]),
        })

n = len(rows)
base_wr = sum(r["win"] for r in rows) / n * 100
print(f"signals analysed: {n}   baseline win rate: {base_wr:.1f}%\n")

def buckets(feature, k=5):
    vals = sorted(r[feature] for r in rows)
    cuts = [vals[int(len(vals)*i/k)] for i in range(1, k)]
    def b(v):
        for i, c in enumerate(cuts):
            if v <= c: return i
        return k-1
    groups = [[] for _ in range(k)]
    for r in rows:
        groups[b(r[feature])].append(r["win"])
    print(f"=== {feature} (quintiles, low -> high) ===")
    for i, g in enumerate(groups):
        if not g: continue
        wr = sum(g)/len(g)*100
        lo = "min" if i==0 else f"{cuts[i-1]:.3f}"
        hi = "max" if i==k-1 else f"{cuts[i]:.3f}"
        bar = "#" * int(wr)
        print(f"  Q{i+1} [{lo:>7}..{hi:>7}] n={len(g):>4}  WR={wr:5.1f}%  {bar}")
    print()

for feat in ["adx","mom_fast","mom_med","mom_slow","vol_ratio","hour"]:
    buckets(feat)

# direction split
for dval in ("LONG","SHORT"):
    g = [r["win"] for r in rows if r["direction"]==dval]
    if g:
        print(f"direction {dval}: n={len(g)} WR={sum(g)/len(g)*100:.1f}%")
