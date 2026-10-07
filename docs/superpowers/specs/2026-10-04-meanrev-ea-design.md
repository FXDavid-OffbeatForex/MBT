# MeanRev_EA: a mean-reversion companion to MACD_Cross_EA on XAUUSD

Date: 2026-10-04
Status: approved design, pending spec review

## 1. Goal and context

The Darwinex Zero account (expected around 1 November 2026, after the trading course) will run **two EAs on one account, forming one DARWIN**:

- `MACD_Cross_EA` v1.42 (magic 240817): H1 trend-following, live on the demo VPS. It holds winners for days and was weak in choppy stretches such as 2024.
- `MeanRev_EA` (new, magic 240819): H1 mean reversion on XAUUSD.

The new EA has two jobs:

1. **Profitable on its own** over the long run, validated out of sample.
2. **A good fit next to MACD.** It should add trades so Darwinex calibration (25 risk-equivalent decisions over at least 15 trading days) finishes faster than MACD alone (median about 100 days). It should also earn when MACD doesn't, so monthly returns, which drive the DarwinIA Silver rating, become smoother.

Decisions already made with the user:

- Run both EAs on the same account (not a replacement, not a second DARWIN).
- Mean reversion as the style.
- Equal risk weight per trade (start at 1% each), scaled down together if the combination is too risky.
- A two-candidate shoot-out: build two entry modes, validate both, ship only the winner.

## 2. Architecture

### 2.1 Files

| File | Purpose |
|---|---|
| `mql5/MeanRev_EA.mq5` | The new EA: inputs, the two entry modes, their exits, OnTick wiring |
| `mql5/EACore.mqh` | Shared, strategy-free parts copied as-is from MACD_Cross_EA v1.42 |
| `scripts/macd_tester.py` | Gains `--expert` and one default-input table per EA |
| `scripts/combine_eas.py` | New: combines two EAs' tester trade logs into one account view |
| `scripts/meanrev_validate.py` | New: runs the shoot-out (Stage A, Stage B, gates, selection) |

`MACD_Cross_EA.mq5` is **not changed**. It is validated and live. It may move onto `EACore.mqh` later, only after its own guards-off match-to-the-cent check against v1.42.

### 2.2 EACore.mqh contents (copied from v1.42 without behaviour changes)

- Lot sizing from risk % of balance and the stop distance (`CalculateLots`), with broker limits and the free-margin cap.
- Spread guard with in-candle retry of a blocked signal (`InpMaxSpreadPoints` = 150, `InpSpreadWaitMin` = 30).
- Daily loss stop (−4% of day-start balance: close own positions, no entries until the next server day) and monthly loss stop (−12% of month-start balance: no new entries until the next month).
- Closed-trade CSV log with the RiskGuard.mqh columns (read by `prop_mc.py`).
- "Ready check" start-up line and hourly `Bar …` status line.
- Restart-safe last-processed-bar tracking (terminal global variable keyed by symbol, timeframe and magic) and own-position discovery by magic.

### 2.3 Shared guards across both EAs

The daily and monthly stops compare **account equity** with the day's or month's starting balance. Both EAs see the same equity, so a bad day caused by either one blocks new entries for both, with no coordination code. On a daily stop each EA closes only its own positions.

## 3. Strategy logic

Both modes evaluate the **closed** H1 candle (shift 1) once per new candle and enter at market on the next one. Common rules:

- One position at a time for this EA (magic 240819). A new signal is ignored while a position is open.
- Risk `InpRiskValue` = 1.0% of balance per trade.
- A hard stop at entry: `InpSLATR` × ATR(14) of the closed candle.
- Time limit: a position still open after `InpMaxBars` H1 candles, counted from its real open time, is closed at market.
- No break-even and no trailing stop.

### 3.1 Mode BB_FADE

- **Long:** close[1] < lower Bollinger Band(20, `InpBBDev`), RSI(14)[1] < `InpRSILow`, and ADX(14)[1] < `InpADXMax`.
- **Short:** close[1] > upper band, RSI(14)[1] > 100 − `InpRSILow`, ADX(14)[1] < `InpADXMax`.
- **Take-profit:** the middle band value at entry, fixed on the order so the broker holds it.
- **Stop:** `InpSLATR` × ATR. **Time limit:** `InpMaxBars`.
- `InpRSILow` = 50 effectively disables the RSI condition. `InpADXMax` = 100 effectively disables the ADX filter.

### 3.2 Mode RSI2_PULLBACK

- **Long:** close[1] > EMA(`InpTrendPeriod`)[1] and RSI(2)[1] < `InpRSI2Entry`.
- **Short:** close[1] < EMA[1] and RSI(2)[1] > 100 − `InpRSI2Entry`.
- **Exit:** at the first new candle where RSI(2)[1] > `InpRSI2Exit` (long) or < 100 − `InpRSI2Exit` (short). No fixed take-profit.
- **Stop:** `InpSLATR` × ATR. **Time limit:** `InpMaxBars`.

### 3.3 Tuning grids (2024-01-01 to 2026-10-01, 1-minute OHLC, numeric inputs only)

