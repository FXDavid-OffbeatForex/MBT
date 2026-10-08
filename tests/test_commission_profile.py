from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest

from core import commission_profile as cp


ACCOUNT = '''CommonUseSettings=0
CommonOrdersLimit=70
CommonPositionsLimit=0
MarginMode=2
MarginFreeMode=1
MarginSOMode=0
MarginFreeProfitMode=0
MarginFlags=0
MarginCall=60.00
MarginStopOut=50.00
CommissionSymbol=Forex\\*
CommissionCharge=2
CommissionEntry=0
CommissionValue=3.5000
CommissionMode=6
CommissionType=1
CommissionCurrency=USD
CommissionSymbol=Metals\\*
CommissionValue=5.5000
'''


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path / 'MBT'
    root.mkdir()
    monkeypatch.setattr(cp, '_ROOT', root)
    monkeypatch.setattr(cp, '_busy', lambda: False)
    template = root / 'native_account.txt'
    template.write_text(ACCOUNT, encoding='utf-16')
    data = tmp_path / 'terminal_data'
    groups = data / 'MQL5' / 'Profiles' / 'Tester' / 'Groups'
    groups.mkdir(parents=True)
    run = root / 'reports' / 'optimization' / 'opt_commission'
    run.mkdir(parents=True)
    request = {'per_lot': 3.5, 'entry': 'both', 'profile_name': 'Broker_Group.txt',
               'template_path': str(template)}
    return root, data, groups, run, request


def test_native_export_replaces_all_commission_blocks_preserves_account(environment):
    root, data, groups, run, request = environment
    profile, template_hash = cp.native_commission_profile(request)
    text = profile.decode('utf-16')
    assert text.count('CommissionSymbol=') == 1
    assert 'CommissionSymbol=*' in text
    assert 'CommissionCurrency=' not in text
    assert 'CommissionMode=0' in text
    assert 'CommissionEntry=0' in text
    assert 'CommissionValue=3.5000' in text
    assert 'CommissionMinimal=0.00' in text
    assert 'CommonUseSettings=1' in text
    for line in ACCOUNT.splitlines():
        if line and not line.startswith(('Commission', 'CommonUseSettings')):
            assert line in text
    assert template_hash == hashlib.sha256(Path(request['template_path']).read_bytes()).hexdigest()


@pytest.mark.parametrize('updates', [
    {'per_lot': True}, {'per_lot': 'NaN'}, {'per_lot': -1}, {'per_lot': 1000001},
    {'per_lot': '.00001'}, {'per_lot': '3,5'}, {'entry': 'roundtrip'}, {'entry': []},
    {'profile_name': '../broker.txt'}, {'profile_name': 'broker.txt:stream'},
    {'profile_name': 'NUL.txt'}, {'profile_name': 'a\nb.txt'},
    {'template_path': None}, {'other': 1},
])
def test_invalid_requests_rejected(environment, updates):
    *_, request = environment
    with pytest.raises(ValueError):
        cp.commission_settings(dict(request, **updates))


def test_template_must_be_confined_complete_native_export(environment, tmp_path):
    *_, request = environment
    outside = tmp_path / 'outside.txt'
    outside.write_text(ACCOUNT, encoding='utf-16')
    with pytest.raises(ValueError, match='inside MBT'):
        cp.commission_settings(dict(request, template_path=str(outside)))
    template = Path(request['template_path'])
    for text in ('CommissionValue=3.5', ACCOUNT + 'Password=hidden\n',
                 ACCOUNT + 'MarginCall=50.00\n'):
        template.write_text(text, encoding='utf-16')
        with pytest.raises(ValueError):
            cp.native_commission_profile(request)


