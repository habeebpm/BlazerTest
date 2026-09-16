//+------------------------------------------------------------------+
//|                                          ClaudeSignalEA.mq5       |
//|                                                                    |
//| Two-file bridge between MQL5 and an external (Claude-driven)      |
//| Python analyst. This EA does NOT decide trade direction itself -  |
//| see MQL5/Experts/XAUUSD_Confluence_EA.mq5 for a self-contained    |
//| indicator strategy. Instead:                                      |
//|                                                                    |
//|  1. EXPORT  - every InpExportIntervalSec seconds, writes the last |
//|               InpBarsToExport bars of InpExportTF to a plain-text |
//|               CSV file (InpExportFileName).                      |
//|  2. Python reads that file, sends it (plus derived scenario       |
//|     statistics) to Claude for analysis, and writes a single       |
//|     BUY/SELL/NONE signal line with SL/TP to a second plain-text   |
//|     file (InpSignalFileName). See python/claude_signal_bot.py.    |
//|  3. EXECUTE - every InpSignalPollSec seconds, this EA reads that   |
//|     signal file and, if it carries a new signal id, opens the     |
//|     trade at the given SL/TP (rejecting anything malformed, on    |
//|     the wrong side of price, or inside the broker's minimum stop  |
//|     distance).                                                    |
//|  4. TRAIL   - every tick, open positions opened by this EA (magic |
//|     number) are trailed by a fixed InpTrailingUSD amount, in      |
//|     account-currency dollars, converted to a price distance via   |
//|     the symbol's tick value/size and the position's own volume,   |
//|     so "$4 trailing stop" means $4 regardless of instrument or    |
//|     lot size. The stop only ever tightens.                        |
//|                                                                    |
//| Both files default to the terminal's shared "Common\Files"        |
//| folder (InpUseCommonFolder = true) rather than this terminal's    |
//| own sandboxed MQL5\Files, because that is the one folder a        |
//| Python process running alongside MetaTrader can also reach        |
//| directly on disk (typically                                       |
//| %APPDATA%\MetaQuotes\Terminal\Common\Files on Windows). See        |
//| python/CLAUDE_SIGNAL_PIPELINE.md for the full protocol and setup. |
//|                                                                    |
//| IMPORTANT DISCLAIMER                                               |
//| ------------------------------------------------------------------ |
//| This bridges live trade execution to an LLM's text output. LLM    |
//| analysis can be wrong, and a malformed or malicious signal file   |
//| could in principle instruct a trade - this EA validates prices,   |
//| sides and stop distances defensively, but no validation replaces  |
//| careful demo-testing. No trading system can guarantee profit.     |
//| Backtest and forward-test on a demo account before risking real   |
//| capital, and only risk money you can afford to lose.              |
//+------------------------------------------------------------------+
#property copyright "ClaudeSignalEA"
#property link      ""
#property version   "1.00"
#property strict
#property description "File-bridge EA: exports chart data for an external (Claude) analyst and executes the BUY/SELL/SL/TP signals it writes back, with a fixed-dollar trailing stop. Educational use - demo-test thoroughly before risking real capital."

#include <Trade\Trade.mqh>

//================================= INPUTS ====================================

input group "=== General ==="
input ulong   InpMagicNumber        = 20260916;    // Magic number
input int     InpSlippagePoints     = 30;           // Max slippage (points)
input bool    InpRequireSymbolMatch = true;         // Ignore signals whose symbol != chart symbol
input int     InpMaxOpenPositions   = 4;            // Max simultaneous open positions (this EA/symbol); 0 = unlimited

input group "=== Chart Data Export (MQL5 -> Python) ==="
input string  InpExportFileName     = "claude_chart_data.txt";  // Export file name
input bool    InpUseCommonFolder    = true;         // Use the shared Common\Files folder (recommended)
input ENUM_TIMEFRAMES InpExportTF   = PERIOD_M1;    // Timeframe of exported bars
input int     InpBarsToExport       = 300;          // How many recent bars to export
input int     InpExportIntervalSec  = 60;           // Seconds between exports ("every minute")

