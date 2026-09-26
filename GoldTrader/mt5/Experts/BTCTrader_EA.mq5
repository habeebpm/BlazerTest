//+------------------------------------------------------------------+
//|                                            BTCTrader_EA.mq5        |
//|                                      part of the GoldTrader package|
//|                                                                    |
//| Exit manager for the BTCUSD instance of GoldTrader. No Telegram    |
//| signals: every BTC entry is decided by app/main.py --profile btc   |
//| (start.bat, BTC_ARGS) - Claude's "full" verdict on the 2-of-3      |
//| legs, SMC structure, M15/H1 alignment, the economic calendar and    |
//| crypto breaking news - and sent under InpMagicNumber with its stop. |
//| This EA manages those positions tick by tick, like UnifiedTrader_EA |
//| does for gold, in units of each trade's OWN risk (R):               |
//|                                                                    |
//|   R      = the distance from the entry to the stop the position    |
//|            was opened with (read from the opening order), so it     |
//|            holds at any BTC price and any ATR-sized stop            |
//|   lock   = at +InpLockR x R profit the stop moves exactly there     |
//|            (profit locked), no broker take-profit                   |
//|   trail  = then InpTrailR x R behind new highs / lows, tightening   |
//|            only                                                     |
//|                                                                    |
//| Gold's $6 stop / $6 lock / $3 trail is the same shape: 1R, +1R,     |
//| 0.5R. Whether a position is locked is read from its own stop every  |
//| tick - nothing to remember across restarts. A stop you move by     |
//| hand is kept until the lock (never loosened by the EA).            |
//|                                                                    |
//| Put it on ONE BTCUSD chart (any timeframe), Algo Trading on. It     |
//| never opens a trade. Remote control lives in UnifiedTrader_EA       |
//| (PauseBtcHab / ResumeBtcHab / PauseHab) - one bot, one poller.      |
//| InpExportCalendar: only when UnifiedTrader_EA is NOT running (it    |
//| already exports the economic calendar both instances read).        |
//|                                                                    |
//| PRICE FILES FOR GOOGLE DRIVE (InpXtrExport), like gold's: closed    |
//| M5/M15/H1 BTCUSD bars (true UTC CSV + manifest) in                  |
//| Common\Files\XTR_Data, copied to InpXtrExportCopyTo (your Drive     |
//| folder; needs "Allow DLL imports"). BTCUSD_M5.csv ... next to        |
//| gold's XAUUSD_ files - Claude reads them from Drive, and            |
//| backtest.py --csv-folder replays them.                              |
//+------------------------------------------------------------------+
#property copyright "BTCTrader_EA"
#property link      ""
#property version   "1.10"
#property strict
#property description "Manages GoldTrader's BTCUSD positions (app/main.py --profile btc): lock at +1R, then trail 0.5R, R = each trade's own opening stop. Never opens a trade. Demo-test with InpDryRun=true first."

#include <Trade\Trade.mqh>
#include <EconCalendar.mqh>
// Copy of the Drive price files to any folder (InpXtrExportCopyTo) uses
// kernel32 CopyFileW. Delete this line for a build with no DLL import.
#define XTR_EXPORT_COPY_DLL
#include <XtrBarExport.mqh>

input group "=== BTC positions (MUST match app/profiles.py 'btc') ==="
input long    InpMagicNumber   = 20260931;  // The BTC instance's magic (profiles.py BTC_MAGIC)
input double  InpLockR         = 1.0;       // Lock the stop at +this x R profit (profiles.py lock_r)
input double  InpTrailR        = 0.5;       // Then trail this x R behind price (profiles.py trail_r)
input bool    InpDryRun        = true;      // Log what would change; do not modify real positions

input group "=== Economic calendar export (only if UnifiedTrader_EA is not running) ==="
input bool    InpExportCalendar     = false;              // Write the calendar file app/main.py reads
input string  InpCalendarExportFile = "econ_calendar.csv"; // MUST match app/config.py econ_calendar_filename
input string  InpNewsCurrencies     = "USD";
input int     InpCalendarRefreshMin = 30;

