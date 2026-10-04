//+------------------------------------------------------------------+
//|                                                MACD_Cross_EA.mq5 |
//|  MACD line / signal line crossover Expert Advisor                |
//|  Designed for XAUUSD, works on any symbol.                       |
//|                                                                  |
//|  v1.42                                                           |
//|  Changelog 1.42 (safety only, signal/exit logic unchanged):      |
//|  - spread guard 150 pts, blocked signal retried within its bar   |
//|  - daily loss stop: close own positions, no entries until next   |
//|    server day; monthly loss stop: no new entries until next month|
//|  - closed-trade CSV log (same columns as RiskGuard.mqh)          |
//|                                                                  |
//|  v1.41                                                           |
//|  Changelog 1.41: hourly status line in the Experts log and a     |
//|  trade-permission check at start/hourly. Trading logic unchanged.|
//|                                                                  |
//|  v1.40                                                           |
//|  Changelog 1.40 (headless sweep 2024-26, real-tick checked):     |
//|  fast EMA 16, SL 0.5%, TP 3.75%, BE at +0.5%, trailing from +1%  |
//|  at 4xATR. 2024-26 real ticks: +201k vs +123k (v1.30), PF 1.55.  |
//|                                                                  |
//|  - Signals evaluated once per closed bar of the chosen TF        |
//|  - Optional zero-line filter (buy below 0, sell above 0)         |
//|  - Optional EMA trend filter on a configurable timeframe         |
//|  - Optional signal-strength filter (MACD gap >= N x ATR)         |
//|  - Optional trading-hours filter (server time)                   |
//|  - SL / TP as a percentage of the open price                     |
//|  - Optional break-even and percentage trailing stop              |
//|  - Lot size from monetary or percentage risk                     |
//|  - Own magic number; never touches foreign positions             |
//|  - Restart-safe: last processed bar persisted in a terminal      |
//|    global variable, positions re-discovered by magic             |
//|  - OnTester custom criterion for optimization ("Custom max")     |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.42"
#property strict
#property description "MACD crossover EA with trend/strength filters, % SL/TP, trailing and risk-based lots"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- enumerations
enum ENUM_RISK_MODE
  {
   RISK_MONEY   = 0,  // Fixed money per trade (account currency)
   RISK_PERCENT = 1   // Percent of account balance per trade
  };

//--- inputs
input group "=== General ==="
input ulong               InpMagic           = 240817;      // Magic number
input ENUM_TIMEFRAMES     InpTimeframe       = PERIOD_H1;   // Signal timeframe
input string              InpComment         = "MACD_X";    // Order comment
input int                 InpSlippagePoints  = 30;          // Max deviation (points)
input int                 InpMaxSpreadPoints = 150;         // Max spread to trade (points, 0 = off)

input group "=== MACD ==="
input int                 InpFastEMA         = 16;          // Fast EMA period
input int                 InpSlowEMA         = 26;          // Slow EMA period
input int                 InpSignalSMA       = 9;           // Signal SMA period
input ENUM_APPLIED_PRICE  InpAppliedPrice    = PRICE_CLOSE; // Applied price
input bool                InpZeroLineFilter  = false;       // Buy only below 0 / Sell only above 0

input group "=== Filters ==="
input bool                InpUseTrendFilter  = true;        // Use EMA trend filter
input ENUM_TIMEFRAMES     InpTrendTF         = PERIOD_H4;   // Trend EMA timeframe
input int                 InpTrendPeriod     = 200;         // Trend EMA period
input double              InpMinGapATR       = 0.0;         // Min MACD-signal gap at cross (x ATR, 0 = off)
input int                 InpATRPeriod       = 14;          // ATR period for gap filter
input bool                InpUseTimeFilter   = false;       // Use trading hours filter
input int                 InpStartHour       = 7;           // Start hour (server time, inclusive)
input int                 InpEndHour         = 20;          // End hour (server time, exclusive)

input group "=== Trade management ==="
input double              InpStopLossPct     = 0.5;         // Stop loss  (% of open price)
input double              InpTakeProfitPct   = 3.75;        // Take profit (% of open price)
input bool                InpCloseOnOpposite = false;       // Close open position on opposite signal
input double              InpBreakEvenPct    = 0.5;         // Move SL to BE after profit of % (0 = off)
input double              InpBreakEvenLockPct= 0.1;         // BE lock-in (% of open price)
input double              InpTrailStartPct   = 1.0;         // Start trailing after profit of % (0 = off)
input double              InpTrailDistPct    = 1.0;         // Trailing distance (% of price) if ATR mult = 0
input double              InpTrailATRMult    = 4.0;         // Trailing distance in ATR multiples (0 = use %)

