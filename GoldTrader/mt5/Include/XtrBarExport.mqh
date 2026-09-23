//+------------------------------------------------------------------+
//|                                              XtrBarExport.mqh     |
//|                                                                    |
//| Writes closed M5/M15/H1 bars (optionally M1) as CSV files plus a   |
//| manifest into Common\Files\<folder> on every M1 candle close -     |
//| the same files, names and format as drive_export/xtr_export.py|
//| (datetime,open,high,low,close,volume; datetime in true UTC;        |
//| ascending; closed bars only). Add that folder to Google Drive for  |
//| Desktop (My Computer -> Add folder) and Drive keeps it synced - no |
//| Python, no credentials.                                            |
//|                                                                    |
//| Folder on disk:                                                    |
//|   C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\Common\Files\ |
//|   <folder>  (MT5: File -> Open Data Folder, up two levels, Common) |
//|                                                                    |
//| UTC: MT5 bar times are the broker server's clock. The offset is    |
//| TimeTradeServer() - TimeGMT(), rounded to 15 minutes; if it is not |
//| within 2 minutes of a 15-minute boundary (PC clock wrong, terminal |
//| not synced yet) nothing is written that cycle - never mislabeled   |
//| timestamps. A timeframe's CSV is rewritten only when it has a new  |
//| closed bar (less Drive traffic); the manifest every minute, as a   |
//| heartbeat. Writes are atomic (temp file + rename), so Drive never  |
//| uploads a half-written file. Never runs in the Strategy Tester.    |
//|                                                                    |
//| COPY TO ANY FOLDER (e.g. G:\My Drive\MyMQChartDrive): MQL5 file   |
//| functions cannot write outside MQL5\Files / Common\Files, so with |
//| copyTo set every file written is then copied there with Windows'   |
//| own CopyFileW/MoveFileExW (kernel32.dll - nothing else). Needs     |
//| "Allow DLL imports" ticked in the EA's Common tab; without it the  |
//| copy is skipped with a warning (the local export still runs).      |
//| Copying is tracked separately from the local write: a failed copy  |
//| (G: not mounted yet at boot, DLLs off) is retried every minute and |
//| never stops or re-churns the local files. Compiled in only when    |
//| the EA defines XTR_EXPORT_COPY_DLL before including this file -    |
//| remove that #define for a build with no DLL import at all (MQL5    |
//| Market does not accept DLL imports).                               |
//+------------------------------------------------------------------+
#ifndef XTR_BAR_EXPORT_MQH
#define XTR_BAR_EXPORT_MQH

#ifdef XTR_EXPORT_COPY_DLL
#import "kernel32.dll"
int CopyFileW(string existingFile, string newFile, int failIfExists);
int MoveFileExW(string existingFile, string newFile, int flags);
int CreateDirectoryW(string pathName, long securityAttributes);
int DeleteFileW(string fileName);
#import
#endif

datetime g_xtrExpLastM1 = 0;          // last M1 bar handled
datetime g_xtrExpLastBar[4];          // per timeframe: last closed bar written
int      g_xtrExpRows[4];             // per timeframe: rows in the file last written
datetime g_xtrExpCopiedBar[4];        // per timeframe: last bar copied to copyTo
datetime g_xtrExpWarnAt = 0;          // throttles warnings to one per 30 min
bool     g_xtrExpInit   = false;

string XtrExpTfName(ENUM_TIMEFRAMES tf)
{
   return(StringSubstr(EnumToString(tf), 7));   // "PERIOD_M15" -> "M15"
}

bool XtrExpWriteAtomic(const string path, const string text)
{
   string tmp = path + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE)
      return(false);
   FileWriteString(h, text);
   FileClose(h);
   if(FileMove(tmp, FILE_COMMON, path, FILE_COMMON|FILE_REWRITE))
      return(true);
   FileDelete(tmp, FILE_COMMON);
   return(false);
}

void XtrExpWarn(const string text)
{
   if(TimeLocal() - g_xtrExpWarnAt < 1800)
      return;
   g_xtrExpWarnAt = TimeLocal();
   Print("XtrBarExport: ", text);
}

// "2026.09.23 14:05:00" -> "2026-09-23 14:05:00"
string XtrExpUtcString(datetime t)
{
   string s = TimeToString(t, TIME_DATE|TIME_SECONDS);
   StringReplace(s, ".", "-");
   return(s);
}

