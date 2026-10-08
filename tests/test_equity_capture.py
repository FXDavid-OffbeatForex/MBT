import hashlib
from pathlib import Path

import pytest

from core.equity_capture import build_equity_adapter, parse_equity_capture, HEADER

IDENTITY = dict(run_id='opt_a_in_sample_1', source_sha256='a'*64,
                symbol='EURUSD', timeframe='H1', model=2)


def capture(tmp_path, lines=None):
    rows = lines or [','.join(HEADER),
        ','.join([IDENTITY['run_id'], 'a'*64, 'EURUSD', 'H1', '2', '0', '2025.01.01 01:00:00', '10000', '10000']),
        ','.join([IDENTITY['run_id'], 'a'*64, 'EURUSD', 'H1', '2', '1', '2025.01.01 02:00:00', '10000', '9500']),
        'END,2,complete']
    path = tmp_path/'curve.csv'
    path.write_text('\n'.join(rows), encoding='utf-8')
    return path


def parse(path, **overrides):
    settings = dict(expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    from_date='2025-01-01', to_date='2025-01-02', **IDENTITY)
    settings.update(overrides)
    return parse_equity_capture(path, **settings)


def test_actual_equity_distinct_from_balance(tmp_path):
    result = parse(capture(tmp_path))
    assert result['points'][1][1:] == (10000,9500)
    assert result['point_count'] == 2


@pytest.mark.parametrize('replacement', ['END,2,truncated', 'END,2,failed', 'END,3,complete', ''])
def test_incomplete_rejected(tmp_path, replacement):
    path = capture(tmp_path)
    path.write_text(path.read_text().replace('END,2,complete', replacement))
    with pytest.raises(ValueError): parse(path)


@pytest.mark.parametrize('old,new', [('9500','nan'), ('9500','inf'),
    ('2025.01.01 02:00:00','2024.12.31 02:00:00'), ('EURUSD','GBPUSD'),
    ('H1,2,1','H1,2,3')])
def test_bad_points_rejected(tmp_path,old,new):
    path = capture(tmp_path)
    path.write_text(path.read_text().replace(old,new))
    with pytest.raises(ValueError): parse(path)


def test_hash_required(tmp_path):
    with pytest.raises(ValueError): parse(capture(tmp_path), expected_sha256='0'*64)


def test_source_adapter_preserves_handlers_and_original_text():
    source='// OnTick comment\nvoid OnTick(){ return; }\nvoid OnDeinit(const int reason){ Print("OnTick"); }'
    adapted = build_equity_adapter(source,**IDENTITY)
    assert '// OnTick comment' in adapted
    assert 'Print("OnTick")' in adapted
    assert 'void MBT_EQ_OriginalTick(){ return; }' in adapted
    assert 'MBT_EQ_OriginalDeinit(reason);' in adapted
    assert 'ACCOUNT_EQUITY' in adapted
    assert 'MQL_OPTIMIZATION' in adapted


@pytest.mark.parametrize('source', ['void OnTimer(){} void OnTick(){}',
    '#include "custom.mqh"\nvoid OnTick(){}', '#define OnTick Alias\nvoid OnTick(){}',
    'int OnTick(){return 0;}', 'void OnTick(){} void OnTick(){}'])
def test_unsupported_source_rejected(source):
    with pytest.raises(ValueError): build_equity_adapter(source,**IDENTITY)
