//+------------------------------------------------------------------+
//| FES_EA.mq5 — Fractal Elasticity System                           |
//|                                                                  |
//| Implements FRACTAL_ELASTICITY_SYSTEM_SPEC.md as a real MT5 EA,   |
//| driven by MT5's own Strategy Tester (MBT's run_strategy_tester)  |
//| rather than MBT's signal-replay backtest — see spec §10.2: the   |
//| signal-replay tool locates a signal's bar by matching `entry` to |
//| a bar CLOSE, which a stop-order entry offset from a close will   |
//| essentially never satisfy. Running as a real EA sidesteps that   |
//| entirely and lets MT5 fill the stop order the way it actually    |
//| would.                                                            |
//|                                                                  |
//| Reuses the two existing indicators via iCustom rather than        |
//| reimplementing their math (jrcMOD_Fractals for swing detection,  |
//| Stochastic_NoLastBar x2 for the fast/slow pair) — matching MBT's |
//| "never recalculate the indicator, use what it actually computed" |
//| philosophy.                                                       |
//|                                                                  |
//| Every input below with a spec-section comment maps to an open    |
//| item in FRACTAL_ELASTICITY_SYSTEM_SPEC.md §9 — they're exposed   |
//| as inputs specifically because the source document does not      |
//| state a definite value; tune per the user's own review.          |
//|                                                                  |
//| SIMPLIFIED-BY-DESIGN: H4 "divergence" confirmation (§3.3a) and    |
//| elastic divergence (§6) are collapsed to mechanical proxies       |
//| (structure sequence + stoch level, not full classic/hidden        |
//| divergence pattern classification) — see the comments on          |
//| CheckH4Confirmation() and CheckElasticDivergence() below. The     |
//| source itself treats these as visual/discretionary judgment       |
//| calls with no stated numeric rule (spec §9.1, §9.8), so a first   |
//| mechanical pass is necessarily an interpretation, not a port.     |
//+------------------------------------------------------------------+
#property strict
#include <SignalLogger.mqh>          // provides input SignalLogFile + LogSignal()/ResetSignalLog()
#include <Trade\Trade.mqh>

//======================= INPUTS =====================================

// --- Fractal swing detection (spec §1.2, §9.6 — period not confirmed from source) ---
input int    InpFractalPeriodD1     = 25;      // jrcMOD_Fractals period, D1 (code default; §9.6 open)
input int    InpFractalPeriodH4     = 25;      // jrcMOD_Fractals period, H4

// --- Dual stochastic pair (spec §1.1) ---
input int    InpSlowK = 14, InpSlowD = 3, InpSlowSlowing = 3;   // "slow" instance
input int    InpFastK = 5,  InpFastD = 3, InpFastSlowing = 3;   // "fast" instance
input double InpStochLevelHigh = 76.4;         // spec §9.2 — resolved: real indicator levels, not 80/20
input double InpStochLevelLow  = 23.6;

// --- Elasticity (spec §9.1 — measure confirmed, hard threshold contraindicated by source) ---
input double InpElasticityMinPoints = 0.0;     // |fastStoch-slowStoch| minimum; 0 = off (recorded only)

// --- Order placement (spec §3.6) ---
input double InpStopOffsetPips = 10.0;         // buy/sell stop distance from candle3 close
input double InpSLBufferPips   = 3.0;          // spec §9.5 — buffer size not confirmed in prose

// --- HH/LL Failure exception (spec §5.4) ---
// "I like to see as little rejection size on its close as possible. If this
// candle has a considerable wick on its close I wait for the V shape" (page
// 11) — the close must sit decisively near the candle's extreme on the
// closing/entry side (small distance between close and that extreme) for
// the fast-path entry to trigger; a larger wick there falls back to the
// standard V-shape. Ratio = distance(close, decisive extreme) / full range.
input double InpRejectionMaxCloseRatio = 0.3;

// --- Pip size override (spec §10.2 "compounding issue" — DE30/XAUUSD) ---
// 0.0 = auto-detect via the 3/5-digit forex heuristic below (PipSize()).
// The heuristic is calibrated for forex quoting conventions and does not
// generalize to indices/metals — for DE30/XAUUSD-style instruments, set
// this explicitly (e.g. 1.0) rather than relying on auto-detection.
input double InpPipMultiplier = 0.0;

// --- Position sizing (spec §6) ---
input double InpLots                 = 0.01;
input bool   InpDoubleLotsOnElasticDiv = true; // "I double the lot size" — page 14

// --- Exit mode (spec §8 — user selected: build all four, selectable) ---
enum ENUM_EXIT_MODE
  {
   EXIT_FRACTAL_LEVEL,     // static TP at next opposing fractal level (spec §8 style 3)
   EXIT_SLOW_STOCH_CROSS,  // close when slow (14,3,3) D1 stoch crosses back through level (style 1)
   EXIT_FAST_STOCH_CROSS,  // close when fast (5,3,3) D1 stoch crosses back through level (style 2)
   EXIT_TRAILING_D1        // trail stop on consecutive same-direction D1 candles (style 4)
  };
input ENUM_EXIT_MODE InpExitMode = EXIT_FRACTAL_LEVEL;

// --- Stage staleness safeguards (not spec-derived — the source never states how
// long a discretionary trader would wait before giving up on a setup; §3.2/§9.8
// leave "logical S/R zone" and pullback timing undefined). Added after a 10-year
// backtest showed STAGE_PULLBACK has no way back to STAGE_IDLE if H4 confirmation
// never (re-)fires: StepPullbackAndElasticity() has no side effects on `stage`,
// so the only exit from STAGE_PULLBACK is a successful StepH4Confirmation() call —
// if price runs away and that setup's H4 conditions never recur, the EA parks on
// one stale breakoutLevel forever. Bounds below are grounded in the PDF's own
// worked examples (breakout-to-entry spans ~8-40 D1 bars across pages 2/19/1).
input int    InpMaxPullbackBars = 20;          // 0 = disabled; STAGE_PULLBACK -> abandon to STAGE_IDLE
input int    InpMaxVShapeBars   = 15;          // 0 = disabled; STAGE_H4_CONFIRMED -> demote to STAGE_PULLBACK
input int    InpPendingOrderExpiryBars = 20;   // 0 = GTC; belt-and-suspenders finite life on the stop order itself

// --- Order placement resilience (not spec-derived) -------------------------
// A D1 signal is acted on moments after the daily close. Two things routinely
// stop the stop order reaching the market, and the original code treated both
// as fatal (ResetSetup(), setup discarded):
//   * the new D1 bar has already gapped through the trigger -> "Invalid price"
//   * the session is shut (Fri->Sun rollover)               -> "Market closed"
// On EURUSD 2019-2026 that discarded 24 setups against 18 placed. Neither is a
// dead signal; both are "not right now".
input bool   InpRetryOrderPlacement = true;    // retry a rejected order instead of dropping the setup
input int    InpOrderRetryBars      = 3;       // D1 bars to keep retrying before giving up
input bool   InpMarketFillOnGap     = true;    // price already through the trigger -> take it at market
input double InpMaxChasePips        = 15.0;    // ...but only this far past it; beyond is chasing, not entering

// --- Misc ---
input int    InpMagicNumber = 20260902;
input bool   InpAllowH4PivotEntry = false;     // spec §3.4 tighter-risk variant; off by default (separate SL/entry geometry, kept simple)