input group "=== Price export for Google Drive (like gold's) - see XtrBarExport.mqh ==="
input bool   InpXtrExport       = true;        // Write closed M5/M15/H1 bars (UTC CSV + manifest) on every M1 close
input string InpXtrExportFolder = "XTR_Data";  // Folder inside Common\Files (the same as gold's is fine)
input string InpXtrExportName   = "BTCUSD";    // File name prefix / manifest symbol (BTCUSD_M5.csv ...)
input int    InpXtrExportBars   = 5000;        // Closed bars per file (50-5000): 5000 = 17 days M5, 52 days M15, 208 days H1
input bool   InpXtrExportM1     = false;       // Also write <name>_M1.csv
input string InpXtrExportCopyTo = "";          // Also copy every file to this folder, e.g. G:\My Drive\MyMQChartDrive (needs "Allow DLL imports")

CTrade trade;

// R per position, read once from the opening order (ticket -> distance).
ulong  g_rTicket[];
double g_rDist[];

//+------------------------------------------------------------------+
//| The distance from the entry to the stop the position was opened    |
//| with: the first order of this position that carried a stop. 0 when |
//| the history has none (then the current stop is used while it is     |
//| still on the loss side - see RiskDistance()).                      |
//+------------------------------------------------------------------+
double OpeningStopDistance(ulong ticket, double openPrice)
{
   long posId = 0;
   if(PositionSelectByTicket(ticket))
      posId = PositionGetInteger(POSITION_IDENTIFIER);
   if(posId == 0 || !HistorySelectByPosition(posId))
      return(0.0);
   datetime first = 0;
   double   sl    = 0.0;
   for(int i = 0; i < HistoryOrdersTotal(); i++)
   {
      ulong o = HistoryOrderGetTicket(i);
      if(o == 0) continue;
      double osl = HistoryOrderGetDouble(o, ORDER_SL);
      datetime setup = (datetime)HistoryOrderGetInteger(o, ORDER_TIME_SETUP);
      if(osl > 0.0 && (first == 0 || setup < first))
      {
         first = setup;
         sl    = osl;
      }
   }
   return(sl > 0.0 ? MathAbs(openPrice - sl) : 0.0);
}

double RiskDistance(ulong ticket, double openPrice, double currentSl, bool isBuy)
{
   for(int i = 0; i < ArraySize(g_rTicket); i++)
      if(g_rTicket[i] == ticket)
         return(g_rDist[i]);
   double r = OpeningStopDistance(ticket, openPrice);
   if(r <= 0.0 && currentSl > 0.0)
   {
      // No order history (e.g. a restart right after a history purge): the
      // current stop is the opening one as long as it is still a loss stop.
      double d = isBuy ? openPrice - currentSl : currentSl - openPrice;
      if(d > 0.0)
         r = d;
   }
   if(r <= 0.0)
      return(0.0);
   int n = ArraySize(g_rTicket);
   ArrayResize(g_rTicket, n + 1);
   ArrayResize(g_rDist, n + 1);
   g_rTicket[n] = ticket;
   g_rDist[n]   = r;
   PrintFormat("BTCTrader_EA: ticket %I64u - R = %.2f (lock at +%.2f, trail %.2f)", ticket, r,
               r * InpLockR, r * InpTrailR);
   return(r);
}

// Drops cached R values of positions that are no longer open.
void PruneRiskCache()
{
   int keep = 0;
   for(int i = 0; i < ArraySize(g_rTicket); i++)
   {
      if(!PositionSelectByTicket(g_rTicket[i]))
         continue;
      g_rTicket[keep] = g_rTicket[i];
      g_rDist[keep]   = g_rDist[i];
      keep++;
   }
   ArrayResize(g_rTicket, keep);
   ArrayResize(g_rDist, keep);
}

