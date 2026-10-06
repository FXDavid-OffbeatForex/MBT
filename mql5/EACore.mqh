//+------------------------------------------------------------------+
//| EACore.mqh -- shared execution core for MBT EAs                  |
//| Copied from MACD_Cross_EA v1.42 without behaviour changes:       |
//| risk sizing, spread guard + in-bar retry, daily/monthly loss     |
//| stops, closed-trade CSV log, Ready check / Bar status lines,     |
//| restart-safe bar tracking. Strategy-free.                        |
//|                                                                  |
//| Before including, the EA defines:                                |
//|   #define EA_NAME    "MeanRev_EA"                                |
//|   #define EA_VERSION "1.00"                                      |
//|   #define EA_MAGIC   240819                                      |
//|   #define EA_COMMENT "MREV"                                      |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

#ifndef EA_TIMEFRAME
#define EA_TIMEFRAME PERIOD_H1        // an EA may #define a different compiled default before including
#endif
#ifndef EA_MAX_SPREAD_POINTS
#define EA_MAX_SPREAD_POINTS 150       // an EA may #define a different compiled default before including
#endif
#ifndef EA_SPREAD_WAIT_MIN
#define EA_SPREAD_WAIT_MIN 30          // an EA may #define a different compiled default before including
#endif
#ifndef EA_DAILY_LOSS_PCT
#define EA_DAILY_LOSS_PCT 4.0          // an EA may #define a different compiled default before including
#endif
#ifndef EA_MONTHLY_LOSS_PCT
#define EA_MONTHLY_LOSS_PCT 12.0       // an EA may #define a different compiled default before including
#endif

enum ENUM_RISK_MODE
  {
   RISK_MONEY   = 0,  // Fixed money per trade (account currency)
   RISK_PERCENT = 1   // Percent of account balance per trade
  };

input group "=== General ==="
input ulong               InpMagic           = EA_MAGIC;    // Magic number
input ENUM_TIMEFRAMES     InpTimeframe       = EA_TIMEFRAME; // Signal timeframe
input string              InpComment         = EA_COMMENT;  // Order comment
input int                 InpSlippagePoints  = 30;          // Max deviation (points)
input int                 InpMaxSpreadPoints = EA_MAX_SPREAD_POINTS; // Max spread to trade (points, 0 = off)

input group "=== Risk ==="
input ENUM_RISK_MODE      InpRiskMode        = RISK_PERCENT;// Risk mode
input double              InpRiskValue       = 1.0;         // Risk value (money or %)

input group "=== Safety guards ==="
input int                 InpSpreadWaitMin   = EA_SPREAD_WAIT_MIN; // Retry a spread-blocked signal for N minutes (0 = drop it)
input double              InpDailyLossPct    = EA_DAILY_LOSS_PCT;   // Daily loss stop, % of day-start balance (0 = off)
input double              InpMonthlyLossPct  = EA_MONTHLY_LOSS_PCT; // Monthly loss stop, % of month-start balance (0 = off)
input bool                InpTradeLog        = true;        // Write closed trades to a CSV in MQL5\Files

input group "=== Diagnostics ==="
input bool                InpLogEveryBar     = true;        // Log a status line on every new signal bar

#define TRADE_LOG_HEADER "close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"

CTrade        g_trade;
CSymbolInfo   g_symbol;
datetime      g_lastBarTime  = 0;
string        g_gvLastBar    = "";
int           g_pendingType  = -1;     // spread-blocked signal: POSITION_TYPE_BUY/SELL, -1 = none
datetime      g_pendingBar   = 0;
double        g_pendingSL    = 0.0;
double        g_pendingTP    = 0.0;
datetime      g_dayStart     = 0;
double        g_dayBalance   = 0.0;
datetime      g_monthStart   = 0;
double        g_monthBalance = 0.0;
bool          g_dayStopHit   = false;
string        g_logFile      = "";
int           g_logFlags     = 0;
uint          g_lastCloseFailMsg = 0;
ulong         g_lastLoggedDeal   = 0;     // newest closing deal written to the trade log
struct EntryInfo   { ulong position; int spread; double slippage; double risk; };
struct ForcedClose { ulong position; string reason; };
EntryInfo     g_entries[];
ForcedClose   g_forced[];

//+------------------------------------------------------------------+
//| Lifecycle                                                        |
//+------------------------------------------------------------------+
bool Core_Init()
  {
   if(InpRiskValue <= 0.0 || (InpRiskMode == RISK_PERCENT && InpRiskValue > 100.0))
     {
      Print("Invalid risk value");
      return(false);
     }
   if(InpSpreadWaitMin < 0 || InpDailyLossPct < 0.0 || InpDailyLossPct >= 100.0 ||
      InpMonthlyLossPct < 0.0 || InpMonthlyLossPct >= 100.0)
     {
      Print("Invalid safety guard inputs");
      return(false);
     }
   if(!g_symbol.Name(_Symbol))
     {
      Print("Failed to initialise symbol ", _Symbol);
      return(false);
     }
   g_symbol.Refresh();

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);

   g_gvLastBar = StringFormat("%s_%s_%s_%I64u", EA_NAME, _Symbol, EnumToString(InpTimeframe), InpMagic);
   if(GlobalVariableCheck(g_gvLastBar))
      g_lastBarTime = (datetime)GlobalVariableGet(g_gvLastBar);
   UpdatePeriodBalances();
   LogInit();
   return(true);
  }