//======================= STATE =====================================

enum ENUM_STAGE
  {
   STAGE_IDLE,             // watching for a structural breakout (spec §3.1)
   STAGE_PULLBACK,         // breakout seen; watching for pullback + elasticity (§3.2) + H4 confirmation (§3.3)
   STAGE_H4_CONFIRMED,     // H4 confirmed; watching D1 for the V-shape (§5)
   STAGE_PENDING_ORDER,    // stop order live; watching for fill or invalidation (§5.2)
   STAGE_AWAIT_PLACEMENT   // order computed but not yet accepted by the server — retrying
  };

struct FesSetup
  {
   ENUM_STAGE stage;
   bool       isLong;
   double     breakoutLevel;      // fractal HH/LL that was broken (§3.1)
   datetime   breakoutTime;       // D1 bar time the breakout close was confirmed — elastic-divergence
                                   // window anchor (§6) before a V-shape pivot exists
   double     pivotExtreme;       // V-shape candle2 low/high — invalidation level (§5.2), SL basis (§7)
   datetime   pivotTime;
   datetime   confirmTime;        // D1 bar time H4 confirmation succeeded — STAGE_H4_CONFIRMED staleness
                                   // clock. Deliberately NOT refreshed by §5.2's return-to-H4_CONFIRMED
                                   // (a repeatedly-invalidated setup must not get a repeatedly-extended
                                   // clock) — only StepH4Confirmation() itself sets this.
   int        vshapeCount;        // how many V-shape candles matched so far (0..3, or 4 under inside-bar exception)
   double     c1ext, c2ext, c3ext;// candle1/2/3 extremes while counting (§5.1)
   bool       usedInsideBarExc;   // §5.3
   bool       usedHHLLFailure;    // §5.4
   bool       usedH4Pivot;        // §3.4 — tighter-risk H4 LH/HL pivot entry
   bool       elasticDivergence;  // §6 — doubles lot size
   double     stopPrice, slPrice, tpPrice;
   ulong      pendingTicket;
   double     pendEntry, pendSL, pendTP, pendLots; // order stashed while STAGE_AWAIT_PLACEMENT
   datetime   pendSignalBar;                       // D1 signal bar that order belongs to
  };

FesSetup   g_setup;
CTrade     g_trade;

int        h_fracD1, h_slowD1, h_fastD1;
int        h_fracH4, h_slowH4, h_fastH4;

datetime   g_lastD1Bar = 0;
datetime   g_lastH4Bar = 0;
datetime   g_lastD1BarExit = 0;   // separate bar-gate for exit management (§8), independent of the
                                   // entry state machine's g_lastD1Bar — see StochCrossExit()/TrailD1()

// Consumed-fractal latch: deliberately OUTSIDE FesSetup so ResetSetup()'s
// ZeroMemory() never touches it. Without this, a timed-out STAGE_PULLBACK
// abandons to STAGE_IDLE, and StepBreakout() immediately re-arms on the exact
// same still-most-recent fractal (a level test, not an edge test — close1
// stays beyond hhPrice/llPrice for as long as the trend continues), producing
// an abandon/re-arm/abandon no-op loop instead of genuinely waiting for the
// NEXT distinct breakout. Latching by the fractal's own confirmed bar time
// means a given fractal can arm at most one setup, ever; a later, different
// (newer) fractal is unaffected and arms normally.
datetime   g_consumedHHTime = 0;
datetime   g_consumedLLTime = 0;

// trailing-exit state (spec §8 style 4)
double     g_trailAnchor = 0.0;

//======================= UTIL =======================================

double PipSize()
  {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(InpPipMultiplier > 0.0)
      return point * InpPipMultiplier; // explicit override — required for DE30/XAUUSD-style symbols (spec §10.2)
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   return (digits == 3 || digits == 5) ? point * 10.0 : point; // forex-only heuristic; doesn't generalize to indices/metals
  }

bool IsNewBar(ENUM_TIMEFRAMES tf, datetime &lastSeen)
  {
   datetime t = iTime(_Symbol, tf, 0);
   if(t != lastSeen)
     {
      lastSeen = t;
      return true;
     }
   return false;
  }

// Scan back from a safe shift (skip the still-unconfirmed tail, spec §1.2 lag warning)
// to find the most recent confirmed fractal marker of the given buffer (0=upper/HH,1=lower/LL).
bool LastConfirmedFractal(int handle, int bufferIdx, int period, double &price, datetime &t, ENUM_TIMEFRAMES tf)
  {
   // jrcMOD_Fractals force-adjusts an even period to odd before computing its
   // own `half` (see jrcMOD_Fractals.mq5 OnInit, fractalPeriodEff) — match it
   // exactly, or an even-period input desyncs our confirmation lag from the
   // indicator's actual one.
   int effPeriod = (period % 2 == 0) ? period + 1 : period;
   int half = effPeriod / 2;
   double buf[];
   ArraySetAsSeries(buf, true);
   int need = 300;
   if(CopyBuffer(handle, bufferIdx, half, need, buf) <= 0)
      return false;
   for(int i = 0; i < ArraySize(buf); i++)
     {
      if(buf[i] != EMPTY_VALUE && buf[i] != 0.0)
        {
         // The buffer value itself is an ATR-offset DISPLAY price for arrow
         // placement (jrcMOD_Fractals.mq5: UpperBuffer[i]=high[i]+atr*disp,
         // LowerBuffer[i]=low[i]-atr*disp), not the real candle extreme —
         // it only tells us a fractal is confirmed at this bar. Fetch the
         // actual high/low at that bar's time instead.
         int thatShift = i + half;
         price = (bufferIdx == 0) ? iHigh(_Symbol, tf, thatShift) : iLow(_Symbol, tf, thatShift);
         t     = iTime(_Symbol, tf, thatShift);
         return true;
        }
     }
   return false;
  }

// Second-most-recent confirmed fractal (for HL/LH sequence checks, spec §3.3b)
bool PriorConfirmedFractal(int handle, int bufferIdx, int period, datetime beforeTime, double &price, datetime &outTime, ENUM_TIMEFRAMES tf)
  {
   // Match jrcMOD_Fractals' own odd-period adjustment — see LastConfirmedFractal() above.
   int effPeriod = (period % 2 == 0) ? period + 1 : period;
   int half = effPeriod / 2;
   double buf[];
   ArraySetAsSeries(buf, true);
   int need = 400;
   if(CopyBuffer(handle, bufferIdx, half, need, buf) <= 0)
      return false;
   for(int i = 0; i < ArraySize(buf); i++)
     {
      if(buf[i] == EMPTY_VALUE || buf[i] == 0.0)
         continue;
      int thatShift = i + half;
      datetime t = iTime(_Symbol, tf, thatShift);
      if(t < beforeTime)
        {
         // Same fix as LastConfirmedFractal(): the buffer only marks
         // confirmation; return the real candle extreme at that bar.
         price = (bufferIdx == 0) ? iHigh(_Symbol, tf, thatShift) : iLow(_Symbol, tf, thatShift);
         outTime = t;
         return true;
        }
     }
   return false;
  }

