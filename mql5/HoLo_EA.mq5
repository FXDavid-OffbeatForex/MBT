//+------------------------------------------------------------------+
//|                                                      HoLo_EA.mq5 |
//|  MichaelG's "HoLo" (Highest Open / Lowest Open, ForexFactory)    |
//|  as described in HoLo.pdf; levels as in smHiLoOpen Lines v1.2.   |
//|  SELL: a trigger candle OPENS between the day's highest H1 open  |
//|  (HO) and the day's high (the Area of Interest); sell when Bid   |
//|  falls back to HO, stop at the day's high. A new day high before |
//|  entry cancels the setup. BUY mirrors it at the lowest open (LO) |
//|  with Ask. Stop to break-even + 1 pip after 5 pips (scaled to    |
//|  % of price: 5 AUDUSD pips = 0.067%). Day = 17:00 NY to 17:00 NY |
//|  = Darwinex server midnight. Entries only in the New York        |
//|  session, flat before the 17:00 NY close.                        |
//|  Execution, guards and trade log from EACore.mqh (v1.42 core).   |
//|                                                                  |
//|  v1.00                                                           |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "HoLo: fade the day's highest/lowest H1 open after a trigger candle opens in the Area of Interest; NY session only"

#define EA_NAME            "HoLo_EA"
#define EA_VERSION         "1.00"
#define EA_MAGIC           261007
#define EA_COMMENT         "HOLO"
#define EA_TIMEFRAME       PERIOD_M15   // trigger timeframe (InpTimeframe)
#define EA_SPREAD_WAIT_MIN 0            // the entry is a price level: a delayed retry would be a late entry
#include "EACore.mqh"
#define D1_WINDOW 5                     // D1 level mode: highest/lowest open of the last 5 daily bars
#define FLAT_MARGIN_MIN 10              // go flat this long before the broker's session close (ticks thin out before it)

input group "=== HoLo ==="
input ENUM_TIMEFRAMES     InpLevelTF         = PERIOD_H1;   // Level timeframe: H1/H4 = today's opens, D1 = last 5 daily opens
input double              InpRR              = 1.0;         // Take profit = RR x stop distance (0 = none)
input double              InpBETriggerPct    = 0.067;       // Stop to break-even after this profit, % of price (0 = off)
input double              InpBELockPct       = 0.013;       // ... locking in this profit, % of price
input double              InpMaxLatePct      = 0.01;        // Max entry beyond the level, % of price ("no late entry")
input double              InpMinStopPct      = 0.03;        // Skip setups with a stop closer than this, % of price
input bool                InpBreakoutFilter  = true;        // No sells once the day broke the previous high (buys: low)

input group "=== New York session ==="
input int                 InpSessionStartNY  = 480;         // Entries from, minutes after NY midnight (480 = 08:00)
input int                 InpSessionEndNY    = 1015;        // Entries until and flat at (1015 = 16:55)
input int                 InpServerNYOffset  = 7;           // Server time minus New York time, hours (Darwinex: 7 all year)

struct Levels { double ho, lo, high, low, pdHigh, pdLow; };

