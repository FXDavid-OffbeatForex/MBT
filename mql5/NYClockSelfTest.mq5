//+------------------------------------------------------------------+
//| NYClockSelfTest.mq5                                              |
//| Server-clock -> New York calendar mapping for the 4H-range port. |
//|                                                                  |
//| This is the timezone module of the strategy, packaged on its own  |
//| with an init-time verification harness so the mapping can be      |
//| proven BEFORE any trading logic is hung off it. Drop the          |
//| "CLOCK" section into the EA verbatim; keep this file as the test. |
//|                                                                  |
//| Design rules:                                                    |
//|  * No TimeGMT(), TimeLocal(), TimeDaylightSavings() or           |
//|    TimeGMTOffset(): all four describe the TERMINAL's machine, and |
//|    in the Strategy Tester they are modelled, not real.            |
//|  * No PERIOD_H4: MT5 aligns H4 bars to server 00:00/04:00/...,    |
//|    and NY 00:00-04:00 is server 07:00-11:00 (06:00-10:00 in the   |
//|    DST-mismatch weeks) - never an H4 boundary on an EET broker.   |
//|  * Everything is pure calendar arithmetic on bar timestamps, so   |
//|    tester and live are bit-identical by construction.             |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Proves the server<->New York mapping used by the 4-hour range strategy."

enum ENUM_DST_RULE
  {
   DST_NONE,   // clock never shifts (fixed UTC offset)
   DST_EU,     // last Sun Mar 01:00 UTC -> last Sun Oct 01:00 UTC (EET/EEST brokers)
   DST_US      // 2nd Sun Mar 02:00 local -> 1st Sun Nov 02:00 local (America/New_York)
  };

enum ENUM_WINDOW_MODE
  {
   WINDOW_WALLCLOCK,  // 00:00 -> 04:00 on the NY wall clock (what a NY-timezone chart draws)
   WINDOW_ELAPSED     // 240 real minutes from the day start (never shortened by a DST jump)
  };

//======================== INPUTS ====================================
input group "Broker clock - the ONLY two values the user must get right"
input int             InpSrvStdOffsetHours  = 2;        // server UTC offset in WINTER (EET = +2)
input ENUM_DST_RULE   InpSrvDstRule         = DST_EU;   // DST rule the server clock follows

input group "Reference clock - the timezone the strategy's rules are written in"
input int             InpTgtStdOffsetHours  = -5;       // New York standard offset (EST = -5)
input ENUM_DST_RULE   InpTgtDstRule         = DST_US;   // New York DST rule

input group "Day + range window (PORTING DECISIONS - the source gives no clock time)"
input int             InpDayStartHourTgt    = 0;        // hour the trading day starts, NY wall clock
input int             InpRangeOffsetMin     = 0;        // range window start, minutes after day start
input int             InpRangeLenMin        = 240;      // range window length in minutes
input ENUM_WINDOW_MODE InpWindowMode        = WINDOW_WALLCLOCK;
input ENUM_TIMEFRAMES InpRangeTF            = PERIOD_M5;// bars the range is aggregated from
input double          InpMinWindowCoverage  = 0.80;     // fraction of expected bars required, else no range

input group "Init verification"
input bool            InpRunSelfTest        = true;     // fixed calendar vectors
input bool            InpRunWeekProbe       = true;     // empirical: week close must land on NY 17:00
input int             InpWeekProbeWeeks     = 26;       // how many weeks of history to scan
input int             InpWeekCloseHourTgt   = 17;       // FX/metals week close in NY (Sun 17:00 open)
input double          InpWeekProbeMinPass   = 0.80;     // pass fraction (holidays shorten some weeks)
input bool            InpPrintDstRegimes    = true;     // print this year's offset-change dates
input bool            InpFailInitOnMismatch = true;     // refuse to run on a failed self-test

//====================== CLOCK (drop-in) =============================
// --- civil calendar, no strings, no MqlDateTime on the hot path ----
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
   long yoe = (long)(y - era * 400);
   long doy = (long)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
   long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
   return era * 146097 + doe - 719468;
  }