def test_sparse_native_profile_does_not_invent_account_settings(environment):
    *_, request = environment
    Path(request['template_path']).write_text(
        'CommonUseSettings=1\nCommissionSymbol=Forex\\*\nCommissionValue=3.5000\n',
        encoding='utf-16')
    native, _ = cp.native_commission_profile(request)
    text = native.decode('utf-16')
    assert 'CommonUseSettings=1' in text
    assert 'Margin' not in text
    assert 'CommonOrdersLimit' not in text
    assert text.count('CommissionSymbol=') == 1


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_exact_restore_success_and_exception(environment, existing, fail):
    root, data, groups, run, request = environment
    target = groups / request['profile_name']
    original = b'original bytes\x00\xff'
    if existing:
        target.write_bytes(original)
    other = groups / 'other.txt'
    other.write_bytes(b'unrelated')
    try:
        with cp.managed_commission_profile(data, request, run) as evidence:
            assert 'CommissionValue=3.5000' in target.read_text(encoding='utf-16')
            assert cp._journal_path(data).is_file()
            with pytest.raises(RuntimeError, match='recovery'):
                cp.assert_no_pending_commission(data)
            if fail:
                raise LookupError('owned tester failed')
    except LookupError:
        assert fail
    assert target.read_bytes() == original if existing else not target.exists()
    assert other.read_bytes() == b'unrelated'
    assert not cp._journal_path(data).exists()
    assert evidence['status'] == 'restored'
    saved = json.loads((Path(evidence['evidence_dir']) / 'lifecycle.json').read_text())
    assert saved['status'] == 'restored'


def test_running_terminal_blocks_mutation(environment, monkeypatch):
    root, data, groups, run, request = environment
    monkeypatch.setattr(cp, '_busy', lambda: True)
    with pytest.raises(RuntimeError, match='Close MT5'):
        with cp.managed_commission_profile(data, request, run):
            pytest.fail('must not run')
    assert not list(groups.iterdir())
    assert not list(run.iterdir())


def test_timeout_preserves_profile_and_journal_until_explicit_recovery(environment, monkeypatch):
    root, data, groups, run, request = environment
    target = groups / request['profile_name']
    target.write_bytes(b'original')
    with pytest.raises(RuntimeError, match='restore remains pending'):
        with cp.managed_commission_profile(data, request, run):
            monkeypatch.setattr(cp, '_busy', lambda: True)
    installed = target.read_bytes()
    assert b'original' != installed
    with pytest.raises(RuntimeError, match='recovery'):
        with cp.managed_commission_profile(data, None, run):
            pytest.fail('must block even default-cost launch')
    with pytest.raises(RuntimeError, match='still running'):
        cp.restore_commission_profile(data)
    assert target.read_bytes() == installed
    monkeypatch.setattr(cp, '_busy', lambda: False)
    assert cp.restore_commission_profile(data)['status'] == 'restored'
    assert target.read_bytes() == b'original'


def test_external_profile_edit_is_never_overwritten(environment):
    root, data, groups, run, request = environment
    target = groups / request['profile_name']
    with pytest.raises(RuntimeError, match='changed externally'):
        with cp.managed_commission_profile(data, request, run):
            target.write_bytes(b'user changes')
    assert target.read_bytes() == b'user changes'
    with pytest.raises(RuntimeError, match='changed externally'):
        cp.restore_commission_profile(data)


def test_backup_corruption_blocks_restore(environment, monkeypatch):
    root, data, groups, run, request = environment
    target = groups / request['profile_name']
    target.write_bytes(b'original')
    with pytest.raises(RuntimeError, match='backup failed integrity'):
        with cp.managed_commission_profile(data, request, run) as evidence:
            (Path(evidence['evidence_dir']) / 'original.bin').write_bytes(b'corrupt')
    assert cp._journal_path(data).exists()


def test_crash_after_prepared_journal_before_profile_write_is_recoverable(environment, monkeypatch):
    root, data, groups, run, request = environment
    target = groups / request['profile_name']
    original_write = cp._write_bytes

    def write(path, raw, **kwargs):
        if path == target:
            raise OSError('simulated failure before native write')
        return original_write(path, raw, **kwargs)

    monkeypatch.setattr(cp, '_write_bytes', write)
    with pytest.raises(OSError, match='before native write'):
        with cp.managed_commission_profile(data, request, run):
            pytest.fail('must not run')
    assert not target.exists()
    assert not cp._journal_path(data).exists()


def test_log_requires_exact_profile_filename_and_artifact_hash(environment):
    root, data, groups, run, request = environment
    with cp.managed_commission_profile(data, request, run) as evidence:
        log = "custom group settings applied from file 'MQL5\\Profiles\\Tester\\Groups\\Broker_Group.txt'"
        assert cp.verify_commission_log(log, evidence)['native_profile_applied']
        with pytest.raises(ValueError, match='did not confirm'):
            cp.verify_commission_log(log.replace('Broker_Group', 'Another_Group'), evidence)
        (Path(evidence['evidence_dir']) / 'profile.txt').write_bytes(b'changed')
        with pytest.raises(ValueError, match='integrity'):
            cp.verify_commission_log(log, evidence)


