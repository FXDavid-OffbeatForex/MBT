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

## Outcome (2026-10-06): closed
Self-check 11/11 (EURUSD real ticks 2024-Q1: hedged start, Fibonacci lots and gaps, exact triggers and lock
arithmetic from the agent log, cycle restarts, A/B on both progressions, account_risk kill switch, red alert).
Stage A, author defaults, 1-minute OHLC, 100k, min_lots 0.01 (`reports/icarus/stage_a.json`, `.log`):

| symbol | from | net $ | PF | legs | baskets | end-of-test $ | max equity DD $ | k | 2018/19-21 %/yr | 2022-24 %/yr | longest basket | gate A |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | 2018 | -6,964 | 0.17 | 424 | 217 | -7,436 | 7,086 | 1.13 | +0.1 | -2.8 | 818 d | fail |
| USDJPY | 2019 | -8,449 | 0.27 | 1,177 | 604 | -9,566 | 9,672 | 0.83 | +0.3 | -2.6 | 173 d | fail |
| GBPUSD | 2019 | +755 | 1.10 | 3,903 | 2,083 | -2,864 | 5,130 | 1.56 | +1.1 | -0.7 | 646 d | fail |
| EURJPY | 2019 | -7,124 | 0.32 | 1,542 | 820 | -8,235 | 9,355 | 0.86 | +0.3 | -2.3 | 132 d | fail |
| USDCHF | 2024 | -818 | 0.44 | 238 | 117 | -998 | 1,178 | 6.79 | n/a | -5.6 | 216 d | n/a |
| EURGBP | 2024 | -1,039 | 0.08 | 26 | 12 | -1,072 | 1,158 | 6.91 | n/a | -7.2 | 110 d | n/a |

- No symbol reaches +10% a year in either regime at the lot scale where the worst drawdown is 8%; the best
  regime anywhere is GBPUSD 2019-21 at +1.1% a year. Gate A fails for all four scored symbols; the one-year
  symbols are negative too.
- The pattern is the grid signature: closed baskets are net positive on every symbol (EURUSD +472, USDJPY
  +1,117, GBPUSD +3,619, EURJPY +1,111 over 6-7 years at 0.01 lots), and one side freezes at 6 legs in each big
  trend (EURUSD 1.25 -> 0.95, USDJPY 103 -> 160) and carries the whole loss: the longest EURUSD basket lasted
  818 days. Even counting only closed baskets, the return at 8% worst drawdown is +0.1 to +0.9% a year.
- Holdout and sensitivity were not run (nothing passed). Icarus is closed under rule 5; no retuning.
