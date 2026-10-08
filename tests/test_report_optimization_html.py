"""Focused checks for the offline MT5 optimization report."""

from pathlib import Path
import hashlib
import re

from core.report_optimization_html import render_optimization_report
from core.report_balance_curve import deal_balance_points


def _results(**overrides):
    result = {
        'optimization': {
            'columns': ['Pass', 'Profit', 'EA<input>'],
            'rows': [{'Pass': '7', 'Profit': '12.5', 'EA<input>': '3'}],
            'row_count': 1,
        },
        'forward': {
            'columns': ['Pass', 'Profit'],
            'rows': [{'Pass': '7', 'Profit': '4.1'}],
            'row_count': 1,
        },
        'counts': {'planned combinations': 12, 'parsed rows': 1,
                   'failed/error rows': 0, 'forward-tested rows': 1},
        'selected_pass': {'Pass': '7', 'Profit': '12.5'},
    }
    result.update(overrides)
    return result


def test_report_escapes_all_untrusted_text_and_only_links_local_artifacts(tmp_path):
    artifact = tmp_path / 'report #1.xml'
    artifact.write_text('<xml/>', encoding='utf-8')
    run = {
        'run_id': '<img src=x onerror=alert(1)>',
        'status': '<b>complete</b>',
        'request': {'EA<script>': '<script>alert(2)</script>'},
        'artifacts': {'MT5 XML': str(artifact), 'remote': 'https://example.test/x',
                      'script': 'javascript:alert(1)'},
    }
    results = _results(selected_pass={'Pass': '7', 'Profit': '<svg onload=alert(3)>'})
    results['optimization']['rows'][0]['EA<input>'] = '<iframe src=x>'
    path = render_optimization_report(run, results, str(tmp_path / 'out.html'))
    source = Path(path).read_text(encoding='utf-8')
    assert path == str((tmp_path / 'out.html').resolve())
    assert '<img src=x' not in source
    assert '<script>alert(2)</script>' not in source
    assert '<svg onload' not in source
    assert '<iframe src=x>' not in source
    assert '&lt;script&gt;alert(2)&lt;/script&gt;' in source
    assert 'EA&lt;input&gt;' in source
    assert 'href="report%20%231.xml"' in source
    assert 'https://example.test' not in source
    assert 'javascript:alert' not in source


def test_missing_forward_and_detailed_run_are_explicit(tmp_path):
    results = _results(forward=None, selected_pass=None)
    source = Path(render_optimization_report(
        {'run_id': 'r1', 'request': {}, 'artifacts': {}}, results,
        str(tmp_path / 'out.html'))).read_text(encoding='utf-8')
    for section in ('Overview', 'Parameter Results', 'In-sample candidates', 'Out-of-sample check'):
        assert section in source
    assert 'No selected pass supplied.' in source
    assert 'Detailed selected-run backtest unavailable.' in source
    assert 'No results supplied.' in source
    assert '<canvas' not in source
    assert 'Chart.js' not in source
    assert 'Monte Carlo analysis' in source  # limitation, not a claimed result


def test_exact_counts_and_unique_pass_forward_comparison(tmp_path):
    source = Path(render_optimization_report(
        {'run_id': 'r2', 'status': 'complete'}, _results(),
        str(tmp_path / 'out.html'))).read_text(encoding='utf-8')
    assert '<dt>planned combinations</dt><dd>12</dd>' in source
    assert '<dt>parsed rows</dt><dd>1</dd>' in source
    assert '<dt>failed/error rows</dt><dd>0</dd>' in source
    assert '<dt>forward-tested rows</dt><dd>1</dd>' in source
    assert 'Reported row count: 1. Rows shown: 1.' in source
    assert 'Selected pass: optimization versus forward' in source
    assert '<td>12.5</td><td>4.1</td>' in source


def test_ambiguous_forward_pass_is_not_joined(tmp_path):
    results = _results()
    results['forward']['rows'].append({'Pass': '7', 'Profit': '99'})
    source = Path(render_optimization_report(
        {'run_id': 'r3'}, results, str(tmp_path / 'out.html'))).read_text(encoding='utf-8')
    assert 'no unique matching pass ID' in source
    assert 'Selected pass: optimization versus forward' not in source


def test_dark_report_charts_only_saved_aggregate_metrics(tmp_path):
    results = _results()
    results['optimization'] = {
        'columns': ['Pass', 'Result', 'Equity DD %', 'Fast', 'Slow'],
        'rows': [
            {'Pass': 1, 'Result': 110, 'Equity DD %': 8, 'Fast': 10, 'Slow': 4},
            {'Pass': 2, 'Result': 130, 'Equity DD %': 12, 'Fast': 12, 'Slow': 4},
        ], 'row_count': 2,
    }
    run = {'run_id': 'r4', 'request': {'parameters': {
        'Fast': {'start': 10}, 'Slow': {'start': 4}}}}
    source = Path(render_optimization_report(run, results, str(tmp_path / 'out.html'))).read_text(encoding='utf-8')
    assert 'color-scheme:dark' in source
    assert 'role="tablist"' in source
    assert 'Parameter landscape' in source
    assert 'Optimization result by pass' in source
    assert 'Equity drawdown by pass' in source
    assert 'role="img"' in source
    assert 'Optimization summary rows contain totals, not a trade-by-trade curve' in source
    assert 'No verified forward rows' not in source


