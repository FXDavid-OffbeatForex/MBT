"""
Exit optimizer — sweep stop-loss multiplier × reward:risk WITHOUT regenerating
signals.

Why this is cheap: the entry points never change when you only move the stop and
target. The signal log already carries entry + the original SL distance, so we
can recover each trade's raw 1×ATR (atr = |entry - sl| / orig_stop_multiplier)
and then recompute SL/TP for any candidate (M, RR). We fetch each signal's
forward bars once, cache them, and replay every combination in memory.

Anti-overfitting: optimize ONLY on the train slice, then report the chosen
config's performance on a locked test slice it never saw.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Callable, Optional

from .connection import load_config
from .ohlcv import fetch_aligned, get_cost_price
from .signals import Signal, load_signals


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


@dataclass
class _CachedSignal:
    sig:        Signal
    one_atr:    float       # recovered raw 1×ATR distance in price
    bars:       list        # aligned tradeable bars (after the signal bar)
    entry_open: float       # realistic fill = open of the first tradeable bar


def _build_cache(signals: List[Signal], fetch_fn: Callable,
                 orig_mult: float, forward_bars: int) -> List[_CachedSignal]:
    cache = []
    for s in signals:
        stop_dist = abs(s.entry - s.sl)
        regime_mult = 1.8 if s.regime.upper() == "VOLATILE" else 1.0
        one_atr = stop_dist / (orig_mult * regime_mult) if stop_dist > 0 else 0.0
        if one_atr <= 0:
            continue
        bars = fetch_fn(s.symbol, s.timeframe, s.time, s.entry, forward_bars)
        if not bars:                       # signal bar couldn't be located
            continue
        cache.append(_CachedSignal(s, one_atr, bars, bars[0]["open"]))
    return cache


def _replay(cs: _CachedSignal, sl: float, tp: float, ambiguous: str):
    """Return (outcome, exit_time) replaying cached bars forward from entry."""
    is_long = cs.sig.direction == "LONG"
    for b in cs.bars:
        if b["time"] <= cs.sig.time:
            continue
        if is_long:
            hit_sl = b["low"]  <= sl
            hit_tp = b["high"] >= tp
        else:
            hit_sl = b["high"] >= sl
            hit_tp = b["low"]  <= tp
        if hit_sl and hit_tp:
            return ("WIN" if ambiguous == "win" else "LOSS", b["time"])
        if hit_sl:
            return ("LOSS", b["time"])
        if hit_tp:
            return ("WIN", b["time"])
    return ("OPEN", None)


def _evaluate(cache: List[_CachedSignal], M: float, RR: float,
              cost_price: float, ambiguous: str) -> dict:
    """Sequential (one-trade-at-a-time) evaluation of one (M, RR) combo."""
    busy_until = None
    rs = []
    wins = losses = 0
    gross_win = gross_loss = 0.0

    for cs in cache:  # cache is pre-sorted chronologically
        if busy_until is not None and cs.sig.time <= busy_until:
            continue

        stop_dist = M * cs.one_atr
        tp_dist   = RR * stop_dist
        if stop_dist <= 0:
            continue
        is_long = cs.sig.direction == "LONG"
        e = cs.entry_open                       # realistic fill
        sl = e - stop_dist if is_long else e + stop_dist
        tp = e + tp_dist   if is_long else e - tp_dist

        outcome, exit_time = _replay(cs, sl, tp, ambiguous)
        cost_R = cost_price / stop_dist

        if outcome == "WIN":
            r = RR - cost_R;  wins += 1;   gross_win  += r
        elif outcome == "LOSS":
            r = -1.0 - cost_R; losses += 1; gross_loss += abs(r)
        else:
            continue  # open trade, not counted; doesn't block forever
        rs.append(r)
        busy_until = exit_time

    closed = wins + losses
    net = sum(rs)
    # max drawdown on equity curve
    peak = run = mdd = 0.0
    for r in rs:
        run += r; peak = max(peak, run); mdd = max(mdd, peak - run)

    return {
        "M": M, "RR": RR,
        "trades": closed, "wins": wins, "losses": losses,
        "win_rate": round(wins / closed * 100, 1) if closed else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "expectancy": round(net / closed, 4) if closed else 0.0,
        "net_r": round(net, 1),
        "max_dd_r": round(mdd, 1),
    }


def optimize_exits(
    signals:      Optional[List[Signal]] = None,
    stop_mults:   Optional[List[float]] = None,
    rr_ratios:    Optional[List[float]] = None,
    train_frac:   float = 0.70,
    min_trades:   int = 100,
    forward_bars: int = 1000,
    fetch_fn:     Callable = fetch_aligned,
) -> dict:
    """
    Sweep SL multiplier × RR. Optimize on the train slice, confirm on the locked
    test slice. Returns the full grid, the train-chosen best, and its test result.
    """
    cfg = load_config()
    if signals is None:
        signals = load_signals()
    if stop_mults is None:
        stop_mults = [1.0, 1.5, 2.0, 2.5, 3.0]
    if rr_ratios is None:
        rr_ratios = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]

    ambiguous = cfg.get("ambiguous_bar", "loss")
    orig_mult = cfg.get("orig_stop_multiplier", 2.5)
    slippage  = cfg.get("slippage_points", 5.0)

    # chronological order; split by time
    signals = sorted(signals, key=lambda s: s.time)
    split   = int(len(signals) * train_frac)
    train_sigs, test_sigs = signals[:split], signals[split:]

    cost_price = get_cost_price(signals[0].symbol, slippage) if signals else 0.0

    train_cache = _build_cache(train_sigs, fetch_fn, orig_mult, forward_bars)
    test_cache  = _build_cache(test_sigs,  fetch_fn, orig_mult, forward_bars)

    # sweep on train
    grid = []
    for M in stop_mults:
        for RR in rr_ratios:
            grid.append(_evaluate(train_cache, M, RR, cost_price, ambiguous))

    # choose best on train: highest expectancy among combos with enough trades
    eligible = [g for g in grid if g["trades"] >= min_trades]
    pool = eligible if eligible else grid
    best = max(pool, key=lambda g: g["expectancy"])

    # confirm on locked test slice
    test_result = _evaluate(test_cache, best["M"], best["RR"], cost_price, ambiguous)

    # baseline (current live config) on test, for reference
    baseline = _evaluate(test_cache, orig_mult, 3.5, cost_price, ambiguous)

    return {
        "train_period": f"{train_sigs[0].time:%Y-%m-%d} → {train_sigs[-1].time:%Y-%m-%d}",
        "test_period":  f"{test_sigs[0].time:%Y-%m-%d} → {test_sigs[-1].time:%Y-%m-%d}",
        "cost_price_per_trade": round(cost_price, 5),
        "grid": grid,
        "train_best": best,
        "test_result": test_result,
        "baseline_on_test": baseline,
    }
