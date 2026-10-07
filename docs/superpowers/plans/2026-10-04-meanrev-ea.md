# MeanRev_EA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `MeanRev_EA`, an H1 XAUUSD mean-reversion EA with two candidate entry modes (BB_FADE, RSI2_PULLBACK), run the shoot-out defined in the spec, and ship only the winning mode to run next to MACD_Cross_EA v1.42 on one account.

**Architecture:** Shared, strategy-free execution code (sizing, spread retry, loss stops, trade log, status lines, restart-safe bar tracking) is copied from MACD_Cross_EA v1.42 into `mql5/EACore.mqh`. `mql5/MeanRev_EA.mq5` holds only the two strategies. Python tooling (system python for analysis, Wine python for MT5) drives MT5's tester headlessly, checks behaviour through the EA's closed-trade CSV, and applies the spec's selection gates.

**Tech Stack:** MQL5 (MetaTrader 5 build 6230 under Wine on macOS), Python 3 (system: pytest, standard library), MBT toolkit (`core/`, `scripts/macd_tester.py`, `scripts/macd_sweep.py`), runner `~/.local/bin/mbt-wine-py`.

**Spec:** `docs/superpowers/specs/2026-10-04-meanrev-ea-design.md`

## Global Constraints

- Symbol XAUUSD, signal timeframe H1 (`InpTimeframe` = 16385), entries evaluated on the closed candle (shift 1), entry at market on the next candle.
- MeanRev_EA magic number **240819**. MACD_Cross_EA keeps 240817. Never reuse 240818.
- `mql5/MACD_Cross_EA.mq5` is not modified by any task.
- Shared guard defaults copied from v1.42: `InpMaxSpreadPoints` = 150, `InpSpreadWaitMin` = 30, `InpDailyLossPct` = 4.0, `InpMonthlyLossPct` = 12.0, `InpTradeLog` = true.
- Risk 1.0% of balance per trade (`InpRiskMode` = RISK_PERCENT, `InpRiskValue` = 1.0). One position at a time for this EA. No break-even, no trailing.
- Validation runs: 100,000 USD deposit, leverage 1:20, real ticks (`--model real_ticks`) for Stage B; 1-minute OHLC (`--model 1min_ohlc`) for Stage A grids on 2024-01-01 to 2026-10-01.
- Gates, verbatim: (1) 2022+2023 net > 0 and PF ≥ 1.15; (2) 2024–26 real-tick PF ≥ 1.20; (3) no calendar year below −10%; (4) monthly return correlation with MACD v1.42 ≤ 0.3 over 2022–26; (5) MACD + mode beats MACD alone on return-to-drawdown over 2022–26 and has a lower median calibration time.
- Stage A survivors: ≥ 150 trades, PF > 1, equity DD ≤ 20%; rank by PF × recovery factor; neighbours must average PF ≥ 1.10.
- Only numeric tester ranges with step > 0. Enums and bools (including `InpEntryMode`) are fixed per launch. Every fixed input is pinned as `v||v||1||v||N`.
- Quit the MetaTrader 5 GUI before any tester run (`osascript -e 'tell application "MetaTrader 5" to quit'`). MT5 is single-instance.
- If a Wine terminal hangs (no log lines, 0% CPU), kill it with `pkill -KILL -f terminal64.exe` and rerun that run once.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **BB_FADE take-profit on the wrong side.** If the next candle opens beyond the middle band (a gap), the target would sit on the losing side of price. Expected: the entry is skipped with a log line, never sent. Pinned in Task 4 (`tp_side_violations`) and exercised in Task 5's selfcheck.
2. **Time limit across weekends and the daily break.** `InpMaxBars` counts H1 candles, not calendar hours. A Friday position must not close at Monday's first tick just because 48 calendar hours passed. Pinned in Task 4 (`time_limit_violations` with weekend-adjusted hours), exercised in Task 5.
3. **Misspelled tester input.** A typo in `--set` would be silently ignored, and MT5 would apply its cached `.set` instead. Expected: the tester driver refuses unknown input names. Pinned in Task 1 (`test_merge_inputs_rejects_unknown`).
4. **Stale trade log.** The tester CSV in Common\Files is overwritten per run. If a run fails, the previous run's CSV must not be read as this run's. Expected: only a file modified after the run started is used. Pinned in Task 1 (`test_pick_fresh_log_*`).
5. **Degenerate data in selection.** No Stage A survivors, or months with zero variance, must not crash or produce a fake winner. Expected: `pick_plateau` returns `None`, correlation returns 0.0, the mode fails its gates. Pinned in Task 2 (`test_monthly_correlation_zero_variance`) and Task 3 (`test_pick_plateau_no_survivors`).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `scripts/ea_profiles.py` | Per-EA tester input defaults, `[TesterInputs]` line building, input-name validation, fresh trade-log detection (pure, importable without MT5) | 1 |
| `scripts/macd_tester.py` | Modified: `--expert`, uses `ea_profiles`, copies the fresh tester trade log into `reports/` | 1 |
| `scripts/macd_sweep.py` | Modified: `run(..., expert=...)` passes `--expert` | 1 |
| `tests/conftest.py` | Puts `scripts/` on `sys.path` | 1 |
| `tests/test_ea_profiles.py` | Tests for `ea_profiles` | 1 |
| `scripts/combine_eas.py` | Load closed-trade CSVs, merge two EAs on a shared balance, drawdown, worst day/month, monthly correlation, calibration days (pure + CLI) | 2 |
| `tests/test_combine_eas.py` | Tests for `combine_eas` | 2 |
| `scripts/selection.py` | Grid launch planning for non-uniform values, Stage A survivors and neighbour check, the five gates, winner choice (pure) | 3 |
| `tests/test_selection.py` | Tests for `selection` | 3 |
| `scripts/tradelog_checks.py` | Behaviour checks on an EA closed-trade CSV (pure) | 4 |
| `tests/test_tradelog_checks.py` | Tests for `tradelog_checks` | 4 |
| `scripts/meanrev_selfcheck.py` | MT5 acceptance harness for MeanRev_EA (compile, determinism, guards, time limit, TP side, exits) | 4 |
| `mql5/EACore.mqh` | Shared execution core copied from v1.42 | 5 |
| `mql5/MeanRev_EA.mq5` | The EA: inputs, BB_FADE (Task 5), RSI2_PULLBACK (Task 6) | 5, 6 |
| `scripts/install_meanrev.sh` | Copy EA + include into the MT5 Advisors folder and compile | 5 |
| `scripts/meanrev_validate.py` | Shoot-out orchestration: Stage A, Stage B, MACD reference, decision | 7 |

Test command for all Python tests: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests -q`.

---

### Task 1: EA profiles and an `--expert` option for the tester driver

**Files:**
- Create: `scripts/ea_profiles.py`
- Create: `tests/conftest.py`
- Create: `tests/test_ea_profiles.py`
- Modify: `scripts/macd_tester.py` (replace `EXPERT`/`DEFAULTS` usage in `write_ini` and `main`)
- Modify: `scripts/macd_sweep.py` (`run()` signature and command line)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ea_profiles.PROFILES: dict[str, dict[str, str]]` with keys `"MACD_Cross_EA"` and `"MeanRev_EA"`.
  - `ea_profiles.expert_path(expert: str) -> str` returns `"Advisors\\<expert>.ex5"`; raises `ValueError` for unknown experts.
  - `ea_profiles.merge_inputs(expert: str, sets: dict[str, str]) -> dict[str, str]`; raises `ValueError` on unknown input names.
  - `ea_profiles.parse_range(spec: str) -> tuple[str, tuple[str, str, str]]`; raises `ValueError` unless numeric with step > 0 and stop ≥ start.
  - `ea_profiles.tester_input_lines(inputs: dict[str, str], ranges: dict[str, tuple[str, str, str]]) -> list[str]`; raises `ValueError` if a range names an input not in `inputs`.
  - `ea_profiles.tester_log_name(expert: str, symbol: str, magic: str) -> str` returns `"<expert>_<symbol>_<magic>_tester_trades.csv"`.
  - `ea_profiles.pick_fresh_log(path: str, started_at: float) -> str | None`.
  - `macd_tester.py` CLI gains `--expert` (default `MACD_Cross_EA`). Result JSON gains `"trade_log"`: the basename of `reports/<name>_trades.csv`, or `null`.
  - `macd_sweep.run(mode, name, sets=None, ranges=None, model="1min_ohlc", frm=IS_FROM, to=IS_TO, timeout=3000, symbol="XAUUSD", expert="MACD_Cross_EA") -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
```

`tests/test_ea_profiles.py`:
```python
import os
import time

import pytest

import ea_profiles as P


def test_expert_path_known_and_unknown():
    assert P.expert_path("MeanRev_EA") == "Advisors\\MeanRev_EA.ex5"
    assert P.expert_path("MACD_Cross_EA") == "Advisors\\MACD_Cross_EA.ex5"
    with pytest.raises(ValueError):
        P.expert_path("NoSuch_EA")


def test_profiles_have_core_inputs():
    core = ["InpMagic", "InpTimeframe", "InpComment", "InpSlippagePoints", "InpMaxSpreadPoints",
            "InpRiskMode", "InpRiskValue", "InpSpreadWaitMin", "InpDailyLossPct", "InpMonthlyLossPct",
            "InpTradeLog", "InpLogEveryBar"]
    for name in ("MACD_Cross_EA", "MeanRev_EA"):
        for k in core:
            assert k in P.PROFILES[name], (name, k)
    assert P.PROFILES["MeanRev_EA"]["InpMagic"] == "240819"
    assert P.PROFILES["MACD_Cross_EA"]["InpMagic"] == "240817"


def test_merge_inputs_overrides_and_copies():
    merged = P.merge_inputs("MeanRev_EA", {"InpSLATR": "2.0"})
    assert merged["InpSLATR"] == "2.0"
    assert P.PROFILES["MeanRev_EA"]["InpSLATR"] == "1.5"   # profile untouched


def test_merge_inputs_rejects_unknown():
    with pytest.raises(ValueError, match="InpSLATRR"):
        P.merge_inputs("MeanRev_EA", {"InpSLATRR": "2.0"})


def test_parse_range_ok_and_refusals():
    assert P.parse_range("InpSLATR=1.0:0.5:2.0") == ("InpSLATR", ("1.0", "0.5", "2.0"))
    for bad in ("InpZeroLineFilter=false:0:true", "InpSLATR=1:0:2", "InpSLATR=2:1:1", "InpSLATR=1:2"):
        with pytest.raises(ValueError):
            P.parse_range(bad)


def test_tester_input_lines_pins_ranges_and_strings():
    inputs = {"InpComment": "MREV", "InpSLATR": "1.5", "InpMaxBars": "12"}
    lines = P.tester_input_lines(inputs, {"InpSLATR": ("1.0", "0.5", "2.0")})
    assert lines == ["InpComment=MREV",
                     "InpSLATR=1.5||1.0||0.5||2.0||Y",
                     "InpMaxBars=12||12||1||12||N"]


def test_tester_input_lines_rejects_range_for_unknown_input():
    with pytest.raises(ValueError):
        P.tester_input_lines({"InpSLATR": "1.5"}, {"InpNope": ("1", "1", "2")})


def test_tester_log_name():
    assert P.tester_log_name("MeanRev_EA", "XAUUSD", "240819") == "MeanRev_EA_XAUUSD_240819_tester_trades.csv"


def test_pick_fresh_log_accepts_new_file(tmp_path):
    f = tmp_path / "log.csv"
    started = time.time() - 1
    f.write_text("x")
    assert P.pick_fresh_log(str(f), started) == str(f)


def test_pick_fresh_log_rejects_stale_or_missing(tmp_path):
    f = tmp_path / "log.csv"
    f.write_text("x")
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert P.pick_fresh_log(str(f), time.time()) is None
    assert P.pick_fresh_log(str(tmp_path / "missing.csv"), 0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_ea_profiles.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ea_profiles'`.

- [ ] **Step 3: Write `scripts/ea_profiles.py`**