// Call from OnInit: MT5 keeps globals across an input change/re-init,
// and a stale "already written" memory would leave a NEW folder empty
// until each timeframe's next bar (up to an hour for H1).
void XtrExpReset()
{
   g_xtrExpLastM1 = 0;
   g_xtrExpInit = false;
   g_xtrExpWarnAt = 0;
   ArrayInitialize(g_xtrExpLastBar, 0);
   ArrayInitialize(g_xtrExpRows, 0);
   ArrayInitialize(g_xtrExpCopiedBar, 0);
}

// "G:\\My Drive\\X\\" -> "G:\\My Drive\\X"
string XtrExpCleanDir(const string dir)
{
   string d = dir;
   StringTrimLeft(d);
   StringTrimRight(d);
   while(StringLen(d) > 3 && (StringGetCharacter(d, StringLen(d) - 1) == '\\'
                              || StringGetCharacter(d, StringLen(d) - 1) == '/'))
      d = StringSubstr(d, 0, StringLen(d) - 1);
   return(d);
}

// Copies Common\Files\<folder>\<file> to <dir>\<file> (temp + replace).
bool XtrExpMirror(const string dir, const string folder, const string file)
{
#ifdef XTR_EXPORT_COPY_DLL
   if(!MQLInfoInteger(MQL_DLLS_ALLOWED))
   {
      XtrExpWarn("copy to " + dir + " skipped - tick \"Allow DLL imports\" in the EA's Common tab "
                 "(only kernel32 CopyFileW/MoveFileExW are used). Local files keep updating.");
      return(false);
   }
   string src = TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files\\" + folder + "\\" + file;
   string dst = dir + "\\" + file;
   string tmp = dst + ".tmp";
   for(int attempt = 0; attempt < 2; attempt++)
   {
      if(CopyFileW(src, tmp, 0) != 0)
      {
         if(MoveFileExW(tmp, dst, 1 | 2) != 0)          // REPLACE_EXISTING | COPY_ALLOWED
            return(true);
         DeleteFileW(tmp);                              // never leave a .tmp behind in Drive
         if(CopyFileW(src, dst, 0) != 0)                // a drive that refuses the rename
            return(true);
      }
      if(attempt == 0)
         CreateDirectoryW(dir, 0);                      // folder missing (or G: just mounted) - once
   }
   XtrExpWarn("could not copy " + file + " to " + dir + " - check the exact folder path (Google "
              "Drive's default is G:\\My Drive\\...) and that Google Drive for Desktop is running. "
              "Retrying every minute; local files keep updating.");
   return(false);
#else
   XtrExpWarn("copy to " + dir + " needs the EA compiled with #define XTR_EXPORT_COPY_DLL.");
   return(false);
#endif
}

// Broker clock minus true UTC, rounded to 15 min; false when untrustworthy.
bool XtrExpBrokerOffset(long &offsetSec)
{
   long raw = (long)TimeTradeServer() - (long)TimeGMT();
   long rounded = (long)MathRound((double)raw / 900.0) * 900;
   offsetSec = rounded;
   return(MathAbs((double)(raw - rounded)) <= 120.0);
}

// One timeframe -> <folder>\<name>_<TF>.csv. Returns rows written (0 = skipped/unchanged, -1 = error).
int XtrExpWriteTf(const string symbol, const string folder, const string name, ENUM_TIMEFRAMES tf,
                  int slot, int bars, int digits, long offsetSec)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int n = CopyRates(symbol, tf, 1, bars, rates);      // start at 1: closed bars only, oldest first
   if(n <= 0)
   {
      XtrExpWarn(StringFormat("no %s history for %s yet (error %d).", XtrExpTfName(tf), symbol,
                              GetLastError()));
      return(-1);
   }
   if(rates[n - 1].time == g_xtrExpLastBar[slot])
      return(0);                                        // no new closed bar - leave the file alone
   string csv = "datetime,open,high,low,close,volume\r\n";
   for(int i = 0; i < n; i++)
      csv += XtrExpUtcString((datetime)((long)rates[i].time - offsetSec)) + "," +
             DoubleToString(rates[i].open, digits) + "," + DoubleToString(rates[i].high, digits) + "," +
             DoubleToString(rates[i].low, digits) + "," + DoubleToString(rates[i].close, digits) + "," +
             IntegerToString(rates[i].tick_volume) + "\r\n";
   string file = name + "_" + XtrExpTfName(tf) + ".csv";
   string path = folder + "\\" + file;
   if(!XtrExpWriteAtomic(path, csv))
   {
      XtrExpWarn(StringFormat("could not write Common\\Files\\%s (error %d).", path, GetLastError()));
      return(-1);
   }
   g_xtrExpLastBar[slot] = rates[n - 1].time;
   g_xtrExpRows[slot] = n;
   return(n);
}

