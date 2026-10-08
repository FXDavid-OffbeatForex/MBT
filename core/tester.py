"""
Automated MT5 Strategy Tester runner.

This is MBT's SECOND backtest engine. The replay engine (backtest.py) reads an
indicator's logged signals and replays bars in Python — fast, for signal-level
iteration. This engine instead drives MT5's OWN Strategy Tester headlessly, so
an Expert Advisor's real code runs bar-by-bar with real spread, swaps and order
execution. Use replay to iterate; use the tester to validate.

How it works (no special API — pure subprocess):
  1. write a tester .ini  ([Tester] section)
  2. launch  terminal64.exe /config:<ini>   (it runs the test and shuts itself
     down via ShutdownTerminal=1)
  3. parse the .htm report it writes; optionally read an EA OnTester() summary.

Cross-platform: on Windows the terminal is launched directly; elsewhere a
launcher (e.g. "wine") from config is prepended. Nothing here imports the
MetaTrader5 python package — the tester is the terminal, not the data API.
"""

import os
import re
import glob
import time
import shutil
import subprocess
import sys
from datetime import datetime

from .connection import load_config, _toolkit_root, reports_dir
from .terminal_lock import terminal_lock

# ENUM_TIMEFRAMES strings MT5 accepts in the .ini Period field
_PERIOD_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D1", "1w": "W1",
    # also accept the MT5 names directly
    "m1": "M1", "m5": "M5", "m15": "M15", "m30": "M30",
    "h1": "H1", "h4": "H4", "d1": "D1", "w1": "W1",
}

# Tester model codes
_MODEL_MAP = {
    "every_tick": 0,
    "1min_ohlc": 1,
    "open_prices": 2,
    "math": 3,
    "real_ticks": 4,
}


def _tester_cfg() -> dict:
    return (load_config().get("tester") or {})


def _terminal_path() -> str:
    cfg = load_config()
    t = _tester_cfg()
    path = (t.get("terminal_path") or cfg.get("mt5_path") or "").strip()
    if not path:
        raise RuntimeError("No terminal path: set tester.terminal_path or mt5_path in config.yaml")
    return path


def _read_text_any(path: str) -> str:
    """Decode a small text file that may be utf-16 / utf-8 / cp1252. utf-16 is
    trusted only with a BOM — Python's BOM-less utf-16 codec silently decodes any
    even-length stream, so a utf-8/cp1252 file would become garbage rather than
    falling through. Otherwise decode utf-8 then cp1252 (cp1252 maps all bytes)."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):       # utf-16 LE/BE BOM
        try:
            return data.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("cp1252", errors="ignore")


def _appdata_terminal_base() -> str:
    """The MetaQuotes/Terminal dir that holds per-install data folders."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "MetaQuotes", "Terminal")
    # off-Windows (Wine) fallback
    home = os.path.expanduser("~")
    return os.path.join(home, ".wine", "drive_c", "users",
                        os.environ.get("USER", ""), "AppData", "Roaming",
                        "MetaQuotes", "Terminal")


def _find_appdata_data_dir(install_dir: str) -> str:
    """Non-portable installs keep their data under
    %APPDATA%/MetaQuotes/Terminal/<hash>/. Pick the folder whose origin.txt
    points at this install (best), else the newest one with an MQL5 tree."""
    base = _appdata_terminal_base()
    if not os.path.isdir(base):
        return ""
    want = os.path.normcase(install_dir.replace("/", "\\"))
    best = []
    for name in os.listdir(base):
        d = os.path.join(base, name)
        if name.lower() == "common" or not os.path.isdir(os.path.join(d, "MQL5")):
            continue                      # skip Common/ and non-terminal dirs
        score = 0
        origin = os.path.join(d, "origin.txt")
        if os.path.isfile(origin):
            txt = _read_text_any(origin)
            if want in os.path.normcase(txt.replace("/", "\\")):
                score = 2
        best.append((score, os.path.getmtime(d), d))
    if not best:
        return ""
    best.sort(reverse=True)               # highest score, then newest
    return best[0][2]


def _data_dir() -> str:
    """MT5 data dir (holds MQL5/, and where reports land). Config tester.data_dir
    wins. Otherwise this MUST mirror _launch_cmd's portability decision, or MT5
    writes the report where we don't look: an explicit tester.portable flag is
    authoritative (portable -> data beside the exe; non-portable -> under
    %APPDATA%/MetaQuotes/Terminal/<hash>). When the flag is unset we fall back to
    the filesystem heuristic (MQL5 next to the exe == portable)."""
    t = _tester_cfg()
    d = (t.get("data_dir") or "").strip()
    if d:
        return d
    exe_dir = os.path.dirname(_terminal_path())
    portable = t.get("portable")
    if portable:                          # explicit portable: mirror /portable launch
        return exe_dir
    if portable is None and os.path.isdir(os.path.join(exe_dir, "MQL5")):
        return exe_dir                    # flag unset: heuristic — MQL5 beside exe
    # explicit non-portable, or no MQL5 beside the exe: data is under %APPDATA%
    found = _find_appdata_data_dir(exe_dir)
    return found or exe_dir               # fallback to exe dir if not located


