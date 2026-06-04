//+------------------------------------------------------------------+
//| SignalLogger.mqh — MBT (MT5 Backtest Toolkit)                    |
//|                                                                  |
//| Drop this file into your terminal's MQL5\Include folder, then    |
//| in your indicator:                                               |
//|                                                                  |
//|   #include <SignalLogger.mqh>                                    |
//|                                                                  |
//|   // when your buy/sell condition is true on bar `shift`:        |
//|   LogSignal(shift, true,  entryPrice, slPrice, tpPrice);  // BUY |
//|   LogSignal(shift, false, entryPrice, slPrice, tpPrice);  // SELL|
//|                                                                  |
//| It writes one standard CSV row per NEW signal to:                |
//|   <terminal>\MQL5\Files\<SignalLogFile>                          |
//| which the MBT Python tools / backtester read directly.           |
//+------------------------------------------------------------------+
#property strict

// Name of the CSV the toolkit reads. Must match `signal_file` in config.yaml.
input string SignalLogFile = "signals.csv";

//+------------------------------------------------------------------+
//| Reset the log (call once in OnInit, or on full recalculation,    |
//| so the file always reflects the currently drawn signals).        |
//+------------------------------------------------------------------+
void ResetSignalLog()
{
   FileDelete(SignalLogFile);
}

//+------------------------------------------------------------------+
//| Internal: ensure header exists, return a handle at end-of-file.  |
//+------------------------------------------------------------------+
int _OpenSignalLog()
{
   bool isNew = !FileIsExist(SignalLogFile);
   int  h     = FileOpen(SignalLogFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE)
      return INVALID_HANDLE;

   if(isNew || FileSize(h) == 0)
      FileWriteString(h, "time,symbol,timeframe,direction,entry,sl,tp,regime\n");
   else
      FileSeek(h, 0, SEEK_END);

   return h;
}

//+------------------------------------------------------------------+
//| Log a signal. Deduplicates by bar time so repeated recalculation |
//| of the same bar does not create duplicate rows.                  |
//|                                                                  |
//| shift   : bar index the signal fired on (0 = current bar)        |
//| isLong  : true for BUY, false for SELL                           |
//| entry   : entry price                                            |
//| sl, tp  : stop-loss / take-profit prices                         |
//| regime  : optional label (e.g. "TRENDING"); pass "" if unused    |
//+------------------------------------------------------------------+
void LogSignal(int shift, bool isLong, double entry, double sl, double tp,
               string regime = "")
{
   datetime barTime = iTime(_Symbol, _Period, shift);

   // --- dedup: skip if this bar's time is already the last logged row ---
   if(FileIsExist(SignalLogFile))
   {
      int rh = FileOpen(SignalLogFile, FILE_READ|FILE_TXT|FILE_ANSI);
      if(rh != INVALID_HANDLE)
      {
         string lastLine = "";
         while(!FileIsEnding(rh))
            lastLine = FileReadString(rh);
         FileClose(rh);

         string stamp = TimeToString(barTime, TIME_DATE|TIME_MINUTES);
         if(StringFind(lastLine, stamp) == 0)
            return;   // already logged this bar
      }
   }

   int h = _OpenSignalLog();
   if(h == INVALID_HANDLE)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   FileWriteString(h, StringFormat("%s,%s,%s,%s,%s,%s,%s,%s\n",
      TimeToString(barTime, TIME_DATE|TIME_MINUTES),
      _Symbol,
      _PeriodToStr(_Period),
      isLong ? "LONG" : "SHORT",
      DoubleToString(entry, digits),
      DoubleToString(sl,    digits),
      DoubleToString(tp,    digits),
      regime));
   FileClose(h);
}

//+------------------------------------------------------------------+
//| Map ENUM_TIMEFRAMES to the toolkit's timeframe strings.          |
//+------------------------------------------------------------------+
string _PeriodToStr(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "1m";
      case PERIOD_M5:  return "5m";
      case PERIOD_M15: return "15m";
      case PERIOD_M30: return "30m";
      case PERIOD_H1:  return "1h";
      case PERIOD_H4:  return "4h";
      case PERIOD_D1:  return "1d";
      case PERIOD_W1:  return "1w";
      default:         return "1h";
   }
}
//+------------------------------------------------------------------+