def test_holdout_counts_parameters_and_raw_artifacts_are_distinct(tmp_path):
    raw_xml = tmp_path / 'opt.xml'
    raw_ini = tmp_path / 'test.ini'
    raw_set = tmp_path / 'inputs.set'
    for path in (raw_xml, raw_ini, raw_set):
        path.write_text('raw', encoding='utf-8')
    run = {'run_id': 'r5', 'request': {'expert': 'TestEA', 'symbol': 'EURUSD',
           'from_date': '2025-01-01', 'to_date': '2025-02-01',
           'parameters': {'Fast': {'type': 'int', 'value': 12, 'start': 10,
                                   'step': 2, 'stop': 12},
                          'Risk': {'type': 'double', 'value': 0.02}}},
           'artifacts': {'Optimization XML': str(raw_xml), 'Tester INI': str(raw_ini),
                         'Tester SET': str(raw_set)}}
    results = _results(forward=None, selected_pass={'Pass': 3, 'Fast': 12})
    results['counts'] = {'planned_combinations': 2, 'parsed_rows': 2,
                         'distinct_parameter_tuples': 2, 'eligible_rows': 2,
                         'forward_rows': 0, 'mt5_reported_passes': 2,
                         'failed_rows': None}
    results['holdout'] = {'status': 'completed', 'from_date': '2025-02-01',
                          'to_date': '2025-03-01', 'selected_pass': 3,
                          'metrics': {'net_profit': 5, 'total_trades': 42}}
    source = Path(render_optimization_report(
        run, results, str(tmp_path / 'report.html'))).read_text(encoding='utf-8')
    assert '<dt>Native MT5 forward rows</dt><dd>0</dd>' in source
    assert '<dt>Independent holdout backtests</dt><dd>1</dd>' in source
    assert 'Native forward rows and independent holdout backtests are different evidence' in source
    assert '<th scope="col">Tested setting</th>' in source
    assert '<td>Fast</td><td>int</td><td>Swept</td><td>10 to 12 (step 2)</td><td>12</td>' in source
    assert '<td>Risk</td><td>double</td><td>Fixed</td><td>0.02</td><td>0.02</td>' in source
    assert '<dt>parameters</dt>' not in source
    assert 'Optimization data (XML)' in source
    assert 'Raw MT5 table; see Parameter Results' in source
    assert 'Optimization settings (INI)' in source
    assert 'EA input ranges (SET)' in source


def test_selected_and_holdout_balance_graphs_are_separate(tmp_path):
    def mt5_report(path, ending):
        path.write_text('''<html><table>
        <tr><td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td><td>Volume</td><td>Price</td><td>Order</td><td>Commission</td><td>Swap</td><td>Profit</td><td>Balance</td><td>Comment</td></tr>
        <tr><td>2025.01.01 00:00:00</td><td>1</td><td></td><td>balance</td><td></td><td></td><td></td><td></td><td>0</td><td>0</td><td>10 000.00</td><td>10 000.00</td><td></td></tr>
        <tr><td>2025.01.02 00:00:00</td><td>2</td><td>EURUSD</td><td>buy</td><td>out</td><td>1</td><td>1.1</td><td>2</td><td>0</td><td>0</td><td>0</td><td>''' + ending + '''</td><td></td></tr>
        </table></html>''', encoding='utf-8')
        return hashlib.sha256(path.read_bytes()).hexdigest()
    training_report = tmp_path / 'training.htm'
    holdout_report = tmp_path / 'holdout.htm'
    training_hash = mt5_report(training_report, '10 012.00')
    holdout_hash = mt5_report(holdout_report, '10 004.00')
    results = _results()
    results['in_sample'] = {'status': 'completed', 'from_date': '2025-01-01',
                            'to_date': '2025-02-01', 'metrics': {'net_profit': 12},
                            'report_path': str(training_report), 'report_sha256': training_hash}
    results['holdout'] = {'status': 'completed', 'from_date': '2025-02-01',
                          'to_date': '2025-03-01', 'selected_pass': '7',
                          'metrics': {'net_profit': 4},
                          'report_path': str(holdout_report), 'report_sha256': holdout_hash}
    source = Path(render_optimization_report(
        {'run_id': 'r6'}, results, str(tmp_path / 'report.html'))).read_text(encoding='utf-8')
    assert 'In-sample means the earlier dates' in source
    assert 'Out-of-sample means later dates' in source
    assert 'aria-label="In-sample Account balance after each deal"' in source
    assert 'aria-label="Out-of-sample Account balance after each deal"' in source
    assert 'Drawdown from recorded balance peak' in source
    assert 'These are not tick-by-tick equity curves' in source
    assert 'src="training.png"' not in source
    assert 'href="training.htm"' in source
    assert deal_balance_points(training_report)[-1][1] == 10012


