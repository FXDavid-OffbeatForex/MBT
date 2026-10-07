//+------------------------------------------------------------------+
//|                                              RSI_Reversal_EA.mq5 |
//| Buys when RSI closes below the buy level, sells when it closes   |
//| above the sell level. The same direction trades again only after |
//| RSI has closed back across 50. Optional MA trend filter, SL/TP   |
//| and trailing stop as % of the open price, money or % risk.       |
//|                                                                  |
//| Signals use closed bars of the RSI timeframe, evaluated once per |
//| bar. Re-arm state is rebuilt from the account's deal history at  |
//| start-up, so a restart, crash or VPS migration cannot duplicate  |
//| a trade. Only positions with InpMagic on _Symbol are touched.    |
//|                                                                  |
//| v1.10 defaults tuned on XAUUSD 2019-2024 (each 3-year regime     |
//| profitable on its own), held out 2025-26: PF 1.77 real ticks.    |
//| Original spec defaults: RSI_Reversal_EA_spec.set.                |
//+------------------------------------------------------------------+
#property copyright   "MBT"
#property version     "1.10"
#property description "RSI threshold entries with a re-arm at 50, optional MA filter,"
#property description "SL / TP / trailing stop as % of open price, money or % risk sizing."

#include <Trade\Trade.mqh>

enum ENUM_RISK_MODE
  {
   RISK_PERCENT = 0,  // Percent of account balance
   RISK_MONEY   = 1   // Fixed money amount (account currency)
  };

input group "=== General ==="
input ulong            InpMagic            = 261004;       // Magic number
input string           InpComment          = "RSI_REV";    // Order comment
input int              InpSlippage         = 30;           // Max slippage (points)

input group "=== RSI signal ==="
input ENUM_TIMEFRAMES  InpRsiTimeframe     = PERIOD_H6;    // RSI timeframe
input int              InpRsiPeriod        = 4;            // RSI period
input double           InpRsiBuyLevel      = 40.0;         // Buy when RSI closes below
input double           InpRsiSellLevel     = 55.0;         // Sell when RSI closes above

input group "=== Moving average filter ==="
input bool             InpUseMaFilter      = true;         // Use MA filter (buy above MA, sell below)
input ENUM_TIMEFRAMES  InpMaTimeframe      = PERIOD_H12;   // MA timeframe
input int              InpMaPeriod         = 250;          // MA period
input ENUM_MA_METHOD   InpMaMethod         = MODE_SMA;     // MA type

input group "=== Exits, % of position open price (0 = off) ==="
input double           InpStopLossPct      = 1.0;          // Stop loss %
input double           InpTakeProfitPct    = 5.0;          // Take profit %
input double           InpTrailTriggerPct  = 1.0;          // Trailing stop trigger % (0 = no trailing)
input double           InpTrailDistancePct = 1.0;          // Trailing stop distance %
input double           InpTrailStepPct     = 0.05;         // Trailing stop step %

input group "=== Risk ==="
input ENUM_RISK_MODE   InpRiskMode         = RISK_PERCENT; // Risk mode
input double           InpRiskValue        = 1.0;          // Risk per trade (% of balance or money)
input double           InpLotsWithoutSL    = 0.01;         // Fixed lots when stop loss is 0

#define REARM_LEVEL   50.0  // RSI must close beyond this before the same direction trades again
#define RETRY_SECONDS 10    // pause after a failed trade request before the next attempt

CTrade          g_trade;
int             g_rsiHandle = INVALID_HANDLE;
int             g_maHandle  = INVALID_HANDLE;
ENUM_TIMEFRAMES g_rsiTf;
ENUM_TIMEFRAMES g_maTf;
double          g_slFrac, g_tpFrac, g_trigFrac, g_distFrac, g_stepFrac;
double          g_tickSize, g_lotStep, g_lotMin, g_lotMax;
int             g_lotDigits;
bool            g_hedging;
string          g_gvName;        // terminal global variable holding the last evaluated bar ("" in the tester)
bool            g_synced;        // re-arm flags rebuilt from the account
bool            g_buyArmed;
bool            g_sellArmed;
datetime        g_lastBar;       // last RSI bar whose closed predecessor was evaluated
datetime        g_savedBar;      // last bar written to g_gvName
int             g_pending;       // ORDER_TYPE_BUY / ORDER_TYPE_SELL waiting for execution this bar, -1 = none
double          g_pendingRsi;
int             g_attempts;
datetime        g_nextTry;
string          g_lastNote;      // last reason an entry was delayed or skipped (logged once per change)
datetime        g_trailPause;

