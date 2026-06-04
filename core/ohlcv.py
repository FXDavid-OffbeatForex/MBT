"""
OHLCV price fetching from MT5. Broker-agnostic — whatever terminal config.yaml
points at is the source of truth.
"""

from datetime import datetime, timedelta
import MetaTrader5 as mt5

from .connection import connect

TIMEFRAME_MAP = {
    "1m":  mt5.TIMEFRAME_M1,
    "5m":  mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,
    "1h":  mt5.TIMEFRAME_H1,
    "4h":  mt5.TIMEFRAME_H4,
    "1d":  mt5.TIMEFRAME_D1,
    "1w":  mt5.TIMEFRAME_W1,
}

# Approx seconds per bar — used to bound the forward fetch window.
TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def fetch_recent(symbol: str, timeframe: str, count: int = 100) -> list:
    """Most-recent `count` bars, newest first."""
    connect()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'. Options: {list(TIMEFRAME_MAP)}")

    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], 0, count)
    if rates is None:
        raise RuntimeError(f"No data for {symbol} {timeframe}: {mt5.last_error()}")

    return [
        {
            "time":   datetime.fromtimestamp(int(r["time"])).strftime("%Y-%m-%d %H:%M"),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in reversed(rates)
    ]


def fetch_after(symbol: str, timeframe: str, start: datetime, count: int = 1000) -> list:
    """
    Bars at or after `start`, chronological order (oldest first).
    Used by the backtester to replay each trade forward from its entry bar.

    Uses copy_rates_range (unambiguous: returns the [start, end] window) rather
    than copy_rates_from, which returns bars *ending* at the date.
    """
    connect()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'.")

    end = start + timedelta(seconds=TF_SECONDS.get(timeframe, 3600) * count)
    rates = mt5.copy_rates_range(symbol, TIMEFRAME_MAP[timeframe], start, end)
    if rates is None:
        return []

    return [
        {
            "time":  datetime.fromtimestamp(int(r["time"])),
            "open":  float(r["open"]),
            "high":  float(r["high"]),
            "low":   float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rates
    ]
