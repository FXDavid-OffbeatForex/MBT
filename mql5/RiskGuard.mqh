//+------------------------------------------------------------------+
//| RiskGuard.mqh — MBT (MT5 Backtest Toolkit)                       |
//|                                                                  |
//| Prop-firm risk limits + closed-trade log for any EA. Drop it in  |
//| MQL5\Include, then in the EA:                                    |
//|                                                                  |
//|   #include <RiskGuard.mqh>                                       |
//|   OnInit:   if(!RG_Init(InpMagic)) return(INIT_PARAMETERS_INCORRECT);
//|   OnTick:   RG_OnTick();                  // first line          |
//|   entry:    string why;                                          |
//|             if(RG_CanOpen(why)) RG_Open(ORDER_TYPE_BUY, sl, tp, "c");
//|   OnTradeTransaction: RG_OnTradeTransaction(trans);              |
//|   OnDeinit: RG_Deinit();                                         |
//|                                                                  |
//| Enforced on ACCOUNT equity (floating P&L included), like firms:  |
//|  - lot size from risk % of balance and the SL; no SL = no trade  |
//|  - daily stop: equity <= day-start balance - X% of initial       |
//|      -> close positions, no entries until the next day reset     |
//|  - total stop: equity <= initial - Y% of initial                 |
//|      -> close positions, halt for good                           |
//|  - optional target lock: equity >= initial + Z% -> close, halt   |
//|  - entry refused if its SL plus the open SLs could cross a stop  |
//|  - max entries per day, max open positions, max spread, Friday   |
//|    cut-off hour                                                  |
//| Total-stop and target halts survive restarts in the terminal     |
//| global variable RG_<login>_<magic>_halt: delete it (F3) to       |
//| resume. Day-start balance is rebuilt from deal history, so a     |
//| restart mid-day keeps the right daily floor.                     |
//|                                                                  |
//| Trade log: one CSV row per closing deal of a position this magic |
//| opened, rebuilt from history (closes during downtime are caught  |
//| up at the next start). Chart: <terminal>\MQL5\Files\ ; tester:   |
//| Common\Files\ (as SignalLogger.mqh). profit = net of commission  |
//| and swap, balance = after the close, so scripts/prop_mc.py reads |
//| it directly.                                                     |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>

input group "=== Risk guard (prop-firm limits) ==="
input double RG_RiskPct          = 0.5;    // Risk per trade (% of balance)
input double RG_InitialBalance   = 0.0;    // Challenge start balance (0 = net deposits in history)
input double RG_DailyStopPct     = 4.0;    // Daily stop: % of initial below day-start balance (firm 5)
input double RG_TotalStopPct     = 8.0;    // Total stop: % of initial below initial balance (firm 10)
input double RG_TargetPct        = 0.0;    // Close all and halt at this profit, % of initial (0 = off)
input int    RG_DayResetHour     = 0;      // Server hour the firm's trading day starts
input int    RG_MaxTradesPerDay  = 3;      // Max new entries per day (0 = off)
input int    RG_MaxPositions     = 1;      // Max open positions of this magic
input int    RG_MaxSpreadPoints  = 0;      // Skip entries above this spread, points (0 = off)
input int    RG_FridayCutoff     = 0;      // Friday server hour: flatten, no entries (0 = off)
input bool   RG_CloseAllOnStop   = false;  // On a stop close ALL account positions (false = this magic)
input int    RG_DeviationPoints  = 30;     // Max price deviation for market orders (points)
input bool   RG_TradeLog         = true;   // Write the closed-trade CSV

enum ENUM_RG_HALT
  {
   RG_HALT_NONE   = 0,
   RG_HALT_DAILY  = 1,   // until the next day reset
   RG_HALT_TOTAL  = 2,   // persistent
   RG_HALT_TARGET = 3    // persistent
  };

struct RGEntry               // known only for entries this run placed
  {
   ulong  position;
   int    spread;            // points at send
   double slippage;          // points, positive = worse than requested
   double risk;              // money lost if the SL is hit
  };