input group "=== Risk ==="
input ENUM_RISK_MODE      InpRiskMode        = RISK_PERCENT;// Risk mode
input double              InpRiskValue       = 1.0;         // Risk value (money or %)

input group "=== Safety guards ==="
input int                 InpSpreadWaitMin   = 30;          // Retry a spread-blocked signal for N minutes (0 = drop it)
input double              InpDailyLossPct    = 4.0;         // Daily loss stop, % of day-start balance (0 = off)
input double              InpMonthlyLossPct  = 12.0;        // Monthly loss stop, % of month-start balance (0 = off)
input bool                InpTradeLog        = true;        // Write closed trades to a CSV in MQL5\Files

input group "=== Diagnostics ==="
input bool                InpLogEveryBar     = true;        // Log a status line on every new signal bar

//--- globals
CTrade        g_trade;
CSymbolInfo   g_symbol;
int           g_macdHandle   = INVALID_HANDLE;
int           g_trendHandle  = INVALID_HANDLE;
int           g_atrHandle    = INVALID_HANDLE;
datetime      g_lastBarTime  = 0;      // last signal bar evaluated
string        g_gvLastBar    = "";     // terminal global variable (restart persistence)
bool          g_manageStops  = false;  // true if BE or trailing enabled
//--- v1.42 guards
int           g_pendingType  = -1;     // spread-blocked signal: POSITION_TYPE_BUY/SELL, -1 = none
datetime      g_pendingBar   = 0;
datetime      g_dayStart     = 0;      // server day / month whose start balance is cached
double        g_dayBalance   = 0.0;
datetime      g_monthStart   = 0;
double        g_monthBalance = 0.0;
bool          g_dayStopHit   = false;  // daily stop reported for g_dayStart
//--- trade log
string        g_logFile      = "";
int           g_logFlags     = 0;
struct EntryInfo { ulong position; int spread; double slippage; double risk; };
EntryInfo     g_entries[];

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
//--- validate inputs
   if(InpFastEMA <= 0 || InpSlowEMA <= 0 || InpSignalSMA <= 0 || InpFastEMA >= InpSlowEMA)
     {
      Print("Invalid MACD periods: fast must be < slow and all > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpStopLossPct <= 0.0 || InpTakeProfitPct <= 0.0)
     {
      Print("Stop loss and take profit percentages must be > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRiskValue <= 0.0 || (InpRiskMode == RISK_PERCENT && InpRiskValue > 100.0))
     {
      Print("Invalid risk value");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpUseTrendFilter && InpTrendPeriod <= 0)
     {
      Print("Trend EMA period must be > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpMinGapATR > 0.0 && InpATRPeriod <= 0)
     {
      Print("ATR period must be > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpUseTimeFilter && (InpStartHour < 0 || InpStartHour > 23 || InpEndHour < 0 || InpEndHour > 24))
     {
      Print("Invalid trading hours");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if((InpTrailStartPct > 0.0 && InpTrailDistPct <= 0.0 && InpTrailATRMult <= 0.0) ||
      InpBreakEvenPct < 0.0 || InpBreakEvenLockPct < 0.0 || InpTrailATRMult < 0.0 ||
      (InpTrailATRMult > 0.0 && InpATRPeriod <= 0))
     {
      Print("Invalid trailing / break-even settings");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpSpreadWaitMin < 0 || InpDailyLossPct < 0.0 || InpDailyLossPct >= 100.0 ||
      InpMonthlyLossPct < 0.0 || InpMonthlyLossPct >= 100.0)
     {
      Print("Invalid safety guard inputs");
      return(INIT_PARAMETERS_INCORRECT);
     }

//--- symbol
   if(!g_symbol.Name(_Symbol))
     {
      Print("Failed to initialise symbol ", _Symbol);
      return(INIT_FAILED);
     }
   g_symbol.Refresh();

//--- indicators
   g_macdHandle = iMACD(_Symbol, InpTimeframe, InpFastEMA, InpSlowEMA, InpSignalSMA, InpAppliedPrice);
   if(g_macdHandle == INVALID_HANDLE)
     {
      Print("Failed to create MACD handle, error ", GetLastError());
      return(INIT_FAILED);
     }
   if(InpUseTrendFilter)
     {
      g_trendHandle = iMA(_Symbol, InpTrendTF, InpTrendPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_trendHandle == INVALID_HANDLE)
        {
         Print("Failed to create trend EMA handle, error ", GetLastError());
         return(INIT_FAILED);
        }
     }
   if(InpMinGapATR > 0.0 || (InpTrailStartPct > 0.0 && InpTrailATRMult > 0.0))
     {
      g_atrHandle = iATR(_Symbol, InpTimeframe, InpATRPeriod);
      if(g_atrHandle == INVALID_HANDLE)
        {
         Print("Failed to create ATR handle, error ", GetLastError());
         return(INIT_FAILED);
        }
     }

//--- trade object
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);

   g_manageStops = (InpBreakEvenPct > 0.0 || InpTrailStartPct > 0.0);

//--- restart persistence: key unique per symbol / timeframe / magic
   g_gvLastBar = StringFormat("MACDX_%s_%s_%I64u", _Symbol, EnumToString(InpTimeframe), InpMagic);
   if(GlobalVariableCheck(g_gvLastBar))
      g_lastBarTime = (datetime)GlobalVariableGet(g_gvLastBar);

   PrintFormat("MACD_Cross_EA v1.42 started on %s %s, magic %I64u, own positions: %d",
               _Symbol, EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
//--- echo effective inputs so every tester run / restart is verifiable in the Journal
   PrintFormat("Inputs: MACD %d/%d/%d zeroFilter=%s | trend=%s %s EMA%d gapATR=%.2f hours=%s(%d-%d)",
               InpFastEMA, InpSlowEMA, InpSignalSMA, InpZeroLineFilter ? "on" : "off",
               InpUseTrendFilter ? "on" : "off", EnumToString(InpTrendTF), InpTrendPeriod,
               InpMinGapATR, InpUseTimeFilter ? "on" : "off", InpStartHour, InpEndHour);
   PrintFormat("Inputs: SL=%.2f%% TP=%.2f%% closeOpp=%s BE=%.2f%%(+%.2f%%) trail start=%.2f%% dist=%s | risk %s %.2f",
               InpStopLossPct, InpTakeProfitPct, InpCloseOnOpposite ? "on" : "off",
               InpBreakEvenPct, InpBreakEvenLockPct, InpTrailStartPct,
               InpTrailATRMult > 0.0 ? StringFormat("%.1fxATR(%d)", InpTrailATRMult, InpATRPeriod)
                                     : StringFormat("%.2f%%", InpTrailDistPct),
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   PrintFormat("Guards: max spread %s, retry %d min | daily stop %s | monthly stop %s | trade log %s",
               InpMaxSpreadPoints > 0 ? StringFormat("%d pts", InpMaxSpreadPoints) : "off", InpSpreadWaitMin,
               InpDailyLossPct > 0.0 ? StringFormat("-%.1f%%", InpDailyLossPct) : "off",
               InpMonthlyLossPct > 0.0 ? StringFormat("-%.1f%%", InpMonthlyLossPct) : "off",
               InpTradeLog ? "on" : "off");
   UpdatePeriodBalances();
   LogInit();
   Print("Ready check: ", TradePermissionText(), " | ", GuardStatus());
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ReleaseHandle(g_macdHandle);
   ReleaseHandle(g_trendHandle);
   ReleaseHandle(g_atrHandle);
//--- drop persistence only when removed on purpose; keep it on restart / recompile
   if(reason == REASON_REMOVE || reason == REASON_PARAMETERS || reason == REASON_CHARTCHANGE)
      GlobalVariableDel(g_gvLastBar);
  }

//+------------------------------------------------------------------+
void ReleaseHandle(int &handle)
  {
   if(handle != INVALID_HANDLE)
     {
      IndicatorRelease(handle);
      handle = INVALID_HANDLE;
     }
  }

//+------------------------------------------------------------------+
//| Diagnostics: can this EA place orders right now?                 |
//+------------------------------------------------------------------+
string TradePermissionText()
  {
   return(StringFormat("terminal=%s program=%s account=%s expert=%s connected=%s",
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "on" : "OFF",
          MQLInfoInteger(MQL_TRADE_ALLOWED)          ? "on" : "OFF",
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)  ? "on" : "OFF",
          AccountInfoInteger(ACCOUNT_TRADE_EXPERT)   ? "on" : "OFF",
          TerminalInfoInteger(TERMINAL_CONNECTED)    ? "yes" : "NO"));
  }

void LogBar(const datetime bar, const double m, const double s, const string verdict)
  {
   if(!InpLogEveryBar)
      return;
   PrintFormat("Bar %s | MACD-signal %+.3f | %s | own positions %d | trading %s | %s",
               TimeToString(bar, TIME_DATE | TIME_MINUTES), m - s, verdict, CountOwnPositions(),
               TradePermissionText(), GuardStatus());
  }

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
  {
//--- loss stops first: a breached daily stop closes our positions
   GuardsOnTick();

//--- stop management runs every tick (cheap: loops own positions only)
   if(g_manageStops)
      ManageOpenPositions();

//--- signal logic once per new bar of the signal timeframe
   datetime barTime = iTime(_Symbol, InpTimeframe, 0);
   if(g_pendingType >= 0)
      RetryPendingSignal(barTime);
   if(barTime == 0 || barTime == g_lastBarTime)
      return;
   g_pendingType = -1;                 // a new bar replaces any stale pending signal

//--- MACD values of the two last CLOSED bars (shift 1 and 2)
   double macd[2], sig[2];
   if(CopyBuffer(g_macdHandle, MAIN_LINE,   1, 2, macd) < 2 ||
      CopyBuffer(g_macdHandle, SIGNAL_LINE, 1, 2, sig)  < 2)
      return;                      // data not ready, retry next tick
//--- CopyBuffer fills oldest-first: [0] = shift 2, [1] = shift 1
   const double macdPrev = macd[0], macdCurr = macd[1];
   const double sigPrev  = sig[0],  sigCurr  = sig[1];

//--- mark bar processed (memory + persistent)
   g_lastBarTime = barTime;
   GlobalVariableSet(g_gvLastBar, (double)barTime);

//--- crossover detection
   bool crossUp   = (macdPrev <= sigPrev) && (macdCurr >  sigCurr);
   bool crossDown = (macdPrev >= sigPrev) && (macdCurr <  sigCurr);
   if(!crossUp && !crossDown)
     {
      LogBar(barTime, macdCurr, sigCurr, "no cross");
      return;
     }

//--- zero-line filter
   if(InpZeroLineFilter)
     {
      if(crossUp   && macdCurr >= 0.0) crossUp   = false;
      if(crossDown && macdCurr <= 0.0) crossDown = false;
      if(!crossUp && !crossDown)
        {
         LogBar(barTime, macdCurr, sigCurr, "cross blocked by zero-line filter");
         return;
        }
     }

//--- trading hours filter (applies to opening only)
   if(InpUseTimeFilter && !InTradingHours(barTime))
     {
      LogBar(barTime, macdCurr, sigCurr, "cross outside trading hours");
      return;
     }

//--- signal strength filter
   if(InpMinGapATR > 0.0)
     {
      double atr[1];
      if(CopyBuffer(g_atrHandle, 0, 1, 1, atr) < 1 || atr[0] <= 0.0)
         return;
      if(MathAbs(macdCurr - sigCurr) < InpMinGapATR * atr[0])
        {
         LogBar(barTime, macdCurr, sigCurr, "cross too weak (gap filter)");
         return;
        }
     }

//--- trend filter: last closed signal-bar close vs. trend EMA (last closed trend bar)
   if(InpUseTrendFilter)
     {
      double ema[1];
      if(CopyBuffer(g_trendHandle, 0, 1, 1, ema) < 1)
         return;
      double close = iClose(_Symbol, InpTimeframe, 1);
      if(crossUp   && close <= ema[0]) crossUp   = false;
      if(crossDown && close >= ema[0]) crossDown = false;
      if(!crossUp && !crossDown)
        {
         LogBar(barTime, macdCurr, sigCurr, StringFormat("cross blocked by trend filter (close %.2f vs EMA %.2f)", close, ema[0]));
         return;
        }
     }

   LogBar(barTime, macdCurr, sigCurr, crossUp ? "BUY signal" : "SELL signal");
   HandleSignal(crossUp ? POSITION_TYPE_BUY : POSITION_TYPE_SELL);
  }

//+------------------------------------------------------------------+
//| Trading hours check (server time of the signal bar)              |
//+------------------------------------------------------------------+
bool InTradingHours(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   if(InpStartHour < InpEndHour)
      return(dt.hour >= InpStartHour && dt.hour < InpEndHour);
   return(dt.hour >= InpStartHour || dt.hour < InpEndHour);   // overnight window
  }

//+------------------------------------------------------------------+
//| Act on a directional signal                                      |
//+------------------------------------------------------------------+
void HandleSignal(const ENUM_POSITION_TYPE wanted)
  {
   if(!TradingAllowed())
      return;

   bool blocked = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(type == wanted)
         blocked = true;                     // already in this direction
      else if(InpCloseOnOpposite)
        {
         if(!g_trade.PositionClose(ticket))
            PrintFormat("Close #%I64u failed: %d %s", ticket, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
        }
      else
         blocked = true;                     // keep opposite position, do not hedge
     }

   if(!blocked)
      OpenPosition(wanted);
  }

//+------------------------------------------------------------------+
//| Open a market position with % SL / TP and risk-based lot          |
//+------------------------------------------------------------------+
void OpenPosition(const ENUM_POSITION_TYPE type)
  {
   if(!g_symbol.RefreshRates())
      return;

   string why;
   if(!GuardsAllowEntry(why))
     {
      PrintFormat("Signal skipped, %s", why);
      return;
     }

   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
     {
      if(InpSpreadWaitMin > 0 && g_pendingType < 0)
        {
         g_pendingType = (int)type;
         g_pendingBar  = iTime(_Symbol, InpTimeframe, 0);
         PrintFormat("Signal delayed, spread %d > %d points -- retrying for up to %d min",
                     g_symbol.Spread(), InpMaxSpreadPoints, InpSpreadWaitMin);
        }
      else if(InpSpreadWaitMin <= 0)
         PrintFormat("Signal skipped, spread %d > %d points", g_symbol.Spread(), InpMaxSpreadPoints);
      return;
     }

   const int    digits  = g_symbol.Digits();
   const double minDist = (double)g_symbol.StopsLevel() * g_symbol.Point();
   const bool   isBuy   = (type == POSITION_TYPE_BUY);
   const double price   = isBuy ? g_symbol.Ask() : g_symbol.Bid();

   double slDist = MathMax(price * InpStopLossPct   / 100.0, minDist);
   double tpDist = MathMax(price * InpTakeProfitPct / 100.0, minDist);

   double sl = NormalizeDouble(isBuy ? price - slDist : price + slDist, digits);
   double tp = NormalizeDouble(isBuy ? price + tpDist : price - tpDist, digits);

   double lots = CalculateLots(MathAbs(price - sl), isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, price);
   if(lots <= 0.0)
     {
      Print("Signal skipped, lot calculation returned 0 (risk too small or insufficient margin)");
      return;
     }

   bool ok = isBuy ? g_trade.Buy (lots, _Symbol, price, sl, tp, InpComment)
                   : g_trade.Sell(lots, _Symbol, price, sl, tp, InpComment);

   uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s failed: %d %s", isBuy ? "Buy" : "Sell", rc, g_trade.ResultRetcodeDescription());
      return;
     }
//--- remember entry context for the trade log
   const double fill = g_trade.ResultPrice();
   const int    n    = ArraySize(g_entries);
   ArrayResize(g_entries, n + 1, 64);
   g_entries[n].position = g_trade.ResultOrder();
   g_entries[n].spread   = g_symbol.Spread();
   g_entries[n].slippage = (fill > 0.0) ? (isBuy ? fill - price : price - fill) / g_symbol.Point() : 0.0;
   double perLot = 0.0;
   g_entries[n].risk = (OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, price, sl, perLot) && perLot < 0.0)
                       ? -perLot : 0.0;
  }

//+------------------------------------------------------------------+
//| Break-even and trailing stop for own positions                    |
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   if(!g_symbol.RefreshRates())
      return;

   const int    digits    = g_symbol.Digits();
   const double point     = g_symbol.Point();
   const double minDist   = (double)MathMax(g_symbol.StopsLevel(), g_symbol.FreezeLevel()) * point;

//--- ATR for volatility-based trailing (last closed bar), fetched once per tick
   double atrDist = 0.0;
   if(InpTrailStartPct > 0.0 && InpTrailATRMult > 0.0)
     {
      double atr[1];
      if(CopyBuffer(g_atrHandle, 0, 1, 1, atr) < 1 || atr[0] <= 0.0)
         return;
      atrDist = atr[0] * InpTrailATRMult;
     }

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;

      const ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      const bool   isBuy   = (type == POSITION_TYPE_BUY);
      const double open    = PositionGetDouble(POSITION_PRICE_OPEN);
      const double curSL   = PositionGetDouble(POSITION_SL);
      const double curTP   = PositionGetDouble(POSITION_TP);
      const double market  = isBuy ? g_symbol.Bid() : g_symbol.Ask();   // exit price
      const double profit  = isBuy ? market - open : open - market;     // in price units
      if(profit <= 0.0)
         continue;

      double newSL = 0.0;

      //--- break-even
      if(InpBreakEvenPct > 0.0 && profit >= open * InpBreakEvenPct / 100.0)
        {
         double lock = open * InpBreakEvenLockPct / 100.0;
         newSL = isBuy ? open + lock : open - lock;
        }

      //--- trailing
      if(InpTrailStartPct > 0.0 && profit >= open * InpTrailStartPct / 100.0)
        {
         double dist  = (atrDist > 0.0) ? atrDist : market * InpTrailDistPct / 100.0;
         double trail = isBuy ? market - dist : market + dist;
         if(newSL == 0.0 || (isBuy ? trail > newSL : trail < newSL))
            newSL = trail;
        }

      if(newSL == 0.0)
         continue;

      //--- respect stops / freeze level
      if(isBuy  && market - newSL < minDist) newSL = market - minDist;
      if(!isBuy && newSL - market < minDist) newSL = market + minDist;
      newSL = NormalizeDouble(newSL, digits);

      //--- only ever tighten, and only if it actually changes
      bool better = (curSL == 0.0) || (isBuy ? newSL > curSL + point : newSL < curSL - point);
      if(!better)
         continue;

      if(!g_trade.PositionModify(ticket, newSL, curTP))
         PrintFormat("Modify #%I64u failed: %d %s", ticket, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Lot size from risk, SL distance, broker limits and free margin   |
//+------------------------------------------------------------------+
double CalculateLots(const double slDistance, const ENUM_ORDER_TYPE orderType, const double price)
  {
   if(slDistance <= 0.0)
      return(0.0);

   double riskMoney = (InpRiskMode == RISK_MONEY)
                      ? InpRiskValue
                      : AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskValue / 100.0;
   if(riskMoney <= 0.0)
      return(0.0);

   double tickSize  = g_symbol.TickSize();
   double tickValue = g_symbol.TickValueLoss();
   if(tickSize <= 0.0 || tickValue <= 0.0)
      return(0.0);

   double lossPerLot = slDistance / tickSize * tickValue;
   if(lossPerLot <= 0.0)
      return(0.0);

   double lots = riskMoney / lossPerLot;

   const double step   = g_symbol.LotsStep();
   const double minLot = g_symbol.LotsMin();
   const double maxLot = g_symbol.LotsMax();
   if(step <= 0.0)
      return(0.0);

   lots = MathFloor(lots / step) * step;
   if(lots > maxLot) lots = maxLot;
   if(lots < minLot) return(0.0);        // never exceed configured risk

   double margin = 0.0;
   if(!OrderCalcMargin(orderType, _Symbol, lots, price, margin))
      return(0.0);
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.95;
   if(margin > freeMargin && margin > 0.0)
     {
      lots = MathFloor(lots * freeMargin / margin / step) * step;
      if(lots < minLot)
         return(0.0);
     }

   const int volDigits = (int)MathRound(-MathLog10(step));
   return(NormalizeDouble(lots, volDigits));
  }

//+------------------------------------------------------------------+
//| True if the currently selected position belongs to this EA       |
//+------------------------------------------------------------------+
bool IsOwnPosition()
  {
   return(PositionGetInteger(POSITION_MAGIC) == (long)InpMagic &&
          PositionGetString(POSITION_SYMBOL) == _Symbol);
  }

//+------------------------------------------------------------------+
int CountOwnPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && IsOwnPosition())
         count++;
   return(count);
  }

//+------------------------------------------------------------------+
//| Terminal / account permission check                              |
//+------------------------------------------------------------------+
bool TradingAllowed()
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
     {
      Print("Trading is not allowed (terminal/account/EA settings)");
      return(false);
     }
   if(g_symbol.TradeMode() == SYMBOL_TRADE_MODE_DISABLED || g_symbol.TradeMode() == SYMBOL_TRADE_MODE_CLOSEONLY)
     {
      Print("Trading disabled for symbol ", _Symbol);
      return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
//| Custom optimization criterion ("Custom max" in the tester)        |
//| net profit x profit factor, penalised by relative drawdown        |
//+------------------------------------------------------------------+
double OnTester()
  {
   double trades = TesterStatistics(STAT_TRADES);
   if(trades < 30)
      return(0.0);
   double profit = TesterStatistics(STAT_PROFIT);
   if(profit <= 0.0)
      return(0.0);
   double pf  = TesterStatistics(STAT_PROFIT_FACTOR);
   double ddP = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
   return(profit * pf / (1.0 + ddP));
  }
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| v1.42 safety guards                                              |
//+------------------------------------------------------------------+
//--- balance at time t = current balance minus trading P&L booked since t (deposits excluded)
double BalanceAt(const datetime t)
  {
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(!HistorySelect(t, TimeCurrent() + 86400))
      return(bal);
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong d = HistoryDealGetTicket(i);
      const long  k = HistoryDealGetInteger(d, DEAL_TYPE);
      if(d != 0 && k != DEAL_TYPE_BALANCE && k != DEAL_TYPE_CREDIT)
         bal -= HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
                HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
   return(bal);
  }

void UpdatePeriodBalances()
  {
   const datetime now = TimeCurrent();
   const datetime day = now - now % 86400;
   if(day != g_dayStart)
     {
      g_dayStart   = day;
      g_dayBalance = BalanceAt(day);
      g_dayStopHit = false;
     }
   MqlDateTime t;
   TimeToStruct(now, t);
   t.day = 1; t.hour = 0; t.min = 0; t.sec = 0;
   const datetime month = StructToTime(t);
   if(month != g_monthStart)
     {
      g_monthStart   = month;
      g_monthBalance = BalanceAt(month);
     }
  }

double DailyFloor()   { return(g_dayBalance   * (1.0 - InpDailyLossPct   / 100.0)); }
double MonthlyFloor() { return(g_monthBalance * (1.0 - InpMonthlyLossPct / 100.0)); }
bool   DailyStopped()   { return(InpDailyLossPct   > 0.0 && AccountInfoDouble(ACCOUNT_EQUITY) <= DailyFloor()); }
bool   MonthlyStopped() { return(InpMonthlyLossPct > 0.0 && AccountInfoDouble(ACCOUNT_EQUITY) <= MonthlyFloor()); }

string GuardStatus()
  {
   const double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   return(StringFormat("guards %s | day %+.2f%% (stop -%.1f%%) | month %+.2f%% (stop -%.1f%%)",
                       DailyStopped() ? "DAILY STOP" : MonthlyStopped() ? "MONTHLY STOP" : "ok",
                       g_dayBalance   > 0.0 ? 100.0 * (eq / g_dayBalance   - 1.0) : 0.0, InpDailyLossPct,
                       g_monthBalance > 0.0 ? 100.0 * (eq / g_monthBalance - 1.0) : 0.0, InpMonthlyLossPct));
  }

void GuardsOnTick()
  {
   UpdatePeriodBalances();
   if(!DailyStopped())
      return;
   if(!g_dayStopHit)
     {
      g_dayStopHit = true;
      PrintFormat("DAILY STOP: equity %.2f <= floor %.2f (-%.1f%% of day start %.2f) -- closing own positions, no entries until next server day",
                  AccountInfoDouble(ACCOUNT_EQUITY), DailyFloor(), InpDailyLossPct, g_dayBalance);
     }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && IsOwnPosition() && !g_trade.PositionClose(ticket))
         PrintFormat("Daily stop close #%I64u failed: %d %s -- retrying", ticket,
                     g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
     }
  }

bool GuardsAllowEntry(string &why)
  {
   why = "";
   if(DailyStopped())
      why = StringFormat("daily loss stop (equity %.2f <= %.2f)", AccountInfoDouble(ACCOUNT_EQUITY), DailyFloor());
   else if(MonthlyStopped())
      why = StringFormat("monthly loss stop (equity %.2f <= %.2f)", AccountInfoDouble(ACCOUNT_EQUITY), MonthlyFloor());
   return(why == "");
  }

//--- a spread-blocked signal is retried on every tick of the same bar, up to InpSpreadWaitMin minutes
void RetryPendingSignal(const datetime barTime)
  {
   if(barTime != g_pendingBar || TimeCurrent() - g_pendingBar > InpSpreadWaitMin * 60)
     {
      PrintFormat("Delayed %s signal dropped, spread stayed above %d points",
                  g_pendingType == POSITION_TYPE_BUY ? "BUY" : "SELL", InpMaxSpreadPoints);
      g_pendingType = -1;
      return;
     }
   if(!g_symbol.RefreshRates() || g_symbol.Spread() > InpMaxSpreadPoints)
      return;
   const ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)g_pendingType;
   g_pendingType = -1;
   PrintFormat("Spread back to %d points, executing delayed %s signal", g_symbol.Spread(),
               type == POSITION_TYPE_BUY ? "BUY" : "SELL");
   HandleSignal(type);
  }

//+------------------------------------------------------------------+
//| Closed-trade CSV log (RiskGuard.mqh columns, read by prop_mc.py) |
//+------------------------------------------------------------------+
#define TRADE_LOG_HEADER "close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"

void LogInit()
  {
   g_logFile = "";
   if(!InpTradeLog || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   const bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   g_logFlags = tester ? FILE_COMMON : 0;
   const string name = StringFormat("MACD_Cross_EA_%s_%I64u%s_trades.csv", _Symbol, InpMagic, tester ? "_tester" : "");
   if(tester)
      FileDelete(name, g_logFlags);
   const int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_logFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Trade log: cannot open %s, error %d -- logging off", name, GetLastError());
      return;
     }
   if(FileSize(h) == 0)
      FileWriteString(h, TRADE_LOG_HEADER);
   FileClose(h);
   g_logFile = name;
  }

string ReasonName(const long reason)
  {
   switch((int)reason)
     {
      case DEAL_REASON_SL:     return("sl");
      case DEAL_REASON_TP:     return("tp");
      case DEAL_REASON_SO:     return("stop_out");
      case DEAL_REASON_EXPERT: return(g_dayStopHit ? "daily_stop" : "expert");
      case DEAL_REASON_CLIENT: return(MQLInfoInteger(MQL_TESTER) ? "end_of_test" : "manual");
      case DEAL_REASON_MOBILE:
      case DEAL_REASON_WEB:    return("manual");
     }
   return("other");
  }

void LogClosingDeal(const ulong deal)
  {
   if(g_logFile == "" || !HistoryDealSelect(deal))
      return;
   const long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
      return;
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
      return;
   const datetime closeTime = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   const ulong    pos    = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   const bool     wasBuy = (HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_SELL);
   const double   vol    = HistoryDealGetDouble(deal, DEAL_VOLUME);
   const double   px     = HistoryDealGetDouble(deal, DEAL_PRICE);
   const double   gross  = HistoryDealGetDouble(deal, DEAL_PROFIT);
   const double   swap   = HistoryDealGetDouble(deal, DEAL_SWAP);
   const double   outFee = HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
   const double   sl     = HistoryDealGetDouble(deal, DEAL_SL);
   const double   tp     = HistoryDealGetDouble(deal, DEAL_TP);
   const string   reason = ReasonName(HistoryDealGetInteger(deal, DEAL_REASON));

   if(!HistorySelectByPosition(pos))
      return;
   datetime openTime = 0;
   long     inMagic  = -1;
   double   inVol = 0.0, inPxVol = 0.0, inFee = 0.0;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      const ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;
      const double v = HistoryDealGetDouble(d, DEAL_VOLUME);
      if(openTime == 0)
        {
         openTime = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
         inMagic  = HistoryDealGetInteger(d, DEAL_MAGIC);
        }
      inVol   += v;
      inPxVol += HistoryDealGetDouble(d, DEAL_PRICE) * v;
      inFee   += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
   if(inMagic != (long)InpMagic || inVol <= 0.0)
      return;
   const double share = MathMin(vol / inVol, 1.0);
   const double comm  = outFee + inFee * share;
   const double net   = gross + swap + comm;
   const int    dg    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string spread = "", slip = "", rMult = "";
   for(int i = ArraySize(g_entries) - 1; i >= 0; i--)
      if(g_entries[i].position == pos)
        {
         spread = IntegerToString(g_entries[i].spread);
         slip   = DoubleToString(g_entries[i].slippage, 1);
         if(g_entries[i].risk > 0.0)
            rMult = DoubleToString(net / (g_entries[i].risk * share), 3);
         break;
        }
   const int h = FileOpen(g_logFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_logFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Trade log write failed, error %d", GetLastError());
      return;
     }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, StringFormat("%s,%s,%s,%s,%.2f,%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%s,%s,%s,%s,%I64u,%I64u,%I64u\n",
                   TimeToString(closeTime, TIME_DATE | TIME_SECONDS), TimeToString(openTime, TIME_DATE | TIME_SECONDS),
                   _Symbol, wasBuy ? "buy" : "sell", vol, DoubleToString(inPxVol / inVol, dg), DoubleToString(px, dg),
                   DoubleToString(sl, dg), DoubleToString(tp, dg), gross, comm, swap, net,
                   AccountInfoDouble(ACCOUNT_BALANCE), rMult, reason, spread, slip, deal, pos, InpMagic));
   FileClose(h);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal != 0)
      LogClosingDeal(trans.deal);
  }
//+------------------------------------------------------------------+
