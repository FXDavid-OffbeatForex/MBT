import copy
import hashlib
import json
from pathlib import Path

import pytest

from core import monte_carlo as mc
from core import optimization as opt
from core.optimization_holdout import _freeze_candidates
from core.optimization_results import parse_optimization_xml
from core.report_monte_carlo import render_monte_carlo

HEADER = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
          'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']


def report(path, profits=(100, -120, 80, -60, 30)):
    def row(cells):
        return '<tr>' + ''.join(f'<td>{v}</td>' for v in cells) + '</tr>'
    rows = [['2025.01.10 00:00:00', '1', '', 'balance', '', '', '', '',
             '0', '0', '5000', '5000', 'initial deposit']]
    balance = 5000
    for index, profit in enumerate(profits):
        balance = round(balance - .1, 2)
        rows.append([f'2025.01.10 00:{index*2+1:02}:00', str(index*2+2), 'EURUSD',
                     'buy', 'in', '.1', '1', '1', '-.1', '0', '0', str(balance), ''])
        balance = round(balance + profit - .1, 2)
        rows.append([f'2025.01.10 00:{index*2+2:02}:00', str(index*2+3), 'EURUSD',
                     'sell', 'out', '.1', '1', '1', '-.1', '0', str(profit), str(balance), ''])
    identity = ('<b>TestEA</b><b>EURUSD</b><b>H1 (2025.01.10 - 2025.01.20)</b><b>Fast=1</b>')
    account = '<table>'+row(['Currency:','USD','Initial Deposit:','5000','Leverage:','1:100'])+'</table>'
    summary = '<table>'+row(['Total Net Profit:',round(balance-5000,2),'Total Trades:',len(profits)])+'</table>'
    path.write_text(identity + account + summary + '<table>' + row(HEADER) + ''.join(row(r) for r in rows) + '</table>', encoding='utf-16')
    return rows


@pytest.fixture
def history(tmp_path):
    path = tmp_path / 'report.htm'
    report(path)
    return mc.read_trade_blocks(path, 'EURUSD')


def test_shuffle_profit_constant_but_drawdown_changes(history):
    result = mc.simulate(history, 'shuffle', 1000, 42)
    assert result['terminal_net_profit'] == dict(p05=29, p50=29, p95=29)
    assert result['max_balance_dd']['p05'] < result['max_balance_dd']['p95']
    assert result['observed']['net_profit'] == 29
    assert result['trade_count'] == 5
    assert 'Only 5' in result['warnings'][0]


@pytest.mark.parametrize('method', ['shuffle', 'bootstrap'])
def test_deterministic_seed_and_histogram_counts(history, method):
    a = mc.simulate(history, method, 200, 123)
    assert a == mc.simulate(history, method, 200, 123)
    assert a != mc.simulate(history, method, 200, 124)
    assert sum(a['drawdown_histogram']['counts']) == 200
    assert sum(a['profit_histogram']['counts']) == 200
    assert a['balance_bands'][0]['p50'] == 5000
    assert a['balance_bands'][-1]['closed_trades'] == 5


def test_bootstrap_profit_varies(history):
    result = mc.simulate(history, 'bootstrap', 1000, 42)
    assert result['terminal_net_profit']['p05'] < result['terminal_net_profit']['p95']
    assert 0 < result['sampled_loss_fraction'] < 1


@pytest.mark.parametrize('method,count,seed', [('wrong',100,1),('shuffle',99,1),
    ('shuffle',5001,1),('shuffle',True,1),('shuffle',100,-1),('shuffle',100,True)])
def test_limits(history, method, count, seed):
    with pytest.raises(ValueError):
        mc.simulate(history, method, count, seed)


def test_event_budget_and_checkpoint_cap(history):
    big = copy.deepcopy(history)
    big['blocks'] *= 1000
    with pytest.raises(ValueError, match='budget'):
        mc.simulate(big, 'bootstrap', 1000, 42)
    big['blocks'] = history['blocks'] * 30
    assert len(mc.simulate(big, 'shuffle', 100, 42)['balance_bands']) == 101


@pytest.mark.parametrize('before,after', [('4999.9','4999.8'), ('<td>in</td>','<td>in/out</td>'),
    ('<td>EURUSD</td>','<td>GBPUSD</td>'), ('<td>out</td>','<td>in</td>'),
    ('<td>100</td>','<td>NaN</td>'), ('<td>0</td>','<td>0.001</td>')])
def test_bad_or_unsupported_deals_fail_closed(tmp_path, before, after):
    path = tmp_path / 'bad.htm'
    report(path)
    markup = path.read_text(encoding='utf-16')
    assert before in markup
    path.write_text(markup.replace(before,after,1),encoding='utf-16')
    with pytest.raises(ValueError):
        mc.read_trade_blocks(path,'EURUSD')


def test_too_few_closed_trades(tmp_path):
    path = tmp_path / 'short.htm'
    report(path, profits=(1,2,3,4))
    with pytest.raises(ValueError,match='five'):
        mc.read_trade_blocks(path,'EURUSD')