// Fractal-level TP target (spec §8 style 3). NOT the same as LastConfirmedFractal():
// for a continuation trade, the single most-recently-confirmed opposing fractal
// is, by construction, the very level rule 1 (§3.1) just broke to trigger the
// setup — i.e. it sits BEHIND entry, not ahead of it, and PlaceStopOrder()'s own
// sanity check correctly rejects it as an invalid TP on essentially every trade
// (confirmed by a real backtest: every fractal-level TP came back on the wrong
// side of entry). "Last HH/LL level" as a target has to mean the nearest
// confirmed swing extreme still AHEAD of price in the trade's favor — scan
// backward through confirmed fractals and return the first (closest) one that
// actually qualifies as a real, unreached target.
bool FindTargetFractal(int handle, int bufferIdx, int period, double entryPrice, bool wantAbove, double &price, ENUM_TIMEFRAMES tf)
  {
   int effPeriod = (period % 2 == 0) ? period + 1 : period;
   int half = effPeriod / 2;
   double buf[];
   ArraySetAsSeries(buf, true);
   int need = 500;
   if(CopyBuffer(handle, bufferIdx, half, need, buf) <= 0)
      return false;
   for(int i = 0; i < ArraySize(buf); i++)
     {
      if(buf[i] == EMPTY_VALUE || buf[i] == 0.0)
         continue;
      int thatShift = i + half;
      double p = (bufferIdx == 0) ? iHigh(_Symbol, tf, thatShift) : iLow(_Symbol, tf, thatShift);
      bool qualifies = wantAbove ? (p > entryPrice) : (p < entryPrice);
      if(qualifies)
        {
         price = p;
         return true;
        }
     }
   return false;
  }

double StochAt(int handle, int shift)
  {
   double v[];
   ArraySetAsSeries(v, true);
   if(CopyBuffer(handle, 0, shift, 1, v) <= 0)
      return EMPTY_VALUE;
   return v[0];
  }

//======================= INIT =======================================

int OnInit()
  {
   h_fracD1 = iCustom(_Symbol, PERIOD_D1, "jrcMOD_Fractals", InpFractalPeriodD1, false, false, false, false, 178, 0.8, 0.8);
   h_fracH4 = iCustom(_Symbol, PERIOD_H4, "jrcMOD_Fractals", InpFractalPeriodH4, false, false, false, false, 178, 0.8, 0.8);
   h_slowD1 = iCustom(_Symbol, PERIOD_D1, "Stochastic_NoLastBar", InpSlowK, InpSlowD, InpSlowSlowing);
   h_fastD1 = iCustom(_Symbol, PERIOD_D1, "Stochastic_NoLastBar", InpFastK, InpFastD, InpFastSlowing);
   h_slowH4 = iCustom(_Symbol, PERIOD_H4, "Stochastic_NoLastBar", InpSlowK, InpSlowD, InpSlowSlowing);
   h_fastH4 = iCustom(_Symbol, PERIOD_H4, "Stochastic_NoLastBar", InpFastK, InpFastD, InpFastSlowing);

   if(h_fracD1 == INVALID_HANDLE || h_fracH4 == INVALID_HANDLE ||
      h_slowD1 == INVALID_HANDLE || h_fastD1 == INVALID_HANDLE ||
      h_slowH4 == INVALID_HANDLE || h_fastH4 == INVALID_HANDLE)
     {
      Print("FES_EA: failed to create one or more indicator handles, error ", GetLastError());
      return(INIT_FAILED);
     }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   ResetSignalLog();
   ResetSetup();
   Comment(StringFormat("FES_EA — exit mode: %s  |  fractal period D1/H4: %d/%d  |  lots: %.2f%s",
           ExitModeName(InpExitMode), InpFractalPeriodD1, InpFractalPeriodH4, InpLots,
           InpDoubleLotsOnElasticDiv ? " (x2 on elastic div)" : ""));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   Comment("");
   IndicatorRelease(h_fracD1); IndicatorRelease(h_fracH4);
   IndicatorRelease(h_slowD1); IndicatorRelease(h_fastD1);
   IndicatorRelease(h_slowH4); IndicatorRelease(h_fastH4);
  }

void ResetSetup()
  {
   ZeroMemory(g_setup);
   g_setup.stage = STAGE_IDLE;
  }

//======================= STAGE STALENESS ==============================
// Not spec-derived (§3.2/§9.8: the source never states a pullback timeout —
// it's a discretionary, visually-judged strategy). Added because a real
// backtest showed STAGE_PULLBACK has no bound: StepPullbackAndElasticity()
// never changes `stage`, so the only way out is a successful H4 confirmation
// — if price runs away and that never (re-)fires, the EA parks on one stale
// breakoutLevel indefinitely. Bounds are grounded in the PDF's own worked
// examples (~8-40 D1 bars breakout-to-entry across pages 1/2/19).
void CheckStageStaleness()
  {
   if(g_setup.stage == STAGE_PULLBACK && InpMaxPullbackBars > 0 && g_setup.breakoutTime != 0)
     {
      int age = iBarShift(_Symbol, PERIOD_D1, g_setup.breakoutTime, false);
      if(age >= InpMaxPullbackBars)
        {
         Print("FES_EA: abandoning stale STAGE_PULLBACK, breakoutLevel=", g_setup.breakoutLevel,
               " isLong=", g_setup.isLong, " age=", age, " bars");
         ResetSetup(); // full abandon — the whole setup (breakout included) is stale
        }
      return;
     }

   if(g_setup.stage == STAGE_H4_CONFIRMED && InpMaxVShapeBars > 0 && g_setup.confirmTime != 0)
     {
      int age = iBarShift(_Symbol, PERIOD_D1, g_setup.confirmTime, false);
      if(age >= InpMaxVShapeBars)
        {
         Print("FES_EA: demoting stale STAGE_H4_CONFIRMED to STAGE_PULLBACK, breakoutLevel=",
               g_setup.breakoutLevel, " isLong=", g_setup.isLong, " age=", age, " bars");
         // Demote, don't abandon: H4 confirmation is a claim about conditions
         // AT confirmation time, and it decays — but the underlying breakout
         // (isLong/breakoutLevel/breakoutTime) is still a real structural fact
         // worth re-confirming against, not discarding. breakoutTime's own
         // clock (checked above, on the same call, next time this stage is
         // STAGE_PULLBACK) remains the outer backstop that prevents an
         // endless confirm/demote/confirm/demote loop from running forever.
         g_setup.vshapeCount      = 0;
         g_setup.c1ext            = 0.0;
         g_setup.c2ext            = 0.0;
         g_setup.c3ext            = 0.0;
         g_setup.pivotTime        = 0;
         g_setup.confirmTime      = 0; // fresh clock only once re-confirmed
         g_setup.usedInsideBarExc = false;
         g_setup.usedHHLLFailure  = false;
         g_setup.usedH4Pivot      = false;
         g_setup.stage = STAGE_PULLBACK;
        }
     }
  }

//======================= MAIN LOOP ===================================

