"""Scoped native MT5 tester group profiles and evidence of real deal commissions.

Call profile management/recovery inside MBT's existing terminal_lock. Profiles
are terminal state: a timeout or conflicting edit leaves a durable recovery
journal and prevents subsequent launches until explicit recovery succeeds.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


_ROOT = Path(__file__).resolve().parents[1]
_ENTRY = {'both': 0, 'in': 1, 'out': 2}
_CACHE_MAX_FILES = 128
_CACHE_MAX_BYTES = 512 * 1024 * 1024
_CACHE_EXTENSIONS = {'.opt', '.gen', '.xml'}
_ACCOUNT_KEYS = {
    'CommonUseSettings', 'CommonOrdersLimit', 'CommonPositionsLimit',
    'MarginMode', 'MarginFreeMode', 'MarginSOMode', 'MarginFreeProfitMode',
    'MarginFlags', 'MarginCall', 'MarginStopOut',
}
_COMMISSION_KEYS = {
    'CommissionSymbol', 'CommissionCharge', 'CommissionRange',
    'CommissionEntry', 'CommissionValue', 'CommissionRangeFrom',
    'CommissionRangeTo', 'CommissionMinimal', 'CommissionMaximal',
    'CommissionMode', 'CommissionType', 'CommissionCurrency',
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value).replace(' ', '').replace(',', ''))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'Invalid {label}') from exc
    if isinstance(value, bool) or not result.is_finite():
        raise ValueError(f'Invalid {label}')
    return result


def _owned(path, label: str) -> Path:
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(_ROOT.resolve()) or candidate == _ROOT.resolve():
        raise ValueError(f'{label} must be inside MBT')
    return candidate


def _profile_name(name) -> str:
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_ .()-]{1,180}\.txt', name)
            or name.startswith('.') or name.endswith(' .txt')
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                             *(f'COM{i}' for i in range(1, 10)),
                                             *(f'LPT{i}' for i in range(1, 10))}):
        raise ValueError('profile_name must be a native Groups .txt basename')
    return name


def commission_settings(value: dict | None) -> dict | None:
    """Validate a bounded, deposit-currency, per-lot native commission request."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'per_lot', 'entry', 'profile_name', 'template_path'}:
        raise ValueError('commission requires per_lot, entry, profile_name and template_path')
    if isinstance(value['per_lot'], str) and not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', value['per_lot']):
        raise ValueError('per_lot must be an unambiguous decimal amount')
    rate = _decimal(value['per_lot'], 'per_lot commission')
    if not 0 <= rate <= 1000000 or rate != rate.quantize(Decimal('.0001')):
        raise ValueError('per_lot must be 0..1000000 with at most four decimal places')
    if not isinstance(value['entry'], str) or value['entry'] not in _ENTRY:
        raise ValueError('commission entry must be both, in or out')
    if not isinstance(value['template_path'], (str, Path)):
        raise ValueError('Commission template must be an MBT path')
    template = _owned(value['template_path'], 'Commission template')
    if template.suffix.lower() != '.txt' or not template.is_file():
        raise ValueError('Commission template must be an existing MBT .txt export')
    return {'per_lot': format(rate, '.4f'), 'entry': value['entry'],
            'profile_name': _profile_name(value['profile_name']),
            'template_path': str(template)}


def _read_template(path: Path) -> tuple[bytes, list[str]]:
    if not 0 < path.stat().st_size <= 1024 * 1024:
        raise ValueError('Native commission template is empty or oversized')
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('Unsupported native commission template encoding') from exc
    seen = set()
    preserved = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(';'):
            preserved.append(line)
            continue
        key, sep, val = line.partition('=')
        if not sep or key not in _ACCOUNT_KEYS | _COMMISSION_KEYS:
            raise ValueError('Unsupported field in native commission template')
        if key in _COMMISSION_KEYS:
            continue
        if key in seen:
            raise ValueError('Duplicate native account setting')
        seen.add(key)
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', val):
            raise ValueError('Invalid native account setting')
        preserved.append('CommonUseSettings=1' if key == 'CommonUseSettings' else line)
    if 'CommonUseSettings' not in seen:
        raise ValueError('Template must contain the native CommonUseSettings field')
    return raw, preserved


