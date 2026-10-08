from pathlib import Path
import hashlib
import json

import pytest

from core.optimization_holdout import (
    _copy_report_assets, _fixed_set, _verify_in_sample_totals,
    _verify_report_identity, optimize_ea_holdout,
    _freeze_candidates, _run_holdout_shortlist,
)


PARAMETERS = {
    "Fast": {"type": "int", "value": 5, "start": 3, "step": 1, "stop": 5},
    "Slow": {"type": "double", "value": 1.5},
    "Enabled": {"type": "bool", "value": False},
}


def _manifest():
    return {"request": {"expert": "Examples\\TestEA", "symbol": "EURUSD",
                        "timeframe": "H1", "parameters": PARAMETERS},
            "holdout_cutoff_date": "2025-02-01",
            "holdout_end_date": "2025-03-01"}


def _report(inputs="Fast=3", period="H1 (2025.02.01 - 2025.03.01)"):
    return ("<html><b>TestEA</b><b>EURUSD</b><b>" + period + "</b><b>" +
            inputs + "</b><b>Slow=1.5</b><b>Enabled=false</b></html>")


def test_fixed_holdout_set_freezes_varied_inputs():
    lines = _fixed_set(PARAMETERS, {"Fast": 3})
    assert "Fast=3||3||1||3||N" in lines
    assert "Slow=1.5||1.5||1||1.5||N" in lines
    assert "Enabled=false" in lines


def test_holdout_report_checks_ea_symbol_dates_and_inputs(tmp_path: Path):
    path = tmp_path / "report.htm"
    path.write_bytes(_report().encode("utf-16"))
    _verify_report_identity(path, _manifest(), {"Fast": 3})
    path.write_bytes(_report(inputs="Fast=4").encode("utf-16"))
    with pytest.raises(ValueError, match="mismatches input Fast"):
        _verify_report_identity(path, _manifest(), {"Fast": 3})
    path.write_bytes(_report(period="H1 (2025.02.02 - 2025.03.01)").encode("utf-16"))
    with pytest.raises(ValueError, match="EA, symbol or period"):
        _verify_report_identity(path, _manifest(), {"Fast": 3})


def test_holdout_rejects_invalid_cutoff_before_terminal_access(monkeypatch):
    monkeypatch.setattr("core.optimization_holdout.opt._data_dir", lambda: pytest.fail("terminal access"))
    with pytest.raises(ValueError, match="from_date < cutoff_date < to_date"):
        optimize_ea_holdout("TestEA", "EURUSD", "H1", "2025-01-01",
                            "2025-01-01", "2025-03-01", PARAMETERS)


def test_mt5_report_graphs_are_copied_and_path_confined(tmp_path: Path):
    data, folder = tmp_path / "mt5", tmp_path / "run"
    data.mkdir()
    folder.mkdir()
    report = folder / "run_holdout.htm"
    report.write_bytes('<img src="run_holdout.png"><img src="run_holdout-hst.png">'.encode("utf-16"))
    (data / "run_holdout.png").write_bytes(b"balance graph")
    (data / "run_holdout-hst.png").write_bytes(b"history graph")
    saved = _copy_report_assets(report, data, folder)
    assert len(saved["graph_assets"]) == 2
    assert Path(saved["balance_graph_path"]).read_bytes() == b"balance graph"
    assert (folder / "run_holdout-hst.png").is_file()
    report.write_bytes('<img src="../outside.png">'.encode("utf-16"))
    with pytest.raises(ValueError, match="unexpected graph image"):
        _copy_report_assets(report, data, folder)


def test_detailed_in_sample_totals_must_match_selected_optimization_pass():
    selected = {"Profit": 615.86, "Trades": 49}
    _verify_in_sample_totals(selected, {"net_profit": 615.86, "total_trades": 49.0})
    with pytest.raises(ValueError, match="profit differs"):
        _verify_in_sample_totals(selected, {"net_profit": 600.0, "total_trades": 49.0})
    with pytest.raises(ValueError, match="trade count differs"):
        _verify_in_sample_totals(selected, {"net_profit": 615.86, "total_trades": 48.0})