// n-th (1-based) weekday `dow` (0=Sun) of a month, at 00:00 of that day.
datetime NthDow(const int y, const int mo, const int dow, const int n)
  {
   long d1  = DaysFromCivil(y, mo, 1);
   int  w1  = (int)((d1 + 4) % 7);        // 1970-01-01 was a Thursday
   int  off = (dow - w1 + 7) % 7;
   return (datetime)((d1 + off + 7 * (n - 1)) * 86400);
  }

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

// --- the two DST rules, both evaluated at a UTC instant ------------
// EU switches simultaneously across the union at 01:00 UTC, so the rule is
// naturally expressed in UTC and has no ambiguity there.
bool EuSummerUtc(const datetime utc)
  {
   int y = YearOf(utc);
   return (utc >= LastDow(y, 3, 0) + 3600 && utc < LastDow(y, 10, 0) + 3600);
  }

// US: 02:00 local standard (= 07:00 UTC) -> 02:00 local daylight (= 06:00 UTC).
// Pre-2007 the window was 1st Sun Apr -> last Sun Oct (02:00 local, same UTC hours).
bool UsSummerUtc(const datetime utc)
  {
   int y = YearOf(utc);
   datetime a, b;
   if(y >= 2007)
     {
      a = NthDow(y, 3, 0, 2)  + 7 * 3600;
      b = NthDow(y, 11, 0, 1) + 6 * 3600;
     }
   else
     {
      a = NthDow(y, 4, 0, 1)  + 7 * 3600;
      b = LastDow(y, 10, 0)   + 6 * 3600;
     }
   return (utc >= a && utc < b);
  }

bool SummerUtc(const datetime utc, const ENUM_DST_RULE rule)
  {
   if(rule == DST_EU) return EuSummerUtc(utc);
   if(rule == DST_US) return UsSummerUtc(utc);
   return false;
  }

int OffsetAtUtc(const datetime utc, const int stdH, const ENUM_DST_RULE rule)
  {
   return stdH * 3600 + (SummerUtc(utc, rule) ? 3600 : 0);
  }

datetime UtcToLocal(const datetime utc, const int stdH, const ENUM_DST_RULE rule)
  {
   return utc + (datetime)OffsetAtUtc(utc, stdH, rule);
  }

// Inverse. Try the standard-offset candidate first; if DST turns out to be in
// force at that instant, step back an hour and re-check. Only the one repeated
// hour per year is ambiguous, and this branch resolves it to STANDARD time.
datetime LocalToUtc(const datetime loc, const int stdH, const ENUM_DST_RULE rule)
  {
   datetime g1 = loc - (datetime)(stdH * 3600);
   if(!SummerUtc(g1, rule))
      return g1;
   datetime g2 = g1 - 3600;
   return SummerUtc(g2, rule) ? g2 : g1;
  }

// --- the three conversions the strategy actually calls -------------
datetime SrvToUtc(const datetime srv) { return LocalToUtc(srv, InpSrvStdOffsetHours, InpSrvDstRule); }
datetime UtcToSrv(const datetime utc) { return UtcToLocal(utc, InpSrvStdOffsetHours, InpSrvDstRule); }
datetime UtcToTgt(const datetime utc) { return UtcToLocal(utc, InpTgtStdOffsetHours, InpTgtDstRule); }
datetime TgtToUtc(const datetime tgt) { return LocalToUtc(tgt, InpTgtStdOffsetHours, InpTgtDstRule); }
datetime SrvToTgt(const datetime srv) { return UtcToTgt(SrvToUtc(srv)); }

// --- (a) which New York calendar day does a bar belong to? ---------
// Returned as a day INDEX (days since epoch of the day's start), which is the
// only identity the EA needs: compare it, do not format it.
long TgtDayIndex(const datetime tgt)
  {
   long s = (long)tgt - (long)InpDayStartHourTgt * 3600;
   return (s >= 0) ? s / 86400 : -((-s + 86399) / 86400);
  }

datetime TgtDayStart(const long idx)
  {
   return (datetime)(idx * 86400 + (long)InpDayStartHourTgt * 3600);
  }

long SrvBarDayIndex(const datetime srv) { return TgtDayIndex(SrvToTgt(srv)); }