```python
"""Per-EA tester input defaults and [TesterInputs] line building.

Pure module (no MT5 / Wine imports) so it is importable by system python and tests.
Every EA input is written explicitly: MT5 silently applies the last GUI .set for any input left out,
and a bare "k=v" keeps an optimize flag cached from an earlier run, so fixed inputs are pinned as
v||v||1||v||N. Unknown input names are refused instead of silently ignored.
"""
import os

STRING_INPUTS = {"InpComment"}

_CORE_DEFAULTS = {
    "InpTimeframe": "16385", "InpSlippagePoints": "30", "InpMaxSpreadPoints": "150",
    "InpRiskMode": "1", "InpRiskValue": "1.0",
    "InpSpreadWaitMin": "30", "InpDailyLossPct": "4.0", "InpMonthlyLossPct": "12.0", "InpTradeLog": "true",
    "InpLogEveryBar": "false",   # hourly status lines are for live/VPS, too noisy for tester runs
}

PROFILES = {
    # MACD_Cross_EA v1.42 compiled defaults
    "MACD_Cross_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "240817", "InpComment": "MACD_X",
        "InpFastEMA": "16", "InpSlowEMA": "26", "InpSignalSMA": "9", "InpAppliedPrice": "1",
        "InpZeroLineFilter": "false",
        "InpUseTrendFilter": "true", "InpTrendTF": "16388", "InpTrendPeriod": "200",
        "InpMinGapATR": "0.0", "InpATRPeriod": "14",
        "InpUseTimeFilter": "false", "InpStartHour": "7", "InpEndHour": "20",
        "InpStopLossPct": "0.5", "InpTakeProfitPct": "3.75", "InpCloseOnOpposite": "false",
        "InpBreakEvenPct": "0.5", "InpBreakEvenLockPct": "0.1",
        "InpTrailStartPct": "1.0", "InpTrailDistPct": "1.0", "InpTrailATRMult": "4.0",
    }),
    # MeanRev_EA v1.00 compiled defaults (pre-tuning; Task 8 replaces the strategy values with the winner's)
    "MeanRev_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "240819", "InpComment": "MREV",
        "InpEntryMode": "0", "InpATRPeriod": "14", "InpSLATR": "1.5", "InpMaxBars": "12",
        "InpBBPeriod": "20", "InpBBDev": "2.0", "InpRSIPeriod": "14", "InpRSILow": "30",
        "InpADXPeriod": "14", "InpADXMax": "20",
        "InpRSI2Period": "2", "InpRSI2Entry": "10", "InpRSI2Exit": "70", "InpTrendPeriod": "200",
    }),
}


def expert_path(expert):
    if expert not in PROFILES:
        raise ValueError(f"unknown expert {expert!r}; known: {sorted(PROFILES)}")
    return f"Advisors\\{expert}.ex5"


def merge_inputs(expert, sets):
    expert_path(expert)
    base = dict(PROFILES[expert])
    unknown = sorted(k for k in sets if k not in base)
    if unknown:
        raise ValueError(f"unknown input(s) for {expert}: {', '.join(unknown)}")
    base.update(sets)
    return base


def parse_range(spec):
    try:
        name, value = spec.split("=", 1)
        start, step, stop = value.split(":")
        ok = float(step) > 0 and float(stop) >= float(start)
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"refusing range {spec!r}: numeric start:step:stop with step > 0 only; "
                         f"sweep bools/enums as separate launches")
    return name.strip(), (start, step, stop)


def tester_input_lines(inputs, ranges):
    unknown = sorted(k for k in ranges if k not in inputs)
    if unknown:
        raise ValueError(f"range for unknown input(s): {', '.join(unknown)}")
    lines = []
    for k, v in inputs.items():
        if k in ranges:
            a, s, b = ranges[k]
            lines.append(f"{k}={v}||{a}||{s}||{b}||Y")
        elif k in STRING_INPUTS:
            lines.append(f"{k}={v}")
        else:
            lines.append(f"{k}={v}||{v}||1||{v}||N")
    return lines


def tester_log_name(expert, symbol, magic):
    return f"{expert}_{symbol}_{magic}_tester_trades.csv"


def pick_fresh_log(path, started_at):
    if os.path.isfile(path) and os.path.getmtime(path) >= started_at:
        return path
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_ea_profiles.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Switch `scripts/macd_tester.py` to `ea_profiles`**

Make these edits.

1. Below the line `from core.connection import reports_dir`, add:
```python
from core.connection import load_config
import ea_profiles
```
Also add `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` directly after the existing `sys.path.insert(0, ROOT)` line, so `ea_profiles` imports under Wine python.

2. Delete the `EXPERT = r"Advisors\MACD_Cross_EA.ex5"` line and the whole `DEFAULTS = {...}` block, including its comment lines.

3. In `write_ini`, replace `f"Expert={EXPERT}"` with `f"Expert={ea_profiles.expert_path(args.expert)}"`. Then replace everything from `lines.append("[TesterInputs]")` down to (not including) `ini_path = ...` with:
```python
    lines.append("[TesterInputs]")
    lines += ea_profiles.tester_input_lines(inputs, ranges)
```

4. In `main()`, add the argument after `--out`:
```python
    ap.add_argument("--expert", default="MACD_Cross_EA", help="EA name under MQL5\\Experts\\Advisors")
```
Replace the block from `inputs = dict(DEFAULTS)` through the end of the `for s in args.range:` loop with:
```python
    sets = {}
    for s in args.set:
        k, v = s.split("=", 1)
        sets[k.strip()] = v.strip()
    ranges = {}
    try:
        inputs = ea_profiles.merge_inputs(args.expert, sets)
        for s in args.range:
            k, r = ea_profiles.parse_range(s)
            ranges[k] = r
    except ValueError as e:
        sys.exit(str(e))
```

5. Add `"expert": args.expert,` to the `result = {...}` dict. Directly after the `if not found:` block, add:
```python
    common = (load_config().get("tester") or {}).get("common_files", "")
    src = os.path.join(common, ea_profiles.tester_log_name(args.expert, args.symbol, inputs["InpMagic"]))
    fresh = ea_profiles.pick_fresh_log(src, t0) if common else None
    result["trade_log"] = None
    if fresh:
        dst_name = name + "_trades.csv"
        shutil.copyfile(fresh, os.path.join(reports_dir(), dst_name))
        result["trade_log"] = dst_name
```

- [ ] **Step 6: Add `expert` to `macd_sweep.run`**

In `scripts/macd_sweep.py`, change the signature line to:
```python
def run(mode, name, sets=None, ranges=None, model="1min_ohlc", frm=IS_FROM, to=IS_TO, timeout=3000, symbol="XAUUSD",
        expert="MACD_Cross_EA"):
```
and change the line ending `"--symbol", symbol]` to:
```python
           "--from", frm, "--to", to, "--timeout", str(timeout), "--symbol", symbol, "--expert", expert]
```

- [ ] **Step 7: Verify MACD runs are unchanged (regression)**

Quit the MT5 GUI first. Run:
```bash
cd /Users/lyudmilnikodimov/Projects/MBT && ~/.local/bin/mbt-wine-py scripts/macd_tester.py single --name t1_regress \
  --model real_ticks --from 2023-01-01 --to 2024-01-01 \
  --set InpMaxSpreadPoints=0 --set InpDailyLossPct=0 --set InpMonthlyLossPct=0 2>/dev/null | python3 -c \
  "import sys,json; r=sys.stdin.read(); d=json.loads(r[r.find('{'):]); print(d['metrics']['net_profit'], d['trade_log'])"
```
Expected: `31165.91 t1_regress_<stamp>_trades.csv`. This is v1.40's 2023 result to the cent, and a trade log was copied.

Also run `~/.local/bin/mbt-wine-py scripts/macd_tester.py single --name t1_typo --set InpSLATRR=1` and expect it to exit with `unknown input(s) for MACD_Cross_EA: InpSLATRR`.

- [ ] **Step 8: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add scripts/ea_profiles.py scripts/macd_tester.py scripts/macd_sweep.py tests/conftest.py tests/test_ea_profiles.py
git commit -m "feat: per-EA tester profiles, --expert option and fresh trade-log capture

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `combine_eas.py`, one account view of two EAs

**Files:**
- Create: `scripts/combine_eas.py`
- Create: `tests/test_combine_eas.py`

**Interfaces:**
- Consumes: closed-trade CSVs with the RiskGuard/EACore header `close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic`. Times are formatted `YYYY.MM.DD HH:MM:SS`, and `balance` is the balance after the close.
- Produces:
  - `combine_eas.Trade` (dataclass: `open_time`, `close_time`, `symbol`, `volume`, `open_price`, `profit`, `balance_after`; property `ret` = profit ÷ balance before the close).
  - `load_trades(path: str) -> list[Trade]`.
  - `merged_returns(*lists: list[Trade]) -> list[tuple[datetime, float]]`, sorted by close time.
  - `equity_curve(rets) -> list[tuple[datetime, float]]`, starting at 1.0.
  - `max_drawdown_pct(curve) -> float` (positive number, percent).
  - `period_returns(rets, fmt: str) -> dict[str, float]`, compounded, in percent, keyed by `strftime(fmt)`.
  - `monthly_correlation(a: list[Trade], b: list[Trade]) -> float`, Pearson over the union of months with missing months as 0. Returns 0.0 if either side has zero variance.
  - `calibration_days(trades: list[Trade], contract: dict[str, float] = CONTRACT) -> list[int]`.
  - `summarize(*lists: list[Trade]) -> dict` with keys `net_return_pct`, `max_dd_pct`, `ret_dd`, `worst_day_pct`, `worst_month_pct`, `calib_median_days`, `trades`.

- [ ] **Step 1: Write the failing tests**

`tests/test_combine_eas.py`:
```python
import datetime as dt

import pytest

import combine_eas as C

T0 = dt.datetime(2024, 1, 1, 10, 0, 0)


def tr(day, profit, balance_after, hours=2.0, volume=1.0, price=2000.0):
    o = T0 + dt.timedelta(days=day)
    return C.Trade(open_time=o, close_time=o + dt.timedelta(hours=hours), symbol="XAUUSD",
                   volume=volume, open_price=price, profit=profit, balance_after=balance_after)


def test_trade_return_uses_balance_before_close():
    assert tr(0, 1000.0, 101000.0).ret == pytest.approx(0.01)


def test_load_trades_parses_csv(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
                 "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"
                 "2024.01.03 11:44:49,2024.01.03 06:00:00,XAUUSD,buy,0.96,2065.22,2054.89,2054.89,2142.67,"
                 "-991.68,-9.89,0.00,-1001.57,98998.43,-1.010,sl,12,0.0,3,2,240817\n")
    (t,) = C.load_trades(str(f))
    assert t.open_time == dt.datetime(2024, 1, 3, 6, 0, 0)
    assert t.profit == pytest.approx(-1001.57)
    assert t.balance_after == pytest.approx(98998.43)


def test_merged_curve_and_drawdown():
    a = [tr(0, 1000.0, 101000.0), tr(2, -2020.0, 98980.0)]   # +1%, then -2%
    b = [tr(1, 1000.0, 101000.0)]                            # +1%
    rets = C.merged_returns(a, b)
    assert [round(r, 4) for _, r in rets] == [0.01, 0.01, -0.02]
    curve = C.equity_curve(rets)
    assert curve[-1][1] == pytest.approx(1.01 * 1.01 * 0.98)
    assert C.max_drawdown_pct(curve) == pytest.approx(2.0)


def test_period_returns_compound():
    rets = [(T0, 0.10), (T0 + dt.timedelta(days=1), 0.10), (T0 + dt.timedelta(days=40), -0.05)]
    m = C.period_returns(rets, "%Y-%m")
    assert m["2024-01"] == pytest.approx(21.0)
    assert m["2024-02"] == pytest.approx(-5.0)


def test_monthly_correlation_identical_and_opposite():
    a = [tr(0, 1000.0, 101000.0), tr(35, -1000.0, 100000.0), tr(70, 2000.0, 102000.0)]
    neg = [tr(0, -1000.0, 99000.0), tr(35, 1000.0, 100000.0), tr(70, -2000.0, 98000.0)]
    assert C.monthly_correlation(a, a) == pytest.approx(1.0)
    assert C.monthly_correlation(a, neg) == pytest.approx(-1.0, abs=0.02)


def test_monthly_correlation_zero_variance():
    a = [tr(0, 1000.0, 101000.0), tr(35, 1000.0, 102000.0)]
    flat = []
    assert C.monthly_correlation(a, flat) == 0.0


def test_calibration_days_reaches_25_decisions():
    trades = [tr(d, 0.0, 100000.0) for d in range(40)]   # identical exposure: each trade weighs exactly 1
    days = C.calibration_days(trades)
    assert days == [24]                                   # 25th trade opens on day 24 of January
    assert C.calibration_days(trades[:10]) == []


def test_summarize_keys_and_values():
    a = [tr(0, 1000.0, 101000.0), tr(2, -2020.0, 98980.0)]
    s = C.summarize(a)
    assert set(s) == {"net_return_pct", "max_dd_pct", "ret_dd", "worst_day_pct", "worst_month_pct",
                      "calib_median_days", "trades"}
    assert s["trades"] == 2
    assert s["worst_day_pct"] == pytest.approx(-2.0)
    assert s["calib_median_days"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_combine_eas.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'combine_eas'`.

- [ ] **Step 3: Write `scripts/combine_eas.py`**

```python
#!/usr/bin/env python3
"""Combine EA closed-trade CSV logs (RiskGuard.mqh / EACore.mqh columns) into one account view.

Each EA was backtested on its own 100k balance. A trade's effect is its return on the balance before
it closed. Merging those returns by close time on one shared balance models equal-weight risk (each
EA keeps risking the same % of the shared balance). Floating P&L, shared-guard interaction and
cross-EA compounding of position size are ignored -- the spec documents this approximation.

  python3 scripts/combine_eas.py reports/macd_trades.csv reports/meanrev_trades.csv
"""
import csv
import datetime as dt
import json
import math
import statistics
import sys
from dataclasses import dataclass

TIME_FMT = "%Y.%m.%d %H:%M:%S"
CONTRACT = {"XAUUSD": 100.0}
CALIB_DECISIONS = 25
CALIB_MIN_DAYS = 15


@dataclass(frozen=True)
class Trade:
    open_time: dt.datetime
    close_time: dt.datetime
    symbol: str
    volume: float
    open_price: float
    profit: float
    balance_after: float

    @property
    def balance_before(self):
        return self.balance_after - self.profit

    @property
    def ret(self):
        return self.profit / self.balance_before if self.balance_before > 0 else 0.0


def load_trades(path):
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Trade(open_time=dt.datetime.strptime(row["open_time"], TIME_FMT),
                             close_time=dt.datetime.strptime(row["close_time"], TIME_FMT),
                             symbol=row["symbol"], volume=float(row["volume"]),
                             open_price=float(row["open_price"]), profit=float(row["profit"]),
                             balance_after=float(row["balance"])))
    return out


def merged_returns(*lists):
    rets = [(t.close_time, t.ret) for lst in lists for t in lst]
    return sorted(rets, key=lambda x: x[0])


def equity_curve(rets):
    eq, curve = 1.0, []
    for when, r in rets:
        eq *= 1.0 + r
        curve.append((when, eq))
    return curve


def max_drawdown_pct(curve):
    peak, worst = 1.0, 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        worst = max(worst, 1.0 - eq / peak)
    return 100.0 * worst


def period_returns(rets, fmt):
    acc = {}
    for when, r in rets:
        key = when.strftime(fmt)
        acc[key] = acc.get(key, 1.0) * (1.0 + r)
    return {k: 100.0 * (v - 1.0) for k, v in acc.items()}


def monthly_correlation(a, b):
    ma = period_returns(merged_returns(a), "%Y-%m")
    mb = period_returns(merged_returns(b), "%Y-%m")
    months = sorted(set(ma) | set(mb))
    xa = [ma.get(m, 0.0) for m in months]
    xb = [mb.get(m, 0.0) for m in months]
    if len(months) < 2 or statistics.pstdev(xa) == 0.0 or statistics.pstdev(xb) == 0.0:
        return 0.0
    return statistics.correlation(xa, xb)


def _exposure(t, contract):
    hours = max((t.close_time - t.open_time).total_seconds() / 3600.0, 0.1)
    lev = t.volume * contract.get(t.symbol, 1.0) * t.open_price / max(t.balance_before, 1.0)
    return lev * math.sqrt(hours)