def _shortlist_fixture(tmp_path, monkeypatch):
    binary = tmp_path / 'test.ex5'
    binary.write_bytes(b'fixed EA')
    manifest = _manifest()
    manifest.update(run_id='test', run_dir=str(tmp_path), data_dir=str(tmp_path),
                    status='completed', ea_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    manifest['request'].update(from_date='2025-01-01', to_date='2025-02-01', min_trades=3, mode='complete')
    rows = [{'Pass': 7, 'Fast': 3, 'Trades': 10, 'Result': 30, 'Profit': 20},
            {'Pass': 8, 'Fast': 4, 'Trades': 10, 'Result': 20, 'Profit': 10},
            {'Pass': 9, 'Fast': 3, 'Trades': 10, 'Result': 25, 'Profit': 15},
            {'Pass': 10, 'Fast': 5, 'Trades': 1, 'Result': 100, 'Profit': 90}]
    result = {'optimization': {'rows': rows}, 'selected_pass': dict(rows[0])}
    monkeypatch.setattr('core.optimization_holdout.opt._ea_paths', lambda *args: (binary, None, None))
    return manifest, result


def test_shortlist_uses_only_eligible_distinct_in_sample_inputs(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    result['holdout'] = {'metrics': {'net_profit': 9999}}
    selection, candidates = _freeze_candidates(manifest, result, 5)
    assert selection['pass_ids'] == [7, 8]
    assert selection['frozen_count'] == 2
    assert len(selection['candidates_sha256']) == 64
    assert candidates[0]['parameters']['Fast'] == 3
    assert candidates[1]['parameters']['Fast'] == 4


def test_shortlist_is_saved_before_testing_and_winner_never_changes(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    calls = []
    def backtest(manifest, selected, kind, start, end, timeout, candidate_rank=None):
        saved = json.loads((tmp_path / 'results.json').read_text())
        assert saved['holdout_selection']['pass_ids'] == [7, 8]
        assert saved['holdout_candidates'][1]['parameters']['Fast'] == 4
        calls.append((selected['Pass'], kind, candidate_rank, timeout))
        return {'status': 'completed', 'selected_pass': selected['Pass'],
                'metrics': {'net_profit': 500 if selected['Pass'] == 8 else -10}}
    monkeypatch.setattr('core.optimization_holdout._single_backtest', backtest)
    _run_holdout_shortlist(manifest, result, 90, 5, 60)
    assert [(p, k, rank) for p, k, rank, _ in calls] == [(7, 'in_sample', None), (7, 'holdout', None),
                                                      (8, 'in_sample', 2), (8, 'holdout', 2)]
    assert all(timeout <= 60 for _, _, _, timeout in calls)
    assert result['selected_pass']['Pass'] == 7
    assert result['holdout']['selected_pass'] == 7
    assert result['holdout_summary']['completed'] == 2
    assert manifest['holdout_batch_status'] == 'completed'


def test_shared_budget_stops_additional_tests_and_records_partial_results(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    clock = iter([0, 0, 1, 61, 61])
    monkeypatch.setattr('core.optimization_holdout.time.monotonic', lambda: next(clock))
    calls = []
    def backtest(*args, **kwargs):
        calls.append(args[2])
        return {'status': 'completed', 'selected_pass': args[1]['Pass'], 'metrics': {'net_profit': 1}}
    monkeypatch.setattr('core.optimization_holdout._single_backtest', backtest)
    _run_holdout_shortlist(manifest, result, 30, 5, 60)
    assert calls == ['in_sample', 'holdout']
    assert manifest['status'] == 'holdout_partial'
    assert result['holdout_summary']['completed'] == 1
    assert result['holdout_summary']['failed'] == 0
    assert result['holdout_summary']['not_run'] == 1
    assert result['holdout_summary']['resumable'] is True
    assert 'budget exhausted' in result['holdout_candidates'][1]['error']


def test_changed_ea_stops_batch_before_any_single_test(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    manifest['ea_sha256'] = '0' * 64
    monkeypatch.setattr('core.optimization_holdout._single_backtest', lambda *a, **k: pytest.fail('launched changed EA'))
    _run_holdout_shortlist(manifest, result, 90, 5, 30)
    assert manifest['status'] == 'selected_detail_failed'
    assert result['holdout_summary']['not_run'] == 2


@pytest.mark.parametrize('top_n,budget', [(0, 600), (10001, 600), (True, 600), (5, 29), (5, 7201)])
def test_invalid_shortlist_limits_reject_before_terminal_access(monkeypatch, top_n, budget):
    monkeypatch.setattr('core.optimization_holdout.opt._data_dir', lambda: pytest.fail('terminal access'))
    with pytest.raises(ValueError):
        optimize_ea_holdout('TestEA', 'EURUSD', 'H1', '2025-01-01', '2025-02-01',
                            '2025-03-01', PARAMETERS, holdout_top_n=top_n,
                            holdout_budget_sec=budget)


def test_relative_drawdown_percent_is_parsed_instead_of_cash(tmp_path):
    from core.tester import parse_tester_report
    path = tmp_path / 'test.htm'
    path.write_bytes('<html><td>Equity Drawdown Relative:</td><td><b>5.25% (529.84)</b></td></html>'.encode('utf-16'))
    assert parse_tester_report(str(path))['equity_dd_relative_pct'] == 5.25


@pytest.mark.parametrize('count,mode,expected', [(4, 'complete', 4), (400, 'complete', 256),
                                               (3000, 'complete', 300), (3000, 'genetic', 750)])
def test_default_selection_matches_mt5_sizing_instead_of_small_fixed_cap(tmp_path, monkeypatch, count, mode, expected):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    manifest['request']['mode'] = mode
    rows = [{'Pass': i, 'Fast': i, 'Trades': 10, 'Result': count - i} for i in range(count)]
    result['optimization']['rows'], result['selected_pass'] = rows, rows[0]
    selection, candidates = _freeze_candidates(manifest, result, None)
    assert len(candidates) == expected
    assert selection['method'] == 'mt5_sized_eligible_subset'
    assert selection['eligible_distinct_count'] == count


def test_resume_preserves_frozen_selection_and_does_not_repeat_completed_tests(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    clock = iter([0, 0, 1, 61, 61])
    monkeypatch.setattr('core.optimization_holdout.time.monotonic', lambda: next(clock))
    calls = []
    def backtest(manifest, selected, kind, *args, **kwargs):
        calls.append((selected['Pass'], kind))
        report = tmp_path / f'{kind}_{selected["Pass"]}.htm'
        report.write_bytes(b'example report')
        return {'status': 'completed', 'selected_pass': selected['Pass'],
                'report_path': str(report), 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
                'metrics': {'net_profit': 1000}}
    monkeypatch.setattr('core.optimization_holdout._single_backtest', backtest)
    _run_holdout_shortlist(manifest, result, 30, None, 60)
    frozen = dict(result['holdout_selection'])
    clock = iter([100, 101, 102, 103])
    _run_holdout_shortlist(manifest, result, 30, None, 60, resume=True)
    assert calls == [(7, 'in_sample'), (7, 'holdout'), (8, 'in_sample'), (8, 'holdout')]
    assert result['holdout_selection'] == frozen
    assert result['selected_pass']['Pass'] == 7
    assert result['holdout_summary']['completed'] == 2
    assert manifest['status'] == 'completed'


def test_resume_rejects_mutated_candidate_plan(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    selection, candidates = _freeze_candidates(manifest, result, None)
    result['holdout_selection'], result['holdout_candidates'] = selection, candidates
    candidates[1]['parameters']['Fast'] = 99
    with pytest.raises(ValueError, match='selection was changed'):
        _run_holdout_shortlist(manifest, result, 90, None, 30, resume=True)


def test_mt5_effective_complete_fallback_uses_complete_selection_fraction(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    manifest['request']['mode'] = 'genetic'
    manifest['tester_diagnostics'] = {'effective_mode': 'complete'}
    rows = [{'Pass': i, 'Fast': i, 'Trades': 10, 'Result': 3000 - i} for i in range(3000)]
    result['optimization']['rows'], result['selected_pass'] = rows, rows[0]
    selection, candidates = _freeze_candidates(manifest, result, None)
    assert selection['selection_mode'] == 'complete'
    assert len(candidates) == 300


def test_failed_candidate_is_distinct_from_unrun_candidates(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    third = {'Pass': 11, 'Fast': 5, 'Trades': 10, 'Result': 10}
    result['optimization']['rows'].append(third)
    def backtest(manifest, selected, kind, *args, **kwargs):
        if selected['Pass'] == 8:
            raise RuntimeError('MT5 did not export the report')
        return {'status': 'completed', 'selected_pass': selected['Pass'], 'metrics': {'net_profit': 10}}
    monkeypatch.setattr('core.optimization_holdout._single_backtest', backtest)
    _run_holdout_shortlist(manifest, result, 30, None, 60)
    assert [c['status'] for c in result['holdout_candidates']] == ['completed', 'failed', 'not_run']
    assert result['holdout_summary']['completed'] == 1
    assert result['holdout_summary']['failed'] == 1
    assert result['holdout_summary']['not_run'] == 1
    assert manifest['status'] == 'holdout_partial'


def test_resume_between_periods_reuses_completed_candidate_training(tmp_path, monkeypatch):
    manifest, result = _shortlist_fixture(tmp_path, monkeypatch)
    clock = iter([0, 0, 1, 2, 61, 61])
    monkeypatch.setattr('core.optimization_holdout.time.monotonic', lambda: next(clock))
    calls = []
    def backtest(manifest, selected, kind, *args, **kwargs):
        calls.append((selected['Pass'], kind))
        report = tmp_path / f'{kind}_{selected["Pass"]}.htm'
        report.write_bytes(b'report')
        return {'status': 'completed', 'selected_pass': selected['Pass'],
                'report_path': str(report), 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest()}
    monkeypatch.setattr('core.optimization_holdout._single_backtest', backtest)
    _run_holdout_shortlist(manifest, result, 30, None, 60)
    assert result['holdout_summary']['in_sample_completed'] == 2
    assert result['holdout_summary']['holdout_completed'] == 1
    assert result['holdout_summary']['not_run'] == 1
    clock = iter([100, 101, 102])
    _run_holdout_shortlist(manifest, result, 30, None, 60, resume=True)
    assert calls == [(7, 'in_sample'), (7, 'holdout'), (8, 'in_sample'), (8, 'holdout')]
    assert result['holdout_summary']['completed'] == 2
