from core.optimize_exits import optimize_exits
import time

t0 = time.time()
r = optimize_exits()
print(f"took {round(time.time()-t0,1)}s")
print("train:", r["train_period"].replace("→", "to"))
print("test :", r["test_period"].replace("→", "to"))
print("cost/trade (price):", r["cost_price_per_trade"])
print()

rrs = sorted(set(g["RR"] for g in r["grid"]))
ms  = sorted(set(g["M"]  for g in r["grid"]))

print("=== TRAIN: expectancy (R per trade) — rows=SL mult, cols=RR ===")
header = "SL\\RR".ljust(7) + "".join(f"{rr:>8}" for rr in rrs)
print(header)
for M in ms:
    row = f"{M:>6} "
    for rr in rrs:
        g = next(x for x in r["grid"] if x["M"] == M and x["RR"] == rr)
        row += f"{g['expectancy']:>8.3f}"
    print(row)

print()
print("=== TRAIN: net R — rows=SL mult, cols=RR ===")
print(header)
for M in ms:
    row = f"{M:>6} "
    for rr in rrs:
        g = next(x for x in r["grid"] if x["M"] == M and x["RR"] == rr)
        row += f"{g['net_r']:>8.0f}"
    print(row)

def show(label, d):
    print(f"\n{label}: M={d['M']} RR={d['RR']} | trades={d['trades']} "
          f"WR={d['win_rate']}% PF={d['profit_factor']} "
          f"exp={d['expectancy']}R net={d['net_r']}R maxDD={d['max_dd_r']}R")

show("TRAIN BEST", r["train_best"])
show("  -> on LOCKED TEST", r["test_result"])
show("BASELINE 2.5/3.5 on TEST", r["baseline_on_test"])
