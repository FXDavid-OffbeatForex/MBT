# MBT — MT5 Backtest Toolkit

**Backtest any MetaTrader 5 indicator by talking to Claude.**

**Requirements:** Python 3.9+ · MetaTrader 5 (Windows) · Claude Code

Drop one include into your indicator, and MBT lets you fetch live data, read
your indicator's signals, and run a full backtest with an HTML report — all
through Claude, no scripting.

The core principle: **MBT never recalculates your indicator in Python.** Your
indicator logs the signals *it* generated; MBT reads those real signals and
replays real broker price bars forward to see whether each trade hit its stop
or its target. What you backtest is exactly what your indicator drew.

---

## The problem we were solving

I use Claude to code my MQL5 indicators. To verify an indicator was calculating correctly, the natural next step was to have Claude write a Python script that checks the logic. The problem: if Claude made a mistake in the MQL5 code, there's a good chance it made the same mistake in the Python version it wrote to verify it. You're not getting an independent check — you're just confirming Claude was consistent with itself. Any bug in the original MQL5 code would still be invisible.

The solution was to make the indicator log its own decisions — every signal it fires, the exact entry, SL, TP, and context it calculated at that moment. Now you're not testing a copy. You're testing the real thing.

That's what MBT is built on. The indicator logs what it actually did. MBT reads those logs, fetches real broker price bars, and replays each trade forward — verifying the signals are generated correctly and whether the stop or the target was hit first. **What you backtest is exactly what your indicator drew — not a Python approximation of it.**

---

## How it works

```
  Your MT5 indicator                MBT (this toolkit)
  ┌──────────────────┐              ┌────────────────────────────┐
  │ #include          │   writes     │ reads signals.csv          │
  │  <SignalLogger>   ├─ signals.csv ┤                            │
  │ LogSignal(...)    │              │ replays real MT5 bars      │
  └──────────────────┘              │ forward → WIN / LOSS / OPEN │
                                     │                            │
                                     │ tools exposed to Claude:   │
                                     │  get_ohlcv                 │
                                     │  get_signals               │
                                     │  backtest  (+ HTML report) │
                                     │  validate_signals          │
                                     └────────────────────────────┘
```

---

## Install

> **New to this?** Paste the MBT repo URL into Claude Code and say:
> `"Clone this repo and set up the MBT MCP server for me"`
> Claude will clone it, run the installer, and register the server — just edit `config.yaml` when it's done.

```bash
cd MBT
python install.py
```

This installs dependencies, creates `config.yaml`, copies `SignalLogger.mqh`
into your MT5 `Include` folder, and prints the command to register the MCP
server with Claude Code.

Then edit **config.yaml**:

```yaml
mt5_path: "C:/.../terminal64.exe"   # the terminal your indicator runs on
signal_file: "signals.csv"           # must match SignalLogFile in your indicator
default_symbol: "EURUSD"
default_timeframe: "1h"
ambiguous_bar: "loss"                # conservative
```

Register the server (printed by the installer):

```bash
claude mcp add mbt python "/abs/path/to/MBT/mcp_server.py"
```

---

## Add logging to your indicator

```mql5
#include <SignalLogger.mqh>

// In OnInit (or on full recalculation) so the log matches the chart:
ResetSignalLog();

// When your buy/sell condition is true on bar `shift`:
LogSignal(shift, true,  entry, sl, tp, "TRENDING");  // BUY
LogSignal(shift, false, entry, sl, tp, "TRENDING");  // SELL
```

`regime` is optional — pass `""` if you don't use it. When present, the backtest
report breaks results down per regime.

Compile, attach to the chart. The indicator writes `signals.csv` to
`<terminal>\MQL5\Files\`.

---

## Use it

In Claude Code, inside this project:

> "backtest my signals"
> "fetch the last 300 EURUSD 1h bars"
> "validate my signal file"
> "backtest signals since 2026-01-01 and give me the report"

The `backtest` tool returns full metrics and writes an HTML report (with an
equity curve) to `reports/`.

---

## What the report contains

- Total trades · wins · losses · open
- Win rate · profit factor · expectancy (avg R)
- Net result · max drawdown (in R)
- Average win / loss · max win/loss streaks
- Per-regime breakdown
- Equity curve (cumulative R)

Everything is in **R units** (1R = the risk on each trade, entry→SL), so
results compare cleanly across symbols, timeframes, and account sizes.

---

## Signal CSV format

`SignalLogger.mqh` writes this standard header:

```
time,symbol,timeframe,direction,entry,sl,tp,regime
```

Any file matching this format works — MBT is indicator-agnostic. Extra columns
are ignored, and a header-less legacy file is still read if its first column is
a timestamp.

---

## Tools (MCP)

| Tool | Purpose |
|------|---------|
| `ping` | check MT5 is running and reachable |
| `get_ohlcv` | live OHLCV bars for any symbol/timeframe (max 2000) |
| `get_signals` | read your indicator's logged signals |
| `backtest` | replay + full metrics + HTML report (requires internet for chart) |
| `validate_signals` | check SL/TP geometry of every signal |
| `get_config` | show the active terminal + signal file |

> **Note:** the HTML report is written to `reports/` on your machine and opened in a
> browser. Claude cannot open it directly — it will give you the file path.

---

## Examples

`examples/` contains sample signal CSVs you can use to try MBT without attaching
an indicator. Point `signal_file` in `config.yaml` to any of them:

```yaml
signal_file: "C:/abs/path/to/MBT/examples/test_signals.csv"
```

---

## Research scripts

`scripts/` contains one-off analysis scripts used during strategy development.
They are not part of the core toolkit and have no documentation — treat them as
reference material, not user tools.
