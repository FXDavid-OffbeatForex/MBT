"""
Read the standard MBT signal CSV produced by SignalLogger.mqh.

Standard header (written by SignalLogger.mqh):
    time,symbol,timeframe,direction,entry,sl,tp[,...extra columns]

The reader is tolerant:
  * Works with or without a header row.
  * Accepts "direction" OR "signal" for the LONG/SHORT column.
  * Falls back to config default_symbol / default_timeframe when the CSV
    rows don't carry their own.
  * Ignores any extra columns an indicator adds for its own purposes.
This means the legacy ARM signal file (which has no header) still loads.
"""

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .connection import load_config, signal_file_path

# Legacy ARM column order (file has no header row)
_ARM_LEGACY = [
    "time", "direction", "entry", "sl", "tp", "atr_pips", "regime",
    "adx", "mom_fast", "mom_med", "mom_slow", "vol_ratio", "vpt_dir", "near_level",
]

_DATE_RE = re.compile(r"^\d{4}[.\-/]\d{2}[.\-/]\d{2}")


@dataclass
class Signal:
    time:      datetime
    direction: str          # "LONG" or "SHORT"
    entry:     float
    sl:        float
    tp:        float
    symbol:    str
    timeframe: str
    regime:    str = ""


def _parse_time(s: str) -> Optional[datetime]:
    s = s.strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _looks_like_header(first_row: List[str]) -> bool:
    """A header row's first cell is not a date."""
    return not (first_row and _DATE_RE.match(first_row[0].strip()))


def load_signals(path: Optional[str] = None) -> List[Signal]:
    """Load and normalise all signals from the configured (or given) CSV."""
    cfg  = load_config()
    path = path or signal_file_path()

    with open(path, encoding="cp1252", errors="replace", newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    if _looks_like_header(rows[0]):
        header   = [h.strip().lower() for h in rows[0]]
        data     = rows[1:]
    else:
        header   = _ARM_LEGACY
        data     = rows

    def col(row, *names):
        for n in names:
            if n in header:
                idx = header.index(n)
                if idx < len(row):
                    return row[idx].strip()
        return ""

    out: List[Signal] = []
    for row in data:
        if not row:
            continue
        t = _parse_time(col(row, "time"))
        if t is None:
            continue

        direction = col(row, "direction", "signal").upper()
        if direction not in ("LONG", "SHORT"):
            continue

        try:
            entry = float(col(row, "entry"))
            sl    = float(col(row, "sl"))
            tp    = float(col(row, "tp"))
        except ValueError:
            continue

        out.append(Signal(
            time      = t,
            direction = direction,
            entry     = entry,
            sl        = sl,
            tp        = tp,
            symbol    = col(row, "symbol")    or cfg.get("default_symbol", "EURUSD"),
            timeframe = col(row, "timeframe") or cfg.get("default_timeframe", "1h"),
            regime    = col(row, "regime"),
        ))

    return out
