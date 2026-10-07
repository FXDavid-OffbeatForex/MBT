# Blessing_3 v3.9.6.23: MQL5 compile fix and test plan (pre-registered 2026-10-07)

## Source
`Blessing_3_v3.9.6.23.mq5` (third-party MQL5 port of the MT4 Blessing 3 grid EA, embedding fxsaber's MT4Orders of
2020-09-30). Fixed on 2026-10-06 so it compiles on the current MetaEditor (five `const T &` parameters taken by
value, one renamed error constant). Found and fixed on 2026-10-07 while preparing the test: the port's
`iMAOnArray` shim measured an empty local array, so the SmartGrid RSI moving average was always 0; with the
default `UseSmartGrid=true` sell baskets could never add a level while buys added without the filter. One token
changed (`ArraySize(__array)`), both copies.

With the compiled defaults (191 inputs in the MT5 build, all pinned in `scripts/ea_profiles.py`): direction from
EMA(100) on the chart timeframe (+-10 pips band), entries as two pending stop/limit orders around a 25-pip grid
line offset 5 pips (`B3Traditional`); grid adds by SmartGrid market orders 25/50/100 pips apart for levels 2-5 /
6-9 / 10-15 when RSI(14, M15) agrees with the direction, at most once per `EntryDelay` 2400 s; lots from balance
(`UseMM`, `LAF` 0.5, `Level` 7, `Multiplier` 1.4: first lot 0.20 at 100k, ladder x1, 2, 3, 4, 6, 8, 11, 15, 21,
29, 41, 57, 80, 112, 157); virtual basket take profit 50/100/200 pips on the first leg's size, break-even + 2 pips
from level 12; equity stop closes the basket at a floating loss of 50% of the portion (`MaxDDPercent`); no stop
loss per order; holiday shutdown 18 Dec - 1 Jan; everything but the equity stop runs once per chart bar
(`EnableOncePerBar`). `StopTradePercent` 10 halts the EA for good once balance falls 10% below a stepped
high-water mark (and calls MessageBox). Margin is checked per order against the portion, so grid depth depends
on leverage.

## Stage A (one run per symbol, author defaults)
- Chart H4 (the MAPeriod comment reads "H4 = 100, H1 = 400"), 1-minute OHLC, 100k USD, leverage 1:30 (Darwinex
  FX), Darwinex-Demo: EURUSD from 2018-01-01, USDJPY, GBPUSD, EURJPY from 2019-01-01, USDCHF and EURGBP from
  2024-01-01, all to 2024-12-31, one continuous run each.
- Inputs: compiled defaults except `StopTradePercent=100` (off): it is a permanent kill switch, like Icarus's
  account_risk, and would end a seven-year run at its first 10% loss. The 50% basket equity stop, the holiday
  shutdown and the balance-based sizing stay on, as the author shipped them.
- Lots scale with balance, so the drawdown in percent is deposit-invariant and the lot-scale question is "what
  LAF": k = 8 / max equity drawdown %, scaled return per year = (regime net / 100k / years) x k. This is a
  linear approximation of a compounding sizer; a symbol that passes on it must be confirmed by one re-run with
  `LAF` = 0.5 x k (drawdown <= 8%, +10%/yr in both regimes) before counting as a pass.
- Regimes by close time: 2018/19-2021 and 2022-2024. Trades are rebuilt from the report's Deals table (the EA
  writes no trade log). Positions still open at the end are closed by the tester and counted in 2022-24.

## Gate A (per symbol)
1. The run completes: no stop-out.
2. k = 8 / max equity drawdown % (lot multiple for an 8% worst drawdown, the roadmap's total stop).
3. Pass if scaled return >= +10% a year in BOTH regimes, then confirmed by the LAF re-run above. USDCHF/EURGBP have
   one year and one regime: report only, cannot pass.
4. A confirmed symbol gets, once: the holdout 2025-01-01..2026-10-01 (real ticks where available: EURUSD, USDJPY,
   EURJPY) at the confirmed LAF, same test; prop_mc on the rebuilt trades; and a sensitivity check at the same
   LAF with GAF 0.8/1.2, Multiplier 1.3/1.5, MaxTrades 12/15->18, TP_SetArray x0.8/x1.2 (one change at a time):
   a neighbour failing the gate in either regime fails the symbol.
5. Nothing passes -> Blessing is closed; no retuning.

## Outcome
(to be filled after Stage A)