//+------------------------------------------------------------------+
//| Lifecycle                                                        |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(!InputsValid())
      return(INIT_PARAMETERS_INCORRECT);

   g_rsiTf = (InpRsiTimeframe == PERIOD_CURRENT) ? Period() : InpRsiTimeframe;
   g_maTf  = (InpMaTimeframe  == PERIOD_CURRENT) ? Period() : InpMaTimeframe;

   g_rsiHandle = iRSI(_Symbol, g_rsiTf, InpRsiPeriod, PRICE_CLOSE);
   if(g_rsiHandle == INVALID_HANDLE)
     {
      PrintFormat("Failed to create RSI handle, error %d", GetLastError());
      return(INIT_FAILED);
     }
   g_maHandle = INVALID_HANDLE;
   if(InpUseMaFilter)
     {
      g_maHandle = iMA(_Symbol, g_maTf, InpMaPeriod, 0, InpMaMethod, PRICE_CLOSE);
      if(g_maHandle == INVALID_HANDLE)
        {
         PrintFormat("Failed to create MA handle, error %d", GetLastError());
         return(INIT_FAILED);
        }
     }

   g_tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(g_tickSize <= 0.0)
      g_tickSize = _Point;
   g_lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   g_lotMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   g_lotMax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(g_lotStep <= 0.0 || g_lotMin <= 0.0 || g_lotMax < g_lotMin)
     {
      PrintFormat("Invalid volume settings for %s", _Symbol);
      return(INIT_FAILED);
     }
   g_lotDigits = 0;
   for(double s = g_lotStep; s < 1.0 - 1e-9 && g_lotDigits < 8; s *= 10.0)
      g_lotDigits++;
   g_hedging = (AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);

   g_slFrac   = InpStopLossPct      / 100.0;
   g_tpFrac   = InpTakeProfitPct    / 100.0;
   g_trigFrac = InpTrailTriggerPct  / 100.0;
   g_distFrac = InpTrailDistancePct / 100.0;
   g_stepFrac = InpTrailStepPct     / 100.0;

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippage);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);

//--- reset explicitly: a parameter or chart change re-runs OnInit without reloading the program
   g_synced     = false;
   g_buyArmed   = true;
   g_sellArmed  = true;
   g_pending    = -1;
   g_attempts   = 0;
   g_nextTry    = 0;
   g_lastNote   = "";
   g_trailPause = 0;
   g_lastBar    = 0;
   g_gvName     = "";
   if(!MQLInfoInteger(MQL_TESTER))
     {
      g_gvName = StringSubstr(StringFormat("RSI_REV_%I64u_%s_%d", InpMagic, _Symbol, PeriodSeconds(g_rsiTf) / 60), 0, 63);
      if(GlobalVariableCheck(g_gvName))
         g_lastBar = (datetime)GlobalVariableGet(g_gvName);
     }
   g_savedBar = g_lastBar;

   PrintFormat("RSI_Reversal_EA %s | magic %I64u | RSI(%d) %s buy < %.1f, sell > %.1f | MA filter %s | "
               "SL %s | TP %s | trailing %s | risk %s | %s account",
               _Symbol, InpMagic, InpRsiPeriod, StringSubstr(EnumToString(g_rsiTf), 7), InpRsiBuyLevel, InpRsiSellLevel,
               InpUseMaFilter ? StringFormat("%s(%d) %s", StringSubstr(EnumToString(InpMaMethod), 5), InpMaPeriod,
                                             StringSubstr(EnumToString(g_maTf), 7)) : "off",
               InpStopLossPct > 0.0 ? StringFormat("%.2f%%", InpStopLossPct) : "none",
               InpTakeProfitPct > 0.0 ? StringFormat("%.2f%%", InpTakeProfitPct) : "none",
               InpTrailTriggerPct > 0.0 ? StringFormat("at +%.2f%%, distance %.2f%%, step %.2f%%",
                                                       InpTrailTriggerPct, InpTrailDistancePct, InpTrailStepPct) : "off",
               InpRiskMode == RISK_PERCENT ? StringFormat("%.2f%% of balance", InpRiskValue)
                                           : StringFormat("%.2f %s", InpRiskValue, AccountInfoString(ACCOUNT_CURRENCY)),
               g_hedging ? "hedging" : "netting (opens only while the symbol has no position)");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_rsiHandle != INVALID_HANDLE)
      IndicatorRelease(g_rsiHandle);
   if(g_maHandle != INVALID_HANDLE)
      IndicatorRelease(g_maHandle);
   g_rsiHandle = INVALID_HANDLE;
   g_maHandle  = INVALID_HANDLE;
