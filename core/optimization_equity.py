"""Explicit, sequential instrumented reruns for genuine candidate equity data."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from . import optimization as opt
from .compiler import compile_mql5
from .equity_capture import build_equity_adapter, parse_equity_capture
from .optimization_holdout import (_candidate_hash, _freeze_candidates,
                                   _single_backtest, _verify_period_report_identity)
from .optimization_results import parse_optimization_xml
from .report_balance_curve import deal_balance_points
from .terminal_lock import terminal_lock
from .tester import parse_tester_report


def _match_details(original: dict, captured: dict) -> None:
    if abs(original['metrics']['net_profit'] - captured['metrics']['net_profit']) > .02:
        raise ValueError('Instrumented EA profit differs from original candidate')
    if original['metrics']['total_trades'] != captured['metrics']['total_trades']:
        raise ValueError('Instrumented EA trade count differs from original candidate')
    if deal_balance_points(Path(original['report_path'])) != deal_balance_points(Path(captured['report_path'])):
        raise ValueError('Instrumented EA chronological deal balances differ from original candidate')


def _common_files() -> Path:
    appdata = os.environ.get('APPDATA')
    if os.name != 'nt' or not appdata:
        raise ValueError('Equity capture common-files location is qualified for native Windows only')
    return Path(appdata) / 'MetaQuotes' / 'Terminal' / 'Common' / 'Files' / 'MBT_Equity'


def capture_candidate_equity(run_id: str, pass_id: int | None = None,
                             period: str = 'both', timeout_sec: int = 120,
                             html_report: bool = True) -> dict:
    """Capture one candidate, maximum two single tests; never re-optimize.

    Only verified original details are eligible. The generated EA must reproduce
    their totals and chronological deal balances before measured equity is saved.
    """
    if period not in ('both', 'in_sample', 'holdout'):
        raise ValueError('period must be both, in_sample or holdout')
    if pass_id is not None and type(pass_id) is not int:
        raise ValueError('pass_id must be an integer or null')
    if type(timeout_sec) is not int or not 30 <= timeout_sec <= 7200:
        raise ValueError('timeout_sec must be 30..7200')
    if type(html_report) is not bool:
        raise ValueError('html_report must be boolean')
    folder = opt._run_dir(run_id).resolve()
    output = {'run_id': run_id, 'captures': {}, 'report_html': None}
    with terminal_lock(opt._terminal_path(), str(opt._data_dir())):
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        result = json.loads((folder/'results.json').read_text(encoding='utf-8'))
        if manifest.get('run_id') != run_id or Path(manifest['run_dir']).resolve() != folder:
            raise ValueError('Saved manifest does not belong to run folder')
        if (Path(manifest['terminal_path']).resolve() != Path(opt._terminal_path()).resolve()
                or Path(manifest['data_dir']).resolve() != opt._data_dir().resolve()):
            raise ValueError('Configured MT5 installation changed')
        request = manifest['request']
        opt._verify_input_schema(manifest)
        if opt.testing_settings(request.get('testing'))['execution_delay_ms'] == -1:
            raise ValueError('Random delay prevents verified equity replay')
        xml = Path(manifest['optimization_path']).resolve()
        if xml.parent != folder or opt._hash(xml) != manifest['optimization_sha256']:
            raise ValueError('Saved optimization XML changed')
        parsed_optimization = parse_optimization_xml(xml)
        opt._validate_observed(manifest, parsed_optimization)
        if parsed_optimization != result['optimization']:
            raise ValueError('Optimization rows differ from original XML')
        candidates = result.get('holdout_candidates', [])
        if not candidates or not result.get('holdout_selection'):
            raise ValueError('Equity capture requires a frozen candidate shortlist')
        expected, _ = _freeze_candidates(manifest, result, result['holdout_selection']['requested_top_n'])
        if (_candidate_hash(candidates) != result['holdout_selection']['candidates_sha256']
                or expected['candidates_sha256'] != result['holdout_selection']['candidates_sha256']):
            raise ValueError('Frozen shortlist changed')
        chosen = result['selected_pass']['Pass'] if pass_id is None else pass_id
        matching = [c for c in candidates if c['optimization_pass']['Pass'] == chosen]
        if len(matching) != 1:
            raise ValueError('Pass is not uniquely present in frozen candidates')
        candidate = matching[0]
        output['selected_pass'] = chosen
        data = Path(manifest['data_dir'])
        binary, source, _ = opt._ea_paths(request['expert'], data)
        if opt._hash(binary) != manifest['ea_sha256'] or opt._hash(source) != manifest['source_sha256']:
            raise ValueError('Original EA/source changed since optimization')
        raw_source = source.read_bytes()
        source_text = raw_source.decode('utf-16') if raw_source.startswith((b'\xff\xfe',b'\xfe\xff')) else raw_source.decode('utf-8-sig')
        model = opt._MODEL_MAP[request['model']]
        periods = ('in_sample','holdout') if period == 'both' else (period,)
        for kind in periods:
            detail = candidate.get(kind)
            if not detail or detail.get('status') != 'completed' or not detail.get('report_identity_verified'):
                raise ValueError('Candidate needs a verified original detailed report for requested period')
            baseline = Path(detail['report_path']).resolve()
            if baseline.parent != folder or opt._hash(baseline) != detail['report_sha256']:
                raise ValueError('Original candidate report changed')
            start, stop = detail['from_date'], detail['to_date']
            _verify_period_report_identity(baseline, manifest, candidate['optimization_pass'], start, stop)
            saved = detail.get('equity_capture')
            if saved:
                if any(saved.get(key) != value for key,value in {
                    'source_sha256':manifest['source_sha256'],'symbol':request['symbol'],
                    'timeframe':request['timeframe'],'model':model,'from_date':start,
                    'to_date':stop,'original_report_sha256':detail['report_sha256'],
                    'optimization_pass_id':chosen}.items()):
                    raise ValueError('Saved equity provenance does not match candidate')
                saved_path = Path(saved['path']).resolve()
                if saved_path.parent != folder:
                    raise ValueError('Saved equity capture outside run folder')
                replay_path = Path(saved['replay_report_path']).resolve()
                if replay_path.parent != folder or opt._hash(replay_path) != saved['replay_report_sha256']:
                    raise ValueError('Saved equity replay report changed')
                _match_details(detail, {'report_path': str(replay_path),
                                        'metrics': parse_tester_report(str(replay_path))})
                parse_equity_capture(saved_path, expected_sha256=saved['sha256'], **{
                    name: saved[name] for name in ('run_id','source_sha256','symbol','timeframe','model','from_date','to_date')})
                output['captures'][kind] = {'status':'reused', **saved}
                continue
            capture_id = 'opt_' + uuid.uuid4().hex
            identity = dict(run_id=capture_id, source_sha256=manifest['source_sha256'],
                            symbol=request['symbol'], timeframe=request['timeframe'],model=model)
            csv_source = _common_files()/(capture_id+'.csv')
            if csv_source.exists():
                raise ValueError('Unique capture path already exists')
            generated = data/'MQL5'/'Experts'/'MBT_Equity'/('cap_'+capture_id+'.mq5')
            try:
                if opt._terminal_busy():
                    raise RuntimeError('MT5 terminal is already running')
                adapter = build_equity_adapter(source_text, **identity)
                generated.parent.mkdir(parents=True,exist_ok=True)
                with generated.open('x',encoding='utf-8') as stream:
                    stream.write(adapter)
                compiled = compile_mql5(str(generated), timeout=timeout_sec)
                ex5 = generated.with_suffix('.ex5')
                if not compiled.get('ok') or not ex5.is_file() or ex5.stat().st_mtime < generated.stat().st_mtime:
                    raise RuntimeError('Generated equity recorder did not compile successfully')
                replay = copy.deepcopy(manifest)
                replay['run_id'] = capture_id
                replay['request']['expert'] = 'MBT_Equity\\'+generated.stem
                if opt._hash(binary) != manifest['ea_sha256'] or opt._hash(source) != manifest['source_sha256']:
                    raise ValueError('Original EA changed before instrumented replay')
                captured = _single_backtest(replay,candidate['optimization_pass'],kind,start,stop,timeout_sec)
                _match_details(detail,captured)
                if opt._hash(binary) != manifest['ea_sha256'] or opt._hash(source) != manifest['source_sha256']:
                    raise ValueError('Original EA changed during instrumented replay')
                if not csv_source.is_file():
                    raise ValueError('MT5 did not produce equity observations')
                csv_hash = opt._hash(csv_source)
                parsed = parse_equity_capture(csv_source,expected_sha256=csv_hash,from_date=start,to_date=stop,**identity)
                target = folder/(capture_id+'_equity.csv')
                if target.exists():
                    raise ValueError('Saved unique equity artifact already exists')
                shutil.copy2(csv_source,target)
                meta = dict(path=str(target),sha256=csv_hash,from_date=start,to_date=stop,
                    point_count=parsed['point_count'],sampling=parsed['sampling'],source=parsed['source'],
                    limitations=parsed['limitations'],generated_source_sha256=opt._hash(generated),
                    generated_binary_sha256=opt._hash(ex5),replay_report_path=captured['report_path'],
                    replay_report_sha256=captured['report_sha256'],original_report_sha256=detail['report_sha256'],
                    original_deal_balances_verified=True,**identity)
                meta['optimization_pass_id'] = chosen
                detail['equity_capture'] = meta
                detail.pop('equity_capture_error',None)
                output['captures'][kind] = {'status':'completed',**meta}
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                detail['equity_capture_error'] = str(exc)
                output['captures'][kind] = {'status':'failed','error':str(exc)}
            if chosen == result['selected_pass']['Pass']:
                result[kind] = detail
            opt._write_json(folder/'results.json',result)
    output['status'] = 'completed' if all(c['status'] in ('completed','reused') for c in output['captures'].values()) else 'partial_or_failed'
    if html_report:
        try:
            output['report_html'] = opt.render_experiment_report(run_id)['report_html']
        except Exception as exc:
            output['report_error'] = str(exc)
    return output
