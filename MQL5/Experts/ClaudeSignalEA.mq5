//+------------------------------------------------------------------+
//|                                          ClaudeSignalEA.mq5       |
//|                                                                    |
//| Two-file bridge between MQL5 and an external (Claude-driven)      |
//| Python analyst implementing the XTR micro-scalp signal spec       |
//| (python/CLAUDE_SIGNAL_PIPELINE.md). This EA does NOT decide trade |
//| direction itself - see MQL5/Experts/XAUUSD_Confluence_EA.mq5 for  |
//| a self-contained indicator strategy. Instead:                     |
//|                                                                    |
//|  1. EXPORT  - every InpExportIntervalSec seconds, writes:          |
//|       - a metadata header (symbol, digits, tick value/size,       |
//|         volume min/max/step, bid/ask/spread, account equity)      |
//|       - EMA9/EMA21/RSI14/MACD-histogram for M5, M15 and H1        |
//|         (plus ADX14/ATR14/Bollinger(20,2) for M5 only), all read  |
//|         from MQL5's own indicator buffers on the last CLOSED bar  |
//|         so Python never has to reconstruct them from a short bar  |
//|         window                                                     |
//|       - the last 10 M5 MACD-histogram values (for the "re-        |
//|         expansion" gate in the trend-continuation setup)          |
//|       - the last InpBarsM1/M5/M15/H1 raw OHLC bars per timeframe  |
//|         (for structural-swing / liquidity-sweep detection)        |
//|       - m5_dir/m15_dir/h1_dir (BULLISH/BEARISH/MIXED, the same     |
//|         rule Python's direction_for() applies) and htf_align       |
//|         (BUY/SELL/NONE - BUY/SELL when >= 2 of the 3 agree). These |
//|         read the CURRENT, still-forming bar on each timeframe (the |
//|         live reading in MT5 right now), unlike everything else in  |
//|         this export which uses the last CLOSED bar - deliberately: |
//|         this is a COST pre-filter, not a trading rule. By default  |
//|         Python skips the Claude API call entirely when htf_align   |
//|         is NONE, before spending anything, and re-evaluates fresh  |
//|         every cycle regardless, so reacting to a value that can    |
//|         still move before the bar closes is fine here - no trade   |
//|         rides on it, unlike the closed-bar indicators below.       |
//|         Computed here, not recomputed independently in Python, so  |
//|         there is exactly one implementation of the rule to keep    |
//|         consistent - see python/CLAUDE_SIGNAL_PIPELINE.md for the  |
//|         cost/performance trade-off (it also skips setups, e.g.     |
//|         RSI-extreme bounces and liquidity sweeps, that are often   |
//|         contrarian to the higher timeframes by design).            |
//|     to a single plain-text file (InpExportFileName).              |
//|  2. Python reads that file and asks Claude to analyze it in real  |
//|     time - direction, setup rationale, stop and target are        |
//|     Claude's own read of the live data, informed by the XTR setup |
//|     archetypes and general trading principles as knowledge, not   |
//|     decided by a fixed if/else rule table. Python keeps only risk |
//|     *containment* mechanical: a sanity band on the proposed stop  |
//|     distance, a same-side-of-price check, the two-loss standdown  |
//|     per setup type, the 10-minute time-decay, and position-sizing |
//|     arithmetic from account equity and risk %. It writes the      |
//|     result to a second plain-text file (InpSignalFileName). See   |
//|     python/claude_signal_bot.py.                                  |
//|  3. EXECUTE - every InpSignalPollSec seconds, this EA reads that   |
//|     signal file and, if it carries a new signal id, opens the     |
//|     trade at the given SL/TP (rejecting anything malformed, on    |
//|     the wrong side of price, or inside the broker's minimum stop  |
//|     distance). The setup type travels in the order comment so the |
//|     outcome can be attributed back to it when the position closes.|
//|  4. TRAIL   - every tick, open positions opened by this EA (magic |
//|     number) are trailed by a fixed InpTrailingUSD amount, in      |
//|     account-currency dollars, converted to a price distance via   |
//|     the symbol's tick value/size and the position's own volume,   |
//|     so "$4 trailing stop" means $4 regardless of instrument or    |
//|     lot size. The stop only ever tightens.                        |
//|  5. OUTCOME - OnTradeTransaction watches for this EA's positions   |
//|     closing (SL/TP/manual/time-decay - all the same to it) and    |
//|     appends a WIN/LOSS record per setup type to InpOutcomeFileName |
//|     so Python's two-loss standdown rule (spec section 10) has     |
//|     something to read.                                             |
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
//| This bridges live trade execution to an LLM-assisted analysis      |
//| pipeline. The mechanical rules are deterministic, but a malformed  |
//| or malicious signal file could in principle instruct a trade -    |
//| this EA validates prices, sides and stop distances defensively,   |
//| but no validation replaces careful demo-testing. No trading       |
//| system can guarantee profit. Backtest and forward-test on a demo  |
//| account before risking real capital, and only risk money you can  |
//| afford to lose.                                                   |
//+------------------------------------------------------------------+
#property copyright "ClaudeSignalEA"
#property link      ""
#property version   "2.00"
#property strict
#property description "File-bridge EA for the XTR micro-scalp pipeline: exports M1/M5/M15/H1 indicator+bar data for an external (Claude-assisted) analyst, executes the BUY/SELL/SL/TP signals it writes back, logs per-setup-type outcomes, and trails a fixed-dollar stop. Educational use - demo-test thoroughly before risking real capital."

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
input int     InpBarsM1             = 30;           // M1 bars to export (entry timing / sweep detection)
input int     InpBarsM5             = 30;           // M5 bars to export (primary signal timeframe)
input int     InpBarsM15            = 30;           // M15 bars to export (trend-alignment filter)
input int     InpBarsH1             = 30;           // H1 bars to export (trend-alignment filter)
input int     InpMacdHistBarsM5     = 10;           // M5 MACD-histogram history exported (re-expansion gate)
input int     InpExportIntervalSec  = 60;           // Seconds between exports ("every minute")

