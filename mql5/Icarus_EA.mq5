//+------------------------------------------------------------------+
//|                                                    Icarus_EA.mq5 |
//|  MQL5 port of "Icarus 2.2" (lifesdream.org; MaPi, HoHe, fxtrue,  |
//|  FX1079; based on Super Money Grid v1.41): a no-indicator grid.  |
//|  A buy grid and a sell grid run independently and both start at  |
//|  launch. Each side adds an averaging leg when the money loss of  |
//|  its largest leg passes grid_size x gap(n) pips (gaps and lots   |
//|  grow by their progressions) and closes the whole side once its  |
//|  profit exceeded take_profit and then fell back to profit_lock x |
//|  peak. No stop loss per leg. Hedging account only.               |
//|  Trading logic only: no panel, lines, buttons or persistence.    |
//|  Differences from the MQ4 (docs/superpowers/specs/               |
//|  2026-10-06-icarus-prereg.md): red alert (equity drawdown from   |
//|  peak > equity_warning) blocks adds and new cycles as the        |
//|  published rules say; account_risk stops the grid in the tick it |
//|  trips; a side that added a leg skips its lock check that tick;  |
//|  lock state resets whenever a side is flat; lots are normalised  |
//|  to the volume step; sort ties go by ticket.                     |
//|  Execution, spread guard and trade log from EACore.mqh.          |
//|                                                                  |
//|  v1.00                                                           |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "Icarus 2.2 grid (MQL5 port): hedged buy and sell grids, progressive gaps and lots, basket trailing lock"

#define EA_NAME              "Icarus_EA"
#define EA_VERSION           "1.00"
#define EA_MAGIC             261009
#define EA_COMMENT           "ICARUS"
#define EA_SPREAD_WAIT_MIN   0            // a grid leg is a price event: a delayed retry would be a different leg
#define EA_MAX_SPREAD_POINTS 100          // max_spread 100 MT4 points
#define EA_DAILY_LOSS_PCT    0            // EACore's stops close legs the grid would reopen next tick; the kill switch is InpAccountRisk
#define EA_MONTHLY_LOSS_PCT  0
#include "EACore.mqh"
#define MAX_LEGS 100                      // max_open_positions in the MQ4

enum ENUM_PROGRESSION
  {
   PROG_FLAT       = 0,  // 0: all the same
   PROG_DALEMBERT  = 1,  // 1: 1/2/3/4/5 (D'Alembert)
   PROG_MARTINGALE = 2,  // 2: 1/2/4/8/16 (Martingale)
   PROG_FIBONACCI  = 3   // 3: 1/1/2/3/5 (Fibonacci)
  };

input group "=== Icarus grid ==="
input int              InpGridPips         = 20;             // grid_size: distance between legs, pips
input ENUM_PROGRESSION InpGridProgression  = PROG_FIBONACCI; // gs_progression: how the distance grows
input int              InpTakeProfitPips   = 20;             // take_profit: pips on the reference leg, arms the trailing lock
input double           InpProfitLock       = 0.3;            // profit_lock: share of the peak profit the lock keeps
input double           InpMinLots          = 0.01;           // min_lots: first leg of a cycle
input double           InpEquityWarning    = 0.20;           // equity_warning: drawdown from peak equity that blocks new legs (0 = off)
input double           InpAccountRisk      = 1.00;           // account_risk: equity below (1 - risk) x balance closes all and stops (1 = off)
input ENUM_PROGRESSION InpLotProgression   = PROG_FIBONACCI; // progression: how the lots grow within a cycle
input int              InpMaxPositions     = 6;              // max_positions per cycle (0 = 100)
input bool             InpUnbalanceControl = false;          // unbalance_control: a new cycle starts with the other side's 3rd-largest lot

struct Leg  { ulong ticket; ulong id; double lots; double profit; };
struct Side
  {
   bool   buy;
   int    n;
   Leg    leg[];          // ascending by lots, then by ticket: leg[n-1] is the largest (newest) leg
   double total;          // money profit of the side incl. swap and commission
   double maxProfit;      // peak profit since the lock armed (0 = not armed)
   double closeProfit;    // the lock: close the side when total falls below it
  };
struct CommEntry { ulong id; double comm; };

