"""Detailed MT5 in-sample and out-of-sample checks for frozen EA candidates.

This is deliberately not MT5's optimization Forward Results table. It uses a
fixed split and a separate single backtest, so cached forward XML is irrelevant.
"""

from __future__ import annotations

import json
import html
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from . import optimization as opt
from .terminal_lock import terminal_lock
from .tester import _launch_cmd, parse_tester_report
from .commission_profile import managed_commission_profile, verify_commission_report
from .optimization_log import tester_log_snapshot, tester_log_commission


def _fixed_set(parameters: dict, selected: dict) -> str:
    lines = []
    for name, item in parameters.items():
        value = selected[name] if "start" in item else item["value"]
        typ = item["type"]
        if typ in ("int", "long", "double", "float") or "enum_values" in item:
            number = (opt._enum_value(value, item, name) if "enum_values" in item
                      else opt._number(value, name))
            opt._representable(number, typ, name)
            if typ in ("int", "long") and number != number.to_integral_value():
                raise ValueError(f"{name} is not an integer")
            lines.append(f"{name}={number}||{number}||1||{number}||N")
        elif typ == "bool":
            value = opt._bool_value(value, name)
            lines.append(f"{name}={'true' if value else 'false'}")
        elif typ == "string":
            lines.append(f"{name}={'' if value == '' else opt._clean_line(value, name)}")
        else:
            raise ValueError(f"Unsupported fixed input {name}")
    return "\n".join(lines) + "\n"


def _verify_report_identity(report: Path, manifest: dict, selected: dict) -> None:
    """Check the visible MT5 settings, not merely a report's existence."""
    _verify_period_report_identity(
        report, manifest, selected,
        manifest["holdout_cutoff_date"], manifest["holdout_end_date"],
    )


def _report_markup(report: Path) -> str:
    raw = report.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _verify_period_report_identity(report: Path, manifest: dict, selected: dict,
                                   start: str, end: str) -> None:
    markup = _report_markup(report)
    bold = [html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
            for value in re.findall(r"<b\b[^>]*>(.*?)</b>", markup, re.I | re.S)]
    request = manifest["request"]
    expected_period = (f"{request['timeframe']} "
                       f"({start.replace('-', '.')} - {end.replace('-', '.')})")
    if (request["expert"].split("\\")[-1] not in bold
            or request["symbol"] not in bold or expected_period not in bold):
        raise ValueError("Holdout report EA, symbol or period does not match the request")
    if request.get('testing'):
        testing = opt.testing_settings(request['testing'])
        settings = {}
        for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', markup, re.I | re.S):
            cells = [html.unescape(re.sub(r'<[^>]+>', '', cell)).strip()
                     for cell in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, re.I | re.S)]
            for index, cell in enumerate(cells[:-1]):
                if cell in ('Currency:', 'Initial Deposit:', 'Leverage:'):
                    settings[cell] = cells[index + 1]
        if (settings.get('Currency:') != testing['currency']
                or settings.get('Leverage:') != f"1:{testing['leverage']}"
                or opt._number(settings.get('Initial Deposit:', '').replace(' ', '').replace('\u00a0', ''), 'report deposit')
                   != opt._number(testing['deposit'], 'requested deposit')):
            raise ValueError('Detailed MT5 account settings do not match requested testing settings')
    actual_inputs = {}
    for item in bold:
        if "=" in item:
            name, value = item.split("=", 1)
            actual_inputs[name] = value
    for name, item in request["parameters"].items():
        expected = selected[name] if "start" in item else item["value"]
        actual = actual_inputs.get(name)
        if actual is None:
            raise ValueError(f"Holdout report lacks input {name}")
        if "enum_values" in item:
            if opt._enum_value(actual, item, name) != opt._enum_value(expected, item, name):
                raise ValueError(f"Holdout report mismatches input {name}")
        elif item["type"] == "bool":
            if opt._bool_value(actual, name) != opt._bool_value(expected, name):
                raise ValueError(f"Holdout report mismatches input {name}")
        elif item["type"] in ("int", "long", "float", "double"):
            if opt._number(actual, name) != opt._number(expected, name):
                raise ValueError(f"Holdout report mismatches input {name}")
        elif actual != (str(expected).lower() if item["type"] == "bool" else expected):
            raise ValueError(f"Holdout report mismatches input {name}")