def _fmt_date(d) -> str:
    """Accept 'YYYY-MM-DD' / 'YYYY.MM.DD' / datetime -> MT5 'YYYY.MM.DD'."""
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y.%m.%d")
    s = str(d).strip().replace("-", ".").replace("/", ".")
    return s


def _validate_execution_delay(value):
    if type(value) is not int or not -1 <= value <= 600000:
        raise ValueError('execution_delay_ms must be -1 (random), 0 (none), or 1..600000 (fixed milliseconds)')
    return value


def build_tester_ini(expert, symbol, period="H1", from_date=None, to_date=None,
                     model="open_prices", deposit=10000, leverage=100,
                     report_path="", set_file="", optimization=0,
                     execution_delay_ms=0) -> str:
    """Return the text of a [Tester] config .ini."""
    period = _PERIOD_MAP.get(str(period).lower(), str(period).upper())
    model_code = _MODEL_MAP.get(str(model).lower(), 2)
    _validate_execution_delay(execution_delay_ms)
    if model_code == 3 and execution_delay_ms != 0:
        raise ValueError('Execution delays are not applicable to math calculations')

    lines = [
        "[Tester]",
        f"Expert={expert}",
        f"Symbol={symbol}",
        f"Period={period}",
        f"Model={model_code}",
        "Optimization=%d" % optimization,
        f"Deposit={deposit}",
        f"Leverage={leverage}",
        "Currency=USD",
        f"ExecutionMode={execution_delay_ms}",
        "ForwardMode=0",
        "UseLocal=1",
        "UseRemote=0",
        "UseCloud=0",
        "Visual=0",
        "ShutdownTerminal=1",
        "ReplaceReport=1",
    ]
    if from_date:
        lines.append(f"FromDate={_fmt_date(from_date)}")
    if to_date:
        lines.append(f"ToDate={_fmt_date(to_date)}")
    if report_path:
        lines.append(f"Report={report_path}")
    if set_file:
        lines.append(f"ExpertParameters={set_file}")
    return "\n".join(lines) + "\n"


def _to_wine_path(launcher: str, path: str) -> str:
    """When the Windows terminal is launched through a Wine launcher, MT5 needs a
    Windows-style path for /config: — a unix path like /home/.. is read against
    the terminal's C: drive and fails ("cannot load config ... at start").
    Translate via `winepath -w`; fall back to mapping the unix root onto Wine's
    Z: drive. No-op when not launching through wine (native Windows / metatester)."""
    if "wine" not in launcher:
        return path
    try:
        out = subprocess.run(["winepath", "-w", path], capture_output=True,
                             text=True, timeout=15)
        win = out.stdout.strip()
        if win:
            return win
    except (OSError, subprocess.SubprocessError):
        pass
    return "Z:" + path.replace("/", "\\")      # Wine maps the unix root to Z:\


def _launch_cmd(ini_path: str) -> list:
    terminal = _terminal_path()
    launcher = (_tester_cfg().get("launcher") or "").strip()
    if not launcher and sys.platform != "win32":
        launcher = "wine"          # sensible default off-Windows
    cmd = [terminal]
    if _tester_cfg().get("portable"):
        cmd.append("/portable")    # install keeps its data next to the exe
    # /config: must be a path the terminal can read; under Wine that's a Windows path.
    cmd.append(f"/config:{_to_wine_path(launcher, ini_path)}")
    if launcher:
        cmd = launcher.split() + cmd
    return cmd


def _newest_report(report_basename: str) -> str:
    """Locate the freshest .htm report MT5 wrote. MT5 may put it next to the
    terminal, in the data dir, or where Report= pointed; search the likely spots
    and return the most-recently-modified match (or '')."""
    base = os.path.splitext(report_basename)[0]
    name = os.path.basename(base)
    data = _data_dir()
    try:
        install_dir = os.path.dirname(_terminal_path())
    except RuntimeError:
        install_dir = ""
    candidates = []
    for d in (os.path.dirname(report_basename) or "", data, _toolkit_root(),
              os.path.join(data, "MQL5", "Files"), install_dir):
        if d:
            candidates += glob.glob(os.path.join(d, name + "*.htm"))
            candidates += glob.glob(os.path.join(d, name + "*.html"))
    candidates = [c for c in set(candidates) if os.path.isfile(c)]
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _common_files_dir() -> str:
    """MetaQuotes Common/Files dir — where an EA's OnTester() summary lands if it
    writes with FILE_COMMON. Config wins; otherwise best-effort by platform."""
    d = (_tester_cfg().get("common_files") or "").strip()
    if d:
        return d
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, "MetaQuotes", "Terminal", "Common", "Files") if appdata else ""
    # Wine default
    home = os.path.expanduser("~")
    return os.path.join(home, ".wine", "drive_c", "users", os.environ.get("USER", ""),
                        "AppData", "Roaming", "MetaQuotes", "Terminal", "Common", "Files")


