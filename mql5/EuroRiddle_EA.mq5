//+------------------------------------------------------------------+
//|                                                EuroRiddle_EA.mq5 |
//|  Tradable versions of the "EuroRiddle" forum rules: EURUSD, one  |
//|  trade a day, fade the day's extreme, 15 pip stop, 25 pip target.|
//|  The literal rule needs the day's final high/low in advance; the |
//|  versions (docs/superpowers/specs/2026-10-06-euroriddle-         |
//|  prereg.md) use only what is known at the time:                  |
//|   A PREV_DAY       fade a cross of the previous day's high / low |
//|   B RANGE_EXHAUST  fade new extremes once the range >= ADR(20)   |
//|   C LATE_EXTREME   fade the first new extreme from 12:00 NY      |
//|  Optional H1 RSI or MACD confirmation. Day = 17:00-17:00 New York|
//|  = server midnight; flat before the broker's session close.      |
//|  Execution, guards and trade log from EACore.mqh.                |
//|                                                                  |
//|  v1.00                                                           |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "EuroRiddle: one EURUSD trade a day fading the day's extreme, 15/25 pips; three tradable versions"

#define EA_NAME              "EuroRiddle_EA"
#define EA_VERSION           "1.00"
#define EA_MAGIC             261008
#define EA_COMMENT           "EURID"
#define EA_SPREAD_WAIT_MIN   0            // the signal is a price event: a delayed retry would be a different trade
#define EA_MAX_SPREAD_POINTS 20           // 2 pips on a 5-digit EURUSD quote
#include "EACore.mqh"
#define FLAT_MARGIN_MIN 10                // go flat this long before the broker's session close

enum ENUM_RIDDLE_MODE
  {
   MODE_PREV_DAY      = 0,  // A: fade a cross of the previous day's high / low
   MODE_RANGE_EXHAUST = 1,  // B: fade new extremes once the range reaches ADR
   MODE_LATE_EXTREME  = 2   // C: fade the first new extreme after the late-day cutoff
  };
enum ENUM_RIDDLE_FILTER
  {
   FILTER_NONE = 0,  // No confirmation
   FILTER_RSI  = 1,  // RSI overbought / oversold on InpTimeframe
   FILTER_MACD = 2   // MACD histogram fading on InpTimeframe
  };

input group "=== EuroRiddle ==="
input ENUM_RIDDLE_MODE    InpMode            = MODE_PREV_DAY; // Version
input ENUM_RIDDLE_FILTER  InpFilter          = FILTER_NONE;   // Confirmation filter (timeframe: InpTimeframe)
input int                 InpStopPips        = 15;            // Stop loss, pips
input int                 InpTargetPips      = 25;            // Take profit, pips
input int                 InpADRPeriod       = 20;            // B: days in the average daily range
input double              InpADRFrac         = 1.0;           // B: fade once today's range >= this x ADR
input int                 InpLateStartNY     = 720;           // C: from this New York minute (720 = 12:00)
input int                 InpRSIPeriod       = 14;            // RSI period
input double              InpRSILevel        = 70.0;          // RSI filter: short at/above, long at/below 100 - level
input int                 InpServerNYOffset  = 7;             // Server time minus New York time, hours (Darwinex: 7)

int      g_rsi = INVALID_HANDLE, g_macd = INVALID_HANDLE;
datetime g_day = 0, g_tradedDay = 0;
double   g_runHigh = 0.0, g_runLow = 0.0, g_prevBid = 0.0, g_adr = 0.0, g_pip = 0.0;
int      g_flatMin = 1440;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpStopPips <= 0 || InpTargetPips <= 0 || InpADRPeriod < 1 || InpADRFrac <= 0.0 || InpLateStartNY < 0 ||
      InpLateStartNY >= 1440 || InpRSIPeriod < 2 || InpRSILevel <= 50.0 || InpRSILevel >= 100.0)
     {
      Print("Invalid EuroRiddle inputs");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);
   g_pip = (g_symbol.Digits() == 3 || g_symbol.Digits() == 5) ? 10.0 * g_symbol.Point() : g_symbol.Point();
   if(InpFilter == FILTER_RSI)
      g_rsi = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
   if(InpFilter == FILTER_MACD)
      g_macd = iMACD(_Symbol, InpTimeframe, 12, 26, 9, PRICE_CLOSE);
   if((InpFilter == FILTER_RSI && g_rsi == INVALID_HANDLE) || (InpFilter == FILTER_MACD && g_macd == INVALID_HANDLE))
     {
      Print("Failed to create the filter indicator, error ", GetLastError());
      return(INIT_FAILED);
     }
   //--- restart-safe: already traded today?
   const datetime now = TimeCurrent(), today = now - now % 86400;
   if(HistorySelect(today, now + 60))
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         const ulong d = HistoryDealGetTicket(i);
         if(HistoryDealGetInteger(d, DEAL_MAGIC) == (long)InpMagic && HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
            g_tradedDay = today;
        }
   PrintFormat("%s v%s started on %s, magic %I64u, own positions: %d", EA_NAME, EA_VERSION, _Symbol, InpMagic, CountOwnPositions());
   PrintFormat("Inputs: version %s | filter %s on %s | stop %d pips, target %d pips | ADR(%d) x %.2f | late from %02d:%02d NY | RSI(%d) %.0f/%.0f | risk %s %.2f",
               EnumToString(InpMode), EnumToString(InpFilter), EnumToString(InpTimeframe), InpStopPips, InpTargetPips,
               InpADRPeriod, InpADRFrac, InpLateStartNY / 60, InpLateStartNY % 60, InpRSIPeriod, InpRSILevel, 100.0 - InpRSILevel,
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_rsi != INVALID_HANDLE)
      IndicatorRelease(g_rsi);
   if(g_macd != INVALID_HANDLE)
      IndicatorRelease(g_macd);
   Core_Deinit(reason);
  }