//+------------------------------------------------------------------+
//| Call from OnTick and OnTimer; does work once per new M1 bar.       |
//+------------------------------------------------------------------+
void XtrExpMaybeExport(bool enabled, const string symbol, const string folder, const string name,
                       int bars, bool includeM1, const string copyTo = "")
{
   if(!enabled || MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   datetime m1 = iTime(symbol, PERIOD_M1, 0);
   if(m1 == 0 || m1 == g_xtrExpLastM1)
      return;
   if(!g_xtrExpInit)
   {
      ArrayInitialize(g_xtrExpLastBar, 0);
      ArrayInitialize(g_xtrExpRows, 0);
      FolderCreate(folder, FILE_COMMON);
      g_xtrExpInit = true;
   }
   long offsetSec;
   if(!XtrExpBrokerOffset(offsetSec))
   {
      XtrExpWarn("broker clock vs PC UTC is not a clean 15-minute offset (PC clock wrong or terminal "
                 "not synced yet) - skipping this export rather than mislabel timestamps.");
      return;
   }
   g_xtrExpLastM1 = m1;
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   ENUM_TIMEFRAMES tfs[4] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_H1};
   string counts = "";
   bool failed = false;
   for(int k = 0; k < 4; k++)
   {
      if(k == 0 && !includeM1)
         continue;
      int rows = XtrExpWriteTf(symbol, folder, name, tfs[k], k, bars, digits, offsetSec);
      if(rows < 0)
      {
         failed = true;                                 // retried on the next M1 bar
         continue;
      }
      if(StringLen(counts) > 0)
         counts += ", ";
      counts += "\"" + XtrExpTfName(tfs[k]) + "\": " + IntegerToString(g_xtrExpRows[k]);
   }
   if(failed && StringLen(counts) == 0)
      return;
   string manifest =
      "{\r\n"
      "  \"symbol\": \"" + name + "\",\r\n"
      "  \"datetime_timezone\": \"UTC\",\r\n"
      "  \"datetime_format\": \"%Y-%m-%d %H:%M:%S (UTC, no offset suffix)\",\r\n"
      "  \"quote_digits\": " + IntegerToString(digits) + ",\r\n"
      "  \"broker_utc_offset_hours\": " + DoubleToString(offsetSec / 3600.0, 2) + ",\r\n"
      "  \"bars_per_file\": {" + counts + "},\r\n"
      "  \"exported_at_utc\": \"" + XtrExpUtcString(TimeGMT()) + "\",\r\n"
      "  \"exported_by\": \"UnifiedTrader_EA\"\r\n"
      "}\r\n";
   bool manifestOk = XtrExpWriteAtomic(folder + "\\" + name + "_manifest.json", manifest);
   if(!manifestOk)
      XtrExpWarn(StringFormat("could not write the manifest (error %d).", GetLastError()));

   // Copy step, independent of the local write: every CSV not yet copied
   // at its current bar, then the manifest (heartbeat). Stops at the first
   // failure (G: down) and retries all of it next minute.
   string dir = XtrExpCleanDir(copyTo);
   if(StringLen(dir) == 0)
      return;
   for(int k = 0; k < 4; k++)
   {
      if((k == 0 && !includeM1) || g_xtrExpLastBar[k] == 0 || g_xtrExpCopiedBar[k] == g_xtrExpLastBar[k])
         continue;
      if(!XtrExpMirror(dir, folder, name + "_" + XtrExpTfName(tfs[k]) + ".csv"))
         return;
      g_xtrExpCopiedBar[k] = g_xtrExpLastBar[k];
   }
   if(manifestOk)
      XtrExpMirror(dir, folder, name + "_manifest.json");
}

#endif