input group "=== Trade Signals (Python -> MQL5) ==="
input string  InpSignalFileName     = "claude_trade_signals.txt"; // Signal file name
input string  InpAckFileName        = "claude_trade_ack.txt";     // Execution-ack log (appended)
input string  InpOutcomeFileName    = "claude_trade_outcomes.txt";// Per-position outcome log (appended)
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

// Indicator handles: M5 has the full set (§1), M15/H1 only need EMA/RSI/MACD.
int hEma9M5, hEma21M5, hRsiM5, hMacdM5, hAdxM5, hAtrM5, hBandsM5;
int hEma9M15, hEma21M15, hRsiM15, hMacdM15;
int hEma9H1, hEma21H1, hRsiH1, hMacdH1;

// Parallel arrays mapping an open position (by position id) back to the
// setup type / signal id it was opened for, so OnTradeTransaction can
// attribute a close to a setup type for the two-loss standdown rule.
// In-memory only: a terminal/EA restart with positions still open will
// log their eventual close as setup_type "unknown" (documented limitation).
long   g_posId[];
long   g_posSignal[];
string g_posSetup[];
bool   g_posBuy[];

//+------------------------------------------------------------------+
int CreateHandle(int h, string what)
{
   if(h == INVALID_HANDLE)
      PrintFormat("ClaudeSignalEA: failed to create indicator handle for %s, err=%d", what, GetLastError());
   return(h);
}

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

   hEma9M5   = CreateHandle(iMA(_Symbol, PERIOD_M5, 9, 0, MODE_EMA, PRICE_CLOSE), "EMA9 M5");
   hEma21M5  = CreateHandle(iMA(_Symbol, PERIOD_M5, 21, 0, MODE_EMA, PRICE_CLOSE), "EMA21 M5");
   hRsiM5    = CreateHandle(iRSI(_Symbol, PERIOD_M5, 14, PRICE_CLOSE), "RSI14 M5");
   hMacdM5   = CreateHandle(iMACD(_Symbol, PERIOD_M5, 12, 26, 9, PRICE_CLOSE), "MACD M5");
   hAdxM5    = CreateHandle(iADX(_Symbol, PERIOD_M5, 14), "ADX14 M5");
   hAtrM5    = CreateHandle(iATR(_Symbol, PERIOD_M5, 14), "ATR14 M5");
   hBandsM5  = CreateHandle(iBands(_Symbol, PERIOD_M5, 20, 0, 2.0, PRICE_CLOSE), "Bands M5");

   hEma9M15  = CreateHandle(iMA(_Symbol, PERIOD_M15, 9, 0, MODE_EMA, PRICE_CLOSE), "EMA9 M15");
   hEma21M15 = CreateHandle(iMA(_Symbol, PERIOD_M15, 21, 0, MODE_EMA, PRICE_CLOSE), "EMA21 M15");
   hRsiM15   = CreateHandle(iRSI(_Symbol, PERIOD_M15, 14, PRICE_CLOSE), "RSI14 M15");
   hMacdM15  = CreateHandle(iMACD(_Symbol, PERIOD_M15, 12, 26, 9, PRICE_CLOSE), "MACD M15");

   hEma9H1   = CreateHandle(iMA(_Symbol, PERIOD_H1, 9, 0, MODE_EMA, PRICE_CLOSE), "EMA9 H1");
   hEma21H1  = CreateHandle(iMA(_Symbol, PERIOD_H1, 21, 0, MODE_EMA, PRICE_CLOSE), "EMA21 H1");
   hRsiH1    = CreateHandle(iRSI(_Symbol, PERIOD_H1, 14, PRICE_CLOSE), "RSI14 H1");
   hMacdH1   = CreateHandle(iMACD(_Symbol, PERIOD_H1, 12, 26, 9, PRICE_CLOSE), "MACD H1");

   int handles[] = {hEma9M5, hEma21M5, hRsiM5, hMacdM5, hAdxM5, hAtrM5, hBandsM5,
                     hEma9M15, hEma21M15, hRsiM15, hMacdM15,
                     hEma9H1, hEma21H1, hRsiH1, hMacdH1};
   for(int i = 0; i < ArraySize(handles); i++)
      if(handles[i] == INVALID_HANDLE)
         return(INIT_FAILED);

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
   int handles[] = {hEma9M5, hEma21M5, hRsiM5, hMacdM5, hAdxM5, hAtrM5, hBandsM5,
                     hEma9M15, hEma21M15, hRsiM15, hMacdM15,
                     hEma9H1, hEma21H1, hRsiH1, hMacdH1};
   for(int i = 0; i < ArraySize(handles); i++)
      if(handles[i] != INVALID_HANDLE)
         IndicatorRelease(handles[i]);
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
//| Read one indicator buffer value at `shift` (default 1 = last       |
//| CLOSED bar, avoiding repaint). Returns EMPTY_VALUE on failure.     |
//+------------------------------------------------------------------+
double IndicatorValue(const int handle, const int bufferIndex = 0, const int shift = 1)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(handle == INVALID_HANDLE || CopyBuffer(handle, bufferIndex, shift, 1, buf) != 1)
      return(EMPTY_VALUE);
   return(buf[0]);
}

