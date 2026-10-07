//+------------------------------------------------------------------+
//| FourHourRangeScalper.mq5 — the "4-hour range" 5-minute scalper   |
//|                                                                  |
//| Implements docs/specs/FOUR_HOUR_RANGE_SCALP_SPEC.md — a port of  |
//| the strategy described in "The BEST 5 Minute Scalping Strategy   |
//| Ever" (video transcript). Every rule below carries the transcript |
//| timestamp it comes from; every input exists because the source    |
//| does NOT pin that value down (marked [UNSTATED] in the spec).     |
//|                                                                  |
//| The strategy in one paragraph:                                    |
//|   Mark the high and low of the first 4-hour candle of the NEW     |
//|   YORK day (NY 00:00-04:00). Then on M5, wait for a candle to     |
//|   CLOSE outside that range, and then for a later candle to CLOSE  |
//|   back inside it. Fade the breakout: broke the high -> short,     |
//|   broke the low -> long. Stop at the extreme of the breakout      |
//|   excursion, target 2x the stop. Repeat until the NY day ends.    |
//|                                                                  |
//| WHY THE RANGE IS BUILT FROM M5 BARS AND NOT FROM AN H4 BAR:       |
//|   MT5 aligns H4 bars to the BROKER SERVER clock, so an H4 bar     |
//|   starts at server 00:00/04:00/08:00... On an EET broker NY 00:00 |
//|   is server 07:00 — never an H4 boundary. iHigh(_Symbol,          |
//|   PERIOD_H4, ...) therefore cannot express "the first 4-hour      |
//|   candle of the New York day" at all. We aggregate M5 bars whose  |
//|   New York timestamp falls inside the window instead.             |
//|                                                                  |
//| Designed to be driven headlessly by MBT:                          |
//|   run_strategy_tester  -> MT5's own tester runs this EA           |
//|   backtest             -> MBT replays the signals this EA logs    |
//|   signal_parity        -> diffs the two, to catch port drift      |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "4-hour range (New York) breakout-fade scalper on M5. Fixed 2R."

#include <SignalLogger.mqh>      // input SignalLogFile + LogSignal()/ResetSignalLog()
#include <Trade\Trade.mqh>

//======================= INPUTS =====================================

// --- Step 1: the range (spec §1) -----------------------------------------
input group "Range — the first 4H candle of the New York day"
input int    InpRangeStartHourNY   = 0;     // NY hour the range window opens (0 = NY midnight)
input int    InpRangeHours         = 4;     // length of the "4-hour candle"
input double InpMinRangeCoverage   = 0.5;   // fraction of the window that must have bars, else no range today

// --- The clock (spec §6) --------------------------------------------------
// Two hops: server -> UTC (your broker's offset + DST rule), UTC -> New York
// (US DST rule, always). Defaults are for an EET/EEST broker such as Darwinex.
enum ENUM_SRV_DST
  {
   SRV_DST_EU,      // server clock follows EU DST (last Sun Mar -> last Sun Oct) — EET/EEST
   SRV_DST_US,      // server clock follows US DST (2nd Sun Mar -> 1st Sun Nov)
   SRV_DST_NONE     // server clock never shifts (fixed GMT offset)
  };
input group "Broker clock"
input int          InpServerGMTOffsetWinter = 2;            // server GMT offset in WINTER (EET = 2)
input ENUM_SRV_DST InpServerDST             = SRV_DST_EU;   // which DST rule the server clock follows

// --- Step 2: the setup (spec §2) -----------------------------------------
enum ENUM_BRK_MODE
  {
   BRK_CLOSE_OUTSIDE,   // a bar must CLOSE beyond the edge — the transcript's rule [00:02:52]
   BRK_WICK_OUTSIDE     // a wick beyond the edge is enough — explicitly NOT the rule; for comparison only
  };
input group "Setup — breakout then re-entry"
input ENUM_BRK_MODE InpBreakoutMode        = BRK_CLOSE_OUTSIDE;
input bool          InpAllowPreArmedOutside = false;  // price already outside when the range opens = armed breakout? (spec §7.3)

// --- Step 3: entry, stop, target (spec §3) -------------------------------
enum ENUM_SL_ANCHOR
  {
   SL_BREAKOUT_EXTREME,  // the extreme reached while outside — "the exact high of the breakout move" [00:03:22]
   SL_RANGE_EDGE         // the range edge itself — the looser phrasing used in later examples
  };
enum ENUM_OVERSIZED
  {
   OVER_ALLOW,   // take the wide stop — the literal basic rule (default; see spec §3.5)
   OVER_SKIP,    // skip the trade entirely
   OVER_CAP,     // cap the stop at InpMaxSLRangeMult x the range height
   OVER_SWING    // mechanical proxy for "nearest key level": last M5 swing inside the excursion
  };