void OnTick()
  {
   ManageOpenPosition();

   bool newD1 = IsNewBar(PERIOD_D1, g_lastD1Bar);
   bool newH4 = IsNewBar(PERIOD_H4, g_lastH4Bar);

   // Placed here deliberately — before the STAGE_PENDING_ORDER and
   // PositionSelectMine() early returns below — so staleness is checked for
   // EVERY stage on every new D1 bar, not skipped while the machine happens
   // to be frozen (which is exactly the condition it exists to catch). A
   // 10-year EURUSD backtest confirmed STAGE_PULLBACK has no other way back
   // to STAGE_IDLE if H4 confirmation never (re-)fires for a stale setup.
   if(newD1)
      CheckStageStaleness();

   if(g_setup.stage == STAGE_AWAIT_PLACEMENT)
     {
      StepAwaitPlacement();
      return;
     }

   if(g_setup.stage == STAGE_PENDING_ORDER)
     {
      CheckPendingInvalidation();
      return;
     }

   if(PositionSelectMine())
      return; // one trade at a time (matches MBT's sequential=True assumption, spec §10.3)

   if(newD1)
     {
      switch(g_setup.stage)
        {
         case STAGE_IDLE:
            StepBreakout();
            break;
         case STAGE_PULLBACK:
            // §5.4's fast-path substitutes only for rule 4 (the D1 V-shape),
            // not for rule 3 (H4 confirmation) — it must not fire before
            // STAGE_H4_CONFIRMED. See the STAGE_H4_CONFIRMED case below.
            StepPullbackAndElasticity();
            break;
         case STAGE_H4_CONFIRMED:
            StepHHLLFailure();
            if(g_setup.stage == STAGE_H4_CONFIRMED)
               StepVShape();
            break;
        }
     }

   if(newH4 && g_setup.stage == STAGE_PULLBACK)
      StepH4Confirmation();
  }

//======================= §3.1 BREAKOUT ===============================

void StepBreakout()
  {
   double hhPrice, llPrice; datetime hhT, llT;
   bool haveHH = LastConfirmedFractal(h_fracD1, 0, InpFractalPeriodD1, hhPrice, hhT, PERIOD_D1);
   bool haveLL = LastConfirmedFractal(h_fracD1, 1, InpFractalPeriodD1, llPrice, llT, PERIOD_D1);
   double close1 = iClose(_Symbol, PERIOD_D1, 1);

   // hhT/llT != g_consumed*Time: a level test (close1 > hhPrice) stays true
   // for as long as the trend continues, so without this latch, a setup that
   // just timed out and abandoned to STAGE_IDLE would immediately re-arm on
   // the exact same fractal on the very next bar — an abandon/re-arm loop
   // instead of a genuine wait for the NEXT breakout. See g_consumedHHTime's
   // declaration for the full reasoning.
   if(haveHH && close1 > hhPrice && hhT != g_consumedHHTime)
     {
      g_setup.isLong = true;
      g_setup.breakoutLevel = hhPrice;
      g_setup.breakoutTime = iTime(_Symbol, PERIOD_D1, 1);
      g_setup.stage = STAGE_PULLBACK;
      g_consumedHHTime = hhT;
     }
   else if(haveLL && close1 < llPrice && llT != g_consumedLLTime)
     {
      g_setup.isLong = false;
      g_setup.breakoutLevel = llPrice;
      g_setup.breakoutTime = iTime(_Symbol, PERIOD_D1, 1);
      g_setup.stage = STAGE_PULLBACK;
      g_consumedLLTime = llT;
     }
  }

//======================= §3.2 PULLBACK + ELASTICITY ==================
// "Logical support/resistance" zone selection is discretionary in the
// source (spec §9.8, no mechanical rule stated). Proxy used here: the
// pullback is considered "into a zone" once price has retraced back
// toward the breakout level itself (within a tolerance band) — the
// simplest reading consistent with every worked example, where the
// zone always sits near the broken structural level. Elasticity is
// recorded (spec §10.4), not gated, unless InpElasticityMinPoints > 0.

void StepPullbackAndElasticity()
  {
   double close1 = iClose(_Symbol, PERIOD_D1, 1);
   double tolPips = 30.0 * PipSize(); // generous zone width; tune per instrument/user review
   bool nearZone = MathAbs(close1 - g_setup.breakoutLevel) <= tolPips;
   if(!nearZone)
      return;

   double fast = StochAt(h_fastD1, 1);
   double slow = StochAt(h_slowD1, 1);
   if(fast == EMPTY_VALUE || slow == EMPTY_VALUE)
      return;

   double elasticity = MathAbs(fast - slow);
   if(InpElasticityMinPoints > 0.0 && elasticity < InpElasticityMinPoints)
      return; // gated only if the user explicitly turns the gate on

   // stays in STAGE_PULLBACK; H4 confirmation is evaluated on new H4 bars (StepH4Confirmation)
  }

//======================= §3.3 H4 CONFIRMATION =========================
// Gate = (H4 stoch beyond the level per page 16, required either way) AND
// (structure OR divergence — spec §3.3's "confirm either (a) H4 stochastic
// divergence ... or (b) H4 structure"). Structure = H4 fractal swing
// sequence HL for longs / LH for shorts (unchanged mechanical proxy).
// Divergence = CheckH4Divergence() below, a real classic/hidden classifier
// comparing H4 price extremes against H4 stoch extremes at those same bar
// times (not just a level/slope proxy). Elastic divergence (§6, fast-vs-slow
// line divergence) is a separate, independent check — see CheckElasticDivergence().

void StepH4Confirmation()
  {
   double fast = StochAt(h_fastH4, 1);
   double slow = StochAt(h_slowH4, 1);
   if(fast == EMPTY_VALUE || slow == EMPTY_VALUE)
      return;

   // Required filter either way (spec §3.3 states this as a preference/filter,
   // not one of the two alternative confirmation paths) — left as the
   // original OR-based gate (either line beyond its level) since the spec
   // doesn't clearly mandate AND here; not changed by this fix pass.
   bool levelOK = g_setup.isLong ? (fast > InpStochLevelLow || slow > InpStochLevelLow)
                                  : (fast < InpStochLevelHigh || slow < InpStochLevelHigh);
   if(!levelOK)
      return;

   int bufIdx = g_setup.isLong ? 1 : 0; // lows for long (HL), highs for short (LH)
   double lastP, priorP; datetime lastT, priorT;
   if(!LastConfirmedFractal(h_fracH4, bufIdx, InpFractalPeriodH4, lastP, lastT, PERIOD_H4))
      return;
   if(!PriorConfirmedFractal(h_fracH4, bufIdx, InpFractalPeriodH4, lastT, priorP, priorT, PERIOD_H4))
      return;

   bool structureOK = g_setup.isLong ? (lastP > priorP) : (lastP < priorP);
   bool confirmed = structureOK || CheckH4Divergence(g_setup.isLong); // (a) divergence OR (b) structure — either is sufficient
   if(!confirmed)
      return;

   g_setup.elasticDivergence = CheckElasticDivergence();

   if(InpAllowH4PivotEntry)
     {
      // spec §3.4: tighter-risk H4-pivot variant — enter directly off the H4
      // LH/HL pivot instead of waiting for the D1 V-shape. Entry is beyond
      // the pivot candle's OPPOSITE extreme from the one used in the
      // structure check above (page 5: sell-stop below the LH pivot's LOW,
      // while the LH itself is defined by the pivot's HIGH); SL sits beyond
      // that same structure-defining extreme (lastP), mirroring §7's
      // pivot-extreme-plus-buffer convention.
      int barIdx = iBarShift(_Symbol, PERIOD_H4, lastT, false);
      if(barIdx >= 0)
        {
         double oppositeExtreme = g_setup.isLong ? iHigh(_Symbol, PERIOD_H4, barIdx)
                                                  : iLow(_Symbol, PERIOD_H4, barIdx);
         g_setup.pivotExtreme = lastP;   // SL basis — same extreme used in the HL/LH structure check
         g_setup.usedH4Pivot  = true;    // RegimeLabel() → "H4_PIVOT"
         // No stated pip distance for this variant (page 5 draws the stop
         // line directly at the pivot's opposite extreme) — 0 offset, same
         // convention as the inside-bar/HH-LL-failure exceptions.
         PlaceStopOrder(oppositeExtreme, 0.0);
         return; // skip StepVShape() entirely — this path is an alternative to it, not a prerequisite
        }
     }

   g_setup.stage = STAGE_H4_CONFIRMED;
   g_setup.confirmTime = iTime(_Symbol, PERIOD_D1, 1); // staleness clock — CheckStageStaleness()
   g_setup.vshapeCount = 0;
  }

