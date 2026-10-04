//+------------------------------------------------------------------+
//|                                                   MeanRev_EA.mq5 |
//|  H1 XAUUSD mean reversion, companion to MACD_Cross_EA (same      |
//|  account, one DARWIN). Spec: docs/superpowers/specs/             |
//|  2026-10-04-meanrev-ea-design.md                                 |
//|                                                                  |
//|  v1.00                                                           |
//|  - BB_FADE: close beyond Bollinger band + RSI(14) stretched +    |
//|    ADX(14) below max (no strong trend); target = middle band     |
//|  - RSI2_PULLBACK: RSI(2) dip in the EMA trend; exit on RSI(2)    |
//|    rebound                                                       |
//|  - hard stop = SL x ATR(14), time limit = max H1 candles held    |
//|  - execution, guards and trade log from EACore.mqh (v1.42 core)  |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "Mean-reversion EA (BB fade / RSI2 pullback) with MACD_Cross_EA v1.42 safety core"

#define EA_NAME    "MeanRev_EA"
#define EA_VERSION "1.00"
#define EA_MAGIC   240819
#define EA_COMMENT "MREV"
#include "EACore.mqh"

enum ENUM_MR_MODE
  {
   MR_BB_FADE       = 0,  // Bollinger fade in quiet markets
   MR_RSI2_PULLBACK = 1   // RSI(2) pullback in the trend
  };

input group "=== Strategy ==="
input ENUM_MR_MODE        InpEntryMode       = MR_BB_FADE;  // Entry mode
input int                 InpATRPeriod       = 14;          // ATR period (stop distance)
input double              InpSLATR           = 1.5;         // Stop loss (x ATR)
input int                 InpMaxBars         = 12;          // Time limit (H1 candles held, 0 = off)

input group "=== BB_FADE ==="
input int                 InpBBPeriod        = 20;          // Bollinger period
input double              InpBBDev           = 2.0;         // Bollinger deviation
input int                 InpRSIPeriod       = 14;          // RSI period
input double              InpRSILow          = 30.0;        // Long below / short above 100-x (50 = off)
input int                 InpADXPeriod       = 14;          // ADX period
input double              InpADXMax          = 20.0;        // Trade only if ADX below (100 = off)

input group "=== RSI2_PULLBACK ==="
input int                 InpRSI2Period      = 2;           // Short RSI period
input double              InpRSI2Entry       = 10.0;        // Long below / short above 100-x
input double              InpRSI2Exit        = 70.0;        // Long exit above / short exit below 100-x
input int                 InpTrendPeriod     = 200;         // Trend EMA period