//--- keep the bar marker across restarts; drop it when the EA is removed or its inputs change
   if(g_gvName != "" && (reason == REASON_REMOVE || reason == REASON_CHARTCLOSE || reason == REASON_PARAMETERS))
      GlobalVariableDel(g_gvName);
  }

void OnTick()
  {
   if(g_trigFrac > 0.0)
      TrailStops();

   if(!g_synced)
     {
      if(!SyncArmState())
         return;
      g_synced = true;
     }

   const datetime bar = iTime(_Symbol, g_rsiTf, 0);
   if(bar == 0)
      return;
   if(bar != g_lastBar)
     {
      if(g_pending >= 0)
         PrintFormat("%s signal expired unfilled: %s", Side(g_pending == ORDER_TYPE_BUY), g_lastNote);
      g_pending = -1;
      if(!EvaluateBar())
         return;                 // indicator data not ready yet, retry on the next tick
      g_lastBar = bar;
     }
   if(g_pending >= 0 && TimeCurrent() >= g_nextTry)
      TryEntry();

   if(g_gvName != "" && g_pending < 0 && g_savedBar != g_lastBar)
     {
      GlobalVariableSet(g_gvName, (double)g_lastBar);
      GlobalVariablesFlush();
      g_savedBar = g_lastBar;
     }
  }

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
bool InputsValid()
  {
   string err = "";
   if(InpRsiPeriod < 2)
      err = "RSI period must be at least 2";
   else if(InpRsiBuyLevel <= 0.0 || InpRsiBuyLevel > REARM_LEVEL)
      err = "RSI buy level must be above 0 and not above 50";
   else if(InpRsiSellLevel < REARM_LEVEL || InpRsiSellLevel >= 100.0)
      err = "RSI sell level must be at least 50 and below 100";
   else if(InpUseMaFilter && InpMaPeriod < 1)
      err = "MA period must be at least 1";
   else if(InpStopLossPct < 0.0 || InpStopLossPct >= 100.0)
      err = "Stop loss % must be from 0 to below 100";
   else if(InpTakeProfitPct < 0.0)
      err = "Take profit % must not be negative";
   else if(InpTrailTriggerPct < 0.0 || InpTrailDistancePct < 0.0 || InpTrailStepPct < 0.0)
      err = "Trailing stop inputs must not be negative";
   else if(InpTrailTriggerPct > 0.0 && InpTrailDistancePct <= 0.0)
      err = "Trailing distance % must be above 0 when the trailing stop is on";
   else if(InpRiskValue <= 0.0 || (InpRiskMode == RISK_PERCENT && InpRiskValue > 100.0))
      err = "Risk value must be above 0 (and at most 100 in percent mode)";
   else if(InpStopLossPct == 0.0 && InpLotsWithoutSL <= 0.0)
      err = "Fixed lots must be above 0 when the stop loss is 0";
   else if(InpSlippage < 0)
      err = "Slippage must not be negative";
   if(err == "")
      return(true);
   Print("Invalid inputs: ", err);
   return(false);
  }

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
bool IsOwnPosition()
  {
   return(PositionGetInteger(POSITION_MAGIC) == (long)InpMagic && PositionGetString(POSITION_SYMBOL) == _Symbol);
  }

double NormalizePrice(const double price)
  {
   return(NormalizeDouble(MathRound(price / g_tickSize) * g_tickSize, _Digits));
  }

string Side(const bool isBuy)
  {
   return(isBuy ? "BUY" : "SELL");
  }

string PriceText(const double price)
  {
   return(price > 0.0 ? DoubleToString(price, _Digits) : "none");
  }