| Mode | Input | Values |
|---|---|---|
| BB_FADE | `InpBBDev` | 2.0, 2.5 |
| | `InpRSILow` | 25, 30, 50 |
| | `InpADXMax` | 15, 20, 25, 100 |
| | `InpSLATR` | 1.0, 1.5, 2.0, 3.0 |
| | `InpMaxBars` | 6, 12, 24 |
| RSI2_PULLBACK | `InpRSI2Entry` | 5, 10, 15 |
| | `InpRSI2Exit` | 50, 70 |
| | `InpTrendPeriod` | 100, 200 |
| | `InpSLATR` | 1.5, 2.0, 3.0, 4.0 |
| | `InpMaxBars` | 12, 24, 48 |

That is 288 combinations for BB_FADE and 144 for RSI2_PULLBACK. The entry mode is fixed per launch, never ranged (enum and bool ranges blow up MT5 grids). Every fixed input is pinned explicitly as `v||v||1||v||N`.

## 4. Validation and selection

### 4.1 Stage A: tuning

- Survivors: at least 150 trades, profit factor above 1, equity drawdown of 20% or less.
- Rank survivors by profit factor × recovery factor.
- Neighbour check: for the top-ranked combination, change each input by one grid step in each direction. The neighbours that exist must average a profit factor of at least 1.10. If not, move down the ranking to the first combination that passes.

### 4.2 Stage B: real-tick validation

Run each mode's Stage A winner on real ticks for 2022, 2023, 2024, 2025 and 2026 to 1 October, separately. Use 100,000 USD, 1:20 leverage and 1% risk, matching the MACD runs. 2022 and 2023 were not used in tuning.

### 4.3 Gates (all must pass)

| # | Gate | Threshold |
|---|---|---|
| 1 | Out-of-sample profit, 2022 + 2023 combined | Net above 0 and profit factor at least 1.15 |
| 2 | In-sample profit on real ticks, 2024–26 | Profit factor at least 1.20 |
| 3 | No disaster year | No calendar year below −10% |
| 4 | Different from MACD | Monthly return correlation with MACD v1.42 of 0.3 or less over 2022–26 |
| 5 | Better together | MACD + mode at equal weight beats MACD alone on return-to-drawdown over 2022–26, and has a lower median calibration time |

### 4.4 Selection

- If both modes pass, the lower correlation with MACD wins. On a tie, the higher combined return-to-drawdown wins.
- If neither passes, nothing ships. Report which gates each mode failed and why. The account stays MACD-only. The gates are not loosened after seeing results.
- The losing mode is removed from the EA before release, so the shipped EA contains one strategy.

### 4.5 Combined risk level

`combine_eas.py` merges the two tester trade logs (MACD v1.42 and the winner, same periods, real ticks) by close time into one daily P&L series on a shared balance. It reports:

- correlation of daily and monthly returns;
- combined net profit, maximum drawdown, worst day and worst month;
- median calibration days, using the risk-equivalent weighting already used for MACD: exposure = leverage × √hours held; weight = √(exposure ÷ largest exposure so far); done at 25 weighted decisions and at least 15 trading days.

If the combined worst day is beyond −3% or the maximum drawdown beyond 20%, both EAs' risk is scaled down by the same factor (for example to 0.75%) until both limits hold. Changing MACD's live risk input is confirmed with the user first.

Known approximation: MT5 cannot run two EAs in one test, so the merged curve ignores the interaction of shared guards and compounding. The loss stops never fired in MACD's 2022–26 real-tick runs, so this effect is expected to be small.

### 4.6 Built-in checks

- Determinism: two identical guards-off runs give identical results.
- Forced triggers: each guard (daily stop, monthly stop, spread retry) fires once under tight limits, as for v1.42.
- Time limit: a forced short `InpMaxBars` closes positions at the expected candle.

## 5. Deployment

1. **Demo first.** Put `MeanRev_EA` on its own XAUUSD H1 chart on the demo bot account 3000110062, next to MACD. Sync both charts to the VPS (London LD6 24). Every sync must happen with both charts open on the Mac.
2. **Forward check.** After about 2 to 3 weeks, compare live trades with a tester replay of the same weeks: entries, stops and lot sizes should match apart from small slippage.
3. **Darwinex Zero.** Deploy both EAs on the Zero account from day one, so both count toward calibration. Freeze settings until calibration completes.

## 6. Error handling

- Indicator or ATR data not ready: skip the signal, leave the candle unprocessed, retry on the next tick.
- Order failure: log the broker reason code; no blind retry.
- Time-limit close failure: retry every tick.
- Restart: positions re-discovered by magic; the time limit uses the position's real open time.

## 7. Removal rules (live)

Remove `MeanRev_EA` from the account, leaving MACD running alone, if any of these happen:

- its own drawdown exceeds 15%;
- three losing months in a row;
- live trades diverge from a tester replay of the same weeks.

Removal means closing its chart on the Mac and re-syncing the VPS.

## 8. Out of scope

- Changing MACD_Cross_EA's logic or moving it onto `EACore.mqh` now.
- Break-even or trailing stops for the mean-reversion EA.
- Symbols other than XAUUSD; timeframes other than H1.
- Session-fade strategies (rejected: the earlier Asian mean-reversion EA found nothing).
