import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from core import optimization_equity as eq


@pytest.mark.parametrize('kwargs',[{'pass_id':True},{'period':'forward'},{'timeout_sec':1},{'html_report':1}])
def test_invalid_requests_never_launch(kwargs):
    with pytest.raises(ValueError): eq.capture_candidate_equity('opt_a',**kwargs)


def test_match_requires_original_totals_and_chronology(monkeypatch):
    detail={'metrics':{'net_profit':10,'total_trades':2},'report_path':'original'}
    other={'metrics':{'net_profit':10,'total_trades':2},'report_path':'replay'}
    monkeypatch.setattr(eq,'deal_balance_points',lambda path:[(1,100)] if str(path)=='original' else [(1,101)])
    with pytest.raises(ValueError,match='chronological'): eq._match_details(detail,other)
    other['metrics']['net_profit']=20
    with pytest.raises(ValueError,match='profit'): eq._match_details(detail,other)


def fixture_run(tmp_path,monkeypatch):
    folder=tmp_path/'run';folder.mkdir()
    data=tmp_path/'data';data.mkdir()
    binary=tmp_path/'original.ex5';binary.write_bytes(b'binary')
    source=tmp_path/'original.mq5';source.write_text('void OnTick(){}')
    report=folder/'original.htm';report.write_text('original')
    xml=folder/'original.xml';xml.write_text('xml')
    sha=eq.opt._hash
    detail=dict(status='completed',report_identity_verified=True,report_path=str(report),
        report_sha256=sha(report),from_date='2025-01-01',to_date='2025-01-02',
        metrics={'net_profit':10,'total_trades':2})
    manifest=dict(run_id='opt_a',run_dir=str(folder),data_dir=str(data),terminal_path=str(tmp_path/'terminal.exe'),
        optimization_path=str(xml),optimization_sha256=sha(xml),ea_sha256=sha(binary),source_sha256=sha(source),
        request=dict(expert='original',symbol='EURUSD',timeframe='H1',model='open_prices',parameters={}))
    row={'Pass':3}
    candidate=dict(rank=1,optimization_pass=row,parameters={},in_sample=detail.copy(),holdout=detail.copy())
    selection=dict(candidates_sha256=eq._candidate_hash([candidate]),requested_top_n=None)
    result=dict(selected_pass=row,optimization={'rows':[row]},holdout_selection=selection,holdout_candidates=[candidate])
    (folder/'manifest.json').write_text(json.dumps(manifest));(folder/'results.json').write_text(json.dumps(result))
    monkeypatch.setattr(eq.opt,'_run_dir',lambda run:folder)
    monkeypatch.setattr(eq.opt,'_terminal_path',lambda:manifest['terminal_path'])
    monkeypatch.setattr(eq.opt,'_data_dir',lambda:data)
    monkeypatch.setattr(eq.opt,'_terminal_busy',lambda:False)
    monkeypatch.setattr(eq.opt,'_ea_paths',lambda *args:(binary,source,'original'))
    monkeypatch.setattr(eq,'terminal_lock',lambda *args:nullcontext())
    monkeypatch.setattr(eq,'parse_optimization_xml',lambda path:result['optimization'])
    monkeypatch.setattr(eq.opt,'_validate_observed',lambda *args:None)
    monkeypatch.setattr(eq,'_freeze_candidates',lambda *args:(selection,[candidate]))
    monkeypatch.setattr(eq,'_verify_period_report_identity',lambda *args:None)
    common=tmp_path/'common';common.mkdir()
    monkeypatch.setattr(eq,'_common_files',lambda:common)
    return folder,data,common,manifest


def test_compile_failure_truthful_and_original_preserved(tmp_path,monkeypatch):
    folder,data,common,manifest=fixture_run(tmp_path,monkeypatch)
    monkeypatch.setattr(eq,'compile_mql5',lambda *args,**kwargs:{'ok':False})
    outcome=eq.capture_candidate_equity('opt_a',period='in_sample',html_report=False)
    assert outcome['captures']['in_sample']['status']=='failed'
    saved=json.loads((folder/'results.json').read_text())
    assert 'equity_capture' not in saved['holdout_candidates'][0]['in_sample']
    assert saved['holdout_candidates'][0]['in_sample']['report_path']==str(folder/'original.htm')


