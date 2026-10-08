from __future__ import annotations

from pathlib import Path
import os
import subprocess
import threading

import pytest

from core import optimization
from core.terminal_lock import terminal_lock


PARAMETERS = {
    "Fast": {"type": "int", "value": 2, "start": 1, "step": 1, "stop": 3},
    "Slow": {"type": "double", "value": 1.5, "start": 1.0, "step": 0.5, "stop": 2.0},
}


def _spreadsheet(rows: str = "", headers=("Pass", "Fast", "Slow", "Result", "Trades")) -> str:
    header = "<Row>" + "".join(f"<Cell><Data>{name}</Data></Cell>" for name in headers) + "</Row>"
    return (
        '<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">'
        f"<Worksheet><Table>{header}{rows}</Table></Worksheet></Workbook>"
    )


def _row(pass_id: int = 1, fast: int = 1, slow: str = "1", result: str = "15", trades: int = 40) -> str:
    values = (pass_id, fast, slow, result, trades)
    return "<Row>" + "".join(f"<Cell><Data>{value}</Data></Cell>" for value in values) + "</Row>"


def _grid_rows():
    return [
        _row(pass_id=pass_id, fast=fast, slow=slow, result=str(100 - pass_id), trades=40)
        for pass_id, (fast, slow) in enumerate(
            (fast, slow) for fast in (1, 2, 3) for slow in ("1", "1.5", "2")
        )
    ]


@pytest.fixture
def local_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"synthetic terminal marker")
    data = tmp_path / "data"
    profile = data / "MQL5" / "Profiles" / "Tester"
    experts = data / "MQL5" / "Experts"
    profile.mkdir(parents=True)
    experts.mkdir(parents=True)
    (experts / "TestEA.ex5").write_bytes(b"synthetic binary")
    (experts / "TestEA.mq5").write_text(
        "input int Fast=2;\ninput double Slow=1.5;\n", encoding="utf-8"
    )
    source = experts / "TestEA.mq5"
    binary = experts / "TestEA.ex5"
    os.utime(binary, (source.stat().st_mtime + 5, source.stat().st_mtime + 5))
    reports = tmp_path / "reports"
    monkeypatch.setattr(optimization, "_terminal_path", lambda: str(terminal))
    monkeypatch.setattr(optimization, "_data_dir", lambda: data)
    monkeypatch.setattr(optimization, "_run_dir", lambda run_id: reports / run_id)
    monkeypatch.setattr(optimization, "_terminal_busy", lambda: False)
    monkeypatch.setattr("core.terminal_lock.reports_dir", lambda: str(reports))
    return terminal, data, profile, reports