def native_commission_profile(commission: dict) -> tuple[bytes, str]:
    """Render a native UTF-16LE Advanced Set while retaining account defaults."""
    request = commission_settings(commission)
    if request is None:
        raise ValueError('Commission configuration is required')
    original, lines = _read_template(Path(request['template_path']))
    lines += ['CommissionSymbol=*', 'CommissionCharge=2', 'CommissionRange=0',
              f"CommissionEntry={_ENTRY[request['entry']]}",
              f"CommissionValue={request['per_lot']}",
              'CommissionRangeFrom=0.00', 'CommissionRangeTo=100000000000000000000.00',
              'CommissionMinimal=0.00', 'CommissionMaximal=0.00',
              'CommissionMode=0', 'CommissionType=1']
    return b'\xff\xfe' + ('\r\n'.join(lines) + '\r\n').encode('utf-16-le'), _sha(original)


def _busy() -> bool:
    # Lazy import avoids importing the tester or reading its machine config here.
    from .optimization import _terminal_busy
    return _terminal_busy()


def _journal_path(data: Path) -> Path:
    identity = os.path.normcase(str(Path(data).resolve()))
    return _owned(_ROOT / 'reports' / 'optimization' / 'commission_locks' /
                  (_sha(identity.encode('utf-8')) + '.json'), 'Commission journal')


def assert_no_pending_commission(data: Path) -> None:
    """Required preflight for every MBT launcher, including default-cost runs."""
    if _journal_path(data).exists():
        raise RuntimeError('A native commission profile needs recovery before another MT5 launch')


