import json
from pathlib import Path

import pytest

from core import optimization_batch as batch


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.opt, "reports_dir", lambda: str(tmp_path))
    monkeypatch.setattr(batch, "_environment", lambda settings: {
        "terminal_path": "test-terminal", "data_dir": "test-data",
        "ea_sha256": "binary-hash", "source_sha256": "source-hash"})
    settings = {"expert": "Examples/MA", "timeframe": "H1", "from_date": "2025.01.01",
                "to_date": "2025.06.01", "parameters": {"Period": {"type": "int", "value": 10}}}
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        run_id = "opt_" + f"{len(calls):032x}"
        folder = batch.opt._run_dir(run_id)
        folder.mkdir(parents=True)
        child = {"run_id": run_id, "request": {k: v for k, v in kwargs.items()
                                                if k not in ("html_report", "holdout_budget_sec")}}
        child["request"]["expert"] = kwargs["expert"].replace("/", "\\")
        child["request"]["timeframe"] = batch.opt._PERIOD_MAP.get(kwargs["timeframe"].lower(), kwargs["timeframe"])
        child["request"]["testing"] = batch.opt.testing_settings(kwargs.get("testing"))
        if "cutoff_date" in kwargs:
            child["request"]["to_date"] = kwargs["cutoff_date"]
            child["holdout_cutoff_date"] = kwargs["cutoff_date"]
            child["holdout_end_date"] = kwargs["to_date"]
        (folder / "manifest.json").write_text(json.dumps(child))
        (folder / "results.json").write_text(json.dumps({"selected_pass": {
            "Pass": len(calls), "Profit": -5 if len(calls) == 2 else 20,
            "Trades": 12, "Period": 10}, "holdout": {"metrics": {"net_profit": 3, "total_trades": 5}}}))
        (folder / "report.html").write_text("local child report")
        return {"run_id": run_id, "status": "completed"}

    monkeypatch.setattr(batch.opt, "optimize_ea", run)
    monkeypatch.setattr(batch.hold, "optimize_ea_holdout", run)
    return settings, calls, tmp_path


def test_all_markets_preserved_and_dark_offline_report(setup):
    settings, calls, tmp_path = setup
    outcome = batch.optimize_ea_symbols(["EURUSD", "GBPUSD"], settings)
    assert [c["symbol"] for c in calls] == ["EURUSD", "GBPUSD"]
    assert outcome["counts"]["completed"] == 2
    page = Path(outcome["report_html"]).read_text()
    assert "-5" in page and "EURUSD" in page and "GBPUSD" in page
    assert "no pooled winner" in page and "background:#0b1120" in page
    assert "http://" not in page and "https://" not in page
    assert "../" in page and "report.html" in page
    batch.continue_symbol_optimization(outcome["batch_id"])
    assert len(calls) == 2


def test_budget_pauses_and_resume_preserves_finished(setup, monkeypatch):
    settings, calls, _ = setup
    clock = [0]
    monkeypatch.setattr(batch.time, "monotonic", lambda: clock[0])
    original = batch.opt.optimize_ea

    def run(**kwargs):
        result = original(**kwargs)
        clock[0] += 40
        return result

    monkeypatch.setattr(batch.opt, "optimize_ea", run)
    result = batch.optimize_ea_symbols(["EURUSD", "GBPUSD"], settings, budget_sec=60, timeout_sec=30)
    assert result["status"] == "paused"
    assert result["counts"]["not_run"] == 1
    first = result["markets"][0]["run_id"]
    resumed = batch.continue_symbol_optimization(result["batch_id"], budget_sec=60)
    assert resumed["status"] == "completed"
    assert resumed["markets"][0]["run_id"] == first
    assert len(calls) == 2


def test_failures_not_retried_or_hidden(setup, monkeypatch):
    settings, _, _ = setup
    calls = []

    def fail(**kwargs):
        calls.append(kwargs["symbol"])
        raise ValueError("<script>failure</script>")

    monkeypatch.setattr(batch.opt, "optimize_ea", fail)
    result = batch.optimize_ea_symbols(["EURUSD", "GBPUSD"], settings)
    assert result["counts"]["failed"] == 2
    assert result["status"] == "completed_with_failures"
    page = Path(result["report_html"]).read_text()
    assert "&lt;script&gt;failure&lt;/script&gt;" in page
    assert "<script>failure" not in page
    batch.continue_symbol_optimization(result["batch_id"])
    assert len(calls) == 2


def test_holdout_allocates_budget_and_resumes_child_not_optimization(setup, monkeypatch):
    settings, calls, _ = setup
    settings.update(cutoff_date="2025.03.01", holdout_timeout_sec=30)
    original = batch.hold.optimize_ea_holdout

    def partial(**kwargs):
        result = original(**kwargs)
        result["status"] = "holdout_partial"
        return result

    resumed = []

    def resume(run_id, **kwargs):
        resumed.append((run_id, kwargs))
        return {"run_id": run_id, "status": "completed"}

    monkeypatch.setattr(batch.hold, "optimize_ea_holdout", partial)
    monkeypatch.setattr(batch.hold, "continue_holdout_tests", resume)
    result = batch.optimize_ea_symbols(["EURUSD"], settings, timeout_sec=30, budget_sec=90)
    assert 30 <= calls[0]["holdout_budget_sec"] <= 60
    finished = batch.continue_symbol_optimization(result["batch_id"], budget_sec=60)
    assert finished["status"] == "completed" and len(calls) == 1 and len(resumed) == 1