Side      g_buy, g_sell;
CommEntry g_comm[];                       // entry-deal commission per position id (ponytail: never pruned, 16 bytes a leg)
bool      g_stopAll = false, g_red = false;
double    g_peakEquity = 0.0, g_tickValue = 0.0, g_lotStep = 0.01, g_lotMin = 0.01, g_lotMax = 100.0;
int       g_lotDigits = 2, g_maxPositions = 6;
uint      g_lastLotMsg = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpGridPips <= 0 || InpTakeProfitPips <= 0 || InpProfitLock <= 0.0 || InpProfitLock >= 1.0 || InpMinLots <= 0.0 ||
      InpEquityWarning < 0.0 || InpEquityWarning > 1.0 || InpAccountRisk < 0.0 || InpAccountRisk > 1.0 ||
      InpMaxPositions < 0 || InpMaxPositions > MAX_LEGS)
     {
      Print("Invalid Icarus inputs");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("Icarus needs a hedging account: it holds a buy grid and a sell grid at the same time");
      return(INIT_FAILED);
     }
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);
   const int digits = g_symbol.Digits();
   g_lotStep = g_symbol.LotsStep();
   g_lotMin  = g_symbol.LotsMin();
   g_lotMax  = g_symbol.LotsMax();
   if(digits < 2 || digits > 5 || g_lotStep <= 0.0 || g_lotMin <= 0.0 || g_lotMax <= 0.0)
     {
      PrintFormat("Unsupported symbol settings: digits %d (pip needs 2-5), lot step %.4f", digits, g_lotStep);
      return(INIT_FAILED);
     }
   g_lotDigits    = (int)MathRound(-MathLog10(g_lotStep));
   g_maxPositions = (InpMaxPositions == 0) ? MAX_LEGS : InpMaxPositions;
   g_tickValue    = g_symbol.TickValue();
   g_buy.buy  = true;
   g_sell.buy = false;
   PrintFormat("%s v%s started on %s, magic %I64u, own positions: %d", EA_NAME, EA_VERSION, _Symbol, InpMagic, CountOwnPositions());
   PrintFormat("Inputs: grid %d pips, gaps %s | take profit %d pips, lock %.2f | min lots %.2f, lots %s, max %d legs | equity warning %s | account risk %s | unbalance %s | pip value %.2f per lot",
               InpGridPips, EnumToString(InpGridProgression), InpTakeProfitPips, InpProfitLock, InpMinLots,
               EnumToString(InpLotProgression), g_maxPositions,
               (InpEquityWarning > 0.0 && InpEquityWarning < 1.0) ? StringFormat("%.0f%%", 100.0 * InpEquityWarning) : "off",
               (InpAccountRisk > 0.0 && InpAccountRisk < 1.0) ? StringFormat("%.0f%%", 100.0 * InpAccountRisk) : "off",
               InpUnbalanceControl ? "on" : "off", PipValue(1.0));
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { Core_Deinit(reason); }

//+------------------------------------------------------------------+
//| Arithmetic (MQ4 names in comments)                               |
//+------------------------------------------------------------------+
double PipValue(const double lots)                     // CalculatePipValue: tick value x 10 on 3/5-digit quotes
  {
   const int d = g_symbol.Digits();
   return(g_tickValue * ((d == 3 || d == 5) ? 10.0 : 1.0) * lots);
  }

double TakeProfit(const double lots) { return(InpTakeProfitPips * PipValue(lots)); }   // CalculateTP

int Fib(const int i)                                   // CalculateFibonacci: 1, 1, 2, 3, 5, 8 ... for i = 1, 2, 3 ...
  {
   int a = 1, b = 1;
   for(int k = 3; k <= i; k++) { const int c = a + b; a = b; b = c; }
   return(b);
  }

double GapFactor(const int n)                          // CalculateVolume(n) / min_lots: grid multiple before leg n+1
  {
   if(n <= 1)
      return(1.0);
   switch(InpGridProgression)
     {
      case PROG_DALEMBERT:  return(n);
      case PROG_MARTINGALE: return(MathPow(2.0, n - 1));
      case PROG_FIBONACCI:  return(Fib(n));
     }
   return(1.0);
  }