bool   g_sellArmed = false, g_buyArmed = false;
double g_sellHO = 0.0, g_sellHigh = 0.0, g_buyLO = 0.0, g_buyLow = 0.0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if((InpLevelTF != PERIOD_H1 && InpLevelTF != PERIOD_H4 && InpLevelTF != PERIOD_D1) ||
      PeriodSeconds(InpTimeframe) >= PeriodSeconds(InpLevelTF))
     {
      Print("Level timeframe must be H1, H4 or D1, and the trigger timeframe (InpTimeframe) lower");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRR < 0.0 || InpBETriggerPct < 0.0 || InpBELockPct < 0.0 ||
      (InpBETriggerPct > 0.0 && InpBELockPct >= InpBETriggerPct) || InpMaxLatePct < 0.0 || InpMinStopPct < 0.0 ||
      InpSessionStartNY < 0 || InpSessionEndNY > 1440 || InpSessionStartNY >= InpSessionEndNY ||
      InpServerNYOffset < -12 || InpServerNYOffset > 14)
     {
      Print("Invalid HoLo inputs (RR/BE/late/min stop >= 0, BE lock below trigger, session start < end within 0-1440)");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);
   PrintFormat("%s v%s started on %s, levels %s, trigger %s, magic %I64u, own positions: %d",
               EA_NAME, EA_VERSION, _Symbol, EnumToString(InpLevelTF), EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
   PrintFormat("Inputs: RR %.2f | BE +%.3f%% lock %.3f%% | late %.3f%% | min stop %.3f%% | breakout filter %s | NY %02d:%02d-%02d:%02d (server = NY%+d h) | risk %s %.2f",
               InpRR, InpBETriggerPct, InpBELockPct, InpMaxLatePct, InpMinStopPct, InpBreakoutFilter ? "on" : "off",
               InpSessionStartNY / 60, InpSessionStartNY % 60, InpSessionEndNY / 60, InpSessionEndNY % 60, InpServerNYOffset,
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   string flat = "";
   for(int d = 1; d <= 5; d++)
      flat += StringFormat(" %s %02d:%02d", StringSubstr(EnumToString((ENUM_DAY_OF_WEEK)d), 0, 3), SessionEndNY(d) / 60, SessionEndNY(d) % 60);
   Print("Entries stop and positions go flat at NY time:", flat);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { Core_Deinit(reason); }

void Diag(const string msg)
  {
   if(InpLogEveryBar)
      Print(msg);
  }

//--- minutes after New York midnight
int NYMinute(const datetime t)
  {
   const datetime ny = t - (datetime)(InpServerNYOffset * 3600);
   return((int)((ny % 86400) / 60));
  }

//--- NY minute at which entries stop and positions go flat on this server weekday: the input, or earlier when the
//--- broker's trade session ends sooner (Darwinex gold: 23:55 server = 16:55 NY on Fridays, no ticks after it)
int SessionEndNY(const int dayOfWeek)
  {
   long end = -1;
   datetime from, to;
   for(uint i = 0; SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dayOfWeek, i, from, to); i++)
      end = MathMax(end, (long)to);                                   // seconds after server midnight
   if(end < 0)
      return(InpSessionEndNY);
   return((int)MathMin(InpSessionEndNY, (end - InpServerNYOffset * 3600) / 60 - FLAT_MARGIN_MIN));
  }

bool InSession(const datetime t)
  {
   static datetime cachedDay = 0;
   static int      endNY = 0;
   const datetime day = t - t % 86400;
   if(day != cachedDay)
     {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      cachedDay = day;
      endNY = SessionEndNY(dt.day_of_week);
     }
   const int m = NYMinute(t);
   return(m >= InpSessionStartNY && m < endNY);
  }

//--- day (or 5-day window for D1 levels): highest/lowest level-timeframe open, high/low, previous window's high/low
bool ComputeLevels(Levels &lv)
  {
   const int n = (InpLevelTF == PERIOD_D1) ? D1_WINDOW : 1;
   MqlRates d[];
   ArraySetAsSeries(d, true);
   if(CopyRates(_Symbol, PERIOD_D1, 0, 2 * n, d) < 2 * n)
      return(false);
   lv.high = d[0].high;  lv.low = d[0].low;
   lv.pdHigh = d[n].high; lv.pdLow = d[n].low;
   for(int i = 1; i < n; i++)
     {
      lv.high = MathMax(lv.high, d[i].high);           lv.low = MathMin(lv.low, d[i].low);
      lv.pdHigh = MathMax(lv.pdHigh, d[n + i].high);   lv.pdLow = MathMin(lv.pdLow, d[n + i].low);
     }
   MqlRates b[];
   if(CopyRates(_Symbol, InpLevelTF, d[n - 1].time, TimeCurrent(), b) < 1)   // includes the forming bar's open
      return(false);
   lv.ho = b[0].open;
   lv.lo = b[0].open;
   for(int i = 1; i < ArraySize(b); i++)
     {
      lv.ho = MathMax(lv.ho, b[i].open);
      lv.lo = MathMin(lv.lo, b[i].open);
     }
   return(true);
  }

//--- a new trigger candle opened: re-validate armed setups, arm when the open is inside an Area of Interest
bool OnNewTriggerBar(const datetime bar, const bool session)
  {
   Levels lv;
   if(!ComputeLevels(lv))
      return(false);
   Core_MarkBar(bar);
   if(g_sellArmed && (lv.ho != g_sellHO || lv.high > g_sellHigh))
     {
      g_sellArmed = false;
      Diag(StringFormat("SELL setup cancelled: HO %.2f -> %.2f, high %.2f -> %.2f", g_sellHO, lv.ho, g_sellHigh, lv.high));
     }
   if(g_buyArmed && (lv.lo != g_buyLO || lv.low < g_buyLow))
     {
      g_buyArmed = false;
      Diag(StringFormat("BUY setup cancelled: LO %.2f -> %.2f, low %.2f -> %.2f", g_buyLO, lv.lo, g_buyLow, lv.low));
     }
   const double o = iOpen(_Symbol, InpTimeframe, 0);
   const string ctx = StringFormat("open %.2f | HO %.2f LO %.2f | high %.2f low %.2f | prev high %.2f low %.2f",
                                   o, lv.ho, lv.lo, lv.high, lv.low, lv.pdHigh, lv.pdLow);
   if(!session || CountOwnPositions() > 0)
     {
      Core_LogBar(bar, ctx + (session ? " | in position" : " | outside NY session"));
      return(true);
     }
   const double minStop = InpMinStopPct / 100.0 * o;
   string what = "";
   if(!g_sellArmed && o > lv.ho && o < lv.high)
     {
      if(InpBreakoutFilter && lv.high > lv.pdHigh)
         what += " | sell skipped: day above previous high";
      else if(lv.high - lv.ho < minStop)
         what += " | sell skipped: stop too close";
      else
        {
         g_sellArmed = true; g_sellHO = lv.ho; g_sellHigh = lv.high;
         what += " | SELL armed";
        }
     }
   if(!g_buyArmed && o < lv.lo && o > lv.low)
     {
      if(InpBreakoutFilter && lv.low < lv.pdLow)
         what += " | buy skipped: day below previous low";
      else if(lv.lo - lv.low < minStop)
         what += " | buy skipped: stop too close";
      else
        {
         g_buyArmed = true; g_buyLO = lv.lo; g_buyLow = lv.low;
         what += " | BUY armed";
        }
     }
   Core_LogBar(bar, ctx + (what == "" ? " | no setup" : what));
   return(true);
  }

void ManageBreakEven()
  {
   if(InpBETriggerPct <= 0.0 || !g_symbol.RefreshRates())
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;
      const double open = PositionGetDouble(POSITION_PRICE_OPEN);
      const double sl   = PositionGetDouble(POSITION_SL);
      const double trig = InpBETriggerPct / 100.0 * open, lock = InpBELockPct / 100.0 * open;
      const double half = g_symbol.Point() / 2.0;
      double newSL = 0.0;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
        {
         if(g_symbol.Bid() - open >= trig && sl < open + lock - half)      // the Bid closes a buy
            newSL = open + lock;
        }
      else if(open - g_symbol.Ask() >= trig && (sl == 0.0 || sl > open - lock + half))   // the Ask closes a sell
         newSL = open - lock;
      if(newSL > 0.0)
         g_trade.PositionModify(ticket, NormalizeDouble(newSL, g_symbol.Digits()), PositionGetDouble(POSITION_TP));
     }
  }