input group "=== Trade Signals (Python -> MQL5) ==="
input string  InpSignalFileName     = "claude_trade_signals.txt"; // Signal file name
input string  InpAckFileName        = "claude_trade_ack.txt";     // Execution-ack log (appended)
input int     InpSignalPollSec      = 5;            // Seconds between signal-file checks
input double  InpDefaultLot         = 0.01;         // Lot used when the signal omits one (<= 0)

input group "=== Risk Management ==="
input double  InpTrailingUSD        = 4.0;          // Trailing-stop distance, in account-currency $, per position
input double  InpTrailStartUSD      = 4.0;          // Profit ($) required before the trail engages

//================================= STATE =====================================

CTrade   trade;
datetime g_lastExport   = 0;
datetime g_lastPoll     = 0;
long     g_lastSignalId = -1;
string   g_gvName;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTrailingUSD <= 0.0)
   {
      Print("ClaudeSignalEA: InpTrailingUSD must be > 0.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

   g_gvName = "ClaudeSignalEA_" + _Symbol + "_" + (string)InpMagicNumber + "_lastid";
   if(GlobalVariableCheck(g_gvName))
      g_lastSignalId = (long)GlobalVariableGet(g_gvName);

   EventSetTimer(1);
   ExportChartData();   // seed a file immediately rather than waiting a full interval
   g_lastExport = TimeCurrent();

   PrintFormat("ClaudeSignalEA initialized on %s. export=%s (every %ds) signal=%s (poll %ds) "
               "common_folder=%s trailing=$%.2f lastSignalId=%d",
               _Symbol, InpExportFileName, InpExportIntervalSec, InpSignalFileName,
               InpSignalPollSec, InpUseCommonFolder ? "yes" : "no", InpTrailingUSD, g_lastSignalId);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void OnTimer()
{
   datetime now = TimeCurrent();
   if(now - g_lastExport >= InpExportIntervalSec)
   {
      ExportChartData();
      g_lastExport = now;
   }
   if(now - g_lastPoll >= InpSignalPollSec)
   {
      CheckSignalFile();
      g_lastPoll = now;
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenPositions();
}

//+------------------------------------------------------------------+
//| File-flag helpers                                                  |
//+------------------------------------------------------------------+
int CommonFlag() { return(InpUseCommonFolder ? FILE_COMMON : 0); }

//+------------------------------------------------------------------+
//| Export the last InpBarsToExport bars of InpExportTF as CSV.       |
//| Writes to a .tmp file first and swaps it in, so Python never sees |
//| a half-written file mid-read.                                     |
//+------------------------------------------------------------------+
void ExportChartData()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, InpExportTF, 0, InpBarsToExport, rates);
   if(copied <= 0)
   {
      PrintFormat("ClaudeSignalEA: CopyRates failed, err=%d", GetLastError());
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      PrintFormat("ClaudeSignalEA: SymbolInfoTick failed, err=%d", GetLastError());
      return;
   }

   string tmpName = InpExportFileName + ".tmp";
   int handle = FileOpen(tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI | CommonFlag());
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("ClaudeSignalEA: cannot open %s for export, err=%d", tmpName, GetLastError());
      return;
   }

   int    digits     = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point      = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   int    spreadPts  = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   string exportedStr = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
   StringReplace(exportedStr, " ", "T");

   FileWrite(handle, StringFormat(
      "#symbol=%s digits=%d point=%.5f tick_value=%.5f tick_size=%.5f bid=%.5f ask=%.5f "
      "spread=%d timeframe=%s exported=%s",
      _Symbol, digits, point, tickValue, tickSize, tick.bid, tick.ask,
      spreadPts, EnumToString(InpExportTF), exportedStr));
   FileWrite(handle, "time,open,high,low,close,tick_volume,spread");

   for(int i = copied - 1; i >= 0; i--)   // oldest -> newest
   {
      FileWrite(handle, StringFormat("%s,%.5f,%.5f,%.5f,%.5f,%d,%d",
                TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
                rates[i].open, rates[i].high, rates[i].low, rates[i].close,
                (int)rates[i].tick_volume, (int)rates[i].spread));
   }
   FileClose(handle);

   FileDelete(InpExportFileName, CommonFlag());
   if(!FileMove(tmpName, CommonFlag(), InpExportFileName, CommonFlag()))
      PrintFormat("ClaudeSignalEA: FileMove %s -> %s failed, err=%d", tmpName, InpExportFileName, GetLastError());
}