//+------------------------------------------------------------------+
//| Format one double for the export file: "NA" for EMPTY_VALUE.      |
//+------------------------------------------------------------------+
string Fmt(const double value)
{
   if(value == EMPTY_VALUE) return("NA");
   return(StringFormat("%.5f", value));
}

//+------------------------------------------------------------------+
//| BULLISH/BEARISH/MIXED for one timeframe, from its own EMA9/EMA21/ |
//| RSI14/MACD-histogram - the exact same rule claude_signal_bot.py's |
//| direction_for() applies to these same exported values. Computed   |
//| here too (not left to Python alone) so the mechanical 2-of-3 HTF  |
//| pre-filter below has one authoritative answer, exported plainly,  |
//| rather than two independent implementations that could drift.    |
//|                                                                    |
//| shift=0 (default here) reads the CURRENT, still-forming bar - the  |
//| live MT5 reading right now, which is what the pre-filter below     |
//| should react to (it only ever decides whether to spend an API      |
//| call THIS cycle, re-evaluated fresh next cycle regardless, so a    |
//| value that can still move before the bar closes is fine - there is |
//| no trade riding on it). This is a deliberate difference from the   |
//| ##INDICATORS values Claude actually analyzes (WriteIndicatorRow),  |
//| which stay on shift=1, the last CLOSED bar, precisely because a    |
//| real trading decision must not repaint. Pass shift=1 explicitly if |
//| a closed-bar direction is ever needed elsewhere.                   |
//+------------------------------------------------------------------+
string DirectionLabel(const int hEma9, const int hEma21, const int hRsi, const int hMacd, const int shift = 0)
{
   double ema9  = IndicatorValue(hEma9, 0, shift);
   double ema21 = IndicatorValue(hEma21, 0, shift);
   double rsi   = IndicatorValue(hRsi, 0, shift);
   double macdMain = IndicatorValue(hMacd, 0, shift);
   double macdSig  = IndicatorValue(hMacd, 1, shift);
   if(ema9 == EMPTY_VALUE || ema21 == EMPTY_VALUE || rsi == EMPTY_VALUE ||
      macdMain == EMPTY_VALUE || macdSig == EMPTY_VALUE)
      return("MIXED");
   double macdHist = macdMain - macdSig;
   if(ema9 > ema21 && rsi > 50.0 && macdHist > 0.0) return("BULLISH");
   if(ema9 < ema21 && rsi < 50.0 && macdHist < 0.0) return("BEARISH");
   return("MIXED");
}

