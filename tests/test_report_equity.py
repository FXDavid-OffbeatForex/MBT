import hashlib
from core.report_optimization_html import _equity_graph, _parameter_table
from core.equity_capture import HEADER


def test_measured_equity_charts_and_hash_rejection(tmp_path):
    capture_id = 'opt_' + 'a' * 32
    source_hash = 'b' * 64
    csv = tmp_path / 'equity.csv'
    csv.write_text(','.join(HEADER) + '\n' +
        f'{capture_id},{source_hash},EURUSD,H1,2,0,2025.01.01 00:00:00,10000,10000\n' +
        f'{capture_id},{source_hash},EURUSD,H1,2,1,2025.01.01 01:00:00,10000,9500\nEND,2,complete\n')
    replay = tmp_path / 'replay.htm'
    replay.write_text('verified replay')
    detail = {'report_sha256': 'c' * 64, 'from_date': '2025-01-01', 'to_date': '2025-02-01'}
    detail['equity_capture'] = dict(path=str(csv), sha256=hashlib.sha256(csv.read_bytes()).hexdigest(),
        replay_report_path=str(replay), replay_report_sha256=hashlib.sha256(replay.read_bytes()).hexdigest(),
        original_report_sha256=detail['report_sha256'], original_deal_balances_verified=True,
        run_id=capture_id, source_sha256=source_hash, symbol='EURUSD', timeframe='H1', model=2,
        from_date=detail['from_date'], to_date=detail['to_date'])
    output = _equity_graph(detail, tmp_path / 'report.html', 'Selected')
    assert 'Measured account equity at tester ticks' in output
    assert 'Drawdown from observed equity peak' in output
    assert '2 recorded equity points' in output
    assert 'open-price models remain sparse' in output
    csv.write_text(csv.read_text() + 'tampered')
    assert 'could not be verified' in _equity_graph(detail, tmp_path / 'report.html', 'Selected')


def test_enum_labels_are_readable():
    output = _parameter_table({'Method': {'type': 'ENUM_MA_METHOD', 'value': 0,
        'start': 0, 'step': 1, 'stop': 1, 'enum_values': {'MODE_SMA': 0, 'MODE_EMA': 1}}}, {'Method': 1})
    assert 'MODE_SMA (0)' in output and 'MODE_EMA (1)' in output