input group "Entry, stop and target"
input double          InpRR              = 2.0;    // take profit as a multiple of risk [00:03:29]
input ENUM_SL_ANCHOR  InpSLAnchor        = SL_BREAKOUT_EXTREME;
input double          InpSLBufferPoints  = 0.0;    // extra points beyond the anchor (0 = exactly on it)
input ENUM_OVERSIZED  InpOversizedSL     = OVER_ALLOW;
input double          InpMaxSLRangeMult  = 1.0;    // "too large" threshold, as a multiple of the range height
input int             InpSwingLookback   = 2;      // OVER_SWING: bars each side that define a swing

// --- Trade management (spec §4) ------------------------------------------
input group "Trade management"
input int    InpMaxTradesPerDay     = 0;      // 0 = unlimited [00:04:38]
input bool   InpOnePositionAtATime  = true;   // [UNSTATED] — matches MBT's python replay, keeps the two engines comparable
input bool   InpCloseAtDayEnd       = false;  // [UNSTATED] — transcript only ever stops ENTRIES, never exits
input double InpRiskPercent         = 1.0;    // % of balance risked per trade (0 = use InpFixedLots)
input double InpFixedLots           = 0.0;    // fixed lot size when InpRiskPercent = 0
input double InpMinSLPoints         = 0.0;    // ignore setups whose stop is tighter than this (0 = broker floor only)
input double InpMaxLots             = 0.0;    // hard lot cap (0 = none); margin is capped regardless
input int    InpMagicNumber         = 20260920;

// --- Diagnostics ---------------------------------------------------------
input group "Diagnostics"
input bool   InpLogSignals = true;    // write every entry to SignalLogFile for MBT's backtest/signal_parity
input bool   InpWriteTesterCsv = true;// write <EA>_tester.csv to Common/Files for MBT's read_ea_summary()
input bool   InpVerbose      = false; // print the NY mapping and each day's range

//======================= STATE ======================================

// Where we are in the per-day breakout/re-entry cycle.
enum ENUM_RSTATE
  {
   RS_WAIT_RANGE,   // NY day has started, the 00:00-04:00 window is not finished yet
   RS_NO_RANGE,     // the window produced too little data — stand down for this NY day
   RS_INSIDE,       // range is valid and armed; watching for a bar to close outside it
   RS_BROKEN_UP,    // a bar closed above the high; tracking the excursion extreme, waiting for re-entry
   RS_BROKEN_DOWN,  // a bar closed below the low;  tracking the excursion extreme, waiting for re-entry
   RS_DONE          // InpMaxTradesPerDay reached — no more entries this NY day
  };

CTrade   g_trade;

int      g_nyDay       = INT_MIN;   // NY day number (days since epoch) the current range belongs to
ENUM_RSTATE g_state    = RS_WAIT_RANGE;
double   g_rangeHigh   = 0.0;
double   g_rangeLow    = 0.0;
int      g_rangeBars   = 0;         // M5 bars that contributed to the range
double   g_excExtreme  = 0.0;       // extreme reached during the current breakout excursion
int      g_tradesToday = 0;
datetime g_lastBar     = 0;

// Per-position bookkeeping, so OnTester can report in R rather than currency.
struct OpenTrade
  {
   ulong    posId;
   double   riskMoney;   // what 1R was worth, in account currency, at entry
   datetime opened;
  };
OpenTrade g_open[];

// Closed-trade R tally (the OnTester summary).
double   g_rs[];         // realised R per closed trade, in order

// Why setups did not become trades. Reported in the tester summary: a backtest
// that silently drops a third of its signals is not the strategy being claimed.
int      g_sigTotal    = 0;   // completed breakout -> re-entry sequences
int      g_skipOverlap = 0;   // a position was already open (InpOnePositionAtATime)
int      g_skipTight   = 0;   // stop closer than the broker's minimum / InpMinSLPoints
int      g_skipOversz  = 0;   // OVER_SKIP: stop wider than InpMaxSLRangeMult x range
int      g_skipCap     = 0;   // InpMaxTradesPerDay reached
int      g_skipSizing  = 0;   // could not compute a tradeable lot size
int      g_skipReject  = 0;   // broker rejected the order
int      g_marginCut   = 0;   // lots reduced to fit free margin (risk < intended)

//======================= NEW YORK CLOCK (spec §6) ===================
//
// Validated by transliterating these exact functions to Python and sweeping
// every hour of 2017-2027 (96,397 hours) against the IANA tz database: zero
// mismatches. Deliberately avoids TimeGMT()/TimeLocal(), which are not
// dependable inside the Strategy Tester, and avoids StringToTime so there is
// no string work on the per-bar path.

int DaysInMonth(const int y, const int m)
  {
   static int dim[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
   if(m == 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0))
      return 29;
   return dim[m - 1];
  }

// Days since 1970-01-01 for a civil date (Howard Hinnant's days_from_civil).
long DaysFromCivil(int y, const int m, const int d)
  {
   y -= (m <= 2) ? 1 : 0;
   long era = (long)((y >= 0 ? y : y - 399) / 400);
   long yoe = (long)(y - era * 400);                                    // [0,399]
   long doy = (long)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);   // [0,365]
   long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;                    // [0,146096]
   return era * 146097 + doe - 719468;
  }