def test_partial_exit_cash_stays_in_one_trade_block(tmp_path):
    path = tmp_path/'partial.htm'
    report(path)
    markup=path.read_text(encoding='utf-16')
    # Replace first complete exit with two half exits, preserving total costs.
    original=('<td>2025.01.10 00:02:00</td><td>3</td><td>EURUSD</td>'
              '<td>sell</td><td>out</td><td>.1</td><td>1</td><td>1</td>'
              '<td>-.1</td><td>0</td><td>100</td><td>5099.8</td><td></td>')
    first=original.replace('<td>.1</td>','<td>.05</td>').replace('<td>-.1</td>','<td>-.05</td>')
    first=first.replace('<td>100</td>','<td>50</td>').replace('<td>5099.8</td>','<td>5049.85</td>')
    second=first.replace('<td>3</td>','<td>100</td>').replace('<td>5049.85</td>','<td>5099.8</td>')
    assert original in markup
    path.write_text(markup.replace(original,first+'</tr><tr>'+second),encoding='utf-16')
    history=mc.read_trade_blocks(path,'EURUSD')
    assert history['blocks'][0]==[-10,4995,4995]
    assert history['trade_count']==5 and history['exit_deal_count']==6
    assert history['net_profit_cents']==2900


def test_open_final_trade_is_rejected(tmp_path):
    path=tmp_path/'open.htm'
    report(path)
    markup=path.read_text(encoding='utf-16')
    begin=markup.rfind('<tr>')
    path.write_text(markup[:begin]+'</table>',encoding='utf-16')
    with pytest.raises(ValueError,match='Open trades'):
        mc.read_trade_blocks(path,'EURUSD')


def test_zero_crossing_explicit_not_stopout(history):
    small=copy.deepcopy(history)
    small['initial_cents']=100
    result=mc.simulate(small,'shuffle',100,42)
    assert result['synthetic_zero_crossing_fraction']>0
    assert any('not a broker stopout' in w for w in result['warnings'])


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    folder = tmp_path / ('opt_'+'a'*32)
    folder.mkdir()
    monkeypatch.setattr(opt, '_run_dir', lambda run_id: folder)
    monkeypatch.setattr('core.terminal_lock.reports_dir',lambda: str(tmp_path))
    monkeypatch.setattr('core.commission_profile._journal_path',lambda data: tmp_path/'unused_journal.json')
    xml = folder/'test.xml'
    headers=['Pass','Fast','Result','Profit','Trades']
    def xr(values):
        return '<Row>'+''.join(f'<Cell><Data>{v}</Data></Cell>' for v in values)+'</Row>'
    xml.write_text('<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table>'+
        xr(headers)+xr([1,1,5029,29,5])+xr([2,2,5000,0,5])+'</Table></Worksheet></Workbook>')
    manifest = {'run_id':folder.name,'run_dir':str(folder),'status':'completed',
        'terminal_path':str(tmp_path/'terminal.exe'),'data_dir':str(tmp_path/'data'),
        'optimization_path':str(xml),'optimization_sha256':opt._hash(xml),'planned_combinations':2,
        'request':{'expert':'TestEA','symbol':'EURUSD','timeframe':'H1','mode':'complete',
            'model':'open_prices','min_trades':1,'from_date':'2025-01-01','to_date':'2025-01-10',
            'testing':{'deposit':5000,'currency':'USD','leverage':100},
            'parameters':{'Fast':{'type':'int','value':1,'start':1,'step':1,'stop':2}}}}
    parsed = parse_optimization_xml(xml)
    opt._validate_observed(manifest,parsed)
    results = {'optimization':parsed,'forward':None,'counts':{},'selected_pass':parsed['rows'][0]}
    selection,candidates = _freeze_candidates(manifest,results,1)
    path=folder/'detail.htm'
    report(path)
    detail={'status':'completed','selected_pass':1,'report_identity_verified':True,
        'report_path':str(path),'report_sha256':opt._hash(path),'from_date':'2025-01-10',
        'to_date':'2025-01-20','metrics':{'net_profit':29,'total_trades':5}}
    candidates[0]['holdout']=detail
    results.update(holdout_selection=selection,holdout_candidates=candidates,holdout=detail)
    opt._write_json(folder/'manifest.json',manifest)
    opt._write_json(folder/'results.json',results)
    return folder,manifest,results


def test_saved_run_preserves_selection_and_report(experiment):
    folder,manifest,results=experiment
    original_manifest=(folder/'manifest.json').read_bytes()
    original_report=(folder/'detail.htm').read_bytes()
    output=mc.run_monte_carlo(folder.name,simulations=100,html_report=False)
    updated=json.loads((folder/'results.json').read_text())
    assert updated['selected_pass']==results['selected_pass']
    assert updated['holdout_selection']==results['holdout_selection']
    assert (folder/'manifest.json').read_bytes()==original_manifest
    assert (folder/'detail.htm').read_bytes()==original_report
    loaded=mc.load_analysis(updated['monte_carlo'][0],manifest,updated,folder)
    assert loaded['provenance']['period']=='holdout'
    assert loaded['provenance']['pass_id']==1
    assert output['summary']['terminal_net_profit']['p50']==29


