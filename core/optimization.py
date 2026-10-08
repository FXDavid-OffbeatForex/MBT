"""Run a bounded MT5 EA optimization and retain its exact evidence.

MT5 is the execution authority. This module does not simulate EA trades.
"""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import struct
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .connection import load_config, reports_dir
from .tester import _MODEL_MAP, _PERIOD_MAP, _terminal_path, _launch_cmd
from .optimization_results import parse_optimization_xml, join_forward_results
from .optimization_log import tester_log_snapshot, tester_log_diagnostics
from .terminal_lock import terminal_lock
from .optimization_settings import testing_settings, testing_ini
from .ea_input_schema import parse_source_schema
from .commission_profile import managed_commission_profile
from .optimization_log import tester_log_commission


_MODES = {"complete": 1, "genetic": 2}
_CRITERIA = {
    "balance": 0, "balance_profitability": 1, "balance_expected_payoff": 2,
    "balance_drawdown": 3, "balance_recovery": 4, "balance_sharpe": 5,
    "custom": 6, "complex": 7,
}
_FORWARD = {"off": 0, "half": 1, "third": 2, "quarter": 3, "custom": 4}
_INPUT_RE = re.compile(r"^\s*input\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=", re.MULTILINE)
_SAFE_NAME = re.compile(r"^[A-Za-z_]\w*$")
_RUN_ID = re.compile(r"^opt_[0-9a-f]{32}$")


class _EnumType(str):
    """String-compatible source type with independently parsed enum members."""

    def __new__(cls, name: str, members: dict[str, int]):
        obj = super().__new__(cls, name)
        obj.members = members
        return obj


class _DeclaredType(str):
    def __new__(cls, name: str, fixed_only: bool = False):
        obj = super().__new__(cls, name)
        obj.fixed_only = fixed_only
        return obj


def _schema_types(schema: dict) -> dict:
    types = {}
    for name, meta in schema['inputs'].items():
        typ = (_EnumType(meta['type'], meta['enum_values']) if 'enum_values' in meta
               else _DeclaredType(meta['type'], meta['fixed_only']))
        typ.fixed_only = meta['fixed_only']
        types[name] = typ
    return types


def _verify_input_schema(manifest: dict) -> None:
    saved = manifest.get('input_schema')
    if saved:
        actual = parse_source_schema(Path(saved['source_path']), Path(saved['mql5_root']))
        if actual['schema_sha256'] != saved['schema_sha256']:
            raise ValueError('EA input source/include metadata changed since optimization')


_BUILTIN_ENUMS = {"ENUM_MA_METHOD": {
    "MODE_SMA": 0, "MODE_EMA": 1, "MODE_SMMA": 2, "MODE_LWMA": 3,
}}


def _enum_types(code: str) -> dict[str, _EnumType]:
    enums = {name: _EnumType(name, members.copy()) for name, members in _BUILTIN_ENUMS.items()}
    declarations = re.findall(r"\benum\s+([A-Za-z_]\w*)\s*\{([^{}]*)\}\s*;", code, re.S)
    if len(declarations) != len(re.findall(r"\benum\b", code)):
        raise ValueError("EA has unsupported enum declarations")
    for name, body in declarations:
        if name in enums:
            raise ValueError("EA has duplicate or reserved enum type")
        members = {}
        for declaration in body.rstrip().rstrip(",").split(","):
            match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*=\s*([+-]?\d+)\s*", declaration)
            if not match or match[1] in members:
                raise ValueError("Local enums require unique explicitly valued members")
            value = int(match[2])
            _representable(Decimal(value), "int", name)
            if value in members.values():
                raise ValueError("Enum aliases are unsupported")
            members[match[1]] = value
        enums[name] = _EnumType(name, members)
    return enums