// n-th (1-based) weekday `dow` (0=Sun..6=Sat) of a month, at 00:00.
datetime NthDow(const int y, const int mo, const int dow, const int n)
  {
   long d1  = DaysFromCivil(y, mo, 1);
   int  w1  = (int)((d1 + 4) % 7);          // epoch day 0 was a Thursday -> +4 makes Sunday 0
   int  off = (dow - w1 + 7) % 7;
   return (datetime)((d1 + off + 7 * (n - 1)) * 86400);
  }

// Last weekday `dow` of a month, at 00:00.
datetime LastDow(const int y, const int mo, const int dow)
  {
   long dl = DaysFromCivil(y, mo, DaysInMonth(y, mo));
   int  wl = (int)((dl + 4) % 7);
   return (datetime)((dl - ((wl - dow + 7) % 7)) * 86400);
  }

int YearOf(const datetime t)
  {
   MqlDateTime s;
   TimeToStruct(t, s);
   return s.year;
  }

// EU summer time in force at this UTC instant? (last Sun Mar 01:00 UTC -> last Sun Oct 01:00 UTC)
bool EuSummer(const datetime utc)
  {
   int y = YearOf(utc);
   return (utc >= LastDow(y, 3, 0) + 3600 && utc < LastDow(y, 10, 0) + 3600);
  }

// US eastern daylight time in force at this UTC instant?
// Starts 2nd Sun Mar 02:00 EST (= 07:00 UTC), ends 1st Sun Nov 02:00 EDT (= 06:00 UTC).
bool UsSummer(const datetime utc)
  {
   int y = YearOf(utc);
   return (utc >= NthDow(y, 3, 0, 2) + 7 * 3600 && utc < NthDow(y, 11, 0, 1) + 6 * 3600);
  }

// The server clock's offset from UTC, in seconds, at a given SERVER stamp.
// Two-pass: guess using base+1h, then re-evaluate DST at the resulting UTC
// instant. The only stamps where the two passes could disagree are the ones
// inside the switch hour itself, which on a real server clock do not exist.
int ServerUtcOffsetSec(const datetime srv)
  {
   int base = InpServerGMTOffsetWinter * 3600;
   if(InpServerDST == SRV_DST_NONE)
      return base;

   datetime guess = srv - base - 3600;
   bool     dst   = (InpServerDST == SRV_DST_EU) ? EuSummer(guess) : UsSummer(guess);
   datetime utc   = srv - base - (dst ? 3600 : 0);
   bool     dst2  = (InpServerDST == SRV_DST_EU) ? EuSummer(utc) : UsSummer(utc);
   return base + (dst2 ? 3600 : 0);
  }

// A server stamp -> the same instant as New York wall time.
datetime ServerToNY(const datetime srv)
  {
   datetime utc = srv - ServerUtcOffsetSec(srv);
   return utc - (UsSummer(utc) ? 4 : 5) * 3600;
  }

// Day number (days since epoch) of a New York wall-clock time — our "trading day" key.
int NYDayNumber(const datetime ny)
  {
   return (int)((long)ny / 86400);
  }

int NYHour(const datetime ny)
  {
   return (int)(((long)ny % 86400) / 3600);
  }

// The "session day" a range-window instant belongs to: NYDayNumber() after
// shifting the clock back by the window's own start hour, so a window that
// crosses midnight (InpRangeStartHourNY + InpRangeHours > 24, e.g. start=22,
// hours=4 => NY 22:00-02:00) is one contiguous session instead of being cut
// in half by the calendar-day rollover. With the default InpRangeStartHourNY
// = 0 this is identical to NYDayNumber(ny).
int SessionDayNumber(const datetime ny)
  {
   return NYDayNumber(ny - (datetime)InpRangeStartHourNY * 3600);
  }

//======================= HELPERS ====================================

double PointSize()  { return SymbolInfoDouble(_Symbol, SYMBOL_POINT); }
int    DigitsOf()   { return (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS); }
double Norm(const double p) { return NormalizeDouble(p, DigitsOf()); }

// Is this NY stamp inside the range-building window? Measures the hour offset
// from the window's own start (rather than the raw NY hour) so a window that
// wraps past midnight is a single contiguous range, not two disjoint pieces.
bool InRangeWindow(const datetime ny)
  {
   int h = NYHour(ny - (datetime)InpRangeStartHourNY * 3600);
   return (h < InpRangeHours);
  }

