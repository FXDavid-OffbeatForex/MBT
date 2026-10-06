# Icarus_EA: MQL5 port of Icarus 2.2 and test plan (pre-registered 2026-10-06)

## Source
`Icarus_2.2.mq4` (lifesdream.org; MaPi, HoHe, fxtrue, FX1079; "based on Super Money Grid v1.41") plus the author's
published rules. A no-indicator grid EA for FX majors: a buy grid and a sell grid run independently and both
start at launch (hedging account). Each side adds an averaging order when the money loss of its largest leg
passes grid_size x gap(n) pips; the lots grow by `progression`, the gaps by `gs_progression` (0 flat,
1 D'Alembert, 2 Martingale, 3 Fibonacci). When a side's profit (incl. commission and swap) exceeds take_profit x
pip value of the reference leg, a trailing lock arms at profit_lock x peak and the side closes once profit falls
below it. No stop loss per order; account_risk closes everything and stops when equity < (1 - account_risk) x
balance. Defaults: grid 20 pips, Fibonacci gaps and lots, take_profit 20, profit_lock 0.3, min_lots 0.01,
equity_warning 0.20, account_risk 1.0 (off), max_positions 6, unbalance_control off, max_spread 100 points.
Default cycle: lots 1,1,2,3,5,8 x 0.01 at gaps 20,20,40,60,100 pips; after leg 6 the side only waits for the lock.

## Port (mql5/Icarus_EA.mq5, execution and trade log from EACore.mqh)
- Trading logic only: no panel, lines, hot buttons, manual modes or GlobalVariable persistence (user decision).
- Red alert (equity drawdown from peak equity > equity_warning) blocks automatic adds and new cycles, as the
  published rules say; in the MQ4 code it only coloured buttons (user decision; off in Stage A anyway).
- MQ4 bugs fixed: account_risk closes everything in the tick it trips (the MQ4 ran the grid once more); a side
  that added a leg skips its lock check that tick (the MQ4 could close a basket on a stale snapshot and orphan
  the new leg into the next cycle); lock state resets whenever a side is flat; lots are normalised to the volume
  step and skipped above the symbol maximum; sort ties go by ticket.
- MT5 specifics: leg profit = position profit + swap + 2 x entry-deal commission (the tester charges half per
  deal, MT4 charged the round turn at open); EACore's daily/monthly stops compiled to 0 (the grid has no entry
  gate for them, they would close legs the grid reopens); spread guard = EACore InpMaxSpreadPoints 100.

## Stage A (one run per symbol, author defaults)
- Symbols the author names, 1-minute OHLC, 100k USD, 1:20, Darwinex-Demo: EURUSD from 2018-01-01 (pre-2018
  history leaks the day's extremes), USDJPY, GBPUSD, EURJPY from 2019-01-01, USDCHF and EURGBP from 2024-01-01
  (history start). All to 2024-12-31, one continuous run each (baskets carry over the regime boundary).
- Inputs: author defaults; equity_warning and account_risk off, EACore stops off, so that no %-of-account rule
  fires and P&L scales linearly with lots.
- Regimes scored from the trade log by close time: 2018/19-2021 and 2022-2024. End-of-test closes are floating
  loss, reported separately and counted in the 2022-24 net.

## Gate A (per symbol)
1. The run completes: no stop-out, no margin call at min_lots 0.01.
2. k = 8,000 / max equity drawdown in money (the lot multiple at which the worst drawdown is 8% of 100k, the
   roadmap's total-drawdown stop).
3. Pass if net x k >= 10,000 x years in BOTH regimes (+10% a year at that drawdown). USDCHF/EURGBP have one
   year and one regime: report only, cannot pass.
4. A passing symbol gets, once: the holdout 2025-01-01..2026-10-01 (real ticks where available: EURUSD, USDJPY,
   EURJPY), same inputs, same k, same +10%/yr test; prop_mc on the trade log; and a sensitivity check at the
   same k with grid 15/25, take_profit 15/25, profit_lock 0.2/0.4, max_positions 5/7 (one change at a time): a
   neighbour failing the gate in either regime fails the symbol.
5. Nothing passes -> Icarus is closed; no retuning. A daily-loss (5%) check needs an equity-per-day log and is
   deferred to the holdout stage.

## Outcome
(to be filled after Stage A)
