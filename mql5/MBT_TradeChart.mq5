// Generated display-only script. No trading APIs, DLLs or account history reads.
#property strict
struct RecordedDeal { datetime time; double price; bool buy; string label; };
RecordedDeal deals[] = {
// MBT_DEALS
};
void OnStart()
{
   long chart=ChartID();
   ChartSetInteger(chart,CHART_AUTOSCROLL,false);
   ChartSetInteger(chart,CHART_MODE,CHART_CANDLES);
   ChartSetInteger(chart,CHART_SHOW_TRADE_HISTORY,false);
   int created=0;
   for(int i=0;i<ArraySize(deals);i++)
   {
      string name="MBT_RECORDED_"+IntegerToString(i);
      if(!ObjectCreate(chart,name,deals[i].buy?OBJ_ARROW_BUY:OBJ_ARROW_SELL,0,deals[i].time,deals[i].price))
         continue;
      ObjectSetInteger(chart,name,OBJPROP_COLOR,deals[i].buy?clrDeepSkyBlue:clrOrangeRed);
      ObjectSetString(chart,name,OBJPROP_TOOLTIP,deals[i].label);
      created++;
   }
   Comment("MBT historical test deal overlay\n// MBT_LABEL\nNot live trades; not animated tester playback");
   // Load the saved history before navigating to the first recorded deal.
   int offset=-1;
   MqlRates history[];
   for(int attempt=0;attempt<80;attempt++)
   {
      CopyRates(_Symbol,_Period,deals[0].time-86400,deals[0].time+86400,history);
      offset=iBarShift(_Symbol,_Period,deals[0].time,true);
      if(offset>=0 && iTime(_Symbol,_Period,offset)<=deals[0].time)break;
      Sleep(250);
   }
   bool positioned=false;
   if(offset>=0)
   {
      for(int attempt=0;attempt<20;attempt++)
      {
         ChartNavigate(chart,CHART_END,-offset+20);
         ChartRedraw(chart);
         Sleep(250);
         long first=ChartGetInteger(chart,CHART_FIRST_VISIBLE_BAR);
         long width=ChartGetInteger(chart,CHART_VISIBLE_BARS);
         if(offset<=first && offset>=first-width+1){positioned=true;break;}
      }
   }
   ChartRedraw(chart);
   Sleep(500);
   bool screenshot=ChartScreenShot(chart,"// MBT_SCREENSHOT",1280,720,ALIGN_LEFT);
   int file=FileOpen("// MBT_STATUS",FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(file!=INVALID_HANDLE)
   {
      FileWriteString(file,IntegerToString(created)+","+IntegerToString((int)positioned)+","+IntegerToString((int)screenshot));
      FileClose(file);
   }
}
