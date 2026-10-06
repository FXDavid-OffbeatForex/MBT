//+------------------------------------------------------------------+
//|                                                  VWAP_RSI_EA.mq5 |
//|  Port of the TradingView indicator "VWAP + RSI Candle Color":    |
//|  bullish = close > anchored VWAP and RSI > 50, bearish = both    |
//|  below, neutral otherwise. Trades the first bar of agreement     |
//|  (state change), stop beyond that bar, target = RR x stop        |
//|  distance. One position at a time; the magic number isolates it  |
//|  from other EAs on a hedging account.                            |
//|  Execution, guards and trade log from EACore.mqh (v1.42 core).   |
//|                                                                  |
//|  v1.00                                                           |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "VWAP + RSI(50) agreement entry, stop beyond the signal bar, fixed R target; MACD_Cross_EA v1.42 safety core"

#define EA_NAME    "VWAP_RSI_EA"
#define EA_VERSION "1.00"
#define EA_MAGIC   261006
#define EA_COMMENT "VWAP_RSI"
#include "EACore.mqh"
#define ATR_PERIOD 14

enum ENUM_ANCHOR
  {
   ANCHOR_DAY   = 0,  // Day (server midnight)
   ANCHOR_WEEK  = 1,  // Week (Monday 00:00 server)
   ANCHOR_MONTH = 2   // Month
  };

enum ENUM_REENTRY
  {
   REENTRY_FRESH_ONLY = 0,  // Enter only when the state just changed
   REENTRY_EVERY_BAR  = 1   // Enter on any agreeing bar without a position
  };

input group "=== Strategy ==="
input ENUM_ANCHOR         InpAnchor          = ANCHOR_DAY;          // VWAP anchor
input int                 InpRSIPeriod       = 21;                  // RSI period (close)
input double              InpRR              = 1.0;                 // Take profit = RR x stop distance
input double              InpSLBufferATR     = 0.0;                 // Stop beyond the signal bar by k x ATR(14)
input ENUM_REENTRY        InpReentryMode     = REENTRY_FRESH_ONLY;  // Re-entry rule

int g_rsi = INVALID_HANDLE;
int g_atr = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpRSIPeriod <= 1 || InpRR <= 0.0 || InpSLBufferATR < 0.0)
     {
      Print("Invalid RSI period / RR / SL buffer");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);
   g_rsi = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
   g_atr = iATR(_Symbol, InpTimeframe, ATR_PERIOD);
   if(g_rsi == INVALID_HANDLE || g_atr == INVALID_HANDLE)
     {
      Print("Failed to create indicators, error ", GetLastError());
      return(INIT_FAILED);
     }
   PrintFormat("%s v%s started on %s %s, magic %I64u, own positions: %d",
               EA_NAME, EA_VERSION, _Symbol, EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
   PrintFormat("Inputs: anchor %s | RSI%d vs 50 | RR %.2f | SL buffer %.2fxATR(%d) | re-entry %s | risk %s %.2f",
               EnumToString(InpAnchor), InpRSIPeriod, InpRR, InpSLBufferATR, ATR_PERIOD,
               InpReentryMode == REENTRY_FRESH_ONLY ? "fresh only" : "every bar",
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_rsi != INVALID_HANDLE)
      IndicatorRelease(g_rsi);
   if(g_atr != INVALID_HANDLE)
      IndicatorRelease(g_atr);
   Core_Deinit(reason);
  }

//--- start of the VWAP period containing t, in server time (the day boundary EACore's daily stop uses)
datetime PeriodStart(const datetime t)
  {
   const datetime day = t - t % 86400;
   if(InpAnchor == ANCHOR_DAY)
      return(day);
   MqlDateTime dt;
   TimeToStruct(t, dt);
   if(InpAnchor == ANCHOR_WEEK)
      return(day - ((dt.day_of_week + 6) % 7) * 86400);   // Monday 00:00
   dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
   return(StructToTime(dt));
  }

