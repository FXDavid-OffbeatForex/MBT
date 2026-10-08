"""Bounded fixed-cash resampling of verified, fully closed native MT5 trades."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import platform
from pathlib import Path
import random
import re
from uuid import uuid4

from .commission_profile import _HistoryParser
from . import optimization as opt
from .optimization_holdout import (_candidate_hash, _freeze_candidates,
                                   _verify_period_report_identity)
from .optimization_results import parse_optimization_xml
from .terminal_lock import terminal_lock
from .tester import parse_tester_report

VERSION = 1
MAX_EVENTS = 2_000_000


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value.replace(' ', '').replace(',', ''))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError('Invalid numeric native deal value') from exc
    if not result.is_finite() or abs(result) > Decimal('1000000000000'):
        raise ValueError('Non-finite or oversized native deal value')
    return result


def _money(value: str) -> int:
    number = _decimal(value)
    if number != number.quantize(Decimal('.01')):
        raise ValueError('Monte Carlo currently requires two-decimal cash values')
    return int(number * 100)


def read_trade_blocks(path: Path, symbol: str) -> dict:
    """Keep entry/exit cash events together; refuse ambiguous trade grouping."""
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('Detailed MT5 report is missing or oversized')
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        markup = raw.decode('utf-16')
    else:
        try:
            markup = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            markup = raw.decode('cp1252')
    parser = _HistoryParser()
    parser.feed(markup)
    parser.close()
    headers = [(i, table, row) for i, (table, row) in enumerate(parser.rows)
               if row[:5] == ['Time', 'Deal', 'Symbol', 'Type', 'Direction']]
    if len(headers) != 1:
        raise ValueError('Exactly one native deal history is required')
    position, table, header = headers[0]
    standard = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
                'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']
    if header not in (standard, standard[:9] + ['Fee'] + standard[9:]):
        raise ValueError('Unsupported native deal-history columns')
    blocks, current, seen = [], [], set()
    deposit = balance = None
    latest = None
    volume = Decimal(0)
    side = None
    exit_deals = 0
    first_trade = last_trade = None
    for table_id, cells in parser.rows[position + 1:]:
        if table_id != table:
            break
        if not cells:
            continue
        if not cells[0]:
            if len(cells) not in (1, 6 if len(header) == 13 else 7):
                raise ValueError('Native deal has missing timestamp')
            continue
        if len(cells) != len(header) or len(seen) >= 100000:
            raise ValueError('Malformed or oversized native deal history')
        row = dict(zip(header, cells))
        try:
            stamp = datetime.strptime(row['Time'], '%Y.%m.%d %H:%M:%S')
        except ValueError as exc:
            raise ValueError('Invalid native deal timestamp') from exc
        if latest is not None and stamp < latest:
            raise ValueError('Deal history is not chronological')
        latest = stamp
        if not row['Deal'].isdigit() or row['Deal'] in seen:
            raise ValueError('Missing or duplicate native deal identity')
        seen.add(row['Deal'])
        cash = sum(_money(row.get(key, '0')) for key in ('Profit', 'Commission', 'Swap', 'Fee'))
        recorded = _money(row['Balance'])
        if row['Type'].lower() == 'balance':
            if (deposit is not None or row['Symbol'] or row['Direction'] or cash <= 0
                    or any(_money(row.get(k, '0')) for k in ('Commission', 'Swap', 'Fee'))
                    or cash != recorded):
                raise ValueError('Only one initial deposit is supported; no transfers')
            deposit = balance = recorded
            continue
        if deposit is None or row['Symbol'] != symbol or row['Type'].lower() not in ('buy', 'sell'):
            raise ValueError('Unsupported deal or additional trading symbol')
        first_trade = first_trade or stamp
        last_trade = stamp
        if recorded != balance + cash:
            raise ValueError('Deal cash/costs do not reconcile with recorded balance')
        balance = recorded
        amount = _decimal(row['Volume'])
        if amount <= 0:
            raise ValueError('Invalid native deal volume')
        direction = row['Direction'].lower()
        if direction == 'in':
            if volume:
                raise ValueError('Overlapping or scale-in trades are not supported')
            volume, side = amount, row['Type'].lower()
            current = [cash]
        elif direction == 'out':
            if not volume or amount > volume or row['Type'].lower() == side:
                raise ValueError('Exit cannot be assigned to a single open trade')
            volume -= amount
            exit_deals += 1
            current.append(cash)
            if not volume:
                blocks.append(current)
                current, side = [], None
        else:
            raise ValueError('Reversals and Close By deals are not supported')
    if volume or current:
        raise ValueError('Open trades remain at the end of this report')
    if deposit is None or len(blocks) < 5:
        raise ValueError('Monte Carlo requires at least five fully closed trades')
    return {'initial_cents': deposit, 'blocks': blocks, 'net_profit_cents': balance - deposit,
            'trade_count': len(blocks), 'exit_deal_count': exit_deals, 'deal_count': sum(map(len, blocks)),
            'first_trade_date': first_trade.date().isoformat(), 'last_trade_date': last_trade.date().isoformat(),
            'report_sha256': hashlib.sha256(raw).hexdigest()}


def _quantiles(values: list) -> dict:
    ordered = sorted(values)
    def at(q):
        index = (len(ordered) - 1) * q
        low = int(index)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 6)
    return {label: at(q) for label, q in [('p05', .05), ('p50', .5), ('p95', .95)]}


def _histogram(values: list) -> dict:
    low, high = min(values), max(values)
    if low == high:
        return {'edges': [low, high], 'counts': [len(values)]}
    width = (high - low) / 20
    counts = [0] * 20
    for value in values:
        counts[min(int((value - low) / width), 19)] += 1
    return {'edges': [low + i * width for i in range(21)], 'counts': counts}


def simulate(history: dict, method: str, simulations: int, seed: int) -> dict:
    if method not in ('shuffle', 'bootstrap'):
        raise ValueError('method must be shuffle or bootstrap')
    if type(simulations) is not int or not 100 <= simulations <= 5000:
        raise ValueError('simulations must be an integer 100..5000')
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError('seed must be an integer 0..4294967295')
    blocks = history['blocks']
    count = len(blocks)
    if simulations * count * max(map(len, blocks)) > MAX_EVENTS:
        raise ValueError('Simulation exceeds 2000000 deal-event budget; reduce simulations')
    checkpoints = sorted({round(i * count / min(count, 100)) for i in range(min(count, 100) + 1)})
    samples = {i: [] for i in checkpoints}
    finals, drawdowns, percentages = [], [], []
    crosses = 0
    rng = random.Random(seed)
    initial = history['initial_cents']
    def measure(sequence, collect=False):
        balance = peak = initial
        dd, pct = 0, 0.0
        crossed = False
        if collect:
            samples[0].append(balance / 100)
        for i, block in enumerate(sequence, 1):
            for cash in block:
                balance += cash
                peak = max(peak, balance)
                dd = max(dd, peak - balance)
                pct = max(pct, (peak - balance) / peak * 100)
                crossed |= balance <= 0
            if collect and i in samples:
                samples[i].append(balance / 100)
        return (balance - initial) / 100, dd / 100, pct, crossed
    observed = measure(blocks)
    for _ in range(simulations):
        if method == 'shuffle':
            sequence = list(blocks)
            rng.shuffle(sequence)
        else:
            sequence = [blocks[rng.randrange(count)] for _ in range(count)]
        profit, dd, pct, crossed = measure(sequence, True)
        finals.append(profit)
        drawdowns.append(dd)
        percentages.append(pct)
        crosses += crossed
    warnings = [
        'Fixed recorded cash sizes and costs; adaptive sizing and percentage-risk compounding are not rerun.',
        'Synthetic balance paths, not intratrade equity or future-profit forecasts.',
        'Exchangeable-trade assumption; serial dependence and market regimes are not modeled.',
        'Resampling does not remove selection/overfitting bias or establish strategy robustness.',
        'Pointwise bands are not a realizable path or confidence limits on future performance.',
        'Crossing zero is a synthetic cash-path event, not a broker stopout probability.',
    ]
    if count < 30:
        warnings.insert(0, f'Only {count} closed trades: very small sample, exploratory diagnostics only.')
    if method == 'shuffle':
        warnings.append('Shuffle preserves every trade: final profit is constant; drawdown can change.')
    return {'version': VERSION, 'python_version': platform.python_version(),
            'rng': 'Python random.Random / MT19937', 'quantile_interpolation': 'linear',
            'method': method, 'simulations': simulations, 'seed': seed,
            'sizing_model': 'fixed_recorded_cash', 'trade_count': count,
            'deal_count': history['deal_count'], 'initial_balance': initial / 100,
            'observed': {'net_profit': observed[0], 'max_balance_dd': observed[1],
                         'max_balance_dd_pct': observed[2]},
            'terminal_net_profit': _quantiles(finals), 'max_balance_dd': _quantiles(drawdowns),
            'max_balance_dd_pct': _quantiles(percentages),
            'sampled_loss_fraction': sum(p < 0 for p in finals) / simulations,
            'synthetic_zero_crossing_fraction': crosses / simulations,
            'balance_bands': [{'closed_trades': i, **_quantiles(samples[i])} for i in checkpoints],
            'drawdown_histogram': _histogram(drawdowns), 'profit_histogram': _histogram(finals),
            'warnings': warnings}


def _candidate(manifest: dict, result: dict, folder: Path, pass_id: int | None, period: str):
    if manifest.get('run_id') != folder.name or Path(manifest['run_dir']).resolve() != folder:
        raise ValueError('Run identity does not match saved folder')
    xml = Path(manifest['optimization_path']).resolve()
    if xml.parent != folder or opt._hash(xml) != manifest['optimization_sha256']:
        raise ValueError('Saved optimization evidence changed')
    parsed = parse_optimization_xml(xml)
    opt._validate_observed(manifest, parsed)
    if parsed != result['optimization']:
        raise ValueError('Saved rows differ from optimization XML')
    selection = result.get('holdout_selection')
    candidates = result.get('holdout_candidates') or []
    if not selection or not candidates:
        raise ValueError('Monte Carlo requires a frozen candidate with detailed reports')
    expected, _ = _freeze_candidates(manifest, result, selection['requested_top_n'])
    if (_candidate_hash(candidates) != selection['candidates_sha256']
            or expected['candidates_sha256'] != selection['candidates_sha256']):
        raise ValueError('Frozen candidate selection changed')
    chosen = result['selected_pass']['Pass'] if pass_id is None else pass_id
    matches = [c for c in candidates if c['optimization_pass']['Pass'] == chosen]
    if len(matches) != 1:
        raise ValueError('Requested pass is not a unique frozen candidate')
    candidate = matches[0]
    detail = candidate.get(period)
    if not detail or detail.get('status') != 'completed' or not detail.get('report_identity_verified'):
        raise ValueError('Requested period has no verified detailed report; no period fallback')
    report = Path(detail['report_path']).resolve()
    if report.parent != folder or opt._hash(report) != detail['report_sha256']:
        raise ValueError('Detailed candidate report changed or is outside run folder')
    # Historical analysis needs no live template/source/binary. Check saved account
    # identity but obtain all actual cash costs from the immutable detailed report.
    historical = copy.deepcopy(manifest)
    historical['request']['testing'] = opt.testing_settings({
        k: v for k, v in (manifest['request'].get('testing') or {}).items()
        if k in ('deposit', 'currency', 'leverage', 'execution_delay_ms')})
    _verify_period_report_identity(report, historical, candidate['optimization_pass'],
                                   detail['from_date'], detail['to_date'])
    history = read_trade_blocks(report, manifest['request']['symbol'])
    if history['report_sha256'] != detail['report_sha256']:
        raise ValueError('Detailed report changed while reading trade history')
    if not detail['from_date'] <= history['first_trade_date'] <= history['last_trade_date'] <= detail['to_date']:
        raise ValueError('Deal dates fall outside the requested report period')
    profit = detail.get('metrics', {}).get('net_profit')
    trades = detail.get('metrics', {}).get('total_trades')
    native_metrics = parse_tester_report(str(report))
    if (type(profit) not in (int, float) or not math.isfinite(profit)
            or abs(history['net_profit_cents'] / 100 - profit) > .011
            or trades not in (history['trade_count'], history['exit_deal_count'])
            or native_metrics.get('net_profit') != profit or native_metrics.get('total_trades') != trades
            or Decimal(history['initial_cents']) / 100 != Decimal(str(historical['request']['testing']['deposit']))):
        raise ValueError('Closed trade history differs from verified report totals')
    provenance = {'run_id': manifest['run_id'], 'pass_id': chosen, 'period': period,
                  'from_date': detail['from_date'], 'to_date': detail['to_date'],
                  'symbol': manifest['request']['symbol'],
                  'currency': historical['request']['testing'].get('currency', 'USD'),
                  'report_path': str(report), 'report_sha256': detail['report_sha256'],
                  'optimization_sha256': manifest['optimization_sha256'],
                  'candidates_sha256': selection['candidates_sha256'],
                  'parameters': candidate['parameters']}
    return history, provenance


def run_monte_carlo(run_id: str, pass_id: int | None = None, period: str = 'holdout',
                    method: str = 'shuffle', simulations: int = 1000, seed: int = 42,
                    html_report: bool = True) -> dict:
    if period not in ('in_sample', 'holdout'):
        raise ValueError('period must be in_sample or holdout; periods are never pooled')
    if pass_id is not None and type(pass_id) is not int:
        raise ValueError('pass_id must be an integer or null')
    if type(html_report) is not bool:
        raise ValueError('html_report must be boolean')
    folder = opt._run_dir(run_id).resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    # Shared with candidate/equity writers: no lost result updates, no MT5 launch.
    with terminal_lock(manifest['terminal_path'], manifest['data_dir']):
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
        if len(results.get('monte_carlo') or []) >= 100:
            raise ValueError('Run already contains 100 analyses; start a separate experiment')
        history, provenance = _candidate(manifest, results, folder, pass_id, period)
        analysis = simulate(history, method, simulations, seed)
        analysis.update(provenance=provenance, created_at=datetime.now(timezone.utc).isoformat())
        identity = 'mc_' + uuid4().hex
        path = folder / (identity + '.json')
        opt._write_json(path, analysis)
        reference = {'analysis_id': identity, 'path': str(path), 'sha256': opt._hash(path),
                     'pass_id': provenance['pass_id'], 'period': period, 'method': method,
                     'simulations': simulations, 'seed': seed}
        analyses = results.setdefault('monte_carlo', [])
        analyses.append(reference)
        opt._write_json(folder / 'results.json', results)
    output = {'run_id': run_id, **reference, 'summary': {k: analysis[k] for k in (
        'trade_count', 'observed', 'terminal_net_profit', 'max_balance_dd', 'max_balance_dd_pct',
        'sampled_loss_fraction', 'synthetic_zero_crossing_fraction', 'warnings')}, 'report_html': None}
    if html_report:
        try:
            output['report_html'] = opt.render_experiment_report(run_id)['report_html']
        except Exception as exc:
            output['report_error'] = str(exc)
    return output


def load_analysis(reference: dict, run: dict, results: dict, folder: Path) -> dict:
    """Verify saved analysis and original evidence before displaying its charts."""
    path = Path(reference['path']).resolve()
    if (path.parent != folder or not re.fullmatch(r'mc_[0-9a-f]{32}\.json', path.name)
            or path.stem != reference['analysis_id']
            or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024
            or opt._hash(path) != reference['sha256']):
        raise ValueError('Monte Carlo artifact is missing, changed or outside run folder')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('Monte Carlo artifact changed while reading')
    analysis = json.loads(raw.decode('utf-8'))
    _, provenance = _candidate(run, results, folder, reference['pass_id'], reference['period'])
    if (analysis['version'] != VERSION or analysis['provenance'] != provenance
            or any(analysis[k] != reference[k] for k in ('method', 'simulations', 'seed'))):
        raise ValueError('Monte Carlo provenance does not match frozen source')
    return analysis


def get_monte_carlo_results(run_id: str, analysis_id: str) -> dict:
    """Read one stored analysis by ID, validating its original evidence again."""
    if not isinstance(analysis_id, str) or not re.fullmatch(r'mc_[0-9a-f]{32}', analysis_id):
        raise ValueError('Invalid analysis_id')
    folder = opt._run_dir(run_id).resolve()
    run = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    matches = [r for r in results.get('monte_carlo', []) if r['analysis_id'] == analysis_id]
    if len(matches) != 1:
        raise ValueError('Analysis is not uniquely present in this run')
    return {'run_id': run_id, 'analysis_id': analysis_id,
            'analysis': load_analysis(matches[0], run, results, folder)}
