//+------------------------------------------------------------------+
//|                                     ClaudeSMC_TradeManager.mq5     |
//|                                                                    |
//| Manages exits for positions opened by this solution's Python       |
//| advisor (python/main.py) - places NO new trades itself. All entry  |
//| decisions (Claude's confluence validation, the same-direction      |
//| position cap, fixed lot sizing) live in Python; this EA's only job |
//| is tick-by-tick trade management, which Python's poll loop is too  |
//| coarse-grained to do reliably.                                     |
//|                                                                    |
//| WHY THE SPLIT: Python opens a position with a hard $6 broker       |
//| take-profit (see executor.py) so the trade is protected even if    |
//| Python itself goes offline. This EA then watches that position on  |
//| every tick and, once floating profit reaches InpTpArmDollars AND   |
//| the trailing stop can actually move there, drops the fixed TP in   |
//| favour of an InpTrailDollars trailing stop for the rest of the     |
//| move. A Python polling loop checking every N seconds could miss a  |
//| fast spike past the arm level and never trail it; a tick EA can't. |
//|                                                                    |
//| SCOPE: only touches positions on this chart's symbol whose magic   |
//| number equals InpMagicNumber - never anything opened by hand or by |
//| a different EA/system on the same account. InpMagicNumber MUST     |
//| match the Python advisor's AdvisorConfig.magic (both default to    |
//| 20260921) or this EA will simply never see the positions Python    |
//| opens.                                                              |
//|                                                                    |
//| DOLLAR -> PRICE CONVERSION: InpTpArmDollars/InpTrailDollars are    |
//| USD amounts, not raw price units - this EA converts them to a      |
//| price distance itself, per position, using that position's own     |
//| volume and the symbol's live tick value/size:                      |
//|   price_distance = dollars * tick_size / (tick_value * volume)     |
//| the exact inverse of the formula python/mt5_gateway.py's            |
//| price_distance_for_dollars() uses to size Python's own initial SL/ |
//| TP - so a $6 stop means $6 of account risk regardless of contract   |
//| size, broker, or (if ever changed) lot size, not an assumed 100oz   |
//| contract.                                                           |
//|                                                                    |
//| SETUP: attach to an XAUUSD chart alongside (or instead of) running |
//| python main.py on the same or a different machine - this EA only   |
//| needs MT5 itself, no WebRequest, no Telegram, nothing external.     |
//| Runs with InpDryRun = true until you turn it off; nothing above    |
//| this line touches a position.                                      |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>

input group "=== Identification ==="
input long   InpMagicNumber   = 20260921;   // Must match the Python advisor's AdvisorConfig.magic
input bool   InpDryRun        = true;        // Log what would happen; do not modify real positions

input group "=== Exit rule - USD amounts, converted to price per position's own volume ==="
input double InpTpArmDollars  = 6.0;         // Floating profit (USD) that arms the trail
input double InpTrailDollars  = 3.0;         // Trailing distance (USD) once armed

CTrade trade;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTpArmDollars <= 0.0 || InpTrailDollars <= 0.0)
   {
      Print("ClaudeSMC_TradeManager: InpTpArmDollars and InpTrailDollars must both be positive.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(MQLInfoInteger(MQL_TESTER))
      Print("ClaudeSMC_TradeManager: running in the Strategy Tester - InpDryRun still governs "
            "whether positions are actually modified.");

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stopsLevelPrice = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(tickValue > 0.0 && tickSize > 0.0)
   {
      // Illustrative only, at the Python default of 0.01 lots - OnTick()
      // always recomputes per position using that position's real volume,
      // since a wider lot size converts the same dollar amount to a
      // proportionally SMALLER price distance (see DollarsToPrice below).
      double sampleTrailDist = InpTrailDollars * tickSize / (tickValue * 0.01);
      if(sampleTrailDist < stopsLevelPrice)
         PrintFormat("ClaudeSMC_TradeManager: WARNING - at 0.01 lots, InpTrailDollars=%.2f converts to "
                     "a %.5f price distance, tighter than this symbol's broker minimum stop distance "
                     "(%.5f). The trailing stop may never be able to move for that position size - its "
                     "take-profit is correctly kept in place instead of being dropped for a trail that "
                     "can't engage (see ManagePosition). Widen InpTrailDollars if you want trailing to "
                     "actually activate.", InpTrailDollars, sampleTrailDist, stopsLevelPrice);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| dollars -> price distance for THIS symbol at the given volume,    |
//| via the broker's own tick value/size - never assumes a contract   |
//| size (see the file header).                                       |
//+------------------------------------------------------------------+
double DollarsToPrice(double dollars, double volume)
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || volume <= 0.0)
      return(0.0);
   return(dollars * tickSize / (tickValue * volume));
}

//+------------------------------------------------------------------+
//| Arm-then-trail for one position. Mirrors TelegramSMC_Copier.mq5's |
//| ManageOpenPositions exactly - including the fix for the bug where  |
//| the take-profit was dropped even when the trail could never move   |
//| (see that file's history): the TP is only ever replaced in the     |
//| SAME tick the trailing SL actually moves, never on profit alone.   |
//| A broker whose minimum stop distance is wider than InpTrailDollars |
//| (in price terms) correctly keeps the fixed TP in place forever     |
//| rather than leaving the position with no protection at all.        |
//+------------------------------------------------------------------+
void ManagePosition(ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if((long)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
      return;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      return;

   double volume = PositionGetDouble(POSITION_VOLUME);
   double armDist = DollarsToPrice(InpTpArmDollars, volume);
   double trailDist = DollarsToPrice(InpTrailDollars, volume);
   if(armDist <= 0.0 || trailDist <= 0.0)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   long type = PositionGetInteger(POSITION_TYPE);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentSl = PositionGetDouble(POSITION_SL);
   double currentTp = PositionGetDouble(POSITION_TP);

   bool changeSl = false;
   double newSl = currentSl;

   if(type == POSITION_TYPE_BUY)
   {
      double profit = tick.bid - openPrice;
      if(profit >= armDist)
      {
         double candidate = NormalizeDouble(tick.bid - trailDist, digits);
         if((currentSl <= 0.0 || candidate > currentSl) && (tick.bid - candidate) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
      bool changeTp = changeSl && (currentTp != 0.0);
      if(changeSl || changeTp)
      {
         if(InpDryRun)
            PrintFormat("ClaudeSMC_TradeManager: [DRY-RUN] would modify BUY ticket %I64u sl %.2f -> %.2f, "
                        "tp -> none", ticket, currentSl, changeSl ? newSl : currentSl);
         else
            trade.PositionModify(ticket, changeSl ? newSl : currentSl, 0.0);
      }
   }
   else if(type == POSITION_TYPE_SELL)
   {
      double profit = openPrice - tick.ask;
      if(profit >= armDist)
      {
         double candidate = NormalizeDouble(tick.ask + trailDist, digits);
         if((currentSl <= 0.0 || candidate < currentSl) && (candidate - tick.ask) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
      bool changeTp = changeSl && (currentTp != 0.0);
      if(changeSl || changeTp)
      {
         if(InpDryRun)
            PrintFormat("ClaudeSMC_TradeManager: [DRY-RUN] would modify SELL ticket %I64u sl %.2f -> %.2f, "
                        "tp -> none", ticket, currentSl, changeSl ? newSl : currentSl);
         else
            trade.PositionModify(ticket, changeSl ? newSl : currentSl, 0.0);
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      ManagePosition(ticket);
   }
}
//+------------------------------------------------------------------+
