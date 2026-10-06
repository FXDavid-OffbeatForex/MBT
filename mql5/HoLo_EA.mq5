//+------------------------------------------------------------------+
//|                                                      HoLo_EA.mq5 |
//|  MichaelG's "HoLo" (Highest Open / Lowest Open, ForexFactory)    |
//|  as described in HoLo.pdf; levels as in smHiLoOpen Lines v1.2.   |
//|  SELL: a trigger candle OPENS between the day's highest H1 open  |
//|  (HO) and the day's high (the Area of Interest); sell when Bid   |
//|  falls back to HO, stop at the day's high. A new day high before |
//|  entry cancels the setup. BUY mirrors it at the lowest open (LO) |
//|  with Ask. Stop to break-even + 1 pip after 5 pips (scaled to    |
//|  % of price: 5 AUDUSD pips = 0.067%). Day = 17:00 NY to 17:00 NY |
//|  = Darwinex server midnight. Entries only in the New York        |
//|  session, flat before the 17:00 NY close.                        |
//|  Execution, guards and trade log from EACore.mqh (v1.42 core).   |
//|                                                                  |
//|  v1.00                                                           |
//+------------------------------------------------------------------+
#property copyright "2026"
#property version   "1.00"
#property strict
#property description "HoLo: fade the day's highest/lowest H1 open after a trigger candle opens in the Area of Interest; NY session only"

#define EA_NAME            "HoLo_EA"
#define EA_VERSION         "1.00"
#define EA_MAGIC           261007
#define EA_COMMENT         "HOLO"
#define EA_TIMEFRAME       PERIOD_M15   // trigger timeframe (InpTimeframe)
#define EA_SPREAD_WAIT_MIN 0            // the entry is a price level: a delayed retry would be a late entry
#include "EACore.mqh"
#define D1_WINDOW 5                     // D1 level mode: highest/lowest open of the last 5 daily bars
#define FLAT_MARGIN_MIN 10              // go flat this long before the broker's session close (ticks thin out before it)

enum ENUM_HOLO_ENTRY
  {
   ENTRY_TOUCH         = 0,  // Touch: enter when price returns to the level (PDF)
   ENTRY_CLOSE_CONFIRM = 1   // Close-confirm: enter after a trigger candle closes beyond the level
  };

input group "=== HoLo ==="
input ENUM_HOLO_ENTRY     InpEntryMode       = ENTRY_TOUCH; // Entry mode
input ENUM_TIMEFRAMES     InpLevelTF         = PERIOD_H1;   // Level timeframe: H1/H4 = today's opens, D1 = last 5 daily opens
input double              InpRR              = 1.0;         // Take profit = RR x stop distance (0 = none)
input double              InpBETriggerPct    = 0.067;       // Stop to break-even after this profit, % of price (0 = off)
input double              InpBELockPct       = 0.013;       // ... locking in this profit, % of price
input double              InpMaxLatePct      = 0.01;        // Max entry beyond the level, % of price ("no late entry")
input double              InpMinStopPct      = 0.03;        // Skip setups with a stop closer than this, % of price
input bool                InpBreakoutFilter  = true;        // No sells once the day broke the previous high (buys: low)

input group "=== New York session ==="
input int                 InpSessionStartNY  = 480;         // Entries from, minutes after NY midnight (480 = 08:00)
input int                 InpSessionEndNY    = 1015;        // Entries until and flat at (1015 = 16:55)
input int                 InpServerNYOffset  = 7;           // Server time minus New York time, hours (Darwinex: 7 all year)

input group "=== Research (tester) ==="
input bool                InpExcursionLog    = false;       // Write each trade's favourable excursions per R level to a CSV

struct Levels { double ho, lo, high, low, pdHigh, pdLow; };

#define EXC_N 13
double EXC_X[EXC_N] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0};
//--- per open position: reached x R (hit), came back to entry after it (back), best R after x before coming back (run)
struct Excursion { ulong position; bool buy; double entry, risk, riskMoney, mfe; bool hit[EXC_N]; bool back[EXC_N]; double run[EXC_N];
                   double mfeP, maeP, lastM, lastA; string path; double trendH4, trendH12, aoiAtr, atrTrig; };
