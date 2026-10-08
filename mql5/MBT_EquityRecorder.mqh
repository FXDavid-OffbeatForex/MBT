// Embedded by MBT into a generated EA copy. Never enabled for live or optimizer.
// FILE_COMMON: https://www.mql5.com/en/docs/files/fileopen
// AccountInfoDouble: https://www.mql5.com/en/docs/account/accountinfodouble
int MBT_EQ_handle=INVALID_HANDLE;
int MBT_EQ_count=0;
bool MBT_EQ_truncated=false;
bool MBT_EQ_failed=false;
void MBT_EQ_Sample()
{
   if(!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION)) return;
   if(MBT_EQ_failed || MBT_EQ_truncated) return;
   if(MBT_EQ_handle==INVALID_HANDLE)
   {
      MBT_EQ_handle=FileOpen("MBT_Equity\\"+MBT_EQ_RUN+".csv",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,',',CP_UTF8);
      if(MBT_EQ_handle==INVALID_HANDLE){ MBT_EQ_failed=true; return; }
      FileWrite(MBT_EQ_handle,"run_id","source_sha256","symbol","timeframe","model","sequence","time","balance","equity");
   }
   if(MBT_EQ_count>=500000){ MBT_EQ_truncated=true; return; }
   uint written=FileWrite(MBT_EQ_handle,MBT_EQ_RUN,MBT_EQ_SOURCE,MBT_EQ_SYMBOL,MBT_EQ_PERIOD,
             MBT_EQ_MODEL,MBT_EQ_count,TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
             DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),8),DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),8));
   if(written==0){ MBT_EQ_failed=true; return; }
   MBT_EQ_count++;
}
void MBT_EQ_Close()
{
   if(MBT_EQ_handle==INVALID_HANDLE) return;
   FileWrite(MBT_EQ_handle,"END",MBT_EQ_count,MBT_EQ_truncated ? "truncated" : (MBT_EQ_failed ? "failed" : "complete"));
   FileFlush(MBT_EQ_handle);
   FileClose(MBT_EQ_handle);
   MBT_EQ_handle=INVALID_HANDLE;
}