//--- VWAP of bar i (series index, 0 = last closed bar), anchored at that bar's own period. 0.0 = not computable.
//--- The first bar of a period has VWAP == its own hlc3, exactly like the Pine script.
double VwapAt(const MqlRates &r[], const int i)
  {
   const datetime start = PeriodStart(r[i].time);
   const int n = ArraySize(r);
   double pv = 0.0, v = 0.0;
   int k = i;
   for(; k < n && r[k].time >= start; k++)
     {
      const double tv = (double)r[k].tick_volume;
      pv += (r[k].high + r[k].low + r[k].close) / 3.0 * tv;
      v  += tv;
     }
   if(k == n || v <= 0.0)
      return(0.0);                              // copied window ended before the anchor, or no volume
   return(pv / v);
  }

int State(const double close, const double vwap, const double rsi)
  {
   if(vwap <= 0.0)
      return(0);
   if(close > vwap && rsi > 50.0)
      return(1);
   if(close < vwap && rsi < 50.0)
      return(-1);
   return(0);
  }

void OnTick()
  {
   Core_OnTickStart();
   const datetime bar = Core_CurrentBar();
   Core_RetryPending(bar);
   if(!Core_IsNewBar(bar))
      return;
   const int anchorSec = (InpAnchor == ANCHOR_DAY) ? 86400 : (InpAnchor == ANCHOR_WEEK) ? 7 * 86400 : 31 * 86400;
   const int need = anchorSec / PeriodSeconds(InpTimeframe) + 2;   // time-based upper bound on bars per period
   MqlRates r[];
   ArraySetAsSeries(r, true);
   double rsi[2], atr[1];                        // CopyBuffer fills static arrays oldest-first: rsi[1] = shift 1, rsi[0] = shift 2
   if(CopyRates(_Symbol, InpTimeframe, 1, need, r) < 2 ||
      CopyBuffer(g_rsi, 0, 1, 2, rsi) < 2 || CopyBuffer(g_atr, 0, 1, 1, atr) < 1 ||
      rsi[0] == EMPTY_VALUE || rsi[1] == EMPTY_VALUE || atr[0] <= 0.0)
      return;                                   // data not ready: bar stays unprocessed, retried next tick
   const double vwap1 = VwapAt(r, 0), vwap2 = VwapAt(r, 1);
   const int s1 = State(r[0].close, vwap1, rsi[1]);
   const int s2 = State(r[1].close, vwap2, rsi[0]);
   Core_MarkBar(bar);
   const string ctx = StringFormat("close %.2f vwap %.2f rsi %.1f state %d prev %d", r[0].close, vwap1, rsi[1], s1, s2);
   if(CountOwnPositions() > 0)
     {
      Core_LogBar(bar, ctx + " | in position");
      return;
     }
   if(s1 == 0 || (InpReentryMode == REENTRY_FRESH_ONLY && s1 == s2))
     {
      Core_LogBar(bar, ctx + " | no signal");
      return;
     }
   if(!g_symbol.RefreshRates())
      return;
   const bool   buy  = (s1 > 0);
   const double buf  = InpSLBufferATR * atr[0];
   const double sl   = buy ? r[0].low - buf : r[0].high + buf + g_symbol.Spread() * g_symbol.Point();   // sell stop fills at ask
   const double px   = buy ? g_symbol.Ask() : g_symbol.Bid();
   const double dist = buy ? px - sl : sl - px;
   if(dist <= 0.0)
     {
      PrintFormat("Signal skipped, %s price %.2f is already beyond the signal bar stop %.2f", buy ? "BUY" : "SELL", px, sl);
      return;
     }
   Core_LogBar(bar, ctx + StringFormat(" | %s sl %.2f dist %.2f", buy ? "BUY" : "SELL", sl, dist));
   Core_Open(buy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, dist, buy ? px + InpRR * dist : px - InpRR * dist);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