//--- entry context for the research log, set just before an order is sent and picked up by ExcTrack
double    g_ctxTrendH4 = 0.0, g_ctxTrendH12 = 0.0, g_ctxAoiAtr = 0.0, g_ctxAtrTrig = 0.0;
int       g_emaH4 = INVALID_HANDLE, g_smaH12 = INVALID_HANDLE, g_atrLevel = INVALID_HANDLE, g_atrTrig = INVALID_HANDLE;
Excursion g_exc[];
string    g_excFile  = "";
int       g_excFlags = 0;

bool     g_sellArmed = false, g_buyArmed = false;
datetime g_sellArmBar = 0, g_buyArmBar = 0;
double g_sellHO = 0.0, g_sellHigh = 0.0, g_buyLO = 0.0, g_buyLow = 0.0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if((InpLevelTF != PERIOD_H1 && InpLevelTF != PERIOD_H4 && InpLevelTF != PERIOD_D1) ||
      PeriodSeconds(InpTimeframe) >= PeriodSeconds(InpLevelTF))
     {
      Print("Level timeframe must be H1, H4 or D1, and the trigger timeframe (InpTimeframe) lower");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRR < 0.0 || InpBETriggerPct < 0.0 || InpBELockPct < 0.0 ||
      (InpBETriggerPct > 0.0 && InpBELockPct >= InpBETriggerPct) || InpMaxLatePct < 0.0 || InpMinStopPct < 0.0 ||
      InpSessionStartNY < 0 || InpSessionEndNY > 1440 || InpSessionStartNY >= InpSessionEndNY ||
      InpServerNYOffset < -12 || InpServerNYOffset > 14)
     {
      Print("Invalid HoLo inputs (RR/BE/late/min stop >= 0, BE lock below trigger, session start < end within 0-1440)");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(!Core_Init())
      return(INIT_PARAMETERS_INCORRECT);
   ExcInit();
   PrintFormat("%s v%s started on %s, levels %s, trigger %s, magic %I64u, own positions: %d",
               EA_NAME, EA_VERSION, _Symbol, EnumToString(InpLevelTF), EnumToString(InpTimeframe), InpMagic, CountOwnPositions());
   PrintFormat("Inputs: RR %.2f | BE +%.3f%% lock %.3f%% | late %.3f%% | min stop %.3f%% | breakout filter %s | NY %02d:%02d-%02d:%02d (server = NY%+d h) | risk %s %.2f",
               InpRR, InpBETriggerPct, InpBELockPct, InpMaxLatePct, InpMinStopPct, InpBreakoutFilter ? "on" : "off",
               InpSessionStartNY / 60, InpSessionStartNY % 60, InpSessionEndNY / 60, InpSessionEndNY % 60, InpServerNYOffset,
               InpRiskMode == RISK_PERCENT ? "percent" : "money", InpRiskValue);
   string flat = "";
   for(int d = 1; d <= 5; d++)
      flat += StringFormat(" %s %02d:%02d", StringSubstr(EnumToString((ENUM_DAY_OF_WEEK)d), 0, 3), SessionEndNY(d) / 60, SessionEndNY(d) % 60);
   Print("Entries stop and positions go flat at NY time:", flat);
   Core_PrintReady();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   int hs[] = {g_emaH4, g_smaH12, g_atrLevel, g_atrTrig};
   for(int i = 0; i < ArraySize(hs); i++)
      if(hs[i] != INVALID_HANDLE)
         IndicatorRelease(hs[i]);
   for(int j = ArraySize(g_exc) - 1; j >= 0; j--)   // positions the tester closes at the end of the test
      ExcWrite(j);
   Core_Deinit(reason);
  }

//+------------------------------------------------------------------+
//| Excursion log (research): replays exit rules offline             |
//+------------------------------------------------------------------+
void ExcInit()
  {
   if(!InpExcursionLog || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   g_excFlags = MQLInfoInteger(MQL_TESTER) ? FILE_COMMON : 0;
   g_excFile  = StringFormat("%s_%s_%I64u_exc.csv", EA_NAME, _Symbol, InpMagic);
   FileDelete(g_excFile, g_excFlags);
   const int h = FileOpen(g_excFile, FILE_WRITE | FILE_TXT | FILE_ANSI | g_excFlags);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("Excursion log open failed, error %d", GetLastError());
      g_excFile = "";
      return;
     }
   string hdr = "position,side,entry,risk_price,risk_money,mfe_r";
   for(int k = 0; k < EXC_N; k++)
      hdr += StringFormat(",hit_%.2f,back_%.2f,run_%.2f", EXC_X[k], EXC_X[k], EXC_X[k]);
   FileWriteString(h, hdr + ",trend_h4,trend_h12,aoi_atr,atr_trig,path\n");
   FileClose(h);
   g_emaH4    = iMA(_Symbol, PERIOD_H4, 200, 0, MODE_EMA, PRICE_CLOSE);    // MACD_Cross_EA's trend filter
   g_smaH12   = iMA(_Symbol, PERIOD_H12, 250, 0, MODE_SMA, PRICE_CLOSE);   // RSI_Reversal_EA's trend filter
   g_atrLevel = iATR(_Symbol, InpLevelTF, 14);
   g_atrTrig  = iATR(_Symbol, InpTimeframe, 14);
  }

