"""
MBT MCP server — exposes the MT5 Backtest Toolkit to Claude.

Generic and config-driven: works for ANY indicator that logs signals with
SignalLogger.mqh. No strategy-specific logic lives here.

Register (run once, inside the MBT folder):
    claude mcp add mbt python "<abs path>/MBT/mcp_server.py"
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from banner import banner
from mcp.server.fastmcp import FastMCP

from core.connection import load_config, connect, signal_file_path
from core.ohlcv      import fetch_recent
from core.signals    import load_signals
from core.backtest   import run_backtest, report_to_dict
from core.report_html import render as render_html
from core.tester      import run_strategy_tester as _run_tester
from core.compiler    import compile_mql5 as _compile_mql5
from core.parity      import signal_parity as _signal_parity
from core.indicator_runner import run_indicator as _run_indicator

mcp = FastMCP("MBT — MT5 Backtest Toolkit")


@mcp.tool()
def get_ohlcv(symbol: str, timeframe: str, count: int = 100) -> dict:
    """
    Fetch live OHLCV bars from the configured MT5 terminal (newest first).
    timeframe: 1m 5m 15m 30m 1h 4h 1d 1w.  Generic — works for any symbol.
    count: max 2000 bars.
    """
    count = min(count, 2000)
    try:
        bars = fetch_recent(symbol, timeframe, count)
        return {"symbol": symbol, "timeframe": timeframe, "count": len(bars), "bars": bars}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_signals(since_date: str = None) -> dict:
    """
    Read the signals your indicator logged (the file named in config.yaml).
    These are the indicator's own decisions — not recalculated in Python.
    since_date: optional 'YYYY-MM-DD' filter.
    """
    try:
        sigs = load_signals()
        if since_date:
            cutoff = since_date
            sigs = [s for s in sigs if s.time.strftime("%Y-%m-%d") >= cutoff]
        return {
            "count": len(sigs),
            "file":  signal_file_path(),
            "signals": [
                {
                    "time": s.time.strftime("%Y-%m-%d %H:%M"),
                    "symbol": s.symbol, "timeframe": s.timeframe,
                    "direction": s.direction,
                    "entry": s.entry, "sl": s.sl, "tp": s.tp,
                    "regime": s.regime,
                }
                for s in sigs
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def backtest(since_date: str = None, html_report: bool = True) -> dict:
    """
    Backtest the logged signals: replay real MT5 bars forward from each entry
    to see whether SL or TP was hit first. Returns full metrics (win rate,
    profit factor, expectancy, drawdown, per-regime breakdown) in R units.

    since_date:   optional 'YYYY-MM-DD' filter on signals.
    html_report:  write a standalone HTML report to reports/ and return its path.
                  The report requires an internet connection (loads Chart.js from CDN).
                  Claude cannot open the file — share the path with the user to open in a browser.
    """
    try:
        sigs = load_signals()
        if since_date:
            sigs = [s for s in sigs if s.time.strftime("%Y-%m-%d") >= since_date]
        if not sigs:
            return {"error": "No signals to backtest (check config.yaml signal_file / since_date)."}

        rep = run_backtest(signals=sigs)
        out = report_to_dict(rep)

        if html_report:
            out["report_html"] = render_html(rep)

        return out
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def validate_signals() -> dict:
    """
    Sanity-check the signal file: count rows, flag any with bad SL/TP geometry
    (e.g. a LONG whose TP is below entry, or SL above entry). Strategy-agnostic.
    """
    try:
        sigs = load_signals()
        problems = []
        for s in sigs:
            if s.direction == "LONG" and not (s.sl < s.entry < s.tp):
                problems.append({"time": s.time.strftime("%Y-%m-%d %H:%M"),
                                 "issue": "LONG geometry: expected sl < entry < tp",
                                 "entry": s.entry, "sl": s.sl, "tp": s.tp})
            if s.direction == "SHORT" and not (s.tp < s.entry < s.sl):
                problems.append({"time": s.time.strftime("%Y-%m-%d %H:%M"),
                                 "issue": "SHORT geometry: expected tp < entry < sl",
                                 "entry": s.entry, "sl": s.sl, "tp": s.tp})
        return {
            "total": len(sigs),
            "valid": len(sigs) - len(problems),
            "problems": problems,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_config() -> dict:
    """Show the active MBT configuration (which terminal and signal file are in use).
    Note: the output includes local file paths — do not share it publicly."""
    try:
        cfg = load_config()
        return {"config": cfg, "resolved_signal_file": signal_file_path()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def run_strategy_tester(expert: str, symbol: str, timeframe: str = "1h",
                        from_date: str = None, to_date: str = None,
                        model: str = None, deposit: float = None) -> dict:
    """
    Run a REAL MT5 Strategy Tester backtest of an Expert Advisor and return the
    parsed metrics. Unlike `backtest` (which replays logged signals in Python),
    this drives MT5's own tester headlessly so the EA's real code runs bar-by-bar
    with real spread, swaps and execution.

    expert:     EA name in MQL5/Experts (e.g. 'RegimePlusPro_Gold_EA'), .ex5 optional.
    symbol:     broker symbol (e.g. 'XAUUSD').
    timeframe:  1m 5m 15m 30m 1h 4h 1d 1w.
    from_date / to_date: 'YYYY-MM-DD' (omit to use the broker's full history).
    model:      every_tick | 1min_ohlc | open_prices | math | real_ticks.
                'open_prices' is faithful and fast for bar-close EAs; use
                'every_tick'/'real_ticks' for spread/slippage-accurate numbers.
                Omit to use tester.default_model from config (ships open_prices).

    Requires tester.* configured in config.yaml (terminal path etc.). Returns the
    report path plus metrics; may take minutes for long ranges / tick models.
    """
    try:
        return _run_tester(expert, symbol, timeframe, from_date, to_date,
                           model=model, deposit=deposit)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def compile_ea(source: str) -> dict:
    """
    Compile an EA or indicator with MetaEditor and return structured results:
    {ok, errors, warnings, messages[{severity,file,line,col,code,text}], ex5}.
    The first feedback step when building/fixing an EA — works whether or not the
    terminal is running.

    source: name (e.g. 'RegimePlusPro_Gold_EA'), a path under the MQL5 tree, or an
            absolute path. '.mq5' is assumed if no extension is given.
    Requires tester.* in config.yaml (MetaEditor is found next to terminal64.exe).
    """
    try:
        return _compile_mql5(source)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def run_indicator(indicator: str, symbol: str, timeframe: str = "1h",
                  from_date: str = None, to_date: str = None,
                  signal_file: str = "signals.csv") -> dict:
    """
    Run an indicator headlessly so it logs its own signals — no chart attach.

    MT5 can't attach an indicator to a chart programmatically, so this loads the
    indicator inside MT5's Strategy Tester (via a generic host EA) and lets it
    compute bar-by-bar over the date range. The indicator's own SignalLogger.mqh
    writes the signals, which this reads back. After it returns, run `backtest`
    to evaluate those signals.

    indicator:  name under MQL5/Indicators (e.g. 'RegimePlusePro'), .ex5 optional.
    symbol:     broker symbol (e.g. 'XAUUSD').
    timeframe:  1m 5m 15m 30m 1h 4h 1d 1w.
    from_date / to_date: 'YYYY-MM-DD' (omit for the broker's full history).
    signal_file: the indicator's SignalLogFile name (default 'signals.csv').

    Requires tester.* in config.yaml, the host EA + the indicator compiled, and
    the indicator to log via SignalLogger.mqh. The indicator runs with its DEFAULT
    inputs. Returns the signal CSV path and how many signals were logged.
    """
    try:
        return _run_indicator(indicator, symbol, timeframe, from_date, to_date,
                              signal_file=signal_file)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def signal_parity(reference: str, candidate: str = None,
                  price_tol: float = 0.0) -> dict:
    """
    Mechanically diff two signal sets (same MBT CSV format) bar-by-bar and report
    the first divergence — the deterministic "does the EA match the strategy"
    check. Typical use: reference = the source indicator's (or a prototype's)
    signals, candidate = the EA's logged signals.

    reference: path to the trusted reference signal CSV.
    candidate: path to the candidate CSV; defaults to the configured signal_file.
    price_tol: max abs difference on entry/sl/tp still counted as a match.

    Returns counts (matched / mismatched / only-in-each) and the first divergence.
    """
    try:
        return _signal_parity(reference, candidate, price_tol=price_tol)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ping() -> dict:
    """Check whether the MT5 terminal is running and reachable. Use this first
    if any other tool returns an error, to confirm the connection is alive."""
    try:
        connect()
        import MetaTrader5 as mt5
        info = mt5.terminal_info()
        if info is None:
            return {"connected": False, "error": "terminal_info() returned None"}
        return {
            "connected": True,
            "terminal": info.name,
            "build": info.build,
            "trade_allowed": info.trade_allowed,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


if __name__ == "__main__":
    print(banner(stream=sys.stderr), file=sys.stderr)
    mcp.run(transport="stdio")
