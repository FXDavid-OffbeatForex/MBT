"""Read recorded deal balances from an MT5 single-test HTML report.

These are account-balance observations after deals, not intrade equity ticks.
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import math
from pathlib import Path


class _DealTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cell = []
        self.row = []
        self.header_found = False
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == 'tr':
            self.in_row, self.row = True, []
        elif tag in ('td', 'th') and self.in_row:
            self.in_cell, self.cell = True, []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ('td', 'th') and self.in_cell:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.in_cell = False
        elif tag == 'tr' and self.in_row:
            if (len(self.row) == 13 and self.row[:5] ==
                    ['Time', 'Deal', 'Symbol', 'Type', 'Direction'] and
                    self.row[11] == 'Balance'):
                self.header_found = True
            elif self.header_found and len(self.row) == 13:
                self.rows.append(self.row)
            self.in_row = False


def deal_balance_points(path: Path) -> list[tuple[datetime, float]]:
    """Return exact recorded deal balances, or fail closed on unsupported reports."""
    if not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('MT5 report is missing or too large for chart parsing')
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        markup = raw.decode('utf-16')
    else:
        try:
            markup = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            markup = raw.decode('cp1252')
    parser = _DealTable()
    parser.feed(markup)
    if not parser.header_found or not 2 <= len(parser.rows) <= 100000:
        raise ValueError('MT5 report has no usable deal-balance table')
    points = []
    for row in parser.rows:
        try:
            stamp = datetime.strptime(row[0], '%Y.%m.%d %H:%M:%S')
            balance = float(row[11].replace(' ', '').replace(',', ''))
        except (ValueError, OverflowError) as exc:
            raise ValueError('MT5 deal table has an invalid time or balance') from exc
        if not math.isfinite(balance) or (points and stamp < points[-1][0]):
            raise ValueError('MT5 deal balances are non-finite or out of order')
        points.append((stamp, balance))
    return points