struct RGForced
  {
   ulong  position;
   string reason;
  };

#define RG_LOG_HEADER "close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"

CTrade   g_rgTrade;
ulong    g_rgMagic        = 0;
double   g_rgInitial      = 0.0;
datetime g_rgDayStart     = 0;
double   g_rgDayBalance   = 0.0;
int      g_rgHalt         = RG_HALT_NONE;
string   g_rgHaltGV       = "";
string   g_rgLogFile      = "";     // "" = logging off
int      g_rgLogFlags     = 0;
ulong    g_rgLastDeal     = 0;      // newest closing deal already scanned
datetime g_rgLastDealTime = 0;
bool     g_rgLogDirty     = false;
uint     g_rgLastComment  = 0;
uint     g_rgLastFailMsg  = 0;
RGEntry  g_rgEntries[];
RGForced g_rgForced[];

//+------------------------------------------------------------------+
//| Public API                                                       |
//+------------------------------------------------------------------+
bool RG_Init(const ulong magic)
  {
   if(RG_RiskPct <= 0.0 || RG_RiskPct > 5.0 || RG_DailyStopPct <= 0.0 || RG_TotalStopPct <= 0.0 ||
      RG_TotalStopPct >= 100.0 || RG_InitialBalance < 0.0 || RG_TargetPct < 0.0 ||
      RG_DayResetHour < 0 || RG_DayResetHour > 23 || RG_MaxTradesPerDay < 0 || RG_MaxPositions < 1 ||
      RG_MaxSpreadPoints < 0 || RG_FridayCutoff < 0 || RG_FridayCutoff > 23 || RG_DeviationPoints < 0)
     {
      Print("RiskGuard: invalid inputs (risk 0-5%, stops > 0, hours 0-23, max positions >= 1)");
      return(false);
     }
   g_rgMagic = magic;
   g_rgTrade.SetExpertMagicNumber(magic);
   g_rgTrade.SetDeviationInPoints(RG_DeviationPoints);
   g_rgTrade.SetTypeFillingBySymbol(_Symbol);
   g_rgTrade.SetAsyncMode(false);
   g_rgTrade.LogLevel(LOG_LEVEL_ERRORS);

   g_rgInitial = (RG_InitialBalance > 0.0) ? RG_InitialBalance : RG_NetDeposits();
   g_rgHaltGV  = StringFormat("RG_%I64d_%I64u_halt", AccountInfoInteger(ACCOUNT_LOGIN), magic);
   if(MQLInfoInteger(MQL_TESTER))
      GlobalVariableDel(g_rgHaltGV);
   g_rgHalt = GlobalVariableCheck(g_rgHaltGV) ? (int)GlobalVariableGet(g_rgHaltGV) : RG_HALT_NONE;
   g_rgDayStart = 0;
   RG_CheckDay();

   RG_LogInit();
   RG_SyncLog();

   PrintFormat("RiskGuard: risk %.2f%%/trade | initial %.2f | daily stop %.1f%% | total stop %.1f%% (floor %.2f) | "
               "target %s | day reset %02d:00 | max %d trades/day, %d position(s) | spread %s | Friday cut-off %s | log %s",
               RG_RiskPct, g_rgInitial, RG_DailyStopPct, RG_TotalStopPct, RG_TotalFloor(),
               RG_TargetPct > 0.0 ? StringFormat("%.1f%%", RG_TargetPct) : "off", RG_DayResetHour,
               RG_MaxTradesPerDay, RG_MaxPositions,
               RG_MaxSpreadPoints > 0 ? StringFormat("<= %d", RG_MaxSpreadPoints) : "any",
               RG_FridayCutoff > 0 ? StringFormat("%02d:00", RG_FridayCutoff) : "off",
               g_rgLogFile == "" ? "off" : g_rgLogFile);
   if(g_rgHalt == RG_HALT_TOTAL || g_rgHalt == RG_HALT_TARGET)
      PrintFormat("RiskGuard: HALTED (%s) by a previous run -- delete global variable %s (F3) to resume",
                  RG_HaltName(g_rgHalt), g_rgHaltGV);
   return(true);
  }