//+------------------------------------------------------------------+
//| Append one line to the execution-ack log so Python can see what   |
//| happened to a signal it wrote.                                    |
//+------------------------------------------------------------------+
void WriteAck(const long id, const string status, const string detail)
{
   int handle = FileOpen(InpAckFileName, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | CommonFlag());
   if(handle == INVALID_HANDLE) return;
   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle, StringFormat("%s,%d,%s,%s",
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), id, status, detail));
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Read the signal file and act on it if it carries a fresh id.      |
//| Expected single-line CSV format (see python/claude_signal_bot.py):|
//|   id,symbol,action,lot,sl,tp,timestamp,reason                     |
//| action is one of BUY | SELL | NONE | CLOSE                        |
//+------------------------------------------------------------------+
void CheckSignalFile()
{
   if(!FileIsExist(InpSignalFileName, CommonFlag()))
      return;

   int handle = FileOpen(InpSignalFileName, FILE_READ | FILE_TXT | FILE_ANSI | CommonFlag());
   if(handle == INVALID_HANDLE)
      return;

   string lastLine = "";
   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0 || StringGetCharacter(line, 0) == '#')
         continue;
      lastLine = line;
   }
   FileClose(handle);

   if(lastLine == "")
      return;

   string parts[];
   int n = StringSplit(lastLine, ',', parts);
   if(n < 7)
   {
      PrintFormat("ClaudeSignalEA: malformed signal line, ignored: %s", lastLine);
      return;
   }

   long   id     = StringToInteger(parts[0]);
   string symbol = parts[1];
   string action = parts[2];
   StringToUpper(action);
   double lot    = StringToDouble(parts[3]);
   double sl     = StringToDouble(parts[4]);
   double tp     = StringToDouble(parts[5]);
   string reason = (n > 7) ? parts[7] : "";

   if(id <= g_lastSignalId)
      return;   // already processed, or a stale/duplicate write

   g_lastSignalId = id;
   GlobalVariableSet(g_gvName, (double)id);

   if(InpRequireSymbolMatch && symbol != _Symbol)
   {
      PrintFormat("ClaudeSignalEA: signal id=%d for %s ignored on chart %s", id, symbol, _Symbol);
      WriteAck(id, "REJECTED", "symbol mismatch");
      return;
   }

   if(action == "NONE" || action == "HOLD")
   {
      WriteAck(id, "SKIPPED", "no-trade signal");
      return;
   }

   if(action == "CLOSE" || action == "CLOSE_ALL")
   {
      CloseAllManaged();
      WriteAck(id, "CLOSED", "close signal");
      return;
   }

   if(action != "BUY" && action != "SELL")
   {
      PrintFormat("ClaudeSignalEA: unknown action '%s' in signal id=%d, ignored", action, id);
      WriteAck(id, "REJECTED", "unknown action");
      return;
   }

   ExecuteSignal(id, action == "BUY", lot, sl, tp, reason);
}

//+------------------------------------------------------------------+
int CountManagedPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      count++;
   }
   return(count);
}

//+------------------------------------------------------------------+
void CloseAllManaged()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
double NormalizeLots(double lots)
{
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   lots = MathFloor(lots / lotStep) * lotStep;
   return(NormalizeDouble(lots, 2));
}

