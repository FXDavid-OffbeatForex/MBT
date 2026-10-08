from contextlib import nullcontext
from pathlib import Path

import pytest

from core import tester
from core.report_single_html import render_single_backtest_report


@pytest.fixture
def result(tmp_path):
    report = tmp_path / 'single.htm'
    rows = [
        ['Expert:', 'MyEA<script>alert(1)</script>'], ['Symbol:', 'EURUSD'],
        ['Period:', 'H1 (2025.01.01 - 2025.12.31)'], ['Inputs:', 'Period=12'],
        ['', 'Shift=6'], ['Currency:', 'USD'], ['Initial Deposit:', '10 000.00'],
        ['Leverage:', '1:100'], ['Total Net Profit:', '100.00'],
        ['Total Trades:', '1'], ['Balance Drawdown Maximal:', '0.00'],
        ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
         'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment'],
        ['2025.01.01 00:00:00', '1', '', 'balance', '', '', '', '', '', '', '10000', '10000', ''],
        ['2025.01.02 00:00:00', '2', 'EURUSD', 'buy', 'in', '1', '1', '1', '0', '0', '100', '10100', '&lt;script&gt;bad&lt;/script&gt;'],
    ]
    report.write_text('<html><table>' + ''.join('<tr>' + ''.join('<td>'+cell+'</td>' for cell in row) + '</tr>' for row in rows) + '</table></html>', encoding='utf-16')
    ini = tmp_path / 'single.ini'
    ini.write_text('[Tester]\nOptimization=0\n', encoding='utf-8')
    return {'report_html': str(report), 'ini': str(ini), 'timed_out': False}


def test_standalone_charts_settings_history_and_no_experiment(result):
    output = Path(render_single_backtest_report(result)).read_text(encoding='utf-8')
    assert output.count('<svg') == 2
    assert 'Period=12' in output and 'Shift=6' in output
    assert 'H1 (2025.01.01 - 2025.12.31)' in output
    assert 'Deal history' in output and 'Native report SHA-256' in output
    assert '<script>bad</script>' not in output
    assert '&lt;script&gt;bad&lt;/script&gt;' in output
    for text in ('Parameter Results', 'In-sample candidates', 'Out-of-sample check', 'Monte Carlo'):
        assert text not in output


@pytest.mark.parametrize('flag', ['error', 'timed_out'])
def test_failed_tests_not_rendered(result, flag):
    result[flag] = True
    with pytest.raises(ValueError):
        render_single_backtest_report(result)


def test_missing_native_report(result):
    Path(result['report_html']).unlink()
    with pytest.raises(ValueError):
        render_single_backtest_report(result)


def test_utf8_report(result):
    path = Path(result['report_html'])
    markup = path.read_text(encoding='utf-16')
    path.write_text(markup, encoding='utf-8')
    assert Path(render_single_backtest_report(result)).is_file()


@pytest.mark.parametrize('delay,label', [(0, 'No delay'), (100, 'Fixed 100 ms'), (-1, 'Random delay (MT5)')])
def test_report_displays_delay_from_ini(result, delay, label):
    Path(result['ini']).write_text(f'[Tester]\nExecutionMode={delay}\nModel=2\n', encoding='utf-8')
    output = Path(render_single_backtest_report(result)).read_text(encoding='utf-8')
    assert label in output and 'Open prices only' in output
    if delay == -1:
        assert 'stochastic' in output


def test_no_deal_table_does_not_invent_curve(result):
    path = Path(result['report_html'])
    markup = path.read_text(encoding='utf-16').split('<tr><td>Time')[0] + '</table></html>'
    path.write_text(markup, encoding='utf-16')
    output = Path(render_single_backtest_report(result)).read_text(encoding='utf-8')
    assert '<svg' not in output
    assert 'No curve inferred' in output


@pytest.mark.parametrize('enabled', [True, False])
def test_single_runner_auto_report_and_opt_out(monkeypatch, result, enabled):
    from core import optimization
    monkeypatch.setattr(tester, 'terminal_lock', lambda *a: nullcontext())
    monkeypatch.setattr(tester, '_terminal_path', lambda: 'terminal')
    monkeypatch.setattr(tester, '_data_dir', lambda: 'data')
    monkeypatch.setattr(optimization, '_terminal_busy', lambda: False)
    monkeypatch.setattr(tester, '_run_strategy_tester_unlocked', lambda *a: dict(result))
    actual = tester.run_strategy_tester('MyEA', 'EURUSD', html_report=enabled)
    assert actual['native_report_html'] == result['report_html']
    assert actual['report_html'].endswith('_mbt.html' if enabled else '.htm')


def test_render_failure_preserves_native_result(monkeypatch, result):
    from core import optimization, report_single_html
    monkeypatch.setattr(tester, 'terminal_lock', lambda *a: nullcontext())
    monkeypatch.setattr(tester, '_terminal_path', lambda: 'terminal')
    monkeypatch.setattr(tester, '_data_dir', lambda: 'data')
    monkeypatch.setattr(optimization, '_terminal_busy', lambda: False)
    monkeypatch.setattr(tester, '_run_strategy_tester_unlocked', lambda *a: dict(result))
    def fail(*a):
        raise ValueError('render failed')
    monkeypatch.setattr(report_single_html, 'render_single_backtest_report', fail)
    actual = tester.run_strategy_tester('MyEA', 'EURUSD')
    assert actual['report_html'] == result['report_html']
    assert actual['report_error'] == 'render failed'
