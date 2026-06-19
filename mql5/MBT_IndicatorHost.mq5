//+------------------------------------------------------------------+
//| MBT_IndicatorHost.mq5 — MBT (MT5 Backtest Toolkit)               |
//|                                                                  |
//| A generic, do-nothing Expert Advisor whose only job is to LOAD   |
//| another indicator inside MT5's Strategy Tester and make it run.  |
//|                                                                  |
//| Why this exists:                                                 |
//|   MT5's API can't attach an indicator to a chart, and the tester |
//|   only runs Experts — not indicators. But an Expert can load any |
//|   indicator with iCustom(), and once loaded the indicator's      |
//|   OnCalculate() runs bar-by-bar over the test range. If that     |
//|   indicator logs its own signals (via SignalLogger.mqh), those   |
//|   signals get written with zero manual chart work.               |
//|                                                                  |
//| This EA places NO trades. It just hosts the indicator and pulls  |
//| one buffer value each bar so the terminal actually computes it   |
//| (a custom indicator in the tester is only calculated when its    |
//| data is requested).                                              |
//|                                                                  |
//| Driven by MBT's `run_indicator` tool, which passes the indicator |
//| name through a .set file (InpIndicator).                         |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "MBT host EA — runs another indicator headlessly so it logs its signals. Places no trades."

// Indicator name relative to MQL5\Indicators (e.g. "RegimePlusePro" or
// "Subfolder\\MyIndi"), with or without the .ex5 extension. Passed by the tool.
input string InpIndicator = "";

// How many of the indicator's plotted buffers to request each bar. Requesting a
// buffer is what forces the tester to calculate the indicator; one is enough for
// almost every indicator, but a couple is cheap insurance.
input int    InpBuffersToPull = 2;

int      g_handle  = INVALID_HANDLE;
datetime g_lastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   string name = InpIndicator;
   StringTrimLeft(name);
   StringTrimRight(name);
   if(name == "")
   {
      Print("MBT_IndicatorHost: InpIndicator is empty — nothing to host. ",
            "Pass the indicator name via the .set file.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   // iCustom wants the name with no extension — strip a trailing .ex5/.mq5 if the
   // caller passed one (the tool documents the extension as optional).
   string lower = name;
   StringToLower(lower);
   if(StringFind(lower, ".ex5", StringLen(lower) - 4) >= 0 ||
      StringFind(lower, ".mq5", StringLen(lower) - 4) >= 0)
      name = StringSubstr(name, 0, StringLen(name) - 4);
   // iCustom resolves the name under MQL5\Indicators.
   g_handle = iCustom(_Symbol, _Period, name);
   if(g_handle == INVALID_HANDLE)
   {
      PrintFormat("MBT_IndicatorHost: could not load indicator '%s' (iCustom failed). "
                  "Check the name/path under MQL5\\Indicators and that it compiled.", name);
      return(INIT_FAILED);
   }
   PrintFormat("MBT_IndicatorHost: hosting '%s' on %s %s.",
               name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_handle != INVALID_HANDLE)
      IndicatorRelease(g_handle);
}

//+------------------------------------------------------------------+
//| Once per new bar, request the hosted indicator's buffers so the  |
//| tester computes it for the just-closed bar. We never act on the  |
//| values — the indicator's own logging does the real work.         |
//+------------------------------------------------------------------+
void OnTick()
{
   if(g_handle == INVALID_HANDLE)
      return;

   datetime barTime = iTime(_Symbol, _Period, 0);
   if(barTime == g_lastBar)
      return;                       // still the same bar — pull once per bar
   g_lastBar = barTime;

   double buf[];
   int    pulls = (InpBuffersToPull < 1) ? 1 : InpBuffersToPull;
   for(int b = 0; b < pulls; b++)
   {
      // Best-effort: a buffer index the indicator doesn't have simply returns
      // <=0 copied; that's fine, the request still triggers calculation.
      CopyBuffer(g_handle, b, 0, 2, buf);
   }
}
//+------------------------------------------------------------------+