def test_instrumented_success_and_reuse_no_second_launch(tmp_path,monkeypatch):
    folder,data,common,manifest=fixture_run(tmp_path,monkeypatch)
    def compile(source,**kwargs):
        Path(source).with_suffix('.ex5').write_bytes(b'compiled')
        return {'ok':True}
    calls=[]
    def single(replay,row,kind,start,stop,timeout):
        calls.append(kind)
        capture_id=replay['run_id']
        (common/(capture_id+'.csv')).write_text('\n'.join([
            ','.join(eq.parse_equity_capture.__globals__['HEADER']),
            f'{capture_id},{manifest["source_sha256"]},EURUSD,H1,2,0,2025.01.01 01:00:00,10000,10000',
            f'{capture_id},{manifest["source_sha256"]},EURUSD,H1,2,1,2025.01.01 02:00:00,10000,9500',
            'END,2,complete']))
        return {'report_path':str(folder/'original.htm'),'report_sha256':eq.opt._hash(folder/'original.htm')}
    monkeypatch.setattr(eq,'compile_mql5',compile)
    monkeypatch.setattr(eq,'_single_backtest',single)
    monkeypatch.setattr(eq,'_match_details',lambda *args:None)
    monkeypatch.setattr(eq,'parse_tester_report',lambda *args:{'net_profit':10,'total_trades':2})
    outcome=eq.capture_candidate_equity('opt_a',period='in_sample',html_report=False)
    assert outcome['captures']['in_sample']['status']=='completed'
    reused=eq.capture_candidate_equity('opt_a',period='in_sample',html_report=False)
    assert reused['captures']['in_sample']['status']=='reused'
    assert calls==['in_sample']
    assert reused['captures']['in_sample']['optimization_pass_id']==3
    # A reused CSV is not enough: preserve and verify its replay evidence too.
    saved=json.loads((folder/'results.json').read_text())
    replay_report=folder/'separate_replay.htm'
    replay_report.write_text('original replay')
    metadata=saved['holdout_candidates'][0]['in_sample']['equity_capture']
    metadata['replay_report_path']=str(replay_report)
    metadata['replay_report_sha256']=eq.opt._hash(replay_report)
    (folder/'results.json').write_text(json.dumps(saved))
    replay_report.write_text('tampered replay')
    with pytest.raises(ValueError,match='replay report changed'):
        eq.capture_candidate_equity('opt_a',period='in_sample',html_report=False)


def test_advanced_xml_inputs_normalized_before_provenance_comparison(tmp_path,monkeypatch):
    real_validate=eq.opt._validate_observed
    folder,_,_,manifest=fixture_run(tmp_path,monkeypatch)
    manifest['request'].update(mode='genetic',parameters={
        'Filter':{'type':'bool','value':True},
        'Method':{'type':'MAKind','value':1,'enum_values':{'SMA':0,'EMA':1}}})
    normalized={'columns':['Pass','Result','Trades','Filter','Method'],
        'rows':[{'Pass':3,'Result':10,'Trades':2,'Filter':True,'Method':1}]}
    parsed={'columns':normalized['columns'],'rows':[{'Pass':3,'Result':10,'Trades':2,'Filter':'true','Method':'EMA'}]}
    result=json.loads((folder/'results.json').read_text())
    result['optimization']=normalized
    (folder/'results.json').write_text(json.dumps(result))
    (folder/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(eq.opt,'_validate_observed',real_validate)
    monkeypatch.setattr(eq,'parse_optimization_xml',lambda path:parsed)
    monkeypatch.setattr(eq,'compile_mql5',lambda *args,**kwargs:{'ok':False})
    outcome=eq.capture_candidate_equity('opt_a',period='in_sample',html_report=False)
    assert outcome['captures']['in_sample']['status']=='failed'
    assert 'compile' in outcome['captures']['in_sample']['error']
    assert parsed==normalized
