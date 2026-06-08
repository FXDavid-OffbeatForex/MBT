"""
Validate the momentum-inversion finding: does the pattern hold in BOTH the
train half and the locked test half? A real edge shows in both; noise doesn't.
"""
import csv
from datetime import datetime
from core.connection import signal_file_path
from core.backtest import run_backtest

rep = run_backtest(sequential=False)
outcome_by_time = {t.time.strftime("%Y-%m-%d %H:%M"): t.outcome for t in rep.trades}

COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
rows = []
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try:
            dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError:
            continue
        oc = outcome_by_time.get(dt.strftime("%Y-%m-%d %H:%M"))
        if oc not in ("WIN","LOSS"): continue
        rows.append({"dt": dt, "win": 1 if oc=="WIN" else 0,
                     "mom_med": abs(float(d["mom_med"])),
                     "mom_slow": abs(float(d["mom_slow"])),
                     "vol_ratio": float(d["vol_ratio"])})

rows.sort(key=lambda r: r["dt"])
split = int(len(rows)*0.7)
train, test = rows[:split], rows[split:]

def terciles(data, feat):
    vals = sorted(r[feat] for r in data)
    c1, c2 = vals[len(vals)//3], vals[2*len(vals)//3]
    g = [[],[],[]]
    for r in data:
        idx = 0 if r[feat]<=c1 else (1 if r[feat]<=c2 else 2)
        g[idx].append(r["win"])
    return [(sum(x)/len(x)*100 if x else 0, len(x)) for x in g]

for feat in ["mom_med","mom_slow","vol_ratio"]:
    tr = terciles(train, feat)
    te = terciles(test, feat)
    print(f"=== {feat}: win rate by tercile (low / mid / high) ===")
    print(f"  TRAIN: {tr[0][0]:5.1f}% / {tr[1][0]:5.1f}% / {tr[2][0]:5.1f}%   (n={tr[0][1]},{tr[1][1]},{tr[2][1]})")
    print(f"  TEST : {te[0][0]:5.1f}% / {te[1][0]:5.1f}% / {te[2][0]:5.1f}%   (n={te[0][1]},{te[1][1]},{te[2][1]})")
    print()