//+------------------------------------------------------------------+
//| The mechanical pre-filter itself: BUY/SELL when at least 2 of the |
//| 3 timeframes agree on a direction, NONE otherwise. This does NOT  |
//| decide whether to trade - claude_signal_bot.py still makes that   |
//| call. It decides whether the cycle is worth spending an API call  |
//| on at all; see python/CLAUDE_SIGNAL_PIPELINE.md for the cost/     |
//| performance trade-off (it also skips setups - RSI-extreme bounces |
//| and liquidity-sweep reversals - that are often contrarian to the  |
//| higher timeframes by design, not just noise).                     |
//+------------------------------------------------------------------+
string HtfAlignment(const string m5Dir, const string m15Dir, const string h1Dir)
{
   int bulls = 0, bears = 0;
   if(m5Dir  == "BULLISH") bulls++; else if(m5Dir  == "BEARISH") bears++;
   if(m15Dir == "BULLISH") bulls++; else if(m15Dir == "BEARISH") bears++;
   if(h1Dir  == "BULLISH") bulls++; else if(h1Dir  == "BEARISH") bears++;
   if(bulls >= 2) return("BUY");
   if(bears >= 2) return("SELL");
   return("NONE");
}

//+------------------------------------------------------------------+
//| Write one timeframe's indicator row. adx/atr/bandsUpper/bandsLower|
//| are only meaningful for M5 (§1) - pass EMPTY_VALUE for the others.|
//+------------------------------------------------------------------+
void WriteIndicatorRow(const int handle, const string tf, const int hEma9, const int hEma21,
                        const int hRsi, const int hMacd, const double adx, const double atr,
                        const double bandsUpper, const double bandsLower)
{
   double ema9  = IndicatorValue(hEma9);
   double ema21 = IndicatorValue(hEma21);
   double rsi   = IndicatorValue(hRsi);
   double macdMain = IndicatorValue(hMacd, 0);
   double macdSig  = IndicatorValue(hMacd, 1);
   double macdHist = (macdMain == EMPTY_VALUE || macdSig == EMPTY_VALUE) ? EMPTY_VALUE : (macdMain - macdSig);

   FileWrite(handle, StringFormat("%s,%s,%s,%s,%s,%s,%s,%s,%s",
             tf, Fmt(ema9), Fmt(ema21), Fmt(rsi), Fmt(macdHist),
             Fmt(adx), Fmt(atr), Fmt(bandsUpper), Fmt(bandsLower)));
}