//+------------------------------------------------------------------+
//| Lock at +InpLockR x R, then trail InpTrailR x R; clears any broker |
//| take-profit (a standing TP would race the lock/trail and win).     |
//+------------------------------------------------------------------+
void ManagePosition(ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if((long)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber || PositionGetString(POSITION_SYMBOL) != _Symbol)
      return;
   long   type      = PositionGetInteger(POSITION_TYPE);
   bool   isBuy     = (type == POSITION_TYPE_BUY);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentSl = PositionGetDouble(POSITION_SL);
   double currentTp = PositionGetDouble(POSITION_TP);
   double r = RiskDistance(ticket, openPrice, currentSl, isBuy);
   if(r <= 0.0)
      return;                                   // no stop known: nothing safe to do
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   int    digits      = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point       = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double lockDist    = r * InpLockR;
   double trailDist   = r * InpTrailR;
   bool   changeSl    = false;
   double newSl       = currentSl;

   if(isBuy)
   {
      double lockLevel = NormalizeDouble(openPrice + lockDist, digits);
      bool armed = (currentSl > 0.0 && currentSl >= lockLevel - point);
      if(!armed)
      {
         if(tick.bid - openPrice >= lockDist && (tick.bid - lockLevel) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.bid - trailDist, digits);
         if(candidate > currentSl + point && (tick.bid - candidate) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }
   else
   {
      double lockLevel = NormalizeDouble(openPrice - lockDist, digits);
      bool armed = (currentSl > 0.0 && currentSl <= lockLevel + point);
      if(!armed)
      {
         if(openPrice - tick.ask >= lockDist && (lockLevel - tick.ask) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.ask + trailDist, digits);
         if(candidate < currentSl - point && (candidate - tick.ask) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }

   if(!changeSl && currentTp == 0.0)
      return;
   double slToSend = changeSl ? newSl : currentSl;
   if(InpDryRun)
   {
      PrintFormat("BTCTrader_EA: [DRY-RUN] would modify %s ticket %I64u sl %.2f -> %.2f%s",
                  isBuy ? "BUY" : "SELL", ticket, currentSl, slToSend,
                  currentTp != 0.0 ? " (clearing broker TP)" : "");
      return;
   }
   if(!trade.PositionModify(ticket, slToSend, 0.0))
      PrintFormat("BTCTrader_EA: modify ticket %I64u failed (retcode=%d %s)", ticket,
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());
   else if(changeSl)
      PrintFormat("BTCTrader_EA: %s ticket %I64u stop %.2f -> %.2f", isBuy ? "BUY" : "SELL", ticket,
                  currentSl, newSl);
}

void ManageAll()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      ManagePosition(ticket);
   }
}

int OnInit()
{
   if(InpMagicNumber <= 0 || InpLockR <= 0.0 || InpTrailR <= 0.0)
   {
      Print("BTCTrader_EA: InpMagicNumber, InpLockR and InpTrailR must all be > 0.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   string sym = _Symbol;
   StringToUpper(sym);
   if(StringFind(sym, "BTC") < 0 && StringFind(sym, "XBT") < 0)
      PrintFormat("BTCTrader_EA: WARNING - this chart is %s, not Bitcoin. Put the EA on your BTCUSD "
                  "chart (it only manages positions of its own chart's symbol).", _Symbol);
   if(InpXtrExport && (InpXtrExportBars < 50 || InpXtrExportBars > 5000
                       || StringLen(InpXtrExportFolder) == 0 || StringLen(InpXtrExportName) == 0))
   {
      Print("BTCTrader_EA: InpXtrExportBars must be 50-5000 and InpXtrExportFolder/InpXtrExportName "
            "non-empty (or set InpXtrExport=false).");
      return(INIT_PARAMETERS_INCORRECT);
   }
   XtrExpReset();
   if(InpXtrExport && StringLen(InpXtrExportCopyTo) > 0 && !MQLInfoInteger(MQL_DLLS_ALLOWED))
      PrintFormat("BTCTrader_EA: InpXtrExportCopyTo=%s needs \"Allow DLL imports\" (EA Common tab) - "
                  "until then files stay in Common\\Files\\%s only.", InpXtrExportCopyTo, InpXtrExportFolder);
   trade.SetExpertMagicNumber(InpMagicNumber);
   EventSetTimer(1);
   PrintFormat("BTCTrader_EA: managing %s positions with magic %I64d - lock at +%.2fR, trail %.2fR "
               "(R = each trade's opening stop)%s. Entries come from start.bat (BTC lines).",
               _Symbol, InpMagicNumber, InpLockR, InpTrailR, InpDryRun ? " - DRY-RUN, nothing modified" : "");
   ManageAll();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   ManageAll();
   if(InpExportCalendar)
      EconMaybeExport(InpCalendarExportFile, InpNewsCurrencies, InpCalendarRefreshMin);
   XtrExpMaybeExport(InpXtrExport, _Symbol, InpXtrExportFolder, InpXtrExportName, InpXtrExportBars,
                     InpXtrExportM1, InpXtrExportCopyTo);
}

// Weekend / quiet-market ticks can be minutes apart: the timer keeps the
// cache tidy and the calendar and price exports going between them.
void OnTimer()
{
   PruneRiskCache();
   ManageAll();
   if(InpExportCalendar)
      EconMaybeExport(InpCalendarExportFile, InpNewsCurrencies, InpCalendarRefreshMin);
   XtrExpMaybeExport(InpXtrExport, _Symbol, InpXtrExportFolder, InpXtrExportName, InpXtrExportBars,
                     InpXtrExportM1, InpXtrExportCopyTo);
}