// --- (b) is a bar inside the range window? -------------------------
// Both edges are converted NY -> UTC once per day, so membership is a plain
// comparison of UTC instants and needs no wall-clock hour arithmetic.
void WindowUtc(const long idx, datetime &aUtc, datetime &bUtc)
  {
   datetime ws = TgtDayStart(idx) + (datetime)(InpRangeOffsetMin * 60);
   aUtc = TgtToUtc(ws);
   bUtc = (InpWindowMode == WINDOW_ELAPSED)
          ? aUtc + (datetime)(InpRangeLenMin * 60)
          : TgtToUtc(ws + (datetime)(InpRangeLenMin * 60));
  }

void WindowSrv(const long idx, datetime &aSrv, datetime &bSrv)
  {
   datetime a, b;
   WindowUtc(idx, a, b);
   aSrv = UtcToSrv(a);
   bSrv = UtcToSrv(b);
  }

bool BarInWindow(const datetime srvBarOpen, const long idx)
  {
   datetime a, b, u = SrvToUtc(srvBarOpen);
   WindowUtc(idx, a, b);
   return (u >= a && u < b);          // bars are stamped at OPEN: [a, b) == the H4 candle
  }

//====================== RANGE BUILDER ===============================
struct RangeDay
  {
   long     idx;          // NY day index
   bool     valid;
   double   hi, lo;
   int      bars, expected;
   datetime srvFrom, srvTo;
  };

bool BuildRange(const long idx, RangeDay &r)
  {
   r.idx = idx; r.valid = false; r.hi = 0.0; r.lo = 0.0; r.bars = 0;
   datetime aUtc, bUtc;
   WindowUtc(idx, aUtc, bUtc);
   r.srvFrom = UtcToSrv(aUtc);
   r.srvTo   = UtcToSrv(bUtc);
   int psec  = PeriodSeconds(InpRangeTF);
   r.expected = (int)((bUtc - aUtc) / psec);        // DST-aware: 36 or 60 on the two Sundays

   MqlRates rates[];
   int n = CopyRates(_Symbol, InpRangeTF, r.srvFrom, r.srvTo - 1, rates);
   if(n <= 0)
      return false;

   double hi = -DBL_MAX, lo = DBL_MAX;
   int used = 0;
   for(int i = 0; i < n; i++)
     {
      if(!BarInWindow(rates[i].time, idx))          // guards the CopyRates edges
         continue;
      hi = MathMax(hi, rates[i].high);
      lo = MathMin(lo, rates[i].low);
      used++;
     }
   r.bars = used;
   if(used <= 0 || r.expected <= 0)
      return false;
   if((double)used / (double)r.expected < InpMinWindowCoverage)
      return false;
   r.hi = hi; r.lo = lo; r.valid = true;
   return true;
  }

//====================== VERIFICATION ================================
string TS(const datetime t) { return TimeToString(t, TIME_DATE | TIME_MINUTES); }

int DeltaHours(const datetime srv) { return (int)((srv - SrvToTgt(srv)) / 3600); }

// Fixed vectors. They exercise the CALENDAR, so they call the conversions with
// explicit EET/EU + NY/US parameters and are independent of the inputs.
bool Vec(const string srvStr, const string wantTgtStr, int &pass, int &fail)
  {
   datetime srv  = StringToTime(srvStr);
   datetime utc  = LocalToUtc(srv, 2, DST_EU);
   datetime tgt  = UtcToLocal(utc, -5, DST_US);
   datetime want = StringToTime(wantTgtStr);
   bool ok = (tgt == want);
   if(ok) pass++; else fail++;
   if(!ok)
      PrintFormat("[CLOCK] SELFTEST FAIL  server %s -> NY %s  (expected %s)", srvStr, TS(tgt), wantTgtStr);
   return ok;
  }

