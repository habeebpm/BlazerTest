//+------------------------------------------------------------------+
//|                                  TelegramSMC_TradeLogger.mq5      |
//|                                                                    |
//| Logs every open and close of a position tagged with               |
//| InpMagicNumber on InpSymbol to TelegramSMC_Results.csv (this       |
//| terminal's MQL5\Files, or the shared Common\Files folder if        |
//| InpUseCommonFolder is set) - one row per OPEN event and one row    |
//| per CLOSE event, linked by position_id and order_ticket.           |
//|                                                                    |
//| A watcher only: it never places, modifies or closes an order and   |
//| never talks to Telegram - it reads trade history (DEAL_REASON gives|
//| the close reason: SL / TP / client / expert / stop-out). Defaults  |
//| = GoldTrader: Telegram trades (20260922, "Telegram_Sig_Unified")   |
//| and Claude trades (20260921, "Claude_Sig") into one file, the one  |
//| the dashboard reads. Duration and price move come from the         |
//| position's opening deal, so they are right even for a position     |
//| opened before the logger was attached.                             |
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
#property version   "1.10"
#property strict
#property description "GoldTrader trade journal: logs every open/close of the Telegram (20260922) and Claude (20260921) positions to TelegramSMC_Results.csv. Never trades."

#include <TelegramSMC_Common.mqh>

input group "=== What to log ==="
input string InpSymbol          = "XAUUSD";     // Symbol to log (match the trading EA's symbol)
input ulong  InpMagicNumber     = 20260922;     // Magic number to log (GoldTrader Telegram trades)
input string InpSourceLabel     = "Telegram_Sig_Unified"; // Source tag for InpMagicNumber's rows
input ulong  InpMagicNumber2    = 20260921;     // 2nd magic logged into the same file (GoldTrader Claude trades; 0 = off)
input string InpSourceLabel2    = "Claude_Sig"; // Source tag for InpMagicNumber2's rows
input bool   InpUseCommonFolder = false;        // Write to the shared Common\Files folder instead of this terminal's Files

//+------------------------------------------------------------------+
//| Expert initialization - purely event-driven, no timer/tick work.  |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpMagicNumber2 != 0 && InpMagicNumber2 == InpMagicNumber)
   {
      Print("TelegramSMC_TradeLogger: InpMagicNumber2 equals InpMagicNumber - set it to 0 or a different magic.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   PrintFormat("TelegramSMC_TradeLogger: watching symbol=%s magic=%I64u source=%s -> %s\\%s",
               InpSymbol, InpMagicNumber, InpSourceLabel,
               InpUseCommonFolder ? "Common\\Files" : "MQL5\\Files", TSMC_RESULTS_FILE);
   if(InpMagicNumber2 != 0)
      PrintFormat("TelegramSMC_TradeLogger: also watching magic=%I64u source=%s (same file)",
                  InpMagicNumber2, InpSourceLabel2);
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
void LogResultRow(long magic, const string &source, const string &event, long positionId,
                   long orderTicket, const string &direction,
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
                 IntegerToString(magic) + "," +
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
                 TsmcCsvField(source);

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
   string source;
   if((ulong)magic == InpMagicNumber)
      source = InpSourceLabel;
   else if(InpMagicNumber2 != 0 && (ulong)magic == InpMagicNumber2)
      source = InpSourceLabel2;
   else
      return;

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
      LogResultRow(magic, source, "OPEN", positionId, orderTicket, direction, volume, price, sl, tp,
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

      LogResultRow(magic, source, "CLOSE", positionId, orderTicket, direction, volume, price, 0.0, 0.0,
                   profit, swap, commission, reason, durationMin, priceMove, comment);
   }
}
//+------------------------------------------------------------------+
