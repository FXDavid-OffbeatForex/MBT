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
from core.optimization import (
    optimize_ea as _optimize_ea,
    get_optimization_results as _get_optimization_results,
    render_experiment_report as _render_experiment_report,
    recover_forward_report as _recover_forward_report,
)
from core.optimization_holdout import optimize_ea_holdout as _optimize_ea_holdout
from core.optimization_holdout import continue_holdout_tests as _continue_holdout_tests
from core.optimization_equity import capture_candidate_equity as _capture_candidate_equity
from core.monte_carlo import (run_monte_carlo as _run_monte_carlo,
                             get_monte_carlo_results as _get_monte_carlo_results)
from core.ea_input_schema import inspect_ea_inputs as _inspect_ea_inputs, import_native_set as _import_native_set
from core.optimization_batch import (
    optimize_ea_symbols as _optimize_ea_symbols,
    continue_symbol_optimization as _continue_symbol_optimization,
    get_symbol_optimization_results as _get_symbol_optimization_results,
    render_symbol_optimization_report as _render_symbol_optimization_report,
)

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
                        model: str = None, deposit: float = None,
                        html_report: bool = True, execution_delay_ms: int = 0) -> dict:
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
    html_report: generate a styled standalone EA report by default. The original
                 MT5 report remains available as native_report_html. Rendering
                 failure preserves the backtest and returns report_error.
    execution_delay_ms: 0 for no delay, -1 for native MT5 random delay, or
                        1..600000 for a fixed delay in milliseconds.
                        Random-delay results are not deterministic. Math mode
                        does not support execution delays.
    """
    try:
        return _run_tester(expert, symbol, timeframe, from_date, to_date,
                           model=model, deposit=deposit, html_report=html_report,
                           execution_delay_ms=execution_delay_ms)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def start_mt5_chart_launcher() -> dict:
    """Start an opt-in, token-protected localhost bridge for HTML Open trades in MT5 buttons.

    Expires after 30 minutes. Refresh the report after starting. MT5 must be
    closed before clicking; no running terminal is terminated. The bridge opens
    display-only charts from saved MBT test deals, never live trades or backtests.
    """
    try:
        from core.chart_launcher import start_chart_launcher
        return start_chart_launcher()
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def stop_mt5_chart_launcher() -> dict:
    """Stop the local report-button bridge, leaving MT5 and open charts untouched."""
    from core.chart_launcher import stop_chart_launcher
    return stop_chart_launcher()


@mcp.tool()
def open_backtest_chart(report_path: str = None, run_id: str = None,
                        pass_id: int = None, period: str = 'in_sample') -> dict:
    """Open MT5 with saved historical deal markers, without rerunning or attaching the EA.

    Supply a native standalone report_path inside MBT reports, or optimization
    run_id with optional pass_id (defaults to the original winner) and period
    in_sample/holdout. A completed detailed candidate report is required.
    Close MT5 first. Opens a display-only chart and leaves the terminal open with
    automated trading disabled. This is an annotated broker-history chart, not
    native tester playback or exact reproduction of the tester's candle feed.
    Supports one symbol and up to 5000 saved trading deals; no guessed trade links.
    """
    try:
        from core.trade_chart import open_trade_chart
        return open_trade_chart(report_path, run_id, pass_id, period)
    except Exception as exc:
        return {'error': str(exc)}


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
def optimize_ea(expert: str, symbol: str, timeframe: str, from_date: str,
                to_date: str, parameters: dict, mode: str = "complete",
                criterion: str = "balance", forward_mode: str = "off",
                forward_date: str = None, model: str = "open_prices",
                max_combinations: int = 1000, min_trades: int = 30,
                timeout_sec: int = 1800, html_report: bool = True,
                testing: dict | None = None) -> dict:
    """Optimize a compiled EA's declared inputs in MT5, using local agents only.

    An adjacent .mq5 source is required so MBT can verify input names/types.
    Supply every input as {type, value}; numeric inputs may also have
    {start, step, stop}. Example: {"MovingPeriod": {"type": "int",
    "value": 12, "start": 10, "step": 2, "stop": 20}}. Use one run at a time.
    This is MT5 optimization, not MBT signal-replay optimization. Forward
    export is opt-in until the configured MT5 installation proves reliable.
    A verified run writes the offline HTML report by default and returns
    report_html. Set html_report=False to skip it; invalid or unverified runs
    never receive a success-looking HTML report.
    testing accepts deposit, currency, leverage (100 means 1:100), and
    execution_delay_ms (-1=random; 0=no delay; 1..600000=fixed).
    Optional testing.commission specifies per_lot (deposit currency), entry
    (both/in/out), profile_name (native Groups basename), and template_path
    (a native tester settings export inside MBT). Profiles are restored afterward.
    """
    try:
        return _optimize_ea(expert, symbol, timeframe, from_date, to_date,
                            parameters, mode, criterion, forward_mode, forward_date,
                            model, max_combinations, min_trades, timeout_sec,
                            html_report=html_report, testing=testing)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_optimization_results(run_id: str, offset: int = 0,
                             limit: int = 100) -> dict:
    """Read a saved MBT optimization, with paginated MT5 pass and forward rows."""
    try:
        return _get_optimization_results(run_id, offset, limit)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def render_experiment_report(run_id: str) -> dict:
    """Create one offline HTML report from saved MT5 optimization evidence."""
    try:
        return _render_experiment_report(run_id)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def recover_forward_report(run_id: str, html_report: bool = True) -> dict:
    """Recover a missing forward XML after a manual MT5 Forward Results export.

    Export to reports/optimization/<run_id>/<run_id>.forward.xml. MBT checks
    the saved base artifact, forward pass IDs/parameters, and known row count.
    This does not make MT5's command-line forward export reliable.
    """
    try:
        return _recover_forward_report(run_id, html_report)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def optimize_ea_holdout(expert: str, symbol: str, timeframe: str, from_date: str,
                        cutoff_date: str, to_date: str, parameters: dict,
                        mode: str = "complete", criterion: str = "balance",
                        model: str = "open_prices", max_combinations: int = 1000,
                        min_trades: int = 1, timeout_sec: int = 1800,
                        html_report: bool = True, holdout_top_n: int | None = None,
                        holdout_budget_sec: int = 600, holdout_timeout_sec: int = 120,
                        testing: dict | None = None) -> dict:
    """Optimize before a cutoff and test a frozen in-sample shortlist on later dates.

    By default, select 10% (complete) or 25% (genetic) of distinct eligible
    in-sample passes, with a minimum of 256 where available; select all when
    fewer exist. holdout_top_n explicitly overrides this selection size.
    Detailed in-sample and holdout tests for every candidate share a 600-second
    budget by default, with a 120-second limit per single test. A batch pauses
    before starting a test when its full time allowance no longer fits.
    Original selection never changes based on holdout.
    Reports include cross-period comparisons and each candidate's balance/DD
    curves for both periods, in exclusive accordions with the winner open.
    This is an independent check, distinct from MT5's native forward subset.
    """
    try:
        return _optimize_ea_holdout(
            expert, symbol, timeframe, from_date, cutoff_date, to_date,
            parameters, mode, criterion, model, max_combinations,
            min_trades, timeout_sec, html_report, holdout_top_n, holdout_budget_sec, holdout_timeout_sec,
            testing=testing,
        )
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def continue_holdout_tests(run_id: str, timeout_sec: int = 120,
                           holdout_budget_sec: int = 600, html_report: bool = True) -> dict:
    """Complete missing in-sample/holdout reports for the saved frozen candidates.

    Reuses the original optimization, selection, and completed tests. No
    reranking on out-of-sample results. Tests run sequentially and preserve
    failures separately from candidates not run yet.
    """
    try:
        return _continue_holdout_tests(run_id, timeout_sec, holdout_budget_sec, html_report)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def run_monte_carlo(run_id: str, pass_id: int | None = None, period: str = 'holdout',
                    method: str = 'shuffle', simulations: int = 1000, seed: int = 42,
                    html_report: bool = True) -> dict:
    """Resample verified closed trades, without launching MT5 or re-optimizing.

    Defaults to the original IS winner's out-of-sample detail. Choose shuffle
    (same trades, new order, constant final profit) or bootstrap (with replacement,
    varying sample/profit). Fixed recorded cash sizes/costs, NOT adaptive sizing
    or percentage-risk compounding. Requires >=5 ordinary single-position closed
    trades; rejects ambiguous/overlapping/open histories. 100..5000 paths, seed
    0..4294967295; at most 2M simulated deal events. periods in_sample/holdout are
    separate, never pooled or silently substituted. Small samples are flagged.
    Adds dark balance-band/profit/DD charts to the existing report by default.
    Historical resampling does not prevent overfitting or forecast future profits.
    """
    try:
        return _run_monte_carlo(run_id, pass_id, period, method, simulations, seed, html_report)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def get_monte_carlo_results(run_id: str, analysis_id: str) -> dict:
    """Inspect a saved Monte Carlo analysis by its returned mc_ ID.

    Rechecks analysis/source hashes, frozen candidate identity and native report
    totals. Returns method, seed, sizing assumptions, quantiles, bands/histograms
    and warnings without rerunning resampling or launching MT5.
    """
    try:
        return _get_monte_carlo_results(run_id, analysis_id)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def restore_tester_commissions() -> dict:
    """Recover an interrupted MBT commission transaction after MT5 is closed.

    Only restores the configured terminal's exact journalled prior profile;
    refuses external edits. Other launches stay blocked until recovery succeeds.
    """
    from core import optimization as opt
    from core.terminal_lock import terminal_lock
    from core.commission_profile import restore_commission_profile
    try:
        with terminal_lock(opt._terminal_path(), str(opt._data_dir()), commission_recovery=True):
            result = restore_commission_profile(opt._data_dir())
        return {'status': result['status'], 'profile_name': result['request']['profile_name']}
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def inspect_ea_inputs(expert: str) -> dict:
    """Inspect installed EA inputs/defaults/groups/fixed-only flags and enum choices.

    Resolves confined source/include files and returns their hashes; does not
    run EA code. Adjacent compiled source is required. Unresolved conditional
    inputs or runtime range overrides are rejected rather than guessed.
    """
    try:
        return _inspect_ea_inputs(expert)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def import_ea_parameters(expert: str, set_name: str) -> dict:
    """Import a native SET basename from this terminal's tester profile folder.

    Values/ranges come from SET; types and fixed-only restrictions come from
    freshly verified EA source/includes. Returns parameters for optimization,
    without launching MT5 or changing the SET.
    """
    try:
        import re
        from pathlib import Path
        from core.optimization import _data_dir
        if not isinstance(set_name,str) or not re.fullmatch(r'[A-Za-z0-9_ .-]{1,120}\.set',set_name):
            raise ValueError('set_name must be a native tester SET basename')
        root=(_data_dir()/'MQL5'/'Profiles'/'Tester').resolve()
        target=(root/set_name).resolve()
        if target.parent!=root:
            raise ValueError('SET escapes tester profile folder')
        return _import_native_set(target,_inspect_ea_inputs(expert))
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def optimize_ea_symbols(symbols: list[str], settings: dict, budget_sec: int = 600,
                        timeout_sec: int = 120, html_report: bool = True) -> dict:
    """Sequential optimization on 1..20 explicit broker markets with a saved comparison.

    settings contains optimize_ea arguments except symbol/timeout/html_report.
    Add cutoff_date for independent holdout mode. Every market has its own
    in-sample winner. Budget pauses unfinished work; no cloud or remote agents.
    """
    try:
        return _optimize_ea_symbols(symbols, settings, budget_sec, timeout_sec, html_report)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def continue_symbol_optimization(batch_id: str, budget_sec: int = 600,
                                 html_report: bool = True) -> dict:
    """Resume saved unrun markets/partial holdouts, without reranking or retrying failures."""
    try:
        return _continue_symbol_optimization(batch_id, budget_sec, html_report)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def get_symbol_optimization_results(batch_id: str) -> dict:
    """Read statuses and child run IDs for all markets in a saved batch."""
    try:
        return _get_symbol_optimization_results(batch_id)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def render_symbol_optimization_report(batch_id: str) -> dict:
    """Render an offline dark per-market comparison with individual experiment links."""
    try:
        return _render_symbol_optimization_report(batch_id)
    except Exception as exc:
        return {'error': str(exc)}


@mcp.tool()
def capture_candidate_equity(run_id: str, pass_id: int | None = None,
                             period: str = 'both', timeout_sec: int = 120,
                             html_report: bool = True) -> dict:
    """Record actual equity for one frozen candidate's IS/OOS detailed reports.

    Creates and compiles an isolated recorder copy of supported EA source,
    leaving the original untouched. Maximum two sequential single tests per
    call. timeout_sec applies separately to each compilation and backtest,
    so both periods can use up to four timeout allowances plus overhead.
    Replay must reproduce original deals/totals. Defaults to IS winner;
    specify pass_id for another frozen candidate. Sampling depends on tick
    model: open-price runs cannot reveal hidden intrabar equity. Offline HTML
    adds equity and equity-DD charts only when captured evidence is verified.
    """
    try:
        return _capture_candidate_equity(run_id, pass_id, period, timeout_sec, html_report)
    except Exception as exc:
        return {'error': str(exc)}


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
