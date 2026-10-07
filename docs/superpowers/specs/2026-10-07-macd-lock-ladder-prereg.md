# MACD_Cross_EA profit-lock ladder screen: pre-registration (2026-10-07)

Written before any of these settings were run. The user asked to explore moving the stop to secure profit only;
trailing stays at the v1.42 rule (from +1.0%, 4 x ATR(14, H1)).

## Grid (1 R = the 0.5% initial stop)
- Step 1, protect: trigger 0.5 / 0.75 / 1.0 / 1.5% (1, 1.5, 2, 3 R) x lock-in 0.1 / 0.25% (+0.2, +0.5 R) = 8 settings.
  0.5% / 0.1% is the live v1.42 rule.
- Step 2, secure: off, or trigger 2.0 / 2.5 / 3.0% (4, 5, 6 R) x lock-in 1.0 / 1.5% (+2, +3 R) = 7 settings.
- Step 3 off. 8 x 7 = 56 settings. Everything else at the v1.43 defaults (= v1.42).

## Screen
- MT5 optimizer, 1-minute OHLC (gold history is clean from 2018), separately on 2019-2021 and 2022-2024.
- Baseline = step 1 0.5/0.1 with step 2 off, from the same optimization.
- A setting qualifies if, in both periods, its profit factor and net profit are at least the baseline's.
- Plateau: its neighbours (one step along each of the four axes) average a profit factor no more than 0.03 below
  the baseline's in both periods.
- Finalist: the qualifying plateau setting with the highest smaller-of-the-two PF ratios against the baseline.

## Confirmation (finalist only)
- Real ticks 2019-01 to 2026-10, single run, against the baseline run of the same day (2026-10-07).
- Adopt only if: prop_mc (FTMO 2-step) pass probability is at least the baseline's at 0.3% and 0.5% risk on both
  2019-26 and 2022-26 trades, and its 2025-26 net R (not used in the screen) is not below the baseline's.
- If nothing qualifies, the live exits stay unchanged.