//--- Broker minimum distance between price and SL/TP, plus one tick so rounding can never undercut it.
double MinStopDistance()
  {
   return((double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point + g_tickSize);
  }

//+------------------------------------------------------------------+
//| Re-arm state                                                     |
//+------------------------------------------------------------------+
//--- Rebuilds the re-arm flags from the account itself: the newest own entry per direction and the
//--- RSI bars that closed after it. Needs no stored state, so restarts and VPS moves are safe.
bool SyncArmState()
  {
   double probe[];
   if(CopyBuffer(g_rsiHandle, 0, 0, 1, probe) != 1 || BarsCalculated(g_rsiHandle) < Bars(_Symbol, g_rsiTf))
      return(false);

   datetime lastBuy = 0, lastSell = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && IsOwnPosition())
        {
         const datetime t = (datetime)PositionGetInteger(POSITION_TIME);
         if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
           { if(t > lastBuy) lastBuy = t; }
         else if(t > lastSell)
            lastSell = t;
        }
   if(!HistorySelect(0, TimeCurrent() + 86400))
      return(false);
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong deal = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != (long)InpMagic ||
         HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN ||
         HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      const datetime t    = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      const long     type = HistoryDealGetInteger(deal, DEAL_TYPE);
      if(type == DEAL_TYPE_BUY)
        { if(t > lastBuy) lastBuy = t; }
      else if(type == DEAL_TYPE_SELL && t > lastSell)
         lastSell = t;
     }

   const int buy  = RearmedSince(lastBuy, true);
   const int sell = RearmedSince(lastSell, false);
   if(buy < 0 || sell < 0)
      return(false);
   g_buyArmed  = (buy == 1);
   g_sellArmed = (sell == 1);
   PrintFormat("State: buys %s (last buy %s), sells %s (last sell %s)",
               g_buyArmed ? "armed" : "wait for RSI > 50", lastBuy > 0 ? TimeToString(lastBuy) : "none",
               g_sellArmed ? "armed" : "wait for RSI < 50", lastSell > 0 ? TimeToString(lastSell) : "none");
   return(true);
  }

//--- 1 = RSI closed beyond 50 on a bar after the entry, 0 = not yet, -1 = data not ready.
int RearmedSince(const datetime entry, const bool isBuy)
  {
   if(entry == 0)
      return(1);
   const int bars = Bars(_Symbol, g_rsiTf);
   if(bars < 2)
      return(-1);
   if(entry < iTime(_Symbol, g_rsiTf, bars - 1))
      return(1);                       // older than the loaded history: long since re-armed
   const int shift = iBarShift(_Symbol, g_rsiTf, entry);
   if(shift < 0)
      return(-1);
   if(shift == 0)
      return(0);                       // entered on the forming bar: no bar has closed since
   double rsi[];
   if(CopyBuffer(g_rsiHandle, 0, 1, shift, rsi) != shift)
      return(-1);
   for(int i = 0; i < shift; i++)
      if(isBuy ? rsi[i] > REARM_LEVEL : rsi[i] < REARM_LEVEL)
         return(1);
   return(0);
  }

//+------------------------------------------------------------------+
//| Signal                                                           |
//+------------------------------------------------------------------+
//--- Runs once per new RSI bar on the bar that just closed. Returns false when data are not ready.
bool EvaluateBar()
  {
   double rsi[];
   if(CopyBuffer(g_rsiHandle, 0, 1, 1, rsi) != 1 || BarsCalculated(g_rsiHandle) < Bars(_Symbol, g_rsiTf))
      return(false);
   const double r = rsi[0];
   if(r > REARM_LEVEL)
      g_buyArmed = true;
   if(r < REARM_LEVEL)
      g_sellArmed = true;

   int signal = -1;
   if(r < InpRsiBuyLevel && g_buyArmed)
      signal = ORDER_TYPE_BUY;
   else if(r > InpRsiSellLevel && g_sellArmed)
      signal = ORDER_TYPE_SELL;
   if(signal < 0)
      return(true);

   if(g_maHandle != INVALID_HANDLE)
     {
      double  ma[];
      MqlTick tick;
      if(CopyBuffer(g_maHandle, 0, 0, 1, ma) != 1 || BarsCalculated(g_maHandle) < Bars(_Symbol, g_maTf) ||
         !SymbolInfoTick(_Symbol, tick))
         return(false);
      const bool   isBuy = (signal == ORDER_TYPE_BUY);
      const double price = isBuy ? tick.ask : tick.bid;
      if(isBuy ? price <= ma[0] : price >= ma[0])
        {
         PrintFormat("%s signal (RSI %.2f) filtered: price %s is %s MA %s", Side(isBuy), r,
                     DoubleToString(price, _Digits), isBuy ? "not above" : "not below", DoubleToString(ma[0], _Digits));
         return(true);
        }
     }
   g_pending    = signal;
   g_pendingRsi = r;
   g_attempts   = 0;
   g_nextTry    = 0;
   g_lastNote   = "";
   return(true);
  }

