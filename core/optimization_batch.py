"""Sequential multi-market experiments; never select a pooled/OOS winner."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
import uuid

from . import optimization as opt
from . import optimization_holdout as hold
from .report_hover import HOVER_SCRIPT

_ID = re.compile(r"batch_[0-9a-f]{32}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9_.-]{1,40}\Z")
_SETTINGS = {"expert", "timeframe", "from_date", "to_date", "parameters", "mode",
             "criterion", "model", "max_combinations", "min_trades", "forward_mode",
             "forward_date", "cutoff_date", "holdout_top_n", "holdout_timeout_sec", "testing"}


def _folder(batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not _ID.fullmatch(batch_id):
        raise ValueError("Invalid batch_id")
    root = (Path(opt.reports_dir()) / "optimization_batches").resolve()
    path = (root / batch_id).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Invalid batch path")
    return path


def _digest(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _limits(budget_sec: int, timeout_sec: int) -> None:
    if type(budget_sec) is not int or not 30 <= budget_sec <= 7200:
        raise ValueError("budget_sec must be an integer between 30 and 7200")
    if type(timeout_sec) is not int or not 30 <= timeout_sec <= 7200:
        raise ValueError("timeout_sec must be an integer between 30 and 7200")


@contextmanager
def _lock(folder: Path):
    path = folder / "batch.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("Batch already running, or interrupted lock requires operator review") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def _load(batch_id: str) -> tuple[Path, dict]:
    folder = _folder(batch_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    request = manifest["request"]
    if manifest.get("batch_id") != batch_id or manifest.get("request_sha256") != _digest(request):
        raise ValueError("Batch request changed; cannot continue")
    if [item.get("symbol") for item in manifest["markets"]] != request["symbols"]:
        raise ValueError("Batch symbol order changed")
    return folder, manifest


def _summary(manifest: dict) -> None:
    counts = {name: sum(item["status"] == name for item in manifest["markets"])
              for name in ("completed", "no_eligible_pass", "not_run", "holdout_partial", "running")}
    counts["failed"] = sum(item["status"] not in counts for item in manifest["markets"])
    counts["planned_markets"] = len(manifest["markets"])
    manifest["counts"] = counts
    manifest["status"] = ("paused" if counts["not_run"] or counts["holdout_partial"]
                          else "needs_review" if counts["running"]
                          else "completed_with_failures" if counts["failed"] else "completed")


def _validate_child(market: dict, settings: dict) -> Path:
    folder = opt._run_dir(market["run_id"])
    child = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    request = child["request"]
    if (market.get("child_request_sha256") != _digest(request)
            or market.get("child_request") != request):
        raise ValueError("Child normalized request changed")
    if child.get("run_id") != market["run_id"] or request.get("symbol") != market["symbol"]:
        raise ValueError("Child run does not match batch market")
    return folder


def _record_child(market: dict, settings: dict) -> None:
    folder = opt._run_dir(market["run_id"])
    child = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    request = child["request"]
    expected = {k: v for k, v in settings.items()
                if k not in ('cutoff_date', 'holdout_top_n', 'holdout_timeout_sec')}
    expected["symbol"] = market["symbol"]
    expert = str(settings["expert"]).replace("/", "\\")
    if expert.lower().endswith(".ex5"):
        expert = expert[:-4]
    expected["expert"] = expert
    expected["timeframe"] = opt._PERIOD_MAP.get(str(settings["timeframe"]).lower(), settings["timeframe"])
    if "testing" in expected:
        expected["testing"] = opt.testing_settings(expected["testing"])
    # Source-validated enum metadata is added by the runner. Preserve and hash
    # that normalized request rather than comparing it to the raw AI inputs.
    raw_parameters = expected.pop("parameters", {})
    observed_parameters = request.get("parameters", {})
    def parameter_matches(name, spec):
        if not isinstance(spec, dict):
            return False
        actual = observed_parameters[name]
        for key, value in spec.items():
            if key == "enum_values":
                continue  # Runner replaces caller metadata with source authority.
            if key == "value" and isinstance(value, str) and "enum_values" in actual:
                value = actual["enum_values"].get(value, value)
            if actual.get(key) != value:
                return False
        return True
    if set(raw_parameters) != set(observed_parameters) or any(
            not parameter_matches(name, spec) for name, spec in raw_parameters.items()):
        raise ValueError("Child inputs do not match batch")
    if "cutoff_date" in settings:
        expected["to_date"] = settings["cutoff_date"]
        if (child.get("holdout_cutoff_date") != settings["cutoff_date"]
                or child.get("holdout_end_date") != settings["to_date"]):
            raise ValueError("Child holdout dates do not match batch")
    if child.get("run_id") != market["run_id"] or any(request.get(k) != v for k, v in expected.items()):
        raise ValueError("Child run does not match batch settings or market")
    market["child_request"] = request
    market["child_request_sha256"] = _digest(request)


def _environment(settings: dict) -> dict:
    data = opt._data_dir()
    binary, source, _ = opt._ea_paths(settings["expert"], data)
    return {"terminal_path": str(Path(opt._terminal_path()).resolve()),
            "data_dir": str(Path(data).resolve()), "ea_sha256": opt._hash(binary),
            "source_sha256": opt._hash(source),
            "input_schema_sha256": opt.parse_source_schema(source, data / 'MQL5')['schema_sha256']}


def _execute(folder: Path, manifest: dict, budget_sec: int, html_report: bool) -> dict:
    started = time.monotonic()
    request = manifest["request"]
    timeout = request["timeout_sec"]
    settings = dict(request["settings"])
    is_holdout = "cutoff_date" in settings
    per_detail = min(timeout, settings.get("holdout_timeout_sec", 120))
    with _lock(folder):
        # Another caller may have completed work after our initial read but
        # before we obtained the lock. Never execute from that stale snapshot.
        manifest = _load(manifest["batch_id"])[1]
        # Capture source/binary and terminal identity before the first launch.
        # A pause cannot mix different code or terminals into one comparison.
        if "environment" not in manifest:
            manifest["environment"] = _environment(settings)
            opt._write_json(folder / "manifest.json", manifest)
        for market in manifest["markets"]:
            if market["status"] not in ("not_run", "holdout_partial"):
                continue
            remaining = math.floor(budget_sec - (time.monotonic() - started))
            continuing = market["status"] == "holdout_partial"
            required = per_detail if continuing else timeout + (per_detail if is_holdout else 0)
            if remaining < required:
                break
            if _environment(settings) != manifest["environment"]:
                raise ValueError("Batch EA/source or terminal identity changed; create a new batch")
            market["status"] = "running"
            opt._write_json(folder / "manifest.json", manifest)
            try:
                if continuing:
                    _validate_child(market, settings)
                    outcome = hold.continue_holdout_tests(market["run_id"], timeout_sec=per_detail,
                                                          holdout_budget_sec=min(7200, remaining),
                                                          html_report=html_report)
                else:
                    arguments = dict(settings, symbol=market["symbol"], timeout_sec=timeout,
                                     html_report=html_report)
                    if is_holdout:
                        arguments["holdout_budget_sec"] = min(7200, remaining - timeout)
                        outcome = hold.optimize_ea_holdout(**arguments)
                    else:
                        outcome = opt.optimize_ea(**arguments)
                # Store the child's actual outcome, including failures and incomplete markets.
                market.update(outcome)
                if market.get("run_id") and not continuing:
                    _record_child(market, settings)
                if _environment(settings) != manifest["environment"]:
                    raise ValueError("Batch EA/source or terminal identity changed during child run")
            except Exception as exc:
                market["status"] = "failed"
                market["error"] = f"{type(exc).__name__}: {exc}"
            market["finished_at"] = datetime.now(timezone.utc).isoformat()
            _summary(manifest)
            opt._write_json(folder / "manifest.json", manifest)
        manifest["last_batch_elapsed_sec"] = round(time.monotonic() - started, 3)
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        _summary(manifest)
        opt._write_json(folder / "manifest.json", manifest)
        if html_report:
            try:
                render_symbol_optimization_report(manifest["batch_id"])
                manifest.pop("report_error", None)
            except Exception as exc:
                manifest["report_error"] = f"{type(exc).__name__}: {exc}"
            opt._write_json(folder / "manifest.json", manifest)
    return get_symbol_optimization_results(manifest["batch_id"])


def optimize_ea_symbols(symbols: list[str], settings: dict, budget_sec: int = 600,
                        timeout_sec: int = 120, html_report: bool = True) -> dict:
    """Run the same specification on <=20 markets, sequentially and resumably.

    cutoff_date selects independent holdout mode. Each market retains its own
    in-sample winner. A budget is a scheduling bound, not a CPU/RAM ceiling;
    terminal cleanup/report overhead may extend wall time beyond that budget.
    """
    _limits(budget_sec, timeout_sec)
    if type(html_report) is not bool:
        raise ValueError("html_report must be a boolean")
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 20
            or any(not isinstance(s, str) or not _SYMBOL.fullmatch(s) for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("Provide 1 to 20 distinct valid broker symbol names")
    if not isinstance(settings, dict) or set(settings) - _SETTINGS:
        raise ValueError("Unsupported settings; symbol/timeout/budget belong to the batch")
    if not {"expert", "timeframe", "from_date", "to_date", "parameters"} <= set(settings):
        raise ValueError("Missing required optimization settings")
    if "cutoff_date" in settings and ({"forward_mode", "forward_date"} & set(settings)):
        raise ValueError("Independent holdout cannot also request native forward settings")
    if "cutoff_date" not in settings and ({"holdout_top_n", "holdout_timeout_sec"} & set(settings)):
        raise ValueError("Holdout settings require cutoff_date")
    if "holdout_timeout_sec" in settings:
        _limits(budget_sec, settings["holdout_timeout_sec"])
    # Round-trip captures JSON primitives and rejects NaN, unserializable objects.
    request = json.loads(json.dumps({"symbols": symbols, "settings": settings,
                                    "timeout_sec": timeout_sec}, allow_nan=False))
    batch_id = "batch_" + uuid.uuid4().hex
    folder = _folder(batch_id)
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"batch_id": batch_id, "request": request,
                "request_sha256": _digest(request), "status": "created",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "markets": [{"symbol": s, "status": "not_run"} for s in symbols]}
    opt._write_json(folder / "manifest.json", manifest)
    return _execute(folder, manifest, budget_sec, html_report)


def continue_symbol_optimization(batch_id: str, budget_sec: int = 600,
                                 html_report: bool = True) -> dict:
    """Resume only unrun markets or partial holdouts; never rerun failures."""
    folder, manifest = _load(batch_id)
    _limits(budget_sec, manifest["request"]["timeout_sec"])
    if type(html_report) is not bool:
        raise ValueError("html_report must be a boolean")
    return _execute(folder, manifest, budget_sec, html_report)


def get_symbol_optimization_results(batch_id: str) -> dict:
    folder, manifest = _load(batch_id)
    report = folder / "report.html"
    return {**manifest, "report_html": str(report) if report.is_file() else None}


def _child_result(market: dict, settings: dict) -> tuple[dict, bool]:
    run_id = market.get("run_id")
    if not run_id:
        return {}, False
    folder = _validate_child(market, settings)
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    report = folder / "report.html"
    return result, report.is_file()


def render_symbol_optimization_report(batch_id: str) -> dict:
    """Offline per-market evidence comparison, not portfolio or pooled equity."""
    folder, manifest = _load(batch_id)
    rows = []
    profits = []
    for market in manifest["markets"]:
        evidence_error = ""
        try:
            result, link = _child_result(market, manifest["request"]["settings"])
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
            result, link = {}, None
            evidence_error = f" Evidence unavailable: {type(exc).__name__}."
        selected = result.get("selected_pass") or {}
        out = result.get("holdout") or {}
        metrics = out.get("metrics") or {}
        profits.append((market["symbol"], selected.get("Profit"), metrics.get("net_profit")))
        label = escape(market["symbol"])
        if link:
            # Only MBT run-id-derived paths become links, never arbitrary outcome paths.
            relative = os.path.relpath(Path(opt._run_dir(market["run_id"])) / "report.html", folder)
            label = f'<a href="{escape(quote(relative.replace(os.sep, "/"), safe="/.-_"), quote=True)}">{label}</a>'
        parameter_cells = []
        for name, spec in manifest["request"]["settings"]["parameters"].items():
            value = selected.get(name, spec.get("value", "—"))
            parameter_cells.append(f"<tr><td>{escape(name)}</td><td>{escape(str(value))}</td></tr>")
        inputs_html = "<table class=inputs>" + "".join(parameter_cells) + "</table>"
        values = [label, escape(market["status"]), escape(str(selected.get("Pass", "—"))),
                  escape(str(selected.get("Profit", "—"))),
                  escape(str(selected.get("Trades", "—"))),
                  escape(str(metrics.get("net_profit", "—"))),
                  escape(str(metrics.get("total_trades", "—"))),
                  inputs_html,
                  escape(str(market.get("error") or market.get("holdout_error") or "") + evidence_error)]
        rows.append("<tr>" + "".join(f"<td>{v}</td>" for v in values) + "</tr>")
    headers = ["Market / report", "Status", "IS winner pass", "IS profit", "IS trades",
               "Winner OOS profit", "Winner OOS trades", "Winner inputs", "Diagnostics"]
    finite = lambda v: type(v) in (int, float) and math.isfinite(v)
    scale = max([abs(v) for _, a, b in profits for v in (a, b) if finite(v)] or [1]) or 1
    svg = [f'<svg viewBox="0 0 900 {max(80, len(profits) * 80 + 40)}" role="img" aria-label="Per-market selected candidate in-sample and out-of-sample net profits"><title>Separate market winners; not pooled performance</title>']
    svg.append('<text x="160" y="20" fill="#76b9ff">In sample</text><text x="280" y="20" fill="#64d8ae">Out of sample</text>')
    for index, (symbol, in_profit, out_profit) in enumerate(profits):
        y = 60 + index * 80
        svg.append(f'<text x="5" y="{y}" fill="#e4ecf6">{escape(symbol)}</text>')
        for offset, value, color, period in ((0, in_profit, "#76b9ff", "IS"), (25, out_profit, "#64d8ae", "OOS")):
            if finite(value):
                width = abs(value) / scale * 250
                x = 450 if value >= 0 else 450 - width
                svg.append(f'<rect x="{x:.2f}" y="{y + offset - 14}" width="{width:.2f}" height="16" fill="{color}"><title>{escape(symbol)} {period}: {value:g}</title></rect>')
                svg.append(f'<text x="730" y="{y + offset}" fill="{color}">{value:g}</text>')
            else:
                svg.append(f'<text x="730" y="{y + offset}" fill="#9aacc5">{period}: unavailable</text>')
    svg.append(f'<line x1="450" y1="35" x2="450" y2="{len(profits) * 80 + 20}" stroke="#9aacc5"/></svg>')
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MBT multi-market experiment</title><style>body{margin:0;background:#0b1120;color:#e4ecf6;font:15px system-ui;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:28px}p{color:#aabbd0;line-height:1.6}a{color:#76b9ff}section{background:#111d30;border:1px solid #293b54;border-radius:12px;padding:20px;margin:20px 0;overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:14px;border-bottom:1px solid #293b54;vertical-align:top}th{color:#9aacc5}td{max-width:380px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}</style><main>
<h1>Multi-market experiment</h1><p>Sequential local MT5 runs. Every requested market remains visible, including failed or unrun markets. Winners are selected separately on each market’s in-sample results; no pooled winner or portfolio performance is claimed. Viewing out-of-sample results to choose a market is another selection step and requires a fresh final holdout.</p>'''
    html += "<p>" + escape(batch_id) + " · " + escape(manifest["status"]) + "</p><section><h2>Independent market results</h2>" + "".join(svg)
    html += "</section><section><table><thead><tr>"
    html += "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>"
    html += "".join(rows) + "</tbody></table></section><section><h2>Frozen experiment settings</h2><pre>"
    html += escape(json.dumps(manifest["request"], indent=2, ensure_ascii=False))
    html += "</pre><p>Budget schedules sequential tests; it does not impose a CPU or RAM ceiling. No remote or cloud agents are enabled by this workflow. Open individual reports for candidate comparisons and available measured charts.</p></section></main></html>"
    report = folder / "report.html"
    html = html.replace('</html>', HOVER_SCRIPT + '</html>')
    report.write_text(html, encoding="utf-8")
    return {"batch_id": batch_id, "report_html": str(report)}