def test_request_tampering_and_path_traversal_rejected(setup):
    settings, _, _ = setup
    result = batch.optimize_ea_symbols(["EURUSD"], settings, budget_sec=30, timeout_sec=60)
    path = batch._folder(result["batch_id"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["request"]["symbols"] = ["USDJPY"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="changed"):
        batch.continue_symbol_optimization(result["batch_id"])
    with pytest.raises(ValueError):
        batch.get_symbol_optimization_results("../../other")


def test_child_identity_checked_before_resume(setup, monkeypatch):
    settings, calls, _ = setup
    settings["cutoff_date"] = "2025.03.01"
    settings["holdout_timeout_sec"] = 30
    result = batch.optimize_ea_symbols(["EURUSD"], settings, timeout_sec=30, budget_sec=90)
    manifest_path = batch._folder(result["batch_id"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["markets"][0]["status"] = "holdout_partial"
    manifest_path.write_text(json.dumps(manifest))
    child_path = batch.opt._run_dir(manifest["markets"][0]["run_id"]) / "manifest.json"
    child = json.loads(child_path.read_text())
    child["request"]["symbol"] = "USDJPY"
    child_path.write_text(json.dumps(child))
    resumed = []
    monkeypatch.setattr(batch.hold, "continue_holdout_tests", lambda *a, **kw: resumed.append(a))
    outcome = batch.continue_symbol_optimization(result["batch_id"])
    assert not resumed and outcome["counts"]["failed"] == 1
    assert "changed" in outcome["markets"][0]["error"]


def test_normalized_inputs_testing_and_enum_metadata_resume(setup, monkeypatch):
    settings, _, _ = setup
    settings.update(timeframe="1h", testing={"deposit": 5000}, cutoff_date="2025.03.01", holdout_timeout_sec=30)
    settings["parameters"] = {"Method": {"type": "ENUM_MA_METHOD", "value": "MODE_SMA", "start": 0, "step": 1, "stop": 1}}
    original = batch.hold.optimize_ea_holdout

    def run(**kwargs):
        outcome = original(**kwargs)
        path = batch.opt._run_dir(outcome["run_id"]) / "manifest.json"
        child = json.loads(path.read_text())
        child["request"]["parameters"]["Method"]["enum_values"] = {"MODE_SMA": 0, "MODE_EMA": 1}
        child["request"]["parameters"]["Method"]["value"] = 0
        path.write_text(json.dumps(child))
        return dict(outcome, status="holdout_partial")

    monkeypatch.setattr(batch.hold, "optimize_ea_holdout", run)
    monkeypatch.setattr(batch.hold, "continue_holdout_tests", lambda run_id, **kwargs: {"run_id": run_id, "status": "completed"})
    result = batch.optimize_ea_symbols(["EURUSD"], settings, timeout_sec=30, budget_sec=90)
    assert result["markets"][0]["status"] == "holdout_partial"
    assert result["markets"][0]["child_request"]["testing"]["currency"] == "USD"
    assert result["markets"][0]["child_request"]["parameters"]["Method"]["enum_values"]
    assert "Evidence unavailable" not in Path(result["report_html"]).read_text()
    assert batch.continue_symbol_optimization(result["batch_id"], budget_sec=60)["status"] == "completed"


def test_reload_under_lock_avoids_stale_replay(setup, monkeypatch):
    settings, calls, _ = setup
    outcome = batch.optimize_ea_symbols(["EURUSD"], settings, budget_sec=30, timeout_sec=60)
    folder, stale = batch._load(outcome["batch_id"])
    saved = json.loads((folder / "manifest.json").read_text())
    saved["markets"][0]["status"] = "completed"
    batch.opt._write_json(folder / "manifest.json", saved)
    result = batch._execute(folder, stale, 90, False)
    assert result["status"] == "completed" and not calls


@pytest.mark.parametrize("symbols", [[], ["EURUSD", "EURUSD"], ["../EURUSD"], ["EURUSD\n"], ["A"] * 21])
def test_invalid_symbols(setup, symbols):
    with pytest.raises(ValueError):
        batch.optimize_ea_symbols(symbols, setup[0])


def test_forbidden_remote_settings_and_invalid_budget(setup):
    settings, _, _ = setup
    with pytest.raises(ValueError):
        batch.optimize_ea_symbols(["EURUSD"], dict(settings, use_cloud=True))
    with pytest.raises(ValueError):
        batch.optimize_ea_symbols(["EURUSD"], settings, timeout_sec=True)


def test_interrupted_running_market_not_automatically_relaunched(setup):
    settings, calls, _ = setup
    outcome = batch.optimize_ea_symbols(["EURUSD"], settings, budget_sec=30, timeout_sec=60)
    path = batch._folder(outcome["batch_id"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["markets"][0]["status"] = "running"
    path.write_text(json.dumps(manifest))
    outcome = batch.continue_symbol_optimization(outcome["batch_id"])
    assert outcome["status"] == "needs_review" and not calls


def test_changed_strategy_or_terminal_cannot_mix_markets(setup, monkeypatch):
    settings, calls, _ = setup
    result = batch.optimize_ea_symbols(["EURUSD"], settings, budget_sec=30, timeout_sec=60)
    monkeypatch.setattr(batch, "_environment", lambda settings: {"ea_sha256": "changed"})
    with pytest.raises(ValueError, match="identity changed"):
        batch.continue_symbol_optimization(result["batch_id"], budget_sec=90)
    assert not calls


def test_html_error_preserves_batch_id_and_results(setup, monkeypatch):
    settings, calls, _ = setup
    def fail(batch_id):
        raise OSError("report write failed")
    monkeypatch.setattr(batch, "render_symbol_optimization_report", fail)
    outcome = batch.optimize_ea_symbols(["EURUSD"], settings)
    assert outcome["batch_id"].startswith("batch_")
    assert outcome["counts"]["completed"] == 1 and len(calls) == 1
    assert "report write failed" in outcome["report_error"]