//+------------------------------------------------------------------+
//| Entry                                                            |
//+------------------------------------------------------------------+
void TryEntry()
  {
   const bool isBuy = (g_pending == ORDER_TYPE_BUY);
//--- a request that timed out may still have filled: never open the same signal twice
   if(g_attempts > 0 && OpenedSince(isBuy, g_lastBar))
     {
      PrintFormat("%s position found after an unconfirmed request, signal done", Side(isBuy));
      Disarm(isBuy);
      return;
     }
   g_attempts++;

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
     {
      Defer("algo trading is disabled", true);
      return;
     }
   const long mode = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(mode == SYMBOL_TRADE_MODE_DISABLED || mode == SYMBOL_TRADE_MODE_CLOSEONLY ||
      mode == (isBuy ? SYMBOL_TRADE_MODE_SHORTONLY : SYMBOL_TRADE_MODE_LONGONLY))
     {
      Defer("the symbol does not allow this direction now", false);
      return;
     }
   if(!g_hedging && PositionSelect(_Symbol))
     {
      Defer("netting account already holds a position on " + _Symbol, false);
      return;
     }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.ask <= 0.0 || tick.bid <= 0.0)
     {
      Defer("no price", true);
      return;
     }

   const double price = isBuy ? tick.ask : tick.bid;
   double sl, tp;
   EntryStops(isBuy, tick, sl, tp);
   string why = "";
   const double lots = CalcLots(isBuy, price, sl, why);
   if(lots <= 0.0)
     {
      Defer(why, false);
      return;
     }

   const bool sent = isBuy ? g_trade.Buy(lots, _Symbol, price, sl, tp, InpComment)
                           : g_trade.Sell(lots, _Symbol, price, sl, tp, InpComment);
   const uint rc = g_trade.ResultRetcode();
   if(sent && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s %s lots @ %s | SL %s | TP %s | RSI %.2f", Side(isBuy), DoubleToString(lots, g_lotDigits),
                  DoubleToString(g_trade.ResultPrice() > 0.0 ? g_trade.ResultPrice() : price, _Digits),
                  PriceText(sl), PriceText(tp), g_pendingRsi);
      Disarm(isBuy);
      return;
     }
   Defer(StringFormat("order failed, %u %s", rc, g_trade.ResultRetcodeDescription()), !PermanentFailure(rc));
  }

void Disarm(const bool isBuy)
  {
   if(isBuy)
      g_buyArmed = false;
   else
      g_sellArmed = false;
   g_pending = -1;
  }

//--- Logs only when the reason changes, so a retry loop cannot flood the journal.
void Defer(const string why, const bool retry)
  {
   if(why != g_lastNote)
      PrintFormat("%s entry %s: %s", Side(g_pending == ORDER_TYPE_BUY), retry ? "delayed" : "skipped", why);
   g_lastNote = why;
   if(retry)
      g_nextTry = TimeCurrent() + RETRY_SECONDS;
   else
      g_pending = -1;
  }

bool PermanentFailure(const uint rc)
  {
   switch(rc)
     {
      case TRADE_RETCODE_INVALID:
      case TRADE_RETCODE_INVALID_VOLUME:
      case TRADE_RETCODE_INVALID_STOPS:
      case TRADE_RETCODE_INVALID_FILL:
      case TRADE_RETCODE_TRADE_DISABLED:
      case TRADE_RETCODE_NO_MONEY:
      case TRADE_RETCODE_LIMIT_VOLUME:
      case TRADE_RETCODE_LIMIT_POSITIONS:
      case TRADE_RETCODE_LONG_ONLY:
      case TRADE_RETCODE_SHORT_ONLY:
      case TRADE_RETCODE_CLOSE_ONLY:
      case TRADE_RETCODE_HEDGE_PROHIBITED:
         return(true);
     }
   return(false);
  }

bool OpenedSince(const bool isBuy, const datetime since)
  {
   const long type = isBuy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && IsOwnPosition() && PositionGetInteger(POSITION_TYPE) == type &&
         (datetime)PositionGetInteger(POSITION_TIME) >= since)
         return(true);
   return(false);
  }

