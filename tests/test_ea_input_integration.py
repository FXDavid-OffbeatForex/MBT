"""Focused integration regressions for source metadata; never launch MT5."""
import os

import pytest

from core import optimization as opt
from core import optimization_holdout as hold
from core.ea_input_schema import parse_source_schema
from core.report_optimization_html import _parameter_table, _period_metrics


def fixture_schema(tmp_path):
    root = tmp_path / 'MQL5'
    path = root / 'Experts' / 'TestEA.mq5'
    path.parent.mkdir(parents=True)
    include = path.parent / 'inputs.mqh'
    include.write_text('enum Direction { BOTH, LONG, SHORT };\nsinput Direction Fixed=BOTH;\n')
    path.write_text('#include "inputs.mqh"\ninput group "Filters"\ninput bool Filter=true;\ninput string Note="";')
    return path, include, parse_source_schema(path, root)


def parameters():
    return {'Fixed': {'type': 'Direction', 'value': 0},
            'Filter': {'type': 'bool', 'value': True, 'start': False, 'step': 1, 'stop': True},
            'Note': {'type': 'string', 'value': ''}}


def test_fixed_only_enum_and_empty_strings_roundtrip_into_frozen_set(tmp_path):
    path, include, schema = fixture_schema(tmp_path)
    request = parameters()
    lines, count = opt._set_lines(request, opt._schema_types(schema), 10)
    assert count == 2 and 'Note=' in lines
    assert request['Fixed']['enum_values'] == {'BOTH': 0, 'LONG': 1, 'SHORT': 2}
    frozen = hold._fixed_set(request, {'Pass': 1, 'Filter': False})
    assert 'Fixed=0||0||1||0||N\n' in frozen
    assert 'Filter=false\n' in frozen and 'Note=\n' in frozen
    request['Fixed'].update(start=0, step=1, stop=1)
    with pytest.raises(ValueError, match='fixed-only'):
        opt._set_lines(request, opt._schema_types(schema), 10)


@pytest.mark.parametrize('value', ['\n', 'bad=value', 'bad|value', '\x00'])
def test_empty_string_support_does_not_allow_set_injection(tmp_path, value):
    _, _, schema = fixture_schema(tmp_path)
    request = parameters()
    request['Note']['value'] = value
    with pytest.raises(ValueError):
        opt._set_lines(request, opt._schema_types(schema), 10)


def test_include_change_is_detected_before_single_backtest_or_profile_write(tmp_path, monkeypatch):
    source, include, schema = fixture_schema(tmp_path)
    manifest = {'run_id': 'opt_' + 'a'*32, 'run_dir': str(tmp_path), 'data_dir': str(tmp_path),
                'request': {'parameters': parameters()}, 'input_schema': schema}
    include.write_text(include.read_text().replace('SHORT', 'SHORT=7'))
    launches = []
    monkeypatch.setattr(hold.subprocess, 'run', lambda *args, **kwargs: launches.append(args))
    with pytest.raises(ValueError, match='metadata changed'):
        hold._single_backtest(manifest, {'Pass': 0, 'Filter': True}, 'in_sample',
                              '2025-01-01', '2025-02-01', 30)
    assert launches == [] and list(tmp_path.glob('*.set')) == []


def test_include_newer_than_binary_fails_before_optimization_launch(tmp_path, monkeypatch):
    source, include, schema = fixture_schema(tmp_path)
    binary = source.with_suffix('.ex5')
    binary.write_bytes(b'fixture binary')
    stamp = source.stat().st_mtime
    os.utime(binary, (stamp + 1, stamp + 1))
    os.utime(include, (stamp + 2, stamp + 2))
    monkeypatch.setattr(opt, '_data_dir', lambda: tmp_path)
    launches = []
    monkeypatch.setattr(opt.subprocess, 'run', lambda *args, **kwargs: launches.append(args))
    with pytest.raises(ValueError, match='dependency is newer'):
        opt._optimize_ea_unlocked('TestEA', 'EURUSD', 'H1', '2025-01-01', '2025-02-01',
                                 parameters(), timeout_sec=30)
    assert launches == []


def test_report_fixed_only_group_and_enum_labels_use_frozen_schema(tmp_path):
    _, _, schema = fixture_schema(tmp_path)
    request = parameters()
    opt._set_lines(request, opt._schema_types(schema), 10)
    page = _parameter_table(request, {'Filter': True}, schema)
    assert 'fixed-only input' in page and 'BOTH (0)' in page and 'Filters' in page


def test_zero_trade_commission_report_does_not_claim_per_deal_proof():
    detail = {'metrics': {'total_trades': 0, 'net_profit': 0},
              'commission_deals': {'verified': False, 'status': 'no_trading_deals',
                                   'native_deals_verified': 0, 'commission_total': 0},
              'commission_profile': {'status': 'restored'}}
    page = _period_metrics(detail)
    assert 'No trading deals to verify' in page
    assert 'Verified per deal' not in page
    assert 'Deals checked for commissions' in page
    assert 'Tester profile restored' in page


def test_real_commission_proof_has_distinct_report_label():
    detail = {'metrics': {'total_trades': 1},
              'commission_deals': {'verified': True, 'status': 'verified',
                                   'native_deals_verified': 2, 'commission_total': -.70},
              'commission_profile': {'status': 'restored'}}
    page = _period_metrics(detail)
    assert 'Verified per deal' in page and 'No trading deals to verify' not in page
