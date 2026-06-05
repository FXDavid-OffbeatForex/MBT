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
from .ohlcv import fetch_aligned, get_cost_price
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
    exit_time: Optional[datetime] = None   # bar time when SL/TP hit (None if open)


@dataclass
class BacktestReport:
    symbol:           str
    timeframe:        str
    total:            int
    signals_skipped:  int       # signals ignored because a trade was already open
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
    years:            float = 0.0
    risk_per_trade:   float = 0.01
    annual_return_pct: float = 0.0
    monthly_return_pct: float = 0.0
    max_drawdown_pct:  float = 0.0
    equity_curve:     List[float] = field(default_factory=list)
    by_regime:        dict        = field(default_factory=dict)
    trades:           List[TradeResult] = field(default_factory=list)


def _simulate_one(sig: Signal, bars: list, ambiguous: str, cost_price: float = 0.0) -> TradeResult:
    """Replay bars forward from entry; decide WIN / LOSS / OPEN. cost_price is
    the per-trade transaction cost in price terms, charged in R."""
    is_long = sig.direction == "LONG"
    risk    = abs(sig.entry - sig.sl)
    reward  = abs(sig.tp - sig.entry)
    rr      = (reward / risk) if risk > 0 else 0.0
    cost_R  = (cost_price / risk) if risk > 0 else 0.0
    win_r   = rr - cost_R
    loss_r  = -1.0 - cost_R

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
                                   sig.sl, sig.tp, sig.regime, "WIN", win_r, rr, i, b["time"])
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "LOSS", loss_r, rr, i, b["time"])
        if hit_sl:
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "LOSS", loss_r, rr, i, b["time"])
        if hit_tp:
            return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                               sig.sl, sig.tp, sig.regime, "WIN", win_r, rr, i, b["time"])

    return TradeResult(sig.time, sig.symbol, sig.direction, sig.entry,
                       sig.sl, sig.tp, sig.regime, "OPEN", 0.0, rr, 0, None)


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
    fetch_fn:       Callable = fetch_aligned,
    ambiguous:      Optional[str] = None,
    forward_bars:   int = 1000,
    sequential:     bool = True,
) -> BacktestReport:
    """
    Run the backtest over all signals (loaded from the configured CSV if not
    supplied). `fetch_fn` is injectable so the engine can be unit-tested
    without a live MT5 connection.

    sequential=True (default, realistic): one position at a time. When a trade
    is open, any signal that fires before it resolves is SKIPPED — this collapses
    clusters of consecutive same-setup signals into a single real trade, the way
    an actual trader or single-position EA would behave.

    sequential=False: every signal is evaluated independently (overlapping
    trades allowed). Useful for raw per-signal expectancy, not realistic P&L.
    """
    cfg = load_config()
    if ambiguous is None:
        ambiguous = cfg.get("ambiguous_bar", "loss")
    if signals is None:
        signals = load_signals()

    results: List[TradeResult] = []
    skipped = 0

    # symbol-aware transaction cost (spread + slippage), charged per trade in R
    slippage = cfg.get("slippage_points", 5.0)
    cost_price = get_cost_price(signals[0].symbol, slippage) if signals else 0.0

    if sequential:
        busy_until = None
        for sig in sorted(signals, key=lambda s: s.time):
            if busy_until is not None and sig.time <= busy_until:
                skipped += 1
                continue
            bars = fetch_fn(sig.symbol, sig.timeframe, sig.time, sig.entry, forward_bars)
            res  = _simulate_one(sig, bars, ambiguous, cost_price)
            results.append(res)
            if res.exit_time is not None:
                busy_until = res.exit_time   # open trade → next signal can enter
    else:
        for sig in signals:
            bars = fetch_fn(sig.symbol, sig.timeframe, sig.time, sig.entry, forward_bars)
            results.append(_simulate_one(sig, bars, ambiguous, cost_price))

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

    # --- percentage returns: translate R into account % via risk-per-trade ---
    risk = cfg.get("risk_per_trade", 0.01)
    mdd_r = _max_drawdown(eq)
    times = [r.time for r in closed]
    years = 0.0
    if len(times) >= 2:
        years = (max(times) - min(times)).days / 365.25
    annual_r = (net_r / years) if years > 0 else 0.0
    annual_pct  = annual_r * risk * 100
    monthly_pct = annual_pct / 12
    mdd_pct     = mdd_r * risk * 100

    return BacktestReport(
        symbol          = sym,
        timeframe       = tf,
        total           = len(results),
        signals_skipped = skipped,
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
        max_drawdown_r  = round(mdd_r, 2),
        gross_win       = round(gross_win, 2),
        gross_loss      = round(gross_loss, 2),
        years              = round(years, 2),
        risk_per_trade     = risk,
        annual_return_pct  = round(annual_pct, 1),
        monthly_return_pct = round(monthly_pct, 2),
        max_drawdown_pct   = round(mdd_pct, 1),
        equity_curve    = eq,
        by_regime       = by_regime,
        trades          = results,
    )


def report_to_dict(rep: BacktestReport, include_trades: bool = False) -> dict:
    """Compact dict for returning through the MCP layer."""
    d = {
        "symbol": rep.symbol, "timeframe": rep.timeframe,
        "trades_taken": rep.total, "signals_skipped": rep.signals_skipped,
        "wins": rep.wins, "losses": rep.losses,
        "open": rep.open_trades,
        "win_rate_pct": rep.win_rate,
        "profit_factor": rep.profit_factor,
        "expectancy_r": rep.expectancy,
        "net_r": rep.net_r,
        "avg_win_r": rep.avg_win, "avg_loss_r": rep.avg_loss,
        "max_win_streak": rep.max_win_streak,
        "max_loss_streak": rep.max_loss_streak,
        "max_drawdown_r": rep.max_drawdown_r,
        "years": rep.years,
        "risk_per_trade_pct": round(rep.risk_per_trade * 100, 2),
        "annual_return_pct": rep.annual_return_pct,
        "monthly_return_pct": rep.monthly_return_pct,
        "max_drawdown_pct": rep.max_drawdown_pct,
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