// Lots that put `riskMoney` at risk over `slDistance` price units, clamped to
// the symbol's volume constraints. Returns 0 when the trade cannot be sized.
double LotsForRisk(const double riskMoney, const double slDistance)
  {
   if(slDistance <= 0.0)
      return 0.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0)
      return 0.0;

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0.0)
      return 0.0;

   double lots = riskMoney / lossPerLot;

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep > 0.0)
      lots = MathFloor(lots / lotStep) * lotStep;
   if(lots < minLot)
      lots = minLot;                      // never size below the broker's minimum
   if(lots > maxLot)
      lots = maxLot;
   return lots;
  }

// Shrink `lots` until the broker's margin requirement fits inside free margin.
// Without this, a very tight stop asks for a huge notional and the order comes
// back 10019 "not enough money" — which would silently delete those setups from
// the sample and quietly bias the backtest.
double FitToMargin(double lots, const bool isLong)
  {
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(free <= 0.0 || lots <= 0.0)
      return 0.0;

   double price = isLong ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(price <= 0.0)
      return lots;

   ENUM_ORDER_TYPE type = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(lotStep <= 0.0)
      lotStep = 0.01;

   double need = 0.0;
   // Keep a 10% buffer so a tick of adverse movement between calc and fill does
   // not tip the order over the margin line.
   for(int guard = 0; guard < 64; guard++)
     {
      if(!OrderCalcMargin(type, _Symbol, lots, price, need))
         return lots;                       // cannot tell — let the broker decide
      if(need <= free * 0.9)
         return lots;
      double scaled = lots * (free * 0.9) / MathMax(need, 1e-9);
      double next   = MathFloor(scaled / lotStep) * lotStep;
      if(next >= lots)                      // not converging
         next = lots - lotStep;
      lots = next;
      if(lots < minLot)
         return 0.0;                        // cannot afford even the minimum
     }
   return lots;
  }

// What one lot loses over `slDistance` — used to report the ACTUAL money at risk
// once the lot size has been rounded to the broker's step.
double RiskMoneyFor(const double lots, const double slDistance)
  {
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0)
      return 0.0;
   return lots * (slDistance / tickSize) * tickValue;
  }

int OpenPositionCount()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         n++;
     }
   return n;
  }

// A NY-session label for MBT's per-regime breakdown — far more informative than
// re-stating the direction, which the report already splits on.
string SessionLabel(const int nyHour)
  {
   if(nyHour <  8) return "LONDON";    // NY 04:00-08:00 — London morning
   if(nyHour < 12) return "NY_AM";     // the London/NY overlap
   if(nyHour < 16) return "NY_PM";
   if(nyHour < 20) return "LATE";
   return "ASIA";
  }

//======================= LIFECYCLE ==================================