def _ea_summary_path(expert: str) -> str:
    """Path to the EA's OnTester() summary CSV in the Common/Files dir."""
    name = os.path.splitext(os.path.basename(expert))[0] + "_tester.csv"
    return os.path.join(_common_files_dir(), name)


def read_ea_summary(expert: str) -> dict:
    """Read an optional EA OnTester() summary CSV (metric,value rows) from the
    Common/Files dir. Returns {} if absent. This is build-independent and the
    most reliable metrics source when the EA writes it."""
    path = _ea_summary_path(expert)
    if not os.path.isfile(path):
        return {}
    out = {"_source": path}
    with open(path, encoding="cp1252", errors="replace") as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == 2 and parts[0] != "metric":
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    out[parts[0]] = parts[1]
    return out


# label in MT5 report -> our key
_REPORT_FIELDS = {
    "Total Net Profit": "net_profit",
    "Profit Factor": "profit_factor",
    "Expected Payoff": "expected_payoff",
    "Recovery Factor": "recovery_factor",
    "Sharpe Ratio": "sharpe_ratio",
    "Total Trades": "total_trades",
    "Balance Drawdown Maximal": "balance_dd_max",
    "Equity Drawdown Maximal": "equity_dd_max",
    "Balance Drawdown Relative": "balance_dd_relative_pct",
    "Equity Drawdown Relative": "equity_dd_relative_pct",
}


def _num(s: str):
    # Treat space / NBSP / comma as thousands separators (English-locale MT5
    # reports), so "1,234.56" / "1\xa0234.56" parse as 1234.56 (not 1.0).
    s = s.strip().replace("\xa0", " ")
    m = re.search(r"-?\d[\d ,]*\.?\d*", s)
    if not m:
        return s
    try:
        return float(m.group(0).replace(" ", "").replace(",", ""))
    except ValueError:
        return s


def parse_tester_report(path: str) -> dict:
    """Parse the key metrics out of an MT5 tester .htm report. Defensive: strips
    tags to text and label-matches, so it tolerates layout differences."""
    raw = _read_text_any(path)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("\xa0", " ")                 # NBSP thousands sep -> space
    text = re.sub(r"[ \t]+", " ", text)

    out = {"_source": path}
    for label, key in _REPORT_FIELDS.items():
        m = re.search(re.escape(label) + r"\s*:?\s*([-\d ,.%()]+)", text)
        if m:
            out[key] = _num(m.group(1))
    # win rate: "Profit Trades (% of total): 117 (27.66%)"
    m = re.search(r"Profit Trades.*?\(([\d.]+)%\)", text)
    if m:
        out["win_rate_pct"] = float(m.group(1))
    return out