//--- SL/TP as a percentage of the open price, pushed out to the broker's minimum distance if needed.
void EntryStops(const bool isBuy, const MqlTick &tick, double &sl, double &tp)
  {
   const double price   = isBuy ? tick.ask : tick.bid;
   const double minDist = MinStopDistance();
   sl = 0.0;
   tp = 0.0;
   if(g_slFrac > 0.0)
      sl = NormalizePrice(isBuy ? MathMin(price * (1.0 - g_slFrac), tick.bid - minDist)
                                : MathMax(price * (1.0 + g_slFrac), tick.ask + minDist));
   if(g_tpFrac > 0.0)
      tp = NormalizePrice(isBuy ? MathMax(price * (1.0 + g_tpFrac), tick.bid + minDist)
                                : MathMin(price * (1.0 - g_tpFrac), tick.ask - minDist));
  }

//--- Lots that lose the configured risk at the stop loss (fixed lots without a stop), rounded down
//--- to the volume step and reduced if free margin is short. Never rounds up past the risk.
double CalcLots(const bool isBuy, const double price, const double sl, string &why)
  {
   const ENUM_ORDER_TYPE type = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lots = InpLotsWithoutSL;
   if(sl > 0.0)
     {
      const double riskMoney = (InpRiskMode == RISK_MONEY) ? InpRiskValue
                               : AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskValue / 100.0;
      double pnl = 0.0;
      if(!OrderCalcProfit(type, _Symbol, 1.0, price, sl, pnl) || pnl >= 0.0)
        {
         why = StringFormat("cannot value the stop loss distance, error %d", GetLastError());
         return(0.0);
        }
      lots = riskMoney / -pnl;
     }
   const double raw = lots;
   lots = MathMin(MathFloor(lots / g_lotStep + 1e-8) * g_lotStep, g_lotMax);
   if(lots < g_lotMin)
     {
      why = StringFormat("%.4f lots is below the %s lot minimum", raw, DoubleToString(g_lotMin, g_lotDigits));
      return(0.0);
     }
   double margin = 0.0;
   const double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.95;   // keep a 5% buffer
   if(OrderCalcMargin(type, _Symbol, lots, price, margin) && margin > freeMargin)
     {
      lots = MathFloor(lots * freeMargin / margin / g_lotStep) * g_lotStep;
      if(lots < g_lotMin)
        {
         why = "not enough free margin";
         return(0.0);
        }
      PrintFormat("Volume reduced to %s lots by free margin", DoubleToString(lots, g_lotDigits));
     }
   return(NormalizeDouble(lots, g_lotDigits));
  }

//+------------------------------------------------------------------+
//| Trailing stop                                                    |
//+------------------------------------------------------------------+
//--- Once a position is InpTrailTriggerPct in profit its stop follows InpTrailDistancePct behind the
//--- price, and moves only when that gains at least InpTrailStepPct over the current stop.
void TrailStops()
  {
   if(PositionsTotal() == 0 || TimeCurrent() < g_trailPause)
      return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;
      const bool   isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      const double open  = PositionGetDouble(POSITION_PRICE_OPEN);
      const double price = isBuy ? tick.bid : tick.ask;
      if((isBuy ? price - open : open - price) < open * g_trigFrac)
         continue;

      const double sl    = PositionGetDouble(POSITION_SL);
      const double tp    = PositionGetDouble(POSITION_TP);
      const double dist  = MathMax(open * g_distFrac, MinStopDistance());
      const double newSl = NormalizePrice(isBuy ? price - dist : price + dist);
      if(sl > 0.0 && (isBuy ? newSl - sl : sl - newSl) < MathMax(open * g_stepFrac, g_tickSize) - g_tickSize * 0.001)
         continue;
      //--- inside the freeze level the server rejects any change to SL or TP
      const double freeze = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * _Point;
      if(freeze > 0.0 && ((sl > 0.0 && MathAbs(price - sl) <= freeze) || (tp > 0.0 && MathAbs(tp - price) <= freeze)))
         continue;

      if(!g_trade.PositionModify(ticket, newSl, tp) || g_trade.ResultRetcode() != TRADE_RETCODE_DONE)
        {
         PrintFormat("Trailing stop update failed for #%I64u: %u %s", ticket, g_trade.ResultRetcode(),
                     g_trade.ResultRetcodeDescription());
         g_trailPause = TimeCurrent() + RETRY_SECONDS;
         return;
        }
      PrintFormat("Trailing stop #%I64u %s: %s -> %s", ticket, Side(isBuy), PriceText(sl), DoubleToString(newSl, _Digits));
     }
  }
//+------------------------------------------------------------------+