//+------------------------------------------------------------------+
//| Export raw OHLC bars for one timeframe.                           |
//+------------------------------------------------------------------+
void WriteBarsSection(const int handle, const string tf, const ENUM_TIMEFRAMES period, const int count)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, period, 0, count, rates);
   FileWrite(handle, "##BARS_" + tf);
   FileWrite(handle, "time,open,high,low,close");
   if(copied <= 0)
   {
      PrintFormat("ClaudeSignalEA: CopyRates(%s) failed, err=%d", tf, GetLastError());
      return;
   }
   for(int i = copied - 1; i >= 0; i--)   // oldest -> newest
      FileWrite(handle, StringFormat("%s,%.5f,%.5f,%.5f,%.5f",
                TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
                rates[i].open, rates[i].high, rates[i].low, rates[i].close));
}

//+------------------------------------------------------------------+
//| Export the full XTR data set: metadata header, per-timeframe       |
//| indicator snapshot, M5 MACD-histogram history, and raw bars for   |
//| M1/M5/M15/H1. Writes to a .tmp file first and swaps it in, so     |
//| Python never sees a half-written file mid-read.                   |
//+------------------------------------------------------------------+
void ExportChartData()
{
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

   int    digits      = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point       = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double tickValue   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize    = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double volMin      = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volMax      = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double volStep     = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   int    spreadPts   = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   string exportedStr = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
   StringReplace(exportedStr, " ", "T");

   // shift=0: the CURRENT live/forming bar on each timeframe - "the current
   // reading in MT5" right now, not the last closed bar (see DirectionLabel).
   string m5Dir  = DirectionLabel(hEma9M5, hEma21M5, hRsiM5, hMacdM5, 0);
   string m15Dir = DirectionLabel(hEma9M15, hEma21M15, hRsiM15, hMacdM15, 0);
   string h1Dir  = DirectionLabel(hEma9H1, hEma21H1, hRsiH1, hMacdH1, 0);
   string htfAlign = HtfAlignment(m5Dir, m15Dir, h1Dir);

   FileWrite(handle, StringFormat(
      "#symbol=%s digits=%d point=%.5f tick_value=%.5f tick_size=%.5f volume_min=%.2f "
      "volume_max=%.2f volume_step=%.2f bid=%.5f ask=%.5f spread=%d equity=%.2f "
      "m5_dir=%s m15_dir=%s h1_dir=%s htf_align=%s exported=%s",
      _Symbol, digits, point, tickValue, tickSize, volMin, volMax, volStep,
      tick.bid, tick.ask, spreadPts, equity, m5Dir, m15Dir, h1Dir, htfAlign, exportedStr));

   // --- §1/§2 indicator snapshot, last CLOSED bar, per timeframe ---
   FileWrite(handle, "##INDICATORS");
   FileWrite(handle, "tf,ema9,ema21,rsi14,macd_hist,adx14,atr14,bb_upper,bb_lower");
   WriteIndicatorRow(handle, "M5", hEma9M5, hEma21M5, hRsiM5, hMacdM5,
                     IndicatorValue(hAdxM5, 0), IndicatorValue(hAtrM5, 0),
                     IndicatorValue(hBandsM5, 1), IndicatorValue(hBandsM5, 2));
   WriteIndicatorRow(handle, "M15", hEma9M15, hEma21M15, hRsiM15, hMacdM15,
                     EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE);
   WriteIndicatorRow(handle, "H1", hEma9H1, hEma21H1, hRsiH1, hMacdH1,
                     EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE);

   // --- MACD-histogram history for the §4.2 re-expansion gate ---
   FileWrite(handle, "##MACD_HIST_M5");
   FileWrite(handle, "value");
   int n = InpMacdHistBarsM5;
   double mainArr[], sigArr[];
   ArraySetAsSeries(mainArr, true);
   ArraySetAsSeries(sigArr, true);
   int copiedMain = CopyBuffer(hMacdM5, 0, 1, n, mainArr);
   int copiedSig  = CopyBuffer(hMacdM5, 1, 1, n, sigArr);
   int copiedHist = MathMin(copiedMain, copiedSig);
   for(int i = copiedHist - 1; i >= 0; i--)   // oldest -> newest
      FileWrite(handle, StringFormat("%.5f", mainArr[i] - sigArr[i]));

   // --- §1 raw bars, oldest -> newest, per timeframe ---
   WriteBarsSection(handle, "M1", PERIOD_M1, InpBarsM1);
   WriteBarsSection(handle, "M5", PERIOD_M5, InpBarsM5);
   WriteBarsSection(handle, "M15", PERIOD_M15, InpBarsM15);
   WriteBarsSection(handle, "H1", PERIOD_H1, InpBarsH1);

   FileClose(handle);

   FileDelete(InpExportFileName, CommonFlag());
   if(!FileMove(tmpName, CommonFlag(), InpExportFileName, CommonFlag()))
      PrintFormat("ClaudeSignalEA: FileMove %s -> %s failed, err=%d", tmpName, InpExportFileName, GetLastError());
}