//--- call first in OnTick: day rollover, stops, Friday cut-off, log sync
void RG_OnTick()
  {
   RG_CheckDay();
   const double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_rgHalt != RG_HALT_TOTAL && g_rgHalt != RG_HALT_TARGET)
     {
      if(eq <= RG_TotalFloor())
         RG_SetHalt(RG_HALT_TOTAL, StringFormat("equity %.2f <= total floor %.2f -- no more trading", eq, RG_TotalFloor()));
      else if(RG_TargetPct > 0.0 && eq >= g_rgInitial * (1.0 + RG_TargetPct / 100.0))
         RG_SetHalt(RG_HALT_TARGET, StringFormat("equity %.2f >= target %.2f -- profit locked, no more trading",
                                                 eq, g_rgInitial * (1.0 + RG_TargetPct / 100.0)));
      else if(g_rgHalt == RG_HALT_NONE && eq <= RG_DailyFloor())
         RG_SetHalt(RG_HALT_DAILY, StringFormat("equity %.2f <= daily floor %.2f -- no entries until %s",
                                                eq, RG_DailyFloor(), TimeToString(g_rgDayStart + 86400)));
     }
   if(g_rgHalt != RG_HALT_NONE)
      RG_ClosePositions(RG_HaltName(g_rgHalt), RG_CloseAllOnStop);
   else if(RG_FridayBlocked())
      RG_ClosePositions("friday_cutoff", false);

   if(g_rgLogDirty)
     {
      g_rgLogDirty = false;
      RG_SyncLog();
     }
   if(!MQLInfoInteger(MQL_TESTER) && GetTickCount() - g_rgLastComment > 1000)
     {
      g_rgLastComment = GetTickCount();
      Comment(RG_Status());
     }
  }

//--- may a new entry be placed now? `why` says what blocks it
bool RG_CanOpen(string &why)
  {
   why = "";
   const long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(g_rgHalt != RG_HALT_NONE)
      why = "halted: " + RG_HaltName(g_rgHalt);
   else if(RG_FridayBlocked())
      why = "Friday cut-off";
   else if(RG_CountPositions() >= RG_MaxPositions)
      why = StringFormat("%d position(s) already open", RG_CountPositions());
   else if(RG_MaxTradesPerDay > 0 && RG_TradesToday() >= RG_MaxTradesPerDay)
      why = StringFormat("max %d trades today reached", RG_MaxTradesPerDay);
   else if(RG_MaxSpreadPoints > 0 && spread > RG_MaxSpreadPoints)
      why = StringFormat("spread %I64d > %d points", spread, RG_MaxSpreadPoints);
   return(why == "");
  }