void ExcWrite(const int j)
  {
   const int h = FileOpen(g_excFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | g_excFlags);
   if(h == INVALID_HANDLE)
      return;
   string row = StringFormat("%I64u,%s,%.2f,%.2f,%.2f,%.3f", g_exc[j].position, g_exc[j].buy ? "buy" : "sell",
                             g_exc[j].entry, g_exc[j].risk, g_exc[j].riskMoney, g_exc[j].mfe);
   for(int k = 0; k < EXC_N; k++)
      row += StringFormat(",%d,%d,%.3f", g_exc[j].hit[k] ? 1 : 0, g_exc[j].back[k] ? 1 : 0, g_exc[j].run[k]);
   string path = g_exc[j].path;
   if(g_exc[j].mfeP != g_exc[j].lastM || g_exc[j].maeP != g_exc[j].lastA)
      path += StringFormat("%.2f:%.2f|", g_exc[j].mfeP, g_exc[j].maeP);
   row += StringFormat(",%.0f,%.0f,%.3f,%.3f,%s", g_exc[j].trendH4, g_exc[j].trendH12, g_exc[j].aoiAtr, g_exc[j].atrTrig, path);
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, row + "\n");
   FileClose(h);
  }

void ExcTrack()
  {
   if(g_excFile == "")
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)          // start tracking new positions
     {
      if(PositionGetTicket(i) == 0 || !IsOwnPosition())
         continue;
      const ulong id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      bool known = false;
      for(int j = 0; j < ArraySize(g_exc) && !known; j++)
         known = (g_exc[j].position == id);
      const double entry = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL);
      if(known || sl <= 0.0)
         continue;
      const bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double pl = 0.0;
      if(!OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, PositionGetDouble(POSITION_VOLUME), entry, sl, pl))
         continue;
      const int n = ArraySize(g_exc);
      ArrayResize(g_exc, n + 1, 64);
      g_exc[n].position = id; g_exc[n].buy = buy; g_exc[n].entry = entry;
      g_exc[n].risk = MathAbs(entry - sl); g_exc[n].riskMoney = -pl; g_exc[n].mfe = 0.0;
      g_exc[n].mfeP = 0.0; g_exc[n].maeP = 0.0; g_exc[n].lastM = 0.0; g_exc[n].lastA = 0.0; g_exc[n].path = "";
      g_exc[n].trendH4 = g_ctxTrendH4; g_exc[n].trendH12 = g_ctxTrendH12; g_exc[n].aoiAtr = g_ctxAoiAtr; g_exc[n].atrTrig = g_ctxAtrTrig;
      for(int k = 0; k < EXC_N; k++)
        {
         g_exc[n].hit[k] = false; g_exc[n].back[k] = false; g_exc[n].run[k] = 0.0;
        }
     }
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   for(int j = ArraySize(g_exc) - 1; j >= 0; j--)
     {
      if(!PositionSelectByTicket(g_exc[j].position))      // closed (hedging: position ticket == identifier)
        {
         ExcWrite(j);
         ArrayRemove(g_exc, j, 1);
         continue;
        }
      const double r = (g_exc[j].buy ? bid - g_exc[j].entry : g_exc[j].entry - ask) / g_exc[j].risk;   // the closing side's price
      g_exc[j].mfe = MathMax(g_exc[j].mfe, r);
      //--- adverse-move staircase: log (best favourable, worst adverse) whenever the adverse excursion grows
      const double fav = r * g_exc[j].risk;
      g_exc[j].mfeP = MathMax(g_exc[j].mfeP, fav);
      if(-fav > g_exc[j].maeP)
        {
         g_exc[j].maeP = -fav;
         if(g_exc[j].maeP >= g_exc[j].lastA + 0.02 * g_exc[j].risk || g_exc[j].mfeP > g_exc[j].lastM)
           {
            g_exc[j].path += StringFormat("%.2f:%.2f|", g_exc[j].mfeP, g_exc[j].maeP);
            g_exc[j].lastM = g_exc[j].mfeP;
            g_exc[j].lastA = g_exc[j].maeP;
           }
        }
      for(int k = 0; k < EXC_N; k++)
        {
         if(!g_exc[j].hit[k])
           {
            if(r >= EXC_X[k])
              {
               g_exc[j].hit[k] = true;
               g_exc[j].run[k] = r;
              }
           }
         else if(!g_exc[j].back[k])
           {
            if(r <= 0.0)
               g_exc[j].back[k] = true;
            else
               g_exc[j].run[k] = MathMax(g_exc[j].run[k], r);
           }
        }
     }
  }