// spec §3.3(a): H4 stochastic divergence, price vs. either stoch line,
// classic or hidden — compares the two most recent confirmed H4 fractal
// extremes (same structure-direction buffer used by the caller) against the
// H4 stoch values recorded at those same bar times.
//   Classic divergence: price makes a new extreme, the oscillator does not.
//   Hidden divergence:  price does NOT make a new extreme, the oscillator does.
// Checked against both the slow and fast H4 lines independently; either
// showing divergence is sufficient (spec: "price vs. either stoch line").
bool CheckH4Divergence(bool isLong)
  {
   int bufIdx = isLong ? 1 : 0; // lows for long, highs for short — same convention as structure check
   double lastP, priorP; datetime lastT, priorT;
   if(!LastConfirmedFractal(h_fracH4, bufIdx, InpFractalPeriodH4, lastP, lastT, PERIOD_H4))
      return false;
   if(!PriorConfirmedFractal(h_fracH4, bufIdx, InpFractalPeriodH4, lastT, priorP, priorT, PERIOD_H4))
      return false;

   int lastShift  = iBarShift(_Symbol, PERIOD_H4, lastT, false);
   int priorShift = iBarShift(_Symbol, PERIOD_H4, priorT, false);
   if(lastShift < 0 || priorShift < 0)
      return false;

   bool priceNewExtreme = isLong ? (lastP < priorP) : (lastP > priorP);

   int handles[2];
   handles[0] = h_slowH4;
   handles[1] = h_fastH4;
   for(int i = 0; i < 2; i++)
     {
      double stochLast  = StochAt(handles[i], lastShift);
      double stochPrior = StochAt(handles[i], priorShift);
      if(stochLast == EMPTY_VALUE || stochPrior == EMPTY_VALUE)
         continue;

      bool stochNewExtreme = isLong ? (stochLast < stochPrior) : (stochLast > stochPrior);

      bool classic = priceNewExtreme && !stochNewExtreme;  // price extends, oscillator doesn't
      bool hidden  = !priceNewExtreme && stochNewExtreme;  // price doesn't extend, oscillator does
      if(classic || hidden)
         return true;
     }
   return false;
  }

//======================= §6 ELASTIC DIVERGENCE =========================
// A divergence between the fast and slow stoch LINES themselves (not
// price-vs-stoch): the slow line agrees with price's extreme behaviour
// while the fast line diverges from price (classic or hidden). Per spec §6
// this can appear on either the D1 pullback or the H4 confirmation leg, so
// both are checked and OR'd. Detection compares genuine local extremes
// reached since the setup's pivotTime (once a V-shape pivot exists) or
// breakoutTime (before one does) — not a fixed short-lookback slope
// snapshot — matching the glossary's actual extreme-based definition of
// classic/hidden divergence (§2).

// Bars from shift 1 back to (and including) the bar covering sinceTime,
// capped to a sane maximum so a distant anchor doesn't force an unbounded
// scan. Returns >=2 always; callers treat <3 as "not enough history yet".
int ElasticDivWindowBars(datetime sinceTime, ENUM_TIMEFRAMES tf)
  {
   if(sinceTime == 0)
      return 0;
   int shift = iBarShift(_Symbol, tf, sinceTime, false);
   if(shift <= 1)
      return 0;
   return (int)MathMin(shift, 60);
  }

// Splits a stochastic line's values over [1..windowBars] into "current"
// (shift 1, the most recently closed bar) and "best of the older bars"
// (shift 2..windowBars) — best = lowest for isLong (tracking new lows),
// highest for !isLong (tracking new highs), matching the pullback-extreme
// direction convention used throughout this file.
bool StochWindowSplit(int handle, bool isLong, int windowBars, double &cur, double &priorBest)
  {
   cur = StochAt(handle, 1);
   if(cur == EMPTY_VALUE)
      return false;
   priorBest = EMPTY_VALUE;
   for(int s = 2; s <= windowBars; s++)
     {
      double v = StochAt(handle, s);
      if(v == EMPTY_VALUE)
         break; // ran out of history
      if(priorBest == EMPTY_VALUE)
         priorBest = v;
      else if(isLong ? (v < priorBest) : (v > priorBest))
         priorBest = v;
     }
   return priorBest != EMPTY_VALUE;
  }

// Same split, for D1/H4 price (low series for isLong, high series for !isLong).
bool PriceWindowSplit(ENUM_TIMEFRAMES tf, bool isLong, int windowBars, double &cur, double &priorBest)
  {
   cur = isLong ? iLow(_Symbol, tf, 1) : iHigh(_Symbol, tf, 1);
   priorBest = EMPTY_VALUE;
   for(int s = 2; s <= windowBars; s++)
     {
      double v = isLong ? iLow(_Symbol, tf, s) : iHigh(_Symbol, tf, s);
      if(priorBest == EMPTY_VALUE)
         priorBest = v;
      else if(isLong ? (v < priorBest) : (v > priorBest))
         priorBest = v;
     }
   return priorBest != EMPTY_VALUE;
  }

bool CheckElasticDivergenceTF(int slowHandle, int fastHandle, ENUM_TIMEFRAMES tf)
  {
   datetime since = (g_setup.pivotTime != 0) ? g_setup.pivotTime : g_setup.breakoutTime;
   int windowBars = ElasticDivWindowBars(since, tf);
   if(windowBars < 3)
      return false; // not enough history since the anchor to judge a "new" extreme

   double priceCur, pricePrior;
   if(!PriceWindowSplit(tf, g_setup.isLong, windowBars, priceCur, pricePrior))
      return false;
   bool priceNewExtreme = g_setup.isLong ? (priceCur < pricePrior) : (priceCur > pricePrior);

   double slowCur, slowPrior;
   if(!StochWindowSplit(slowHandle, g_setup.isLong, windowBars, slowCur, slowPrior))
      return false;
   bool slowNewExtreme = g_setup.isLong ? (slowCur < slowPrior) : (slowCur > slowPrior);
   bool slowAgrees = (priceNewExtreme == slowNewExtreme); // slow tracks price's extreme behaviour either way

   double fastCur, fastPrior;
   if(!StochWindowSplit(fastHandle, g_setup.isLong, windowBars, fastCur, fastPrior))
      return false;
   bool fastNewExtreme = g_setup.isLong ? (fastCur < fastPrior) : (fastCur > fastPrior);

   bool classic = priceNewExtreme && !fastNewExtreme;  // price extends, fast doesn't confirm
   bool hidden  = !priceNewExtreme && fastNewExtreme;  // price doesn't extend, fast does

   return slowAgrees && (classic || hidden);
  }