HEADER = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
          'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']


def write_report(folder, rows, *, duplicate=False):
    def tr(cells):
        return '<tr>' + ''.join('<td>' + str(c) + '</td>' for c in cells) + '</tr>'
    deposit = ['2025.05.01 00:00:00', '1', '', 'balance', '', '', '', '',
               '0.00', '0.00', '5000.00', '5000.00', 'initial deposit']
    table = '<table>' + tr(HEADER) + tr(deposit) + ''.join(tr(r) for r in rows) + '</table>'
    report = folder / 'report.htm'
    report.write_text(table + (table if duplicate else ''), encoding='utf-16')
    return report


def deals(entry='both', rate=Decimal('3.5')):
    return [
        ['2025.05.01 10:00:00', '2', 'EURUSD', 'buy', 'in', '0.10', '1.11', '1',
         str(-rate / 10 if entry in {'both', 'in'} else 0), '0.00', '0.00', '4999.65', ''],
        ['2025.05.01 11:00:00', '3', 'EURUSD', 'sell', 'out', '0.10', '1.12', '2',
         str(-rate / 10 if entry in {'both', 'out'} else 0), '0.00', '100.00', '5099.30', ''],
    ]


@pytest.mark.parametrize('entry', ['both', 'in', 'out'])
def test_real_deal_columns_verify_each_requested_side(environment, entry):
    root, data, groups, run, request = environment
    request = dict(request, entry=entry)
    report = write_report(run, deals(entry))
    proof = cp.verify_commission_report(report, request, 'EURUSD')
    assert proof['native_deals_verified'] == 2
    assert proof['charged_deals'] == (2 if entry == 'both' else 1)
    assert proof['commission_total'] == (-.70 if entry == 'both' else -.35)


@pytest.mark.parametrize('column,replacement', [
    (8, '0.00'), (8, '0.35'), (8, '-0.34'), (8, 'NaN'),
    (4, 'in/out'), (4, 'out by'), (2, 'GBPUSD'), (3, 'commission'),
    (5, '0'), (5, 'NaN'), (0, 'bad date'), (0, ''), (1, '1'),
])
def test_native_deal_mismatch_and_unsupported_modes_fail_closed(environment, column, replacement):
    root, data, groups, run, request = environment
    rows = deals()
    rows[0][column] = replacement
    with pytest.raises(ValueError):
        cp.verify_commission_report(write_report(run, rows), request, 'EURUSD')


def test_zero_trade_and_duplicate_history_never_claim_native_proof(environment):
    root, data, groups, run, request = environment
    proof = cp.verify_commission_report(write_report(run, []), request, 'EURUSD')
    assert proof['verified'] is False
    assert proof['status'] == 'no_trading_deals'
    assert proof['native_deals_verified'] == 0
    with pytest.raises(ValueError, match='exactly one'):
        cp.verify_commission_report(write_report(run, deals(), duplicate=True), request, 'EURUSD')


def test_native_half_cent_rounding_is_per_deal(environment):
    root, data, groups, run, request = environment
    rows = deals()
    for row in rows:
        row[5] = '0.01'
        row[8] = '-0.04'
    evidence = cp.verify_commission_report(write_report(run, rows), request, 'EURUSD')
    assert evidence['commission_total'] == -.08


CACHE_PREFIX = 'Pilot.EURUSD.H1.20250501.20250508.'


def make_caches(data, contents):
    directory = data / 'Tester' / 'cache'
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in contents.items():
        (directory / name).write_bytes(raw)
    return directory