def _bool_value(value, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.casefold() in ("true", "false", "0", "1"):
        return value.casefold() in ("true", "1")
    raise ValueError(f"{label} requires an unambiguous boolean value")


def _enum_value(value, item: dict, label: str) -> Decimal:
    members = item.get("enum_values")
    if not isinstance(members, dict) or not members:
        raise ValueError(f"{label} lacks verified enum members")
    if isinstance(value, str) and value in members:
        value = members[value]
    number = _number(value, label)
    if number != number.to_integral_value() or number not in members.values():
        raise ValueError(f"{label} is not a declared enum value")
    return number


def _clean_line(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError(f"Invalid {label}")
    return value


def _date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(_clean_line(value, label))
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc


def _number(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def _data_dir() -> Path:
    """Resolve the configured terminal's data dir without newest-folder fallback."""
    cfg = load_config().get("tester") or {}
    terminal = Path(_terminal_path()).resolve()
    if not terminal.is_file():
        raise ValueError("Configured terminal64.exe does not exist")
    explicit = str(cfg.get("data_dir") or "").strip()
    portable = cfg.get("portable")
    if not isinstance(portable, bool):
        raise ValueError("Set tester.portable explicitly before optimization")
    if explicit:
        path = Path(explicit).resolve()
        if not (path / "MQL5" / "Profiles" / "Tester").is_dir():
            raise ValueError("Configured tester.data_dir has no tester profile folder")
        if portable and path != terminal.parent:
            raise ValueError("Portable tester.data_dir must equal the terminal install directory")
        if portable:
            return path
    if portable:
        return terminal.parent
    if sys.platform != "win32":
        raise ValueError("Set tester.data_dir explicitly outside Windows")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValueError("APPDATA is unavailable")
    base = Path(appdata) / "MetaQuotes" / "Terminal"
    matches = []
    for origin in base.glob("*/origin.txt"):
        if origin.parent.name.lower() == "common":
            continue
        raw = origin.read_bytes()
        source = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8", errors="replace")
        if os.path.normcase(source.strip().replace("/", "\\")) == os.path.normcase(str(terminal.parent).replace("/", "\\")):
            matches.append(origin.parent.resolve())
    if len(matches) != 1:
        raise ValueError("Could not uniquely resolve this terminal's normal-mode data directory")
    if explicit and path != matches[0]:
        raise ValueError("Configured tester.data_dir does not match this terminal's origin")
    return matches[0]


def _terminal_busy() -> bool:
    if sys.platform != "win32":
        # Cross-platform detection needs its own verified contract.
        raise ValueError("Native optimization currently requires Windows")
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if completed.returncode:
        raise RuntimeError("Cannot verify whether an MT5 terminal is already running")
    return any(row and row[0].lower() == "terminal64.exe" for row in csv.reader(completed.stdout.splitlines()))


def _ea_paths(expert: str, data: Path) -> tuple[Path, Path, str]:
    expert = _clean_line(expert, "EA name").replace("/", "\\")
    if expert.lower().endswith(".ex5"):
        expert = expert[:-4]
    pieces = expert.split("\\")
    if any(not piece or piece in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_ .-]+", piece) for piece in pieces):
        raise ValueError("EA name must be relative to MQL5/Experts")
    root = (data / "MQL5" / "Experts").resolve()
    binary = root.joinpath(*pieces).with_suffix(".ex5").resolve()
    source = root.joinpath(*pieces).with_suffix(".mq5").resolve()
    if not binary.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError("EA path escapes MQL5/Experts")
    if not binary.is_file() or not source.is_file():
        raise ValueError("This first slice requires compiled EA and adjacent .mq5 source")
    if source.stat().st_mtime > binary.stat().st_mtime:
        raise ValueError("EA source is newer than compiled binary; recompile before optimizing")
    return binary, source, "\\".join(pieces)


def _input_types(source: Path) -> dict[str, str]:
    raw = source.read_text(encoding="utf-8-sig", errors="replace")
    # This first slice supports only a deliberately small, auditable EA source
    # shape. Includes and conditional compilation can otherwise hide inputs or
    # change optimization ranges without our seeing the compiled contract.
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw, flags=re.DOTALL)
    directives = re.findall(r"^\s*#\s*([^\n]+)", code, re.MULTILINE)
    allowed = r"(?:property\s+[^\n]+|define\s+[A-Za-z_]\w*\s+[+-]?\d+(?:\.\d+)?|include\s*<Trade\\Trade\.mqh>)"
    if directives and any(not re.fullmatch(allowed, item.strip()) for item in directives):
        raise ValueError("EA has unsupported preprocessor directives or includes")
    if re.search(r"\b(?:sinput|OnTesterInit|ParameterSetRange)\b", code):
        raise ValueError("EA has unsupported tester range or static input declarations")
    declarations = _INPUT_RE.findall(code)
    input_tokens = re.findall(r"\binput\b", code)
    if len(declarations) != len(input_tokens):
        raise ValueError("EA has input declarations MBT cannot safely parse")
    names = [name for _, name in declarations]
    if len(set(names)) != len(names):
        raise ValueError("EA has duplicate input names")
    enums = _enum_types(code)
    supported = {"int", "double", "float", "long", "bool", "string"} | set(enums)
    if any(typ not in supported for typ, _ in declarations):
        raise ValueError("EA has unsupported input types")
    return {name: enums.get(typ, typ) for typ, name in declarations}


def _representable(number: Decimal, typ: str, label: str) -> None:
    if typ == "int" and not (-2**31 <= number <= 2**31 - 1):
        raise ValueError(f"{label} exceeds MQL int32 bounds")
    if typ == "long" and not (-2**63 <= number <= 2**63 - 1):
        raise ValueError(f"{label} exceeds MQL int64 bounds")
    if typ in ("double", "float"):
        try:
            value = float(number)
            if typ == "float":
                value = struct.unpack("f", struct.pack("f", value))[0]
        except (OverflowError, struct.error) as exc:
            raise ValueError(f"{label} exceeds MQL {typ} bounds") from exc
        if not math.isfinite(value):
            raise ValueError(f"{label} exceeds MQL {typ} bounds")
        if number != 0 and value == 0:
            raise ValueError(f"{label} underflows MQL {typ} precision")


def _machine_float(number: Decimal, typ: str) -> float:
    value = float(number)
    return struct.unpack("f", struct.pack("f", value))[0] if typ == "float" else value


def _set_lines(parameters: dict, source_types: dict[str, str], maximum: int) -> tuple[list[str], int]:
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("parameters must name every EA input")
    if set(parameters) != set(source_types):
        raise ValueError("parameters must exactly match the EA's supported input names")
    lines, planned = [], 1
    for name, typ in source_types.items():
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Unsafe EA input name")
        item = parameters[name]
        if not isinstance(item, dict) or item.get("type") != typ:
            raise ValueError(f"{name} requires declared type {typ}")
        if getattr(typ, 'fixed_only', False) and any(k in item for k in ('start', 'step', 'stop')):
            raise ValueError(f'{name} is a fixed-only sinput and cannot be optimized')
        if set(item) - {"type", "value", "start", "step", "stop", "enum_values"}:
            raise ValueError(f"{name} has unsupported parameter fields")
        if "enum_values" in item and not isinstance(typ, _EnumType):
            raise ValueError(f"{name} has enum metadata for a non-enum input")
        value = item.get("value")
        enum = isinstance(typ, _EnumType)
        if enum:
            # Only source-derived metadata may authorize enum values. Persist it
            # with the request so a later frozen single test can verify labels.
            item["enum_values"] = typ.members.copy()
            value = _enum_value(value, item, f"{name}.value")
            item["value"] = int(value)
        if typ in ("int", "long", "double", "float") or enum:
            current = _number(value, f"{name}.value")
            _representable(current, typ, f"{name}.value")
            if typ in ("int", "long") and current != current.to_integral_value():
                raise ValueError(f"{name} requires integer values")
            has_range = any(key in item for key in ("start", "step", "stop"))
            if has_range:
                if not all(key in item for key in ("start", "step", "stop")):
                    raise ValueError(f"{name} range needs start, step and stop")
                start = _number(item["start"], f"{name}.start")
                step = _number(item["step"], f"{name}.step")
                stop = _number(item["stop"], f"{name}.stop")
                for label, number in (("start", start), ("step", step), ("stop", stop)):
                    _representable(number, typ, f"{name}.{label}")
                if step <= 0 or stop < start:
                    raise ValueError(f"{name} has invalid range")
                if typ in ("int", "long") and any(v != v.to_integral_value() for v in (start, step, stop)):
                    raise ValueError(f"{name} range must be integer")
                if typ in ("double", "float") and (
                    _machine_float(start + step, typ) == _machine_float(start, typ)
                    or _machine_float(stop - step, typ) == _machine_float(stop, typ)
                ):
                    raise ValueError(f"{name}.step cannot advance MQL {typ} values")
                count = int((stop - start) // step) + 1
                if enum:
                    if any(v != v.to_integral_value() for v in (start, step, stop)):
                        raise ValueError(f"{name} enum range must be integer")
                    if count > len(typ.members):
                        raise ValueError(f"{name} enum range contains undeclared values")
                    for index in range(count):
                        _enum_value(start + index * step, item, f"{name}.range")
                if count < 2:
                    raise ValueError(f"{name} range needs at least two values")
                planned *= count
                if planned > maximum:
                    raise ValueError(f"Complete search exceeds max_combinations={maximum}")
                lines.append(f"{name}={current}||{start}||{step}||{stop}||Y")
            else:
                lines.append(f"{name}={current}||{current}||1||{current}||N")
        elif typ == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{name} requires bool")
            if any(key in item for key in ("start", "step", "stop")):
                if (not isinstance(item.get("start"), bool) or item["start"]
                        or not isinstance(item.get("stop"), bool) or not item["stop"]
                        or isinstance(item.get("step"), bool) or item.get("step") != 1):
                    raise ValueError(f"{name} bool sweep requires start=false, step=1, stop=true")
                planned *= 2
                if planned > maximum:
                    raise ValueError(f"Complete search exceeds max_combinations={maximum}")
                lines.append(f"{name}={'true' if value else 'false'}||false||1||true||Y")
            else:
                lines.append(f"{name}={'true' if value else 'false'}")
        elif typ == "string":
            clean = '' if value == '' else _clean_line(value, name)
            if any(char in clean for char in "=|"):
                raise ValueError(f"{name} contains unsupported SET syntax")
            if any(key in item for key in ("start", "step", "stop")):
                raise ValueError(f"{name} string inputs cannot be optimized")
            lines.append(f"{name}={clean}")
        else:
            raise ValueError(f"Unsupported EA input type {typ}")
    if planned == 1:
        raise ValueError("At least one EA input must have a range")
    return lines, planned


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _run_dir(run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid run_id")
    root = (Path(reports_dir()) / "optimization").resolve()
    path = (root / run_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid run path")
    return path


def _hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _validate_observed(manifest: dict, base: dict) -> None:
    """Require every requested complete-grid tuple, not merely a nonempty XML."""
    request = manifest["request"]
    varied = {name: item for name, item in request["parameters"].items() if "start" in item}
    _canonicalize_input_rows(manifest, base)
    columns = set(base["columns"])
    if not {"Pass", "Result", "Trades"}.issubset(columns) or not set(varied).issubset(columns):
        raise ValueError("Optimization report lacks Pass, metrics, or requested parameter columns")
    passes, tuples = set(), set()
    for row in base["rows"]:
        pass_id = row.get("Pass")
        if isinstance(pass_id, bool) or not isinstance(pass_id, int) or pass_id < 0 or pass_id in passes:
            raise ValueError("Optimization report has missing, invalid, or duplicate Pass IDs")
        passes.add(pass_id)
        values = []
        for name, item in varied.items():
            actual = row.get(name)
            if item["type"] == "bool":
                actual = int(_bool_value(actual, name))
                row[name] = bool(actual)
                start, step, stop = Decimal(0), Decimal(1), Decimal(1)
            elif "enum_values" in item:
                actual = int(_enum_value(actual, item, name))
                row[name] = actual
                start, step, stop = (_number(item[key], f"{name}.{key}") for key in ("start", "step", "stop"))
            else:
                start, step, stop = (_number(item[key], f"{name}.{key}") for key in ("start", "step", "stop"))
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
                raise ValueError(f"Optimization report has invalid {name} value")
            value = Decimal(str(actual))
            position = (value - start) / step
            # MT5 serializes floating input values through spreadsheet decimals.
            nearest = position.to_integral_value()
            if not (0 <= nearest <= (stop - start) // step) or abs(position - nearest) > Decimal("0.000001"):
                raise ValueError(f"Optimization report contains {name} outside the requested range")
            values.append(int(nearest))
        key = tuple(values)
        if key in tuples:
            raise ValueError("Optimization report has duplicate parameter tuples")
        tuples.add(key)
    if request["mode"] == "complete" and len(tuples) != manifest["planned_combinations"]:
        raise ValueError(f"Incomplete complete-search result: {len(tuples)} of {manifest['planned_combinations']} requested tuples")


def _canonicalize_input_rows(manifest: dict, parsed: dict) -> None:
    """Canonicalize only typed EA inputs; retain untouched raw XML evidence."""
    for row in parsed["rows"]:
        for name, item in manifest["request"]["parameters"].items():
            if name not in row:
                continue
            if item["type"] == "bool":
                row[name] = _bool_value(row[name], name)
            elif "enum_values" in item:
                row[name] = int(_enum_value(row[name], item, name))


def _optimize_ea_unlocked(
    expert: str, symbol: str, timeframe: str, from_date: str, to_date: str,
    parameters: dict, mode: str = "complete", criterion: str = "balance",
    forward_mode: str = "off", forward_date: str | None = None,
    model: str = "open_prices", max_combinations: int = 1000,
    min_trades: int = 30, timeout_sec: int = 1800,
    testing: dict | None = None,
) -> dict:
    """Run one local-agent MT5 optimization. Never enables live trading/cloud."""
    testing = testing_settings(testing)
    parameters = copy.deepcopy(parameters)
    if mode not in _MODES or criterion not in _CRITERIA or forward_mode not in _FORWARD:
        raise ValueError("Unsupported mode, criterion or forward_mode")
    if testing.get('commission') and forward_mode != 'off':
        raise ValueError('Custom commissions require forward_mode=off; use optimize_ea_holdout for paired out-of-sample tests')
    if model not in _MODEL_MAP or model == "math":
        raise ValueError("Unsupported tick model for EA optimization")
    if not isinstance(max_combinations, int) or not 2 <= max_combinations <= 100000:
        raise ValueError("max_combinations must be 2..100000")
    if not isinstance(min_trades, int) or min_trades < 1:
        raise ValueError("min_trades must be positive")
    if not isinstance(timeout_sec, int) or not 30 <= timeout_sec <= 7200:
        raise ValueError("timeout_sec must be 30..7200")
    start, end = _date(from_date, "from_date"), _date(to_date, "to_date")
    if start >= end:
        raise ValueError("from_date must precede to_date")
    if forward_mode == "custom":
        cut = _date(forward_date, "forward_date")
        if not start < cut < end:
            raise ValueError("forward_date must fall within the test period")
    elif forward_date is not None:
        raise ValueError("forward_date requires forward_mode=custom")
    symbol = _clean_line(symbol, "symbol")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", symbol):
        raise ValueError("Invalid symbol")
    period = _PERIOD_MAP.get(str(timeframe).lower())
    if not period:
        raise ValueError("Unsupported timeframe")
    data = _data_dir()
    binary, source, ea_name = _ea_paths(expert, data)
    input_schema = parse_source_schema(source, data / 'MQL5')
    if any(Path(dep['path']).stat().st_mtime > binary.stat().st_mtime for dep in input_schema['dependencies']):
        raise ValueError('EA source/include dependency is newer than compiled binary; recompile before optimizing')
    source_types = _schema_types(input_schema)
    if not source_types:
        raise ValueError("EA has no supported input declarations")
    set_lines, planned = _set_lines(parameters, source_types, max_combinations)
    if _terminal_busy():
        raise RuntimeError("An MT5 terminal is already running; close it before MBT optimization")

    run_id = "opt_" + uuid4().hex
    folder = _run_dir(run_id)
    folder.mkdir(parents=True, exist_ok=False)
    profile = data / "MQL5" / "Profiles" / "Tester"
    if not profile.is_dir():
        raise ValueError("MT5 tester profile directory not found")
    set_name = run_id + ".set"
    set_path = profile / set_name
    expected = data / (run_id + ".xml")
    expected_forward = data / (run_id + ".forward.xml")
    if any(p.exists() for p in (set_path, expected, expected_forward)):
        raise RuntimeError("Unique MT5 output path unexpectedly exists")
    set_text = "\n".join(set_lines) + "\n"
    (folder / set_name).write_text(set_text, encoding="utf-8")
    with set_path.open("x", encoding="utf-8") as stream:
        stream.write(set_text)
    ini_lines = [
        "[Experts]", "Enabled=0", "AllowLiveTrading=0", "AllowDllImport=0", "",
        "[StartUp]", "Expert=", "", "[Tester]", f"Expert={ea_name}",
        f"ExpertParameters={set_name}", f"Symbol={symbol}", f"Period={period}",
        f"Model={_MODEL_MAP[model]}", f"Optimization={_MODES[mode]}",
        f"OptimizationCriterion={_CRITERIA[criterion]}", f"ForwardMode={_FORWARD[forward_mode]}",
        f"FromDate={start:%Y.%m.%d}", f"ToDate={end:%Y.%m.%d}",
        *testing_ini(testing), "UseLocal=1",
        "UseRemote=0", "UseCloud=0", "Visual=0", f"Report={run_id}",
        "ReplaceReport=0", "ShutdownTerminal=1",
    ]
    if forward_mode == "custom":
        ini_lines.append(f"ForwardDate={cut:%Y.%m.%d}")
    ini_path = folder / (run_id + ".ini")
    ini_path.write_text("\n".join(ini_lines) + "\n", encoding="utf-8")
    request = {
        "expert": ea_name, "symbol": symbol, "timeframe": period,
        "from_date": str(start), "to_date": str(end), "parameters": parameters,
        "mode": mode, "criterion": criterion, "forward_mode": forward_mode,
        "forward_date": forward_date, "model": model, "min_trades": min_trades,
        "testing": testing,
    }
    manifest = {
        "run_id": run_id, "status": "running", "request": request,
        "planned_combinations": planned, "run_dir": str(folder),
        "terminal_path": str(Path(_terminal_path()).resolve()),
        "data_dir": str(data), "ea_sha256": _hash(binary),
        "source_sha256": _hash(source), "set_sha256": _hash(folder / set_name),
        "input_schema": input_schema,
        "ini_sha256": _hash(ini_path), "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(folder / "manifest.json", manifest)
    # The launch is intentionally bounded. A failed/partial result stays inspectable.
    if _terminal_busy():
        manifest["status"] = "busy_terminal"
        _write_json(folder / "manifest.json", manifest)
        raise RuntimeError("MT5 became busy before optimization launch")
    cmd = _launch_cmd(str(ini_path))
    log_snapshot = tester_log_snapshot(data)
    kwargs = {"cwd": str(Path(_terminal_path()).parent), "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "timeout": timeout_sec, "check": False}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        cache_prefix = (f"{binary.stem}.{symbol}.{period}.{start:%Y%m%d}.{end:%Y%m%d}.")
        with managed_commission_profile(data, testing.get('commission'), folder,
                                        cache_prefix=cache_prefix if testing.get('commission') else None) as commission_evidence:
            if commission_evidence and commission_evidence['template_sha256'] != testing['commission_template_sha256']:
                raise ValueError('Native commission template changed before launch')
            completed = subprocess.run(cmd, **kwargs)
            if commission_evidence:
                manifest['commission_verification'] = tester_log_commission(log_snapshot, commission_evidence)
                manifest['commission_profile'] = commission_evidence
        manifest["exit_code"] = completed.returncode
        manifest["status"] = "processing" if completed.returncode == 0 else "launch_failed"
    except subprocess.TimeoutExpired:
        manifest["status"] = "timed_out"
    except OSError as exc:
        manifest["status"] = "launch_failed"
        manifest["error"] = f"Could not launch the configured MT5 terminal: {type(exc).__name__}"
    except (ValueError, RuntimeError) as exc:
        manifest['status'] = 'commission_verification_failed'
        manifest['error'] = str(exc)
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["tester_diagnostics"] = tester_log_diagnostics(log_snapshot)
    if testing.get('commission') and manifest['tester_diagnostics'].get('optimization_cache_reused'):
        manifest['status'] = 'commission_verification_failed'
        manifest['error'] = 'MT5 reused optimization cache despite custom-commission isolation'
    for key, source_path in (("optimization", expected), ("forward", expected_forward)):
        if source_path.is_file() and source_path.stat().st_size:
            target = folder / source_path.name
            shutil.copy2(source_path, target)
            manifest[key + "_path"] = str(target)
            manifest[key + "_sha256"] = _hash(target)
    if manifest["status"] == "processing":
        try:
            base = parse_optimization_xml(manifest["optimization_path"])
            _validate_observed(manifest, base)
            forward = parse_optimization_xml(manifest["forward_path"]) if forward_mode != "off" else None
            if forward is not None:
                _canonicalize_input_rows(manifest, forward)
            if forward is not None:
                varied = [name for name, item in parameters.items() if "start" in item]
                join_forward_results(base, forward, varied)
            result = _normalize(manifest, base, forward)
            _write_json(folder / "results.json", result)
            if mode == "genetic":
                diag = manifest["tester_diagnostics"]
                if diag.get("genetic_disabled_small_grid") and diag.get("effective_mode") == "complete":
                    if len(base["rows"]) != planned:
                        raise ValueError("Incomplete complete-search fallback from genetic request")
                    manifest["status"] = "completed" if result["selected_pass"] else "no_eligible_pass"
                    manifest["warning"] = "MT5 changed the requested genetic search to complete search because the grid was too small"
                elif (diag.get("effective_mode") == "genetic" and diag.get("statistics_done")
                      and diag.get("genetic_finished")
                      and diag.get("genetic_search_space") == planned
                      and diag.get("remote_tasks") == 0 and diag.get("cloud_tasks") == 0):
                    manifest["status"] = "completed" if result["selected_pass"] else "no_eligible_pass"
                else:
                    manifest["status"] = "unverified_completion"
                    manifest["error"] = "Tester log did not confirm completion of a genetic search"
            else:
                manifest["status"] = "completed" if result["selected_pass"] else "no_eligible_pass"
        except (KeyError, ValueError, OSError) as exc:
            manifest["status"] = "incomplete_results" if "Incomplete complete-search" in str(exc) else "invalid_results"
            if isinstance(exc, KeyError) and exc.args == ("forward_path",):
                diag = manifest["tester_diagnostics"]
                manifest["error"] = ("MT5 logged forward results but did not export the requested forward XML"
                                     if diag.get("forward_cached_records") or diag.get("forward_reported_passes")
                                     else "MT5 did not save the requested forward report")
            elif isinstance(exc, KeyError) and exc.args == ("optimization_path",):
                manifest["error"] = "MT5 did not save the optimization report"
            else:
                manifest["error"] = str(exc)
    _write_json(folder / "manifest.json", manifest)
    return {"run_id": run_id, "status": manifest["status"],
            "planned_combinations": planned,
            "parsed_rows": result["counts"]["parsed_rows"] if manifest["status"] in ("completed", "no_eligible_pass", "unverified_completion") else None,
            "run_dir": str(folder), "error": manifest.get("error"), "warning": manifest.get("warning")}


def optimize_ea(
    expert: str, symbol: str, timeframe: str, from_date: str, to_date: str,
    parameters: dict, mode: str = "complete", criterion: str = "balance",
    forward_mode: str = "off", forward_date: str | None = None,
    model: str = "open_prices", max_combinations: int = 1000,
    min_trades: int = 30, timeout_sec: int = 1800,
    html_report: bool = True,
    testing: dict | None = None,
) -> dict:
    """Run MT5, then render a verified result outside the terminal lock."""
    if not isinstance(html_report, bool):
        raise ValueError("html_report must be a boolean")
    with terminal_lock(_terminal_path(), str(_data_dir())):
        outcome = _optimize_ea_unlocked(
            expert, symbol, timeframe, from_date, to_date, parameters, mode,
            criterion, forward_mode, forward_date, model, max_combinations,
            min_trades, timeout_sec, *([testing] if testing is not None else []),
        )
    outcome["report_html"] = None
    if html_report and outcome["status"] in ("completed", "no_eligible_pass"):
        try:
            outcome["report_html"] = render_experiment_report(outcome["run_id"])["report_html"]
        except Exception as exc:
            # Keep the real MT5 outcome and run ID even if presentation fails.
            outcome["report_error"] = str(exc)
    return outcome


def recover_forward_report(run_id: str, html_report: bool = True) -> dict:
    """Finish a failed forward export using MT5's manually exported XML.

    The user must export the Forward Results table to the exact filename in
    this run's MBT folder. No arbitrary path, cache file, or inferred metrics
    are accepted. This does not repair MT5's command-line exporter itself.
    """
    if not isinstance(html_report, bool):
        raise ValueError("html_report must be a boolean")
    folder = _run_dir(run_id)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("run_id") != run_id or manifest.get("status") != "invalid_results"
            or manifest.get("request", {}).get("forward_mode") == "off"
            or manifest.get("forward_path") or not manifest.get("optimization_path")
            or "did not export the requested forward XML" not in manifest.get("error", "")):
        raise ValueError("Run is not awaiting a missing MT5 forward export")
    base_path = folder / (run_id + ".xml")
    forward_path = folder / (run_id + ".forward.xml")
    if (Path(manifest["optimization_path"]).resolve() != base_path.resolve()
            or _hash(base_path) != manifest.get("optimization_sha256")):
        raise ValueError("Saved optimization XML no longer matches the run")
    if not forward_path.is_file() or not forward_path.stat().st_size:
        raise ValueError(f"Export MT5 Forward Results to {forward_path} first")
    base = parse_optimization_xml(base_path)
    _validate_observed(manifest, base)
    forward = parse_optimization_xml(forward_path)
    _canonicalize_input_rows(manifest, forward)
    varied = [name for name, item in manifest["request"]["parameters"].items() if "start" in item]
    join_forward_results(base, forward, varied)
    recorded_count = manifest.get("tester_diagnostics", {}).get("forward_cached_records")
    if recorded_count is not None and forward["row_count"] != recorded_count:
        raise ValueError("Forward export row count differs from MT5's recorded cache count")
    result = _normalize(manifest, base, forward)
    if manifest["request"]["mode"] == "genetic":
        diag = manifest.get("tester_diagnostics", {})
        verified = (diag.get("effective_mode") == "genetic" and diag.get("statistics_done")
                    and diag.get("genetic_finished")
                    and diag.get("genetic_search_space") == manifest["planned_combinations"]
                    and diag.get("remote_tasks") == 0 and diag.get("cloud_tasks") == 0)
        verified |= (diag.get("genetic_disabled_small_grid")
                     and diag.get("effective_mode") == "complete"
                     and base["row_count"] == manifest["planned_combinations"])
        if not verified:
            raise ValueError("Genetic completion remains unverified; cannot recover as completed")
    _write_json(folder / "results.json", result)
    manifest["forward_path"] = str(forward_path)
    manifest["forward_sha256"] = _hash(forward_path)
    manifest["forward_export_source"] = "manual_mt5_forward_results"
    manifest.pop("error", None)
    manifest["status"] = "completed" if result["selected_pass"] else "no_eligible_pass"
    _write_json(manifest_path, manifest)
    outcome = {"run_id": run_id, "status": manifest["status"],
               "forward_rows": forward["row_count"], "report_html": None,
               "warning": "Forward XML was manually exported from MT5; verify the selected cache in MT5"}
    if html_report:
        try:
            outcome["report_html"] = render_experiment_report(run_id)["report_html"]
        except Exception as exc:
            outcome["report_error"] = str(exc)
    return outcome


def _normalize(manifest: dict, base: dict, forward: dict | None) -> dict:
    inputs = {name for name, item in manifest["request"]["parameters"].items() if "start" in item}
    rows = base["rows"]
    tuples = {tuple((key, str(row.get(key))) for key in sorted(inputs)) for row in rows}
    min_trades = manifest["request"]["min_trades"]
    eligible = [row for row in rows if isinstance(row.get("Trades"), (int, float)) and row["Trades"] >= min_trades
                and isinstance(row.get("Result"), (int, float)) and math.isfinite(row["Result"])]
    selected = max(eligible, key=lambda row: row["Result"]) if eligible else None
    return {"optimization": base, "forward": forward, "selected_pass": selected,
            "counts": {"planned_combinations": manifest["planned_combinations"],
                       "parsed_rows": len(rows), "distinct_parameter_tuples": len(tuples),
                       "eligible_rows": len(eligible), "forward_rows": len(forward["rows"]) if forward else 0,
                       "mt5_reported_passes": manifest.get("tester_diagnostics", {}).get("mt5_reported_passes"),
                       "failed_rows": None}}


def get_optimization_results(run_id: str, offset: int = 0, limit: int = 100) -> dict:
    if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Invalid offset or limit")
    folder = _run_dir(run_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    out = {"run_id": run_id, "status": manifest["status"], "request": manifest["request"],
           "planned_combinations": manifest["planned_combinations"],
           "tester_diagnostics": manifest.get("tester_diagnostics", {}),
           "warning": manifest.get("warning"),
           "holdout_status": manifest.get("holdout_status"),
           "holdout_error": manifest.get("holdout_error"),
           "holdout_batch_status": manifest.get("holdout_batch_status"),
           "holdout_batch_error": manifest.get("holdout_batch_error"),
           "selected_detail_status": manifest.get("selected_detail_status"),
           "selected_detail_error": manifest.get("selected_detail_error"),
           "holdout_cutoff_date": manifest.get("holdout_cutoff_date"),
           "holdout_end_date": manifest.get("holdout_end_date")}
    result_path = folder / "results.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        out.update({"counts": result["counts"], "selected_pass": result["selected_pass"],
                    "holdout": result.get("holdout"),
                    "holdout_selection": result.get("holdout_selection"),
                    "holdout_summary": result.get("holdout_summary"),
                    "holdout_candidate_count": len(result.get("holdout_candidates") or []),
                    "holdout_candidates": (result.get("holdout_candidates") or [])[offset:offset + limit],
                    "in_sample": result.get("in_sample"),
                    "monte_carlo": (result.get('monte_carlo') or [])[offset:offset + limit],
                    "columns": result["optimization"]["columns"],
                    "rows": result["optimization"]["rows"][offset:offset + limit],
                    "forward_columns": result["forward"]["columns"] if result["forward"] else [],
                    "forward_rows": result["forward"]["rows"][offset:offset + limit] if result["forward"] else []})
    else:
        out["error"] = manifest.get("error", "Normalized results unavailable")
    return out


def render_experiment_report(run_id: str) -> dict:
    from .report_optimization_html import render_optimization_report

    folder = _run_dir(run_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] not in ("completed", "no_eligible_pass", "holdout_partial",
                                  "holdout_failed", "selected_detail_failed"):
        raise ValueError("Report requires parsed optimization results")
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = {
        "Optimization XML": manifest.get("optimization_path"),
        "Forward XML": manifest.get("forward_path"),
        "Tester INI": str(folder / (run_id + ".ini")),
        "Tester SET": str(folder / (run_id + ".set")),
    }
    if result.get("holdout"):
        manifest["artifacts"]["Independent holdout HTML"] = result["holdout"]["report_path"]
    if result.get("in_sample"):
        manifest["artifacts"]["Selected in-sample HTML"] = result["in_sample"]["report_path"]
    path = render_optimization_report(manifest, result, str(folder / "report.html"))
    return {"run_id": run_id, "report_html": path, "status": manifest["status"]}