def _copy_report_assets(report: Path, data: Path, folder: Path) -> dict:
    """Preserve only MT5's bounded, same-basename PNGs referenced by this report."""
    names = re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']',
                       _report_markup(report), re.I)
    if not 1 <= len(names) <= 10 or len(set(names)) != len(names):
        raise ValueError("MT5 report has missing or duplicate graph images")
    stem = re.escape(report.stem)
    allowed = re.compile(rf"^{stem}(?:-[A-Za-z0-9_-]+)?\.png$", re.I)
    assets = []
    for name in names:
        if not allowed.fullmatch(name):
            raise ValueError("MT5 report references an unexpected graph image")
        source = data / name
        if not source.is_file() or not 0 < source.stat().st_size <= 10 * 1024 * 1024:
            raise ValueError(f"MT5 graph image is missing or oversized: {name}")
        target = folder / name
        if target.exists() and opt._hash(target) != opt._hash(source):
            raise ValueError(f"Saved MT5 graph image differs from source: {name}")
        if not target.exists():
            shutil.copy2(source, target)
        assets.append({"path": str(target), "sha256": opt._hash(target)})
    balance = folder / (report.stem + ".png")
    if not balance.is_file():
        raise ValueError("MT5 report has no balance graph image")
    return {"graph_assets": assets, "balance_graph_path": str(balance),
            "balance_graph_sha256": opt._hash(balance)}


def _verify_in_sample_totals(selected: dict, metrics: dict) -> None:
    if (isinstance(selected.get("Profit"), (int, float))
            and abs(metrics["net_profit"] - selected["Profit"]) > 0.02):
        raise ValueError("Candidate in-sample profit differs from optimization XML")
    if (isinstance(selected.get("Trades"), (int, float))
            and metrics["total_trades"] != selected["Trades"]):
        raise ValueError("Candidate in-sample trade count differs from optimization XML")