def test_matching_caches_isolated_fresh_results_archived_originals_restored(environment):
    root, data, groups, run, request = environment
    names = [CACHE_PREFIX + suffix for suffix in ('03.aaa.opt', '13.bbb.gen', '03.aaa.xml')]
    originals = {name: ('original ' + name).encode() for name in names}
    untouched = {CACHE_PREFIX + '03.aaa.tst': b'single run',
                 'Other.EURUSD.H1.20250501.20250508.03.aaa.opt': b'other strategy',
                 'Pilot.EURUSD.H1.20250502.20250508.03.aaa.opt': b'other dates',
                 'EURUSD.hcc': b'price history'}
    directory = make_caches(data, {**originals, **untouched})
    before = {}
    for name in names:
        os.utime(directory / name, ns=(1500000000123456700, 1600000000765432100))
        before[name] = (directory / name).stat()
    generated_name = CACHE_PREFIX + '03.new.opt'
    with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX) as evidence:
        for name in names:
            assert not (directory / name).exists()
        assert {p.name for p in directory.iterdir()} == set(untouched)
        (directory / names[0]).write_bytes(b'new custom-commission cache')
        (directory / generated_name).write_bytes(b'additional new cache')
        snapshot = json.loads(cp._journal_path(data).read_text())
        assert len(snapshot['cache']['originals']) == 3
        assert snapshot['cache']['phase'] == 'isolated'
    for name, raw in originals.items():
        observed = (directory / name).stat()
        assert observed.st_mtime_ns == before[name].st_mtime_ns
        assert observed.st_atime_ns == before[name].st_atime_ns
        assert observed.st_mode == before[name].st_mode
        assert (directory / name).read_bytes() == raw
    assert not (directory / generated_name).exists()
    archive = Path(evidence['evidence_dir']) / 'cache_generated'
    assert (archive / names[0]).read_bytes() == b'new custom-commission cache'
    assert (archive / generated_name).read_bytes() == b'additional new cache'
    for name, raw in untouched.items():
        assert (directory / name).read_bytes() == raw
    assert evidence['cache']['phase'] == 'restored'
    assert not cp._journal_path(data).exists()


@pytest.mark.parametrize('prefix', ['*', '../', 'Pilot.*.H1.20250501.20250508.',
    'Pilot.EURUSD.H1.20250501.', 'Pilot.EURUSD.H1.20250508.20250501.',
    'Pilot.EURUSD.H1.20250230.20250508.', CACHE_PREFIX + '*',
    'Pilot..EURUSD.H1.20250501.20250508.', 'Pilot/EURUSD.H1.20250501.20250508.'])
def test_unsafe_cache_prefix_rejected_before_profile_or_cache_changes(environment, prefix):
    root, data, groups, run, request = environment
    name = CACHE_PREFIX + '03.aaa.opt'
    directory = make_caches(data, {name: b'original'})
    with pytest.raises(ValueError, match='cache_prefix'):
        with cp.managed_commission_profile(data, request, run, cache_prefix=prefix):
            pytest.fail('unsafe prefix must not run')
    assert (directory / name).read_bytes() == b'original'
    assert not list(groups.iterdir())
    assert not list(run.iterdir())


@pytest.mark.parametrize('create_during_run', [False, True])
def test_missing_cache_directory_is_supported_without_deleting_new_outputs(environment, create_during_run):
    root, data, groups, run, request = environment
    name = CACHE_PREFIX + '03.aaa.opt'
    directory = data / 'Tester' / 'cache'
    assert not directory.exists()
    with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX) as evidence:
        if create_during_run:
            make_caches(data, {name: b'new cache'})
    assert not (directory / name).exists()
    if create_during_run:
        assert (Path(evidence['evidence_dir']) / 'cache_generated' / name).read_bytes() == b'new cache'


@pytest.mark.parametrize('limit', ['files', 'bytes', 'nonfile', 'hardlink'])
def test_cache_bounds_and_nonregular_targets_fail_before_mutation(environment, monkeypatch, limit):
    root, data, groups, run, request = environment
    name = CACHE_PREFIX + '03.aaa.opt'
    directory = make_caches(data, {})
    if limit == 'nonfile':
        (directory / name).mkdir()
    elif limit == 'hardlink':
        other = directory / 'user_original.opt'
        other.write_bytes(b'original')
        os.link(other, directory / name)
    else:
        (directory / name).write_bytes(b'original')
        monkeypatch.setattr(cp, '_CACHE_MAX_FILES' if limit == 'files' else '_CACHE_MAX_BYTES', 0)
    with pytest.raises((ValueError, RuntimeError)):
        with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX):
            pytest.fail('unsafe cache must not run')
    assert (directory / name).exists()
    assert not list(groups.iterdir())