def _invoke(
    monkeypatch, local_terminal, *, forward_mode="half", returncode=0, artifact="both",
    xml="valid", rows=None, headers=("Pass", "Fast", "Slow", "Result", "Trades"),
    forward_rows=None, forward_headers=("Pass", "Fast", "Slow", "Result", "Trades"),
    launch_error=None, mode="complete",
):
    terminal, data, _profile, _reports = local_terminal

    def fake_run(cmd, **kwargs):
        ini = Path(cmd[-1])
        settings = ini.read_text(encoding="utf-8")
        run_id = next(line.split("=", 1)[1] for line in settings.splitlines() if line.startswith("Report="))
        if launch_error:
            raise launch_error
        if artifact in ("both", "optimization"):
            content = _spreadsheet("".join(rows if rows is not None else _grid_rows()), headers) if xml == "valid" else "<Workbook><Table><Row>partial"
            (data / f"{run_id}.xml").write_text(content, encoding="utf-8")
        if artifact in ("both", "forward"):
            selected_rows = forward_rows if forward_rows is not None else [*_grid_rows()[::8]]
            (data / f"{run_id}.forward.xml").write_text(_spreadsheet("".join(selected_rows), forward_headers), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(optimization.subprocess, "run", fake_run)
    monkeypatch.setattr(optimization, "_launch_cmd", lambda ini: [ini])
    return optimization._optimize_ea_unlocked(
        "TestEA", "EURUSD", "1h", "2025-01-01", "2025-02-01", PARAMETERS,
        forward_mode=forward_mode, mode=mode, timeout_sec=30,
    )


def test_set_lines_counts_cartesian_ranges_and_serializes_fixed_inputs():
    parameters = {
        "Fast": {"type": "int", "value": 2, "start": 1, "step": 1, "stop": 3},
        "Slow": {"type": "double", "value": 1.5, "start": 1.0, "step": 0.5, "stop": 2.0},
        "Enabled": {"type": "bool", "value": True},
        "Label": {"type": "string", "value": "alpha"},
    }
    lines, count = optimization._set_lines(
        parameters, {"Fast": "int", "Slow": "double", "Enabled": "bool", "Label": "string"}, 10
    )
    assert count == 9
    assert lines == [
        "Fast=2||1||1||3||Y", "Slow=1.5||1.0||0.5||2.0||Y", "Enabled=true", "Label=alpha"
    ]


@pytest.mark.parametrize(
    "parameters,types,maximum,message",
    [
        ({"A": {"type": "int", "value": 1, "start": 1, "step": 1, "stop": 2}}, {"A": "int", "B": "int"}, 10, "exactly match"),
        ({"A": {"type": "int", "value": 1, "start": 1, "step": 1, "stop": 3}, "B": {"type": "int", "value": 1, "start": 1, "step": 1, "stop": 3}}, {"A": "int", "B": "int"}, 4, "exceeds"),
        ({"A": {"type": "double", "value": float("nan"), "start": 1, "step": 1, "stop": 2}}, {"A": "double"}, 10, "finite"),
        ({"A": {"type": "int", "value": 1.2, "start": 1, "step": 1, "stop": 2}}, {"A": "int"}, 10, "integer"),
        ({"A": {"type": "int", "value": 1, "start": 1, "step": 0, "stop": 2}}, {"A": "int"}, 10, "invalid range"),
        ({"A": {"type": "string", "value": "bad|value", "start": 1, "step": 1, "stop": 2}}, {"A": "string"}, 10, "unsupported SET syntax"),
    ],
)
def test_set_lines_rejects_invalid_or_oversized_ranges(parameters, types, maximum, message):
    with pytest.raises(ValueError, match=message):
        optimization._set_lines(parameters, types, maximum)


def test_set_lines_rejects_numeric_overflow():
    parameters = {"Value": {"type": "double", "value": "1e999", "start": 1, "step": 1, "stop": 2}}
    with pytest.raises(ValueError, match="exceeds MQL double bounds"):
        optimization._set_lines(parameters, {"Value": "double"}, 10)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"from_date": "2025-02-30"}, "YYYY-MM-DD"),
        ({"from_date": "2025-02-01", "to_date": "2025-01-01"}, "must precede"),
        ({"forward_mode": "custom", "forward_date": "2025-01-01"}, "within the test period"),
        ({"symbol": "EURUSD\\nReport=evil"}, "Invalid symbol"),
        ({"timeframe": "13m"}, "Unsupported timeframe"),
    ],
)
def test_runner_rejects_invalid_request_before_resolving_local_data(monkeypatch, kwargs, message):
    monkeypatch.setattr(optimization, "_data_dir", lambda: pytest.fail("data lookup should not occur"))
    args = {"from_date": "2025-01-01", "to_date": "2025-02-01", "symbol": "EURUSD", "timeframe": "1h"}
    args.update(kwargs)
    with pytest.raises(ValueError, match=message):
        optimization._optimize_ea_unlocked("TestEA", args["symbol"], args["timeframe"], args["from_date"], args["to_date"], PARAMETERS, forward_mode=args.get("forward_mode", "half"), forward_date=args.get("forward_date"))


def test_input_source_validation_rejects_duplicate_and_unsupported_declarations(tmp_path: Path):
    source = tmp_path / "bad.mq5"
    source.write_text("input int Fast=1;\ninput int Fast=2;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate input"):
        optimization._input_types(source)
    source.write_text("input color Tint=clrRed;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot safely parse|unsupported input types"):
        optimization._input_types(source)
    source.write_text("input int A=1; input int B=2;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot safely parse"):
        optimization._input_types(source)
    source.write_text("#define PARAM input int\ninput int A=1;\nPARAM B=2;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported preprocessor"):
        optimization._input_types(source)