//--- server minute at which this weekday's trading stops: the broker's session close minus the margin
int FlatMinute(const int dayOfWeek)
  {
   long end = -1;
   datetime from, to;
   for(uint i = 0; SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dayOfWeek, i, from, to); i++)
      end = MathMax(end, (long)to);
   return(end < 0 ? 1440 - FLAT_MARGIN_MIN : (int)(end / 60) - FLAT_MARGIN_MIN);
  }

double AverageDailyRange()
  {
   MqlRates d[];
   if(CopyRates(_Symbol, PERIOD_D1, 1, InpADRPeriod, d) < InpADRPeriod)
      return(0.0);
   double sum = 0.0;
   for(int i = 0; i < InpADRPeriod; i++)
      sum += d[i].high - d[i].low;
   return(sum / InpADRPeriod);
  }

//--- +1 buy, -1 sell, 0 none; uses the extremes of the earlier ticks (g_runHigh/g_runLow) and the previous Bid
int Signal(const double bid, const int serverMin, string &why)
  {
   if(InpMode == MODE_PREV_DAY)
     {
      const double pdh = iHigh(_Symbol, PERIOD_D1, 1), pdl = iLow(_Symbol, PERIOD_D1, 1);
      if(pdh > 0.0 && g_prevBid < pdh && bid >= pdh) { why = StringFormat("A previous-day high %.5f", pdh); return(-1); }
      if(pdl > 0.0 && g_prevBid > pdl && bid <= pdl) { why = StringFormat("A previous-day low %.5f", pdl); return(1); }
      return(0);
     }
   if(InpMode == MODE_RANGE_EXHAUST)
     {
      const double range = MathMax(g_runHigh, bid) - MathMin(g_runLow, bid);
      if(g_adr <= 0.0 || range < InpADRFrac * g_adr)
         return(0);
      why = StringFormat("B range %.1f pips >= %.2f x ADR %.1f pips", range / g_pip, InpADRFrac, g_adr / g_pip);
     }
   else
     {
      const int lateServer = (InpLateStartNY + InpServerNYOffset * 60) % 1440;   // 12:00 NY = 19:00 server
      if(serverMin < lateServer)
         return(0);
      why = StringFormat("C new extreme after %02d:%02d NY", InpLateStartNY / 60, InpLateStartNY % 60);
     }
   if(bid > g_runHigh) { why += StringFormat(", new day high %.5f", bid); return(-1); }
   if(bid < g_runLow)  { why += StringFormat(", new day low %.5f", bid);  return(1); }
   return(0);
  }

bool FilterOk(const int side, string &detail)
  {
   if(InpFilter == FILTER_RSI)
     {
      double r[1];
      if(CopyBuffer(g_rsi, 0, 1, 1, r) < 1)
         return(false);
      detail = StringFormat(", RSI %.1f", r[0]);
      return(side < 0 ? r[0] >= InpRSILevel : r[0] <= 100.0 - InpRSILevel);
     }
   if(InpFilter == FILTER_MACD)
     {
      double m[2], s[2];                          // static arrays fill oldest-first: [1] = last closed bar, [0] = the one before
      if(CopyBuffer(g_macd, 0, 1, 2, m) < 2 || CopyBuffer(g_macd, 1, 1, 2, s) < 2)
         return(false);
      const double h1 = m[1] - s[1], h2 = m[0] - s[0];
      detail = StringFormat(", MACD histogram %.6f -> %.6f", h2, h1);
      return(side < 0 ? h1 < h2 : h1 > h2);
     }
   return(true);
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

void OnTick()
  {
   Core_OnTickStart();
   if(!g_symbol.RefreshRates())
      return;
   const datetime now = TimeCurrent(), day = now - now % 86400;
   const int      serverMin = (int)((now % 86400) / 60);
   const double   bid = g_symbol.Bid();
   if(day != g_day)                                   // new trading day (17:00 New York)
     {
      MqlDateTime dt;
      TimeToStruct(now, dt);
      g_day     = day;
      g_flatMin = FlatMinute(dt.day_of_week);
      g_adr     = AverageDailyRange();
      g_runHigh = MathMax(iHigh(_Symbol, PERIOD_D1, 0), bid);
      g_runLow  = MathMin(iLow(_Symbol, PERIOD_D1, 0), bid);
      g_prevBid = bid;
      return;
     }
   if(serverMin >= g_flatMin)
     {
      if(CountOwnPositions() > 0)
         FlattenOwn("day_end");
     }
   else if(g_tradedDay != day && CountOwnPositions() == 0)
     {
      string why = "", detail = "";
      const int side = Signal(bid, serverMin, why);
      if(side != 0 && FilterOk(side, detail) && (InpMaxSpreadPoints <= 0 || g_symbol.Spread() <= InpMaxSpreadPoints))
        {
         const bool   buy = (side > 0);
         const double px  = buy ? g_symbol.Ask() : bid;
         const double tgt = InpTargetPips * g_pip;
         PrintFormat("EURID %s: %s%s, entry %.5f", buy ? "BUY" : "SELL", why, detail, px);
         if(Core_Open(buy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, InpStopPips * g_pip, buy ? px + tgt : px - tgt))
            g_tradedDay = day;
        }
     }
   g_runHigh = MathMax(g_runHigh, bid);
   g_runLow  = MathMin(g_runLow, bid);
   g_prevBid = bid;
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
