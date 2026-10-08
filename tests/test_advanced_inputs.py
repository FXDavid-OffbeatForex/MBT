"""Bounded advanced EA input contracts; no MT5 process launches."""
from copy import deepcopy
from pathlib import Path

import pytest

from core import optimization as opt
from core import optimization_holdout as holdout


def source_types(tmp_path, text):
    source = tmp_path / "advanced.mq5"
    source.write_text(text, encoding="utf-8")
    return opt._input_types(source)


def parameters():
    return {
        "Method": {"type": "ENUM_MA_METHOD", "value": 0, "start": 0, "step": 1, "stop": 1},
        "Filter": {"type": "bool", "value": True, "start": False, "step": 1, "stop": True},
    }


def test_builtin_enum_boolean_cartesian_grid_and_saved_schema(tmp_path):
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;\ninput bool Filter=true;")
    params = parameters()
    lines, count = opt._set_lines(params, types, 4)
    assert count == 4
    assert lines == ["Method=0||0||1||1||Y", "Filter=true||false||1||true||Y"]
    assert params["Method"]["enum_values"]["MODE_EMA"] == 1
    rows = [dict(Pass=i, Result=0, Trades=0, Method=method, Filter=flag)
            for i, (method, flag) in enumerate([
                ("MODE_SMA", "false"), (0, "true"), ("MODE_EMA", 0), (1, 1)])]
    manifest = {"request": {"parameters": params, "mode": "complete"}, "planned_combinations": 4}
    opt._validate_observed(manifest, {"columns": list(rows[0]), "rows": rows})
    assert [r["Method"] for r in rows] == [0, 0, 1, 1]
    assert [r["Filter"] for r in rows] == [False, True, False, True]
    assert "Filter=false" in holdout._fixed_set(params, rows[0])
    assert "Method=1||1||1||1||N" in holdout._fixed_set(params, rows[3])


def test_complete_tuple_proof_not_row_count(tmp_path):
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;\ninput bool Filter=true;")
    params = parameters()
    opt._set_lines(params, types, 4)
    manifest = {"request": {"parameters": params, "mode": "complete"}, "planned_combinations": 4}
    rows = [dict(Pass=0, Result=0, Trades=0, Method=0, Filter=False)]
    with pytest.raises(ValueError, match="Incomplete complete-search"):
        opt._validate_observed(manifest, {"columns": list(rows[0]), "rows": rows})


def test_local_enum_sparse_values_require_valid_grid(tmp_path):
    types = source_types(tmp_path, "enum Direction { LONG_ONLY=-1, BOTH=1 };\ninput Direction Side=BOTH;")
    params = {"Side": {"type": "Direction", "value": 1, "start": -1, "step": 2, "stop": 1}}
    assert opt._set_lines(params, types, 4)[1] == 2
    params["Side"]["step"] = 1
    with pytest.raises(ValueError, match="undeclared values"):
        opt._set_lines(params, types, 4)


@pytest.mark.parametrize("text", [
    "enum Direction { A, B };\ninput Direction Side=A;",
    "enum Direction { A=0, B=0 };\ninput Direction Side=A;",
    "enum Direction { A=0, B=A+1 };\ninput Direction Side=A;",
    "input ENUM_TIMEFRAMES Period=PERIOD_H1;",
    "input bool Filter=true;\nvoid OnTesterInit() {}",
])
def test_unsupported_enum_or_custom_ranges_fail_closed(tmp_path, text):
    with pytest.raises(ValueError):
        source_types(tmp_path, text)


@pytest.mark.parametrize("patch", [{"start": 0}, {"step": True}, {"stop": False}, {"values": [False, True]}])
def test_boolean_sweep_rejects_ambiguous_or_custom_range(patch):
    params = {"Filter": deepcopy(parameters()["Filter"])}
    params["Filter"].update(patch)
    with pytest.raises(ValueError):
        opt._set_lines(params, {"Filter": "bool"}, 10)


def test_fixed_string_cannot_silently_accept_sweep():
    with pytest.raises(ValueError, match="cannot be optimized"):
        opt._set_lines({"Label": {"type": "string", "value": "test", "start": "a", "step": 1, "stop": "b"}},
                       {"Label": "string"}, 10)


def test_advanced_report_identity_accepts_enum_identifier_and_boolean(tmp_path):
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;\ninput bool Filter=true;")
    params = parameters()
    opt._set_lines(params, types, 4)
    request = {"parameters": params, "expert": "Example", "symbol": "EURUSD", "timeframe": "H1"}
    report = tmp_path / "report.htm"
    report.write_text("<b>Example</b><b>EURUSD</b><b>H1 (2025.01.01 - 2025.02.01)</b>"
                      "<b>Method=MODE_EMA</b><b>Filter=true</b>", encoding="utf-8")
    selected = {"Method": 1, "Filter": True}
    holdout._verify_period_report_identity(report, {"request": request}, selected, "2025-01-01", "2025-02-01")
    with pytest.raises(ValueError, match="mismatches input"):
        holdout._verify_period_report_identity(report, {"request": request}, {"Method": 0, "Filter": True},
                                              "2025-01-01", "2025-02-01")


def test_bool_budget_and_unknown_enum_values_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="max_combinations"):
        opt._set_lines({"Filter": parameters()["Filter"]}, {"Filter": "bool"}, 1)
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;")
    bad = {"Method": {"type": "ENUM_MA_METHOD", "value": 4, "start": 0, "step": 1, "stop": 1}}
    with pytest.raises(ValueError, match="declared enum"):
        opt._set_lines(bad, types, 10)


@pytest.mark.parametrize("value,expected", [(0.0, False), (1.0, True), ("false", False), ("1", True)])
def test_bool_report_representations(value, expected):
    assert opt._bool_value(value, "Filter") is expected


@pytest.mark.parametrize("value", [2, -1, 0.5, float("nan"), "yes", None])
def test_bool_report_ambiguity_fails_closed(value):
    with pytest.raises(ValueError):
        opt._bool_value(value, "Filter")


def test_forged_enum_schema_is_replaced_with_source_mapping(tmp_path):
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;")
    params = {"Method": {"type": "ENUM_MA_METHOD", "value": 0, "start": 0, "step": 1,
                         "stop": 1, "enum_values": {"MODE_SMA": 900}}}
    opt._set_lines(params, types, 10)
    assert params["Method"]["enum_values"]["MODE_SMA"] == 0


def test_forward_bool_enum_canonicalization_matches_base(tmp_path):
    from core.optimization_results import join_forward_results
    types = source_types(tmp_path, "input ENUM_MA_METHOD Method=MODE_SMA;\ninput bool Filter=true;")
    params = parameters()
    opt._set_lines(params, types, 4)
    manifest = {"request": {"parameters": params}}
    base = {"columns": ["Pass", "Method", "Filter"], "rows": [{"Pass": 2, "Method": 1, "Filter": 1.0}]}
    forward = {"columns": base["columns"], "rows": [{"Pass": 2, "Method": "MODE_EMA", "Filter": "true"}]}
    opt._canonicalize_input_rows(manifest, base)
    opt._canonicalize_input_rows(manifest, forward)
    assert len(join_forward_results(base, forward, ["Method", "Filter"])) == 1