def test_numeric_underflow_and_unrepresentable_step_rejected():
    with pytest.raises(ValueError, match="underflows"):
        optimization._set_lines(
            {"A": {"type": "double", "value": "1e-1000", "start": "1e-1000", "step": "1e-1000", "stop": "2e-1000"}},
            {"A": "double"}, 10,
        )
    with pytest.raises(ValueError, match="cannot advance"):
        optimization._set_lines(
            {"A": {"type": "double", "value": 1.0, "start": 1.0, "step": "1e-20", "stop": "1.00000000000000000002"}},
            {"A": "double"}, 10,
        )


@pytest.mark.parametrize(
    "source_text",
    [
        '#include "custom.mqh"\ninput int Fast=1;\n',
        "input int Fast=1;\nvoid OnTesterInit() {}\n",
    ],
)
def test_input_source_validation_rejects_unreviewed_preprocessor_or_range_directives(tmp_path: Path, source_text: str):
    source = tmp_path / "directive.mq5"
    source.write_text(source_text, encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported preprocessor directives|unsupported tester range"):
        optimization._input_types(source)


def test_input_source_validation_accepts_only_reviewed_directive_forms(tmp_path: Path):
    source = tmp_path / "allowed.mq5"
    source.write_text(
        '#property strict\n#define FAST_MAX 10\n#include <Trade\\Trade.mqh>\ninput int Fast=1;\n',
        encoding="utf-8",
    )
    assert optimization._input_types(source) == {"Fast": "int"}


def test_data_dir_requires_explicit_existing_profile_and_never_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"marker")
    invalid_data = tmp_path / "incomplete-data"
    invalid_data.mkdir()
    monkeypatch.setattr(optimization, "_terminal_path", lambda: str(terminal))
    monkeypatch.setattr(optimization, "load_config", lambda: {"tester": {"data_dir": str(invalid_data), "portable": False}})
    with pytest.raises(ValueError, match="no tester profile folder"):
        optimization._data_dir()


def test_ea_paths_rejects_traversal_and_requires_source_and_binary(local_terminal):
    _terminal, data, _profile, _reports = local_terminal
    with pytest.raises(ValueError, match="relative to MQL5/Experts"):
        optimization._ea_paths("..\\outside", data)
    (data / "MQL5" / "Experts" / "TestEA.mq5").unlink()
    with pytest.raises(ValueError, match="compiled EA and adjacent"):
        optimization._ea_paths("TestEA", data)


def test_busy_terminal_fails_before_creating_run(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "_terminal_busy", lambda: True)
    with pytest.raises(RuntimeError, match="already running"):
        optimization._optimize_ea_unlocked(
            "TestEA", "EURUSD", "1h", "2025-01-01", "2025-02-01", PARAMETERS, timeout_sec=30
        )
    assert not list(local_terminal[3].glob("opt_*"))


def test_runner_success_captures_set_ini_and_normalized_rows(monkeypatch, local_terminal):
    result = _invoke(monkeypatch, local_terminal)
    assert result["status"] == "completed"
    assert result["planned_combinations"] == 9
    assert result["parsed_rows"] == 9
    run_dir = Path(result["run_dir"])
    manifest = __import__("json").loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    set_text = (run_dir / f'{result["run_id"]}.set').read_text(encoding="utf-8")
    ini_text = (run_dir / f'{result["run_id"]}.ini').read_text(encoding="utf-8")
    assert "Fast=2||1||1||3||Y" in set_text
    assert "UseLocal=1" in ini_text and "UseRemote=0" in ini_text and "UseCloud=0" in ini_text
    assert manifest["status"] == "completed"


def test_genetic_request_records_effective_complete_fallback(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "tester_log_diagnostics", lambda _snapshot: {
        "log_available": True, "genetic_disabled_small_grid": True,
        "effective_mode": "complete", "mt5_reported_passes": 9,
        "statistics_done": True,
    })
    result = _invoke(monkeypatch, local_terminal, mode="genetic", forward_mode="off")
    assert result["status"] == "completed"
    assert "complete search" in result["warning"]
    stored = optimization.get_optimization_results(result["run_id"])
    assert stored["tester_diagnostics"]["effective_mode"] == "complete"
    assert stored["counts"]["mt5_reported_passes"] == 9