def _run_strategy_tester_unlocked(expert, symbol, timeframe="h1", from_date=None, to_date=None,
                                  model=None, deposit=None, leverage=None,
                                  set_file="", timeout_sec=None, execution_delay_ms=0) -> dict:
    """Run one backtest in MT5's Strategy Tester and return parsed metrics.

    expert     : EA name relative to MQL5/Experts (e.g. 'RegimePlusPro_Gold_EA'),
                 with or without .ex5.
    symbol     : broker symbol (e.g. 'XAUUSD').
    timeframe  : 1m 5m 15m 30m 1h 4h 1d 1w (or MT5 names).
    from/to    : 'YYYY-MM-DD' (omit to use the broker's full available history).
    model      : every_tick | 1min_ohlc | open_prices | math | real_ticks.
    """
    t = _tester_cfg()
    model      = model      or t.get("default_model", "open_prices")
    deposit    = deposit    or t.get("default_deposit", 10000)
    leverage   = leverage   or t.get("default_leverage", 100)
    timeout    = timeout_sec or t.get("timeout_sec", 1800)

    expert_name = expert if expert.lower().endswith(".ex5") else expert + ".ex5"
    stamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    rname   = f"{os.path.splitext(os.path.basename(expert))[0]}_{stamp}"

    # Report= must be a path the TERMINAL can write. A bare name is the portable
    # choice — MT5 writes <name>.htm into its data dir; we then copy it into
    # reports_dir. (An absolute host path fails under Wine, where the terminal
    # sees Windows paths.)
    ini_txt = build_tester_ini(expert_name, symbol, timeframe, from_date, to_date,
                               model, deposit, leverage, report_path=rname,
                               set_file=set_file, execution_delay_ms=execution_delay_ms)

    ini_path = os.path.join(reports_dir(), rname + ".ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(ini_txt)

    # CRITICAL: clear the EA's previous OnTester summary before launching, so a
    # stale CSV from an earlier run can never be read back as THIS run's result
    # (the whole validation relies on ea_summary proving the run actually ran).
    stale = _ea_summary_path(expert)
    if os.path.isfile(stale):
        try: os.remove(stale)
        except OSError: pass
        if os.path.isfile(stale):
            # Could not clear it (file lock, perms). If we proceeded and the run
            # then produced no fresh output, read_ea_summary would hand back this
            # stale CSV as the result with no error — exactly what this guard
            # exists to prevent. Fail loudly instead.
            return {"error": f"Could not clear the stale EA summary at {stale} "
                             f"(file locked?). Close any running MT5 terminal and "
                             f"retry — leaving it risks reporting the previous "
                             f"run's metrics as this run's."}

    cmd = _launch_cmd(ini_path)
    t0 = time.time()
    timed_out = False
    try:
        subprocess.run(cmd, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = round(time.time() - t0, 1)

    # Locate the .htm MT5 wrote (in the data dir), then copy it into reports_dir.
    rep_src = _newest_report(rname)
    rep_path = None
    if rep_src:
        rep_path = os.path.join(reports_dir(), rname + ".htm")
        try:
            if os.path.abspath(rep_src) != os.path.abspath(rep_path):
                shutil.copyfile(rep_src, rep_path)
        except OSError:
            rep_path = rep_src
    metrics  = parse_tester_report(rep_path) if rep_path else {}
    summary  = read_ea_summary(expert)        # EA OnTester() CSV, if it wrote one

    # An instant return with no output almost always means another terminal was
    # already running on this data dir (MT5 is single-instance — the launch just
    # forwarded to it and exited). Flag it so the cause is obvious.
    likely_instance_clash = (elapsed < 8 and not rep_path and not summary)

    result = {
        "expert": expert, "symbol": symbol, "timeframe": timeframe,
        "from": _fmt_date(from_date), "to": _fmt_date(to_date),
        "model": model, "deposit": deposit,
        "execution_delay_ms": execution_delay_ms,
        "execution_delay_mode": ('random' if execution_delay_ms == -1 else
                                 'none' if execution_delay_ms == 0 else 'fixed'),
        "ran_seconds": elapsed, "timed_out": timed_out,
        "command": " ".join(cmd),
        "ini": ini_path,
        "report_html": rep_path or None,
        "metrics": metrics,            # parsed from the .htm report
        "ea_summary": summary,         # from the EA's OnTester(), if present
    }
    if not rep_path and not summary:
        if likely_instance_clash:
            result["error"] = ("Terminal returned in %.1fs with no report — another "
                               "MT5 terminal is almost certainly already running on "
                               "this data dir (MT5 is single-instance, so the launch "
                               "just forwarded to it). Close the running terminal and "
                               "retry; the runner needs to own the terminal." % elapsed)
        else:
            result["error"] = ("No report or EA summary found. Check that the EA "
                               "compiled into MQL5/Experts, the symbol exists, and "
                               "history is available for the date range.")
    return result


def run_strategy_tester(expert, symbol, timeframe="h1", from_date=None, to_date=None,
                        model=None, deposit=None, leverage=None,
                        set_file="", timeout_sec=None, html_report=True,
                        execution_delay_ms=0) -> dict:
    """Serialize this terminal's tester runs with other MBT tester operations."""
    _validate_execution_delay(execution_delay_ms)
    with terminal_lock(_terminal_path(), _data_dir()):
        if sys.platform == "win32":
            from .optimization import _terminal_busy
            if _terminal_busy():
                raise RuntimeError("An MT5 terminal is already running; close it before MBT tester launch")
        result = _run_strategy_tester_unlocked(
            expert, symbol, timeframe, from_date, to_date, model, deposit,
            leverage, set_file, timeout_sec, execution_delay_ms,
        )
        result['native_report_html'] = result.get('report_html')
        if html_report and result.get('report_html') and not result.get('error') and not result.get('timed_out'):
            try:
                from .report_single_html import render_single_backtest_report
                result['report_html'] = render_single_backtest_report(result)
            except Exception as exc:
                result['report_error'] = str(exc)
        return result