double NextLots(const Side &s)                         // NewIkarusOrder: lots of leg n+1
  {
   const double first = s.leg[0].lots, last = s.leg[s.n - 1].lots;
   switch(InpLotProgression)
     {
      case PROG_DALEMBERT:  return(last + first);
      case PROG_MARTINGALE: return(2.0 * last);
      case PROG_FIBONACCI:  return(Fib(s.n + 1) * first);
     }
   return(first);
  }

double Target(const Side &s)                           // the money profit that arms the lock
  {
   if(s.n <= 1 || InpLotProgression == PROG_FLAT)
      return(TakeProfit(s.leg[0].lots));
   if(InpLotProgression == PROG_DALEMBERT)
      return(s.n * TakeProfit(s.leg[0].lots));
   return(TakeProfit(s.leg[s.n - 1].lots));
  }

double NormLots(const double lots) { return(NormalizeDouble(MathRound(lots / g_lotStep) * g_lotStep, g_lotDigits)); }

//+------------------------------------------------------------------+
//| Commission of the entry deal, cached per position id             |
//+------------------------------------------------------------------+
void RememberCommission(const ulong id, const double comm)
  {
   for(int i = ArraySize(g_comm) - 1; i >= 0; i--)
      if(g_comm[i].id == id) { g_comm[i].comm = comm; return; }
   const int n = ArraySize(g_comm);
   ArrayResize(g_comm, n + 1, 64);
   g_comm[n].id   = id;
   g_comm[n].comm = comm;
  }

double EntryCommission(const ulong id)
  {
   for(int i = ArraySize(g_comm) - 1; i >= 0; i--)
      if(g_comm[i].id == id)
         return(g_comm[i].comm);
   double comm = 0.0;                                  // first look at a leg whose deal event has not arrived yet
   if(HistorySelectByPosition(id))
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         const ulong d = HistoryDealGetTicket(i);
         if(d != 0 && HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
            comm += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
        }
   RememberCommission(id, comm);
   return(comm);
  }

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
void Snapshot(Side &s)                                 // UpdateVars + SortByLots for one side
  {
   const long want = s.buy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   s.n = 0;
   s.total = 0.0;
   ArrayResize(s.leg, 0, MAX_LEGS);
   for(int i = 0; i < PositionsTotal(); i++)
     {
      const ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOwnPosition() || PositionGetInteger(POSITION_TYPE) != want)
         continue;
      Leg L;
      L.ticket = t;
      L.id     = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      L.lots   = PositionGetDouble(POSITION_VOLUME);
      L.profit = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + 2.0 * EntryCommission(L.id);
      ArrayResize(s.leg, s.n + 1, MAX_LEGS);
      int k = s.n;                                     // insertion sort: lots ascending, equal lots by ticket ascending
      while(k > 0 && (s.leg[k - 1].lots > L.lots + g_lotStep / 2.0 ||
                      (MathAbs(s.leg[k - 1].lots - L.lots) <= g_lotStep / 2.0 && s.leg[k - 1].ticket > L.ticket)))
        {
         s.leg[k] = s.leg[k - 1];
         k--;
        }
      s.leg[k] = L;
      s.n++;
      s.total += L.profit;
     }
   if(s.n == 0)                                        // flat by any route: the lock starts over
     {
      s.maxProfit   = 0.0;
      s.closeProfit = 0.0;
     }
  }

bool Send(const Side &s, const double lots, const string what, const double gapPips, const double trigger, const double profit)
  {
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
      return(false);
   const double v = NormLots(lots);
   if(v < g_lotMin - 1e-9 || v > g_lotMax + 1e-9)
     {
      if(GetTickCount() - g_lastLotMsg > 60000)
        {
         g_lastLotMsg = GetTickCount();
         PrintFormat("%s %s skipped: %.2f lots outside %.2f-%.2f -- retrying every tick", what, s.buy ? "BUY" : "SELL", v, g_lotMin, g_lotMax);
        }
      return(false);
     }
   const bool ok = s.buy ? g_trade.Buy(v, _Symbol, 0.0, 0.0, 0.0, InpComment)
                         : g_trade.Sell(v, _Symbol, 0.0, 0.0, 0.0, InpComment);
   const uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s %s failed: %d %s", what, s.buy ? "BUY" : "SELL", rc, g_trade.ResultRetcodeDescription());
      return(false);
     }
   PrintFormat("ICARUS %s %s n=%d lots=%.2f gap=%.0f trigger=%.2f profit=%.2f", what, s.buy ? "BUY" : "SELL", s.n + 1, v, gapPips, trigger, profit);
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