//--- market entry sized to RG_RiskPct; refuses without a valid SL or without room to the stops
bool RG_Open(const ENUM_ORDER_TYPE type, const double sl, const double tp, const string comment)
  {
   if(type != ORDER_TYPE_BUY && type != ORDER_TYPE_SELL)
     {
      Print("RiskGuard: RG_Open handles market BUY/SELL only");
      return(false);
     }
   const bool isBuy = (type == ORDER_TYPE_BUY);
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return(false);
   const double price  = isBuy ? tick.ask : tick.bid;
   const double point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   const int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   const double slN    = NormalizeDouble(sl, digits);
   const double tpN    = (tp > 0.0) ? NormalizeDouble(tp, digits) : 0.0;
   const double slDist = isBuy ? price - slN : slN - price;
   if(slN <= 0.0 || slDist <= 0.0 || slDist < SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point)
     {
      PrintFormat("RiskGuard: entry refused, SL %s is not a valid stop for a %s at %s",
                  DoubleToString(slN, digits), isBuy ? "buy" : "sell", DoubleToString(price, digits));
      return(false);
     }

   double perLot = 0.0;   // P&L of 1.0 lot if the SL is hit (negative)
   if(!OrderCalcProfit(type, _Symbol, 1.0, price, slN, perLot) || perLot >= 0.0)
     {
      PrintFormat("RiskGuard: entry refused, cannot value the SL distance (error %d)", GetLastError());
      return(false);
     }
   const double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * RG_RiskPct / 100.0;
   const double step      = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   const double minLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0)
      return(false);
   double lots = MathMin(MathFloor(riskMoney / -perLot / step) * step, maxLot);
   if(lots < minLot)     // never round up past the configured risk
     {
      PrintFormat("RiskGuard: entry skipped, risk %.2f buys less than the minimum lot %.2f", riskMoney, minLot);
      return(false);
     }
   lots = NormalizeDouble(lots, (int)MathRound(-MathLog10(step)));

   double margin = 0.0;
   if(!OrderCalcMargin(type, _Symbol, lots, price, margin) || margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.95)
     {
      PrintFormat("RiskGuard: entry skipped, %.2f lots need margin %.2f > free %.2f",
                  lots, margin, AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      return(false);
     }

//--- worst case: this SL plus every open SL of this magic is hit -- still above both floors?
   const double tradeRisk = lots * -perLot;
   const double openRisk  = RG_OpenRisk();
   if(openRisk < 0.0)
     {
      Print("RiskGuard: entry refused, an open position of this magic has no SL");
      return(false);
     }
   const double eq         = AccountInfoDouble(ACCOUNT_EQUITY);
   const double dailyRoom  = eq - RG_DailyFloor() - openRisk;
   const double totalRoom  = eq - RG_TotalFloor() - openRisk;
   if(tradeRisk > MathMin(dailyRoom, totalRoom))
     {
      PrintFormat("RiskGuard: entry refused, trade risk %.2f (+ open %.2f) exceeds room %.2f to the %s stop",
                  tradeRisk, openRisk, MathMin(dailyRoom, totalRoom), dailyRoom < totalRoom ? "daily" : "total");
      return(false);
     }

   const int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(!g_rgTrade.PositionOpen(_Symbol, type, lots, price, slN, tpN, comment) ||
      (g_rgTrade.ResultRetcode() != TRADE_RETCODE_DONE && g_rgTrade.ResultRetcode() != TRADE_RETCODE_PLACED))
     {
      PrintFormat("RiskGuard: %s %.2f failed: %d %s", isBuy ? "buy" : "sell", lots,
                  g_rgTrade.ResultRetcode(), g_rgTrade.ResultRetcodeDescription());
      return(false);
     }
   const double fill = g_rgTrade.ResultPrice();
   const int    n    = ArraySize(g_rgEntries);
   ArrayResize(g_rgEntries, n + 1, 64);
   g_rgEntries[n].position = g_rgTrade.ResultOrder();   // position id = opening order ticket
   g_rgEntries[n].spread   = spread;
   g_rgEntries[n].slippage = (fill > 0.0) ? (isBuy ? fill - price : price - fill) / point : 0.0;
   g_rgEntries[n].risk     = tradeRisk;
   return(true);
  }

void RG_OnTradeTransaction(const MqlTradeTransaction &trans)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
      g_rgLogDirty = true;     // synced on the next tick, once balance/history are consistent
  }

void RG_Deinit()
  {
   RG_SyncLog();
   if(!MQLInfoInteger(MQL_TESTER))
      Comment("");
  }

string RG_Status()
  {
   const double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   return(StringFormat("RiskGuard %s | equity %.2f | daily floor %.2f (room %.2f) | total floor %.2f (room %.2f) | trades today %d",
                       g_rgHalt == RG_HALT_NONE ? "OK" : "HALTED " + RG_HaltName(g_rgHalt), eq,
                       RG_DailyFloor(), eq - RG_DailyFloor(), RG_TotalFloor(), eq - RG_TotalFloor(), RG_TradesToday()));
  }

