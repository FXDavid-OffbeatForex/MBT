"""
Backtest engine.

Principle (the hard-won lesson): we do NOT recalculate any indicator in Python.
We read the signals the MT5 indicator actually logged, then replay real MT5
price bars forward from each entry to see whether SL or TP was hit first.

Everything is measured in R (risk units): a loss is -1R, a win is +(reward/risk)R.
R units make results comparable across symbols, timeframes, and account sizes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Callable, Optional

from .connection import load_config
from .ohlcv import fetch_after
from .signals import Signal, load_signals


@dataclass
class TradeResult:
    time:      datetime
    symbol:    str
    direction: str
    entry:     float
    sl:        float
    tp:        float
    regime:    str
    outcome:   str       # "WIN" | "LOSS" | "OPEN"
    r:         float     # realised R (+rr, -1, or 0 if open)
    rr:        float     # planned reward:risk
    bars_held: int       # bars until resolution (0 if open)


@dataclass
class BacktestReport:
    symbol:           str
    timeframe:        str
    total:            int
    wins:             int
    losses:           int
    open_trades:      int
    win_rate:         float
    profit_factor:    float
    expectancy:       float    # avg R per closed trade
    net_r:            float
    avg_win:          float
    avg_loss:         float
    max_win_streak:   int
    max_loss_streak:  int
    max_drawdown_r:   float
    gross_win:        float
    gross_loss:       float
    equity_curve:     List[float] = field(default_factory=list)
    by_regime:        dict        = field(default_factory=dict)
    trades:           List[TradeResult] = field(default_factory=list)


def _simulate_one(sig: Signal, bars: list, ambiguous: str) -> TradeResult:
    """Replay bars forward from entry; decide WIN / LOSS / OPEN."""
    is_long = sig.direction == "LONG"
    risk    = abs(sig.entry - sig.sl)
    reward  = abs(sig.tp - sig.entry)
    rr      = (reward / risk) if risk > 0 else 0.0

    # Skip the entry bar itself (signal fires on its close); start the next bar.
    for i, b in enumerate(bars):
        if b["time"] <= sig.time:
            continue

        if is_long:
            hit_sl = b["low"]  <= sig.sl
            hit_tp = b["high"] >= sig.tp
        else:
            hit_sl = b["high"] >= sig.sl
            hit_tp = b["low"]  <= sig.tp

        if hit_sl and hit_tp:
            # Ambiguous bar: cannot tell order from OHLC.
            if ambiguous == "win":
                return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                                   sig.sl, sig.tp, sig.regime, "WIN", rr, rr, i)
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "LOSS", -1.0, rr, i)
        if hit_sl:
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "LOSS", -1.0, rr, i)
        if hit_tp:
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "WIN", rr, rr, i)

    return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                       sig.sl, sig.tp, sig.regime, "OPEN", 0.0, rr, 0)


def _max_drawdown(equity: List[float]) -> float:
    """Largest peak-to-trough drop on the cumulative-R equity curve (in R)."""
    peak = 0.0
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


def _streaks(results: List[TradeResult]):
    win_streak = loss_streak = cur_w = cur_l = 0
    for r in results:
        if r.outcome == "WIN":
            cur_w += 1; cur_l = 0
        elif r.outcome == "LOSS":
            cur_l += 1; cur_w = 0
        else:
            continue
        win_streak  = max(win_streak,  cur_w)
        loss_streak = max(loss_streak, cur_l)
    return win_streak, loss_streak


def run_backtest(
    signals:        Optional[List[Signal]] = None,
    fetch_fn:       Callable = fetch_after,
    ambiguous:      Optional[str] = None,
    forward_bars:   int = 1000,
) -> BacktestReport:
    """
    Run the backtest over all signals (loaded from the configured CSV if not
    supplied). `fetch_fn` is injectable so the engine can be unit-tested
    without a live MT5 connection.
    """
    cfg = load_config()
    if ambiguous is None:
        ambiguous = cfg.get("ambiguous_bar", "loss")
    if signals is None:
        signals = load_signals()

    results: List[TradeResult] = []
    for sig in signals:
        bars = fetch_fn(sig.symbol, sig.timeframe, sig.time, forward_bars)
        results.append(_simulate_one(sig, bars, ambiguous))

    closed   = [r for r in results if r.outcome != "OPEN"]
    wins     = [r for r in closed if r.outcome == "WIN"]
    losses   = [r for r in closed if r.outcome == "LOSS"]

    gross_win  = sum(r.r for r in wins)
    gross_loss = abs(sum(r.r for r in losses))
    net_r      = gross_win - gross_loss

    # equity curve in chronological order
    eq, run = [], 0.0
    for r in sorted(closed, key=lambda x: x.time):
        run += r.r
        eq.append(round(run, 3))

    win_streak, loss_streak = _streaks(sorted(results, key=lambda x: x.time))

    # per-regime breakdown
    by_regime = {}
    regimes = {r.regime for r in closed if r.regime}
    for rg in regimes:
        grp   = [r for r in closed if r.regime == rg]
        w     = [r for r in grp if r.outcome == "WIN"]
        gw    = sum(r.r for r in w)
        gl    = abs(sum(r.r for r in grp if r.outcome == "LOSS"))
        by_regime[rg] = {
            "trades":        len(grp),
            "win_rate":      round(len(w) / len(grp) * 100, 1) if grp else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else float("inf"),
            "net_r":         round(gw - (gl), 2),
        }

    sym = signals[0].symbol    if signals else cfg.get("default_symbol", "")
    tf  = signals[0].timeframe if signals else cfg.get("default_timeframe", "")

    return BacktestReport(
        symbol          = sym,
        timeframe       = tf,
        total           = len(results),
        wins            = len(wins),
        losses          = len(losses),
        open_trades     = len(results) - len(closed),
        win_rate        = round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        profit_factor   = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        expectancy      = round(net_r / len(closed), 3) if closed else 0.0,
        net_r           = round(net_r, 2),
        avg_win         = round(gross_win / len(wins), 2) if wins else 0.0,
        avg_loss        = round(gross_loss / len(losses), 2) if losses else 0.0,
        max_win_streak  = win_streak,
        max_loss_streak = loss_streak,
        max_drawdown_r  = round(_max_drawdown(eq), 2),
        gross_win       = round(gross_win, 2),
        gross_loss      = round(gross_loss, 2),
        equity_curve    = eq,
        by_regime       = by_regime,
        trades          = results,
    )


def report_to_dict(rep: BacktestReport, include_trades: bool = False) -> dict:
    """Compact dict for returning through the MCP layer."""
    d = {
        "symbol": rep.symbol, "timeframe": rep.timeframe,
        "total": rep.total, "wins": rep.wins, "losses": rep.losses,
        "open": rep.open_trades,
        "win_rate_pct": rep.win_rate,
        "profit_factor": rep.profit_factor,
        "expectancy_r": rep.expectancy,
        "net_r": rep.net_r,
        "avg_win_r": rep.avg_win, "avg_loss_r": rep.avg_loss,
        "max_win_streak": rep.max_win_streak,
        "max_loss_streak": rep.max_loss_streak,
        "max_drawdown_r": rep.max_drawdown_r,
        "by_regime": rep.by_regime,
    }
    if include_trades:
        d["trades"] = [
            {
                "time": t.time.strftime("%Y-%m-%d %H:%M"),
                "direction": t.direction, "outcome": t.outcome,
                "r": round(t.r, 2), "rr": round(t.rr, 2),
            }
            for t in rep.trades
        ]
    return d