int g_atr   = INVALID_HANDLE;
int g_bands = INVALID_HANDLE;
int g_rsi   = INVALID_HANDLE;
int g_adx   = INVALID_HANDLE;
int g_rsi2  = INVALID_HANDLE;
int g_ema   = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpATRPeriod <= 0 || InpSLATR <= 0.0 || InpMaxBars < 0)
     {
      Print("Invalid ATR period / SL multiple / time limit");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEntryMode == MR_BB_FADE &&
      (InpBBPeriod <= 1 || InpBBDev <= 0.0 || InpRSIPeriod <= 1 || InpRSILow <= 0.0 || InpRSILow > 50.0 ||
       InpADXPeriod <= 1 || InpADXMax <= 0.0))
     {
      Print("Invalid BB_FADE inputs");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpEntryMode == MR_RSI2_PULLBACK && !RSI2Ready())
      return(INIT_PARAMETERS_INCORRECT);
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);

   g_atr = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   if(InpEntryMode == MR_BB_FADE)
     {
      g_bands = iBands(_Symbol, InpTimeframe, InpBBPeriod, 0, InpBBDev, PRICE_CLOSE);
      g_rsi   = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
      g_adx   = iADX(_Symbol, InpTimeframe, InpADXPeriod);
      if(g_bands == INVALID_HANDLE || g_rsi == INVALID_HANDLE || g_adx == INVALID_HANDLE)
        {
         Print("Failed to create BB_FADE indicators, error ", GetLastError());
         return(INIT_FAILED);
        }
     }
   else
     {
      g_rsi2 = iRSI(_Symbol, InpTimeframe, InpRSI2Period, PRICE_CLOSE);
      g_ema  = iMA(_Symbol, InpTimeframe, InpTrendPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_rsi2 == INVALID_HANDLE || g_ema == INVALID_HANDLE)
        {
         Print("Failed to create RSI2_PULLBACK indicators, error ", GetLastError());
         return(INIT_FAILED);
        }
     }
   if(g_atr == INVALID_HANDLE)
     {
      Print("Failed to create ATR, error ", GetLastError());
      return(INIT_FAILED);
     }

   PrintFormat("%s v%s started on %s %s, magic %I64u, own positions: %d",
               EA_NAME, EA_VERSION, _Symbol, EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
   PrintFormat("Inputs: mode=%s | BB(%d,%.1f) RSI%d<%.0f ADX%d<%.0f | RSI%d entry %.0f exit %.0f EMA%d | SL %.2fxATR(%d) maxBars %d | risk %s %.2f",
               InpEntryMode == MR_BB_FADE ? "BB_FADE" : "RSI2_PULLBACK",
               InpBBPeriod, InpBBDev, InpRSIPeriod, InpRSILow, InpADXPeriod, InpADXMax,
               InpRSI2Period, InpRSI2Entry, InpRSI2Exit, InpTrendPeriod, InpSLATR, InpATRPeriod, InpMaxBars,
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void ReleaseHandle(int &handle)
  {
   if(handle != INVALID_HANDLE)
     {
      IndicatorRelease(handle);
      handle = INVALID_HANDLE;
     }
  }

void OnDeinit(const int reason)
  {
   ReleaseHandle(g_atr);
   ReleaseHandle(g_bands);
   ReleaseHandle(g_rsi);
   ReleaseHandle(g_adx);
   ReleaseHandle(g_rsi2);
   ReleaseHandle(g_ema);
   Core_Deinit(reason);
  }

//+------------------------------------------------------------------+
bool ReadValue(const int handle, const int buffer, double &value)
  {
   double b[1];
   if(handle == INVALID_HANDLE || CopyBuffer(handle, buffer, 1, 1, b) < 1 || b[0] == EMPTY_VALUE)
      return(false);
   value = b[0];
   return(true);
  }

//--- close own positions held for InpMaxBars signal candles (counts candles, not hours: weekend-safe)
void CheckTimeLimit()
  {
   if(InpMaxBars <= 0)
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;
      const datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      const int held = iBarShift(_Symbol, InpTimeframe, opened, false);
      if(held >= InpMaxBars)
         Core_ClosePosition(ticket, "time_limit");
     }
  }

void OnTick()
  {
   Core_OnTickStart();
   CheckTimeLimit();
   const datetime bar = Core_CurrentBar();
   Core_RetryPending(bar);
   if(!Core_IsNewBar(bar))
      return;
   double atr = 0.0;
   const double close = iClose(_Symbol, InpTimeframe, 1);
   if(!ReadValue(g_atr, 0, atr) || atr <= 0.0 || close <= 0.0)
      return;                                   // data not ready: bar stays unprocessed, retried next tick
   if(InpEntryMode == MR_BB_FADE)
      OnBarBBFade(bar, close, atr);
   else
      OnBarRSI2(bar, close, atr);
  }

void OnBarBBFade(const datetime bar, const double close, const double atr)
  {
   double mid = 0.0, up = 0.0, lo = 0.0, rsi = 0.0, adx = 0.0;
   if(!ReadValue(g_bands, 0, mid) || !ReadValue(g_bands, 1, up) || !ReadValue(g_bands, 2, lo) ||
      !ReadValue(g_rsi, 0, rsi) || !ReadValue(g_adx, 0, adx))
      return;
   Core_MarkBar(bar);
   const string ctx = StringFormat("close %.2f band %.2f/%.2f/%.2f RSI %.1f ADX %.1f", close, lo, mid, up, rsi, adx);
   if(CountOwnPositions() > 0)
     {
      Core_LogBar(bar, ctx + " | in position");
      return;
     }
   const bool longSig  = close < lo && rsi < InpRSILow         && adx < InpADXMax;
   const bool shortSig = close > up && rsi > 100.0 - InpRSILow && adx < InpADXMax;
   if(!longSig && !shortSig)
     {
      Core_LogBar(bar, ctx + " | no signal");
      return;
     }
   Core_LogBar(bar, ctx + (longSig ? " | BUY signal" : " | SELL signal"));
   Core_Open(longSig ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, InpSLATR * atr, mid);
  }

bool RSI2Ready()
  {
   if(InpRSI2Period <= 0 || InpRSI2Entry <= 0.0 || InpRSI2Entry >= 50.0 ||
      InpRSI2Exit <= 50.0 || InpRSI2Exit >= 100.0 || InpTrendPeriod <= 1)
     {
      Print("Invalid RSI2_PULLBACK inputs (entry 0-50, exit 50-100, trend period > 1)");
      return(false);
     }
   return(true);
  }

void OnBarRSI2(const datetime bar, const double close, const double atr)
  {
   double rsi2 = 0.0, ema = 0.0;
   if(!ReadValue(g_rsi2, 0, rsi2) || !ReadValue(g_ema, 0, ema))
      return;
   Core_MarkBar(bar);
   const string ctx = StringFormat("close %.2f EMA%d %.2f RSI%d %.1f", close, InpTrendPeriod, ema, InpRSI2Period, rsi2);

   ulong ticket = 0;
   if(Core_SelectOwnPosition(ticket))
     {
      const bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      if((isBuy && rsi2 > InpRSI2Exit) || (!isBuy && rsi2 < 100.0 - InpRSI2Exit))
        {
         Core_LogBar(bar, ctx + " | RSI2 exit");
         Core_ClosePosition(ticket, "rsi_exit");
        }
      else
        {
         Core_LogBar(bar, ctx + " | in position");
         return;
        }
      if(CountOwnPositions() > 0)
         return;                               // close failed: retried on the next new bar
     }

   const bool longSig  = close > ema && rsi2 < InpRSI2Entry;
   const bool shortSig = close < ema && rsi2 > 100.0 - InpRSI2Entry;
   if(!longSig && !shortSig)
     {
      Core_LogBar(bar, ctx + " | no signal");
      return;
     }
   Core_LogBar(bar, ctx + (longSig ? " | BUY signal" : " | SELL signal"));
   Core_Open(longSig ? POSITION_TYPE_BUY : POSITION_TYPE_SELL, InpSLATR * atr, 0.0);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