//+------------------------------------------------------------------+
//| Limits                                                           |
//+------------------------------------------------------------------+
double RG_DailyFloor() { return(g_rgDayBalance - g_rgInitial * RG_DailyStopPct / 100.0); }
double RG_TotalFloor() { return(g_rgInitial * (1.0 - RG_TotalStopPct / 100.0)); }

string RG_HaltName(const int halt)
  {
   switch(halt)
     {
      case RG_HALT_DAILY:  return("daily_stop");
      case RG_HALT_TOTAL:  return("total_stop");
      case RG_HALT_TARGET: return("target_reached");
     }
   return("none");
  }

void RG_SetHalt(const int halt, const string why)
  {
   g_rgHalt = halt;
   if(halt == RG_HALT_TOTAL || halt == RG_HALT_TARGET)
      GlobalVariableSet(g_rgHaltGV, halt);
   PrintFormat("RiskGuard %s: %s", RG_HaltName(halt), why);
  }

//--- start of the firm's trading day containing t
datetime RG_DayStartOf(const datetime t)
  {
   const long shift = (long)RG_DayResetHour * 3600;
   const long s     = (long)t - shift;
   return((datetime)(s - s % 86400 + shift));
  }

void RG_CheckDay()
  {
   const datetime ds = RG_DayStartOf(TimeCurrent());
   if(ds == g_rgDayStart)
      return;
   g_rgDayStart   = ds;
   g_rgDayBalance = RG_BalanceAt(ds);
   if(g_rgHalt == RG_HALT_DAILY)
     {
      g_rgHalt = RG_HALT_NONE;
      PrintFormat("RiskGuard: new day %s, daily stop cleared", TimeToString(ds));
     }
  }

bool RG_FridayBlocked()
  {
   if(RG_FridayCutoff <= 0)
      return(false);
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   return(t.day_of_week == 5 && t.hour >= RG_FridayCutoff);
  }

//+------------------------------------------------------------------+
//| Account / position helpers                                       |
//+------------------------------------------------------------------+
double RG_DealPnl(const ulong deal)
  {
   return(HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP) +
          HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE));
  }

//--- balance at time t: current balance minus trading P&L booked since (deposits/credit excluded)
double RG_BalanceAt(const datetime t)
  {
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(!HistorySelect(t, TimeCurrent() + 86400))
      return(bal);
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong d = HistoryDealGetTicket(i);
      const long  k = HistoryDealGetInteger(d, DEAL_TYPE);
      if(d != 0 && k != DEAL_TYPE_BALANCE && k != DEAL_TYPE_CREDIT)
         bal -= RG_DealPnl(d);
     }
   return(bal);
  }

double RG_NetDeposits()
  {
   double sum = 0.0;
   if(HistorySelect(0, TimeCurrent() + 86400))
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         const ulong d = HistoryDealGetTicket(i);
         if(d != 0 && HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BALANCE)
            sum += HistoryDealGetDouble(d, DEAL_PROFIT);
        }
   return(sum > 0.0 ? sum : AccountInfoDouble(ACCOUNT_BALANCE));
  }

int RG_CountPositions()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) != 0 && PositionGetInteger(POSITION_MAGIC) == (long)g_rgMagic)
         n++;
   return(n);
  }

int RG_TradesToday()
  {
   int n = 0;
   if(HistorySelect(g_rgDayStart, TimeCurrent() + 86400))
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         const ulong d = HistoryDealGetTicket(i);
         if(d != 0 && HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN &&
            HistoryDealGetInteger(d, DEAL_MAGIC) == (long)g_rgMagic)
            n++;
        }
   return(n);
  }

//--- money still at risk if every open SL of this magic is hit (-1 if one has no SL)
double RG_OpenRisk()
  {
   double risk = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetTicket(i) == 0 || PositionGetInteger(POSITION_MAGIC) != (long)g_rgMagic)
         continue;
      const double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0.0)
         return(-1.0);
      const ENUM_ORDER_TYPE t = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double pnl = 0.0;
      if(OrderCalcProfit(t, PositionGetString(POSITION_SYMBOL), PositionGetDouble(POSITION_VOLUME),
                         PositionGetDouble(POSITION_PRICE_CURRENT), sl, pnl) && pnl < 0.0)
         risk -= pnl;
     }
   return(risk);
  }