//+------------------------------------------------------------------+
//| Validate and execute one BUY/SELL signal. SL/TP are absolute      |
//| prices, as computed by the Python/Claude side.                    |
//+------------------------------------------------------------------+
void ExecuteSignal(const long id, const bool isBuy, const double lot,
                    double sl, double tp, const string reason)
{
   if(InpMaxOpenPositions > 0 && CountManagedPositions() >= InpMaxOpenPositions)
   {
      PrintFormat("ClaudeSignalEA: signal id=%d skipped, max open positions (%d) reached", id, InpMaxOpenPositions);
      WriteAck(id, "SKIPPED", "max positions reached");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      WriteAck(id, "FAILED", "no tick available");
      return;
   }

   double price = isBuy ? tick.ask : tick.bid;

   if(sl <= 0.0 || tp <= 0.0)
   {
      PrintFormat("ClaudeSignalEA: signal id=%d missing sl/tp, rejected", id);
      WriteAck(id, "REJECTED", "missing sl/tp");
      return;
   }

   if((isBuy && (sl >= price || tp <= price)) || (!isBuy && (sl <= price || tp >= price)))
   {
      PrintFormat("ClaudeSignalEA: signal id=%d has sl/tp on the wrong side of price (price=%.5f sl=%.5f tp=%.5f), rejected",
                  id, price, sl, tp);
      WriteAck(id, "REJECTED", "sl/tp on wrong side of price");
      return;
   }

   long   stopsLevelPts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double point         = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist   = MathMax((double)stopsLevelPts, 1.0) * point;
   if(MathAbs(price - sl) < minStopDist || MathAbs(price - tp) < minStopDist)
   {
      PrintFormat("ClaudeSignalEA: signal id=%d sl/tp inside the broker's min stop distance (%.5f), rejected",
                  id, minStopDist);
      WriteAck(id, "REJECTED", "sl/tp inside broker min stop distance");
      return;
   }

   double lots = NormalizeLots(lot > 0.0 ? lot : InpDefaultLot);

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   string comment = "ClaudeSignal#" + (string)id;
   bool ok = isBuy ? trade.Buy(lots, _Symbol, price, sl, tp, comment)
                   : trade.Sell(lots, _Symbol, price, sl, tp, comment);

   if(ok)
   {
      PrintFormat("ClaudeSignalEA: %s executed. id=%d lots=%.2f price=%.5f sl=%.5f tp=%.5f reason=%s",
                  isBuy ? "BUY" : "SELL", id, lots, price, sl, tp, reason);
      WriteAck(id, "EXECUTED", StringFormat("ticket=%I64u price=%.5f lots=%.2f", trade.ResultOrder(), price, lots));
   }
   else
   {
      PrintFormat("ClaudeSignalEA: order failed for signal id=%d. retcode=%d desc=%s",
                  id, trade.ResultRetcode(), trade.ResultRetcodeDescription());
      WriteAck(id, "FAILED", StringFormat("retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
   }
}

//+------------------------------------------------------------------+
//| Convert InpTrailingUSD into a price distance for a given volume,  |
//| using the symbol's tick value/size - so "$4" means $4 whatever    |
//| the instrument or lot size, not a fixed number of points.         |
//+------------------------------------------------------------------+
double MoneyPerPriceUnit(const double volume)
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0.0 || volume <= 0.0) return(0.0);
   return((tickValue / tickSize) * volume);
}

//+------------------------------------------------------------------+
//| Fixed-dollar trailing stop for every position this EA opened.     |
//| Only ever tightens the stop, never loosens it.                    |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   long   stopsLevelPts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double point         = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist   = MathMax((double)stopsLevelPts, 1.0) * point;
   int    digits        = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      double volume    = PositionGetDouble(POSITION_VOLUME);
      double moneyUnit = MoneyPerPriceUnit(volume);
      if(moneyUnit <= 0.0) continue;

      double trailDist = InpTrailingUSD / moneyUnit;
      double startDist = InpTrailStartUSD / moneyUnit;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSl = PositionGetDouble(POSITION_SL);
      double currentTp = PositionGetDouble(POSITION_TP);

      if(type == POSITION_TYPE_BUY)
      {
         double profit = tick.bid - openPrice;
         if(profit < startDist) continue;
         double newSl = NormalizeDouble(tick.bid - trailDist, digits);
         if(newSl > currentSl && (tick.bid - newSl) >= minStopDist)
            trade.PositionModify(ticket, newSl, currentTp);
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double profit = openPrice - tick.ask;
         if(profit < startDist) continue;
         double newSl   = NormalizeDouble(tick.ask + trailDist, digits);
         bool   haveSl  = currentSl > 0.0;
         if((!haveSl || newSl < currentSl) && (newSl - tick.ask) >= minStopDist)
            trade.PositionModify(ticket, newSl, currentTp);
      }
   }
}
//+------------------------------------------------------------------+
