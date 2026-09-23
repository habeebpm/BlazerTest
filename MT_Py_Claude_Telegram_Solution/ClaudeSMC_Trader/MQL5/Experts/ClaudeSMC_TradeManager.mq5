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
//| EXIT DESIGN - InpExitStyle selects which of two exit shapes this    |
//| EA runs, mirroring python/config.py's AdvisorConfig.exit_style      |
//| (keep the two in sync by hand - see that module's docstring):       |
//|                                                                    |
//| EXIT_SL_TO_TP1 (default): Python opens every position with a       |
//| stop-loss and, deliberately, NO broker-side take-profit at all     |
//| (see executor.py). This EA is the ONLY thing that ever closes a    |
//| position early, via the stop-loss:                                 |
//|   1. Before InpTp1Dollars of floating profit is reached, the SL    |
//|      just sits at whatever Python originally set it to.            |
//|   2. The instant profit reaches InpTp1Dollars, the SL is moved to  |
//|      EXACTLY that price - locking in that much profit, no more no  |
//|      less, in one deterministic step.                              |
//|   3. From then on the SL trails InpTrailDollars behind new highs/  |
//|      lows, tightening only, for the rest of the move.              |
//|                                                                    |
//| EXIT_BREAKEVEN_R_DECAY: the same steps 2/3 above, plus one earlier |
//| protective step before either can happen:                          |
//|   0. Once floating profit reaches InpBreakevenAtrMult x this        |
//|      position's own InpAtrPeriod-bar ATR on InpAtrTimeframe, OR    |
//|      InpDecayWindowMinutes have passed since the position opened   |
//|      (whichever happens first) - AND ONLY IF price has actually     |
//|      moved into profit far enough to place a valid stop there -     |
//|      the SL moves to EXACTLY the entry price (breakeven), no        |
//|      further, no earlier. A fast move can still jump straight from |
//|      step 0 to step 2 in one tick (the TP1-lock check always runs  |
//|      first) - this step only fires when TP1 hasn't been reached    |
//|      yet.                                                           |
//|                                                                    |
//| "Armed"/"at breakeven" is never stored in the EA in either style - |
//| it's derived every tick from whether the position's OWN current SL |
//| has already reached the lock/breakeven level, so this EA needs no  |
//| memory across ticks or restarts and stays correct even if          |
//| reattached mid-trade.                                               |
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
//| USD amounts AT InpReferenceLot (= Python's reference_lot),         |
//| converted to a price distance with the live tick value/size:       |
//|   price_distance = dollars * tick_size / (tick_value * ref_lot)    |
//| the exact inverse of python/mt5_gateway.py's                        |
//| price_distance_for_dollars(), which sizes Python's initial SL the  |
//| same way (sl_dollars at reference_lot). SL, TP1 and trail are     |
//| therefore all FIXED price distances; with risk-% sizing a bigger   |
//| lot risks and locks proportionally more dollars, keeping the same  |
//| shape. No contract size is ever assumed.                           |
//|                                                                    |
//| SETUP: attach to an XAUUSD chart alongside (or instead of) running |
//| python main.py on the same or a different machine - this EA only   |
//| needs MT5 itself, no WebRequest, no Telegram, nothing external.     |
//| Runs with InpDryRun = true until you turn it off; nothing above    |
//| this line touches a position.                                      |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include <EconCalendar.mqh>

input group "=== Identification ==="
input long   InpMagicNumber   = 20260921;   // Must match the Python advisor's AdvisorConfig.magic
input bool   InpDryRun        = true;        // Log what would happen; do not modify real positions

input group "=== Exit rule - USD amounts at InpReferenceLot, i.e. fixed PRICE distances ==="
input double InpTp1Dollars    = 6.0;         // Profit (USD at InpReferenceLot) that locks the stop-loss in here
input double InpTrailDollars  = 3.0;         // Trailing distance (USD at InpReferenceLot) once locked/armed
input double InpReferenceLot  = 0.01;        // MUST match python/config.py AdvisorConfig.reference_lot (the SL's reference lot too)

enum ENUM_EXIT_STYLE
{
   EXIT_SL_TO_TP1,          // Default - lock at InpTp1Dollars, then trail (see file header)
   EXIT_BREAKEVEN_R_DECAY   // Adds an earlier breakeven step before the same lock/trail (see file header)
};

input group "=== Exit style - must match python/config.py AdvisorConfig.exit_style ==="
input ENUM_EXIT_STYLE InpExitStyle = EXIT_SL_TO_TP1;

input group "=== EXIT_BREAKEVEN_R_DECAY only - ignored under EXIT_SL_TO_TP1 ==="
input double         InpBreakevenAtrMult   = 0.5;        // Move SL to breakeven once profit reaches this x ATR
input ENUM_TIMEFRAMES InpAtrTimeframe      = PERIOD_M5;  // Timeframe the ATR is read from
input int             InpAtrPeriod         = 14;         // ATR period
input double         InpDecayWindowMinutes = 15.0;       // Force breakeven after this long even short of the ATR trigger

input group "=== Economic calendar export (MT5 built-in) - read by python/econ_calendar.py ==="
input string InpCalendarExportFile = "econ_calendar.csv"; // MUST match python config.econ_calendar_filename ("" = no export)
input string InpNewsCurrencies     = "USD";               // Currencies to export, comma-separated
input int    InpCalendarRefreshMin = 5;                   // Re-export every N minutes

