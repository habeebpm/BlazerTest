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

#define TSMC_SIGNALS_HEADER "time_utc,chat_id,action,direction,symbol_ok,entry_low,entry_high,sl,tps,smc_used,smc_pass,smc_reason,sanity_pass,sanity_reason,accepted,order_type,order_price,lots,dry_run,order_ticket,retcode,raw_text"
#define TSMC_RESULTS_HEADER "time_utc,event,position_id,order_ticket,symbol,magic,direction,volume,price,sl,tp,profit,swap,commission,net_profit,close_reason,duration_min,price_move,comment"

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
   int flags = FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ;
   if(useCommon) flags |= FILE_COMMON;

   int handle = FileOpen(filename, flags);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("TelegramSMC: could not open %s for logging, error=%d", filename, GetLastError());
      return(INVALID_HANDLE);
   }

   if(FileSize(handle) == 0)
      FileWriteString(handle, header + "\r\n");
   else
      FileSeek(handle, 0, SEEK_END);

   return(handle);
}
//+------------------------------------------------------------------+
