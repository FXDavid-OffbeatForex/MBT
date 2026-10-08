from datetime import datetime, timedelta
from html import unescape
import json
import re

from core.report_hover import HOVER_SCRIPT, point_attributes
from core.report_optimization_html import _balance_charts, _bar_chart, _heatmap
from core.report_monte_carlo import _bands, _histogram


def test_indicator_hover_has_dates_and_trade_results(monkeypatch, tmp_path):
    from dataclasses import MISSING, fields
    from types import SimpleNamespace
    from core.backtest import BacktestReport
    from core import report_html
    required = {field.name: 0 for field in fields(BacktestReport)
                if field.default is MISSING and field.default_factory is MISSING}
    required.update(symbol='EURUSD', timeframe='H1', total=1, wins=1,
                    profit_factor=1, equity_curve=[2.0], trades=[SimpleNamespace(
                        time=datetime(2025, 1, 1, 12), exit_time=datetime(2025, 1, 2, 14),
                        outcome='WIN', direction='LONG', r=2.0)])
    monkeypatch.setattr(report_html, 'reports_dir', lambda: str(tmp_path))
    from pathlib import Path
    page = Path(report_html.render(BacktestReport(**required))).read_text(encoding='utf-8')
    assert 'Signal: 2025-01-01 12:00:00' in page
    assert 'Exit: 2025-01-02 14:00:00' in page
    assert 'LONG / WIN / trade return: +2.00 R' in page
    assert "intersect: false" in page and 'Cumulative R:' in page


def test_safe_point_payload():
    payload = point_attributes([[1, 2, '</script><img src=x>"']])
    assert '<script' not in payload and '<img' not in payload
    assert json.loads(unescape(payload.split('="', 1)[1][:-1]))[0][2] == '</script><img src=x>"'
    assert 'tip.textContent=text' in HOVER_SCRIPT
    assert 'innerHTML' not in HOVER_SCRIPT


def test_balance_and_drawdown_have_exact_timestamps_and_values():
    start = datetime(2025, 1, 1, 12, 34, 56)
    output = _balance_charts([(start, 10000), (start+timedelta(hours=1), 9500)], 'Test')
    payloads = [json.loads(unescape(value)) for value in re.findall('data-mbt-points="([^"]+)"', output)]
    assert len(payloads) == 2
    assert '2025-01-01 12:34:56' in payloads[0][0][2]
    assert '9,500.00' in payloads[0][1][2]
    assert '5.00%' in payloads[1][1][2]
    assert all(p[0] <= q[0] for p,q in zip(payloads[0],payloads[0][1:]))


def test_optimization_hover_contains_parameters():
    rows = [{'Pass': 3, 'Result': 123.4, 'Period': 12, 'Shift': 6}]
    output = _bar_chart(rows, 'Result', 'Result', '#fff')
    assert 'Period: 12' in output and 'Shift: 6' in output
    request = {'parameters': {'Period': {'start': 10}, 'Shift': {'start': 0}}}
    output = _heatmap(rows, request)
    assert 'data-mbt-tooltip=' in output and 'Period=12' in output


def test_monte_carlo_has_synthetic_not_calendar_hover():
    analysis = {'trade_count': 1, 'provenance': {'currency': 'USD'},
                'balance_bands': [{'closed_trades': 0, 'p05': 10, 'p50': 10, 'p95': 10},
                                  {'closed_trades': 1, 'p05': 5, 'p50': 12, 'p95': 20}],
                'profit_histogram': {'counts': [4], 'edges': [5, 20]}}
    bands = _bands(analysis)
    assert 'data-mbt-points=' in bands
    assert 'not calendar time' in bands
    assert 'P05: 5.00' in bands
    histogram = _histogram(analysis, 'profit_histogram', 'Profit', '#fff')
    assert '5.00 to 20.00: 4 paths' in histogram
