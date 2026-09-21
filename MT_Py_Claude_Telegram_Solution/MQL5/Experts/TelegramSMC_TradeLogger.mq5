//+------------------------------------------------------------------+
//|                                  TelegramSMC_TradeLogger.mq5      |
//|                                                                    |
//| Logs every open and close of a position tagged with               |
//| InpMagicNumber on InpSymbol to TelegramSMC_Results.csv (this       |
//| terminal's MQL5\Files, or the shared Common\Files folder if        |
//| InpUseCommonFolder is set) - one row per OPEN event and one row    |
//| per CLOSE event, linked by position_id and order_ticket.           |
//|                                                                    |
//| DELIBERATELY A SEPARATE EA from TelegramSMC_Copier.mq5: it never   |
//| places, modifies, or closes a single order, and it never talks to  |
//| Telegram. It only WATCHES trade history for the given magic number |
//| via OnTradeTransaction, so it keeps recording results (or can be   |
//| attached/removed) independently of whatever is actually trading -  |
//| the Copier EA, a different EA, or a person clicking buttons with   |
//| the same magic number and symbol.                                  |
//|                                                                    |
//| To pair it with TelegramSMC_Copier.mq5, set InpMagicNumber and     |
//| InpSymbol to match that EA's inputs (20260918 / XAUUSD by          |
//| default) and leave InpSourceLabel at its default "Telegram_Sig" -   |
//| or point this same EA at a different system's magic/symbol (e.g.   |
//| the Claude-SMC Trader's 20260921) and set InpSourceLabel to         |
//| "Claude_Sig", so every row's source column still says which system |
//| actually opened the trade, whichever one you're watching. The close |
//| reason comes straight from MT5's own deal                          |
//| history (SL / TP / client / expert / stop-out, via DEAL_REASON) -   |
//| this EA never has to guess it from price. Duration and price move  |
//| are computed by looking up the position's own opening deal in      |
//| history, which works correctly even for a position that was        |
//| already open before this EA was attached.                          |
//|                                                                    |
//| Pairs with TelegramSMC_Signals.csv (written by                     |
//| TelegramSMC_Copier.mq5): join the two on order_ticket if you want   |
//| the original signal text next to its eventual result - nothing     |
//| here does that merge automatically.                                 |
//|                                                                    |
//| HONEST LIMITATIONS                                                  |
//| ------------------------------------------------------------------ |
//| No OPEN row is written for a position that was already open        |
//| BEFORE this EA was attached (there is no on-init backfill) - but    |
//| its eventual CLOSE row is still complete, since everything in it    |
//| is computed from history at the moment of the close, not from      |
//| anything remembered in memory. A partial close is logged as its    |
//| own CLOSE row with that deal's own (smaller) volume and profit,     |
//| rather than being merged into a single final row per position.     |
//+------------------------------------------------------------------+
#property copyright "TelegramSMC_TradeLogger"
#property link      ""
#property version   "1.00"
#property strict
#property description "Standalone trade journal: logs every open/close of a position tagged with InpMagicNumber to TelegramSMC_Results.csv, independent of whatever EA (or manual trading) actually places the orders."

#include <TelegramSMC_Common.mqh>

input group "=== What to log ==="
input string InpSymbol          = "XAUUSD";     // Symbol to log (match the trading EA's symbol)
input ulong  InpMagicNumber     = 20260918;     // Magic number to log (match the trading EA's)
input string InpSourceLabel     = "Telegram_Sig"; // Free-text tag written to every row's source column - this EA is generic (see header), so re-point it at a different system's magic number (e.g. the Claude-SMC Trader's 20260921) and set this to "Claude_Sig" to keep that log distinguishable too
input bool   InpUseCommonFolder = false;        // Write to the shared Common\Files folder instead of this terminal's Files