int RG_ClosePositions(const string reason, const bool allAccount)
  {
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || (!allAccount && PositionGetInteger(POSITION_MAGIC) != (long)g_rgMagic))
         continue;
      const ulong pos = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(g_rgTrade.PositionClose(ticket))
        {
         const int n = ArraySize(g_rgForced);
         ArrayResize(g_rgForced, n + 1, 16);
         g_rgForced[n].position = pos;
         g_rgForced[n].reason   = reason;
         closed++;
        }
      else if(GetTickCount() - g_rgLastFailMsg > 60000)
        {
         g_rgLastFailMsg = GetTickCount();
         PrintFormat("RiskGuard: close #%I64u (%s) failed: %d %s -- retrying every tick",
                     ticket, reason, g_rgTrade.ResultRetcode(), g_rgTrade.ResultRetcodeDescription());
        }
     }
   if(closed > 0)
      PrintFormat("RiskGuard: closed %d position(s) -- %s", closed, reason);
   return(closed);
  }

//+------------------------------------------------------------------+
//| Trade log                                                        |
//+------------------------------------------------------------------+
void RG_LogInit()
  {
   g_rgLogFile = "";
   if(!RG_TradeLog || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   const bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   g_rgLogFlags = tester ? FILE_COMMON : 0;
   const string name = StringFormat("%s_%s_%I64u%s_trades.csv", MQLInfoString(MQL_PROGRAM_NAME), _Symbol,
                                    g_rgMagic, tester ? "_tester" : "");
   if(tester)
      FileDelete(name, g_rgLogFlags);
   else
      RG_LogResume(name);
   const int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_rgLogFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("RiskGuard: cannot open trade log %s, error %d -- logging off", name, GetLastError());
      return;
     }
   if(FileSize(h) == 0)
      FileWriteString(h, RG_LOG_HEADER);
   FileClose(h);
   g_rgLogFile = name;
  }

//--- continue after the newest deal already in an existing log
void RG_LogResume(const string name)
  {
   if(!FileIsExist(name, g_rgLogFlags))
      return;
   const int h = FileOpen(name, FILE_READ | FILE_TXT | FILE_ANSI | g_rgLogFlags);
   if(h == INVALID_HANDLE)
      return;
   const string header = FileReadString(h);
   string last = "";
   while(!FileIsEnding(h))
     {
      const string s = FileReadString(h);
      if(s != "")
         last = s;
     }
   FileClose(h);
   string cols[], vals[];
   if(StringSplit(header, ',', cols) < 2 || StringSplit(last, ',', vals) != ArraySize(cols))
      return;
   for(int i = 0; i < ArraySize(cols); i++)
     {
      if(cols[i] == "deal")
         g_rgLastDeal = (ulong)StringToInteger(vals[i]);
      else if(cols[i] == "close_time")
         g_rgLastDealTime = StringToTime(vals[i]);
     }
  }

//--- append rows for closing deals newer than the last one scanned
void RG_SyncLog()
  {
   if(g_rgLogFile == "" || !HistorySelect(g_rgLastDealTime, TimeCurrent() + 86400))
      return;
//--- walk back from the current balance to get each closing deal's balance-after
   ulong  deals[];
   double balAfter[];
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   int    n   = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      const ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      const long k = HistoryDealGetInteger(d, DEAL_TYPE);
      const long e = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(d > g_rgLastDeal && (k == DEAL_TYPE_BUY || k == DEAL_TYPE_SELL) &&
         (e == DEAL_ENTRY_OUT || e == DEAL_ENTRY_INOUT || e == DEAL_ENTRY_OUT_BY))
        {
         ArrayResize(deals, n + 1, 16);
         ArrayResize(balAfter, n + 1, 16);
         deals[n]    = d;
         balAfter[n] = bal;
         n++;
        }
      if(k != DEAL_TYPE_CREDIT)
         bal -= RG_DealPnl(d);
     }
   for(int j = n - 1; j >= 0; j--)   // oldest first
      RG_LogDeal(deals[j], balAfter[j]);
  }