void Diag(const string msg)
  {
   if(InpLogEveryBar)
      Print(msg);
  }

//--- minutes after New York midnight
int NYMinute(const datetime t)
  {
   const datetime ny = t - (datetime)(InpServerNYOffset * 3600);
   return((int)((ny % 86400) / 60));
  }

//--- NY minute at which entries stop and positions go flat on this server weekday: the input, or earlier when the
//--- broker's trade session ends sooner (Darwinex gold: 23:55 server = 16:55 NY on Fridays, no ticks after it)
int SessionEndNY(const int dayOfWeek)
  {
   long end = -1;
   datetime from, to;
   for(uint i = 0; SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dayOfWeek, i, from, to); i++)
      end = MathMax(end, (long)to);                                   // seconds after server midnight
   if(end < 0)
      return(InpSessionEndNY);
   return((int)MathMin(InpSessionEndNY, (end - InpServerNYOffset * 3600) / 60 - FLAT_MARGIN_MIN));
  }

bool InSession(const datetime t)
  {
   static datetime cachedDay = 0;
   static int      endNY = 0;
   const datetime day = t - t % 86400;
   if(day != cachedDay)
     {
      MqlDateTime dt;
      TimeToStruct(t, dt);
      cachedDay = day;
      endNY = SessionEndNY(dt.day_of_week);
     }
   const int m = NYMinute(t);
   return(m >= InpSessionStartNY && m < endNY);
  }

//--- day (or 5-day window for D1 levels): highest/lowest level-timeframe open, high/low, previous window's high/low
bool ComputeLevels(Levels &lv)
  {
   const int n = (InpLevelTF == PERIOD_D1) ? D1_WINDOW : 1;
   MqlRates d[];
   ArraySetAsSeries(d, true);
   if(CopyRates(_Symbol, PERIOD_D1, 0, 2 * n, d) < 2 * n)
      return(false);
   lv.high = d[0].high;  lv.low = d[0].low;
   lv.pdHigh = d[n].high; lv.pdLow = d[n].low;
   for(int i = 1; i < n; i++)
     {
      lv.high = MathMax(lv.high, d[i].high);           lv.low = MathMin(lv.low, d[i].low);
      lv.pdHigh = MathMax(lv.pdHigh, d[n + i].high);   lv.pdLow = MathMin(lv.pdLow, d[n + i].low);
     }
   MqlRates b[];
   if(CopyRates(_Symbol, InpLevelTF, d[n - 1].time, TimeCurrent(), b) < 1)   // includes the forming bar's open
      return(false);
   lv.ho = b[0].open;
   lv.lo = b[0].open;
   for(int i = 1; i < ArraySize(b); i++)
     {
      lv.ho = MathMax(lv.ho, b[i].open);
      lv.lo = MathMin(lv.lo, b[i].open);
     }
   return(true);
  }

