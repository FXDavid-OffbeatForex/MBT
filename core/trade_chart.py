"""Display saved native tester deals on an MT5 chart, without running the EA."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from . import tester
from .compiler import compile_mql5
from .report_balance_curve import _DealTable
from .report_single_html import _Rows
from .terminal_lock import terminal_lock

ROOT = Path(__file__).resolve().parents[1]


def _saved_report(report_path, run_id, pass_id, period):
    if bool(report_path) == bool(run_id):
        raise ValueError('Specify either a standalone native report_path or optimization run_id')
    if report_path:
        if pass_id is not None:
            raise ValueError('pass_id requires run_id')
        path = Path(report_path).resolve()
        if not path.is_relative_to((ROOT / 'reports').resolve()) or path.suffix.lower() not in ('.htm', '.html'):
            raise ValueError('Report must be native MT5 HTML inside MBT reports')
        return path, None
    if period not in ('in_sample', 'holdout'):
        raise ValueError('period must be in_sample or holdout')
    if pass_id is not None and (type(pass_id) is not int or pass_id < 0):
        raise ValueError('pass_id must be a nonnegative integer')
    from .optimization import _run_dir
    folder = _run_dir(run_id)
    results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    selected = (results.get('selected_pass') or {}).get('Pass') if pass_id is None else pass_id
    detail = None
    for candidate in results.get('holdout_candidates') or []:
        if str((candidate.get('optimization_pass') or {}).get('Pass')) == str(selected):
            detail = candidate.get(period)
            break
    if detail is None and str(selected) == str((results.get('selected_pass') or {}).get('Pass')):
        detail = results.get(period)
    if not detail or detail.get('status') != 'completed' or not detail.get('report_identity_verified'):
        raise ValueError('Candidate needs a completed detailed single-test report; summary rows cannot show trades')
    path = Path(detail['report_path']).resolve()
    if path.parent != folder.resolve():
        raise ValueError('Candidate report is outside its saved run')
    return path, detail['report_sha256']


def _read_deals(path, expected_hash=None):
    if not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('Native report missing or oversized')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_hash and digest != expected_hash:
        raise ValueError('Saved candidate report hash mismatch')
    markup = tester._read_text_any(str(path))
    parser = _Rows()
    parser.feed(markup)
    identity = {}
    for row in parser.rows:
        cells = [c for c in row if c]
        for i, value in enumerate(cells[:-1]):
            if value in ('Expert:', 'Symbol:', 'Period:'):
                identity[value[:-1]] = cells[i+1]
    symbol = identity.get('Symbol', '')
    period = identity.get('Period', '').split(' ', 1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', symbol) or period not in set(tester._PERIOD_MAP.values()):
        raise ValueError('Unsupported or missing native symbol/timeframe')
    if not identity.get('Expert'):
        raise ValueError('Native EA identity missing')
    table = _DealTable()
    table.feed(markup)
    if not table.header_found:
        raise ValueError('Native deal table unavailable')
    deals = []
    previous = None
    for row in table.rows:
        if row[3].lower() not in ('buy', 'sell'):
            continue
        if row[2] != symbol:
            raise ValueError('Multi-symbol deal overlays are not supported by this single-chart tool')
        stamp = datetime.strptime(row[0], '%Y.%m.%d %H:%M:%S')
        price = float(row[6].replace(' ', '').replace(',', ''))
        if not math.isfinite(price) or price <= 0 or (previous and stamp < previous):
            raise ValueError('Invalid price or chronological deal order')
        previous = stamp
        label = f'Deal {row[1]} / {row[3]} / {row[4]} / volume {row[5]} / {row[0]} / price {row[6]}'
        deals.append((stamp, price, row[3].lower() == 'buy', label))
    if not 1 <= len(deals) <= 5000:
        raise ValueError('Chart display requires 1..5000 recorded trading deals')
    return identity, deals, digest


def _mql_string(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', ' ').replace('\n', ' ') + '"'


def open_trade_chart(report_path=None, run_id=None, pass_id=None, period='in_sample'):
    """Open a new display chart; require a closed terminal and verified saved deal data."""
    path, expected_hash = _saved_report(report_path, run_id, pass_id, period)
    identity, deals, digest = _read_deals(path, expected_hash)
    data = Path(tester._data_dir())
    from .optimization import _terminal_busy
    with terminal_lock(tester._terminal_path(), str(data)):
        if _terminal_busy():
            raise RuntimeError('Close MT5 before opening an MBT trade chart; existing charts will not be taken over')
        token = 'chart_' + uuid.uuid4().hex
        folder = ROOT / 'reports' / 'trade_charts' / token
        folder.mkdir(parents=True)
        script_name = 'MBT_' + token
        source = data / 'MQL5' / 'Scripts' / (script_name + '.mq5')
        source.parent.mkdir(parents=True, exist_ok=True)
        template = (ROOT / 'mql5' / 'MBT_TradeChart.mq5').read_text(encoding='utf-8')
        records = ',\n'.join('{D\'' + stamp.strftime('%Y.%m.%d %H:%M:%S') + '\','
                    + repr(price) + ',' + ('true' if buy else 'false') + ',' + _mql_string(label) + '}'
                    for stamp, price, buy, label in deals)
        label = f'{token} / {identity["Symbol"]} / {identity["Period"]}'.replace('"','').replace('\n',' ')
        text = template.replace('// MBT_DEALS', records).replace('// MBT_LABEL', label)
        text = text.replace('// MBT_SCREENSHOT', token+'.png').replace('// MBT_STATUS', token+'.txt')
        source.write_text(text, encoding='utf-8')
        shutil.copy2(source, folder / source.name)
        compiled = compile_mql5(str(source), timeout=60)
        if not compiled.get('ok') or not compiled.get('ex5'):
            return {'status': 'compile_failed', 'error': compiled, 'chart_id': token}
        ini = folder / 'open_chart.ini'
        ini.write_text('[Experts]\nEnabled=0\nAllowLiveTrading=0\nAllowDllImport=0\n'
            '\n[StartUp]\nExpert=\nScript='+script_name+'\nSymbol='+identity['Symbol']+
            '\nPeriod='+identity['Period'].split(' ',1)[0]+'\nShutdownTerminal=0\n', encoding='utf-8')
        process = subprocess.Popen(tester._launch_cmd(str(ini)), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        status_file = data / 'MQL5' / 'Files' / (token+'.txt')
        deadline = time.monotonic() + 35
        while not status_file.is_file() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.25)
        result = {'chart_id': token, 'status': 'launch_unconfirmed', 'terminal_pid': process.pid,
                  'native_report': str(path), 'report_sha256': digest, 'identity': identity,
                  'expected_deal_markers': len(deals), 'chart_kind': 'saved_test_deal_overlay',
                  'note': 'Display only; terminal stays open. Not native tester playback or live account trades.'}
        if status_file.is_file():
            parts = status_file.read_text(encoding='ascii').strip().split(',')
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                count, positioned, screenshot = map(int, parts)
                result.update(status='displayed' if count == len(deals) and positioned else 'display_partial',
                              drawn_deal_markers=count, navigated_to_history=bool(positioned))
                image = data / 'MQL5' / 'Files' / (token+'.png')
                if screenshot and image.is_file():
                    shutil.copy2(image, folder / 'chart.png')
                    result['screenshot'] = str(folder / 'chart.png')
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
