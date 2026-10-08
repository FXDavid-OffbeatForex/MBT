from contextlib import nullcontext

import pytest

from core import tester


@pytest.mark.parametrize('delay', [-1, 0, 100, 600000])
def test_delay_ini_and_safe_single_settings(delay):
    ini = tester.build_tester_ini('EA', 'EURUSD', execution_delay_ms=delay)
    assert f'ExecutionMode={delay}\n' in ini
    for line in ('Optimization=0', 'ForwardMode=0', 'UseLocal=1', 'UseRemote=0', 'UseCloud=0'):
        assert line + '\n' in ini


@pytest.mark.parametrize('delay', [True, False, 1.5, '100', None, -2, 600001])
def test_bad_delays_rejected_before_launch(delay, monkeypatch):
    def forbidden(*args):
        raise AssertionError('Must not access terminal for invalid inputs')
    monkeypatch.setattr(tester, '_terminal_path', forbidden)
    with pytest.raises(ValueError, match='execution_delay_ms'):
        tester.run_strategy_tester('EA', 'EURUSD', execution_delay_ms=delay)


def test_math_delay_rejected():
    with pytest.raises(ValueError, match='math'):
        tester.build_tester_ini('EA', 'EURUSD', model='math', execution_delay_ms=100)


def test_delay_passed_to_single_runner(monkeypatch):
    from core import optimization
    monkeypatch.setattr(tester, 'terminal_lock', lambda *a: nullcontext())
    monkeypatch.setattr(tester, '_terminal_path', lambda: 'terminal')
    monkeypatch.setattr(tester, '_data_dir', lambda: 'data')
    monkeypatch.setattr(optimization, '_terminal_busy', lambda: False)
    calls = []
    def run(*args):
        calls.append(args)
        return {'report_html': None, 'execution_delay_ms': args[-1]}
    monkeypatch.setattr(tester, '_run_strategy_tester_unlocked', run)
    outcome = tester.run_strategy_tester('EA', 'EURUSD', execution_delay_ms=-1)
    assert calls[0][-1] == -1 and outcome['execution_delay_ms'] == -1


def test_mcp_delay_forwarding(monkeypatch):
    import mcp_server
    calls = []
    monkeypatch.setattr(mcp_server, '_run_tester', lambda *args, **kwargs: calls.append(kwargs) or kwargs)
    mcp_server.run_strategy_tester('EA', 'EURUSD', execution_delay_ms=250)
    assert calls[0]['execution_delay_ms'] == 250
