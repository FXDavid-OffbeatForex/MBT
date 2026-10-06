# EuroRiddle_EA: tradable versions and test plan (pre-registered 2026-10-06)

## Source rules and why they need a translation
"Watch until the high or low for the day has been hit, then fade it; 15 pip stop, 25 pip target, one trade a day,
EURUSD, day from 17:00 to 16:59 EST." A day's high or low is only known after the day ends, so the literal rule
needs future data (the source of its "99.9% accuracy"). The "1-minute confirmation" adds nothing (a price high is
the same on every timeframe) and MACD/RSI are not used by any rule. The versions below replace "the day's
high/low" with something known at the moment of entry.

## Common to every version
- EURUSD, Darwinex-Demo. Trading day = 17:00-17:00 New York = server midnight (server = NY + 7 h all year).
- At most one entry per trading day: the first signal that passes the filter.
- Market entry on the signal tick. Stop 15 pips, target 25 pips. Risk 1% of balance.
- Anything still open goes flat at the broker's session close for that weekday minus 10 minutes; no entries after.
- Spread guard: no entry while the spread is above 2 pips.

## Versions (InpMode)
- A, PREV_DAY: short when the Bid crosses up through the previous day's high; long when it crosses down through
  the previous day's low.
- B, RANGE_EXHAUST: once today's range (high - low) is at least 1.0 x ADR(20) (average high-low of the previous
  20 completed days), short every new day high and long every new day low (the first one taken).
- C, LATE_EXTREME: from 12:00 EST, short the first new day high or long the first new day low.

## Confirmation filter (InpFilter)
- NONE.
- RSI: RSI(14) on H1, last closed bar, >= 70 for shorts and <= 30 for longs.
- MACD: MACD(12,26,9) on H1, histogram (main - signal) of the last closed bar below the bar before it for shorts
  (upward momentum fading), above it for longs.

## Test plan
- Stage A: 3 versions x 3 filters = 9 configurations, 1-minute OHLC, on 2015-2018 and 2019-2022 separately.
- Gate A: PF >= 1.15 with >= 100 trades in both periods.
- Sensitivity (only for configurations passing Gate A): B with ADR fraction 0.8 and 1.2; C with cutoff 10:00 and
  14:00 EST; RSI with 65/35 and 75/25. A neighbour below PF 1.10 in either period fails the configuration.
- Holdout, once, for the best surviving configuration only: real ticks 2023-01-01 to 2026-10-01; PF >= 1.15.
  Then prop_mc with FTMO 2-step rules.
- If nothing passes Gate A, the strategy is closed. Stop and target stay at the rule's 15/25 pips throughout.
