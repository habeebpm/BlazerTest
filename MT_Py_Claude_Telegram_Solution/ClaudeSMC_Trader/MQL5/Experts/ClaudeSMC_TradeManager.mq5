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
//| EXIT DESIGN (exit_style="sl_to_tp1" in python/config.py):          |
//| Python opens every position with a stop-loss and, deliberately, NO |
//| broker-side take-profit at all (see executor.py). This EA is the   |
//| ONLY thing that ever closes a position early, via the stop-loss:   |
//|   1. Before InpTp1Dollars of floating profit is reached, the SL    |
//|      just sits at whatever Python originally set it to.            |
//|   2. The instant profit reaches InpTp1Dollars, the SL is moved to  |
//|      EXACTLY that price - locking in that much profit, no more no  |
//|      less, in one deterministic step.                              |
//|   3. From then on the SL trails InpTrailDollars behind new highs/  |
//|      lows, tightening only, for the rest of the move.              |
//| "Armed" (state 2/3 vs state 1) is never stored in the EA - it's    |
//| derived every tick from whether the position's OWN current SL has  |
//| already reached the lock level, so this EA needs no memory across  |
//| ticks or restarts and stays correct even if reattached mid-trade.  |
//|                                                                    |
//| WHY NO BROKER TAKE-PROFIT AT ALL: an earlier design placed a real  |
//| TP at entry+InpTp1Dollars - the exact same price this EA's arm     |
//| threshold sits at. In live trading a standing TP order fires the    |
//| instant price touches it, which (almost always, since it needs no  |
//| EA round-trip at all) wins the race against this EA noticing and   |
//| moving the SL - so the trail-and-run-further behavior never really |
//| got a chance to engage; backtest.py --compare (exit_style=         |
//| "fixed_tp" is the old design, still implemented there for exactly   |
//| this comparison) demonstrates it concretely. Removing the broker   |
//| TP removes that race: the stop-loss is the only thing that can      |
//| close the position, so this EA's own logic is what actually runs.  |
//| Because of that, ManagePosition() also unconditionally clears any   |
//| broker TP it finds on a managed position, on every tick, even one   |
//| where the SL itself isn't changing yet - not just a leftover from   |
//| an older build of this EA, but any TP a position could carry, since |
//| under this design none should ever have one.                        |
//|                                                                    |
//| A Python polling loop checking every N seconds could still miss a  |
//| fast spike past the arm level and never lock/trail it in time; a   |
//| tick EA can't - that's the other reason this half lives here and   |
//| not in main.py.                                                    |
//|                                                                    |
//| SCOPE: only touches positions on this chart's symbol whose magic   |
//| number equals InpMagicNumber - never anything opened by hand or by |
//| a different EA/system on the same account. InpMagicNumber MUST     |
//| match the Python advisor's AdvisorConfig.magic (both default to    |
//| 20260921) or this EA will simply never see the positions Python    |
//| opens.                                                              |
//|                                                                    |
//| DOLLAR -> PRICE CONVERSION: InpTp1Dollars/InpTrailDollars are      |
//| USD amounts, not raw price units - this EA converts them to a      |
//| price distance itself, per position, using that position's own     |
//| volume and the symbol's live tick value/size:                      |
//|   price_distance = dollars * tick_size / (tick_value * volume)     |
//| the exact inverse of the formula python/mt5_gateway.py's            |
//| price_distance_for_dollars() uses to size Python's own initial SL  |
//| - so "$6" means $6 of account risk regardless of contract size,     |
//| broker, or (if ever changed) lot size, not an assumed 100oz         |
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
input double InpTp1Dollars    = 6.0;         // Floating profit (USD) that locks the stop-loss in here
input double InpTrailDollars  = 3.0;         // Trailing distance (USD) once locked/armed

CTrade trade;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTp1Dollars <= 0.0 || InpTrailDollars <= 0.0)
   {
      Print("ClaudeSMC_TradeManager: InpTp1Dollars and InpTrailDollars must both be positive.");
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
                     "(%.5f). Once locked, the trailing stop may never be able to move for that "
                     "position size - it will just sit at the InpTp1Dollars lock level instead, which "
                     "is still a valid, protected exit, just not a trailing one. Widen InpTrailDollars "
                     "if you want trailing to actually activate.", InpTrailDollars, sampleTrailDist,
                     stopsLevelPrice);
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
//| Lock-then-trail for one position - see the file header for the    |
//| full design rationale. "Armed" (locked in, now trailing) is        |
//| derived from whether the position's OWN current SL has already     |
//| reached the lock level, not stored anywhere - see the header.      |
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
   double tp1Dist = DollarsToPrice(InpTp1Dollars, volume);
   double trailDist = DollarsToPrice(InpTrailDollars, volume);
   if(tp1Dist <= 0.0 || trailDist <= 0.0)
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
      double lockLevel = NormalizeDouble(openPrice + tp1Dist, digits);
      bool armed = (currentSl > 0.0 && currentSl >= lockLevel - point);
      if(!armed)
      {
         double profit = tick.bid - openPrice;
         if(profit >= tp1Dist && (tick.bid - lockLevel) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.bid - trailDist, digits);
         if(candidate > currentSl && (tick.bid - candidate) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }
   else if(type == POSITION_TYPE_SELL)
   {
      double lockLevel = NormalizeDouble(openPrice - tp1Dist, digits);
      bool armed = (currentSl > 0.0 && currentSl <= lockLevel + point);
      if(!armed)
      {
         double profit = openPrice - tick.ask;
         if(profit >= tp1Dist && (lockLevel - tick.ask) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.ask + trailDist, digits);
         if(candidate < currentSl && (candidate - tick.ask) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }
   else
   {
      return;
   }

   // Under this EA's design no managed position should ever carry a broker
   // take-profit (see the file header) - but a position opened before this
   // design shipped, or modified by hand/another tool, could still have one.
   // Clear it unconditionally, even on a tick where the SL itself isn't
   // changing yet, so a leftover TP can never sit there racing the lock-then-
   // trail logic above the way the old fixed_tp design used to.
   if(changeSl || currentTp != 0.0)
   {
      double slToSend = changeSl ? newSl : currentSl;
      if(InpDryRun)
         PrintFormat("ClaudeSMC_TradeManager: [DRY-RUN] would modify %s ticket %I64u sl %.2f -> %.2f%s",
                     type == POSITION_TYPE_BUY ? "BUY" : "SELL", ticket, currentSl, slToSend,
                     currentTp != 0.0 ? " (clearing stale broker TP)" : "");
      else
         trade.PositionModify(ticket, slToSend, 0.0);
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