def _write_bytes(path: Path, raw: bytes, *, exclusive=False) -> None:
    with path.open('xb' if exclusive else 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _save_journal(path: Path, state: dict) -> None:
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    _write_bytes(temporary, json.dumps(state, sort_keys=True, indent=2).encode('utf-8'), exclusive=True)
    os.replace(temporary, path)


def _target(data: Path, name: str) -> Path:
    root = Path(data).resolve()
    groups = root / 'MQL5' / 'Profiles' / 'Tester' / 'Groups'
    if not groups.is_dir() or groups.resolve() != groups:
        raise ValueError('Native tester Groups directory is missing or redirected')
    target = groups / _profile_name(name)
    if target.resolve() != target or (target.exists() and not target.is_file()):
        raise ValueError('Native commission profile target is redirected or invalid')
    return target


def _cache_prefix(value: str) -> str:
    """Validate a complete native cache identity, never a caller-supplied glob."""
    pattern = (r'[A-Za-z0-9_ ()-][A-Za-z0-9_ .()-]{0,119}\.'
               r'[A-Za-z0-9_#&+.-]{1,64}\.'
               r'(?:M(?:1|2|3|4|5|6|10|12|15|20|30)|H(?:1|2|3|4|6|8|12)|D1|W1|MN1)\.'
               r'([0-9]{8})\.([0-9]{8})\.')
    if not isinstance(value, str) or '..' in value or not (match := re.fullmatch(pattern, value)):
        raise ValueError('cache_prefix must identify exact EA, symbol, timeframe and test dates')
    try:
        start, end = (datetime.strptime(item, '%Y%m%d') for item in match.groups())
    except ValueError as exc:
        raise ValueError('cache_prefix contains invalid dates') from exc
    if start >= end:
        raise ValueError('cache_prefix dates must be increasing')
    return value


def _cache_directory(data: Path) -> Path:
    directory = Path(data).resolve() / 'Tester' / 'cache'
    if directory.resolve() != directory or (directory.exists() and not directory.is_dir()):
        raise ValueError('Native optimization cache directory is redirected or invalid')
    return directory


def _cache_name(name: str, prefix: str) -> bool:
    if not isinstance(name, str) or not name.casefold().startswith(prefix.casefold()):
        return False
    tail = name[len(prefix):]
    return (len(name) <= 250 and bool(re.fullmatch(r'[A-Za-z0-9_.-]+', tail))
            and Path(name).suffix.lower() in _CACHE_EXTENSIONS)


def _plain_file(path: Path) -> None:
    if path.resolve() != path or not path.is_file() or path.is_symlink():
        raise RuntimeError('Optimization cache file is missing, redirected or not a regular file')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError('Optimization cache must be a regular file with no hard-link aliases')


def _cache_hash(path: Path) -> str:
    _plain_file(path)
    before = path.stat()
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(block)
            if total > _CACHE_MAX_BYTES:
                raise ValueError('Optimization cache file exceeds isolation size bounds')
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError('Optimization cache changed while its hash was being recorded')
    return digest.hexdigest()


def _cache_inventory(directory: Path, prefix: str) -> list[dict]:
    if not directory.exists():
        return []
    files = []
    total = 0
    for path in sorted(directory.iterdir()):
        if not path.name.casefold().startswith(prefix.casefold()) or path.suffix.lower() not in _CACHE_EXTENSIONS:
            continue
        if not _cache_name(path.name, prefix):
            raise ValueError('Matching optimization cache has an unsupported filename')
        _plain_file(path)
        info = path.stat()
        total += info.st_size
        if len(files) >= _CACHE_MAX_FILES or total > _CACHE_MAX_BYTES:
            raise ValueError('Matching optimization cache exceeds isolation count/size bounds')
        files.append({'name': path.name, 'size': info.st_size,
                      'atime_ns': info.st_atime_ns, 'mtime_ns': info.st_mtime_ns,
                      'mode': stat.S_IMODE(info.st_mode), 'sha256': _cache_hash(path)})
    return files


def _cache_move(source: Path, destination: Path) -> None:
    """Rename exact files without overwriting or changing file identity/metadata."""
    _plain_file(source)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('Optimization cache move destination already exists')
    if destination.parent.resolve() != destination.parent or not destination.parent.is_dir():
        raise RuntimeError('Optimization cache move destination is redirected or missing')
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise RuntimeError('Optimization cache isolation requires a same-filesystem rename')
    source.rename(destination)


def _cache_evidence(state: dict) -> tuple[Path, Path]:
    evidence = _owned(state['evidence_dir'], 'Commission evidence')
    originals = evidence / 'cache_originals'
    generated = evidence / 'cache_generated'
    for directory in (originals, generated):
        if directory.resolve() != directory or not directory.is_dir():
            raise RuntimeError('Optimization cache evidence directory is redirected or missing')
    return originals, generated


def _install_cache(data: Path, state: dict, journal: Path) -> None:
    cache = state.get('cache')
    if cache is None:
        return
    directory = _cache_directory(data)
    originals, _ = _cache_evidence(state)
    for record in cache['originals']:
        source = directory / record['name']
        if _cache_hash(source) != record['sha256']:
            raise RuntimeError('Original optimization cache changed before isolation')
        _cache_move(source, originals / record['name'])
    cache['phase'] = 'isolated'
    _save_journal(journal, state)


def _restore_cache(data: Path, state: dict, journal: Path) -> None:
    cache = state.get('cache')
    if cache is None:
        return
    prefix = _cache_prefix(cache['prefix'])
    directory = _cache_directory(data)
    originals, generated = _cache_evidence(state)
    # Filenames in a durable journal are untrusted until confined again.
    for collection in (cache['originals'], cache['generated']):
        if len(collection) > _CACHE_MAX_FILES or len({r['name'].casefold() for r in collection}) != len(collection):
            raise RuntimeError('Invalid optimization cache recovery inventory')
        for record in collection:
            if not _cache_name(record['name'], prefix):
                raise RuntimeError('Optimization cache recovery filename is outside its exact prefix')
    preserved_active = set()
    for record in cache['originals']:
        saved = originals / record['name']
        active = directory / record['name']
        if saved.exists() or saved.is_symlink():
            if _cache_hash(saved) != record['sha256']:
                raise RuntimeError('Preserved original optimization cache failed integrity verification')
        elif active.is_file() and _cache_hash(active) == record['sha256']:
            # A crash before isolation or after restoration: keep this original.
            preserved_active.add(record['name'].casefold())
        else:
            raise RuntimeError('Original optimization cache is missing or changed; recovery remains pending')
    active_files = _cache_inventory(directory, prefix)
    planned = {record['name'].casefold(): record for record in cache['generated']}
    for record in active_files:
        key = record['name'].casefold()
        if key in preserved_active:
            continue
        if key in planned:
            if planned[key]['sha256'] != record['sha256']:
                raise RuntimeError('Generated optimization cache changed during recovery')
        else:
            if len(planned) >= _CACHE_MAX_FILES:
                raise RuntimeError('Generated optimization cache exceeds recovery count bounds')
            cache['generated'].append(record)
            planned[key] = record
    # Plan every archive destination/hash before any generated file is moved.
    cache['phase'] = 'archiving'
    _save_journal(journal, state)
    for record in cache['generated']:
        archived = generated / record['name']
        active = directory / record['name']
        if archived.exists() or archived.is_symlink():
            if _cache_hash(archived) != record['sha256']:
                raise RuntimeError('Archived optimization cache failed integrity verification')
            if active.exists() and record['name'].casefold() not in preserved_active:
                raise RuntimeError('Optimization cache archive conflicts with a new active file')
        else:
            if _cache_hash(active) != record['sha256']:
                raise RuntimeError('Generated optimization cache failed integrity verification')
            _cache_move(active, archived)
    cache['phase'] = 'restoring_originals'
    _save_journal(journal, state)
    for record in cache['originals']:
        saved = originals / record['name']
        active = directory / record['name']
        if saved.exists():
            if not directory.is_dir():
                raise RuntimeError('Original optimization cache directory disappeared during recovery')
            _cache_move(saved, active)
        if _cache_hash(active) != record['sha256']:
            raise RuntimeError('Restored optimization cache failed integrity verification')
        os.utime(active, ns=(record['atime_ns'], record['mtime_ns']))
        if stat.S_IMODE(active.stat().st_mode) != record['mode']:
            raise RuntimeError('Restored optimization cache file permissions changed')
    cache['phase'] = 'restored'
    _save_journal(journal, state)


def _restore(data: Path, state: dict, journal: Path) -> dict:
    if state.get('data_dir') != str(Path(data).resolve()):
        raise RuntimeError('Commission recovery journal has a different terminal identity')
    target = _target(data, state['request']['profile_name'])
    if state.get('profile_path') != str(target):
        raise RuntimeError('Commission recovery target does not match its journal')
    evidence = _owned(state['evidence_dir'], 'Commission evidence')
    if not evidence.is_dir():
        raise RuntimeError('Commission recovery evidence directory is missing')
    if _busy():
        state['status'] = 'restore_pending'
        _save_journal(journal, state)
        raise RuntimeError('MT5 is still running; native commission restore remains pending')
    expected = state['profile_sha256']
    prior = state['original_sha256']
    current = _sha(target.read_bytes()) if target.is_file() else None
    if current not in {expected, prior}:
        state['status'] = 'restore_conflict'
        _save_journal(journal, state)
        raise RuntimeError('Native commission profile changed externally; recovery will not overwrite it')
    _restore_cache(data, state, journal)
    if state['original_exists']:
        backup = _owned(evidence / 'original.bin', 'Commission backup')
        if backup.parent != evidence:
            raise RuntimeError('Commission backup is redirected')
        if not backup.is_file() or _sha(backup.read_bytes()) != prior:
            raise RuntimeError('Native commission profile backup failed integrity verification')
        if current != prior:
            _write_bytes(target, backup.read_bytes())
    elif current is not None:
        target.unlink()  # Exactly the new profile recorded by this transaction.
    state['status'] = 'restored'
    _save_journal(evidence / 'lifecycle.json', state)
    journal.unlink()
    return state


def restore_commission_profile(data: Path) -> dict:
    """Explicit recovery, only while the caller holds the existing terminal lock."""
    journal = _journal_path(data)
    if not journal.is_file():
        raise ValueError('No pending native commission profile recovery')
    if journal.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Commission recovery journal is oversized')
    state = json.loads(journal.read_text(encoding='utf-8'))
    return _restore(data, state, journal)


@contextmanager
def managed_commission_profile(data: Path, commission: dict | None, run_folder: Path,
                               *, cache_prefix: str | None = None):
    """Install and restore a native profile; caller owns terminal_lock throughout."""
    assert_no_pending_commission(data)
    request = commission_settings(commission)
    if request is None:
        if cache_prefix is not None:
            raise ValueError('Optimization cache isolation requires a native commission profile')
        yield None
        return
    if _busy():
        raise RuntimeError('Close MT5 before installing a native commission profile')
    target = _target(data, request['profile_name'])
    folder = _owned(run_folder, 'Commission run folder')
    if not folder.is_dir():
        raise ValueError('Commission run folder must already exist')
    profile, template_hash = native_commission_profile(request)
    original = target.read_bytes() if target.exists() else None
    if original is not None and len(original) > 1024 * 1024:
        raise ValueError('Existing native commission profile is oversized')
    cache = None
    if cache_prefix is not None:
        prefix = _cache_prefix(cache_prefix)
        directory = _cache_directory(data)
        cache = {'prefix': prefix, 'phase': 'prepared',
                 'originals': _cache_inventory(directory, prefix), 'generated': []}
    evidence = folder / ('commission_' + uuid4().hex)
    evidence.mkdir(exist_ok=False)
    _write_bytes(evidence / 'profile.txt', profile, exclusive=True)
    if original is not None:
        _write_bytes(evidence / 'original.bin', original, exclusive=True)
    if cache is not None:
        (evidence / 'cache_originals').mkdir()
        (evidence / 'cache_generated').mkdir()
    state = {'status': 'prepared', 'request': request, 'data_dir': str(Path(data).resolve()),
             'profile_path': str(target), 'profile_sha256': _sha(profile),
             'template_sha256': template_hash, 'evidence_dir': str(evidence),
             'original_exists': original is not None,
             'original_sha256': _sha(original) if original is not None else None}
    if cache is not None:
        state['cache'] = cache
    journal = _journal_path(data)
    journal.parent.mkdir(parents=True, exist_ok=True)
    # Durable before mutation; a crash leaves a recoverable prepared transaction.
    _save_journal(journal, state)
    try:
        if _busy():
            raise RuntimeError('MT5 became busy before native commission profile installation')
        _install_cache(data, state, journal)
        _write_bytes(target, profile)
        state['status'] = 'installed'
        _save_journal(journal, state)
        _save_journal(evidence / 'lifecycle.json', state)
        yield state
    finally:
        _restore(data, state, journal)


def verify_commission_log(log_text: str, evidence: dict) -> dict:
    """Verify the exact applied native profile and its preserved artifact hash."""
    name = _profile_name(evidence['request']['profile_name'])
    expected = f"custom group settings applied from file 'MQL5\\Profiles\\Tester\\Groups\\{name}'"
    if expected not in log_text:
        raise ValueError('MT5 did not confirm applying the requested native commission profile')
    artifact = _owned(Path(evidence['evidence_dir']) / 'profile.txt', 'Native profile artifact')
    if not artifact.is_file() or _sha(artifact.read_bytes()) != evidence['profile_sha256']:
        raise ValueError('Native commission profile artifact failed integrity verification')
    return {'native_profile_applied': True, 'profile_sha256': evidence['profile_sha256'],
            'profile_name': name}


class _HistoryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table_id = 0
        self.row = None
        self.cell = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.table_id += 1
            self.tables.append(self.table_id)
        elif tag == 'tr':
            self.row = []
        elif tag in {'td', 'th'} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {'td', 'th'} and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append((self.tables[-1] if self.tables else 0, self.row))
            self.row = None
        elif tag == 'table' and self.tables:
            self.tables.pop()


def verify_commission_report(path: Path, commission: dict, symbol: str) -> dict:
    """Check every native trading deal, without inferring costs from profit totals.

    The first slice accepts two-decimal deposit-currency reports, ordinary
    entry/exit deals in one symbol, and a single initial deposit. Reversals,
    Close By, separate fee operations and ambiguous tables fail closed.
    """
    request = commission_settings(commission)
    if request is None:
        raise ValueError('Commission configuration is required')
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('Native MT5 commission report is missing or oversized')
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    parser = _HistoryParser()
    parser.feed(text)
    parser.close()
    headers = [(idx, table, row) for idx, (table, row) in enumerate(parser.rows)
               if row[:5] == ['Time', 'Deal', 'Symbol', 'Type', 'Direction']]
    if len(headers) != 1:
        raise ValueError('Report must contain exactly one native deal history table')
    position, table_id, header = headers[0]
    expected_header = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume',
                       'Price', 'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']
    fee_header = expected_header[:9] + ['Fee'] + expected_header[9:]
    if header not in (expected_header, fee_header):
        raise ValueError('Unsupported native deal history columns')
    rate = Decimal(request['per_lot'])
    quantum = Decimal('.01')
    seen_ids = set()
    latest = None
    deals = charged = deposits = 0
    total = Decimal(0)
    for row_table, cells in parser.rows[position + 1:]:
        if row_table != table_id:
            break
        if not cells:
            continue
        if not cells[0]:
            if len(cells) not in (1, 6 if header == expected_header else 7):
                raise ValueError('Native trading deal is missing its timestamp')
            if len(cells) > 1:
                for amount in cells[1:-1]:
                    _decimal(amount, 'native deal totals footer')
            continue  # Native total footer and spacer have no timestamp.
        if len(cells) != len(header):
            raise ValueError('Malformed native deal history row')
        row = dict(zip(header, cells))
        try:
            stamp = datetime.strptime(row['Time'], '%Y.%m.%d %H:%M:%S')
        except ValueError as exc:
            raise ValueError('Invalid native deal timestamp') from exc
        if latest is not None and stamp < latest:
            raise ValueError('Native deal history is not chronological')
        latest = stamp
        identity = row['Deal']
        if not identity.isdigit() or identity in seen_ids:
            raise ValueError('Missing or duplicate native deal identity')
        seen_ids.add(identity)
        actual = _decimal(row['Commission'], 'native deal commission')
        if actual != actual.quantize(quantum):
            raise ValueError('Only two-decimal native commission reports are supported')
        fee = _decimal(row.get('Fee', '0'), 'native deal fee')
        if fee != 0:
            raise ValueError('Additional native deal fees are outside this commission contract')
        if row['Type'].lower() == 'balance':
            if deposits or deals or actual or row['Symbol'] or row['Direction'] or _decimal(row['Profit'], 'deposit') <= 0:
                raise ValueError('Unsupported balance operation in native commission report')
            deposits += 1
            continue
        direction = row['Direction'].lower()
        if row['Type'].lower() not in {'buy', 'sell'} or direction not in {'in', 'out'}:
            raise ValueError('Unsupported native deal type or direction')
        if row['Symbol'] != symbol:
            raise ValueError('Commission proof does not support additional trading symbols')
        volume = _decimal(row['Volume'], 'native deal volume')
        if not 0 < volume <= 100000000:
            raise ValueError('Invalid native deal volume')
        applies = request['entry'] == 'both' or request['entry'] == direction
        expected = -(rate * volume).quantize(quantum, rounding=ROUND_HALF_UP) if applies else Decimal(0)
        if actual != expected:
            raise ValueError(f'Native commission mismatch on deal {identity}: expected {expected}, observed {actual}')
        deals += 1
        charged += actual != 0
        total += actual
    if deposits != 1:
        raise ValueError('Report has no verifiable native trading deals and initial deposit')
    return {'verified': bool(deals), 'status': 'verified' if deals else 'no_trading_deals',
            'native_deals_verified': deals, 'charged_deals': charged,
            'commission_total': float(total), 'per_lot': request['per_lot'],
            'entry': request['entry'], 'symbol': symbol, 'currency_digits': 2,
            'report_sha256': _sha(raw)}