int RunSelfTest()
  {
   int pass = 0, fail = 0;
   // --- both clocks on standard time: NY = server - 7h
   Vec("2026.01.15 12:00", "2026.01.15 05:00", pass, fail);
   Vec("2026.11.05 12:00", "2026.11.05 05:00", pass, fail);
   // --- both clocks on summer time: still -7h
   Vec("2026.04.01 12:00", "2026.04.01 05:00", pass, fail);
   Vec("2026.07.15 12:00", "2026.07.15 05:00", pass, fail);
   // --- MISMATCH WEEKS: US already on DST / still on DST, EU not -> -6h
   Vec("2026.03.09 12:00", "2026.03.09 06:00", pass, fail);   // Mar 08 -> Mar 29
   Vec("2026.03.28 12:00", "2026.03.28 06:00", pass, fail);
   Vec("2026.10.27 12:00", "2026.10.27 06:00", pass, fail);   // Oct 25 -> Nov 01
   Vec("2026.10.31 12:00", "2026.10.31 06:00", pass, fail);
   // --- the EU switch instant (01:00 UTC): server 02:59 EET then 04:00 EEST
   Vec("2026.03.29 02:59", "2026.03.28 20:59", pass, fail);   // delta 6
   Vec("2026.03.29 04:00", "2026.03.28 21:00", pass, fail);   // delta 7
   // --- the US switch instant (07:00 UTC) INSIDE the range window:
   //     NY 02:00 never happens, so that day's 00:00-04:00 window is 3h long
   Vec("2026.03.08 08:59", "2026.03.08 01:59", pass, fail);
   Vec("2026.03.08 09:00", "2026.03.08 03:00", pass, fail);
   // --- US fall back (06:00 UTC): NY 01:00 happens twice
   Vec("2026.11.01 07:59", "2026.11.01 01:59", pass, fail);
   Vec("2026.11.01 08:00", "2026.11.01 01:00", pass, fail);
   // --- pre-2007 US rule (only matters for deep backtests)
   {
    datetime utc = StringToTime("2005.03.20 12:00");          // US NOT yet on DST in 2005
    if(UsSummerUtc(utc)) { fail++; Print("[CLOCK] SELFTEST FAIL  pre-2007 US rule"); } else pass++;
   }
   PrintFormat("[CLOCK] selftest: %d passed, %d failed.", pass, fail);
   return fail;
  }

// Empirical probe. The FX/metals week is pinned to NY 17:00 by convention, so
// the last bar before the weekend gap MUST map to NY 16:xx under a correct
// mapping - including in the 6-hour mismatch weeks, where that same bar sits at
// server 22:5x instead of 23:5x. This validates offset AND both DST rules
// against the broker's own data, with no external reference.
void RunWeekProbe()
  {
   int psec = PeriodSeconds(InpRangeTF);
   datetime to   = (datetime)iTime(_Symbol, InpRangeTF, 0);
   if(to <= 0) { Print("[CLOCK] week-probe: no history on ", EnumToString(InpRangeTF), "."); return; }
   datetime from = to - (datetime)(InpWeekProbeWeeks * 7 * 86400);

   MqlRates r[];
   int n = CopyRates(_Symbol, InpRangeTF, from, to, r);
   if(n < 100) { Print("[CLOCK] week-probe: not enough history (", n, " bars)."); return; }

   int weeks = 0, ok = 0, shown = 0;
   for(int i = 0; i < n - 1; i++)
     {
      if(r[i + 1].time - r[i].time < 12 * 3600)        // not a weekend gap
         continue;
      weeks++;
      datetime closeTgt = SrvToTgt(r[i].time + psec);   // the bar's CLOSE, in NY
      long     sod      = ((long)closeTgt) % 86400;
      long     want     = (long)InpWeekCloseHourTgt * 3600;
      bool     good     = (sod > want - 3600 && sod <= want);   // 16:00 < close <= 17:00
      if(good) ok++;
      if(!good || shown < 3)
        {
         PrintFormat("[CLOCK] week-probe %s  last bar server %s -> NY close %s  delta %dh  %s",
                     (good ? "ok  " : "FAIL"), TS(r[i].time), TS(closeTgt),
                     DeltaHours(r[i].time), (good ? "" : "<-- mapping is wrong"));
         shown++;
        }
     }
   if(weeks == 0)
     {
      Print("[CLOCK] week-probe: no weekend gap found - 24/7 symbol, probe not applicable.");
      return;
     }
   PrintFormat("[CLOCK] week-probe: %d/%d week closes land on NY %02d:00 (%.0f%%) -> mapping %s",
               ok, weeks, InpWeekCloseHourTgt, 100.0 * ok / weeks,
               ((double)ok / weeks >= InpWeekProbeMinPass ? "CONFIRMED" : "REJECTED"));
  }

