import hashlib
from pathlib import Path
import threading

import pytest

from core import commission_profile as cp
from core import optimization_log as log
from core.optimization_settings import testing_settings as settings, testing_ini as ini
from core.terminal_lock import terminal_lock


def test_native_forward_commissions_rejected_before_any_launch(native_settings):
    from core import optimization as opt
    _, _, config = native_settings
    with pytest.raises(ValueError, match='use optimize_ea_holdout'):
        opt._optimize_ea_unlocked('EA', 'EURUSD', 'H1', '2025-05-01', '2025-05-15',
                                 {}, forward_mode='half', testing={'commission': config})


@pytest.fixture
def native_settings(tmp_path, monkeypatch):
    root = tmp_path / 'MBT'
    root.mkdir()
    monkeypatch.setattr(cp, '_ROOT', root)
    template = root / 'native_group.txt'
    template.write_text('CommonUseSettings=1\nCommissionSymbol=Forex\\*\nCommissionValue=3.5000\n',
                        encoding='utf-16')
    config = {'per_lot': 20, 'entry': 'both', 'profile_name': 'Broker_demo.txt',
              'template_path': str(template)}
    return root, template, config


def test_commission_settings_freeze_template_without_changing_native_module_tuple(native_settings):
    root, template, config = native_settings
    canonical = settings({'commission': config})
    assert canonical['commission']['per_lot'] == '20.0000'
    assert set(canonical['commission']) == set(config)
    assert canonical['commission_template_sha256'] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert settings(canonical) == canonical
    legacy = dict(canonical)
    legacy.pop('commission_template_sha256')
    assert settings(legacy) == canonical
    template.write_text('CommonUseSettings=1\nMarginCall=70.00\n', encoding='utf-16')
    with pytest.raises(ValueError, match='differs from the saved'):
        settings(canonical)


def test_default_settings_stay_unchanged_and_native_commissions_never_enter_ini(native_settings):
    root, template, config = native_settings
    default = settings()
    assert default == dict(deposit=10000.0, currency='USD', leverage=100, execution_delay_ms=0)
    assert settings({'commission': None}) == default
    baseline_ini = ini()
    commissioned_ini = ini({'commission': config})
    assert commissioned_ini == baseline_ini + ['ProfitInPips=0']
    assert not any('Commission' in line or 'CommonUseSettings' in line for line in commissioned_ini)
    with pytest.raises(ValueError, match='requires a native commission'):
        settings({'commission_template_sha256': 'a' * 64})


def test_pending_profile_blocks_acquired_lock_but_allows_explicit_recovery(native_settings, monkeypatch):
    root, template, config = native_settings
    monkeypatch.setattr('core.terminal_lock.reports_dir', lambda: str(root / 'reports'))
    data = root / 'terminal_data'
    terminal = str(root / 'terminal64.exe')
    journal = cp._journal_path(data)
    journal.parent.mkdir(parents=True)
    journal.write_text('{}')
    with pytest.raises(RuntimeError, match='needs recovery'):
        with terminal_lock(terminal, str(data)):
            pytest.fail('pending profile must block launch')
    with terminal_lock(terminal, str(data), commission_recovery=True):
        journal.unlink()
    with terminal_lock(terminal, str(data)):
        pass
    with pytest.raises(ValueError, match='boolean'):
        with terminal_lock(terminal, str(data), commission_recovery='yes'):
            pytest.fail('recovery bypass must be explicit boolean')


def test_pending_guard_itself_runs_under_process_exclusion(native_settings, monkeypatch):
    root, template, config = native_settings
    monkeypatch.setattr('core.terminal_lock.reports_dir', lambda: str(root / 'reports'))
    data = root / 'terminal_data'
    terminal = str(root / 'terminal64.exe')
    outcomes = []

    def contender():
        try:
            with terminal_lock(terminal, str(data), commission_recovery=True):
                outcomes.append('incorrectly acquired')
        except RuntimeError:
            outcomes.append('excluded')

    def guard(_):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    monkeypatch.setattr(cp, 'assert_no_pending_commission', guard)
    with terminal_lock(terminal, str(data)):
        assert outcomes == ['excluded']


def _log_evidence(native_settings):
    root, template, config = native_settings
    artifact_dir = root / 'evidence'
    artifact_dir.mkdir()
    profile, template_hash = cp.native_commission_profile(config)
    (artifact_dir / 'profile.txt').write_bytes(profile)
    evidence = {'request': cp.commission_settings(config), 'evidence_dir': str(artifact_dir),
                'profile_sha256': hashlib.sha256(profile).hexdigest()}
    line = "Tester custom group settings applied from file 'MQL5\\Profiles\\Tester\\Groups\\Broker_demo.txt'\n"
    return evidence, line


def test_commission_log_delta_excludes_stale_confirmation_and_private_text(native_settings):
    root, template, config = native_settings
    evidence, line = _log_evidence(native_settings)
    path = root / 'tester.log'
    prior = ('private-account secret-never-export\n' + line).encode('utf-16-le')
    path.write_bytes(b'\xff\xfe' + prior + 'optimization started\n'.encode('utf-16-le'))
    snapshot = path, 2 + len(prior)
    with pytest.raises(ValueError, match='did not confirm'):
        log.tester_log_commission(snapshot, evidence)
    with path.open('ab') as stream:
        stream.write(('secret-never-export\n' + line).encode('utf-16-le'))
    proof = log.tester_log_commission(snapshot, evidence)
    assert proof == {'native_profile_applied': True, 'profile_name': 'Broker_demo.txt',
                     'profile_sha256': evidence['profile_sha256']}
    assert 'secret-never-export' not in str(proof)
    assert 'private-account' not in str(proof)


@pytest.mark.parametrize('case', ['missing', 'truncated', 'oversized', 'wrong_profile', 'invalid_offset'])
def test_commission_log_requires_bounded_valid_delta(native_settings, monkeypatch, case):
    root, template, config = native_settings
    evidence, line = _log_evidence(native_settings)
    path = root / 'tester.log'
    offset = 0
    if case != 'missing':
        path.write_text(line if case != 'wrong_profile' else line.replace('Broker_demo', 'Other_demo'))
    if case == 'truncated':
        offset = path.stat().st_size + 1
    elif case == 'oversized':
        monkeypatch.setattr(log, '_MAX_BYTES', 8)
    elif case == 'invalid_offset':
        offset = -1
    with pytest.raises(ValueError):
        log.tester_log_commission((path, offset), evidence)