//+------------------------------------------------------------------+
//| Expert initialization - purely event-driven, no timer/tick work.  |
//+------------------------------------------------------------------+
int OnInit()
{
   PrintFormat("TelegramSMC_TradeLogger: watching symbol=%s magic=%I64u source=%s -> %s\\%s",
               InpSymbol, InpMagicNumber, InpSourceLabel,
               InpUseCommonFolder ? "Common\\Files" : "MQL5\\Files", TSMC_RESULTS_FILE);
   PrintFormat("TelegramSMC_TradeLogger: note - positions already open before this EA was attached "
               "get no OPEN row, but their CLOSE row will still be complete.");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {}

//+------------------------------------------------------------------+
//| Finds the deal that opened this position from history - works    |
//| even for a position opened before this EA was attached.           |
//+------------------------------------------------------------------+
bool FindPositionOpenDeal(long positionId, double &openPrice, datetime &openTime, long &openDealType)
{
   if(!HistorySelectByPosition(positionId)) return(false);
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong dt = HistoryDealGetTicket(i);
      if(dt == 0) continue;
      if(HistoryDealGetInteger(dt, DEAL_ENTRY) == DEAL_ENTRY_IN)
      {
         openPrice    = HistoryDealGetDouble(dt, DEAL_PRICE);
         openTime     = (datetime)HistoryDealGetInteger(dt, DEAL_TIME);
         openDealType = HistoryDealGetInteger(dt, DEAL_TYPE);
         return(true);
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Appends one row to TelegramSMC_Results.csv.                       |
//+------------------------------------------------------------------+
void LogResultRow(const string &event, long positionId, long orderTicket, const string &direction,
                   double volume, double price, double sl, double tp, double profit, double swap,
                   double commission, const string &closeReason, double durationMin,
                   double priceMove, const string &comment)
{
   int handle = TsmcOpenCsvForAppend(TSMC_RESULTS_FILE, TSMC_RESULTS_HEADER, InpUseCommonFolder);
   if(handle == INVALID_HANDLE) return;

   double net = profit + swap + commission;
   string ts  = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
   string line = ts + "," +
                 TsmcCsvField(event) + "," +
                 IntegerToString(positionId) + "," +
                 IntegerToString(orderTicket) + "," +
                 TsmcCsvField(InpSymbol) + "," +
                 IntegerToString((long)InpMagicNumber) + "," +
                 TsmcCsvField(direction) + "," +
                 DoubleToString(volume, 2) + "," +
                 DoubleToString(price, 2) + "," +
                 DoubleToString(sl, 2) + "," +
                 DoubleToString(tp, 2) + "," +
                 DoubleToString(profit, 2) + "," +
                 DoubleToString(swap, 2) + "," +
                 DoubleToString(commission, 2) + "," +
                 DoubleToString(net, 2) + "," +
                 TsmcCsvField(closeReason) + "," +
                 DoubleToString(durationMin, 1) + "," +
                 DoubleToString(priceMove, 2) + "," +
                 TsmcCsvField(comment) + "," +
                 TsmcCsvField(InpSourceLabel);

   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Fires on every new deal (and other trade events); a new deal is   |
//| how MT5 represents both a position opening and a position (or     |
//| part of it) closing.                                              |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request,
                         const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   ulong dealTicket = trans.deal;
   if(!HistoryDealSelect(dealTicket)) return;

   string symbol = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
   if(symbol != InpSymbol) return;
   long magic = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);
   if((ulong)magic != InpMagicNumber) return;

   long   entryType   = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
   long   positionId  = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
   long   orderTicket = HistoryDealGetInteger(dealTicket, DEAL_ORDER);
   long   dealType    = HistoryDealGetInteger(dealTicket, DEAL_TYPE);
   double price       = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
   double volume      = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
   string comment     = HistoryDealGetString(dealTicket, DEAL_COMMENT);

   if(entryType == DEAL_ENTRY_IN)
   {
      string direction = (dealType == DEAL_TYPE_BUY) ? "BUY" : "SELL";
      double sl = 0.0, tp = 0.0;
      if(PositionSelectByTicket(positionId))
      {
         sl = PositionGetDouble(POSITION_SL);
         tp = PositionGetDouble(POSITION_TP);
      }
      PrintFormat("TelegramSMC_TradeLogger: OPEN position %I64d (%s) order %I64d - %.2f lots @ %.2f",
                  positionId, direction, orderTicket, volume, price);
      LogResultRow("OPEN", positionId, orderTicket, direction, volume, price, sl, tp,
                   0.0, 0.0, 0.0, "", 0.0, 0.0, comment);
      return;
   }

   if(entryType == DEAL_ENTRY_OUT || entryType == DEAL_ENTRY_OUT_BY || entryType == DEAL_ENTRY_INOUT)
   {
      double profit      = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
      double swap        = HistoryDealGetDouble(dealTicket, DEAL_SWAP);
      double commission  = HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      long   reasonCode  = HistoryDealGetInteger(dealTicket, DEAL_REASON);
      string reason      = EnumToString((ENUM_DEAL_REASON)reasonCode);
      datetime closeTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);

      double   openPrice = 0.0;
      datetime openTime  = 0;
      long     openDealType = dealType;   // fallback if the opening deal can't be found
      bool foundOpen = FindPositionOpenDeal(positionId, openPrice, openTime, openDealType);
      string direction = (openDealType == DEAL_TYPE_BUY) ? "BUY" : "SELL";

      double durationMin = foundOpen ? (double)(closeTime - openTime) / 60.0 : 0.0;
      double priceMove   = 0.0;
      if(foundOpen)
         priceMove = (openDealType == DEAL_TYPE_BUY) ? (price - openPrice) : (openPrice - price);

      PrintFormat("TelegramSMC_TradeLogger: CLOSE position %I64d (%s) - %.2f lots @ %.2f "
                  "net=%.2f reason=%s duration=%.1fmin",
                  positionId, direction, volume, price, profit + swap + commission, reason, durationMin);

      LogResultRow("CLOSE", positionId, orderTicket, direction, volume, price, 0.0, 0.0,
                   profit, swap, commission, reason, durationMin, priceMove, comment);
   }
}
//+------------------------------------------------------------------+