def test_genetic_request_without_tester_evidence_remains_unverified(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "tester_log_diagnostics", lambda _snapshot: {"log_available": False})
    result = _invoke(monkeypatch, local_terminal, mode="genetic", forward_mode="off")
    assert result["status"] == "unverified_completion"


def test_genetic_request_with_finished_local_log_is_completed(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "tester_log_diagnostics", lambda _snapshot: {
        "log_available": True, "effective_mode": "genetic", "genetic_finished": True,
        "genetic_search_space": 9, "statistics_done": True,
        "local_tasks": 5, "remote_tasks": 0, "cloud_tasks": 0,
    })
    result = _invoke(monkeypatch, local_terminal, mode="genetic", forward_mode="off")
    assert result["status"] == "completed"


@pytest.mark.parametrize("status,auto_render", [
    ("completed", True), ("no_eligible_pass", True),
    ("invalid_results", False), ("unverified_completion", False),
])
def test_public_optimizer_renders_only_verified_results_by_default(monkeypatch, local_terminal, status, auto_render):
    calls = []
    monkeypatch.setattr(optimization, "_optimize_ea_unlocked", lambda *args: {
        "run_id": "opt_" + "a" * 32, "status": status,
    })
    monkeypatch.setattr(optimization, "render_experiment_report", lambda run_id: (
        calls.append(run_id) or {"report_html": "C:/safe/report.html"}
    ))
    result = optimization.optimize_ea("TestEA", "EURUSD", "1h", "2025-01-01", "2025-02-01", PARAMETERS)
    assert result["report_html"] == ("C:/safe/report.html" if auto_render else None)
    assert len(calls) == int(auto_render)


def test_public_optimizer_can_skip_html_and_retains_run_on_render_failure(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "_optimize_ea_unlocked", lambda *args: {
        "run_id": "opt_" + "b" * 32, "status": "completed",
    })
    calls = []
    def fail_render(run_id):
        calls.append(run_id)
        raise OSError("disk full")
    monkeypatch.setattr(optimization, "render_experiment_report", fail_render)
    skipped = optimization.optimize_ea("TestEA", "EURUSD", "1h", "2025-01-01", "2025-02-01", PARAMETERS, html_report=False)
    assert skipped["report_html"] is None and not calls
    failed = optimization.optimize_ea("TestEA", "EURUSD", "1h", "2025-01-01", "2025-02-01", PARAMETERS)
    assert failed["status"] == "completed" and failed["report_html"] is None
    assert failed["report_error"] == "disk full" and len(calls) == 1


def test_runner_distinguishes_nonzero_exit_and_preserves_any_outputs(monkeypatch, local_terminal):
    result = _invoke(monkeypatch, local_terminal, returncode=9)
    assert result["status"] == "launch_failed"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["exit_code"] == 9
    assert Path(manifest["optimization_path"]).is_file()


@pytest.mark.parametrize(
    "artifact,xml,forward_mode,expected_error",
    [
        ("forward", "valid", "half", "optimization report"),
        ("optimization", "valid", "half", "forward report"),
        ("optimization", "partial", "off", "malformed optimization XML"),
    ],
)
def test_runner_marks_missing_or_partial_xml_invalid(monkeypatch, local_terminal, artifact, xml, forward_mode, expected_error):
    result = _invoke(monkeypatch, local_terminal, artifact=artifact, xml=xml, forward_mode=forward_mode)
    assert result["status"] == "invalid_results"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert expected_error in manifest["error"]


def test_manual_forward_export_recovers_only_matching_mt5_rows(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "tester_log_diagnostics", lambda _snapshot: {
        "forward_cached_records": 2, "effective_mode": "complete",
    })
    outcome = _invoke(monkeypatch, local_terminal, artifact="optimization")
    assert outcome["status"] == "invalid_results"
    folder = Path(outcome["run_dir"])
    run_id = outcome["run_id"]
    with pytest.raises(ValueError, match="Export MT5 Forward Results"):
        optimization.recover_forward_report(run_id, html_report=False)
    forward = folder / f"{run_id}.forward.xml"
    forward.write_text(_spreadsheet("".join(_grid_rows()[::8])), encoding="utf-8")
    recovered = optimization.recover_forward_report(run_id, html_report=False)
    assert recovered["status"] == "completed"
    assert recovered["forward_rows"] == 2
    assert recovered["report_html"] is None
    manifest = __import__("json").loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["forward_export_source"] == "manual_mt5_forward_results"
    assert optimization.get_optimization_results(run_id)["counts"]["forward_rows"] == 2
    with pytest.raises(ValueError, match="not awaiting"):
        optimization.recover_forward_report(run_id, html_report=False)