CTrade trade;
int g_atrHandle = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTp1Dollars <= 0.0 || InpTrailDollars <= 0.0 || InpReferenceLot <= 0.0)
   {
      Print("ClaudeSMC_TradeManager: InpTp1Dollars, InpTrailDollars and InpReferenceLot must all be positive.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpExitStyle == EXIT_BREAKEVEN_R_DECAY)
   {
      if(InpBreakevenAtrMult <= 0.0 || InpAtrPeriod <= 0 || InpDecayWindowMinutes <= 0.0)
      {
         Print("ClaudeSMC_TradeManager: InpBreakevenAtrMult, InpAtrPeriod and InpDecayWindowMinutes "
               "must all be positive under EXIT_BREAKEVEN_R_DECAY.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      g_atrHandle = iATR(_Symbol, InpAtrTimeframe, InpAtrPeriod);
      if(g_atrHandle == INVALID_HANDLE)
      {
         Print("ClaudeSMC_TradeManager: iATR() failed - cannot run EXIT_BREAKEVEN_R_DECAY.");
         return(INIT_FAILED);
      }
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
      // Exact for every position, whatever its volume: the trail is a
      // fixed price distance (InpTrailDollars at InpReferenceLot).
      double trailDistCheck = DollarsToPrice(InpTrailDollars, InpReferenceLot);
      if(trailDistCheck < stopsLevelPrice)
         PrintFormat("ClaudeSMC_TradeManager: WARNING - InpTrailDollars=%.2f at InpReferenceLot=%.2f is "
                     "a %.5f price distance, tighter than this symbol's broker minimum stop distance "
                     "(%.5f). Once locked, the trailing stop may never be able to move - it will just "
                     "sit at the InpTp1Dollars lock level instead, which is still a valid, protected "
                     "exit, just not a trailing one. Widen InpTrailDollars if you want trailing to "
                     "actually activate.", InpTrailDollars, InpReferenceLot, trailDistCheck,
                     stopsLevelPrice);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_atrHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHandle);
}

//+------------------------------------------------------------------+
//| EXIT_BREAKEVEN_R_DECAY only: InpBreakevenAtrMult x the position's  |
//| own ATR, as a price distance - ATR is already a price-unit value,  |
//| so no dollar conversion is needed here (unlike InpTp1Dollars/       |
//| InpTrailDollars, which ARE dollar amounts). Returns DBL_MAX (never |
//| triggers) if the ATR buffer isn't ready yet, so a position can      |
//| still be protected by the decay-window fallback below.             |
//+------------------------------------------------------------------+
double BreakevenAtrDistance()
{
   if(g_atrHandle == INVALID_HANDLE)
      return(DBL_MAX);
   double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 0, 1, atrBuf) <= 0)
      return(DBL_MAX);
   return(InpBreakevenAtrMult * atrBuf[0]);
}

//+------------------------------------------------------------------+
//| EXIT_BREAKEVEN_R_DECAY only: true once EITHER the ATR-based        |
//| profit trigger OR the decay window has been reached - "whichever   |
//| happens first" per the file header. Does not itself check that     |
//| price has actually moved far enough to place a valid breakeven      |
//| stop - the caller (ManagePosition) guards that separately, since    |
//| it already has the broker's minimum-stop-distance check in hand.    |
//| Reads POSITION_TIME off whatever position the caller already has   |
//| selected (via PositionSelectByTicket) - takes no ticket of its own  |
//| to select, so it must only ever be called right after that select. |
//+------------------------------------------------------------------+
bool BreakevenDue(double profit)
{
   if(profit >= BreakevenAtrDistance())
      return(true);
   datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
   long decaySeconds = (long)(InpDecayWindowMinutes * 60);
   return((TimeCurrent() - openTime) >= decaySeconds);
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

   // At the REFERENCE lot, not this position's own volume: Python sizes
   // the stop as sl_dollars at reference_lot (a fixed price distance) and then
   // scales the lot to risk a % of equity. Converting TP1/trail at the
   // position's real volume would shrink them as the lot grows - risking
   // e.g. $200 at the stop to lock only $6. Fixed price distances keep the
   // SL : TP1 : trail shape identical at every lot size.
   double tp1Dist = DollarsToPrice(InpTp1Dollars, InpReferenceLot);
   double trailDist = DollarsToPrice(InpTrailDollars, InpReferenceLot);
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
         else if(InpExitStyle == EXIT_BREAKEVEN_R_DECAY)
         {
            bool atBreakeven = (currentSl > 0.0 && currentSl >= openPrice - point);
            if(!atBreakeven && (tick.bid - openPrice) >= minStopDist && BreakevenDue(profit))
            {
               newSl = NormalizeDouble(openPrice, digits);
               changeSl = true;
            }
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
         else if(InpExitStyle == EXIT_BREAKEVEN_R_DECAY)
         {
            bool atBreakeven = (currentSl > 0.0 && currentSl <= openPrice + point);
            if(!atBreakeven && (openPrice - tick.ask) >= minStopDist && BreakevenDue(profit))
            {
               newSl = NormalizeDouble(openPrice, digits);
               changeSl = true;
            }
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
   EconMaybeExport(InpCalendarExportFile, InpNewsCurrencies, InpCalendarRefreshMin);
}
//+------------------------------------------------------------------+