// Print the dates on which server->NY changes size this year: four transitions,
// two 6-hour windows. A user can eyeball these against any tz table.
void PrintDstRegimes(const datetime now)
  {
   int y = YearOf(now);
   datetime d = (datetime)(DaysFromCivil(y, 1, 1) * 86400) + 12 * 3600;
   int prev = DeltaHours(d);
   PrintFormat("[CLOCK] %d regimes: from Jan 01 server->NY = %dh", y, prev);
   for(int i = 1; i < 366; i++)
     {
      datetime t = d + (datetime)(i * 86400);
      if(YearOf(t) != y) break;
      int cur = DeltaHours(t);
      if(cur != prev)
        {
         PrintFormat("[CLOCK]   %s server->NY becomes %dh", TimeToString(t, TIME_DATE), cur);
         prev = cur;
        }
     }
  }

//====================== EA ENTRY POINTS =============================
int OnInit()
  {
   if(InpRangeLenMin <= 0 || InpRangeOffsetMin < 0 || InpRangeOffsetMin + InpRangeLenMin > 1440)
     { Print("[CLOCK] range window must fit inside one day."); return INIT_PARAMETERS_INCORRECT; }
   if(InpDayStartHourTgt < 0 || InpDayStartHourTgt > 23)
     { Print("[CLOCK] day start hour must be 0..23."); return INIT_PARAMETERS_INCORRECT; }
   if(InpRangeLenMin * 60 % PeriodSeconds(InpRangeTF) != 0)
      Print("[CLOCK] NOTE: window length is not a whole number of ", EnumToString(InpRangeTF), " bars.");

   datetime now = TimeCurrent();
   datetime utc = SrvToUtc(now);
   datetime tgt = UtcToTgt(now == 0 ? 0 : utc);
   PrintFormat("[CLOCK] %s %s  server=UTC%+d%s  reference=UTC%+d%s",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period),
               InpSrvStdOffsetHours, EnumToString(InpSrvDstRule),
               InpTgtStdOffsetHours, EnumToString(InpTgtDstRule));
   PrintFormat("[CLOCK] now: server %s -> UTC %s -> NY %s  (server->NY = %dh)",
               TS(now), TS(utc), TS(tgt), DeltaHours(now));

   long idx = TgtDayIndex(tgt);
   datetime aSrv, bSrv, aUtc, bUtc;
   WindowSrv(idx, aSrv, bSrv);
   WindowUtc(idx, aUtc, bUtc);
   PrintFormat("[CLOCK] today's range window: NY %s .. %s  ==  server %s .. %s  (%d %s bars expected)",
               TS(TgtDayStart(idx) + (datetime)(InpRangeOffsetMin * 60)),
               TS(TgtDayStart(idx) + (datetime)((InpRangeOffsetMin + InpRangeLenMin) * 60)),
               TS(aSrv), TS(bSrv),
               (int)((bUtc - aUtc) / PeriodSeconds(InpRangeTF)), EnumToString(InpRangeTF));

   int fails = 0;
   if(InpRunSelfTest)   fails = RunSelfTest();
   if(InpPrintDstRegimes) PrintDstRegimes(now);
   if(InpRunWeekProbe)  RunWeekProbe();

   RangeDay rd;
   if(BuildRange(idx - 1, rd))
      PrintFormat("[CLOCK] yesterday's NY range: H=%s L=%s from %d/%d %s bars (server %s..%s)",
                  DoubleToString(rd.hi, _Digits), DoubleToString(rd.lo, _Digits),
                  rd.bars, rd.expected, EnumToString(InpRangeTF), TS(rd.srvFrom), TS(rd.srvTo));
   else
      PrintFormat("[CLOCK] yesterday's NY range: NOT AVAILABLE (%d/%d bars in window %s..%s server)",
                  rd.bars, rd.expected, TS(rd.srvFrom), TS(rd.srvTo));

   if(fails > 0 && InpFailInitOnMismatch)
     {
      Print("[CLOCK] refusing to run: calendar self-test failed.");
      return INIT_FAILED;
     }
   return INIT_SUCCEEDED;
  }

void OnTick() { }
//+------------------------------------------------------------------+