def calibration_days(trades, contract=CONTRACT):
    """Days from each month start until 25 risk-equivalent decisions over >= 15 trading days.

    Darwinex weighting: exposure = leverage x sqrt(hours held); a trade counts
    sqrt(exposure / largest exposure so far). Months that never finish are omitted.
    """
    trades = sorted(trades, key=lambda t: t.open_time)
    starts = sorted({dt.datetime(t.open_time.year, t.open_time.month, 1) for t in trades})
    out = []
    for start in starts:
        sub = [t for t in trades if t.open_time >= start]
        exps, days, mx = [], set(), 0.0
        for t in sub:
            e = _exposure(t, contract)
            exps.append(e)
            mx = max(mx, e)
            days.update({t.open_time.date(), t.close_time.date()})
            decisions = sum(math.sqrt(x / mx) for x in exps) if mx > 0 else 0.0
            if decisions >= CALIB_DECISIONS and len(days) >= CALIB_MIN_DAYS:
                out.append((t.close_time - start).days)
                break
    return out


def summarize(*lists):
    rets = merged_returns(*lists)
    curve = equity_curve(rets)
    net = 100.0 * (curve[-1][1] - 1.0) if curve else 0.0
    dd = max_drawdown_pct(curve)
    days = period_returns(rets, "%Y-%m-%d")
    months = period_returns(rets, "%Y-%m")
    calib = calibration_days([t for lst in lists for t in lst])
    return {"net_return_pct": net, "max_dd_pct": dd, "ret_dd": (net / dd) if dd > 0 else float("inf"),
            "worst_day_pct": min(days.values()) if days else 0.0,
            "worst_month_pct": min(months.values()) if months else 0.0,
            "calib_median_days": statistics.median(calib) if calib else None,
            "trades": len(rets)}


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: combine_eas.py <ea_a_trades.csv> <ea_b_trades.csv>")
    a, b = load_trades(argv[1]), load_trades(argv[2])
    out = {"a_alone": summarize(a), "b_alone": summarize(b), "combined": summarize(a, b),
           "monthly_correlation": monthly_correlation(a, b)}
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_combine_eas.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Cross-check calibration against the earlier MACD estimate**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -c "import sys; sys.path.insert(0,'scripts'); import combine_eas as C, glob; f=sorted(glob.glob('reports/t1_regress_*_trades.csv'))[-1]; print(C.summarize(C.load_trades(f)))"`
Expected: `trades` is 135, and `calib_median_days` is a number between 85 and 130. The earlier analysis of the 2023 v1.40 trades gave 87 to 126.

- [ ] **Step 6: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add scripts/combine_eas.py tests/test_combine_eas.py
git commit -m "feat: combine_eas.py merges two EAs' trade logs into one account view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `selection.py`, grid launches, plateau pick, gates, winner

**Files:**
- Create: `scripts/selection.py`
- Create: `tests/test_selection.py`

**Interfaces:**
- Consumes: optimization pass dicts as parsed by `macd_tester.parse_opt_xml` (keys `"Profit"`, `"Profit Factor"`, `"Recovery Factor"`, `"Equity DD %"`, `"Trades"`, plus each optimized input name). Single-run metrics as parsed by `macd_tester.parse_htm` (keys `net_profit`, `gross_profit`, `gross_loss`, `profit_factor`, `trades`). `combine_eas.summarize` dicts.
- Produces:
  - `split_uniform(values: list[str]) -> list[str]`: segments, each `"a:s:b"` for an evenly spaced run of two or more, or a single value.
  - `plan_launches(grid: dict[str, list[str]]) -> list[tuple[dict[str, str], dict[str, str]]]`: `(fixed_sets, ranges)` per launch, where ranges are `"a:s:b"` strings for `--range`.
  - `survivors(passes, min_trades=150, max_dd=20.0) -> list[dict]`.
  - `score(p: dict) -> float` = PF × RF.
  - `neighbours(p: dict, passes: list[dict], grid: dict[str, list[str]]) -> list[dict]`.
  - `pick_plateau(passes, grid, min_trades=150, max_dd=20.0, min_neighbour_pf=1.10) -> dict | None`.
  - `evaluate_gates(years: dict[str, dict], in_sample: dict, corr: float, combined: dict, macd_alone: dict, deposit=100000.0) -> dict[str, tuple[bool, str]]`, with keys `"1_oos"`, `"2_in_sample"`, `"3_no_disaster_year"`, `"4_correlation"`, `"5_better_together"`.
  - `choose_winner(results: dict[str, dict]) -> str | None`. Each result has `"gates"` (as above), `"corr"` (float) and `"combined"` (summarize dict).

- [ ] **Step 1: Write the failing tests**

`tests/test_selection.py`:
```python
import itertools

import selection as S


def test_split_uniform():
    assert S.split_uniform(["25", "30", "50"]) == ["25:5:30", "50"]
    assert S.split_uniform(["15", "20", "25", "100"]) == ["15:5:25", "100"]
    assert S.split_uniform(["1.0", "1.5", "2.0", "3.0"]) == ["1.0:0.5:2.0", "3.0"]
    assert S.split_uniform(["1.5", "2.0", "3.0", "4.0"]) == ["1.5:0.5:2.0", "3.0:1.0:4.0"]
    assert S.split_uniform(["6", "12", "24"]) == ["6:6:12", "24"]
    assert S.split_uniform(["2.0", "2.5"]) == ["2.0:0.5:2.5"]
    assert S.split_uniform(["200"]) == ["200"]


def test_plan_launches_covers_grid_exactly():
    grid = {"A": ["1", "2", "4"], "B": ["10", "20"]}
    launches = S.plan_launches(grid)
    covered = set()
    for fixed, ranges in launches:
        axes = []
        for k in grid:
            if k in fixed:
                axes.append([float(fixed[k])])
            else:
                a, s, b = (float(x) for x in ranges[k].split(":"))
                n = int(round((b - a) / s)) + 1
                axes.append([a + i * s for i in range(n)])
        covered |= set(itertools.product(*axes))
    want = set(itertools.product([1.0, 2.0, 4.0], [10.0, 20.0]))
    assert covered == want


def p(pf, rf, trades=200, dd=10.0, **params):
    d = {"Profit Factor": pf, "Recovery Factor": rf, "Trades": trades, "Equity DD %": dd, "Profit": 1.0}
    d.update(params)
    return d


GRID = {"X": ["1", "2", "3"], "Y": ["10", "20", "30"]}


def full_grid(pf_of):
    return [p(pf_of(x, y), 2.0, X=float(x), Y=float(y)) for x in (1, 2, 3) for y in (10, 20, 30)]


def test_survivors_filters():
    ps = [p(1.5, 2, trades=100), p(1.5, 2, dd=25), p(0.9, 2), p(1.2, 2)]
    assert S.survivors(ps) == [ps[3]]


def test_neighbours_one_step_only():
    passes = full_grid(lambda x, y: 1.2)
    centre = next(q for q in passes if q["X"] == 2.0 and q["Y"] == 20.0)
    nb = S.neighbours(centre, passes, GRID)
    assert sorted((q["X"], q["Y"]) for q in nb) == [(1.0, 20.0), (2.0, 10.0), (2.0, 30.0), (3.0, 20.0)]


def test_pick_plateau_prefers_broad_region_over_spike():
    def pf(x, y):
        if (x, y) == (3, 30):
            return 3.0          # isolated spike: neighbours (2,30)=1.15, (3,20)=0.9 average 1.025 < 1.10
        return 1.15 if x <= 2 else 0.9
    pick = S.pick_plateau(full_grid(pf), GRID)
    assert pick is not None
    assert (pick["X"], pick["Y"]) != (3.0, 30.0)
    assert pick["X"] <= 2.0


def test_pick_plateau_no_survivors():
    assert S.pick_plateau(full_grid(lambda x, y: 0.9), GRID) is None
    assert S.pick_plateau([], GRID) is None


def year(net, gp, gl):
    return {"net_profit": net, "gross_profit": gp, "gross_loss": gl, "profit_factor": gp / -gl, "trades": 100}


def good_inputs():
    years = {"2022": year(5000, 30000, -25000), "2023": year(6000, 30000, -24000),
             "2024": year(8000, 40000, -32000), "2025": year(9000, 40000, -31000), "2026": year(4000, 20000, -16000)}
    in_sample = {"profit_factor": 1.25}
    combined = {"ret_dd": 3.0, "calib_median_days": 60}
    macd = {"ret_dd": 2.5, "calib_median_days": 100}
    return years, in_sample, 0.1, combined, macd


def test_gates_all_pass():
    g = S.evaluate_gates(*good_inputs())
    assert all(ok for ok, _ in g.values()), g


def test_gates_each_failure():
    years, ins, corr, comb, macd = good_inputs()
    years["2022"] = year(-20000, 10000, -30000)
    g = S.evaluate_gates(years, ins, corr, comb, macd)
    assert not g["1_oos"][0] and not g["3_no_disaster_year"][0]
    g = S.evaluate_gates(*good_inputs()[:1], {"profit_factor": 1.1}, 0.1, comb, macd)
    assert not g["2_in_sample"][0]
    g = S.evaluate_gates(years, ins, 0.45, comb, macd)
    assert not g["4_correlation"][0]
    g = S.evaluate_gates(years, ins, corr, {"ret_dd": 2.0, "calib_median_days": 60}, macd)
    assert not g["5_better_together"][0]
    g = S.evaluate_gates(years, ins, corr, {"ret_dd": 3.0, "calib_median_days": None}, macd)
    assert not g["5_better_together"][0]


def test_choose_winner():
    passing = {k: (True, "") for k in ("1_oos", "2_in_sample", "3_no_disaster_year", "4_correlation",
                                       "5_better_together")}
    failing = dict(passing, **{"1_oos": (False, "x")})
    res = {"bb": {"gates": passing, "corr": 0.20, "combined": {"ret_dd": 3.0}},
           "rsi2": {"gates": passing, "corr": 0.05, "combined": {"ret_dd": 2.0}}}
    assert S.choose_winner(res) == "rsi2"
    res["rsi2"]["corr"] = 0.20
    assert S.choose_winner(res) == "bb"
    res["bb"]["gates"] = failing
    assert S.choose_winner(res) == "rsi2"
    res["rsi2"]["gates"] = failing
    assert S.choose_winner(res) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_selection.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'selection'`.

- [ ] **Step 3: Write `scripts/selection.py`**

```python
"""Shoot-out selection rules for MeanRev_EA (spec section 4). Pure functions, no MT5."""
import itertools

GATE_KEYS = ("1_oos", "2_in_sample", "3_no_disaster_year", "4_correlation", "5_better_together")


def split_uniform(values):
    """Cover a list of grid values with evenly spaced runs (MT5 ranges) plus single values."""
    vals = [float(v) for v in values]
    raw = list(values)
    segs, i = [], 0
    while i < len(vals):
        j = i + 1
        if j < len(vals):
            step = vals[j] - vals[i]
            while j + 1 < len(vals) and abs((vals[j + 1] - vals[j]) - step) < 1e-9:
                j += 1
            segs.append(f"{raw[i]}:{_step_str(raw[i], raw[i + 1], step)}:{raw[j]}")
            i = j + 1
        else:
            segs.append(raw[i])
            i += 1
    return segs


def _step_str(a, b, step):
    decimals = max(len(a.split(".")[1]) if "." in a else 0, len(b.split(".")[1]) if "." in b else 0)
    return f"{step:.{decimals}f}"


def plan_launches(grid):
    """(fixed_sets, ranges) per tester launch so that all launches together cover the grid exactly."""
    per_param = []
    for name, values in grid.items():
        options = []
        for seg in split_uniform(values):
            options.append((name, seg))
        per_param.append(options)
    launches = []
    for combo in itertools.product(*per_param):
        fixed, ranges = {}, {}
        for name, seg in combo:
            if ":" in seg:
                ranges[name] = seg
            else:
                fixed[name] = seg
        launches.append((fixed, ranges))
    return launches


def score(p):
    return float(p.get("Profit Factor") or 0.0) * float(p.get("Recovery Factor") or 0.0)


def survivors(passes, min_trades=150, max_dd=20.0):
    return [p for p in passes
            if float(p.get("Trades") or 0) >= min_trades
            and float(p.get("Equity DD %") or 999.0) <= max_dd
            and float(p.get("Profit Factor") or 0.0) > 1.0]


def neighbours(p, passes, grid):
    axes = {k: sorted(float(v) for v in vals) for k, vals in grid.items()}
    out = []
    for k, vals in axes.items():
        idx = vals.index(float(p[k]))
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(vals):
                want = dict((q, float(p[q])) for q in axes)
                want[k] = vals[j]
                for cand in passes:
                    if all(abs(float(cand[q]) - want[q]) < 1e-9 for q in axes):
                        out.append(cand)
                        break
    return out


def pick_plateau(passes, grid, min_trades=150, max_dd=20.0, min_neighbour_pf=1.10):
    ranked = sorted(survivors(passes, min_trades, max_dd), key=score, reverse=True)
    for cand in ranked:
        nb = neighbours(cand, passes, grid)
        if nb and sum(float(q.get("Profit Factor") or 0.0) for q in nb) / len(nb) >= min_neighbour_pf:
            return cand
    return None


def evaluate_gates(years, in_sample, corr, combined, macd_alone, deposit=100000.0):
    oos = [years["2022"], years["2023"]]
    oos_net = sum(y["net_profit"] for y in oos)
    oos_gl = sum(-y["gross_loss"] for y in oos)
    oos_pf = (sum(y["gross_profit"] for y in oos) / oos_gl) if oos_gl > 0 else 0.0
    worst_year = min(y["net_profit"] / deposit for y in years.values())
    ins_pf = float(in_sample.get("profit_factor") or 0.0)
    calib_c, calib_m = combined.get("calib_median_days"), macd_alone.get("calib_median_days")
    better = (combined["ret_dd"] > macd_alone["ret_dd"] and calib_c is not None
              and (calib_m is None or calib_c < calib_m))
    return {
        "1_oos": (oos_net > 0 and oos_pf >= 1.15, f"2022+2023 net {oos_net:.0f}, PF {oos_pf:.2f}"),
        "2_in_sample": (ins_pf >= 1.20, f"2024-26 PF {ins_pf:.2f}"),
        "3_no_disaster_year": (worst_year >= -0.10, f"worst year {100 * worst_year:.1f}%"),
        "4_correlation": (corr <= 0.3, f"monthly correlation with MACD {corr:.2f}"),
        "5_better_together": (better, f"ret/DD {combined['ret_dd']:.2f} vs MACD {macd_alone['ret_dd']:.2f}; "
                                      f"calibration {calib_c} vs {calib_m} days"),
    }