void RG_LogDeal(const ulong deal, const double balAfter)
  {
   if(!HistoryDealSelect(deal))
      return;
   const datetime closeTime = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   const ulong    pos       = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if(deal > g_rgLastDeal)
      g_rgLastDeal = deal;
   if(closeTime > g_rgLastDealTime)
      g_rgLastDealTime = closeTime;

   const string symbol = HistoryDealGetString(deal, DEAL_SYMBOL);
   const bool   wasBuy = (HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_SELL);   // a sell closes a buy
   const double vol    = HistoryDealGetDouble(deal, DEAL_VOLUME);
   const double px     = HistoryDealGetDouble(deal, DEAL_PRICE);
   const double gross  = HistoryDealGetDouble(deal, DEAL_PROFIT);
   const double swap   = HistoryDealGetDouble(deal, DEAL_SWAP);
   const double outFee = HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
   const double sl     = HistoryDealGetDouble(deal, DEAL_SL);
   const double tp     = HistoryDealGetDouble(deal, DEAL_TP);
   const long   reason = HistoryDealGetInteger(deal, DEAL_REASON);

//--- entry side of the position: only log positions this magic opened
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
   if(inMagic != (long)g_rgMagic || inVol <= 0.0)
      return;

   const double share = MathMin(vol / inVol, 1.0);      // partial closes carry their share of entry costs
   const double comm  = outFee + inFee * share;
   const double net   = gross + swap + comm;
   const int    dg    = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   string exitReason = RG_ReasonName(reason), spread = "", slip = "", rMult = "";
   for(int i = ArraySize(g_rgForced) - 1; i >= 0; i--)
      if(g_rgForced[i].position == pos)
        {
         exitReason = g_rgForced[i].reason;
         break;
        }
   for(int i = ArraySize(g_rgEntries) - 1; i >= 0; i--)
      if(g_rgEntries[i].position == pos)
        {
         spread = IntegerToString(g_rgEntries[i].spread);
         slip   = DoubleToString(g_rgEntries[i].slippage, 1);
         if(g_rgEntries[i].risk > 0.0)
            rMult = DoubleToString(net / (g_rgEntries[i].risk * share), 3);
         break;
        }

   const int h = FileOpen(g_rgLogFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_rgLogFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("RiskGuard: trade log write failed, error %d", GetLastError());
      return;
     }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, StringFormat("%s,%s,%s,%s,%.2f,%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%s,%s,%s,%s,%I64u,%I64u,%I64u\n",
                   TimeToString(closeTime, TIME_DATE | TIME_SECONDS), TimeToString(openTime, TIME_DATE | TIME_SECONDS),
                   symbol, wasBuy ? "buy" : "sell", vol, DoubleToString(inPxVol / inVol, dg), DoubleToString(px, dg),
                   DoubleToString(sl, dg), DoubleToString(tp, dg), gross, comm, swap, net, balAfter,
                   rMult, exitReason, spread, slip, deal, pos, g_rgMagic));
   FileClose(h);
  }

string RG_ReasonName(const long reason)
  {
   switch((int)reason)
     {
      case DEAL_REASON_SL:     return("sl");
      case DEAL_REASON_TP:     return("tp");
      case DEAL_REASON_SO:     return("stop_out");
      case DEAL_REASON_EXPERT: return("expert");
      case DEAL_REASON_CLIENT: return(MQLInfoInteger(MQL_TESTER) ? "end_of_test" : "manual");   // tester's final close
      case DEAL_REASON_MOBILE:
      case DEAL_REASON_WEB:    return("manual");
     }
   return("other");
  }
//+------------------------------------------------------------------+
