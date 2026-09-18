//+------------------------------------------------------------------+
//|                                          ExportM1History.mq5      |
//| One-shot script: exports this terminal's own M1 OHLC history for  |
//| a symbol to a CSV, in the exact format python/backtest_xtr.py's   |
//| load_m1_csv() expects (time,open,high,low,close - ascending order,|
//| time as YYYY.MM.DD HH:MM) - so a real backtest can run against    |
//| YOUR broker's actual historical quotes, not a third-party feed.   |
//|                                                                     |
//| Run it from the Navigator's Scripts tab: drag it onto any chart,   |
//| or right-click > Scripts > ExportM1History. It runs once and       |
//| exits - it does not place trades, does not run in the background, |
//| and is unrelated to ClaudeSignalEA.mq5's own live file bridge.     |
//|                                                                     |
//| IMPORTANT: MT5 only has locally what it has already downloaded.    |
//| If InpStartDate is older than your terminal's cached history,      |
//| open an M1 chart for the symbol and scroll it back to that date    |
//| first (this makes MT5 fetch older bars from the broker's server),  |
//| then run this script - otherwise the export will simply start      |
//| wherever your local history actually begins, which this script     |
//| reports in the Experts log either way so you can tell what you     |
//| actually got.                                                       |
//|                                                                     |
//| Output ordering: this script relies on CopyRates' documented        |
//| default (oldest bar at index 0 when the array has NOT been set     |
//| as series) - but python/backtest_xtr.py's load_m1_csv() re-sorts    |
//| by time regardless, so even if that assumption were ever wrong on  |
//| some build, the exported file still loads correctly either way.     |
//+------------------------------------------------------------------+
#property copyright "ExportM1History"
#property link      ""
#property version   "1.00"
#property script_show_inputs

input string   InpSymbol          = "";                    // Symbol to export (blank = current chart's symbol)
input datetime InpStartDate       = D'2026.01.01 00:00';   // Export bars from this date (server time)
input datetime InpEndDate         = 0;                     // Export bars up to this date (0 = now)
input string   InpOutFileName     = "xauusd_m1_history.csv"; // Output file name
input bool     InpUseCommonFolder = true;                  // Write to the shared Common\Files folder (recommended - easy to find from Python)

int FileFlag() { return(InpUseCommonFolder ? FILE_COMMON : 0); }

void OnStart()
{
   string symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   datetime endDate = (InpEndDate == 0) ? TimeCurrent() : InpEndDate;

   if(!SymbolSelect(symbol, true))
   {
      PrintFormat("ExportM1History: symbol %s not found / could not be selected in Market Watch", symbol);
      return;
   }

   MqlRates rates[];
   int copied = CopyRates(symbol, PERIOD_M1, InpStartDate, endDate, rates);
   if(copied <= 0)
   {
      PrintFormat("ExportM1History: no M1 bars available for %s in that range (err=%d). "
                  "Open an M1 chart for %s and scroll it back to your start date first - that "
                  "makes the terminal download older history from the broker - then run this again.",
                  symbol, GetLastError(), symbol);
      return;
   }

   int handle = FileOpen(InpOutFileName, FILE_WRITE | FILE_TXT | FILE_ANSI | FileFlag());
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("ExportM1History: cannot open %s for writing, err=%d", InpOutFileName, GetLastError());
      return;
   }

   FileWrite(handle, "time,open,high,low,close");
   for(int i = 0; i < copied; i++)
      FileWrite(handle, StringFormat("%s,%.5f,%.5f,%.5f,%.5f",
                TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
                rates[i].open, rates[i].high, rates[i].low, rates[i].close));
   FileClose(handle);

   PrintFormat("ExportM1History: wrote %d M1 bars for %s (%s -> %s) to %s%s - "
               "check those two timestamps against what you actually asked for.",
               copied, symbol, TimeToString(rates[0].time), TimeToString(rates[copied - 1].time),
               InpOutFileName, InpUseCommonFolder ? " (Common\\Files)" : " (this terminal's own Files folder)");
}