def choose_winner(results):
    passed = [m for m, r in results.items() if all(r["gates"][k][0] for k in GATE_KEYS)]
    if not passed:
        return None
    return sorted(passed, key=lambda m: (results[m]["corr"], -results[m]["combined"]["ret_dd"]))[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_selection.py -q`
Expected: `9 passed`. If `test_split_uniform` fails on number formatting, adjust only `_step_str` so the strings match the test exactly. Keep the input strings untouched.

- [ ] **Step 5: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add scripts/selection.py tests/test_selection.py
git commit -m "feat: selection.py with grid launch planning, plateau pick, gates and winner rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Trade-log behaviour checks and the MT5 acceptance harness

**Files:**
- Create: `scripts/tradelog_checks.py`
- Create: `tests/test_tradelog_checks.py`
- Create: `scripts/meanrev_selfcheck.py`

**Interfaces:**
- Consumes: the closed-trade CSV header from Task 2; `macd_sweep.run(..., expert=...)` from Task 1 (result has `"name"`, `"metrics"`, `"trade_log"`).
- Produces:
  - `tradelog_checks.load_rows(path: str) -> list[dict]` with keys `open_time`, `close_time` (datetime), `side` (str), `volume`, `open_price`, `close_price`, `sl`, `tp`, `profit`, `balance` (float), `r_multiple` (float or None), `exit_reason` (str), `spread_pts` (int or None).
  - `trading_hours(open_t, close_t) -> float`: calendar hours minus 48 per Saturday crossed.
  - `tp_side_violations(rows) -> list[dict]`.
  - `time_limit_violations(rows, max_bars: int, slack: float = 2.5) -> list[dict]`.
  - `monthly_stop_check(rows, pct: float) -> tuple[int, list[dict]]`: (breaches, violations).
  - `count_reason(rows, reason: str) -> int`, `max_spread(rows) -> int`, `median_sl_r(rows) -> float | None`.
  - `scripts/meanrev_selfcheck.py --mode bb|rsi2` exits 0 only if every check passes.

- [ ] **Step 1: Write the failing tests**

`tests/test_tradelog_checks.py`:
```python
import datetime as dt

import pytest

import tradelog_checks as K

HDR = ("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
       "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n")


def row(open_t, close_t, side="buy", op=2000.0, tp=2010.0, profit=-100.0, balance=99900.0, r="-1.0",
        reason="sl", spread="30"):
    return (f"{close_t},{open_t},XAUUSD,{side},1.00,{op},{op},0,{tp},0,0,0,{profit},{balance},{r},{reason},"
            f"{spread},0.0,1,1,240819\n")


def write(tmp_path, *rows):
    f = tmp_path / "t.csv"
    f.write_text(HDR + "".join(rows))
    return str(f)


def test_load_rows_types(tmp_path):
    (r,) = K.load_rows(write(tmp_path, row("2024.01.03 10:00:00", "2024.01.03 12:00:00", r="")))
    assert r["open_time"] == dt.datetime(2024, 1, 3, 10)
    assert r["r_multiple"] is None
    assert r["spread_pts"] == 30


def test_trading_hours_subtracts_weekend():
    fri = dt.datetime(2024, 1, 5, 22)          # Friday
    mon = dt.datetime(2024, 1, 8, 2)           # Monday
    assert K.trading_hours(fri, mon) == pytest.approx(4.0)
    assert K.trading_hours(dt.datetime(2024, 1, 3, 10), dt.datetime(2024, 1, 3, 13)) == pytest.approx(3.0)


def test_tp_side_violations(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", side="buy", op=2000, tp=2010),
                             row("2024.01.03 13:00:00", "2024.01.03 14:00:00", side="buy", op=2000, tp=1990),
                             row("2024.01.03 15:00:00", "2024.01.03 16:00:00", side="sell", op=2000, tp=1990),
                             row("2024.01.03 17:00:00", "2024.01.03 18:00:00", side="sell", op=2000, tp=0)))
    bad = K.tp_side_violations(rows)
    assert len(bad) == 1 and bad[0]["tp"] == 1990 and bad[0]["side"] == "buy"


def test_time_limit_violations_weekend_aware(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             # Fri 20:00 -> Mon 09:00 = 61h calendar, 13h trading: fine for max_bars=12
                             row("2024.01.05 20:00:00", "2024.01.08 09:00:00", reason="time_limit"),
                             # closed after 3 trading hours: too early for max_bars=12
                             row("2024.01.03 10:00:00", "2024.01.03 13:00:00", reason="time_limit"),
                             # an sl exit is never a time-limit violation
                             row("2024.01.03 14:00:00", "2024.01.03 15:00:00", reason="sl")))
    bad = K.time_limit_violations(rows, max_bars=12)
    assert len(bad) == 1 and bad[0]["open_time"] == dt.datetime(2024, 1, 3, 10)
    assert K.time_limit_violations(rows[1:2], max_bars=1) == []          # 3h is within 1 + 2.5h
    assert K.time_limit_violations(
        K.load_rows(write(tmp_path, row("2024.01.03 10:00:00", "2024.01.03 15:00:00", reason="time_limit"))),
        max_bars=1) != []                                                # 5h > 3.5h -> violation


def test_monthly_stop_check(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.02 10:00:00", "2024.01.02 12:00:00", profit=-1500, balance=98500),
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", profit=-1000, balance=97500),
                             # opened after the -2% breach in the same month -> violation
                             row("2024.01.04 10:00:00", "2024.01.04 12:00:00", profit=100, balance=97600),
                             # next month is allowed
                             row("2024.02.01 10:00:00", "2024.02.01 12:00:00", profit=100, balance=97700)))
    breaches, bad = K.monthly_stop_check(rows, 2.0)
    assert breaches == 1
    assert len(bad) == 1 and bad[0]["open_time"] == dt.datetime(2024, 1, 4, 10)


