"""
Fade-high-momentum strategy: consistency check + exit optimization.

1. Confirm FADE-HI is profitable in the TRAIN years too (not just test).
2. Optimize the exit (SL mult x RR) on TRAIN, confirm the pick on the locked TEST.
3. Generate an HTML report (out-of-sample) with % returns.
"""
import csv
from datetime import datetime
from core.connection import load_config, signal_file_path
from core.signals import load_signals, Signal
from core.optimize_exits import _build_cache, _pip_size
from core.ohlcv import fetch_aligned
from core.backtest import run_backtest, report_to_dict
from core.report_html import render

cfg       = load_config()
amb       = cfg.get("ambiguous_bar", "loss")
om        = cfg.get("orig_stop_multiplier", 2.5)
risk      = cfg.get("risk_per_trade", 0.01)
cost_pips = cfg.get("spread_pips", 1.0) + cfg.get("slippage_pips", 0.5)

COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
mom = {}
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try: dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError: continue
        mom[dt] = abs(float(d["mom_med"]))

signals = sorted(load_signals(), key=lambda s: s.time)
cache   = _build_cache(signals, fetch_aligned, om, 1000)
cache   = [(cs, mom.get(cs.sig.time, 0.0)) for cs in cache]
split   = int(len(cache) * 0.70)
train, test = cache[:split], cache[split:]
hi_cut  = sorted(m for _, m in train)[int(len(train) * 2 / 3)]
pip     = _pip_size(signals[0].symbol)
cost    = cost_pips * pip

train_hi = [x for x in train if x[1] > hi_cut]
test_hi  = [x for x in test  if x[1] > hi_cut]


def evaluate(items, M, RR):
    busy = None; wins = losses = 0; gw = gl = 0.0; rs = []; dates = []
    for cs, _ in items:
        if busy is not None and cs.sig.time <= busy: continue
        direction = "SHORT" if cs.sig.direction == "LONG" else "LONG"   # FADE
        is_long = direction == "LONG"
        stop = M * cs.one_atr
        if stop <= 0: continue
        tpd = RR * stop; e = cs.entry_open
        sl = e - stop if is_long else e + stop
        tp = e + tpd  if is_long else e - tpd
        oc = "OPEN"; et = None
        for b in cs.bars:
            if b["time"] <= cs.sig.time: continue
            if is_long: hsl, htp = b["low"] <= sl, b["high"] >= tp
            else:       hsl, htp = b["high"] >= sl, b["low"] <= tp
            if hsl and htp: oc = "LOSS" if amb != "win" else "WIN"; et = b["time"]; break
            if hsl: oc = "LOSS"; et = b["time"]; break
            if htp: oc = "WIN";  et = b["time"]; break
        cR = cost / stop
        if oc == "WIN":  rs.append(RR - cR); wins += 1;  gw += RR - cR; dates.append(cs.sig.time); busy = et
        elif oc == "LOSS": rs.append(-1 - cR); losses += 1; gl += 1 + cR; dates.append(cs.sig.time); busy = et
    closed = wins + losses
    net = sum(rs)
    peak = run_ = mdd = 0.0
    for r in rs:
        run_ += r; peak = max(peak, run_); mdd = max(mdd, peak - run_)
    years = (max(dates) - min(dates)).days / 365.25 if len(dates) >= 2 else 0
    ann = (net / years * risk * 100) if years else 0
    return {"M": M, "RR": RR, "trades": closed,
            "WR": round(wins/closed*100,1) if closed else 0,
            "PF": round(gw/gl,2) if gl>0 else 99,
            "exp": round(net/closed,3) if closed else 0,
            "net_r": round(net,1), "mdd_r": round(mdd,1),
            "yrs": round(years,1),
            "ann_pct": round(ann,1), "mo_pct": round(ann/12,2),
            "mdd_pct": round(mdd*risk*100,1)}


print(f"hi-momentum cutoff (train top tercile): mom_med > {hi_cut:.3f}")
print(f"risk/trade for %: {risk*100:.0f}%   cost: {cost_pips} pips\n")

print("=== 1) CONSISTENCY (fixed exit M=3.0 RR=2.0) ===")
for label, items in [("TRAIN", train_hi), ("TEST ", test_hi)]:
    r = evaluate(items, 3.0, 2.0)
    print(f"  {label}: {r['trades']} trades  WR={r['WR']}%  PF={r['PF']}  "
          f"exp={r['exp']}R  ann={r['ann_pct']}%  maxDD={r['mdd_pct']}%")

print("\n=== 2) EXIT OPTIMIZATION (optimize on TRAIN, confirm on TEST) ===")
best = None
for M in [2.0, 2.5, 3.0, 3.5, 4.0]:
    for RR in [1.0, 1.5, 2.0, 2.5, 3.0]:
        r = evaluate(train_hi, M, RR)
        if r["trades"] >= 60 and (best is None or r["exp"] > best["exp"]):
            best = r
print(f"  TRAIN best: M={best['M']} RR={best['RR']}  "
      f"trades={best['trades']} WR={best['WR']}% PF={best['PF']} "
      f"exp={best['exp']}R ann={best['ann_pct']}% maxDD={best['mdd_pct']}%")
t = evaluate(test_hi, best["M"], best["RR"])
print(f"  -> LOCKED TEST: trades={t['trades']} WR={t['WR']}% PF={t['PF']} "
      f"exp={t['exp']}R ann={t['ann_pct']}% mo={t['mo_pct']}% maxDD={t['mdd_pct']}%  ({t['yrs']} yrs)")

# 3) HTML report — out-of-sample (test) fade with the chosen exit
M, RR = best["M"], best["RR"]
fade_sigs = []
for cs, _ in test_hi:
    direction = "SHORT" if cs.sig.direction == "LONG" else "LONG"
    is_long = direction == "LONG"
    stop = M * cs.one_atr; tpd = RR * stop; e = cs.sig.entry
    fade_sigs.append(Signal(
        time=cs.sig.time, direction=direction, entry=e,
        sl=e - stop if is_long else e + stop,
        tp=e + tpd  if is_long else e - tpd,
        symbol=cs.sig.symbol, timeframe=cs.sig.timeframe,
        regime="FADE"))
rep = run_backtest(signals=fade_sigs, sequential=True)
path = render(rep, filename=f"fade_hi_TEST_M{M}_RR{RR}.html")
print(f"\nHTML (out-of-sample): {path}")