bool CheckElasticDivergence()
  {
   return CheckElasticDivergenceTF(h_slowD1, h_fastD1, PERIOD_D1) ||
          CheckElasticDivergenceTF(h_slowH4, h_fastH4, PERIOD_H4);
  }

//======================= §5 V-SHAPE ===================================

void StepVShape()
  {
   double low1 = iLow(_Symbol, PERIOD_D1, 1),  high1 = iHigh(_Symbol, PERIOD_D1, 1);
   double open1 = iOpen(_Symbol, PERIOD_D1, 1), close1 = iClose(_Symbol, PERIOD_D1, 1);
   bool bullish1 = close1 > open1;

   if(g_setup.vshapeCount == 0)
     {
      g_setup.c1ext = g_setup.isLong ? low1 : high1;
      g_setup.vshapeCount = 1;
      return;
     }
   if(g_setup.vshapeCount == 1)
     {
      double ext2 = g_setup.isLong ? low1 : high1;
      bool pivotOK = g_setup.isLong ? (ext2 < g_setup.c1ext) : (ext2 > g_setup.c1ext);
      if(!pivotOK)
        {
         // candle2 didn't extend beyond candle1 — candle1 rolls forward (§5.1 recount)
         g_setup.c1ext = ext2;
         return;
        }
      g_setup.c2ext = ext2;
      g_setup.pivotExtreme = ext2;
      g_setup.pivotTime = iTime(_Symbol, PERIOD_D1, 1);
      g_setup.vshapeCount = 2;
      return;
     }
   if(g_setup.vshapeCount == 2)
     {
      double ext3 = g_setup.isLong ? low1 : high1;
      bool c3ExtremeOK = g_setup.isLong ? (ext3 > g_setup.c2ext) : (ext3 < g_setup.c2ext);
      if(!c3ExtremeOK)
        {
         // candle3 broke the pivot before completing the V — this is §5.1
         // pattern-formation continuation logic (a candle that fails to stay
         // beyond the pivot simply isn't candle 3 yet), NOT §5.2 (which
         // governs strictly after the V completes, as a live pending-order
         // cancel condition). Treating the failing candle as a new candle 1
         // is a design choice, not a spec-mandated reading — the spec gives
         // no worked example for this exact sub-case. An alternative would
         // be to slide the window instead (candle2→candle1, candle3→candle2)
         // rather than discarding candle2 outright; not adopted here since
         // candle2 no longer satisfies "pivot beyond candle1" once candle3
         // has broken past it, so sliding it forward as a new candle1
         // reference is at least as defensible as discarding it.
         g_setup.vshapeCount = 1;
         g_setup.c1ext = ext3;
         g_setup.pivotTime = 0; // discarding candle2's pivot — don't leak its stale pivotTime (matches CheckPendingInvalidation())
         return;
        }

      bool closedRight = g_setup.isLong ? bullish1 : !bullish1;
      if(closedRight)
        {
         PlaceStopOrder(close1, InpStopOffsetPips);
         return;
        }

      // candle3 closed the wrong way — check inside-bar exception (§5.3)
      bool insideBar = (high1 <= iHigh(_Symbol, PERIOD_D1, 2)) && (low1 >= iLow(_Symbol, PERIOD_D1, 2));
      if(insideBar)
        {
         g_setup.vshapeCount = 3; // wait for candle 4
         return;
        }

      // neither: formation invalid, restart looking for a new V-shape
      g_setup.vshapeCount = 1;
      g_setup.c1ext = ext3;
      g_setup.pivotTime = 0; // same as above — this pivot is abandoned too
      return;
     }
   if(g_setup.vshapeCount == 3)
     {
      // this is candle 4 under the inside-bar exception (§5.3)
      bool closedRight = g_setup.isLong ? bullish1 : !bullish1;
      if(closedRight)
        {
         g_setup.usedInsideBarExc = true;
         double trigger = g_setup.isLong ? high1 : low1; // entry at candle4's high (long) / low (short, per §9.4 economic resolution)
         PlaceStopOrder(trigger, 0.0); // no pip offset — trigger IS the level (§5.3)
        }
      else
        {
         // still wrong direction — give up this formation, go back to watching for a fresh V-shape
         g_setup.vshapeCount = 0;
         g_setup.pivotTime = 0; // formation abandoned entirely — don't leak its stale pivotTime
        }
     }
  }

//======================= §5.4 HH/LL FAILURE EXCEPTION =================
// Fast-path entry that bypasses waiting for the full V-shape (§5.1): during
// the pullback, one more marginal new low (long setups) / new high (short
// setups) beyond the broken breakout level, closing as a decisive rejection
// candle, triggers a stop order immediately beyond that candle's close (no
// stated pip distance in the source — 0 offset, same convention as the
// inside-bar exception). Checkable independently of, and as an alternative
// to, StepVShape() — it does not require a V-shape to be in progress.
//
// "Decisive rejection candle" is read here as: the close sits close to the
// candle's extreme on the CLOSING/entry side (i.e. minimal wick beyond the
// close in the direction of the anticipated trade) — matching "I like to
// see as little rejection size on its close as possible... a considerable
// wick on its close [and] I wait for the V shape" (page 11). This is a
// from-scratch interpretation of prose only (§1.3 — no ported "cream
// arrows" algorithm exists), not a literal transcription.
void StepHHLLFailure()
  {
   if(g_setup.usedHHLLFailure) // already fired for this setup
      return;

   double high1  = iHigh(_Symbol, PERIOD_D1, 1),  low1  = iLow(_Symbol, PERIOD_D1, 1);
   double open1  = iOpen(_Symbol, PERIOD_D1, 1),  close1 = iClose(_Symbol, PERIOD_D1, 1);
   double range = high1 - low1;
   if(range <= 0.0)
      return;

   if(g_setup.isLong)
     {
      // "LL failure": pullback prints a marginal new low beyond (below) the
      // broken breakout level, then rejects — closes bullish, decisively
      // near the high.
      bool newExtreme = low1 < g_setup.breakoutLevel;
      bool bullish    = close1 > open1;
      if(!newExtreme || !bullish)
         return;
      double closeToHighRatio = (high1 - close1) / range;
      if(closeToHighRatio > InpRejectionMaxCloseRatio)
         return; // considerable wick above the close — not decisive, fall back to the standard V-shape

      g_setup.elasticDivergence = CheckElasticDivergence(); // §6: this path can also earn the lot-size double
      g_setup.usedHHLLFailure = true;
      g_setup.pivotExtreme = low1; // this candle's own extreme — invalidation/SL basis (keeps CheckPendingInvalidation() meaningful here too)
      PlaceStopOrder(close1, 0.0); // beyond the close, no stated pip distance (§5.4)
     }
   else
     {
      // "HH failure": mirror — marginal new high beyond (above) the broken
      // breakout level, then rejects — closes bearish, decisively near the low.
      bool newExtreme = high1 > g_setup.breakoutLevel;
      bool bearish    = close1 < open1;
      if(!newExtreme || !bearish)
         return;
      double closeToLowRatio = (close1 - low1) / range;
      if(closeToLowRatio > InpRejectionMaxCloseRatio)
         return;

      g_setup.elasticDivergence = CheckElasticDivergence();
      g_setup.usedHHLLFailure = true;
      g_setup.pivotExtreme = high1;
      PlaceStopOrder(close1, 0.0);
     }
  }

//======================= §3.6 / §5.3 ORDER PLACEMENT ==================

