//+------------------------------------------------------------------+
//|                                   TelegramSMC_Common.mqh          |
//|                                                                    |
//| Shared CSV-logging helpers for TelegramSMC_Copier.mq5 and          |
//| TelegramSMC_TradeLogger.mq5 - two independent EAs. This header is  |
//| a code-sharing convenience only, not a runtime link between them:  |
//| either can run without the other. The Copier writes one row per    |
//| Telegram message it evaluates to TSMC_SIGNALS_FILE; the Logger      |
//| writes one row per position open/close to TSMC_RESULTS_FILE. The   |
//| two are joined, if you want to, by matching order_ticket between   |
//| them - nothing here merges them automatically.                     |
//+------------------------------------------------------------------+
#property strict

#define TSMC_SIGNALS_FILE   "TelegramSMC_Signals.csv"
#define TSMC_RESULTS_FILE   "TelegramSMC_Results.csv"

// This EA (TelegramSMC_Copier.mq5) only ever copies Telegram signals, so its
// own order comment and Signals-log rows always carry this literal tag - see
// TelegramSMC_TradeLogger.mq5's InpSourceLabel for the reusable-EA case,
// where the source isn't fixed at compile time.
#define TSMC_SIGNAL_SOURCE  "Telegram_Sig"

// "source" is deliberately the LAST column in both headers, not inserted
// after time_utc: an already-running deployment's log file keeps whatever
// header it was created with (TsmcOpenCsvForAppend below never rewrites an
// existing header), so a column inserted in the middle would silently shift
// every field after it - for every reader that keys off column NAME, not
// position (the ASPX dashboard, csv.DictReader, ...) - the moment this
// build starts appending longer rows under that stale header. Appending at
// the end instead means an old header just doesn't expose "source" for
// that file (a missing feature) rather than corrupting every other column
// (silent data loss) - see TsmcOpenCsvForAppend's mismatch warning below.
#define TSMC_SIGNALS_HEADER "time_utc,chat_id,action,direction,symbol_ok,entry_low,entry_high,sl,tps,smc_used,smc_pass,smc_reason,sanity_pass,sanity_reason,accepted,order_type,order_price,lots,dry_run,order_ticket,retcode,raw_text,source"
#define TSMC_RESULTS_HEADER "time_utc,event,position_id,order_ticket,symbol,magic,direction,volume,price,sl,tp,profit,swap,commission,net_profit,close_reason,duration_min,price_move,comment,source"

//+------------------------------------------------------------------+
//| Quote a field per RFC4180 (wrap in double quotes, double any       |
//| embedded quotes). Always quoting is simpler than deciding when     |
//| it's needed and just as valid, and safely handles raw Telegram     |
//| text that contains commas, quotes, or anything else.               |
//+------------------------------------------------------------------+
string TsmcCsvField(const string value)
{
   string t = value;
   StringReplace(t, "\"", "\"\"");
   return("\"" + t + "\"");
}

//+------------------------------------------------------------------+
//| Opens filename for appending: creates it with the given header    |
//| row if it doesn't exist yet (or is empty), otherwise seeks to the |
//| end so existing rows are preserved. Returns INVALID_HANDLE on      |
//| failure (logged). useCommon writes to the shared Common\Files      |
//| folder (visible to every terminal on the machine) instead of this  |
//| terminal's own MQL5\Files.                                         |
//+------------------------------------------------------------------+
int TsmcOpenCsvForAppend(const string filename, const string header, bool useCommon)
{
   // This function runs on EVERY logged row (every Telegram message
   // evaluated, every position open/close), so the stale-header warning
   // below must only ever print once per filename per EA run - a "|"-
   // delimited list of filenames already warned about, remembered across
   // calls via `static`, is what makes that "once" instead of "every row"
   // (mirrors telegram_copier.py's _warned_stale_signal_log_header flag).
   static string warnedFiles = "|";

   int flags = FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ;
   if(useCommon) flags |= FILE_COMMON;

   int handle = FileOpen(filename, flags);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("TelegramSMC: could not open %s for logging, error=%d", filename, GetLastError());
      return(INVALID_HANDLE);
   }

   if(FileSize(handle) == 0)
   {
      FileWriteString(handle, header + "\r\n");
      return(handle);
   }

   // Read-only check, never modifies the file: warn (once per filename) if
   // the file's actual first line doesn't match what this build would write
   // today, so a schema change (a column added to header/*) is visible in
   // the log instead of silently degrading - see the header comments above.
   string existingHeader = FileReadString(handle);
   if(existingHeader != header && StringFind(warnedFiles, "|" + filename + "|") < 0)
   {
      warnedFiles += filename + "|";
      PrintFormat("TelegramSMC: %s already exists with a different header than this build writes - "
                  "any new column (e.g. \"source\") won't be readable by name for this file until you "
                  "rename/delete it and let a fresh one be created. Existing data and columns are not "
                  "affected - new rows are simply longer than the old header describes.", filename);
   }

   FileSeek(handle, 0, SEEK_END);
   return(handle);
}
//+------------------------------------------------------------------+