//+------------------------------------------------------------------+
//| Append one line to a common-folder log file.                      |
//+------------------------------------------------------------------+
void AppendLine(const string fileName, const string line)
{
   int handle = FileOpen(fileName, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | CommonFlag());
   if(handle == INVALID_HANDLE) return;
   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle, line);
   FileClose(handle);
}

void WriteAck(const long id, const string status, const string detail)
{
   AppendLine(InpAckFileName, StringFormat("%s,%d,%s,%s",
              TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), id, status, detail));
}

void WriteOutcome(const long signalId, const string setupType, const string direction,
                   const double profit, const string outcome)
{
   AppendLine(InpOutcomeFileName, StringFormat("%s,%d,%s,%s,%.2f,%s",
              TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), signalId, setupType,
              direction, profit, outcome));
}

//+------------------------------------------------------------------+
//| Position -> (signal id, setup type, direction) bookkeeping, so an  |
//| outcome can be attributed to a setup type when the position       |
//| closes. In-memory only (see the g_pos* declaration comment).      |
//+------------------------------------------------------------------+
void RememberPosition(const long positionId, const long signalId, const string setupType, const bool isBuy)
{
   int n = ArraySize(g_posId);
   ArrayResize(g_posId, n + 1);
   ArrayResize(g_posSignal, n + 1);
   ArrayResize(g_posSetup, n + 1);
   ArrayResize(g_posBuy, n + 1);
   g_posId[n]     = positionId;
   g_posSignal[n] = signalId;
   g_posSetup[n]  = setupType;
   g_posBuy[n]    = isBuy;
}

bool ForgetPosition(const long positionId, long &signalId, string &setupType, bool &isBuy)
{
   int n = ArraySize(g_posId);
   for(int i = 0; i < n; i++)
   {
      if(g_posId[i] != positionId) continue;
      signalId  = g_posSignal[i];
      setupType = g_posSetup[i];
      isBuy     = g_posBuy[i];
      for(int j = i; j < n - 1; j++)
      {
         g_posId[j]     = g_posId[j + 1];
         g_posSignal[j] = g_posSignal[j + 1];
         g_posSetup[j]  = g_posSetup[j + 1];
         g_posBuy[j]    = g_posBuy[j + 1];
      }
      ArrayResize(g_posId, n - 1);
      ArrayResize(g_posSignal, n - 1);
      ArrayResize(g_posSetup, n - 1);
      ArrayResize(g_posBuy, n - 1);
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Fires on every deal. We only care about deals that CLOSE a         |
//| position this EA opened, so we can log a WIN/LOSS per setup type. |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if((long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != (long)InpMagicNumber) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;

   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) return;   // only closing deals

   long positionId = (long)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT)
                 + HistoryDealGetDouble(trans.deal, DEAL_SWAP)
                 + HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);

   long   signalId;
   string setupType;
   bool   isBuy;
   if(!ForgetPosition(positionId, signalId, setupType, isBuy))
   {
      signalId  = -1;
      setupType = "unknown";
      isBuy     = (HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_SELL);  // closing sell deal = was a buy
   }

   string outcome = (profit >= 0.0) ? "WIN" : "LOSS";
   WriteOutcome(signalId, setupType, isBuy ? "BUY" : "SELL", profit, outcome);
}