def test_timeout_defers_cache_and_profile_restoration_together(environment, monkeypatch):
    root, data, groups, run, request = environment
    name = CACHE_PREFIX + '03.aaa.opt'
    directory = make_caches(data, {name: b'original'})
    with pytest.raises(RuntimeError, match='restore remains pending'):
        with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX) as evidence:
            (directory / name).write_bytes(b'new cache')
            monkeypatch.setattr(cp, '_busy', lambda: True)
    assert (directory / name).read_bytes() == b'new cache'
    assert (Path(evidence['evidence_dir']) / 'cache_originals' / name).read_bytes() == b'original'
    monkeypatch.setattr(cp, '_busy', lambda: False)
    cp.restore_commission_profile(data)
    assert (directory / name).read_bytes() == b'original'
    assert (Path(evidence['evidence_dir']) / 'cache_generated' / name).read_bytes() == b'new cache'


def test_crash_halfway_through_isolation_restores_moved_and_unmoved_originals(environment, monkeypatch):
    root, data, groups, run, request = environment
    names = [CACHE_PREFIX + '03.a.opt', CACHE_PREFIX + '03.b.gen']
    directory = make_caches(data, {name: name.encode() for name in names})
    move = cp._cache_move
    calls = []

    def fail_after_first_move(source, destination):
        move(source, destination)
        calls.append(source)
        if len(calls) == 1:
            monkeypatch.setattr(cp, '_busy', lambda: True)
            raise OSError('simulated crash during isolation')

    monkeypatch.setattr(cp, '_cache_move', fail_after_first_move)
    with pytest.raises(RuntimeError, match='restore remains pending'):
        with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX):
            pytest.fail('partial installation must not run')
    assert not (directory / names[0]).exists()
    assert (directory / names[1]).exists()
    monkeypatch.setattr(cp, '_busy', lambda: False)
    monkeypatch.setattr(cp, '_cache_move', move)
    cp.restore_commission_profile(data)
    for name in names:
        assert (directory / name).read_bytes() == name.encode()


@pytest.mark.parametrize('failed_stage', ['archive', 'restore'])
def test_crash_halfway_through_restore_is_idempotently_recoverable(environment, monkeypatch, failed_stage):
    root, data, groups, run, request = environment
    names = [CACHE_PREFIX + '03.a.opt', CACHE_PREFIX + '03.b.gen']
    directory = make_caches(data, {name: ('original ' + name).encode() for name in names})
    move = cp._cache_move

    def move_then_crash(source, destination):
        move(source, destination)
        if ((failed_stage == 'archive' and destination.parent.name == 'cache_generated')
                or (failed_stage == 'restore' and source.parent.name == 'cache_originals')):
            raise OSError('simulated crash during restore')

    with pytest.raises(OSError, match='during restore'):
        with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX) as evidence:
            for name in names:
                (directory / name).write_bytes(('new ' + name).encode())
            monkeypatch.setattr(cp, '_cache_move', move_then_crash)
    assert cp._journal_path(data).is_file()
    monkeypatch.setattr(cp, '_cache_move', move)
    cp.restore_commission_profile(data)
    for name in names:
        assert (directory / name).read_bytes() == ('original ' + name).encode()
        assert (Path(evidence['evidence_dir']) / 'cache_generated' / name).read_bytes() == ('new ' + name).encode()
    assert not cp._journal_path(data).exists()


def test_corrupted_original_backup_is_preserved_and_blocks_restore(environment):
    root, data, groups, run, request = environment
    name = CACHE_PREFIX + '03.a.opt'
    directory = make_caches(data, {name: b'original'})
    with pytest.raises(RuntimeError, match='original optimization cache failed integrity'):
        with cp.managed_commission_profile(data, request, run, cache_prefix=CACHE_PREFIX) as evidence:
            (directory / name).write_bytes(b'new')
            (Path(evidence['evidence_dir']) / 'cache_originals' / name).write_bytes(b'changed')
    assert (directory / name).read_bytes() == b'new'
    assert cp._journal_path(data).exists()


def test_cross_filesystem_move_fails_without_mutating_either_target(environment, monkeypatch):
    root, data, groups, run, request = environment
    source = root / 'source.opt'
    source.write_bytes(b'original')
    destination_dir = root / 'other_volume'
    destination_dir.mkdir()
    destination = destination_dir / 'source.opt'
    original_stat = Path.stat

    def altered_stat(path, **kwargs):
        info = original_stat(path, **kwargs)
        if path == destination_dir:
            values = list(info)
            values[2] = info.st_dev + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, 'stat', altered_stat)
    with pytest.raises(RuntimeError, match='same-filesystem'):
        cp._cache_move(source, destination)
    assert source.read_bytes() == b'original'
    assert not destination.exists()