int OnInit()
  {
   if(_Period != PERIOD_M5)
      PrintFormat("FourHourRangeScalper: WARNING — the strategy is specified on M5 "
                  "but this chart is %s. Running anyway; results are not the strategy.",
                  EnumToString((ENUM_TIMEFRAMES)_Period));

   if(InpRangeHours <= 0 || InpRangeHours > 24)
     {
      Print("FourHourRangeScalper: InpRangeHours must be 1..24.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpRangeStartHourNY < 0 || InpRangeStartHourNY > 23)
     {
      Print("FourHourRangeScalper: InpRangeStartHourNY must be 0..23.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpRR <= 0.0)
     {
      Print("FourHourRangeScalper: InpRR must be > 0.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpRiskPercent <= 0.0 && InpFixedLots <= 0.0)
     {
      Print("FourHourRangeScalper: set InpRiskPercent > 0, or InpFixedLots > 0.");
      return INIT_PARAMETERS_INCORRECT;
     }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   if(InpLogSignals)
      ResetSignalLog();          // the file always reflects THIS run

   ArrayResize(g_open, 0);
   ArrayResize(g_rs,   0);

   // --- clock sanity check ------------------------------------------------
   // Cross-check the configured offset against the terminal's own idea of GMT.
   // TimeGMT() is not dependable in the tester, so this only ever warns.
   datetime now   = TimeCurrent();
   int      mine  = ServerUtcOffsetSec(now) / 3600;
   int      theirs = (int)MathRound((double)(now - TimeGMT()) / 3600.0);
   datetime ny    = ServerToNY(now);
   PrintFormat("FourHourRangeScalper: server %s -> New York %s  (server is GMT%+d, NY offset %+d h). "
               "Range window = NY %02d:00-%02d:00%s.",
               TimeToString(now, TIME_DATE|TIME_MINUTES),
               TimeToString(ny,  TIME_DATE|TIME_MINUTES),
               mine, (int)((ny - now) / 3600),
               InpRangeStartHourNY, (InpRangeStartHourNY + InpRangeHours) % 24,
               (InpRangeStartHourNY + InpRangeHours > 24) ? " (+1 day)" : "");
   // TimeGMT() is modelled, not real, inside the Strategy Tester — it reports
   // GMT+0 there regardless of the broker — so only cross-check when live.
   if(mine != theirs && !MQLInfoInteger(MQL_TESTER))
      PrintFormat("FourHourRangeScalper: NOTE — configured server offset is GMT%+d but the terminal "
                  "reports GMT%+d. Check InpServerGMTOffsetWinter / InpServerDST.",
                  mine, theirs);

   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { }

//+------------------------------------------------------------------+
//| Everything happens on the close of an M5 bar. Acting on bar 1    |
//| (the bar that just closed) and never on bar 0 is what keeps this |
//| free of look-ahead: the transcript's rules are all "the candle   |
//| closes ...", and a forming candle has no close.                  |
//+------------------------------------------------------------------+
void OnTick()
  {
   ReapClosedPositions();

   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 == g_lastBar || t0 == 0)
      return;
   g_lastBar = t0;

   // Bar 1 is the one that just closed.
   datetime barTime  = iTime(_Symbol, _Period, 1);
   if(barTime == 0)
      return;
   double   barHigh  = iHigh(_Symbol, _Period, 1);
   double   barLow   = iLow(_Symbol, _Period, 1);
   double   barClose = iClose(_Symbol, _Period, 1);
   if(barHigh == 0.0 && barLow == 0.0)
      return;

   datetime ny    = ServerToNY(barTime);
   int      nyDay = SessionDayNumber(ny);

   // --- NY day rollover --------------------------------------------------
   if(nyDay != g_nyDay)
     {
      if(InpCloseAtDayEnd)
         CloseAllPositions("day end");
      g_nyDay       = nyDay;
      g_state       = RS_WAIT_RANGE;
      g_rangeHigh   = 0.0;
      g_rangeLow    = 0.0;
      g_rangeBars   = 0;
      g_excExtreme  = 0.0;
      g_tradesToday = 0;
     }

   // --- Step 1: build the range while we are inside the window ------------
   if(InRangeWindow(ny))
     {
      if(g_state == RS_WAIT_RANGE)
        {
         if(g_rangeBars == 0)
           {
            g_rangeHigh = barHigh;
            g_rangeLow  = barLow;
           }
         else
           {
            g_rangeHigh = MathMax(g_rangeHigh, barHigh);
            g_rangeLow  = MathMin(g_rangeLow,  barLow);
           }
         g_rangeBars++;
        }
      return;                       // never trade inside the range window itself
     }

   // --- the window has closed: promote the range once ---------------------
   if(g_state == RS_WAIT_RANGE)
     {
      int expected = (int)((InpRangeHours * 3600) / PeriodSeconds(_Period));
      if(g_rangeBars < (int)MathCeil(expected * InpMinRangeCoverage) || g_rangeHigh <= g_rangeLow)
        {
         // Too little data in the window (holiday, weekend rollover, symbol not
         // yet trading) — the range would not be the one a chart shows. Stand down.
         g_state = RS_NO_RANGE;
         if(InpVerbose)
            PrintFormat("FourHourRangeScalper: %s — no range (%d of %d expected bars in the NY window).",
                        TimeToString(ny, TIME_DATE), g_rangeBars, expected);
         return;
        }

      g_state = RS_INSIDE;
      if(InpVerbose)
         PrintFormat("FourHourRangeScalper: %s NY range = %s / %s (%d bars, height %s).",
                     TimeToString(ny, TIME_DATE),
                     DoubleToString(g_rangeHigh, DigitsOf()),
                     DoubleToString(g_rangeLow,  DigitsOf()),
                     g_rangeBars,
                     DoubleToString(g_rangeHigh - g_rangeLow, DigitsOf()));

      // The first bar after the window may already be outside the range. The
      // transcript always shows the breakout happening from inside, during the
      // day, so by default that is NOT an armed breakout (spec §7.3).
      if(InpAllowPreArmedOutside)
         ArmIfOutside(barHigh, barLow, barClose);
      return;
     }

   if(g_state == RS_NO_RANGE || g_state == RS_DONE)
      return;

   // --- Step 2/3: the breakout -> re-entry cycle --------------------------
   StepSetup(barHigh, barLow, barClose, ny);
  }

//+------------------------------------------------------------------+
//| Arm a breakout if this bar is already beyond an edge.            |
//+------------------------------------------------------------------+
void ArmIfOutside(const double h, const double l, const double c)
  {
   if(BrokeAbove(h, c))
     {
      g_state      = RS_BROKEN_UP;
      g_excExtreme = h;
     }
   else if(BrokeBelow(l, c))
     {
      g_state      = RS_BROKEN_DOWN;
      g_excExtreme = l;
     }
  }

// "the candle must fully close outside so wicks alone don't count" [00:02:58]
bool BrokeAbove(const double h, const double c)
  {
   return (InpBreakoutMode == BRK_CLOSE_OUTSIDE) ? (c > g_rangeHigh) : (h > g_rangeHigh);
  }
bool BrokeBelow(const double l, const double c)
  {
   return (InpBreakoutMode == BRK_CLOSE_OUTSIDE) ? (c < g_rangeLow) : (l < g_rangeLow);
  }

// "we then wait for price to re-enter and close back inside the range" [00:03:04]
bool ClosedInside(const double c)
  {
   return (c <= g_rangeHigh && c >= g_rangeLow);
  }

//+------------------------------------------------------------------+
//| The per-bar state machine (spec §2, §4).                         |
//+------------------------------------------------------------------+
void StepSetup(const double h, const double l, const double c, const datetime ny)
  {
   switch(g_state)
     {
      case RS_INSIDE:
         ArmIfOutside(h, l, c);
         break;

      case RS_BROKEN_UP:
         // Track the excursion extreme — "the exact high of the breakout move"
         // [00:03:22]. The re-entry bar's own high counts: it is still part of
         // the move that is being faded.
         g_excExtreme = MathMax(g_excExtreme, h);
         if(ClosedInside(c))
            TakeEntry(false, c, ny);                 // broke the high -> SHORT [00:03:16]
         else if(BrokeBelow(l, c))
           {
            // Closed straight through the range to the other side. It never
            // closed INSIDE, so this is not a re-entry — flip the side instead
            // (spec §7.2; the transcript never shows this case).
            g_state      = RS_BROKEN_DOWN;
            g_excExtreme = l;
           }
         break;

      case RS_BROKEN_DOWN:
         g_excExtreme = MathMin(g_excExtreme, l);
         if(ClosedInside(c))
            TakeEntry(true, c, ny);                  // broke the low -> LONG [00:04:19]
         else if(BrokeAbove(h, c))
           {
            g_state      = RS_BROKEN_UP;
            g_excExtreme = h;
           }
         break;

      default:
         break;
     }
  }

//+------------------------------------------------------------------+
//| A completed breakout -> re-entry sequence. Size it, place it,    |
//| log it, then re-arm for the next setup of the day [00:04:38].    |
//+------------------------------------------------------------------+
void TakeEntry(const bool isLong, const double entry, const datetime ny)
  {
   // Whatever happens below, price has closed back inside: the next breakout
   // starts from scratch.
   g_state      = RS_INSIDE;
   double anchor = g_excExtreme;
   g_excExtreme  = 0.0;

   g_sigTotal++;

   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay)
     {
      g_state = RS_DONE;
      g_skipCap++;
      return;
     }

   // --- stop loss (spec §3.3) --------------------------------------------
   double sl = (InpSLAnchor == SL_BREAKOUT_EXTREME) ? anchor
                                                    : (isLong ? g_rangeLow : g_rangeHigh);
   double buf = InpSLBufferPoints * PointSize();
   sl = isLong ? (sl - buf) : (sl + buf);

   double risk = isLong ? (entry - sl) : (sl - entry);
   if(risk <= 0.0)
      return;                                  // degenerate geometry — nothing to trade

   // --- the "breakout was too large" exception (spec §3.5) ---------------
   double rangeH = g_rangeHigh - g_rangeLow;
   if(InpOversizedSL != OVER_ALLOW && rangeH > 0.0 && risk > InpMaxSLRangeMult * rangeH)
     {
      if(InpOversizedSL == OVER_SKIP)
        {
         g_skipOversz++;
         return;
        }

      double capped = risk;
      if(InpOversizedSL == OVER_CAP)
         capped = InpMaxSLRangeMult * rangeH;
      else if(InpOversizedSL == OVER_SWING)
        {
         double sw = NearestSwing(isLong, entry);
         if(sw > 0.0)
            capped = isLong ? (entry - sw) : (sw - entry);
        }

      if(capped > 0.0 && capped < risk)
        {
         risk = capped;
         sl   = isLong ? (entry - risk) : (entry + risk);
        }
     }

   // --- unusably tight stops -------------------------------------------
   // A stop inside the broker's own minimum distance cannot be placed at all,
   // and a stop of a pip or two on a 5-minute chart is inside the spread: the
   // transcript's trader is reading a chart, not shaving ticks. Count these
   // rather than letting the order be rejected downstream.
   double stopsLevel = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist    = MathMax(stopsLevel, InpMinSLPoints) * PointSize();
   if(minDist > 0.0 && risk < minDist)
     {
      g_skipTight++;
      return;
     }

   double tp = isLong ? (entry + InpRR * risk) : (entry - InpRR * risk);
   sl = Norm(sl);
   tp = Norm(tp);

   // --- log the signal BEFORE the fill, so the logged trade is the STRATEGY's
   // trade (entry = the re-entry bar's close) regardless of what the fill does.
   // MBT's python replay locates the bar by matching this entry to a bar close,
   // which is exactly what it is — so signal_parity is meaningful here.
   if(InpLogSignals)
      LogSignal(1, isLong, Norm(entry), sl, tp, SessionLabel(NYHour(ny)));

   if(InpOnePositionAtATime && OpenPositionCount() > 0)
     {
      g_skipOverlap++;                         // sequential only; the python replay skips these too
      return;
     }

   // --- size and send -----------------------------------------------------
   double lots;
   if(InpRiskPercent > 0.0)
     {
      double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
      lots = LotsForRisk(riskMoney, risk);
     }
   else
      lots = InpFixedLots;

   if(InpMaxLots > 0.0 && lots > InpMaxLots)
      lots = InpMaxLots;

   double wanted = lots;
   lots = FitToMargin(lots, isLong);
   if(lots > 0.0 && lots < wanted)
      g_marginCut++;

   if(lots <= 0.0)
     {
      g_skipSizing++;
      if(InpVerbose)
         PrintFormat("FourHourRangeScalper: could not size a trade (risk distance %s) — skipped.",
                     DoubleToString(risk, DigitsOf()));
      return;
     }

   bool ok = isLong ? g_trade.Buy(lots, _Symbol, 0.0, sl, tp, "4HR scalp")
                    : g_trade.Sell(lots, _Symbol, 0.0, sl, tp, "4HR scalp");
   if(!ok)
     {
      g_skipReject++;
      PrintFormat("FourHourRangeScalper: order rejected (%d %s) — %s %s lots, sl %s tp %s.",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription(),
                  isLong ? "BUY" : "SELL", DoubleToString(lots, 2),
                  DoubleToString(sl, DigitsOf()), DoubleToString(tp, DigitsOf()));
      return;
     }

   g_tradesToday++;
   RememberPosition(g_trade.ResultDeal(), RiskMoneyFor(lots, risk));
  }

//+------------------------------------------------------------------+
//| OVER_SWING: the nearest M5 swing high/low inside the excursion — |
//| a mechanical stand-in for the transcript's "nearest key level"   |
//| [00:07:06]. Walks back from the just-closed bar to the bar the   |
//| excursion extreme was set on, and returns the closest fractal    |
//| that still sits beyond the entry. 0.0 if none.                   |
//+------------------------------------------------------------------+
double NearestSwing(const bool isLong, const double entry)
  {
   int k = InpSwingLookback;
   if(k < 1)
      return 0.0;

   int limit = MathMin(Bars(_Symbol, _Period) - k - 1, 200);
   for(int i = 1 + k; i <= limit; i++)
     {
      bool isSwing = true;
      if(isLong)
        {
         double v = iLow(_Symbol, _Period, i);
         for(int j = 1; j <= k && isSwing; j++)
            if(iLow(_Symbol, _Period, i - j) <= v || iLow(_Symbol, _Period, i + j) <= v)
               isSwing = false;
         if(isSwing && v < entry)
            return v;
        }
      else
        {
         double v = iHigh(_Symbol, _Period, i);
         for(int j = 1; j <= k && isSwing; j++)
            if(iHigh(_Symbol, _Period, i - j) >= v || iHigh(_Symbol, _Period, i + j) >= v)
               isSwing = false;
         if(isSwing && v > entry)
            return v;
        }
     }
   return 0.0;
  }

//======================= POSITION BOOKKEEPING =======================

void RememberPosition(const ulong dealTicket, const double riskMoney)
  {
   ulong posId = 0;
   if(HistoryDealSelect(dealTicket))
      posId = (ulong)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
   if(posId == 0)
      return;

   int n = ArraySize(g_open);
   ArrayResize(g_open, n + 1);
   g_open[n].posId     = posId;
   g_open[n].riskMoney = riskMoney;
   g_open[n].opened    = TimeCurrent();
  }

//+------------------------------------------------------------------+
//| Turn every position that has closed since the last check into a  |
//| realised R and drop it from the watch list. R = net money / the  |
//| money that was at risk when the trade was opened — so a stop-out |
//| is -1R by construction and the summary reads in the transcript's |
//| own units.                                                       |
//+------------------------------------------------------------------+
void ReapClosedPositions()
  {
   for(int i = ArraySize(g_open) - 1; i >= 0; i--)
     {
      if(PositionSelectByTicket(g_open[i].posId))
         continue;                              // still open

      double net = 0.0;
      if(HistorySelectByPosition(g_open[i].posId))
        {
         for(int d = HistoryDealsTotal() - 1; d >= 0; d--)
           {
            ulong dt = HistoryDealGetTicket(d);
            if(dt == 0)
               continue;
            net += HistoryDealGetDouble(dt, DEAL_PROFIT)
                 + HistoryDealGetDouble(dt, DEAL_SWAP)
                 + HistoryDealGetDouble(dt, DEAL_COMMISSION);
           }
        }

      if(g_open[i].riskMoney > 0.0)
        {
         int n = ArraySize(g_rs);
         ArrayResize(g_rs, n + 1);
         g_rs[n] = net / g_open[i].riskMoney;
        }

      // drop entry i
      int last = ArraySize(g_open) - 1;
      g_open[i] = g_open[last];
      ArrayResize(g_open, last);
     }
  }

void CloseAllPositions(const string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      g_trade.PositionClose(ticket);
     }
   if(InpVerbose)
      PrintFormat("FourHourRangeScalper: closed open positions (%s).", why);
  }

//======================= TESTER SUMMARY =============================
//
// MBT's core/tester.py read_ea_summary() looks for <EAname>_tester.csv in
// Common/Files and treats it as the most reliable metrics source, because it
// does not depend on parsing MT5's HTML report. Emit the R-based numbers the
// transcript itself speaks in ("a 2 R profit", "a 1 R loss", "a total gain of 8R").

double OnTester()
  {
   ReapClosedPositions();

   int    n = ArraySize(g_rs);
   int    wins = 0, losses = 0;
   double net = 0.0, gw = 0.0, gl = 0.0;
   double peak = 0.0, equity = 0.0, mdd = 0.0;
   int    winStreak = 0, lossStreak = 0, curWin = 0, curLoss = 0;

   for(int i = 0; i < n; i++)
     {
      double r = g_rs[i];
      net += r;
      if(r > 0.0) { wins++;   gw += r;      curWin++;  curLoss = 0; }
      else        { losses++; gl += -r;     curLoss++; curWin  = 0; }
      if(curWin  > winStreak)  winStreak  = curWin;
      if(curLoss > lossStreak) lossStreak = curLoss;

      equity += r;
      if(equity > peak) peak = equity;
      if(peak - equity > mdd) mdd = peak - equity;
     }

   double expectancy = (n > 0) ? net / n : 0.0;
   double winRate    = (n > 0) ? 100.0 * wins / n : 0.0;
   double pf         = (gl > 0.0) ? gw / gl : (gw > 0.0 ? 1e9 : 0.0);

   if(InpWriteTesterCsv)
     {
      string name = "FourHourRangeScalper_tester.csv";
      int h = FileOpen(name, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(h != INVALID_HANDLE)
        {
         FileWriteString(h, "metric,value\n");
         FileWriteString(h, StringFormat("trades,%d\n",          n));
         FileWriteString(h, StringFormat("wins,%d\n",            wins));
         FileWriteString(h, StringFormat("losses,%d\n",          losses));
         FileWriteString(h, StringFormat("win_rate_pct,%.2f\n",  winRate));
         FileWriteString(h, StringFormat("net_r,%.3f\n",         net));
         FileWriteString(h, StringFormat("expectancy_r,%.4f\n",  expectancy));
         FileWriteString(h, StringFormat("profit_factor,%.3f\n", pf));
         FileWriteString(h, StringFormat("max_drawdown_r,%.3f\n", mdd));
         FileWriteString(h, StringFormat("avg_win_r,%.3f\n",     wins   > 0 ? gw / wins   : 0.0));
         FileWriteString(h, StringFormat("avg_loss_r,%.3f\n",    losses > 0 ? gl / losses : 0.0));
         FileWriteString(h, StringFormat("max_win_streak,%d\n",  winStreak));
         FileWriteString(h, StringFormat("max_loss_streak,%d\n", lossStreak));
         FileWriteString(h, StringFormat("net_profit_ccy,%.2f\n",
                                         TesterStatistics(STAT_PROFIT)));
         // Signal accounting — how many setups the STRATEGY produced vs how many
         // became trades, and why the rest did not. Without this a backtest can
         // look clean while quietly discarding most of its own signals.
         FileWriteString(h, StringFormat("setups_total,%d\n",        g_sigTotal));
         FileWriteString(h, StringFormat("skipped_overlap,%d\n",     g_skipOverlap));
         FileWriteString(h, StringFormat("skipped_tight_stop,%d\n",  g_skipTight));
         FileWriteString(h, StringFormat("skipped_oversized,%d\n",   g_skipOversz));
         FileWriteString(h, StringFormat("skipped_daily_cap,%d\n",   g_skipCap));
         FileWriteString(h, StringFormat("skipped_sizing,%d\n",      g_skipSizing));
         FileWriteString(h, StringFormat("skipped_rejected,%d\n",    g_skipReject));
         FileWriteString(h, StringFormat("margin_reduced_lots,%d\n", g_marginCut));
         FileClose(h);
        }
      else
         PrintFormat("FourHourRangeScalper: could not write %s to Common/Files (error %d).",
                     name, GetLastError());
     }

   PrintFormat("FourHourRangeScalper: %d trades, %d/%d W/L (%.1f%%), net %+.2fR, "
               "expectancy %+.3fR, PF %.2f, max DD %.2fR.",
               n, wins, losses, winRate, net, expectancy, pf, mdd);
   PrintFormat("FourHourRangeScalper: %d setups -> skipped %d overlap, %d tight stop, "
               "%d oversized, %d daily cap, %d unsizeable, %d rejected; %d lot-capped by margin.",
               g_sigTotal, g_skipOverlap, g_skipTight, g_skipOversz,
               g_skipCap, g_skipSizing, g_skipReject, g_marginCut);

   return expectancy;     // optimise on expectancy in R, not on raw currency
  }
//+------------------------------------------------------------------+