def test_balance_chart_fails_closed_if_mt5_report_changes(tmp_path):
    report = tmp_path / 'source.htm'
    report.write_text('<html>changed</html>', encoding='utf-8')
    results = _results(in_sample={'status': 'completed', 'report_path': str(report),
                                  'report_sha256': '0' * 64})
    source = Path(render_optimization_report(
        {'run_id': 'r7'}, results, str(tmp_path / 'report.html'))).read_text(encoding='utf-8')
    assert 'could not be verified or parsed' in source
    assert 'aria-label="In-sample Account balance after each deal"' not in source


def test_shortlist_report_shows_partial_counts_and_preserves_in_sample_winner(tmp_path):
    results = _results(forward=None)
    results['counts'] = {'planned_combinations': 3, 'parsed_rows': 3}
    results['holdout_selection'] = {'frozen_count': 3, 'frozen_at': '2026-10-06T00:00:00Z'}
    results['holdout_summary'] = {'planned': 3, 'completed': 2, 'failed': 1, 'not_run': 0}
    results['holdout_candidates'] = [
        {'rank': 1, 'status': 'completed', 'optimization_pass': {'Pass': '7', 'Profit': 12.5},
         'parameters': {'Fast': 3}, 'holdout': {'metrics': {'net_profit': -10}}},
        {'rank': 2, 'status': 'completed', 'optimization_pass': {'Pass': '8', 'Profit': 10},
         'parameters': {'Fast': 4}, 'holdout': {'metrics': {'net_profit': 500}}},
        {'rank': 3, 'status': 'failed', 'optimization_pass': {'Pass': '9', 'Profit': 5},
         'parameters': {'Fast': 5}, 'error': '<timeout>'},
    ]
    source = Path(render_optimization_report(
        {'run_id': 'r8', 'status': 'holdout_partial'}, results, str(tmp_path / 'report.html'))).read_text(encoding='utf-8')
    assert '<dt>Independent holdout backtests</dt><dd>2</dd>' in source
    assert '<dt>Failed holdout tests</dt><dd>1</dd>' in source
    assert '7 (IS winner)' in source
    assert '8 (IS winner)' not in source
    assert '<td>Fast=4</td>' in source
    assert 'aria-label="In-sample and out-of-sample profit comparison"' in source
    assert 'reserve a fresh untouched period' in source
    assert '&lt;timeout&gt;' in source
    assert '<timeout>' not in source


def test_each_period_has_an_exclusive_accordion_with_winner_open_and_embedded_curves(tmp_path):
    def detail(name):
        path = tmp_path / f'{name}.htm'
        columns = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
                   'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']
        rows = [['2025.01.01 00:00:00', '1', '', 'balance', '', '', '', '', '', '', '', '10 000.00', ''],
                ['2025.01.02 00:00:00', '2', '', 'buy', 'out', '', '', '', '', '', '', '10 012.00', '']]
        path.write_text('<table><tr>' + ''.join(f'<td>{c}</td>' for c in columns) + '</tr>' +
                        ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows) + '</table>', encoding='utf-8')
        return {'status': 'completed', 'report_path': str(path),
                'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'metrics': {'net_profit': 12}}
    results = _results(forward=None)
    results['holdout_candidates'] = [
        {'rank': 1, 'status': 'completed', 'optimization_pass': {'Pass': '7'},
         'parameters': {'Fast': 3}, 'in_sample': detail('is7'), 'holdout': detail('oos7')},
        {'rank': 2, 'status': 'completed', 'optimization_pass': {'Pass': '8'},
         'parameters': {'Fast': 4}, 'in_sample': detail('is8'), 'holdout': detail('oos8')},
    ]
    source = Path(render_optimization_report({'run_id': 'r9'}, results, str(tmp_path / 'report.html'))).read_text(encoding='utf-8')
    accordions = re.findall(r'<details\b[^>]*>', source)
    assert len(accordions) == 4
    assert sum(' open' in tag for tag in accordions) == 2
    assert all('data-pass="7"' in tag for tag in accordions if ' open' in tag)
    for period in ('in_sample', 'holdout'):
        assert sum(f'name="mbt-{period}-candidates"' in tag for tag in accordions) == 2
        assert f'data-src="report_{period}_2.html"' in source
        for index in (1, 2):
            child = (tmp_path / f'report_{period}_{index}.html').read_text(encoding='utf-8')
            assert 'Account balance after each deal' in child
            assert 'Drawdown from recorded balance peak' in child
    assert 'Other candidates: detailed out-of-sample tests' not in source
    assert 'Independent holdout: selected pass' not in source