//--- a new trigger candle opened: re-validate armed setups, arm when the open is inside an Area of Interest
bool OnNewTriggerBar(const datetime bar, const bool session)
  {
   Levels lv;
   if(!ComputeLevels(lv))
      return(false);
   Core_MarkBar(bar);
   if(g_sellArmed && (lv.ho != g_sellHO || lv.high > g_sellHigh))
     {
      g_sellArmed = false;
      Diag(StringFormat("SELL setup cancelled: HO %.2f -> %.2f, high %.2f -> %.2f", g_sellHO, lv.ho, g_sellHigh, lv.high));
     }
   if(g_buyArmed && (lv.lo != g_buyLO || lv.low < g_buyLow))
     {
      g_buyArmed = false;
      Diag(StringFormat("BUY setup cancelled: LO %.2f -> %.2f, low %.2f -> %.2f", g_buyLO, lv.lo, g_buyLow, lv.low));
     }
   const double o = iOpen(_Symbol, InpTimeframe, 0);
   const string ctx = StringFormat("open %.2f | HO %.2f LO %.2f | high %.2f low %.2f | prev high %.2f low %.2f",
                                   o, lv.ho, lv.lo, lv.high, lv.low, lv.pdHigh, lv.pdLow);
   if(!session || CountOwnPositions() > 0)
     {
      Core_LogBar(bar, ctx + (session ? " | in position" : " | outside NY session"));
      return(true);
     }
   TryCloseConfirmEntry(bar);
   if(CountOwnPositions() > 0)
      return(true);
   const double minStop = InpMinStopPct / 100.0 * o;
   string what = "";
   if(!g_sellArmed && o > lv.ho && o < lv.high)
     {
      if(InpBreakoutFilter && lv.high > lv.pdHigh)
         what += " | sell skipped: day above previous high";
      else if(lv.high - lv.ho < minStop)
         what += " | sell skipped: stop too close";
      else
        {
         g_sellArmed = true; g_sellHO = lv.ho; g_sellHigh = lv.high; g_sellArmBar = bar;
         what += " | SELL armed";
        }
     }
   if(!g_buyArmed && o < lv.lo && o > lv.low)
     {
      if(InpBreakoutFilter && lv.low < lv.pdLow)
         what += " | buy skipped: day below previous low";
      else if(lv.lo - lv.low < minStop)
         what += " | buy skipped: stop too close";
      else
        {
         g_buyArmed = true; g_buyLO = lv.lo; g_buyLow = lv.low; g_buyArmBar = bar;
         what += " | BUY armed";
        }
     }
   Core_LogBar(bar, ctx + (what == "" ? " | no setup" : what));
   return(true);
  }

double LastClosed(const int handle)
  {
   double b[1];
   return((handle != INVALID_HANDLE && CopyBuffer(handle, 0, 1, 1, b) == 1) ? b[0] : 0.0);
  }

//--- research log context: with-trend sign vs H4 EMA200 and H12 SMA250, Area of Interest width in level-TF ATRs
void SetEntryContext(const double level, const double extreme)
  {
   if(g_excFile == "")
      return;
   const double bid = g_symbol.Bid(), ema = LastClosed(g_emaH4), sma = LastClosed(g_smaH12), atr = LastClosed(g_atrLevel);
   g_ctxTrendH4  = (ema > 0.0) ? (bid > ema ? 1.0 : -1.0) : 0.0;
   g_ctxTrendH12 = (sma > 0.0) ? (bid > sma ? 1.0 : -1.0) : 0.0;
   g_ctxAoiAtr   = (atr > 0.0) ? MathAbs(extreme - level) / atr : 0.0;
   g_ctxAtrTrig  = LastClosed(g_atrTrig);
  }

//--- close-confirm mode: the trigger candle that just closed (opened at or after arming) closed beyond the level
void TryCloseConfirmEntry(const datetime bar)
  {
   if(InpEntryMode != ENTRY_CLOSE_CONFIRM || (!g_sellArmed && !g_buyArmed) || CountOwnPositions() > 0 || !g_symbol.RefreshRates())
      return;
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
      return;
   const datetime prevBar = iTime(_Symbol, InpTimeframe, 1);
   const double   prevClose = iClose(_Symbol, InpTimeframe, 1);
   const double   bid = g_symbol.Bid(), ask = g_symbol.Ask();
   if(g_sellArmed && prevBar >= g_sellArmBar && prevClose < g_sellHO)
     {
      const double sl = g_sellHigh + (ask - bid);
      PrintFormat("HOLO SELL (close-confirmed %.2f < HO %.2f): bid %.2f, stop %.2f", prevClose, g_sellHO, bid, sl);
      SetEntryContext(g_sellHO, g_sellHigh);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_SELL, sl - bid, InpRR > 0.0 ? bid - InpRR * (sl - bid) : 0.0);
      return;
     }
   if(g_buyArmed && prevBar >= g_buyArmBar && prevClose > g_buyLO)
     {
      PrintFormat("HOLO BUY (close-confirmed %.2f > LO %.2f): ask %.2f, stop %.2f", prevClose, g_buyLO, ask, g_buyLow);
      SetEntryContext(g_buyLO, g_buyLow);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_BUY, ask - g_buyLow, InpRR > 0.0 ? ask + InpRR * (ask - g_buyLow) : 0.0);
     }
  }

