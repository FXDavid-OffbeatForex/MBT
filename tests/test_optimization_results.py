import xml.etree.ElementTree as ET

import pytest

from core.optimization_results import (
    ForwardJoinError,
    join_forward_results,
    parse_optimization_xml,
)


SS = "urn:schemas-microsoft-com:office:spreadsheet"
ET.register_namespace("ss", SS)


def _xml(headers, data_rows, *, encoding="utf-8", sparse_rows=None):
    root = ET.Element("Workbook")
    worksheet = ET.SubElement(root, "Worksheet", {f"{{{SS}}}Name": "Report"})
    table = ET.SubElement(worksheet, "Table")
    all_rows = [headers, *data_rows]
    for row_index, values in enumerate(all_rows):
        row = ET.SubElement(table, "Row")
        indexes = (sparse_rows or {}).get(row_index, {})
        for column_index, value in enumerate(values, start=1):
            if value is None and column_index not in indexes:
                continue
            attrs = {}
            if column_index in indexes:
                attrs[f"{{{SS}}}Index"] = str(indexes[column_index])
            cell = ET.SubElement(row, "Cell", attrs)
            if value is not None:
                data = ET.SubElement(cell, "Data", {f"{{{SS}}}Type": "String"})
                data.text = str(value)
    return ET.tostring(root, encoding=encoding, xml_declaration=True)


def _write(tmp_path, raw, name="report.xml"):
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def test_parses_mt5_workbook_shape_and_numeric_values(tmp_path):
    headers = [
        "Pass", "Result", "Profit", "Expected Payoff", "Profit Factor",
        "Recovery Factor", "Sharpe Ratio", "Custom Equity DD %", "Trades",
        "MovingPeriod", "MovingShift", "Vendor Extra",
    ]
    data = [
        ["0", "1", "125.50", "2.5", "1.25", "0.8", "0.12", "4.5", "12", "10", "2", "kept"],
        ["1", "0", "-3", "-0.1", "0", "-0.2", "-0.01", "8", "4", "20", "1", "other"],
    ]
    document = b'<?xml version="1.0"?><Workbook><Worksheet><Table>'
    document += b"<Row>" + b"".join(
        f'<Cell><Data>{header}</Data></Cell>'.encode() for header in headers
    ) + b"</Row>"
    for values in data:
        document += b"<Row>" + b"".join(
            f'<Cell><Data>{value}</Data></Cell>'.encode() for value in values
        ) + b"</Row>"
    document += b"</Table></Worksheet></Workbook>"
    parsed = parse_optimization_xml(_write(tmp_path, document))
    assert parsed["columns"] == headers
    assert parsed["row_count"] == 2
    assert parsed["rows"][0]["Pass"] == 0
    assert parsed["rows"][0]["Profit"] == 125.5
    assert parsed["rows"][0]["Trades"] == 12
    assert parsed["rows"][0]["Vendor Extra"] == "kept"


def test_parses_utf16_and_namespaced_sparse_cells(tmp_path):
    raw = _xml(
        ["Pass", "Profit", "MovingPeriod", "MovingShift"],
        [["7", "42.25", "15", None]],
        encoding="utf-16",
        sparse_rows={1: {3: 3}},
    )
    parsed = parse_optimization_xml(_write(tmp_path, raw))
    assert parsed["rows"] == [
        {"Pass": 7, "Profit": 42.25, "MovingPeriod": 15, "MovingShift": None}
    ]


def test_sparse_index_leaves_missing_cells_as_none(tmp_path):
    raw = _xml(
        ["Pass", "Result", "MovingPeriod", "MovingShift"],
        [["3", None, "18", "2"]],
        sparse_rows={1: {3: 3}},
    )
    parsed = parse_optimization_xml(_write(tmp_path, raw))
    assert parsed["rows"][0] == {
        "Pass": 3, "Result": None, "MovingPeriod": 18, "MovingShift": 2
    }


@pytest.mark.parametrize(
    "raw, message",
    [
        (b"<Workbook><Worksheet><Table><Row><Cell><Data>Pass</Data></Cell></Row><Row>", "malformed"),
        (b"<Workbook><Worksheet><Table><Row><Cell><Data>Pass</Data></Cell><Cell><Data>pass</Data></Cell></Row><Row><Cell><Data>1</Data></Cell></Row></Table></Worksheet></Workbook>", "duplicate"),
        (b"<!DOCTYPE Workbook [<!ENTITY x 'expanded'>]><Workbook><Worksheet><Table><Row><Cell><Data>Pass</Data></Cell></Row></Table></Worksheet></Workbook>", "not allowed"),
        (b"<Workbook><Worksheet><Table><Row><Cell><Data>Pass</Data></Cell></Row></Table></Worksheet></Workbook>", "zero data rows"),
    ],
)
def test_rejects_malformed_duplicate_entity_and_header_only_documents(tmp_path, raw, message):
    with pytest.raises(ValueError, match=message):
        parse_optimization_xml(_write(tmp_path, raw))


def test_rejects_non_finite_numeric_cells(tmp_path):
    raw = _xml(["Pass", "Profit"], [["1", "Infinity"]])
    with pytest.raises(ValueError, match="non-finite"):
        parse_optimization_xml(_write(tmp_path, raw))


def test_forward_join_survives_column_reordering_and_subset():
    base = {
        "columns": ["Pass", "Profit", "MovingPeriod", "MovingShift", "Vendor Metric"],
        "rows": [
            {"Pass": 1, "Profit": 12.0, "MovingPeriod": 10, "MovingShift": 2, "Vendor Metric": "x"},
            {"Pass": 2, "Profit": 16.0, "MovingPeriod": 20, "MovingShift": 1, "Vendor Metric": "y"},
        ],
    }
    forward = {
        "columns": ["MovingShift", "Back Result", "Pass", "MovingPeriod", "Forward Result", "Profit"],
        "rows": [
            {"MovingShift": 1, "Back Result": 0.9, "Pass": 2, "MovingPeriod": 20,
             "Forward Result": 1.1, "Profit": 4.0},
        ],
    }
    joined = join_forward_results(base, forward)
    assert len(joined) == 1
    assert joined[0]["pass"] == 2
    assert joined[0]["base"]["Profit"] == 16.0
    assert joined[0]["forward"]["Forward Result"] == 1.1


def test_forward_join_rejects_duplicate_pass_values():
    base = {"columns": ["Pass", "MovingPeriod"], "rows": [
        {"Pass": 1, "MovingPeriod": 10}, {"Pass": 1, "MovingPeriod": 20},
    ]}
    forward = {"columns": ["Pass", "MovingPeriod"], "rows": [{"Pass": 1, "MovingPeriod": 10}]}
    with pytest.raises(ForwardJoinError, match="duplicate base Pass"):
        join_forward_results(base, forward)


def test_forward_join_surfaces_parameter_mismatch_and_unknown_pass():
    base = {"columns": ["Pass", "MovingPeriod"], "rows": [{"Pass": 1, "MovingPeriod": 10}]}
    mismatch = {"columns": ["Pass", "MovingPeriod"], "rows": [{"Pass": 1, "MovingPeriod": 20}]}
    with pytest.raises(ForwardJoinError, match="mismatched parameters"):
        join_forward_results(base, mismatch)
    unknown = {"columns": ["Pass", "MovingPeriod"], "rows": [{"Pass": 2, "MovingPeriod": 10}]}
    with pytest.raises(ForwardJoinError, match="no base row"):
        join_forward_results(base, unknown)