def test_manual_forward_export_rejects_mismatched_pass_and_count(monkeypatch, local_terminal):
    monkeypatch.setattr(optimization, "tester_log_diagnostics", lambda _snapshot: {
        "forward_cached_records": 2,
    })
    outcome = _invoke(monkeypatch, local_terminal, artifact="optimization")
    folder = Path(outcome["run_dir"])
    forward = folder / f'{outcome["run_id"]}.forward.xml'
    forward.write_text(_spreadsheet(_row(pass_id=99)), encoding="utf-8")
    with pytest.raises(ValueError, match="no base row"):
        optimization.recover_forward_report(outcome["run_id"], html_report=False)
    forward.write_text(_spreadsheet(_grid_rows()[0]), encoding="utf-8")
    with pytest.raises(ValueError, match="row count differs"):
        optimization.recover_forward_report(outcome["run_id"], html_report=False)
    assert not (folder / "results.json").exists()


def test_runner_rejects_incomplete_complete_grid(monkeypatch, local_terminal):
    result = _invoke(monkeypatch, local_terminal, rows=_grid_rows()[:-1])
    assert result["status"] == "incomplete_results"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert "8 of 9 requested tuples" in manifest["error"]


@pytest.mark.parametrize(
    "headers,rows,message",
    [
        (("Fast", "Slow", "Result", "Trades"), ["<Row>" + "".join(f"<Cell><Data>{v}</Data></Cell>" for v in (1, "1", 15, 40)) + "</Row>"], "lacks Pass"),
        (("Pass", "Slow", "Result", "Trades"), ["<Row>" + "".join(f"<Cell><Data>{v}</Data></Cell>" for v in (0, "1", 15, 40)) + "</Row>"], "requested parameter columns"),
    ],
)
def test_runner_rejects_missing_pass_or_requested_parameter_column(monkeypatch, local_terminal, headers, rows, message):
    result = _invoke(monkeypatch, local_terminal, rows=rows, headers=headers)
    assert result["status"] == "invalid_results"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert message in manifest["error"]


@pytest.mark.parametrize(
    "rows,message",
    [
        ([_row(pass_id=0, fast=4), *_grid_rows()[1:]], "outside the requested range"),
        ([_grid_rows()[0], _row(pass_id=1, fast=1, slow="1"), *_grid_rows()[2:]], "duplicate parameter tuples"),
    ],
)
def test_runner_rejects_out_of_range_or_duplicate_observed_tuple(monkeypatch, local_terminal, rows, message):
    result = _invoke(monkeypatch, local_terminal, rows=rows)
    assert result["status"] == "invalid_results"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert message in manifest["error"]


def test_runner_records_oserror_as_launch_failure(monkeypatch, local_terminal):
    result = _invoke(monkeypatch, local_terminal, launch_error=OSError("synthetic launch failure"))
    assert result["status"] == "launch_failed"
    manifest = __import__("json").loads((Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["error"] == "Could not launch the configured MT5 terminal: OSError"


def test_terminal_lock_excludes_another_mbt_launch_and_releases_after_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr("core.terminal_lock.reports_dir", lambda: str(reports))
    terminal, data = str(tmp_path / "terminal64.exe"), str(tmp_path / "data")
    entered = threading.Event()
    result = []
    with terminal_lock(terminal, data):
        def contend():
            try:
                with terminal_lock(terminal, data):
                    entered.set()
            except RuntimeError as exc:
                result.append(str(exc))

        worker = threading.Thread(target=contend)
        worker.start()
        worker.join(timeout=5)
        assert not entered.is_set()
        assert result and "Another MBT operation" in result[0]
    with terminal_lock(terminal, data):
        entered.set()
    assert entered.is_set()