def _single_backtest(manifest: dict, selected: dict, kind: str,
                     start: str, end: str, timeout_sec: int,
                     candidate_rank: int | None = None) -> dict:
    if kind not in ("in_sample", "holdout"):
        raise ValueError("Unsupported selected-pass backtest period")
    if candidate_rank is not None and (type(candidate_rank) is not int
                                       or not 2 <= candidate_rank <= 1000000):
        raise ValueError("Invalid holdout candidate rank")
    run_id = manifest["run_id"]
    folder = Path(manifest["run_dir"])
    data = Path(manifest["data_dir"])
    request = manifest["request"]
    profile = data / "MQL5" / "Profiles" / "Tester"
    opt._verify_input_schema(manifest)
    testing = opt.testing_settings(request.get('testing'))
    suffix = f"_{kind}" if candidate_rank is None else f"_{kind}_candidate_{candidate_rank}"
    set_name = run_id + suffix + ".set"
    set_text = _fixed_set(request["parameters"], selected)
    local_set = folder / set_name
    local_set.write_text(set_text, encoding="utf-8")
    with (profile / set_name).open("x", encoding="utf-8") as stream:
        stream.write(set_text)
    report_name = run_id + suffix
    ini = folder / (report_name + ".ini")
    ini_lines = [
        "[Experts]", "Enabled=0", "AllowLiveTrading=0", "AllowDllImport=0", "",
        "[StartUp]", "Expert=", "", "[Tester]", f"Expert={request['expert']}",
        f"ExpertParameters={set_name}", f"Symbol={request['symbol']}",
        f"Period={request['timeframe']}", f"Model={opt._MODEL_MAP[request['model']]}",
        "Optimization=0", "ForwardMode=0",
        f"FromDate={start.replace('-', '.')}",
        f"ToDate={end.replace('-', '.')}",
        *opt.testing_ini(testing),
        "UseLocal=1", "UseRemote=0", "UseCloud=0", "Visual=0",
        f"Report={report_name}", "ReplaceReport=0", "ShutdownTerminal=1",
    ]
    ini.write_text("\n".join(ini_lines) + "\n", encoding="utf-8")
    expected = data / (report_name + ".htm")
    if expected.exists():
        raise RuntimeError("Unique selected-pass report path already exists")
    if opt._terminal_busy():
        raise RuntimeError("MT5 became busy before selected-pass backtest launch")
    kwargs = {"cwd": str(Path(opt._terminal_path()).parent),
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "timeout": timeout_sec, "check": False}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    commission_proof = {}
    snapshot = tester_log_snapshot(data)
    with managed_commission_profile(data, testing.get('commission'), folder) as evidence:
        if evidence and evidence['template_sha256'] != testing['commission_template_sha256']:
            raise ValueError('Native commission template changed before replay')
        completed = subprocess.run(_launch_cmd(str(ini)), **kwargs)
        if evidence:
            commission_proof['commission_verification'] = tester_log_commission(snapshot, evidence)
            commission_proof['commission_profile'] = evidence
    if completed.returncode != 0:
        raise RuntimeError(f"Selected-pass terminal exited {completed.returncode}")
    if not expected.is_file() or not expected.stat().st_size:
        raise RuntimeError("MT5 did not export the selected-pass single-test report")
    target = folder / expected.name
    shutil.copy2(expected, target)
    if testing.get('commission'):
        commission_proof['commission_deals'] = verify_commission_report(target, testing['commission'], request['symbol'])
    _verify_period_report_identity(target, manifest, selected, start, end)
    graphs = _copy_report_assets(target, data, folder)
    metrics = parse_tester_report(str(target))
    if not isinstance(metrics.get("net_profit"), (int, float)) or not isinstance(metrics.get("total_trades"), (int, float)):
        raise ValueError("Selected-pass report lacks net profit or trade count")
    if kind == "in_sample":
        _verify_in_sample_totals(selected, metrics)
    metrics.pop("_source", None)
    return {"status": "completed", "kind": "independent_single_backtest",
            "period": kind, "selected_pass": selected["Pass"], "from_date": start,
            "to_date": end, "parameters": {
                name: selected[name] if "start" in item else item["value"]
                for name, item in request["parameters"].items()},
            "metrics": metrics, "report_path": str(target),
            "report_sha256": opt._hash(target), "report_identity_verified": True,
            "ini_sha256": opt._hash(ini),
            "set_sha256": opt._hash(local_set), **graphs, **commission_proof}


def _holdout_limits(top_n: int | None, budget_sec: int) -> None:
    if top_n is not None and (type(top_n) is not int or not 1 <= top_n <= 10000):
        raise ValueError("holdout_top_n must be null or an integer between 1 and 10000")
    if type(budget_sec) is not int or not 30 <= budget_sec <= 7200:
        raise ValueError("holdout_budget_sec must be an integer between 30 and 7200")


