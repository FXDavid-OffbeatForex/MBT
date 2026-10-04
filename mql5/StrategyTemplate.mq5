//+------------------------------------------------------------------+
//|                                             StrategyTemplate.mq5 |
//|  Starting point for a new strategy that must live under          |
//|  prop-firm rules. All risk handling, sizing and the trade log    |
//|  live in RiskGuard.mqh; a new strategy only edits the            |
//|  "Strategy" section below: GetSignal() and the SL/TP placement.  |
//|                                                                  |
//|  v1.00                                                           |
//|  Placeholder signal: fast/slow EMA cross on closed bars,         |
//|  SL = ATR x mult, TP = SL x reward:risk. It is NOT meant to be   |
//|  profitable -- it exists to prove the pipeline end to end        |
//|  (tester -> trade CSV -> scripts/prop_mc.py).                    |
//|                                                                  |
//|  - Signals evaluated once per closed bar of the chosen TF        |
//|  - Every order carries a hard SL; lots from RiskGuard risk %     |
//|  - Own magic number; RiskGuard closes only this magic unless     |
//|    RG_CloseAllOnStop is set                                      |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "Strategy template: placeholder EMA cross + RiskGuard prop-firm limits and trade log"

#include <RiskGuard.mqh>

input group "=== Strategy ==="
input ulong           InpMagic       = 260101;      // Magic number
input ENUM_TIMEFRAMES InpTimeframe   = PERIOD_H1;   // Signal timeframe
input string          InpComment     = "TPL";       // Order comment
input int             InpFastEMA     = 20;          // Fast EMA period
input int             InpSlowEMA     = 50;          // Slow EMA period
input int             InpATRPeriod   = 14;          // ATR period
input double          InpSLATR       = 2.0;         // SL distance (x ATR)
input double          InpRewardRisk  = 2.0;         // TP distance (x SL distance, 0 = no TP)
input bool            InpLogEveryBar = false;       // Log a status line on every new signal bar

int      g_fastHandle  = INVALID_HANDLE;
int      g_slowHandle  = INVALID_HANDLE;
int      g_atrHandle   = INVALID_HANDLE;
datetime g_lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpFastEMA <= 0 || InpSlowEMA <= InpFastEMA || InpATRPeriod <= 0 || InpSLATR <= 0.0 || InpRewardRisk < 0.0)
     {
      Print("Invalid inputs: need 0 < fast < slow, ATR period > 0, SL ATR > 0, reward:risk >= 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   g_fastHandle = iMA(_Symbol, InpTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   g_slowHandle = iMA(_Symbol, InpTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   g_atrHandle  = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   if(g_fastHandle == INVALID_HANDLE || g_slowHandle == INVALID_HANDLE || g_atrHandle == INVALID_HANDLE)
     {
      Print("Failed to create indicator handles, error ", GetLastError());
      return(INIT_FAILED);
     }
   if(!RG_Init(InpMagic))
      return(INIT_PARAMETERS_INCORRECT);

   PrintFormat("StrategyTemplate v1.00 on %s %s, magic %I64u | EMA %d/%d | SL %.2fxATR(%d) | RR %.2f",
               _Symbol, EnumToString(InpTimeframe), InpMagic, InpFastEMA, InpSlowEMA, InpSLATR, InpATRPeriod, InpRewardRisk);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   RG_Deinit();
   ReleaseHandle(g_fastHandle);
   ReleaseHandle(g_slowHandle);
   ReleaseHandle(g_atrHandle);
  }

void ReleaseHandle(int &handle)
  {
   if(handle != INVALID_HANDLE)
     {
      IndicatorRelease(handle);
      handle = INVALID_HANDLE;
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   RG_OnTick();     // stops, day reset, Friday cut-off, trade log -- every tick

   datetime barTime = iTime(_Symbol, InpTimeframe, 0);
   if(barTime == 0 || barTime == g_lastBarTime)
      return;
   g_lastBarTime = barTime;

   const int signal = GetSignal();
   if(InpLogEveryBar)
      PrintFormat("Bar %s | signal %+d | %s", TimeToString(barTime, TIME_DATE | TIME_MINUTES), signal, RG_Status());
   if(signal == 0)
      return;

   string why;
   if(!RG_CanOpen(why))
     {
      PrintFormat("%s signal skipped: %s", signal > 0 ? "Buy" : "Sell", why);
      return;
     }

   double atr[1];
   if(CopyBuffer(g_atrHandle, 0, 1, 1, atr) < 1 || atr[0] <= 0.0)
      return;
   const bool   isBuy  = (signal > 0);
   const double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double slDist = atr[0] * InpSLATR;
   const double sl     = isBuy ? price - slDist : price + slDist;
   const double tp     = (InpRewardRisk > 0.0) ? (isBuy ? price + slDist * InpRewardRisk : price - slDist * InpRewardRisk) : 0.0;
   RG_Open(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sl, tp, InpComment);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   RG_OnTradeTransaction(trans);
  }

//+------------------------------------------------------------------+
//| Strategy: +1 buy, -1 sell, 0 nothing (closed bars only)          |
//+------------------------------------------------------------------+
int GetSignal()
  {
   double fast[], slow[];     // dynamic: series flag doesn't apply to fixed-size arrays
   ArraySetAsSeries(fast, true);
   ArraySetAsSeries(slow, true);
   if(CopyBuffer(g_fastHandle, 0, 1, 2, fast) < 2 || CopyBuffer(g_slowHandle, 0, 1, 2, slow) < 2)
      return(0);
   if(fast[0] > slow[0] && fast[1] <= slow[1])
      return(+1);
   if(fast[0] < slow[0] && fast[1] >= slow[1])
      return(-1);
   return(0);
  }
//+------------------------------------------------------------------+
