import pytest
from core.optimization_settings import testing_settings as _settings, testing_ini as _ini
from core.optimization_holdout import optimize_ea_holdout


def test_defaults_and_custom_settings():
    assert 'ExecutionMode=0' in _ini()
    config = dict(deposit=2500, currency='EUR', leverage=30, execution_delay_ms=250)
    assert _settings(config) == config
    assert _ini(config) == ['Deposit=2500.0', 'Currency=EUR', 'Leverage=1:30', 'ExecutionMode=250']
    assert 'ExecutionMode=-1' in _ini({'execution_delay_ms': -1})
    assert 'Deposit=12345.67' in _ini({'deposit': 12345.67})


@pytest.mark.parametrize('config', [
    {'deposit': True}, {'deposit': float('nan')}, {'deposit': -1},
    {'currency': 'USD\nUseCloud=1'}, {'leverage': True}, {'leverage': 0},
    {'execution_delay_ms': 600001}, {'execution_delay_ms': True},
    {'commission': 0}, {'UseCloud': 1},
])
def test_reject_unsafe_settings(config):
    with pytest.raises(ValueError):
        _settings(config)


def test_random_holdout_rejected_before_launch():
    with pytest.raises(ValueError, match='Random execution'):
        optimize_ea_holdout('EA', 'EURUSD', 'H1', '2025-01-01', '2025-02-01',
                            '2025-03-01', {}, testing={'execution_delay_ms': -1})


def test_account_settings_report_identity(tmp_path):
    from core.optimization_holdout import _verify_period_report_identity
    path = tmp_path / 'report.htm'
    markup = ('<b>EA</b><b>EURUSD</b><b>H1 (2025.01.01 - 2025.02.01)</b>'
              '<b>MovingPeriod=12</b><table>'
              '<tr><td>Currency:</td><td><b>USD</b></td></tr>'
              '<tr><td>Initial Deposit:</td><td><b>5 000.00</b></td></tr>'
              '<tr><td>Leverage:</td><td><b>1:30</b></td></tr></table>')
    path.write_text(markup)
    manifest = {'request': {'expert': 'EA', 'symbol': 'EURUSD', 'timeframe': 'H1',
        'parameters': {'MovingPeriod': {'type': 'int', 'value': 12}},
        'testing': {'deposit': 5000, 'leverage': 30}}}
    _verify_period_report_identity(path, manifest, {}, '2025-01-01', '2025-02-01')
    manifest['request']['testing']['deposit'] = 10000
    with pytest.raises(ValueError, match='account settings'):
        _verify_period_report_identity(path, manifest, {}, '2025-01-01', '2025-02-01')
