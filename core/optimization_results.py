"""Safe parsing and matching helpers for native MT5 optimization XML."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence


_MAX_XML_BYTES = 32 * 1024 * 1024
_MAX_COLUMNS = 4096
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_NONFINITE = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
_METRIC_WORDS = (
    "result", "profit", "payoff", "factor", "recovery", "sharpe", "equity",
    "drawdown", "draw down", "dd", "trades", "expected", "balance", "margin",
    "deals", "wins", "losses", "criterion", "custom",
)


class ForwardJoinError(ValueError):
    """Raised when optimization and forward rows cannot be joined unambiguously."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safety_decode(raw: bytes) -> str:
    """Decode supported XML encodings so DTD declarations can be rejected first."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    # XML without a BOM can still be UTF-16 when its declaration uses that encoding.
    if len(raw) >= 4 and raw[0] == 0x3C and raw[1] == 0:
        return raw.decode("utf-16-le")
    if len(raw) >= 4 and raw[0] == 0 and raw[1] == 0x3C:
        return raw.decode("utf-16-be")
    return raw.decode("utf-8")


def _cell_value(cell: ET.Element) -> str | None:
    data = next((child for child in cell if _local_name(child.tag) == "Data"), None)
    if data is None:
        return None
    value = "".join(data.itertext()).strip()
    return value or None


def _convert_value(value: str | None) -> Any:
    if value is None:
        return None
    lowered = value.casefold()
    if lowered in _NONFINITE:
        raise ValueError(f"non-finite numeric value in optimization XML: {value!r}")
    if not _NUMBER.fullmatch(value):
        return value
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite numeric value in optimization XML: {value!r}")
    return number


def _row_cells(row: ET.Element) -> list[str | None]:
    values: list[str | None] = []
    next_column = 1
    for cell in (child for child in row if _local_name(child.tag) == "Cell"):
        explicit_index = next(
            (value for key, value in cell.attrib.items() if _local_name(key).casefold() == "index"),
            None,
        )
        if explicit_index is not None:
            try:
                column = int(explicit_index)
            except ValueError as exc:
                raise ValueError(f"invalid SpreadsheetML cell index: {explicit_index!r}") from exc
            if column < next_column:
                raise ValueError("SpreadsheetML cell indexes must move forward")
            if column > _MAX_COLUMNS:
                raise ValueError("SpreadsheetML cell index exceeds supported column limit")
            while next_column < column:
                values.append(None)
                next_column += 1
        values.append(_cell_value(cell))
        if len(values) > _MAX_COLUMNS:
            raise ValueError("SpreadsheetML row exceeds supported column limit")
        next_column += 1
    return values


def parse_optimization_xml(path: str | Path) -> dict[str, Any]:
    """Parse an MT5 SpreadsheetML optimization report into normalized rows.

    The first row in the first worksheet table is treated as the header. Empty
    sparse cells become ``None``; numeric-looking values become finite ``int``
    or ``float`` values. Unknown headers and textual values are retained.
    """
    source = Path(path)
    with source.open("rb") as stream:
        raw = stream.read(_MAX_XML_BYTES + 1)
    if not raw:
        raise ValueError("optimization XML is empty")
    if len(raw) > _MAX_XML_BYTES:
        raise ValueError(f"optimization XML exceeds {_MAX_XML_BYTES} bytes")
    try:
        safety_text = _safety_decode(raw)
    except UnicodeDecodeError as exc:
        raise ValueError("optimization XML must be UTF-8 or UTF-16") from exc
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", safety_text, re.IGNORECASE):
        raise ValueError("DTD and entity declarations are not allowed in optimization XML")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"malformed optimization XML: {exc}") from exc

    table = next((node for node in root.iter() if _local_name(node.tag) == "Table"), None)
    if table is None:
        raise ValueError("optimization XML contains no SpreadsheetML table")
    xml_rows = [child for child in table if _local_name(child.tag) == "Row"]
    if not xml_rows:
        raise ValueError("optimization XML contains no header row")
    header_cells = _row_cells(xml_rows[0])
    if not header_cells or any(value is None for value in header_cells):
        raise ValueError("optimization XML has an empty header")
    columns = [str(value).strip() for value in header_cells]
    if any(not column for column in columns):
        raise ValueError("optimization XML has an empty header")
    if len({column.casefold() for column in columns}) != len(columns):
        raise ValueError("optimization XML contains duplicate headers")

    rows: list[dict[str, Any]] = []
    for xml_row in xml_rows[1:]:
        cells = _row_cells(xml_row)
        if not cells or all(value is None for value in cells):
            continue
        if len(cells) > len(columns):
            raise ValueError("optimization XML data row has more cells than headers")
        cells.extend([None] * (len(columns) - len(cells)))
        rows.append({column: _convert_value(value) for column, value in zip(columns, cells)})
    if not rows:
        raise ValueError("optimization XML contains zero data rows")
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _pass_column(columns: Sequence[str]) -> str:
    matches = [column for column in columns if column.casefold() == "pass"]
    if len(matches) != 1:
        raise ForwardJoinError("both optimization files must have exactly one Pass column")
    return matches[0]


def _value_key(value: Any) -> tuple[str, Any]:
    if value is None:
        return ("none", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("number", float(value))
    return ("text", str(value))


def _looks_like_metric(column: str) -> bool:
    name = column.casefold().replace("%", " percent ")
    return any(word in name for word in _METRIC_WORDS)


def join_forward_results(
    base: Mapping[str, Any],
    forward: Mapping[str, Any],
    parameter_columns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Join MT5's forward subset by unique Pass plus its parameter tuple.

    Base passes omitted by MT5 from the forward report are normal and omitted
    from the returned list. Any duplicate key, forward-only pass, or parameter
    mismatch raises ``ForwardJoinError`` instead of guessing a match.
    """
    base_columns = list(base.get("columns", []))
    forward_columns = list(forward.get("columns", []))
    base_pass = _pass_column(base_columns)
    forward_pass = _pass_column(forward_columns)
    shared = [
        column for column in base_columns
        if column in forward_columns and column.casefold() != "pass"
    ]
    if parameter_columns is None:
        parameters = [column for column in shared if not _looks_like_metric(column)]
    else:
        parameters = list(parameter_columns)
        missing = [column for column in parameters if column not in shared]
        if missing:
            raise ForwardJoinError(f"parameter columns are not shared: {missing!r}")

    base_by_pass: dict[tuple[str, Any], Mapping[str, Any]] = {}
    for row in base.get("rows", []):
        key = _value_key(row.get(base_pass))
        if key[0] == "none":
            raise ForwardJoinError("base row has no Pass value")
        if key in base_by_pass:
            raise ForwardJoinError(f"duplicate base Pass value: {row.get(base_pass)!r}")
        base_by_pass[key] = row

    forward_by_pass: dict[tuple[str, Any], Mapping[str, Any]] = {}
    for row in forward.get("rows", []):
        key = _value_key(row.get(forward_pass))
        if key[0] == "none":
            raise ForwardJoinError("forward row has no Pass value")
        if key in forward_by_pass:
            raise ForwardJoinError(f"duplicate forward Pass value: {row.get(forward_pass)!r}")
        if key not in base_by_pass:
            raise ForwardJoinError(f"forward Pass has no base row: {row.get(forward_pass)!r}")
        base_row = base_by_pass[key]
        differing = [
            column for column in parameters
            if _value_key(base_row.get(column)) != _value_key(row.get(column))
        ]
        if differing:
            raise ForwardJoinError(
                f"Pass {row.get(forward_pass)!r} has mismatched parameters: {differing!r}"
            )
        forward_by_pass[key] = row

    return [
        {"pass": base_row[base_pass], "base": base_row, "forward": forward_by_pass[key]}
        for key, base_row in base_by_pass.items()
        if key in forward_by_pass
    ]
