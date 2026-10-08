"""Opt-in tester equity observations, never reconstructed from report totals.

The source adapter is intentionally narrow. It returns a generated copy; callers
must compile it in an MBT-owned folder and verify identical original EA results.
CSV completion only proves recorder shutdown, not successful tester completion.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import io
import math
from pathlib import Path
import re

HEADER = ['run_id', 'source_sha256', 'symbol', 'timeframe', 'model', 'sequence',
          'time', 'balance', 'equity']
MAX_BYTES = 64 * 1024 * 1024
MAX_POINTS = 500000


def _identity(run_id: str, source_sha256: str, symbol: str, timeframe: str, model: int):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,100}', run_id):
        raise ValueError('Invalid equity capture identity')
    if not re.fullmatch(r'[a-f0-9]{64}', source_sha256):
        raise ValueError('Invalid original source hash')
    if not re.fullmatch(r'[A-Za-z0-9_.#-]{1,64}', symbol):
        raise ValueError('Unsupported equity capture symbol')
    if not re.fullmatch(r'(?:M|H|D|W|MN)[0-9]{1,3}', timeframe) or model not in (0, 1, 2, 4):
        raise ValueError('Unsupported equity capture period/model')


def build_equity_adapter(source: str, *, run_id: str, source_sha256: str,
                         symbol: str, timeframe: str, model: int) -> str:
    """Return generated standalone source with an embedded recorder.

    Reject timers/transaction/book events: main-symbol tick sampling would not
    describe their intervening changes. No macro-renaming of includes occurs.
    """
    _identity(run_id, source_sha256, symbol, timeframe, model)
    code = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"',
                  lambda m: ' ' * len(m.group()), source)
    if re.search(r'\b(?:MBT_EQ_\w*|OnTimer|OnTrade|OnTradeTransaction|OnBookEvent|OnChartEvent)\b', code):
        raise ValueError('Equity adapter does not support extra event handlers or reserved names')
    for directive in re.findall(r'^\s*#([^\n]+)', source, flags=re.M):
        if not re.fullmatch(r'(?:property\s+[^\n]+|define\s+[A-Za-z_]\w*\s+[+-]?\d+(?:\.\d+)?|include\s*<Trade\\Trade\.mqh>)\s*', directive):
            raise ValueError('Equity adapter rejects custom includes/preprocessor behavior')
    tick = list(re.finditer(r'\bvoid\s+OnTick\s*\(\s*(?:void\s*)?\)\s*\{', code))
    deinit = list(re.finditer(r'\bvoid\s+OnDeinit\s*\(\s*(?:const\s+)?int\s+([A-Za-z_]\w*)\s*\)\s*\{', code))
    if len(tick) != 1 or len(deinit) > 1:
        raise ValueError('Equity adapter requires one ordinary void OnTick handler')
    # All references are renamed, not comments or strings. Source offsets remain
    # intact while the masked code identifies each event token.
    replacements = []
    for old, new in [('OnTick', 'MBT_EQ_OriginalTick'), ('OnDeinit', 'MBT_EQ_OriginalDeinit')]:
        for match in re.finditer(r'\b' + old + r'\b', code):
            replacements.append((match.start(), match.end(), new))
    adapted = source
    for start, end, replacement in sorted(replacements, reverse=True):
        adapted = adapted[:start] + replacement + adapted[end:]
    recorder = (Path(__file__).resolve().parents[1] / 'mql5' / 'MBT_EquityRecorder.mqh').read_text(encoding='utf-8')
    definitions = (f'#define MBT_EQ_RUN "{run_id}"\n'
                   f'#define MBT_EQ_SOURCE "{source_sha256}"\n'
                   f'#define MBT_EQ_SYMBOL "{symbol}"\n'
                   f'#define MBT_EQ_PERIOD "{timeframe}"\n'
                   f'#define MBT_EQ_MODEL {model}\n')
    final_call = 'MBT_EQ_OriginalDeinit(reason);' if deinit else ''
    return (adapted + '\n' + definitions + recorder + '\n'
            'void OnTick(){ MBT_EQ_Sample(); MBT_EQ_OriginalTick(); MBT_EQ_Sample(); }\n'
            f'void OnDeinit(const int reason){{ MBT_EQ_Sample(); {final_call} MBT_EQ_Close(); }}\n')


def parse_equity_capture(path: Path, *, expected_sha256: str, run_id: str,
                         source_sha256: str, symbol: str, timeframe: str,
                         model: int, from_date: str, to_date: str) -> dict:
    """Validate run-bound measured points. Dates are ISO dates, end exclusive."""
    _identity(run_id, source_sha256, symbol, timeframe, model)
    start, stop = datetime.fromisoformat(from_date), datetime.fromisoformat(to_date)
    if start >= stop or start.tzinfo or stop.tzinfo:
        raise ValueError('Invalid capture date bounds')
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('Missing or oversized equity capture')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Equity capture artifact hash mismatch')
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        if next(reader, None) != HEADER:
            raise ValueError('Unsupported equity capture schema')
        points = []
        footer = False
        for row in reader:
            if row and row[0] == 'END':
                if len(row) != 3 or int(row[1]) != len(points) or row[2] != 'complete':
                    raise ValueError('Equity recorder incomplete or truncated')
                footer = True
                if next(reader, None) is not None:
                    raise ValueError('Unexpected equity data after footer')
                break
            if len(row) != len(HEADER) or row[:5] != [run_id, source_sha256, symbol, timeframe, str(model)]:
                raise ValueError('Equity capture does not match requested run')
            if int(row[5]) != len(points) or len(points) >= MAX_POINTS:
                raise ValueError('Invalid equity sequence or excessive observations')
            stamp = datetime.strptime(row[6], '%Y.%m.%d %H:%M:%S')
            balance, equity = float(row[7]), float(row[8])
            if not all(math.isfinite(v) for v in (balance, equity)):
                raise ValueError('Nonfinite equity observation')
            if not start <= stamp < stop or (points and stamp < points[-1][0]):
                raise ValueError('Equity time outside period or out of order')
            points.append((stamp, balance, equity))
        if not footer or len(points) < 2:
            raise ValueError('Equity recorder has no complete usable observations')
    except (csv.Error, UnicodeError, OverflowError, StopIteration) as exc:
        raise ValueError('Malformed equity capture') from exc
    return {'points': points, 'sha256': expected_sha256, 'point_count': len(points),
            'source': 'instrumented_ea_account_info', 'sampling': 'before_and_after_main_symbol_OnTick',
            'model': model, 'limitations': 'Observed tester-event equity, not every hidden price movement; open-price models are sparse. Recorder shutdown alone does not prove test completion.'}