def test_counters(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", reason="sl", r="-1.02", spread="40"),
                             row("2024.01.03 13:00:00", "2024.01.03 14:00:00", reason="time_limit", r="0.3",
                                 spread="15"),
                             row("2024.01.03 15:00:00", "2024.01.03 16:00:00", reason="sl", r="-0.98", spread="")))
    assert K.count_reason(rows, "sl") == 2
    assert K.max_spread(rows) == 40
    assert K.median_sl_r(rows) == pytest.approx(-1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_tradelog_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradelog_checks'`.

- [ ] **Step 3: Write `scripts/tradelog_checks.py`**

```python
"""Behaviour checks on an EA closed-trade CSV (EACore.mqh / RiskGuard.mqh columns). Pure functions."""
import csv
import datetime as dt
import statistics

TIME_FMT = "%Y.%m.%d %H:%M:%S"
_FLOATS = ("volume", "open_price", "close_price", "sl", "tp", "profit", "balance")


def load_rows(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = {"open_time": dt.datetime.strptime(r["open_time"], TIME_FMT),
                 "close_time": dt.datetime.strptime(r["close_time"], TIME_FMT),
                 "side": r["side"], "exit_reason": r["exit_reason"]}
            for k in _FLOATS:
                d[k] = float(r[k])
            d["r_multiple"] = float(r["r_multiple"]) if r["r_multiple"] else None
            d["spread_pts"] = int(r["spread_pts"]) if r["spread_pts"] else None
            rows.append(d)
    return rows


def trading_hours(open_t, close_t):
    hours = (close_t - open_t).total_seconds() / 3600.0
    saturdays = sum(1 for i in range((close_t.date() - open_t.date()).days + 1)
                    if (open_t.date() + dt.timedelta(days=i)).weekday() == 5)
    return hours - 48.0 * saturdays


def tp_side_violations(rows):
    bad = []
    for r in rows:
        if r["tp"] <= 0:
            continue
        if (r["side"] == "buy" and r["tp"] <= r["open_price"]) or (r["side"] == "sell" and r["tp"] >= r["open_price"]):
            bad.append(r)
    return bad


def time_limit_violations(rows, max_bars, slack=2.5):
    """time_limit exits must hold between max_bars - 1 and max_bars + slack trading hours."""
    bad = []
    for r in rows:
        if r["exit_reason"] != "time_limit":
            continue
        h = trading_hours(r["open_time"], r["close_time"])
        if h < max_bars - 1 or h > max_bars + slack:
            bad.append(r)
    return bad


def monthly_stop_check(rows, pct):
    """After closed balance falls pct below the month-start balance, no trade may open later that month."""
    rows = sorted(rows, key=lambda r: r["close_time"])
    breaches, bad = 0, []
    month, start_bal, breach_at = None, None, None
    for r in rows:
        key = r["close_time"].strftime("%Y-%m")
        if key != month:
            month, start_bal, breach_at = key, r["balance"] - r["profit"], None
        if breach_at is not None and r["open_time"] > breach_at and r["open_time"].strftime("%Y-%m") == month:
            bad.append(r)
        if breach_at is None and r["balance"] <= start_bal * (1.0 - pct / 100.0):
            breach_at = r["close_time"]
            breaches += 1
    return breaches, bad


def count_reason(rows, reason):
    return sum(1 for r in rows if r["exit_reason"] == reason)


def max_spread(rows):
    vals = [r["spread_pts"] for r in rows if r["spread_pts"] is not None]
    return max(vals) if vals else 0


def median_sl_r(rows):
    vals = [r["r_multiple"] for r in rows if r["exit_reason"] == "sl" and r["r_multiple"] is not None]
    return statistics.median(vals) if vals else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && python3 -m pytest tests/test_tradelog_checks.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Write the MT5 acceptance harness `scripts/meanrev_selfcheck.py`**

```python
#!/usr/bin/env python3
"""MeanRev_EA acceptance checks in MT5's tester (spec 4.6 + Review Focus). Quit the MT5 GUI first.

  python3 scripts/meanrev_selfcheck.py --mode bb
  python3 scripts/meanrev_selfcheck.py --mode rsi2
Exit code 0 only if every check passes.
"""
import argparse
import os
import subprocess
import sys

import macd_sweep as M
import tradelog_checks as K

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
WIN = ("2025-01-01", "2025-07-01")
OFF = {"InpMaxSpreadPoints": "0", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
MODES = {"bb": {"InpEntryMode": "0", "InpSLATR": "1.5", "InpMaxBars": "12", "InpRSILow": "30", "InpADXMax": "25"},
         "rsi2": {"InpEntryMode": "1", "InpSLATR": "3.0", "InpMaxBars": "24", "InpRSI2Entry": "10",
                  "InpRSI2Exit": "70", "InpTrendPeriod": "200"}}


def mt5_running():
    return subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0


def run(name, sets):
    for attempt in (1, 2):
        d = M.run("single", name, sets=sets, model="1min_ohlc", frm=WIN[0], to=WIN[1], timeout=900,
                  expert="MeanRev_EA")
        if d.get("metrics") and d.get("trade_log"):
            return d, K.load_rows(os.path.join(REPORTS, d["trade_log"]))
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    sys.exit(f"[{name}] no report or trade log after 2 attempts: {d.get('error')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    mode = ap.parse_args().mode
    if mt5_running():
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    M.OUTDIR = os.path.join(REPORTS, "meanrev_selfcheck")
    os.makedirs(M.OUTDIR, exist_ok=True)
    base = dict(MODES[mode], **OFF)
    results = []

    def check(label, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")

    a, rows_a = run(f"sc_{mode}_det1", base)
    b, _ = run(f"sc_{mode}_det2", base)
    ma, mb = a["metrics"], b["metrics"]
    check("determinism", (ma["net_profit"], ma["trades"]) == (mb["net_profit"], mb["trades"]),
          f"{ma['net_profit']} / {ma['trades']} vs {mb['net_profit']} / {mb['trades']}")
    check("enough trades in window", ma["trades"] >= 20, f"{ma['trades']}")
    check("trade log rows == trades", len(rows_a) == int(ma["trades"]), f"{len(rows_a)} vs {ma['trades']}")
    r = K.median_sl_r(rows_a)
    check("1% sizing: median R of sl exits near -1", r is not None and -1.3 <= r <= -0.8, f"{r}")
    check("time limit honoured", not K.time_limit_violations(rows_a, int(base["InpMaxBars"])),
          f"{len(K.time_limit_violations(rows_a, int(base['InpMaxBars'])))} violations")
    if mode == "bb":
        check("BB take-profit on profit side", not K.tp_side_violations(rows_a),
              f"{len(K.tp_side_violations(rows_a))} violations")
    else:
        check("RSI2 exits happen", K.count_reason(rows_a, "rsi_exit") >= 1, f"{K.count_reason(rows_a, 'rsi_exit')}")

    _, rows = run(f"sc_{mode}_bars1", dict(base, InpMaxBars="1"))
    check("forced time limit fires", K.count_reason(rows, "time_limit") >= 1, f"{K.count_reason(rows, 'time_limit')}")
    check("forced time limit bounds", not K.time_limit_violations(rows, 1), f"{len(K.time_limit_violations(rows, 1))}")

    _, rows = run(f"sc_{mode}_daily", dict(base, InpDailyLossPct="0.3"))
    check("forced daily stop closes positions", K.count_reason(rows, "daily_stop") >= 1,
          f"{K.count_reason(rows, 'daily_stop')}")

    _, rows = run(f"sc_{mode}_monthly", dict(base, InpMonthlyLossPct="2.0"))
    breaches, bad = K.monthly_stop_check(rows, 2.0)
    check("forced monthly stop blocks entries", breaches >= 1 and not bad, f"breaches {breaches}, violations {len(bad)}")

    _, rows = run(f"sc_{mode}_spread", dict(base, InpMaxSpreadPoints="20"))
    check("spread guard: no entry above 20 pts", K.max_spread(rows) <= 20, f"max {K.max_spread(rows)}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the harness to verify it fails**

Quit the MT5 GUI. Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_selfcheck.py --mode bb`
Expected: it exits non-zero with `no report or trade log after 2 attempts`, because `MeanRev_EA.ex5` does not exist yet.

- [ ] **Step 7: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add scripts/tradelog_checks.py tests/test_tradelog_checks.py scripts/meanrev_selfcheck.py
git commit -m "test: trade-log behaviour checks and MeanRev_EA MT5 acceptance harness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `EACore.mqh` and `MeanRev_EA` with the BB_FADE mode

**Files:**
- Create: `mql5/EACore.mqh`
- Create: `mql5/MeanRev_EA.mq5`
- Create: `scripts/install_meanrev.sh`

**Interfaces:**
- Consumes: `scripts/meanrev_selfcheck.py` (Task 4) as the acceptance test.
- Produces, in `EACore.mqh` (the EA must `#define EA_NAME`, `EA_VERSION`, `EA_MAGIC`, `EA_COMMENT` before `#include "EACore.mqh"`):
  - Inputs: `InpMagic`, `InpTimeframe`, `InpComment`, `InpSlippagePoints`, `InpMaxSpreadPoints`, `InpRiskMode`, `InpRiskValue`, `InpSpreadWaitMin`, `InpDailyLossPct`, `InpMonthlyLossPct`, `InpTradeLog`, `InpLogEveryBar`.
  - `bool Core_Init()`, `void Core_PrintReady()`, `void Core_Deinit(const int reason)`.
  - `void Core_OnTickStart()`: loss stops, closes own positions on a breached daily stop.
  - `datetime Core_CurrentBar()`, `bool Core_IsNewBar(const datetime bar)`, `void Core_MarkBar(const datetime bar)`.
  - `void Core_RetryPending(const datetime bar)`.
  - `bool Core_Open(const ENUM_POSITION_TYPE type, const double slDistance, const double tpPrice)`; `tpPrice` = 0 means no take-profit.
  - `bool Core_SelectOwnPosition(ulong &ticket)`, which leaves that position selected.
  - `bool Core_ClosePosition(const ulong ticket, const string reason)`.
  - `int CountOwnPositions()`, `bool IsOwnPosition()`.
  - `void Core_LogBar(const datetime bar, const string detail)`.
  - `void Core_OnTradeTransaction(const MqlTradeTransaction &trans)`.
  - `double Core_OnTester()`.
  - Trade log file: `<EA_NAME>_<symbol>_<magic>[_tester]_trades.csv`, exit reasons `sl`, `tp`, `stop_out`, `expert`, `end_of_test`, `manual`, `other`, plus forced reasons passed to `Core_ClosePosition` (`daily_stop`, `time_limit`, `rsi_exit`).
- Produces, in `MeanRev_EA.mq5`: `enum ENUM_MR_MODE { MR_BB_FADE = 0, MR_RSI2_PULLBACK = 1 }` and the strategy inputs listed in `ea_profiles.PROFILES["MeanRev_EA"]`.

- [ ] **Step 1: Write `mql5/EACore.mqh`**

```mql5
//+------------------------------------------------------------------+
//| EACore.mqh -- shared execution core for MBT EAs                  |
//| Copied from MACD_Cross_EA v1.42 without behaviour changes:       |
//| risk sizing, spread guard + in-bar retry, daily/monthly loss     |
//| stops, closed-trade CSV log, Ready check / Bar status lines,     |
//| restart-safe bar tracking. Strategy-free.                        |
//|                                                                  |
//| Before including, the EA defines:                                |
//|   #define EA_NAME    "MeanRev_EA"                                |
//|   #define EA_VERSION "1.00"                                      |
//|   #define EA_MAGIC   240819                                      |
//|   #define EA_COMMENT "MREV"                                      |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

enum ENUM_RISK_MODE
  {
   RISK_MONEY   = 0,  // Fixed money per trade (account currency)
   RISK_PERCENT = 1   // Percent of account balance per trade
  };

input group "=== General ==="
input ulong               InpMagic           = EA_MAGIC;    // Magic number
input ENUM_TIMEFRAMES     InpTimeframe       = PERIOD_H1;   // Signal timeframe
input string              InpComment         = EA_COMMENT;  // Order comment
input int                 InpSlippagePoints  = 30;          // Max deviation (points)
input int                 InpMaxSpreadPoints = 150;         // Max spread to trade (points, 0 = off)

input group "=== Risk ==="
input ENUM_RISK_MODE      InpRiskMode        = RISK_PERCENT;// Risk mode
input double              InpRiskValue       = 1.0;         // Risk value (money or %)

input group "=== Safety guards ==="
input int                 InpSpreadWaitMin   = 30;          // Retry a spread-blocked signal for N minutes (0 = drop it)
input double              InpDailyLossPct    = 4.0;         // Daily loss stop, % of day-start balance (0 = off)
input double              InpMonthlyLossPct  = 12.0;        // Monthly loss stop, % of month-start balance (0 = off)
input bool                InpTradeLog        = true;        // Write closed trades to a CSV in MQL5\Files

input group "=== Diagnostics ==="
input bool                InpLogEveryBar     = true;        // Log a status line on every new signal bar

#define TRADE_LOG_HEADER "close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"

CTrade        g_trade;
CSymbolInfo   g_symbol;
datetime      g_lastBarTime  = 0;
string        g_gvLastBar    = "";
int           g_pendingType  = -1;     // spread-blocked signal: POSITION_TYPE_BUY/SELL, -1 = none
datetime      g_pendingBar   = 0;
double        g_pendingSL    = 0.0;
double        g_pendingTP    = 0.0;
datetime      g_dayStart     = 0;
double        g_dayBalance   = 0.0;
datetime      g_monthStart   = 0;
double        g_monthBalance = 0.0;
bool          g_dayStopHit   = false;
string        g_logFile      = "";
int           g_logFlags     = 0;
uint          g_lastCloseFailMsg = 0;
struct EntryInfo   { ulong position; int spread; double slippage; double risk; };
struct ForcedClose { ulong position; string reason; };
EntryInfo     g_entries[];
ForcedClose   g_forced[];

//+------------------------------------------------------------------+
//| Lifecycle                                                        |
//+------------------------------------------------------------------+
bool Core_Init()
  {
   if(InpRiskValue <= 0.0 || (InpRiskMode == RISK_PERCENT && InpRiskValue > 100.0))
     {
      Print("Invalid risk value");
      return(false);
     }
   if(InpSpreadWaitMin < 0 || InpDailyLossPct < 0.0 || InpDailyLossPct >= 100.0 ||
      InpMonthlyLossPct < 0.0 || InpMonthlyLossPct >= 100.0)
     {
      Print("Invalid safety guard inputs");
      return(false);
     }
   if(!g_symbol.Name(_Symbol))
     {
      Print("Failed to initialise symbol ", _Symbol);
      return(false);
     }
   g_symbol.Refresh();

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);

   g_gvLastBar = StringFormat("%s_%s_%s_%I64u", EA_NAME, _Symbol, EnumToString(InpTimeframe), InpMagic);
   if(GlobalVariableCheck(g_gvLastBar))
      g_lastBarTime = (datetime)GlobalVariableGet(g_gvLastBar);
   UpdatePeriodBalances();
   LogInit();
   return(true);
  }

void Core_PrintReady()
  {
   PrintFormat("Guards: max spread %s, retry %d min | daily stop %s | monthly stop %s | trade log %s",
               InpMaxSpreadPoints > 0 ? StringFormat("%d pts", InpMaxSpreadPoints) : "off", InpSpreadWaitMin,
               InpDailyLossPct > 0.0 ? StringFormat("-%.1f%%", InpDailyLossPct) : "off",
               InpMonthlyLossPct > 0.0 ? StringFormat("-%.1f%%", InpMonthlyLossPct) : "off",
               InpTradeLog ? "on" : "off");
   Print("Ready check: ", TradePermissionText(), " | ", GuardStatus());
  }

void Core_Deinit(const int reason)
  {
   if(reason == REASON_REMOVE || reason == REASON_PARAMETERS || reason == REASON_CHARTCHANGE)
      GlobalVariableDel(g_gvLastBar);
  }

//+------------------------------------------------------------------+
//| Bar tracking                                                     |
//+------------------------------------------------------------------+
datetime Core_CurrentBar()                 { return(iTime(_Symbol, InpTimeframe, 0)); }
bool     Core_IsNewBar(const datetime bar) { return(bar != 0 && bar != g_lastBarTime); }

void Core_MarkBar(const datetime bar)
  {
   g_lastBarTime = bar;
   GlobalVariableSet(g_gvLastBar, (double)bar);
  }

//+------------------------------------------------------------------+
//| Diagnostics                                                      |
//+------------------------------------------------------------------+
string TradePermissionText()
  {
   return(StringFormat("terminal=%s program=%s account=%s expert=%s connected=%s",
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "on" : "OFF",
          MQLInfoInteger(MQL_TRADE_ALLOWED)          ? "on" : "OFF",
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)  ? "on" : "OFF",
          AccountInfoInteger(ACCOUNT_TRADE_EXPERT)   ? "on" : "OFF",
          TerminalInfoInteger(TERMINAL_CONNECTED)    ? "yes" : "NO"));
  }

void Core_LogBar(const datetime bar, const string detail)
  {
   if(!InpLogEveryBar)
      return;
   PrintFormat("Bar %s | %s | own positions %d | trading %s | %s",
               TimeToString(bar, TIME_DATE | TIME_MINUTES), detail, CountOwnPositions(),
               TradePermissionText(), GuardStatus());
  }

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
bool IsOwnPosition()
  {
   return(PositionGetInteger(POSITION_MAGIC) == (long)InpMagic &&
          PositionGetString(POSITION_SYMBOL) == _Symbol);
  }

int CountOwnPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && IsOwnPosition())
         count++;
   return(count);
  }

bool Core_SelectOwnPosition(ulong &ticket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong t = PositionGetTicket(i);
      if(t != 0 && IsOwnPosition())
        {
         ticket = t;
         return(true);
        }
     }
   return(false);
  }

bool Core_ClosePosition(const ulong ticket, const string reason)
  {
   if(!PositionSelectByTicket(ticket))
      return(false);
   const int n = ArraySize(g_forced);
   ArrayResize(g_forced, n + 1, 16);
   g_forced[n].position = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
   g_forced[n].reason   = reason;
   if(g_trade.PositionClose(ticket))
      return(true);
   if(GetTickCount() - g_lastCloseFailMsg > 60000)
     {
      g_lastCloseFailMsg = GetTickCount();
      PrintFormat("Close #%I64u (%s) failed: %d %s -- retrying every tick", ticket, reason,
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
     }
   return(false);
  }

bool TradingAllowed()
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
     {
      Print("Trading is not allowed (terminal/account/EA settings)");
      return(false);
     }
   if(g_symbol.TradeMode() == SYMBOL_TRADE_MODE_DISABLED || g_symbol.TradeMode() == SYMBOL_TRADE_MODE_CLOSEONLY)
     {
      Print("Trading disabled for symbol ", _Symbol);
      return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
//| Entry                                                            |
//+------------------------------------------------------------------+
double CalculateLots(const double slDistance, const ENUM_ORDER_TYPE orderType, const double price)
  {
   if(slDistance <= 0.0)
      return(0.0);
   double riskMoney = (InpRiskMode == RISK_MONEY) ? InpRiskValue
                      : AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskValue / 100.0;
   if(riskMoney <= 0.0)
      return(0.0);
   double tickSize  = g_symbol.TickSize();
   double tickValue = g_symbol.TickValueLoss();
   if(tickSize <= 0.0 || tickValue <= 0.0)
      return(0.0);
   double lossPerLot = slDistance / tickSize * tickValue;
   if(lossPerLot <= 0.0)
      return(0.0);
   double lots = riskMoney / lossPerLot;
   const double step   = g_symbol.LotsStep();
   const double minLot = g_symbol.LotsMin();
   const double maxLot = g_symbol.LotsMax();
   if(step <= 0.0)
      return(0.0);
   lots = MathFloor(lots / step) * step;
   if(lots > maxLot) lots = maxLot;
   if(lots < minLot) return(0.0);        // never exceed configured risk
   double margin = 0.0;
   if(!OrderCalcMargin(orderType, _Symbol, lots, price, margin))
      return(0.0);
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.95;
   if(margin > freeMargin && margin > 0.0)
     {
      lots = MathFloor(lots * freeMargin / margin / step) * step;
      if(lots < minLot)
         return(0.0);
     }
   const int volDigits = (int)MathRound(-MathLog10(step));
   return(NormalizeDouble(lots, volDigits));
  }

bool Core_Open(const ENUM_POSITION_TYPE type, const double slDistance, const double tpPrice)
  {
   if(!TradingAllowed() || !g_symbol.RefreshRates())
      return(false);
   string why;
   if(!GuardsAllowEntry(why))
     {
      PrintFormat("Signal skipped, %s", why);
      return(false);
     }
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
     {
      if(InpSpreadWaitMin > 0 && g_pendingType < 0)
        {
         g_pendingType = (int)type;
         g_pendingBar  = Core_CurrentBar();
         g_pendingSL   = slDistance;
         g_pendingTP   = tpPrice;
         PrintFormat("Signal delayed, spread %d > %d points -- retrying for up to %d min",
                     g_symbol.Spread(), InpMaxSpreadPoints, InpSpreadWaitMin);
        }
      else if(InpSpreadWaitMin <= 0)
         PrintFormat("Signal skipped, spread %d > %d points", g_symbol.Spread(), InpMaxSpreadPoints);
      return(false);
     }

   const int    digits  = g_symbol.Digits();
   const double minDist = (double)g_symbol.StopsLevel() * g_symbol.Point();
   const bool   isBuy   = (type == POSITION_TYPE_BUY);
   const double price   = isBuy ? g_symbol.Ask() : g_symbol.Bid();
   const double slDist  = MathMax(slDistance, minDist);
   const double sl      = NormalizeDouble(isBuy ? price - slDist : price + slDist, digits);
   double tp = 0.0;
   if(tpPrice > 0.0)
     {
      const bool valid = isBuy ? (tpPrice > price && tpPrice - price >= minDist)
                               : (tpPrice < price && price - tpPrice >= minDist);
      if(!valid)
        {
         PrintFormat("Signal skipped, take-profit %s is not beyond entry price %s",
                     DoubleToString(tpPrice, digits), DoubleToString(price, digits));
         return(false);
        }
      tp = NormalizeDouble(tpPrice, digits);
     }

   const double lots = CalculateLots(MathAbs(price - sl), isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, price);
   if(lots <= 0.0)
     {
      Print("Signal skipped, lot calculation returned 0 (risk too small or insufficient margin)");
      return(false);
     }
   const bool ok = isBuy ? g_trade.Buy (lots, _Symbol, price, sl, tp, InpComment)
                         : g_trade.Sell(lots, _Symbol, price, sl, tp, InpComment);
   const uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s failed: %d %s", isBuy ? "Buy" : "Sell", rc, g_trade.ResultRetcodeDescription());
      return(false);
     }
   const double fill = g_trade.ResultPrice();
   const int    n    = ArraySize(g_entries);
   ArrayResize(g_entries, n + 1, 64);
   g_entries[n].position = g_trade.ResultOrder();
   g_entries[n].spread   = g_symbol.Spread();
   g_entries[n].slippage = (fill > 0.0) ? (isBuy ? fill - price : price - fill) / g_symbol.Point() : 0.0;
   double perLot = 0.0;
   g_entries[n].risk = (OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, price, sl, perLot)
                        && perLot < 0.0) ? -perLot : 0.0;
   return(true);
  }

void Core_RetryPending(const datetime barTime)
  {
   if(g_pendingType < 0)
      return;
   if(barTime != g_pendingBar || TimeCurrent() - g_pendingBar > InpSpreadWaitMin * 60)
     {
      PrintFormat("Delayed %s signal dropped, spread stayed above %d points",
                  g_pendingType == POSITION_TYPE_BUY ? "BUY" : "SELL", InpMaxSpreadPoints);
      g_pendingType = -1;
      return;
     }
   if(!g_symbol.RefreshRates() || g_symbol.Spread() > InpMaxSpreadPoints)
      return;
   const ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)g_pendingType;
   g_pendingType = -1;
   if(CountOwnPositions() > 0)
      return;
   PrintFormat("Spread back to %d points, executing delayed %s signal", g_symbol.Spread(),
               type == POSITION_TYPE_BUY ? "BUY" : "SELL");
   Core_Open(type, g_pendingSL, g_pendingTP);
  }

//+------------------------------------------------------------------+
//| Loss stops                                                       |
//+------------------------------------------------------------------+
double BalanceAt(const datetime t)
  {
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(!HistorySelect(t, TimeCurrent() + 86400))
      return(bal);
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong d = HistoryDealGetTicket(i);
      const long  k = HistoryDealGetInteger(d, DEAL_TYPE);
      if(d != 0 && k != DEAL_TYPE_BALANCE && k != DEAL_TYPE_CREDIT)
         bal -= HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
                HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
   return(bal);
  }

void UpdatePeriodBalances()
  {
   const datetime now = TimeCurrent();
   const datetime day = now - now % 86400;
   if(day != g_dayStart)
     {
      g_dayStart   = day;
      g_dayBalance = BalanceAt(day);
      g_dayStopHit = false;
     }
   MqlDateTime t;
   TimeToStruct(now, t);
   t.day = 1; t.hour = 0; t.min = 0; t.sec = 0;
   const datetime month = StructToTime(t);
   if(month != g_monthStart)
     {
      g_monthStart   = month;
      g_monthBalance = BalanceAt(month);
     }
  }

double DailyFloor()     { return(g_dayBalance   * (1.0 - InpDailyLossPct   / 100.0)); }
double MonthlyFloor()   { return(g_monthBalance * (1.0 - InpMonthlyLossPct / 100.0)); }
bool   DailyStopped()   { return(InpDailyLossPct   > 0.0 && AccountInfoDouble(ACCOUNT_EQUITY) <= DailyFloor()); }
bool   MonthlyStopped() { return(InpMonthlyLossPct > 0.0 && AccountInfoDouble(ACCOUNT_EQUITY) <= MonthlyFloor()); }

string GuardStatus()
  {
   const double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   return(StringFormat("guards %s | day %+.2f%% (stop -%.1f%%) | month %+.2f%% (stop -%.1f%%)",
                       DailyStopped() ? "DAILY STOP" : MonthlyStopped() ? "MONTHLY STOP" : "ok",
                       g_dayBalance   > 0.0 ? 100.0 * (eq / g_dayBalance   - 1.0) : 0.0, InpDailyLossPct,
                       g_monthBalance > 0.0 ? 100.0 * (eq / g_monthBalance - 1.0) : 0.0, InpMonthlyLossPct));
  }

void Core_OnTickStart()
  {
   UpdatePeriodBalances();
   if(!DailyStopped())
      return;
   if(!g_dayStopHit)
     {
      g_dayStopHit = true;
      PrintFormat("DAILY STOP: equity %.2f <= floor %.2f (-%.1f%% of day start %.2f) -- closing own positions, no entries until next server day",
                  AccountInfoDouble(ACCOUNT_EQUITY), DailyFloor(), InpDailyLossPct, g_dayBalance);
     }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && IsOwnPosition())
         Core_ClosePosition(ticket, "daily_stop");
     }
  }

bool GuardsAllowEntry(string &why)
  {
   why = "";
   if(DailyStopped())
      why = StringFormat("daily loss stop (equity %.2f <= %.2f)", AccountInfoDouble(ACCOUNT_EQUITY), DailyFloor());
   else if(MonthlyStopped())
      why = StringFormat("monthly loss stop (equity %.2f <= %.2f)", AccountInfoDouble(ACCOUNT_EQUITY), MonthlyFloor());
   return(why == "");
  }

//+------------------------------------------------------------------+
//| Closed-trade CSV log                                             |
//+------------------------------------------------------------------+
void LogInit()
  {
   g_logFile = "";
   if(!InpTradeLog || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   const bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   g_logFlags = tester ? FILE_COMMON : 0;
   const string name = StringFormat("%s_%s_%I64u%s_trades.csv", EA_NAME, _Symbol, InpMagic, tester ? "_tester" : "");
   if(tester)
      FileDelete(name, g_logFlags);
   const int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_logFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Trade log: cannot open %s, error %d -- logging off", name, GetLastError());
      return;
     }
   if(FileSize(h) == 0)
      FileWriteString(h, TRADE_LOG_HEADER);
   FileClose(h);
   g_logFile = name;
  }

string ReasonName(const long reason, const ulong pos)
  {
   for(int i = ArraySize(g_forced) - 1; i >= 0; i--)
      if(g_forced[i].position == pos)
         return(g_forced[i].reason);
   switch((int)reason)
     {
      case DEAL_REASON_SL:     return("sl");
      case DEAL_REASON_TP:     return("tp");
      case DEAL_REASON_SO:     return("stop_out");
      case DEAL_REASON_EXPERT: return("expert");
      case DEAL_REASON_CLIENT: return(MQLInfoInteger(MQL_TESTER) ? "end_of_test" : "manual");
      case DEAL_REASON_MOBILE:
      case DEAL_REASON_WEB:    return("manual");
     }
   return("other");
  }

void LogClosingDeal(const ulong deal)
  {
   if(g_logFile == "" || !HistoryDealSelect(deal))
      return;
   const long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
      return;
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
      return;
   const datetime closeTime = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   const ulong    pos    = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   const bool     wasBuy = (HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_SELL);
   const double   vol    = HistoryDealGetDouble(deal, DEAL_VOLUME);
   const double   px     = HistoryDealGetDouble(deal, DEAL_PRICE);
   const double   gross  = HistoryDealGetDouble(deal, DEAL_PROFIT);
   const double   swap   = HistoryDealGetDouble(deal, DEAL_SWAP);
   const double   outFee = HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
   const double   sl     = HistoryDealGetDouble(deal, DEAL_SL);
   const double   tp     = HistoryDealGetDouble(deal, DEAL_TP);
   const string   reason = ReasonName(HistoryDealGetInteger(deal, DEAL_REASON), pos);

   if(!HistorySelectByPosition(pos))
      return;
   datetime openTime = 0;
   long     inMagic  = -1;
   double   inVol = 0.0, inPxVol = 0.0, inFee = 0.0;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      const ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;
      const double v = HistoryDealGetDouble(d, DEAL_VOLUME);
      if(openTime == 0)
        {
         openTime = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
         inMagic  = HistoryDealGetInteger(d, DEAL_MAGIC);
        }
      inVol   += v;
      inPxVol += HistoryDealGetDouble(d, DEAL_PRICE) * v;
      inFee   += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
   if(inMagic != (long)InpMagic || inVol <= 0.0)
      return;
   const double share = MathMin(vol / inVol, 1.0);
   const double comm  = outFee + inFee * share;
   const double net   = gross + swap + comm;
   const int    dg    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string spread = "", slip = "", rMult = "";
   for(int i = ArraySize(g_entries) - 1; i >= 0; i--)
      if(g_entries[i].position == pos)
        {
         spread = IntegerToString(g_entries[i].spread);
         slip   = DoubleToString(g_entries[i].slippage, 1);
         if(g_entries[i].risk > 0.0)
            rMult = DoubleToString(net / (g_entries[i].risk * share), 3);
         break;
        }
   const int h = FileOpen(g_logFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_logFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Trade log write failed, error %d", GetLastError());
      return;
     }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, StringFormat("%s,%s,%s,%s,%.2f,%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%s,%s,%s,%s,%I64u,%I64u,%I64u\n",
                   TimeToString(closeTime, TIME_DATE | TIME_SECONDS), TimeToString(openTime, TIME_DATE | TIME_SECONDS),
                   _Symbol, wasBuy ? "buy" : "sell", vol, DoubleToString(inPxVol / inVol, dg), DoubleToString(px, dg),
                   DoubleToString(sl, dg), DoubleToString(tp, dg), gross, comm, swap, net,
                   AccountInfoDouble(ACCOUNT_BALANCE), rMult, reason, spread, slip, deal, pos, InpMagic));
   FileClose(h);
  }

void Core_OnTradeTransaction(const MqlTradeTransaction &trans)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal != 0)
      LogClosingDeal(trans.deal);
  }

//--- custom optimization criterion ("Custom max"), same as MACD_Cross_EA
double Core_OnTester()
  {
   const double trades = TesterStatistics(STAT_TRADES);
   if(trades < 30)
      return(0.0);
   const double profit = TesterStatistics(STAT_PROFIT);
   if(profit <= 0.0)
      return(0.0);
   return(profit * TesterStatistics(STAT_PROFIT_FACTOR) / (1.0 + TesterStatistics(STAT_EQUITY_DDREL_PERCENT)));
  }
//+------------------------------------------------------------------+
```

- [ ] **Step 2: Write `mql5/MeanRev_EA.mq5` with the BB_FADE mode**

`RSI2_PULLBACK` inputs are declared now so the input list is final. Its signal function is added in Task 6. Until then, selecting mode 1 fails `OnInit` with a clear message.

```mql5
//+------------------------------------------------------------------+
//|                                                   MeanRev_EA.mq5 |
//|  H1 XAUUSD mean reversion, companion to MACD_Cross_EA (same      |
//|  account, one DARWIN). Spec: docs/superpowers/specs/             |
//|  2026-10-04-meanrev-ea-design.md                                 |
//|                                                                  |
//|  v1.00                                                           |
//|  - BB_FADE: close beyond Bollinger band + RSI(14) stretched +    |
//|    ADX(14) below max (no strong trend); target = middle band     |
//|  - RSI2_PULLBACK: RSI(2) dip in the EMA trend; exit on RSI(2)    |
//|    rebound                                                       |
//|  - hard stop = SL x ATR(14), time limit = max H1 candles held    |
//|  - execution, guards and trade log from EACore.mqh (v1.42 core)  |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "Mean-reversion EA (BB fade / RSI2 pullback) with MACD_Cross_EA v1.42 safety core"

#define EA_NAME    "MeanRev_EA"
#define EA_VERSION "1.00"
#define EA_MAGIC   240819
#define EA_COMMENT "MREV"
#include "EACore.mqh"

enum ENUM_MR_MODE
  {
   MR_BB_FADE       = 0,  // Bollinger fade in quiet markets
   MR_RSI2_PULLBACK = 1   // RSI(2) pullback in the trend
  };

input group "=== Strategy ==="
input ENUM_MR_MODE        InpEntryMode       = MR_BB_FADE;  // Entry mode
input int                 InpATRPeriod       = 14;          // ATR period (stop distance)
input double              InpSLATR           = 1.5;         // Stop loss (x ATR)
input int                 InpMaxBars         = 12;          // Time limit (H1 candles held, 0 = off)

input group "=== BB_FADE ==="
input int                 InpBBPeriod        = 20;          // Bollinger period
input double              InpBBDev           = 2.0;         // Bollinger deviation
input int                 InpRSIPeriod       = 14;          // RSI period
input double              InpRSILow          = 30.0;        // Long below / short above 100-x (50 = off)
input int                 InpADXPeriod       = 14;          // ADX period
input double              InpADXMax          = 20.0;        // Trade only if ADX below (100 = off)

input group "=== RSI2_PULLBACK ==="
input int                 InpRSI2Period      = 2;           // Short RSI period
input double              InpRSI2Entry       = 10.0;        // Long below / short above 100-x
input double              InpRSI2Exit        = 70.0;        // Long exit above / short exit below 100-x
input int                 InpTrendPeriod     = 200;         // Trend EMA period

int g_atr   = INVALID_HANDLE;
int g_bands = INVALID_HANDLE;
int g_rsi   = INVALID_HANDLE;
int g_adx   = INVALID_HANDLE;
int g_rsi2  = INVALID_HANDLE;
int g_ema   = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpATRPeriod <= 0 || InpSLATR <= 0.0 || InpMaxBars < 0)
     {
      Print("Invalid ATR period / SL multiple / time limit");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEntryMode == MR_BB_FADE &&
      (InpBBPeriod <= 1 || InpBBDev <= 0.0 || InpRSIPeriod <= 1 || InpRSILow <= 0.0 || InpRSILow > 50.0 ||
       InpADXPeriod <= 1 || InpADXMax <= 0.0))
     {
      Print("Invalid BB_FADE inputs");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEntryMode == MR_RSI2_PULLBACK && !RSI2Ready())
      return(INIT_PARAMETERS_INCORRECT);
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);

   g_atr = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   if(InpEntryMode == MR_BB_FADE)
     {
      g_bands = iBands(_Symbol, InpTimeframe, InpBBPeriod, 0, InpBBDev, PRICE_CLOSE);
      g_rsi   = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
      g_adx   = iADX(_Symbol, InpTimeframe, InpADXPeriod);
      if(g_bands == INVALID_HANDLE || g_rsi == INVALID_HANDLE || g_adx == INVALID_HANDLE)
        {
         Print("Failed to create BB_FADE indicators, error ", GetLastError());
         return(INIT_FAILED);
        }
     }
   else
     {
      g_rsi2 = iRSI(_Symbol, InpTimeframe, InpRSI2Period, PRICE_CLOSE);
      g_ema  = iMA(_Symbol, InpTimeframe, InpTrendPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_rsi2 == INVALID_HANDLE || g_ema == INVALID_HANDLE)
        {
         Print("Failed to create RSI2_PULLBACK indicators, error ", GetLastError());
         return(INIT_FAILED);
        }
     }
   if(g_atr == INVALID_HANDLE)
     {
      Print("Failed to create ATR, error ", GetLastError());
      return(INIT_FAILED);
     }

   PrintFormat("%s v%s started on %s %s, magic %I64u, own positions: %d",
               EA_NAME, EA_VERSION, _Symbol, EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
   PrintFormat("Inputs: mode=%s | BB(%d,%.1f) RSI%d<%.0f ADX%d<%.0f | RSI%d entry %.0f exit %.0f EMA%d | SL %.2fxATR(%d) maxBars %d | risk %s %.2f",
               InpEntryMode == MR_BB_FADE ? "BB_FADE" : "RSI2_PULLBACK",
               InpBBPeriod, InpBBDev, InpRSIPeriod, InpRSILow, InpADXPeriod, InpADXMax,
               InpRSI2Period, InpRSI2Entry, InpRSI2Exit, InpTrendPeriod, InpSLATR, InpATRPeriod, InpMaxBars,
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void ReleaseHandle(int &handle)
  {
   if(handle != INVALID_HANDLE)
     {
      IndicatorRelease(handle);
      handle = INVALID_HANDLE;
     }
  }

void OnDeinit(const int reason)
  {
   ReleaseHandle(g_atr);
   ReleaseHandle(g_bands);
   ReleaseHandle(g_rsi);
   ReleaseHandle(g_adx);
   ReleaseHandle(g_rsi2);
   ReleaseHandle(g_ema);
   Core_Deinit(reason);
  }

//+------------------------------------------------------------------+
bool ReadValue(const int handle, const int buffer, double &value)
  {
   double b[1];
   if(handle == INVALID_HANDLE || CopyBuffer(handle, buffer, 1, 1, b) < 1 || b[0] == EMPTY_VALUE)
      return(false);
   value = b[0];
   return(true);
  }

//--- close own positions held for InpMaxBars signal candles (counts candles, not hours: weekend-safe)
void CheckTimeLimit()
  {
   if(InpMaxBars <= 0)
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;
      const datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      const int held = iBarShift(_Symbol, InpTimeframe, opened, false);
      if(held >= InpMaxBars)
         Core_ClosePosition(ticket, "time_limit");
     }
  }

void OnTick()
  {
   Core_OnTickStart();
   CheckTimeLimit();
   const datetime bar = Core_CurrentBar();
   Core_RetryPending(bar);
   if(!Core_IsNewBar(bar))
      return;
   double atr = 0.0;
   const double close = iClose(_Symbol, InpTimeframe, 1);
   if(!ReadValue(g_atr, 0, atr) || atr <= 0.0 || close <= 0.0)
      return;                                   // data not ready: bar stays unprocessed, retried next tick
   if(InpEntryMode == MR_BB_FADE)
      OnBarBBFade(bar, close, atr);
   else
      OnBarRSI2(bar, close, atr);
  }

void OnBarBBFade(const datetime bar, const double close, const double atr)
  {
   double mid = 0.0, up = 0.0, lo = 0.0, rsi = 0.0, adx = 0.0;
   if(!ReadValue(g_bands, 0, mid) || !ReadValue(g_bands, 1, up) || !ReadValue(g_bands, 2, lo) ||
      !ReadValue(g_rsi, 0, rsi) || !ReadValue(g_adx, 0, adx))
      return;
   Core_MarkBar(bar);
   const string ctx = StringFormat("close %.2f band %.2f/%.2f/%.2f RSI %.1f ADX %.1f", close, lo, mid, up, rsi, adx);
   if(CountOwnPositions() > 0)
     {
      Core_LogBar(bar, ctx + " | in position");
      return;
     }
   const bool longSig  = close < lo && rsi < InpRSILow         && adx < InpADXMax;
   const bool shortSig = close > up && rsi > 100.0 - InpRSILow && adx < InpADXMax;
   if(!longSig && !shortSig)
     {
      Core_LogBar(bar, ctx + " | no signal");
      return;
     }
   Core_LogBar(bar, ctx + (longSig ? " | BUY signal" : " | SELL signal"));
   Core_Open(longSig ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, InpSLATR * atr, mid);
  }

//--- replaced in Task 6
bool RSI2Ready()
  {
   Print("RSI2_PULLBACK mode is not implemented in this build");
   return(false);
  }

void OnBarRSI2(const datetime bar, const double close, const double atr) { }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
```

- [ ] **Step 3: Write `scripts/install_meanrev.sh` (copy + compile)**

```bash
#!/bin/sh
# Copy MeanRev_EA + EACore.mqh into MT5's Advisors folder and compile with MetaEditor. Exit 1 on errors.
set -e
cd "$(dirname "$0")/.."
ADV="$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors"
cp mql5/EACore.mqh mql5/MeanRev_EA.mq5 "$ADV/"
"$HOME/.local/bin/mbt-wine-py" -c "
import sys; sys.path.insert(0, '.')
from core.compiler import compile_mql5
r = compile_mql5('C:/Program Files/MetaTrader 5/MQL5/Experts/Advisors/MeanRev_EA.mq5')
print({k: r.get(k) for k in ('ok', 'errors', 'warnings', 'messages', 'error')})
sys.exit(0 if r.get('ok') and not r.get('warnings') else 1)
" 2>/dev/null
```
Run `chmod +x scripts/install_meanrev.sh`.

- [ ] **Step 4: Compile**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && scripts/install_meanrev.sh`
Expected: `{'ok': True, 'errors': 0, 'warnings': 0, 'messages': [], 'error': None}` and exit code 0. On compile errors, fix the reported line in `mql5/EACore.mqh` or `mql5/MeanRev_EA.mq5` and rerun. Warnings count as failures.

- [ ] **Step 5: Run the acceptance harness for BB_FADE**

Quit the MT5 GUI. Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_selfcheck.py --mode bb`
Expected: every line `PASS` and `11/11 checks passed`, exit code 0.

If "enough trades in window" fails because there are fewer than 20 trades, set `MODES["bb"]["InpADXMax"]` in the harness to `"100"` (ADX filter off) and rerun. Note the change in the commit message. Do not loosen any other check.

- [ ] **Step 6: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add mql5/EACore.mqh mql5/MeanRev_EA.mq5 scripts/install_meanrev.sh
git commit -m "feat: MeanRev_EA v1.00 with BB_FADE mode on the shared EACore.mqh (v1.42 core)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: RSI2_PULLBACK mode

**Files:**
- Modify: `mql5/MeanRev_EA.mq5`: replace the two Task 5 placeholders `RSI2Ready()` and `OnBarRSI2(...)`.

**Interfaces:**
- Consumes: `Core_SelectOwnPosition`, `Core_ClosePosition`, `Core_Open`, `Core_LogBar`, `Core_MarkBar`, `CountOwnPositions` (Task 5).
- Produces: a working `InpEntryMode = MR_RSI2_PULLBACK`, with exit reason `rsi_exit` in the trade log.

- [ ] **Step 1: Run the harness to verify it fails**

Quit the MT5 GUI. Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_selfcheck.py --mode rsi2`
Expected: it exits non-zero, because `OnInit` refuses mode 1 and no report is written.

- [ ] **Step 2: Replace the placeholders in `mql5/MeanRev_EA.mq5`**

Replace the whole block from `//--- replaced in Task 6` through `void OnBarRSI2(const datetime bar, const double close, const double atr) { }` with:
```mql5
bool RSI2Ready()
  {
   if(InpRSI2Period <= 0 || InpRSI2Entry <= 0.0 || InpRSI2Entry >= 50.0 ||
      InpRSI2Exit <= 50.0 || InpRSI2Exit >= 100.0 || InpTrendPeriod <= 1)
     {
      Print("Invalid RSI2_PULLBACK inputs (entry 0-50, exit 50-100, trend period > 1)");
      return(false);
     }
   return(true);
  }

void OnBarRSI2(const datetime bar, const double close, const double atr)
  {
   double rsi2 = 0.0, ema = 0.0;
   if(!ReadValue(g_rsi2, 0, rsi2) || !ReadValue(g_ema, 0, ema))
      return;
   Core_MarkBar(bar);
   const string ctx = StringFormat("close %.2f EMA%d %.2f RSI%d %.1f", close, InpTrendPeriod, ema, InpRSI2Period, rsi2);

   ulong ticket = 0;
   if(Core_SelectOwnPosition(ticket))
     {
      const bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      if((isBuy && rsi2 > InpRSI2Exit) || (!isBuy && rsi2 < 100.0 - InpRSI2Exit))
        {
         Core_LogBar(bar, ctx + " | RSI2 exit");
         Core_ClosePosition(ticket, "rsi_exit");
        }
      else
        {
         Core_LogBar(bar, ctx + " | in position");
         return;
        }
      if(CountOwnPositions() > 0)
         return;                               // close failed: retried on the next new bar
     }

   const bool longSig  = close > ema && rsi2 < InpRSI2Entry;
   const bool shortSig = close < ema && rsi2 > 100.0 - InpRSI2Entry;
   if(!longSig && !shortSig)
     {
      Core_LogBar(bar, ctx + " | no signal");
      return;
     }
   Core_LogBar(bar, ctx + (longSig ? " | BUY signal" : " | SELL signal"));
   Core_Open(longSig ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, InpSLATR * atr, 0.0);
  }
```

- [ ] **Step 3: Compile**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT && scripts/install_meanrev.sh`
Expected: `ok: True`, 0 errors, 0 warnings.

- [ ] **Step 4: Run both harness modes**

Quit the MT5 GUI. Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_selfcheck.py --mode rsi2 && python3 meanrev_selfcheck.py --mode bb`
Expected: `11/11 checks passed` for both. BB_FADE is re-run to prove Task 6 did not change it.

- [ ] **Step 5: Commit**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add mql5/MeanRev_EA.mq5
git commit -m "feat: MeanRev_EA RSI2_PULLBACK mode (RSI(2) dip in EMA trend, exit on rebound)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The shoot-out, `meanrev_validate.py`

**Files:**
- Create: `scripts/meanrev_validate.py`

**Interfaces:**
- Consumes: `macd_sweep.run(..., expert=...)` (Task 1); `combine_eas.load_trades`, `summarize`, `monthly_correlation` (Task 2); `selection.plan_launches`, `pick_plateau`, `evaluate_gates`, `choose_winner` (Task 3); `MeanRev_EA` with both modes (Tasks 5 and 6).
- Produces: `reports/meanrev_shootout/` containing `stage_a_<mode>.json`, `stage_b_<mode>.json`, `macd.json` and `decision.json`. The decision holds per-mode gates, the winner or `null`, and the combined risk check.

- [ ] **Step 1: Write `scripts/meanrev_validate.py`**

```python
#!/usr/bin/env python3
"""MeanRev_EA shoot-out (spec section 4). System python; MT5 runs go through macd_sweep.run.

Quit the MT5 GUI first. Steps can be rerun individually; each writes reports/meanrev_shootout/<step>.json.
  python3 scripts/meanrev_validate.py stage-a --mode bb      # grids on 2024-26, 1-min OHLC
  python3 scripts/meanrev_validate.py stage-a --mode rsi2
  python3 scripts/meanrev_validate.py stage-b --mode bb      # real ticks per year + 2024-26 + 2022-26
  python3 scripts/meanrev_validate.py stage-b --mode rsi2
  python3 scripts/meanrev_validate.py macd                   # MACD v1.42 reference, real ticks 2022-26
  python3 scripts/meanrev_validate.py decide                 # gates, winner, combined risk check
"""
import argparse
import json
import os
import subprocess
import sys

import combine_eas as C
import macd_sweep as M
import selection as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
OUT = os.path.join(REPORTS, "meanrev_shootout")
IS_FROM, IS_TO = "2024-01-01", "2026-10-01"
MODE_SETS = {"bb": {"InpEntryMode": "0"}, "rsi2": {"InpEntryMode": "1"}}
GRIDS = {
    "bb": {"InpBBDev": ["2.0", "2.5"], "InpRSILow": ["25", "30", "50"], "InpADXMax": ["15", "20", "25", "100"],
           "InpSLATR": ["1.0", "1.5", "2.0", "3.0"], "InpMaxBars": ["6", "12", "24"]},
    "rsi2": {"InpRSI2Entry": ["5", "10", "15"], "InpRSI2Exit": ["50", "70"], "InpTrendPeriod": ["100", "200"],
             "InpSLATR": ["1.5", "2.0", "3.0", "4.0"], "InpMaxBars": ["12", "24", "48"]},
}
YEARS = {"2022": ("2022-01-01", "2023-01-01"), "2023": ("2023-01-01", "2024-01-01"),
         "2024": ("2024-01-01", "2025-01-01"), "2025": ("2025-01-01", "2026-01-01"),
         "2026": ("2026-01-01", "2026-10-01")}
WINDOWS = dict(YEARS, **{"2024-2026": (IS_FROM, IS_TO), "2022-2026": ("2022-01-01", IS_TO)})


def save(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)


def load(name):
    with open(os.path.join(OUT, name + ".json")) as f:
        return json.load(f)


def guarded_run(*args, **kw):
    """macd_sweep.run with one retry after killing a hung Wine terminal."""
    for attempt in (1, 2):
        d = M.run(*args, **kw)
        if d.get("metrics") or d.get("passes") is not None and d.get("passes"):
            return d
        subprocess.run(["pkill", "-KILL", "-f", "terminal64.exe"])
        subprocess.run(["sleep", "5"])
    return d


def stage_a(mode):
    passes = []
    for i, (fixed, ranges) in enumerate(S.plan_launches(GRIDS[mode])):
        d = guarded_run("opt", f"a_{mode}_{i:02d}", sets=dict(MODE_SETS[mode], **fixed), ranges=ranges,
                        model="1min_ohlc", frm=IS_FROM, to=IS_TO, timeout=1800, expert="MeanRev_EA")
        for p in d.get("passes") or []:
            for k, v in fixed.items():
                p[k] = float(v)
            passes.append(p)
    want = 1
    for vals in GRIDS[mode].values():
        want *= len(vals)
    pick = S.pick_plateau(passes, GRIDS[mode])
    M.log(f"[stage-a {mode}] {len(passes)}/{want} passes, pick: {pick}")
    save(f"stage_a_{mode}", {"passes": passes, "expected": want, "pick": pick})


def stage_b(mode):
    a = load(f"stage_a_{mode}")
    if not a["pick"]:
        save(f"stage_b_{mode}", {"error": "no Stage A survivor"})
        M.log(f"[stage-b {mode}] skipped: no Stage A survivor")
        return
    sets = dict(MODE_SETS[mode], **{k: M.fmt(float(a["pick"][k])) for k in GRIDS[mode]})
    out = {"sets": sets, "runs": {}}
    for label, (frm, to) in WINDOWS.items():
        d = guarded_run("single", f"b_{mode}_{label}", sets=sets, model="real_ticks", frm=frm, to=to,
                        timeout=1800, expert="MeanRev_EA")
        out["runs"][label] = {"metrics": d.get("metrics"), "trade_log": d.get("trade_log"), "error": d.get("error")}
    save(f"stage_b_{mode}", out)


def macd():
    d = guarded_run("single", "b_macd_2022-2026", sets={}, model="real_ticks", frm="2022-01-01", to=IS_TO,
                    timeout=1800, expert="MACD_Cross_EA")
    save("macd", {"metrics": d.get("metrics"), "trade_log": d.get("trade_log"), "error": d.get("error")})


def decide():
    m = load("macd")
    macd_trades = C.load_trades(os.path.join(REPORTS, m["trade_log"]))
    macd_alone = C.summarize(macd_trades)
    results = {}
    for mode in GRIDS:
        try:
            b = load(f"stage_b_{mode}")
        except FileNotFoundError:
            continue
        if "runs" not in b or any(not b["runs"][w]["metrics"] for w in WINDOWS):
            results[mode] = {"error": b.get("error", "missing Stage B runs")}
            continue
        mr = C.load_trades(os.path.join(REPORTS, b["runs"]["2022-2026"]["trade_log"]))
        corr = C.monthly_correlation(macd_trades, mr)
        combined = C.summarize(macd_trades, mr)
        years = {y: b["runs"][y]["metrics"] for y in YEARS}
        gates = S.evaluate_gates(years, b["runs"]["2024-2026"]["metrics"], corr, combined, macd_alone)
        results[mode] = {"sets": b["sets"], "gates": gates, "corr": corr, "combined": combined,
                         "alone": C.summarize(mr)}
    scored = {k: v for k, v in results.items() if "gates" in v}
    winner = S.choose_winner(scored) if scored else None
    risk = None
    if winner:
        c = scored[winner]["combined"]
        factor = min(1.0, 3.0 / abs(c["worst_day_pct"]) if c["worst_day_pct"] < 0 else 1.0,
                     20.0 / c["max_dd_pct"] if c["max_dd_pct"] > 0 else 1.0)
        risk = {"worst_day_pct": c["worst_day_pct"], "max_dd_pct": c["max_dd_pct"],
                "scale_needed": factor < 1.0, "suggested_risk_pct": round(int(factor * 20) / 20.0, 2)}
    save("decision", {"macd_alone": macd_alone, "modes": results, "winner": winner, "risk_check": risk})
    print(json.dumps({"winner": winner, "risk_check": risk,
                      "gates": {k: v.get("gates") for k, v in results.items()}}, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["stage-a", "stage-b", "macd", "decide"])
    ap.add_argument("--mode", choices=sorted(GRIDS))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    M.OUTDIR = OUT
    if args.step in ("stage-a", "stage-b", "macd") and \
            subprocess.run(["pgrep", "-f", "terminal64.exe"], capture_output=True).returncode == 0:
        sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
    if args.step in ("stage-a", "stage-b") and not args.mode:
        sys.exit("--mode is required for stage-a / stage-b")
    {"stage-a": lambda: stage_a(args.mode), "stage-b": lambda: stage_b(args.mode),
     "macd": macd, "decide": decide}[args.step]()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry check of the launch plan**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 -c "import meanrev_validate as V, selection as S; [print(m, len(S.plan_launches(g))) for m, g in V.GRIDS.items()]"`
Expected: `bb 16` and `rsi2 4`.

- [ ] **Step 3: Run Stage A for both modes**

Quit the MT5 GUI. Run in the background:
```bash
cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_validate.py stage-a --mode bb && python3 meanrev_validate.py stage-a --mode rsi2
```
Expected: `reports/meanrev_shootout/stage_a_bb.json` with 288 passes and `stage_a_rsi2.json` with 144 passes. Each `pick` is a pass dict or `null`. If a count is short, one launch failed: find the launch whose `a_<mode>_NN` run JSON has no `passes`, and rerun that step.

- [ ] **Step 4: Run Stage B and the MACD reference**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_validate.py stage-b --mode bb && python3 meanrev_validate.py stage-b --mode rsi2 && python3 meanrev_validate.py macd
```
Expected: `stage_b_bb.json` and `stage_b_rsi2.json` each have 7 runs, each with `metrics` and `trade_log`. `macd.json` has `metrics.net_profit` of at least 230,000. v1.42's default guards gave +230,149.97 on 2024–26 alone, and 2022–26 compounds on top of it. If MACD comes in lower, stop and investigate before deciding.

- [ ] **Step 5: Decide**

Run: `cd /Users/lyudmilnikodimov/Projects/MBT/scripts && python3 meanrev_validate.py decide`
Expected: a JSON with `winner` set to `"bb"`, `"rsi2"` or `null`, every gate's result and detail, and `risk_check`.

Report the gates table to the user exactly as produced. Never loosen a gate after seeing results.
- **If `winner` is `null`:** stop here. Report which gates failed per mode and recommend MACD alone. Tasks 8 and 9 are skipped.
- **If `risk_check.scale_needed` is true:** report `suggested_risk_pct` and get the user's confirmation before Task 8 changes risk for both EAs. MACD's live risk changes too.

- [ ] **Step 6: Commit the script and the decision**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add scripts/meanrev_validate.py
git add -f reports/meanrev_shootout/decision.json
git commit -m "feat: MeanRev_EA shoot-out orchestration and decision (winner: <bb|rsi2|none>)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Replace `<bb|rsi2|none>` with the actual result.

---

### Task 8: Ship the winning mode (only if Task 7 named a winner)

**Files:**
- Modify: `mql5/MeanRev_EA.mq5`
- Modify: `scripts/ea_profiles.py` (`PROFILES["MeanRev_EA"]`)
- Modify: `scripts/meanrev_selfcheck.py` (drop the removed mode)

**Interfaces:**
- Consumes: `reports/meanrev_shootout/decision.json`, with `winner` and `modes[winner].sets`.
- Produces: `MeanRev_EA.mq5` v1.00 containing only the winning strategy, with the winner's tuned values as compiled defaults. Its real-tick 2022–26 result is identical to the Stage B result.

- [ ] **Step 1: Remove the losing mode**

**If the winner is `bb`:**
- Delete `enum ENUM_MR_MODE` and the `InpEntryMode` input line.
- Delete the `=== RSI2_PULLBACK ===` input group: `InpRSI2Period`, `InpRSI2Entry`, `InpRSI2Exit`, `InpTrendPeriod`.
- Delete the globals `g_rsi2` and `g_ema`, and the functions `RSI2Ready` and `OnBarRSI2`.
- In `OnInit`:
  - Drop the `if(InpEntryMode == MR_RSI2_PULLBACK && !RSI2Ready())` check and the whole `else` branch that creates the RSI2/EMA handles.
  - Make the BB_FADE validation and handle creation unconditional.
  - Change the `Inputs:` line to `"Inputs: BB(%d,%.1f) RSI%d<%.0f ADX%d<%.0f | SL %.2fxATR(%d) maxBars %d | risk %s %.2f"`, removing the matching arguments.
- In `OnDeinit`, delete the `ReleaseHandle(g_rsi2);` and `ReleaseHandle(g_ema);` lines.
- In `OnTick`, replace the `if/else` with `OnBarBBFade(bar, close, atr);`.
- In `ea_profiles.PROFILES["MeanRev_EA"]`, delete `InpEntryMode`, `InpRSI2Period`, `InpRSI2Entry`, `InpRSI2Exit` and `InpTrendPeriod`.
- In `meanrev_selfcheck.py`, delete the `"rsi2"` entry from `MODES`, and the `"InpEntryMode"` key from `MODES["bb"]`.

**If the winner is `rsi2`:**
- Delete `enum ENUM_MR_MODE` and the `InpEntryMode` input line.
- Delete the `=== BB_FADE ===` input group: `InpBBPeriod`, `InpBBDev`, `InpRSIPeriod`, `InpRSILow`, `InpADXPeriod`, `InpADXMax`.
- Delete the globals `g_bands`, `g_rsi` and `g_adx`, and the function `OnBarBBFade`.
- In `OnInit`:
  - Drop the BB_FADE validation block and the BB_FADE handle branch.
  - Make the `RSI2Ready()` check and the RSI2/EMA handle creation unconditional.
  - Change the `Inputs:` line to `"Inputs: RSI%d entry %.0f exit %.0f EMA%d | SL %.2fxATR(%d) maxBars %d | risk %s %.2f"`, removing the matching arguments.
- In `OnDeinit`, delete the `ReleaseHandle(g_bands);`, `ReleaseHandle(g_rsi);` and `ReleaseHandle(g_adx);` lines.
- In `OnTick`, replace the `if/else` with `OnBarRSI2(bar, close, atr);`.
- In `ea_profiles.PROFILES["MeanRev_EA"]`, delete `InpEntryMode`, `InpBBPeriod`, `InpBBDev`, `InpRSIPeriod`, `InpRSILow`, `InpADXPeriod` and `InpADXMax`.
- In `meanrev_selfcheck.py`, delete the `"bb"` entry from `MODES`, and the `"InpEntryMode"` key from `MODES["rsi2"]`.

In both cases, update the header comment block so it lists only the shipped strategy.

- [ ] **Step 2: Set the winner's values as compiled defaults**

For each key in `decision.json → modes[winner].sets` except `InpEntryMode`, change that input's default in `mql5/MeanRev_EA.mq5` and its value in `ea_profiles.PROFILES["MeanRev_EA"]` to the decided value. If Task 7's `risk_check` scaling was confirmed by the user, set `InpRiskValue` to the confirmed percentage in `mql5/EACore.mqh` and in both profiles. Also change the MACD live input on the VPS during Task 9, but leave `MACD_Cross_EA.mq5` itself untouched.

- [ ] **Step 3: Compile, then run the Python tests and the harness**

Run:
```bash
cd /Users/lyudmilnikodimov/Projects/MBT && scripts/install_meanrev.sh && python3 -m pytest tests -q && cd scripts && python3 meanrev_selfcheck.py --mode <winner>
```
Expected: compile `ok: True` with 0 warnings, all pytest tests pass, and `11/11 checks passed`. Update `test_profiles_have_core_inputs` and `test_merge_inputs_*` in `tests/test_ea_profiles.py` only if they referenced a deleted input.

- [ ] **Step 4: Parity, comparing the shipped EA with the Stage B result**

Quit the MT5 GUI. Run:
```bash
cd /Users/lyudmilnikodimov/Projects/MBT && ~/.local/bin/mbt-wine-py scripts/macd_tester.py single --expert MeanRev_EA \
  --name ship_parity --model real_ticks --from 2022-01-01 --to 2026-10-01 2>/dev/null | python3 -c \
  "import sys,json; r=sys.stdin.read(); print(json.loads(r[r.find('{'):])['metrics']['net_profit'])"
```
Expected: exactly `stage_b_<winner>.json → runs["2022-2026"].metrics.net_profit`, to the cent. If risk was rescaled in Step 2, compare against a Stage B rerun at the new risk instead. Any difference means Step 1 or Step 2 changed behaviour, so fix it before committing.

- [ ] **Step 5: Commit and push**

```bash
cd /Users/lyudmilnikodimov/Projects/MBT && git add mql5/MeanRev_EA.mq5 mql5/EACore.mqh scripts/ea_profiles.py scripts/meanrev_selfcheck.py tests/test_ea_profiles.py
git commit -m "feat: ship MeanRev_EA v1.00 with the <winner> mode and its validated defaults

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 9: Demo deployment next to MACD (user-assisted)

**Files:** none. These are operational steps on the MT5 GUI and the MQL5 VPS.

**Interfaces:**
- Consumes: the compiled `MeanRev_EA.ex5` in the Advisors folder (Task 8), and the demo bot account 3000110062 with VPS London LD6 24.
- Produces: both EAs running on the VPS, confirmed by their `Ready check` lines.

- [ ] **Step 1: Attach on the Mac**

The user logs into account 3000110062 and opens a second XAUUSD **H1** chart, keeping MACD's chart open. They drag `MeanRev_EA` onto it, tick **Allow Algo Trading** on the Common tab, press **Reset** on the Inputs tab, and confirm the magic number is 240819.

- [ ] **Step 2: Check the Mac's Experts tab**

Expected lines: `MeanRev_EA v1.00 started on XAUUSD PERIOD_H1, magic 240819`, the `Inputs:` line with the decided values, `Guards: max spread 150 pts, retry 30 min | daily stop -4.0% | monthly stop -12.0% | trade log on`, and a `Ready check:` line ending in `guards ok`.

- [ ] **Step 3: Sync with both charts open**

The user turns Algo Trading on, right-clicks VPS London LD6 24, chooses **Synchronize Experts, Indicators**, then turns Algo Trading off on the Mac.

- [ ] **Step 4: Verify on the VPS**

In the VPS Journal, the Terminal log must show `'3000110062': 2 charts, 2 EAs`. In the Experts log there must be a `Ready check` line from **each** EA: `MACD_Cross_EA v1.42` and `MeanRev_EA v1.00`.

- [ ] **Step 5: Forward check after about 2 to 3 weeks**

Export the demo account history for MeanRev_EA's magic 240819. Run the same weeks in the tester:
```bash
cd /Users/lyudmilnikodimov/Projects/MBT && ~/.local/bin/mbt-wine-py scripts/macd_tester.py single --expert MeanRev_EA --name fwd_<from>_<to> --model real_ticks --from <from> --to <to>
```
Compare entries, stops and lot sizes trade by trade. Small slippage differences are fine. Missing or extra trades must be explained before Darwinex Zero deployment.

- [ ] **Step 6: Live removal rules (spec section 7)**

Check monthly. Remove `MeanRev_EA` (close its chart on the Mac, re-sync the VPS, MACD keeps running) if its own drawdown exceeds 15%, it has three losing months in a row, or its live trades diverge from a tester replay of the same weeks.