void ManageBreakEven()
  {
   if(InpBETriggerPct <= 0.0 || !g_symbol.RefreshRates())
      return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOwnPosition())
         continue;
      const double open = PositionGetDouble(POSITION_PRICE_OPEN);
      const double sl   = PositionGetDouble(POSITION_SL);
      const double trig = InpBETriggerPct / 100.0 * open, lock = InpBELockPct / 100.0 * open;
      const double half = g_symbol.Point() / 2.0;
      double newSL = 0.0;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
        {
         if(g_symbol.Bid() - open >= trig && sl < open + lock - half)      // the Bid closes a buy
            newSL = open + lock;
        }
      else if(open - g_symbol.Ask() >= trig && (sl == 0.0 || sl > open - lock + half))   // the Ask closes a sell
         newSL = open - lock;
      if(newSL > 0.0)
         g_trade.PositionModify(ticket, NormalizeDouble(newSL, g_symbol.Digits()), PositionGetDouble(POSITION_TP));
     }
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

//--- enter exactly at the level: Bid back to HO for a sell, Ask back to LO for a buy, never more than InpMaxLatePct beyond it
void TryEntry()
  {
   if(InpEntryMode != ENTRY_TOUCH || (!g_sellArmed && !g_buyArmed) || CountOwnPositions() > 0)
      return;
   if(InpMaxSpreadPoints > 0 && g_symbol.Spread() > InpMaxSpreadPoints)
      return;                                   // wait until the spread allows the entry
   const double bid = g_symbol.Bid(), ask = g_symbol.Ask();
   if(g_sellArmed && bid <= g_sellHO && g_sellHO - bid <= InpMaxLatePct / 100.0 * g_sellHO)
     {
      const double sl = g_sellHigh + (ask - bid);   // the high is a Bid high, a sell stop triggers on the Ask
      const double dist = sl - bid;
      PrintFormat("HOLO SELL: bid %.2f at HO %.2f, stop %.2f (high %.2f + spread)", bid, g_sellHO, sl, g_sellHigh);
      SetEntryContext(g_sellHO, g_sellHigh);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_SELL, dist, InpRR > 0.0 ? bid - InpRR * dist : 0.0);
      return;
     }
   if(g_buyArmed && ask >= g_buyLO && ask - g_buyLO <= InpMaxLatePct / 100.0 * g_buyLO)
     {
      const double dist = ask - g_buyLow;             // the low is a Bid low and a buy stop triggers on the Bid
      PrintFormat("HOLO BUY: ask %.2f at LO %.2f, stop %.2f", ask, g_buyLO, g_buyLow);
      SetEntryContext(g_buyLO, g_buyLow);
      g_sellArmed = g_buyArmed = false;
      Core_Open(POSITION_TYPE_BUY, dist, InpRR > 0.0 ? ask + InpRR * dist : 0.0);
     }
  }

void OnTick()
  {
   Core_OnTickStart();
   ExcTrack();
   const bool session = InSession(TimeCurrent());
   if(session)
      ManageBreakEven();
   else
     {
      g_sellArmed = g_buyArmed = false;
      if(CountOwnPositions() > 0)
         FlattenOwn("session_end");
     }
   const datetime bar = Core_CurrentBar();
   if(Core_IsNewBar(bar) && !OnNewTriggerBar(bar, session))
      return;                                   // data not ready: bar stays unprocessed, retried next tick
   if(!session || !g_symbol.RefreshRates())
      return;
   const double bid = g_symbol.Bid();
   if(g_sellArmed && bid > g_sellHigh)
     {
      g_sellArmed = false;
      Diag(StringFormat("SELL setup cancelled: new high %.2f above %.2f (intraday breakout)", bid, g_sellHigh));
     }
   if(g_buyArmed && bid < g_buyLow)
     {
      g_buyArmed = false;
      Diag(StringFormat("BUY setup cancelled: new low %.2f below %.2f (intraday breakout)", bid, g_buyLow));
     }
   TryEntry();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   Core_OnTradeTransaction(trans);
  }

double OnTester() { return(Core_OnTester()); }
//+------------------------------------------------------------------+
