"""
Diagnose 2021 — the one losing year. Join each 2021 trade's outcome to the
regime/ADX/momentum values the indicator logged, to see whether a stricter
trend filter could have kept us out of the chop.
Compare the feature profile of 2021 vs the profitable years.
"""
import csv
from datetime import datetime
from collections import defaultdict
from core.connection import signal_file_path
from core.backtest import run_backtest

# outcomes per signal time
rep = run_backtest()
outcome = {t.time.strftime("%Y-%m-%d %H:%M"): t for t in rep.trades if t.outcome in ("WIN","LOSS")}

COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
rows = []
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try: dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError: continue
        key = dt.strftime("%Y-%m-%d %H:%M")
        if key not in outcome: continue
        t = outcome[key]
        rows.append({"year": dt.year, "win": 1 if t.outcome=="WIN" else 0,
                     "r": t.r, "adx": float(d["adx"]),
                     "mom_fast": abs(float(d["mom_fast"])),
                     "vol_ratio": float(d["vol_ratio"]),
                     "regime": d["regime"]})

def profile(label, items):
    n=len(items)
    if not n: return
    wr=sum(x["win"] for x in items)/n*100
    net=sum(x["r"] for x in items)
    adx=sum(x["adx"] for x in items)/n
    mf=sum(x["mom_fast"] for x in items)/n
    vr=sum(x["vol_ratio"] for x in items)/n
    print(f"{label:<22} n={n:>4} WR={wr:5.1f}% net={net:>7.1f}R | avgADX={adx:5.1f} avgMomFast={mf:.3f} avgVolRatio={vr:.3f}")

y2021=[x for x in rows if x["year"]==2021]
other=[x for x in rows if x["year"]!=2021]
print("Feature profile: 2021 (the bad year) vs all other years\n")
profile("2021 (losing)", y2021)
profile("all other years", other)

print("\n2021 win rate by ADX bucket (is high ADX still bad in 2021?):")
buckets=defaultdict(list)
for x in y2021:
    b = "ADX<25" if x["adx"]<25 else ("ADX 25-35" if x["adx"]<35 else "ADX>35")
    buckets[b].append(x)
for b in ["ADX<25","ADX 25-35","ADX>35"]:
    if buckets[b]:
        g=buckets[b]; wr=sum(z["win"] for z in g)/len(g)*100; net=sum(z["r"] for z in g)
        print(f"  {b:<10} n={len(g):>3} WR={wr:5.1f}% net={net:>6.1f}R")

print("\nADX>35 (strong-trend only) — does filtering to it rescue each year?")
for y in sorted(set(x['year'] for x in rows)):
    g=[x for x in rows if x['year']==y and x['adx']>35]
    if g:
        net=sum(z['r'] for z in g); wr=sum(z['win'] for z in g)/len(g)*100
        print(f"  {y}: n={len(g):>3} WR={wr:5.1f}% net={net:>6.1f}R")