def test_no_period_fallback_or_nonfrozen_pass(experiment):
    folder,_,_=experiment
    with pytest.raises(ValueError,match='no period fallback'):
        mc.run_monte_carlo(folder.name,period='in_sample',html_report=False)
    with pytest.raises(ValueError,match='unique frozen'):
        mc.run_monte_carlo(folder.name,pass_id=2,html_report=False)
    assert not list(folder.glob('mc_*.json'))


@pytest.mark.parametrize('target', ['xml','report','freeze','totals'])
def test_changed_evidence_rejected(experiment,target):
    folder,manifest,results=experiment
    if target=='xml':
        (folder/'test.xml').write_text('changed')
    elif target=='report':
        (folder/'detail.htm').write_text('changed')
    elif target=='freeze':
        results['holdout_candidates'][0]['parameters']['Fast']=2
    else:
        results['holdout_candidates'][0]['holdout']['metrics']['net_profit']=30
    opt._write_json(folder/'results.json',results)
    with pytest.raises(ValueError):
        mc.run_monte_carlo(folder.name,simulations=100,html_report=False)


def test_saved_charts_and_corruption(experiment):
    folder,manifest,_=experiment
    output=mc.run_monte_carlo(folder.name,simulations=100,method='bootstrap',html_report=False)
    updated=json.loads((folder/'results.json').read_text())
    html=render_monte_carlo(manifest,updated,folder)
    assert 'Synthetic balance bands' in html
    assert 'Sampled maximum balance drawdown' in html
    assert 'Sampled terminal net profit' in html
    assert 'Only 5' in html
    assert '<polygon' in html and '<rect' in html
    Path(output['path']).write_text('{}')
    assert 'analysis unavailable' in render_monte_carlo(manifest,updated,folder)


def test_cap_before_any_new_output(experiment):
    folder,_,results=experiment
    results['monte_carlo']=[{}]*100
    opt._write_json(folder/'results.json',results)
    with pytest.raises(ValueError,match='100 analyses'):
        mc.run_monte_carlo(folder.name,html_report=False)
    assert not list(folder.glob('mc_*.json'))


def test_render_failure_retains_completed_analysis(experiment,monkeypatch):
    folder,_,_=experiment
    def failed(*args):
        raise ValueError('render failed')
    monkeypatch.setattr(opt,'render_experiment_report',failed)
    output=mc.run_monte_carlo(folder.name,simulations=100)
    assert output['report_error']=='render failed'
    assert Path(output['path']).is_file()


def test_in_sample_requires_separate_matching_dates(experiment):
    folder,manifest,results=experiment
    detail=copy.deepcopy(results['holdout'])
    path=folder/'is.htm'
    text=(folder/'detail.htm').read_text(encoding='utf-16')
    text=text.replace('2025.01.10 - 2025.01.20','2025.01.01 - 2025.01.10')
    text=text.replace('2025.01.10 00:','2025.01.01 00:')
    path.write_text(text,encoding='utf-16')
    detail.update(report_path=str(path),report_sha256=opt._hash(path),from_date='2025-01-01',to_date='2025-01-10')
    results['holdout_candidates'][0]['in_sample']=detail
    opt._write_json(folder/'results.json',results)
    output=mc.run_monte_carlo(folder.name,period='in_sample',simulations=100,html_report=False)
    stored=json.loads(Path(output['path']).read_text())
    assert stored['provenance']['period']=='in_sample'
    assert stored['provenance']['from_date']=='2025-01-01'


def test_report_escapes_analysis_labels(experiment):
    folder,manifest,_=experiment
    output=mc.run_monte_carlo(folder.name,simulations=100,html_report=False)
    results=json.loads((folder/'results.json').read_text())
    path=Path(output['path'])
    data=json.loads(path.read_text())
    data['warnings'].append('<img src=x onerror=alert(1)>')
    opt._write_json(path,data)
    results['monte_carlo'][0]['sha256']=opt._hash(path)
    rendered=render_monte_carlo(manifest,results,folder)
    assert '<img src=x' not in rendered
    assert '&lt;img' in rendered


def test_inspect_saved_analysis_without_rerunning(experiment,monkeypatch):
    folder,_,_=experiment
    output=mc.run_monte_carlo(folder.name,simulations=100,html_report=False)
    def no_rerun(*args):
        raise AssertionError('No resampling during inspection')
    monkeypatch.setattr(mc,'simulate',no_rerun)
    stored=mc.get_monte_carlo_results(folder.name,output['analysis_id'])
    assert stored['analysis']['terminal_net_profit']['p50']==29
    Path(output['path']).write_text('{}')
    with pytest.raises(ValueError):
        mc.get_monte_carlo_results(folder.name,output['analysis_id'])


def test_inspection_id_confinement(experiment):
    folder,_,_=experiment
    with pytest.raises(ValueError,match='Invalid analysis_id'):
        mc.get_monte_carlo_results(folder.name,'../../secrets')
    with pytest.raises(ValueError,match='not uniquely present'):
        mc.get_monte_carlo_results(folder.name,'mc_'+'0'*32)