void PlaceStopOrder(double basePrice, double offsetPips)
  {
   double offset = offsetPips * PipSize();
   double entry = g_setup.isLong ? basePrice + offset : basePrice - offset;
   double buffer = InpSLBufferPips * PipSize();
   double sl = g_setup.isLong ? g_setup.pivotExtreme - buffer : g_setup.pivotExtreme + buffer;

   double tp = 0.0;
   if(InpExitMode == EXIT_FRACTAL_LEVEL)
     {
      double price;
      int bufIdx = g_setup.isLong ? 0 : 1; // HH for a long TP, LL for a short TP
      if(FindTargetFractal(h_fracD1, bufIdx, InpFractalPeriodD1, entry, g_setup.isLong, price, PERIOD_D1))
         tp = price;
     }

   // Sanity check retained as defense-in-depth even though FindTargetFractal()
   // already only returns qualifying (correct-side) prices — a fractal
   // indicator handle could still change behavior under different inputs.
   // MT5 rejects BuyStop/SellStop outright on a wrong-side TP, silently
   // dropping an otherwise valid signal with no LogSignal() call, so this
   // must never be allowed to fail the whole order.
   bool tpOK = (tp == 0.0) || (g_setup.isLong && tp > entry) || (!g_setup.isLong && tp < entry);
   if(!tpOK)
      tp = 0.0;

   double lots = InpLots * (g_setup.elasticDivergence && InpDoubleLotsOnElasticDiv ? 2.0 : 1.0);

   // Finite pending-order life (not spec-derived — the source says nothing
   // about GTC vs. expiry; a deliberate interpretation, belt-and-suspenders
   // alongside §5.2's price-based invalidation, so a stop order that neither
   // fills nor gets invalidated for a very long time still eventually clears).
   // Stash the computed order and hand it to the placement loop rather than
   // firing once and discarding the setup on any rejection.
   g_setup.pendEntry     = entry;
   g_setup.pendSL        = sl;
   g_setup.pendTP        = tp;
   g_setup.pendLots      = lots;
   g_setup.pendSignalBar = iTime(_Symbol, PERIOD_D1, 1);
   g_setup.stage         = STAGE_AWAIT_PLACEMENT;

   TryPlaceOrder();
  }

//+------------------------------------------------------------------+
//| Can we actually trade this symbol right now?                     |
//+------------------------------------------------------------------+
bool MarketIsTradable()
  {
   ENUM_SYMBOL_TRADE_MODE mode =
      (ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(mode == SYMBOL_TRADE_MODE_DISABLED || mode == SYMBOL_TRADE_MODE_CLOSEONLY)
      return false;
   return (SymbolInfoDouble(_Symbol, SYMBOL_ASK) > 0.0 &&
           SymbolInfoDouble(_Symbol, SYMBOL_BID) > 0.0);
  }

//+------------------------------------------------------------------+
//| Get the stashed order onto the market. Three outcomes, and the   |
//| distinction is the point of the fix:                             |
//|   * trigger still ahead of price -> place the stop order (§3.6)  |
//|   * price already through it     -> a resting order WOULD have   |
//|     filled, so take it at market, but only within                |
//|     InpMaxChasePips; past that we'd be chasing, not entering     |
//|   * shut / still too close       -> keep the setup and retry     |
//+------------------------------------------------------------------+
bool TryPlaceOrder()
  {
   if(!MarketIsTradable())
      return false;

   double point   = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double entry = g_setup.pendEntry;
   double sl    = g_setup.pendSL;
   double tp    = g_setup.pendTP;
   double lots  = g_setup.pendLots;
   double chase = InpMaxChasePips * PipSize();

   datetime expiry = 0;
   ENUM_ORDER_TYPE_TIME timeType = ORDER_TIME_GTC;
   if(InpPendingOrderExpiryBars > 0)
     {
      timeType = ORDER_TIME_SPECIFIED;
      expiry   = TimeCurrent() + InpPendingOrderExpiryBars * 86400;
     }

   bool ok       = false;
   bool atMarket = false;

   if(g_setup.isLong)
     {
      if(entry > ask + minDist)
         ok = g_trade.BuyStop(lots, entry, _Symbol, sl, tp, timeType, expiry, "FES");
      else if(InpMarketFillOnGap && (ask - entry) <= chase)
        {
         atMarket = true;
         ok = g_trade.Buy(lots, _Symbol, 0.0, sl, tp, "FES gap");
        }
      else
         return false;   // ran too far past the trigger; wait for it to come back
     }
   else
     {
      if(entry < bid - minDist)
         ok = g_trade.SellStop(lots, entry, _Symbol, sl, tp, timeType, expiry, "FES");
      else if(InpMarketFillOnGap && (entry - bid) <= chase)
        {
         atMarket = true;
         ok = g_trade.Sell(lots, _Symbol, 0.0, sl, tp, "FES gap");
        }
      else
         return false;
     }

   if(!ok)
     {
      PrintFormat("FES_EA: placement retry (retcode %d %s) entry=%s ask=%s bid=%s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription(),
                  DoubleToString(entry, _Digits), DoubleToString(ask, _Digits),
                  DoubleToString(bid, _Digits));
      return false;   // keep the setup; StepAwaitPlacement() bounds the retries
     }

   g_setup.stopPrice     = atMarket ? (g_setup.isLong ? ask : bid) : entry;
   g_setup.slPrice       = sl;
   g_setup.tpPrice       = tp;
   g_setup.pendingTicket = atMarket ? 0 : g_trade.ResultOrder();
   g_setup.stage         = STAGE_PENDING_ORDER;

   int shift = iBarShift(_Symbol, PERIOD_D1, g_setup.pendSignalBar, false);
   if(shift < 0) shift = 1;

   string regime = RegimeLabel();
   LogSignal(shift, g_setup.isLong, g_setup.stopPrice, sl,
             (tp > 0 ? tp : g_setup.stopPrice), regime);
   DrawTradeLabel(g_setup.pendSignalBar, g_setup.stopPrice, g_setup.isLong, regime);
   return true;
  }

//+------------------------------------------------------------------+
//| Drive the retry while STAGE_AWAIT_PLACEMENT, bounded by          |
//| InpOrderRetryBars. Mirrors CheckPendingInvalidation(): a setup    |
//| that never reaches the market falls back to STAGE_H4_CONFIRMED,  |
//| preserving the breakout and H4 confirmation, rather than reset.  |
//+------------------------------------------------------------------+
void StepAwaitPlacement()
  {
   // §5.2 invalidation still applies while we wait
   double last = g_setup.isLong ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool invalidated = g_setup.isLong ? (last < g_setup.pivotExtreme)
                                     : (last > g_setup.pivotExtreme);

   int age = (g_setup.pendSignalBar != 0)
             ? iBarShift(_Symbol, PERIOD_D1, g_setup.pendSignalBar, false) : 0;
   if(age < 0) age = 0;
   bool expired = (!InpRetryOrderPlacement) || (age > InpOrderRetryBars);

   if(invalidated || expired)
     {
      if(expired && !invalidated)
         PrintFormat("FES_EA: gave up placing order after %d D1 bar(s), entry=%s",
                     age, DoubleToString(g_setup.pendEntry, _Digits));
      g_setup.vshapeCount      = 0;
      g_setup.c1ext            = 0.0;
      g_setup.c2ext            = 0.0;
      g_setup.c3ext            = 0.0;
      g_setup.pivotTime        = 0;
      g_setup.usedInsideBarExc = false;
      g_setup.usedHHLLFailure  = false;
      g_setup.usedH4Pivot      = false;
      g_setup.pendEntry        = 0.0;
      g_setup.pendSL           = 0.0;
      g_setup.pendTP           = 0.0;
      g_setup.pendLots         = 0.0;
      g_setup.pendSignalBar    = 0;
      g_setup.stage            = STAGE_H4_CONFIRMED;
      return;
     }

   TryPlaceOrder();
  }

string RegimeLabel()
  {
   if(g_setup.usedH4Pivot)       return "H4_PIVOT";
   if(g_setup.usedInsideBarExc)  return "INSIDE_BAR";
   if(g_setup.usedHHLLFailure)   return "HHLL_FAILURE";
   return "STANDARD_V";
  }

string ExitModeName(ENUM_EXIT_MODE m)
  {
   switch(m)
     {
      case EXIT_FRACTAL_LEVEL:    return "FRACTAL_LEVEL";
      case EXIT_SLOW_STOCH_CROSS: return "SLOW_STOCH_CROSS";
      case EXIT_FAST_STOCH_CROSS: return "FAST_STOCH_CROSS";
      case EXIT_TRAILING_D1:      return "TRAILING_D1";
     }
   return "?";
  }

// On-chart annotation (not spec-derived — a visual aid so a trade's entry rule
// and the active exit setting are readable directly on the chart, not only in
// the report/journal). One text object per trade, anchored at the entry bar
// and price, with the elastic-divergence lot-double flagged too since it's
// otherwise invisible on the chart.
void DrawTradeLabel(datetime t, double price, bool isLong, string regime)
  {
   string name = StringFormat("FES_lbl_%d", (int)t);
   if(ObjectFind(0, name) >= 0)
      ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_TEXT, 0, t, price);
   string txt = StringFormat("%s %s\n%s%s", isLong ? "LONG" : "SHORT", regime,
                              "exit=" + ExitModeName(InpExitMode),
                              g_setup.elasticDivergence ? "\nx2 lot (elastic div)" : "");
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_COLOR, isLong ? clrDeepSkyBlue : clrOrange);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, isLong ? ANCHOR_TOP : ANCHOR_BOTTOM);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
  }