//+------------------------------------------------------------------+
//| Read the signal file and act on it if it carries a fresh id.      |
//| Expected single-line CSV format (see python/claude_signal_bot.py):|
//|   id,symbol,action,lot,sl,tp,timestamp,setup_type,reason          |
//| action is one of BUY | SELL | NONE | CLOSE | CLOSE_ALL | CLOSE_ID |
//| (CLOSE_ID reuses the `lot` column to carry the target signal id -  |
//| see CloseBySignalId - so only that one position is closed, not     |
//| every position this EA holds).                                     |
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
   if(n < 8)
   {
      PrintFormat("ClaudeSignalEA: malformed signal line, ignored: %s", lastLine);
      return;
   }

   long   id        = StringToInteger(parts[0]);
   string symbol     = parts[1];
   string action     = parts[2];
   StringToUpper(action);
   double lot        = StringToDouble(parts[3]);
   double sl         = StringToDouble(parts[4]);
   double tp         = StringToDouble(parts[5]);
   string setupType  = parts[7];
   string reason     = (n > 8) ? parts[8] : "";

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

   if(action == "CLOSE_ID")
   {
      // Targeted close (used for time-decay): the `lot` column is reused to
      // carry the ORIGINAL signal id whose position should be closed, so a
      // stale trade doesn't take fresh, still-legitimate ones down with it.
      long targetSignalId = (long)MathRound(lot);
      bool found = CloseBySignalId(targetSignalId);
      WriteAck(id, found ? "CLOSED" : "SKIPPED",
               StringFormat("target_signal_id=%d%s", targetSignalId, found ? "" : " not found (already closed?)"));
      return;
   }

   if(action != "BUY" && action != "SELL")
   {
      PrintFormat("ClaudeSignalEA: unknown action '%s' in signal id=%d, ignored", action, id);
      WriteAck(id, "REJECTED", "unknown action");
      return;
   }

   ExecuteSignal(id, action == "BUY", lot, sl, tp, setupType, reason);
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
//| Close only the one position opened for `targetSignalId`, looked up   |
//| via the same g_posId/g_posSignal map ExecuteSignal populates and     |
//| OnTradeTransaction consumes. Returns false if no open position is    |
//| currently tracked under that signal id (already closed, or the       |
//| mapping was lost across an EA restart - a documented limitation).    |
//+------------------------------------------------------------------+
bool CloseBySignalId(const long targetSignalId)
{
   int n = ArraySize(g_posSignal);
   for(int i = 0; i < n; i++)
   {
      if(g_posSignal[i] != targetSignalId) continue;
      ulong ticket = (ulong)g_posId[i];
      if(!PositionSelectByTicket(ticket)) continue;   // stale entry; keep scanning
      return(trade.PositionClose(ticket));
   }
   return(false);
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
                    double sl, double tp, const string setupType, const string reason)
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

   string comment = "XTR#" + (string)id + "#" + setupType;
   bool ok = isBuy ? trade.Buy(lots, _Symbol, price, sl, tp, comment)
                   : trade.Sell(lots, _Symbol, price, sl, tp, comment);

   if(ok)
   {
      PrintFormat("ClaudeSignalEA: %s executed. id=%d setup=%s lots=%.2f price=%.5f sl=%.5f tp=%.5f reason=%s",
                  isBuy ? "BUY" : "SELL", id, setupType, lots, price, sl, tp, reason);
      WriteAck(id, "EXECUTED", StringFormat("ticket=%I64u price=%.5f lots=%.2f setup=%s", trade.ResultOrder(), price, lots, setupType));

      long positionId = (long)HistoryDealGetInteger(trade.ResultDeal(), DEAL_POSITION_ID);
      if(positionId != 0)
         RememberPosition(positionId, id, setupType, isBuy);
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