void Core_PrintReady()
  {
   PrintFormat("Guards: max spread %s, retry %d min | daily stop %s | monthly stop %s | trade log %s",
               InpMaxSpreadPoints > 0 ? StringFormat("%d pts", InpMaxSpreadPoints) : "off", InpSpreadWaitMin,
               InpDailyLossPct > 0.0 ? StringFormat("-%.1f%%", InpDailyLossPct) : "off",
               InpMonthlyLossPct > 0.0 ? StringFormat("-%.1f%%", InpMonthlyLossPct) : "off",
               InpTradeLog ? "on" : "off");
   Print("Ready check: ", TradePermissionText(), " | ", GuardStatus());
  }

void Core_Deinit(const int reason)
  {
//--- tester only: catch up closing deals that raised no trade event (the end-of-test close).
//--- Never live: a VPS re-sync or restart would re-append the whole account history to the log.
   if(g_logFile != "" && MQLInfoInteger(MQL_TESTER) && HistorySelect(0, TimeCurrent() + 86400))
     {
      ulong pending[];
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         const ulong d = HistoryDealGetTicket(i);
         if(d > g_lastLoggedDeal)
           {
            const int n = ArraySize(pending);
            ArrayResize(pending, n + 1, 16);
            pending[n] = d;
           }
        }
      for(int i = 0; i < ArraySize(pending); i++)
         LogClosingDeal(pending[i]);
     }
   if(reason == REASON_REMOVE || reason == REASON_PARAMETERS || reason == REASON_CHARTCHANGE)
      GlobalVariableDel(g_gvLastBar);
  }

//+------------------------------------------------------------------+
//| Bar tracking                                                     |
//+------------------------------------------------------------------+
datetime Core_CurrentBar()                 { return(iTime(_Symbol, InpTimeframe, 0)); }
bool     Core_IsNewBar(const datetime bar) { return(bar != 0 && bar != g_lastBarTime); }

void Core_MarkBar(const datetime bar)
  {
   g_lastBarTime = bar;
   GlobalVariableSet(g_gvLastBar, (double)bar);
  }

//+------------------------------------------------------------------+
//| Diagnostics                                                      |
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

void Core_LogBar(const datetime bar, const string detail)
  {
   if(!InpLogEveryBar)
      return;
   PrintFormat("Bar %s | %s | own positions %d | trading %s | %s",
               TimeToString(bar, TIME_DATE | TIME_MINUTES), detail, CountOwnPositions(),
               TradePermissionText(), GuardStatus());
  }

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
bool IsOwnPosition()
  {
   return(PositionGetInteger(POSITION_MAGIC) == (long)InpMagic &&
          PositionGetString(POSITION_SYMBOL) == _Symbol);
  }

int CountOwnPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && IsOwnPosition())
         count++;
   return(count);
  }

bool Core_SelectOwnPosition(ulong &ticket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong t = PositionGetTicket(i);
      if(t != 0 && IsOwnPosition())
        {
         ticket = t;
         return(true);
        }
     }
   return(false);
  }