//======================= §5.2 PENDING-ORDER INVALIDATION ==============

void CheckPendingInvalidation()
  {
   if(!OrderSelect(g_setup.pendingTicket))
     {
      // order no longer pending — either filled (position now open) or already removed
      if(PositionSelectMine())
         return; // filled; ManageOpenPosition() handles it from here
      ResetSetup();
      return;
     }

   double last = g_setup.isLong ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool invalidated = g_setup.isLong ? (last < g_setup.pivotExtreme) : (last > g_setup.pivotExtreme);
   if(invalidated)
     {
      // spec §5.2: invalidation only restarts the 3-candle V-shape count —
      // it is NOT a reason to discard the whole setup. A blanket
      // ResetSetup() here would wipe breakoutLevel/isLong/elasticDivergence
      // (and breakoutTime, needed by CheckElasticDivergence()'s window
      // anchor) and force the entire D1→H4→D1 sequence to restart from
      // scratch on every pending-order cancel.
      g_trade.OrderDelete(g_setup.pendingTicket);

      // Reset only V-shape-tracking state...
      g_setup.vshapeCount      = 0;
      g_setup.c1ext            = 0.0;
      g_setup.c2ext            = 0.0;
      g_setup.c3ext            = 0.0;
      g_setup.pivotTime        = 0;   // stale pivot's time; a fresh one is set when candle2 is re-confirmed
      g_setup.usedInsideBarExc = false;
      g_setup.pendingTicket    = 0;
      // ...and the other regime-path flags, so a subsequent standard V-shape
      // entry isn't mislabeled as HHLL_FAILURE/H4_PIVOT by RegimeLabel()
      // from this now-cancelled attempt (not explicitly listed in the fix
      // spec, which predates fixes 1/5 adding these flags, but required for
      // RegimeLabel() correctness on the next order placed for this setup).
      g_setup.usedHHLLFailure  = false;
      g_setup.usedH4Pivot      = false;

      g_setup.stage = STAGE_H4_CONFIRMED; // resume watching for a fresh V-shape

      // isLong, breakoutLevel, breakoutTime and elasticDivergence are
      // deliberately PRESERVED — do not call ResetSetup() here.
     }
  }

//======================= §8 EXIT MANAGEMENT ============================

bool PositionSelectMine()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      return true;
     }
   return false;
  }

void ManageOpenPosition()
  {
   if(!PositionSelectMine())
      return;
   bool isLong = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;

   switch(InpExitMode)
     {
      case EXIT_FRACTAL_LEVEL:
         break; // static TP already on the position; nothing to manage bar-by-bar
      case EXIT_SLOW_STOCH_CROSS:
         if(StochCrossExit(h_slowD1, isLong))
            ClosePositionAndReset();
         break;
      case EXIT_FAST_STOCH_CROSS:
         if(StochCrossExit(h_fastD1, isLong))
            ClosePositionAndReset();
         break;
      case EXIT_TRAILING_D1:
         TrailD1(isLong);
         break;
     }
  }

// Exit style 1/2 (spec §8): close when the stoch crosses back through its
// OB/OS level — was beyond the level on the prior bar, now back inside it.
// Gated on the dedicated exit-management bar variable (g_lastD1BarExit) so
// this only re-evaluates once per new D1 bar, independent of the entry
// state machine's g_lastD1Bar and of OnTick's call ordering.
bool StochCrossExit(int handle, bool isLong)
  {
   if(!IsNewBar(PERIOD_D1, g_lastD1BarExit))
      return false;
   double prev = StochAt(handle, 2);
   double cur  = StochAt(handle, 1);
   if(prev == EMPTY_VALUE || cur == EMPTY_VALUE)
      return false;
   if(isLong)
      return (prev >= InpStochLevelHigh && cur < InpStochLevelHigh);
   else
      return (prev <= InpStochLevelLow && cur > InpStochLevelLow);
  }

// Exit style 4 (spec §8): trail the stop forward under/over each new
// same-direction D1 candle's opposite extreme. Uses the dedicated
// g_lastD1BarExit exit-management bar-gate (not the local-copy/dummy
// pattern this used to use, whose correctness depended entirely on
// ManageOpenPosition() running before OnTick's own IsNewBar call on the
// same tick) — see g_lastD1BarExit's declaration.
void TrailD1(bool isLong)
  {
   if(!IsNewBar(PERIOD_D1, g_lastD1BarExit))
      return;

   double open1 = iOpen(_Symbol, PERIOD_D1, 1), close1 = iClose(_Symbol, PERIOD_D1, 1);
   bool favorable = isLong ? (close1 > open1) : (close1 < open1);
   if(!favorable)
      return;

   double newSL = isLong ? iLow(_Symbol, PERIOD_D1, 1) : iHigh(_Symbol, PERIOD_D1, 1);
   double curSL = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   bool improve = isLong ? (curSL == 0 || newSL > curSL) : (curSL == 0 || newSL < curSL);
   if(improve)
      g_trade.PositionModify(_Symbol, newSL, tp);
  }

void ClosePositionAndReset()
  {
   g_trade.PositionClose(_Symbol);
   ResetSetup();
  }