def _candidate_hash(candidates: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        [{"rank": c["rank"], "optimization_pass": c["optimization_pass"],
          "parameters": c["parameters"]} for c in candidates],
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _freeze_candidates(manifest: dict, result: dict, top_n: int | None) -> tuple[dict, list[dict]]:
    """Select from in-sample evidence only, preserving the original tie order."""
    request = manifest["request"]
    eligible = [row for row in result["optimization"]["rows"]
                if type(row.get("Result")) in (int, float) and math.isfinite(row["Result"])
                and type(row.get("Trades")) in (int, float) and math.isfinite(row["Trades"])
                and row["Trades"] >= request["min_trades"]]
    eligible.sort(key=lambda row: row["Result"], reverse=True)
    candidates, seen = [], set()
    for row in eligible:
        fixed = _fixed_set(request["parameters"], row)
        if fixed in seen:
            continue
        seen.add(fixed)
        candidates.append({"rank": len(candidates) + 1, "status": "pending",
                           "optimization_pass": dict(row),
                           "parameters": {name: row[name] if "start" in item else item["value"]
                                          for name, item in request["parameters"].items()}})
    mode = manifest.get('tester_diagnostics', {}).get('effective_mode')
    if mode not in ('complete', 'genetic'):
        mode = request['mode']
    fraction = .25 if mode == 'genetic' else .10
    selected_count = (min(len(candidates), max(256, math.ceil(len(candidates) * fraction)))
                      if top_n is None else min(top_n, len(candidates)))
    candidates = candidates[:selected_count]
    if not candidates or candidates[0]["optimization_pass"] != result["selected_pass"]:
        raise ValueError("Frozen shortlist does not match the saved in-sample winner")
    selection = {"method": "mt5_sized_eligible_subset" if top_n is None else "explicit_top_n",
                 "requested_top_n": top_n, "selection_mode": mode,
                 "fraction": fraction if top_n is None else None,
                 "minimum_candidates": 256 if top_n is None else None,
                 "eligible_distinct_count": len(seen),
                 "rounding": "Round percentage upward to a whole candidate",
                 "frozen_count": len(candidates), "min_trades": request["min_trades"],
                 "frozen_at": datetime.now(timezone.utc).isoformat(),
                 "prior_holdout_inspection": "not_attested",
                 "winner_policy": "Keep the in-sample winner; no selection on holdout results",
                 "pass_ids": [item["optimization_pass"]["Pass"] for item in candidates]}
    selection["candidates_sha256"] = _candidate_hash(candidates)
    return selection, candidates


def _run_holdout_shortlist(manifest: dict, result: dict, timeout_sec: int,
                          top_n: int | None, budget_sec: int, resume: bool = False) -> None:
    """Persist the frozen plan before running any selected-pass detail or holdout."""
    folder = Path(manifest["run_dir"])
    if resume:
        selection, candidates = result['holdout_selection'], result['holdout_candidates']
        if _candidate_hash(candidates) != selection['candidates_sha256']:
            raise ValueError('Frozen candidate selection was changed')
        if candidates[0]['optimization_pass'] != result['selected_pass']:
            raise ValueError('Saved in-sample winner changed')
        for item in candidates:
            for period in ('in_sample', 'holdout'):
                detail = item.get(period)
                if not detail:
                    continue
                report = Path(detail['report_path']).resolve()
                if report.parent != folder.resolve() or opt._hash(report) != detail['report_sha256']:
                    raise ValueError('Completed candidate report changed; cannot resume')
        saved_training = result.get('in_sample')
        if saved_training:
            report = Path(saved_training['report_path']).resolve()
            if report.parent != folder.resolve() or opt._hash(report) != saved_training['report_sha256']:
                raise ValueError('Saved in-sample report changed; cannot resume')
    else:
        selection, candidates = _freeze_candidates(manifest, result, top_n)
        result["holdout_selection"], result["holdout_candidates"] = selection, candidates
    manifest["holdout_budget_sec"] = budget_sec
    manifest["holdout_batch_status"] = "running"
    manifest['status'] = 'holdout_running'
    manifest.pop('holdout_batch_error', None)
    opt._write_json(folder / "results.json", result)
    opt._write_json(folder / "manifest.json", manifest)
    started = time.monotonic()
    budget_exhausted = False
    per_test_limit = min(timeout_sec, budget_sec)

    def remaining_timeout() -> int:
        remaining = math.ceil(budget_sec - (time.monotonic() - started))
        if remaining < per_test_limit:
            raise RuntimeError("Shared holdout time budget exhausted")
        binary, _, _ = opt._ea_paths(manifest["request"]["expert"], Path(manifest["data_dir"]))
        if opt._hash(binary) != manifest["ea_sha256"]:
            raise RuntimeError("EA changed between optimization and holdout tests")
        return per_test_limit

    request = manifest["request"]
    try:
        if not result.get('in_sample'):
            result["in_sample"] = _single_backtest(
                manifest, result["selected_pass"], "in_sample", request["from_date"],
                manifest["holdout_cutoff_date"], remaining_timeout(),
            )
        manifest["selected_detail_status"] = "completed"
        candidates[0]['in_sample'] = result['in_sample']
        if result.get('holdout'):
            candidates[0]['holdout'] = result['holdout']
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        manifest.update(selected_detail_status="failed", selected_detail_error=str(exc),
                        holdout_status="not_run", holdout_batch_status="not_run",
                        status="selected_detail_failed")
        for item in candidates:
            item.update(status="not_run", error="Selected in-sample detail failed")
    else:
        opt._write_json(folder / "results.json", result)
        opt._write_json(folder / "manifest.json", manifest)
        for index, item in enumerate(candidates):
            if item['status'] == 'failed':
                continue
            if item.get('in_sample') and item.get('holdout'):
                item['status'] = 'completed'
                continue
            item["status"] = "running"
            item.pop('error', None)
            item.pop('failed_period', None)
            item.pop('incomplete_period', None)
            opt._write_json(folder / "results.json", result)
            period = 'in_sample'
            try:
                for period in ('in_sample', 'holdout'):
                    if item.get(period):
                        continue
                    start, end = ((request['from_date'], manifest['holdout_cutoff_date'])
                                  if period == 'in_sample' else
                                  (manifest['holdout_cutoff_date'], manifest['holdout_end_date']))
                    args = (manifest, item['optimization_pass'], period, start, end, remaining_timeout())
                    detail = (_single_backtest(*args) if index == 0 else
                              _single_backtest(*args, candidate_rank=item['rank']))
                    item[period] = detail
                    if index == 0 and period == 'holdout':
                        result['holdout'] = detail
                        manifest['holdout_status'] = 'completed'
                    opt._write_json(folder / 'results.json', result)
                item['status'] = 'completed'
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                budget_exhausted = str(exc) == 'Shared holdout time budget exhausted'
                item.update(status="not_run" if budget_exhausted else "failed", error=str(exc))
                item['incomplete_period' if budget_exhausted else 'failed_period'] = period
                for pending in candidates[index + 1:]:
                    if pending['status'] not in ('completed', 'failed'):
                        pending.update(status="not_run", error="Stopped after a candidate failure or budget exhaustion")
                if index == 0 and period == 'holdout' and not budget_exhausted:
                    manifest.update(holdout_status="failed", holdout_error=str(exc))
                manifest["holdout_batch_error"] = str(exc)
                break
            finally:
                opt._write_json(folder / "results.json", result)
                opt._write_json(folder / "manifest.json", manifest)
        completed = sum(c["status"] == "completed" for c in candidates)
        manifest["holdout_batch_status"] = ("paused_budget" if budget_exhausted else
                                             "completed" if completed == len(candidates)
                                             else "partial" if completed else "failed")
        manifest["status"] = ("completed" if completed == len(candidates) else
                              "holdout_partial" if completed or budget_exhausted else "holdout_failed")
        if not result.get('holdout') and budget_exhausted:
            manifest['holdout_status'] = 'not_run'
    elapsed = round(time.monotonic() - started, 2)
    previous_elapsed = (result.get('holdout_summary') or {}).get('elapsed_sec', 0) if resume else 0
    result["holdout_summary"] = {
        "planned": len(candidates),
        "in_sample_completed": sum(bool(c.get('in_sample')) for c in candidates),
        "holdout_completed": sum(bool(c.get('holdout')) for c in candidates),
        "in_sample_failed": sum(c['status'] == 'failed' and c.get('failed_period') == 'in_sample' for c in candidates),
        "holdout_failed": sum(c['status'] == 'failed' and c.get('failed_period', 'holdout') == 'holdout' for c in candidates),
        **{state: sum(c["status"] == state for c in candidates)
           for state in ("completed", "failed", "not_run")},
        "elapsed_sec": round(previous_elapsed + elapsed, 2),
        "last_batch_elapsed_sec": elapsed,
        "resumable": any(c['status'] == 'not_run' for c in candidates) and manifest.get('selected_detail_status') == 'completed',
    }
    opt._write_json(folder / "results.json", result)
    opt._write_json(folder / "manifest.json", manifest)


def optimize_ea_holdout(
    expert: str, symbol: str, timeframe: str, from_date: str, cutoff_date: str,
    to_date: str, parameters: dict, mode: str = "complete", criterion: str = "balance",
    model: str = "open_prices", max_combinations: int = 1000,
    min_trades: int = 1, timeout_sec: int = 1800,
    html_report: bool = True,
    holdout_top_n: int | None = None, holdout_budget_sec: int = 600,
    holdout_timeout_sec: int = 120,
    testing: dict | None = None,
) -> dict:
    """Optimize in sample, then test a frozen shortlist on later dates sequentially."""
    if opt.testing_settings(testing)['execution_delay_ms'] == -1:
        raise ValueError('Random execution delays are not supported for paired holdout runs: exact in-sample replay cannot be verified; use a fixed delay')
    _holdout_limits(holdout_top_n, holdout_budget_sec)
    if type(holdout_timeout_sec) is not int or not 30 <= holdout_timeout_sec <= 7200:
        raise ValueError('holdout_timeout_sec must be an integer between 30 and 7200')
    start = opt._date(from_date, "from_date")
    cut = opt._date(cutoff_date, "cutoff_date")
    end = opt._date(to_date, "to_date")
    if not start < cut < end:
        raise ValueError("Expected from_date < cutoff_date < to_date")
    if not isinstance(html_report, bool):
        raise ValueError("html_report must be a boolean")
    with terminal_lock(opt._terminal_path(), str(opt._data_dir())):
        outcome = opt._optimize_ea_unlocked(
            expert, symbol, timeframe, from_date, cutoff_date, parameters,
            mode, criterion, "off", None, model, max_combinations,
            min_trades, timeout_sec, *([testing] if testing is not None else []),
        )
        folder = Path(outcome["run_dir"])
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["holdout_cutoff_date"] = cutoff_date
        manifest["holdout_end_date"] = to_date
        if outcome["status"] == "completed":
            result_path = folder / "results.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            _run_holdout_shortlist(manifest, result, min(timeout_sec, holdout_timeout_sec), holdout_top_n, holdout_budget_sec)
        else:
            manifest["selected_detail_status"] = "not_run"
            manifest["holdout_status"] = "not_run"
        opt._write_json(manifest_path, manifest)
    outcome["holdout_status"] = manifest["holdout_status"]
    outcome["selected_detail_status"] = manifest["selected_detail_status"]
    outcome["status"] = manifest["status"]
    outcome["holdout_error"] = manifest.get("holdout_error")
    outcome["selected_detail_error"] = manifest.get("selected_detail_error")
    outcome["holdout_batch_status"] = manifest.get("holdout_batch_status")
    if outcome["status"] in ("completed", "holdout_partial", "holdout_failed", "selected_detail_failed"):
        saved = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        outcome["holdout_summary"] = saved.get("holdout_summary")
        outcome["holdout_selection"] = saved.get("holdout_selection")
    outcome["report_html"] = None
    if html_report and outcome["status"] in ("completed", "no_eligible_pass", "holdout_partial",
                                              "holdout_failed", "selected_detail_failed"):
        try:
            outcome["report_html"] = opt.render_experiment_report(outcome["run_id"])["report_html"]
        except Exception as exc:
            outcome["report_error"] = str(exc)
    return outcome


def continue_holdout_tests(run_id: str, timeout_sec: int = 120,
                           holdout_budget_sec: int = 600, html_report: bool = True) -> dict:
    """Complete missing in-sample/holdout details of the unchanged frozen shortlist."""
    _holdout_limits(None, holdout_budget_sec)
    if type(timeout_sec) is not int or not 30 <= timeout_sec <= 7200:
        raise ValueError('timeout_sec must be an integer between 30 and 7200')
    if not isinstance(html_report, bool):
        raise ValueError('html_report must be a boolean')
    folder = opt._run_dir(run_id)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    if manifest.get('run_id') != run_id or Path(manifest['run_dir']).resolve() != folder.resolve():
        raise ValueError('Saved manifest does not belong to this run folder')
    if manifest.get('status') not in ('completed', 'holdout_partial', 'holdout_failed', 'holdout_running'):
        raise ValueError('Run has no resumable completed optimization')
    if not result.get('holdout_selection') or not result.get('holdout_candidates'):
        raise ValueError('Run has no frozen holdout shortlist')
    xml = Path(manifest['optimization_path']).resolve()
    if xml.parent != folder.resolve() or opt._hash(xml) != manifest['optimization_sha256']:
        raise ValueError('Saved optimization XML changed')
    from .optimization_results import parse_optimization_xml
    parsed_xml = parse_optimization_xml(xml)
    opt._canonicalize_input_rows(manifest, parsed_xml)
    if parsed_xml != result['optimization']:
        raise ValueError('Saved optimization rows no longer match the original XML')
    expected, _ = _freeze_candidates(manifest, result, result['holdout_selection']['requested_top_n'])
    if expected['candidates_sha256'] != result['holdout_selection']['candidates_sha256']:
        raise ValueError('Frozen shortlist no longer matches in-sample selection')
    if Path(opt._terminal_path()).resolve() != Path(manifest['terminal_path']).resolve() or opt._data_dir().resolve() != Path(manifest['data_dir']).resolve():
        raise ValueError('Configured MT5 installation changed since optimization')
    if any(c['status'] != 'failed' and (not c.get('in_sample') or not c.get('holdout'))
           for c in result['holdout_candidates']):
        with terminal_lock(manifest['terminal_path'], manifest['data_dir']):
            _run_holdout_shortlist(manifest, result, timeout_sec, None, holdout_budget_sec, resume=True)
    outcome = {'run_id': run_id, 'status': manifest['status'],
               'holdout_batch_status': manifest.get('holdout_batch_status'),
               'holdout_summary': result.get('holdout_summary'), 'report_html': None}
    if html_report:
        outcome['report_html'] = opt.render_experiment_report(run_id)['report_html']
    return outcome


def complete_selected_detail(run_id: str, timeout_sec: int = 120) -> dict:
    """Add a selected-pass training report and preserve graphs for a saved run.

    Existing optimization and holdout calculations are reused; only the
    missing in-sample single backtest is launched.
    """
    if not isinstance(timeout_sec, int) or not 30 <= timeout_sec <= 7200:
        raise ValueError("timeout_sec must be 30..7200")
    folder = opt._run_dir(run_id)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_id or manifest.get("status") != "completed":
        raise ValueError("Run is not a completed optimization")
    result_path = folder / "results.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    selected = result.get("selected_pass")
    holdout = result.get("holdout")
    if not isinstance(selected, dict) or not isinstance(holdout, dict) or holdout.get("status") != "completed":
        raise ValueError("Run has no selected pass and completed holdout")
    if holdout.get("selected_pass") != selected.get("Pass"):
        raise ValueError("Saved holdout does not match selected pass")
    data = Path(manifest["data_dir"])
    with terminal_lock(manifest["terminal_path"], str(data)):
        binary, _, _ = opt._ea_paths(manifest["request"]["expert"], data)
        if opt._hash(binary) != manifest["ea_sha256"]:
            raise RuntimeError("EA changed since the saved optimization")
        saved_holdout = Path(holdout["report_path"])
        if (saved_holdout.resolve().parent != folder.resolve()
                or opt._hash(saved_holdout) != holdout["report_sha256"]):
            raise ValueError("Saved holdout report no longer matches the run")
        holdout.update(_copy_report_assets(saved_holdout, data, folder))
        if not result.get("in_sample"):
            try:
                result["in_sample"] = _single_backtest(
                    manifest, selected, "in_sample",
                    manifest["request"]["from_date"],
                    manifest["request"]["to_date"], timeout_sec,
                )
                manifest["selected_detail_status"] = "completed"
                manifest.pop("selected_detail_error", None)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                manifest["selected_detail_status"] = "failed"
                manifest["selected_detail_error"] = str(exc)
        opt._write_json(result_path, result)
        opt._write_json(manifest_path, manifest)
    output = {"run_id": run_id, "selected_detail_status": manifest.get("selected_detail_status"),
              "holdout_graphs_preserved": len(holdout["graph_assets"]),
              "error": manifest.get("selected_detail_error")}
    output["report_html"] = opt.render_experiment_report(run_id)["report_html"]
    return output
