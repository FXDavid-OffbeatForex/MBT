# 4-Hour Range Scalping Strategy — port spec

Source: `Transcript - The BEST 5 Minute Scalping Strategy Ever.pdf` (YouTube transcript,
~15 min). Implemented as `mql5/FourHourRangeScalper.mq5`.

Every rule below cites the transcript timestamp it comes from. Rules marked
**[UNSTATED]** are decisions the transcript does not make; each one is an EA input
so it can be tested rather than assumed.

---

## 1. The instrument of the strategy: one candle

> "for this strategy the only thing you need is one candle ... we're using what's
> called the 4hour range which is simply the range between the high and low of the
> first 4hour candle that forms during the day" — [00:00:38]–[00:00:55]

### 1.1 Which candle
- The **first 4-hour candle of the day**, where "the day" is a **New York calendar
  day**: "go to the time zone setting ... and select New York time ... this step is
  very important because we want the first 4-hour candle to be based specifically on
  the New York time" — [00:01:36]–[00:01:47].
- On a NY-aligned chart the first 4H candle of the day spans **NY 00:00 → 04:00**.
- It must be **fully closed** before the range is used: "make sure that the first
  4hour candle has fully closed before marking the range" — [00:02:05].
- The two levels are extended **to the end of that day** — [00:01:59].

### 1.2 Why this cannot be read off an H4 chart in MT5
MT5 aligns H4 bars to the **broker server** clock (00:00/04:00/08:00/… server time).
The broker here is Darwinex-Demo on **EET/EEST** (UTC+2 winter, UTC+3 summer, EU DST
rules), so NY 00:00 lands on server **07:00** most of the year — never an H4 boundary.

The EA therefore builds the range by **aggregating the M5 bars** whose New York
timestamp falls in the window. See §6 for the clock.

---

## 2. Step 2 — the scalp setup (5-minute chart)

> "we go down to the 5-minut time frame to look for scalp setups ... the setup we're
> looking for is when the 5-minute candle closes outside of the range either the range
> high or range low but again the key here is the candle must fully close outside so
> wicks alone don't count" — [00:02:46]–[00:03:04]

> "after the breakout we then wait for price to re-enter and close back inside the
> range again but make sure that all of this happens within the same day as the 4hour
> range we marked" — [00:03:04]–[00:03:16]

So the trigger is a two-part sequence, both parts confirmed **on the close of an M5
bar**:

1. **Breakout** — an M5 bar *closes* beyond a range edge. A wick through the edge is
   explicitly not enough: "the candle didn't actually close below it only the wick
   that means the setup still isn't valid" — [00:03:54].
2. **Re-entry** — a later M5 bar *closes* back inside the range.

Both must occur inside the same NY day as the range.

---

## 3. Step 3 — entry, stop, target

### 3.1 Direction (fade the breakout)
- Broke **above** the range high, then re-entered → **SHORT** — [00:03:16].
- Broke **below** the range low, then re-entered → **LONG** — [00:04:19].

### 3.2 Entry price
The close of the re-entry candle. The transcript never names another entry price and
every worked example enters immediately on that close.

### 3.3 Stop loss
> "for the stop loss we place it at the exact high of the breakout move" — [00:03:22]
> "we place it at the exact low of the breakout move" — [00:04:26]

The later worked examples narrate this loosely as "stop loss goes at the highs" /
"at the range high". Read together with the two explicit statements above, the rule is
**the extreme of the breakout excursion** — the highest high (shorts) or lowest low
(longs) reached while price was outside the range. On a small breakout that extreme
sits a whisker beyond the range edge, which is why the loose narration reads the same
way on the chart.

`InpSLAnchor` lets you test the literal alternative (the range edge itself).

### 3.4 Take profit
> "for the take-profit aim for at least two times the stop-loss size" — [00:03:29]

TP = entry ± 2 × |entry − SL|. Fixed 2R. `InpRR`.

### 3.5 The "breakout was too large" exception — **[UNSTATED, discretionary]**
> "the breakout was quite huge which means if we were to place a stop loss at the low
> it would be a very large stop-loss so for these types of scenarios instead of using
> the exact low of the range I looked for the nearest key level to place the stop loss
> in this case there was a small resistance level" — [00:06:54]–[00:07:10]
> (again at [00:12:02] and [00:12:39], using "an order block")

