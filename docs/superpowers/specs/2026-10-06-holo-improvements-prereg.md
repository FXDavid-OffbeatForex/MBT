# HoLo_EA improvement screen: pre-registration (2026-10-06)

Written and committed before any of these variants were run. Results that do not follow these rules are not
selection evidence.

## Data
- XAUUSD, Darwinex-Demo, real ticks, 2019-01-01 to 2025-01-01. 2025-01-01 to 2026-10-01 stays untouched until a
  single finalist is chosen.
- Setups: H1 levels / M30 trigger, H4 levels / M15 trigger, D1 levels / M5 trigger.
- One excursion-logged run per setup and entry mode (6 runs): no take-profit, no break-even, 1% risk, all other
  inputs at the v1.00 defaults.

## Entry modes
- TOUCH (PDF, current): sell when Bid falls to the highest open (HO), buy when Ask rises to the lowest open (LO).
- CLOSE_CONFIRM (idea 4): after the setup arms, sell at market on the first new trigger bar whose previous trigger
  candle (opened at or after arming) closed below HO; buys mirror with a close above LO. Same stop (day extreme),
  same cancellation rules, no late-entry limit.

## Filters, stops, targets (replayed offline from the excursion log)
- Trend (idea 1): none | H4 EMA200 | H12 SMA250 (the filters of the two passing EAs). With-trend only: buy when the
  Bid is above the average of the last closed bar, sell when below.
- Room (idea 3): Area of Interest width at entry (day extreme to the level) / ATR(14) of the level timeframe, last
  closed bar. none | drop the lowest tercile | drop the highest tercile. Terciles come from that setup's own 2019-21
  trades and are applied unchanged to 2022-24.
- Stop (idea 2): original (day extreme + spread) | 0.5 x original | the nearer of the original and 1 x ATR(14) of
  the trigger timeframe. Only tighter-or-equal stops can be replayed. Costs scale with the stop: cost_R x d / s.
- Target: 1, 2 or 3 R of the chosen stop. Trades that hit neither exit at the session flat.

## Selection rule
- Grid per setup and mode: 3 trend x 3 room x 3 stop x 3 target = 81 cells; 486 cells in total.
- A cell qualifies if PF >= 1.15 in both 2019-21 and 2022-24, with >= 60 trades in each period.
- Plateau: the qualifying cell's neighbours along the stop and target axes average PF >= 1.10 in both periods.
- Finalist: the qualifying plateau cell with the highest min(PF 2019-21, PF 2022-24). Ties: more trades.
- With 486 cells some will pass by chance. The finalist is implemented in the EA, re-run as a real backtest on
  2019-2024 (must match the replay within 0.10 PF), then run once on the 2025-26 holdout (must keep PF >= 1.15).
  Only then does it go to prop_mc. If no cell qualifies, HoLo is closed.