bool Core_ClosePosition(const ulong ticket, const string reason)
  {
   if(!PositionSelectByTicket(ticket))
      return(false);
   const int n = ArraySize(g_forced);
   ArrayResize(g_forced, n + 1, 16);
   g_forced[n].position = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
   g_forced[n].reason   = reason;
   if(g_trade.PositionClose(ticket))
      return(true);
   if(GetTickCount() - g_lastCloseFailMsg > 60000)
     {
      g_lastCloseFailMsg = GetTickCount();
      PrintFormat("Close #%I64u (%s) failed: %d %s -- retrying every tick", ticket, reason,
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
     }
   return(false);
  }

bool TradingAllowed()
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
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
//| Entry                                                            |
//+------------------------------------------------------------------+
double CalculateLots(const double slDistance, const ENUM_ORDER_TYPE orderType, const double price)
  {
   if(slDistance <= 0.0)
      return(0.0);
   double riskMoney = (InpRiskMode == RISK_MONEY) ? InpRiskValue
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

bool Core_Open(const ENUM_POSITION_TYPE type, const double slDistance, const double tpPrice)
  {
   if(!TradingAllowed() || !g_symbol.RefreshRates())
      return(false);
   string why;
   if(!GuardsAllowEntry(why))
     {
      PrintFormat("Signal skipped, %s", why);
      return(false);
     }
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
     {
      if(InpSpreadWaitMin > 0 && g_pendingType < 0)
        {
         g_pendingType = (int)type;
         g_pendingBar  = Core_CurrentBar();
         g_pendingSL   = slDistance;
         g_pendingTP   = tpPrice;
         PrintFormat("Signal delayed, spread %d > %d points -- retrying for up to %d min",
                     g_symbol.Spread(), InpMaxSpreadPoints, InpSpreadWaitMin);
        }
      else if(InpSpreadWaitMin <= 0)
         PrintFormat("Signal skipped, spread %d > %d points", g_symbol.Spread(), InpMaxSpreadPoints);
      return(false);
     }

   const int    digits  = g_symbol.Digits();
   const double minDist = (double)g_symbol.StopsLevel() * g_symbol.Point();
   const bool   isBuy   = (type == POSITION_TYPE_BUY);
   const double price   = isBuy ? g_symbol.Ask() : g_symbol.Bid();
   const double slDist  = MathMax(slDistance, minDist);
   const double sl      = NormalizeDouble(isBuy ? price - slDist : price + slDist, digits);
   double tp = 0.0;
   if(tpPrice > 0.0)
     {
      const bool valid = isBuy ? (tpPrice > price && tpPrice - price >= minDist)
                               : (tpPrice < price && price - tpPrice >= minDist);
      if(!valid)
        {
         PrintFormat("Signal skipped, take-profit %s is not beyond entry price %s",
                     DoubleToString(tpPrice, digits), DoubleToString(price, digits));
         return(false);
        }
      tp = NormalizeDouble(tpPrice, digits);
     }

   const double lots = CalculateLots(MathAbs(price - sl), isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, price);
   if(lots <= 0.0)
     {
      Print("Signal skipped, lot calculation returned 0 (risk too small or insufficient margin)");
      return(false);
     }
   const bool ok = isBuy ? g_trade.Buy (lots, _Symbol, price, sl, tp, InpComment)
                         : g_trade.Sell(lots, _Symbol, price, sl, tp, InpComment);
   const uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s failed: %d %s", isBuy ? "Buy" : "Sell", rc, g_trade.ResultRetcodeDescription());
      return(false);
     }
   const double fill = g_trade.ResultPrice();
   const int    n    = ArraySize(g_entries);
   ArrayResize(g_entries, n + 1, 64);
   g_entries[n].position = g_trade.ResultOrder();
   g_entries[n].spread   = g_symbol.Spread();
   g_entries[n].slippage = (fill > 0.0) ? (isBuy ? fill - price : price - fill) / g_symbol.Point() : 0.0;
   double perLot = 0.0;
   g_entries[n].risk = (OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, price, sl, perLot)
                        && perLot < 0.0) ? -perLot : 0.0;
   return(true);
  }

void Core_RetryPending(const datetime barTime)
  {
   if(g_pendingType < 0)
      return;
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
   if(CountOwnPositions() > 0)
      return;
   PrintFormat("Spread back to %d points, executing delayed %s signal", g_symbol.Spread(),
               type == POSITION_TYPE_BUY ? "BUY" : "SELL");
   Core_Open(type, g_pendingSL, g_pendingTP);
  }

//+------------------------------------------------------------------+
//| Loss stops                                                       |
//+------------------------------------------------------------------+
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

double DailyFloor()     { return(g_dayBalance   * (1.0 - InpDailyLossPct   / 100.0)); }
double MonthlyFloor()   { return(g_monthBalance * (1.0 - InpMonthlyLossPct / 100.0)); }
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

void Core_OnTickStart()
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
      if(ticket != 0 && IsOwnPosition())
         Core_ClosePosition(ticket, "daily_stop");
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

//+------------------------------------------------------------------+
//| Closed-trade CSV log                                             |
//+------------------------------------------------------------------+
void LogInit()
  {
   g_logFile = "";
   if(!InpTradeLog || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   const bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   g_logFlags = tester ? FILE_COMMON : 0;
   const string name = StringFormat("%s_%s_%I64u%s_trades.csv", EA_NAME, _Symbol, InpMagic, tester ? "_tester" : "");
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

string ReasonName(const long reason, const ulong pos)
  {
   for(int i = ArraySize(g_forced) - 1; i >= 0; i--)
      if(g_forced[i].position == pos)
         return(g_forced[i].reason);
   switch((int)reason)
     {
      case DEAL_REASON_SL:     return("sl");
      case DEAL_REASON_TP:     return("tp");
      case DEAL_REASON_SO:     return("stop_out");
      case DEAL_REASON_EXPERT: return("expert");
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
   if(deal > g_lastLoggedDeal && HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN)
      g_lastLoggedDeal = deal;
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
   const string   reason = ReasonName(HistoryDealGetInteger(deal, DEAL_REASON), pos);

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

void Core_OnTradeTransaction(const MqlTradeTransaction &trans)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal != 0)
      LogClosingDeal(trans.deal);
  }

//--- custom optimization criterion ("Custom max"), same as MACD_Cross_EA
double Core_OnTester()
  {
   const double trades = TesterStatistics(STAT_TRADES);
   if(trades < 30)
      return(0.0);
   const double profit = TesterStatistics(STAT_PROFIT);
   if(profit <= 0.0)
      return(0.0);
   return(profit * TesterStatistics(STAT_PROFIT_FACTOR) / (1.0 + TesterStatistics(STAT_EQUITY_DDREL_PERCENT)));
  }
//+------------------------------------------------------------------+
