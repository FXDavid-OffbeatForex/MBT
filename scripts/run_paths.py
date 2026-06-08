"""
Test the momentum-inversion finding out-of-sample.

Variants, all evaluated ONLY on the locked test slice, with a fixed neutral exit
(so we isolate the entry change), realistic costs, and one-trade-at-a-time:

  BASELINE      : all signals, original direction
  PATH A FILTER : drop high-momentum signals, keep original direction
  PATH B FADE-HI: take ONLY high-momentum signals, FLIP direction (mean-revert)
  PATH B FADE-ALL: take ALL signals, FLIP direction (full mean-reversion)

The high-momentum cutoff is derived from TRAIN only, then applied to TEST.
"""
import csv
from datetime import datetime
from core.connection import load_config, signal_file_path
from core.signals import load_signals
from core.optimize_exits import _build_cache, _pip_size

cfg        = load_config()
ambiguous  = cfg.get("ambiguous_bar", "loss")
orig_mult  = cfg.get("orig_stop_multiplier", 2.5)
cost_pips  = cfg.get("spread_pips", 1.0) + cfg.get("slippage_pips", 0.5)

M_FIX, RR_FIX = 3.0, 2.0   # neutral fixed exit for apples-to-apples comparison

# --- mom_med by signal time (from raw log) ---
COLS = ["time","direction","entry","sl","tp","atr_pips","regime",
        "adx","mom_fast","mom_med","mom_slow","vol_ratio","vpt_dir","near_level"]
mom_by_time = {}
with open(signal_file_path(), encoding="ansi") as f:
    for r in csv.reader(f):
        if not r or len(r) < 12 or not r[0][:4].isdigit(): continue
        d = dict(zip(COLS, r))
        try:
            dt = datetime.strptime(d["time"], "%Y.%m.%d %H:%M")
        except ValueError:
            continue
        mom_by_time[dt] = abs(float(d["mom_med"]))

# --- build cache (fetch forward bars once) ---
signals = sorted(load_signals(), key=lambda s: s.time)
cache   = _build_cache(signals, __import__("core.ohlcv", fromlist=["fetch_after"]).fetch_after,
                       orig_mult, 1000)
# attach mom_med
cache = [(cs, mom_by_time.get(cs.sig.time, 0.0)) for cs in cache]

# --- train/test split ---
split = int(len(cache) * 0.70)
train, test = cache[:split], cache[split:]

# high-momentum cutoff from TRAIN (top tercile)
train_moms = sorted(m for _, m in train)
hi_cut = train_moms[int(len(train_moms) * 2 / 3)]
print(f"high-momentum cutoff (train top tercile): mom_med > {hi_cut:.3f}")
print(f"test signals: {len(test)} | fixed exit M={M_FIX} RR={RR_FIX} | cost {cost_pips} pips\n")

pip        = _pip_size(signals[0].symbol)
cost_price = cost_pips * pip


def evaluate(items, flip):
    """items: list of (cached_signal, mom_med). flip: reverse trade direction."""
    busy_until = None
    wins = losses = 0
    gw = gl = 0.0
    rs = []
    for cs, _m in items:
        if busy_until is not None and cs.sig.time <= busy_until:
            continue
        direction = cs.sig.direction
        if flip:
            direction = "SHORT" if direction == "LONG" else "LONG"
        is_long = direction == "LONG"
        stop = M_FIX * cs.one_atr
        if stop <= 0:
            continue
        tpd = RR_FIX * stop
        e = cs.sig.entry
        sl = e - stop if is_long else e + stop
        tp = e + tpd  if is_long else e - tpd

        outcome = "OPEN"; exit_time = None
        for b in cs.bars:
            if b["time"] <= cs.sig.time:
                continue
            if is_long:
                hsl, htp = b["low"] <= sl, b["high"] >= tp
            else:
                hsl, htp = b["high"] >= sl, b["low"] <= tp
            if hsl and htp:
                outcome = "WIN" if ambiguous == "win" else "LOSS"; exit_time = b["time"]; break
            if hsl:
                outcome = "LOSS"; exit_time = b["time"]; break
            if htp:
                outcome = "WIN"; exit_time = b["time"]; break

        cost_R = cost_price / stop
        if outcome == "WIN":
            r = RR_FIX - cost_R; wins += 1; gw += r
        elif outcome == "LOSS":
            r = -1.0 - cost_R; losses += 1; gl += abs(r)
        else:
            continue
        rs.append(r); busy_until = exit_time

    closed = wins + losses
    net = sum(rs)
    peak = run = mdd = 0.0
    for r in rs:
        run += r; peak = max(peak, run); mdd = max(mdd, peak - run)
    pf = (gw / gl) if gl > 0 else float("inf")
    return {
        "trades": closed,
        "win_rate": round(wins/closed*100,1) if closed else 0,
        "profit_factor": round(pf,2),
        "expectancy": round(net/closed,4) if closed else 0,
        "net_r": round(net,1),
        "max_dd_r": round(mdd,1),
    }


variants = {
    "BASELINE (all, original dir)":       (test, False),
    "PATH A FILTER (drop hi-mom)":        ([x for x in test if x[1] <= hi_cut], False),
    "PATH B FADE-HI (hi-mom, flipped)":   ([x for x in test if x[1] >  hi_cut], True),
    "PATH B FADE-ALL (all, flipped)":     (test, True),
}

print(f"{'variant':<34}{'trades':>7}{'WR%':>7}{'PF':>7}{'exp_R':>8}{'net_R':>8}{'maxDD':>7}")
print("-"*78)
for name, (items, flip) in variants.items():
    r = evaluate(items, flip)
    print(f"{name:<34}{r['trades']:>7}{r['win_rate']:>7}{r['profit_factor']:>7}"
          f"{r['expectancy']:>8}{r['net_r']:>8}{r['max_dd_r']:>7}")