void FlattenOwn(const string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && IsOwnPosition())
         Core_ClosePosition(ticket, reason);
     }
  }

//--- enter exactly at the level: Bid back to HO for a sell, Ask back to LO for a buy, never more than InpMaxLatePct beyond it
void TryEntry()
  {
   if((!g_sellArmed && !g_buyArmed) || CountOwnPositions() > 0)
      return;
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
      return;                                   // wait until the spread allows the entry
   const double bid = g_symbol.Bid(), ask = g_symbol.Ask();
   if(g_sellArmed && bid <= g_sellHO && g_sellHO - bid <= InpMaxLatePct / 100.0 * g_sellHO)
     {
      const double sl = g_sellHigh + (ask - bid);   // the high is a Bid high, a sell stop triggers on the Ask
      const double dist = sl - bid;
      PrintFormat("HOLO SELL: bid %.2f at HO %.2f, stop %.2f (high %.2f + spread)", bid, g_sellHO, sl, g_sellHigh);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_SELL, dist, InpRR > 0.0 ? bid - InpRR * dist : 0.0);
      return;
     }
   if(g_buyArmed && ask >= g_buyLO && ask - g_buyLO <= InpMaxLatePct / 100.0 * g_buyLO)
     {
      const double dist = ask - g_buyLow;             // the low is a Bid low and a buy stop triggers on the Bid
      PrintFormat("HOLO BUY: ask %.2f at LO %.2f, stop %.2f", ask, g_buyLO, g_buyLow);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_BUY, dist, InpRR > 0.0 ? ask + InpRR * dist : 0.0);
     }
  }

void OnTick()
  {
   Core_OnTickStart();
   const bool session = InSession(TimeCurrent());
   if(session)
      ManageBreakEven();
   else
     {
      g_sellArmed = g_buyArmed = false;
      if(CountOwnPositions() > 0)
         FlattenOwn("session_end");
     }
   const datetime bar = Core_CurrentBar();
   if(Core_IsNewBar(bar) && !OnNewTriggerBar(bar, session))
      return;                                   // data not ready: bar stays unprocessed, retried next tick
   if(!session || !g_symbol.RefreshRates())
      return;
   const double bid = g_symbol.Bid();
   if(g_sellArmed && bid > g_sellHigh)
     {
      g_sellArmed = false;
      Diag(StringFormat("SELL setup cancelled: new high %.2f above %.2f (intraday breakout)", bid, g_sellHigh));
     }
   if(g_buyArmed && bid < g_buyLow)
     {
      g_buyArmed = false;
      Diag(StringFormat("BUY setup cancelled: new low %.2f below %.2f (intraday breakout)", bid, g_buyLow));
     }
   TryEntry();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