"Nearest key level" is a human judgement call with no stated measurement. The EA
exposes `InpOversizedSL` with mechanical proxies and **defaults to `OVER_ALLOW`** —
i.e. it takes the wide stop, exactly as the stated basic rule says. Anything else
would quietly make the EA better than the strategy being tested.

---

## 4. Trade management across the day

- **Multiple trades per day are allowed**: "as long as we're still within the same
  trading day as the 4hour range we marked we can still take more trades if another
  valid setup shows up" — [00:04:38]–[00:04:49]. After an entry the EA re-arms and
  watches for the next breakout.
- **No re-entry, no trade**: "price is trading well outside of our 4hour range however
  it didn't manage to re-enter again until the end of the day that means for this
  trading day there were no more valid entries" — [00:04:49]–[00:05:00].
- **[UNSTATED]** whether a new setup may be taken while a position is still open. The
  worked examples are all sequential. Default: one position at a time
  (`InpOnePositionAtATime`), which also matches how MBT's Python signal-replay
  backtest skips overlapping signals — so the two engines stay comparable.
- **[UNSTATED]** whether an open position is closed at the end of the NY day. The
  transcript only ever stops *entries*. Default: leave it to SL/TP
  (`InpCloseAtDayEnd = false`).

---

## 5. Claimed results (the thing to beat)

| Market | Trades | Wins | Losses | Net | Win rate |
|---|---|---|---|---|---|
| Crypto (BTC) — [00:08:21] | 7 | 5 | 2 | +8R | ~72% |
| Forex (EURUSD) — [00:11:29] | 6 | 5 | 1 | +9R | ~83% |
| Gold (XAUUSD) — [00:14:26] | 10 | 6 | 4 | +8R | 60% |

The transcript itself flags the sample as far too small: "if we want to get a more
accurate performance we would need to back test using way more than just seven
trades" — [00:08:41]. That is exactly what this port is for.

---

## 6. The New York clock (implementation note)

The EA converts each bar's server timestamp to New York wall time in two hops, with
no reliance on `TimeGMT()`/`TimeLocal()` (unreliable in the Strategy Tester):

1. **server → UTC** using the broker's winter GMT offset (`InpServerGMTOffsetWinter`,
   default `2`) plus its DST rule (`InpServerDST`, default EU).
2. **UTC → New York** using the US rule: EDT (UTC−4) from the 2nd Sunday of March
   02:00 EST to the 1st Sunday of November 02:00 EDT, else EST (UTC−5).

Both hops are computed from a days-from-civil calendar, so there are no strings, no
tz database and no platform dependency.

This matters: the US and EU switch on different dates, so server→NY is **−7h for most
of the year but −6h for ~25 days** (the ~3 weeks in March when the US has sprung
forward and the EU has not, plus the ~1 week in late October/early November when the
EU has fallen back and the US has not). On those days the range window sits at server
06:00–10:00 rather than 07:00–11:00. A fixed −7 offset would mis-mark the range on
every one of them.

The algorithm was validated by transliterating it to Python and sweeping **every hour
of 2017–2027 (96,397 hours)** against the IANA tz database: **zero mismatches**. The
only hours skipped are the 11 non-existent server stamps at the EU spring-forward,
which cannot appear in bar data.

`InpVerbose` prints the resolved mapping and each day's range so it can be spot-checked
against a NY-timezone chart, and `OnInit` warns if the configured offset disagrees with
the terminal's own `TimeGMT()`.

---

## 7. Known gaps between this EA and the transcript

1. The "nearest key level" stop (§3.5) is discretionary; the default takes the literal
   wide stop instead.
2. A bar that closes from above the range high straight through to below the range low
   is not treated as a re-entry (it did not close *inside*); the EA flips the breakout
   side instead. The transcript never shows this case.
3. Price already sitting outside the range at the moment the range becomes valid is not
   an armed breakout by default (`InpAllowPreArmedOutside = false`) — the transcript
   always shows the breakout happening from inside, during the day.
4. The transcript's crypto examples cannot be reproduced on this broker: Darwinex-Demo
   carries no crypto symbols. EURUSD and XAUUSD both have M1 history back to 2017.