//+------------------------------------------------------------------+
//| Robot() for one side                                             |
//+------------------------------------------------------------------+
void RunSide(Side &s, const Side &other, const bool red)
  {
   if(s.n == 0)                                        // new cycle
     {
      if(red)
         return;
      double lots = InpMinLots;                        // CalculateStartingVolume
      if(InpUnbalanceControl && other.n >= 4)
         lots = other.leg[other.n - 3].lots;           // the other side's 3rd-largest leg
      Send(s, MathMin(MathMax(lots, g_lotMin), g_lotMax), "OPEN", 0.0, 0.0, 0.0);
      return;
     }
   if(s.n < g_maxPositions && !red)                    // add the next leg
     {
      const Leg    last    = s.leg[s.n - 1];
      const double gapPips = GapFactor(s.n) * InpGridPips;
      const double trigger = -(gapPips * PipValue(last.lots));   // CalculateSL(lots[n-1], n)
      if(last.profit <= trigger && Send(s, NextLots(s), "ADD", gapPips, trigger, last.profit))
         return;                                       // the snapshot is stale now; judge the lock next tick
     }
   const double target = Target(s);
   if(s.maxProfit == 0.0 && s.total > target)          // arm
     {
      s.maxProfit   = s.total;
      s.closeProfit = InpProfitLock * s.total;
     }
   if(s.maxProfit > 0.0 && s.total > s.maxProfit)      // trail
     {
      s.maxProfit   = s.total;
      s.closeProfit = InpProfitLock * s.total;
     }
   if(s.maxProfit > 0.0 && s.closeProfit > 0.0 && s.maxProfit > s.closeProfit && s.total < s.closeProfit)
     {
      PrintFormat("ICARUS CLOSE %s n=%d total=%.2f target=%.2f max=%.2f lock=%.2f", s.buy ? "BUY" : "SELL", s.n, s.total, target, s.maxProfit, s.closeProfit);
      for(int i = 0; i < s.n; i++)                     // smallest lot first, as the MQ4
         Core_ClosePosition(s.leg[i].ticket, "basket_tp");
      s.maxProfit   = 0.0;
      s.closeProfit = 0.0;
     }
  }

void OnTick()
  {
   Core_OnTickStart();
   if(!g_symbol.RefreshRates())
      return;
   g_tickValue = g_symbol.TickValue();
   const double equity = AccountInfoDouble(ACCOUNT_EQUITY), balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(equity > g_peakEquity)
      g_peakEquity = equity;
   if(!g_stopAll && InpAccountRisk > 0.0 && InpAccountRisk < 1.0 && (1.0 - InpAccountRisk) * balance > equity)
     {
      g_stopAll = true;
      PrintFormat("ICARUS STOP_ALL equity=%.2f floor=%.2f balance=%.2f -- closing everything, no more trading", equity, (1.0 - InpAccountRisk) * balance, balance);
     }
   if(g_stopAll)
     {
      FlattenOwn("account_risk");
      return;
     }
   Snapshot(g_buy);
   Snapshot(g_sell);
   const bool red = InpEquityWarning > 0.0 && InpEquityWarning < 1.0 && equity < (1.0 - InpEquityWarning) * g_peakEquity;
   if(red != g_red)
     {
      g_red = red;
      PrintFormat("ICARUS %s equity=%.2f peak=%.2f", red ? "RED" : "GREEN", equity, g_peakEquity);
     }
   RunSide(g_buy, g_sell, red);
   RunSide(g_sell, g_buy, red);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal != 0 && HistoryDealSelect(trans.deal) &&
      HistoryDealGetInteger(trans.deal, DEAL_ENTRY) == DEAL_ENTRY_IN && HistoryDealGetInteger(trans.deal, DEAL_MAGIC) == (long)InpMagic)
      RememberCommission((ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID),
                         HistoryDealGetDouble(trans.deal, DEAL_COMMISSION) + HistoryDealGetDouble(trans.deal, DEAL_FEE));
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
