//+------------------------------------------------------------------+
//|                                XAUUSD_MTF_RSI_MACD_BB_EA.mq5      |
//|                                                                    |
//| Multi-timeframe RSI / MACD / Bollinger Bands / swing-structure     |
//| EA for XAUUSD (Gold).                                              |
//|                                                                    |
//| STRATEGY SUMMARY                                                   |
//| ------------------------------------------------------------------ |
//| Three independent timeframes (InpTF1 < InpTF2 < InpTF3, e.g.       |
//| M15/H1/H4) are each scored on the SAME four criteria:               |
//|                                                                    |
//|  BUY  1. RSI > InpRsiBuyLevel (45-50) AND rising vs InpRsiLookback  |
//|          bars back.                                                |
//|       2. MACD histogram positive (covers "just crossed up" too,     |
//|          since a fresh cross makes the histogram positive on the   |
//|          bar it happens).                                          |
//|       3. Close above the Bollinger middle band.                    |
//|       4. Close still above the most recent swing low (structure    |
//|          intact - no breakdown).                                   |
//|                                                                    |
//|  SELL 1. RSI < InpRsiSellLevel (45-50) AND falling vs the same      |
//|          lookback.                                                 |
//|       2. MACD histogram negative AND expanding (more negative       |
//|          than the prior closed bar).                                |
//|       3. Close below the Bollinger middle band.                    |
//|       4. CONFIRMED close below the most recent swing low - a wick   |
//|          through it does not count, the bar must close through it. |
//|                                                                    |
//| Each timeframe counts how many of the four line up bullish and how |
//| many line up bearish and keeps whichever side has more (0-4). That  |
//| gives a net score per timeframe of buyCount - sellCount, from -4    |
//| (outright sell) to +4 (outright buy), 0 meaning that timeframe is   |
//| neutral or contradictory.                                          |
//|                                                                    |
//| COMBINING THE THREE TIMEFRAMES                                     |
//| ------------------------------------------------------------------ |
//| combinedScore = 100 * (sum of netScore[i] * InpWeightTF[i]) /       |
//|                  (4 * sum of InpWeightTF[i])                        |
//| ... i.e. a -100..+100 scale where +100 means all three timeframes   |
//| hit a clean 4/4 buy. Higher timeframes get more weight by default   |
//| (InpWeightTF3 > InpWeightTF2 > InpWeightTF1) so H4 matters more     |
//| than M15, per the "weighted by timeframe" idea in the spec.         |
//|                                                                    |
//| Swing-low/high confirmation (criterion 4) on ANY timeframe, in the  |
//| direction the combined score already leans, is treated as the      |
//| strongest single piece of evidence: it adds InpSwingBonusPoints to  |
//| the combined score, which can push a "leaning" score over the entry |
//| threshold into a "confirmed" one - matching the manual process      |
//| described in the spec.                                              |
//|                                                                    |
//| "Agreeing timeframes" = how many of the three sit at conviction     |
//| (|netScore| >= 3, i.e. 3 or 4 of 4 criteria) on the same side as     |
//| the overall combined score:                                         |
//|   3 agreeing  -> full alignment   -> STRONG   Buy/Sell               |
//|   2 agreeing  -> partial alignment-> MODERATE Buy/Sell (building/   |
//|                  weakening - the other timeframe(s) disagree or      |
//|                  are still neutral)                                  |
//|   0-1 agreeing-> no alignment     -> NEUTRAL                         |
//|                                                                    |
//| A trade fires only when BOTH gates pass: |combinedScore| >=          |
//| InpEntryThreshold AND agreeing timeframes >= InpMinAgreeingTF.       |
//| Trading a direction on which both a buy and a sell would otherwise  |
//| qualify never happens by construction (the sign of combinedScore    |
//| picks one side).                                                    |
//|                                                                    |
//| SWING STRUCTURE: a simple fractal/pivot scan over the last           |
//| InpSwingScanBars closed bars - a bar is a pivot low when its low is |
//| below the low of InpSwingPivotWidth bars on EITHER side of it. The   |
//| most recent one found is the reference "most recent swing low" used |
//| by criterion 4 on both the buy and the sell side, exactly as         |
//| described in the spec (the sell side deliberately reuses the swing  |
//| LOW, not a swing high, as its breakdown reference).                  |
//|                                                                    |
//| RISK: fixed 0.01 lots (InpFixedLot) per position, up to              |
//| InpMaxOpenPositions = 4 positions open at once (four 0.01-lot        |
//| positions, not a single 4.00-lot position). Each position carries a  |
//| fixed $6.00 stop-loss (InpStopLossPips = 60 pips) and, once in       |
//| $3.00 profit (InpTrailStartPips = 30 pips), a $3.00 trailing stop    |
//| (InpTrailPips) that only ever tightens. One XAUUSD lot is 100oz, so  |
//| at 0.01 lots $1 of price is $1 of P/L - 60 pips (=$6.00 price        |
//| distance on a 2-digit gold feed) is therefore a $6.00 stop and 30    |
//| pips a $3.00 trail, per 0.01-lot position. Four positions open at    |
//| once therefore risk up to $24.00 combined.                          |
//|                                                                    |
//| Everything else (session filter using the broker's own trading      |
//| hours, spread filter, daily-loss circuit breaker, profit lock,       |
//| flatten-before-close) mirrors XAUUSD_Confluence_EA.mq5 in this same  |
//| repository, so the two EAs behave consistently operationally even   |
//| though their entry signals are unrelated.                           |
//|                                                                    |
//| IMPORTANT DISCLAIMER                                               |
//| ------------------------------------------------------------------ |
//| No trading system - this one included - can guarantee profit.     |
//| XAUUSD is a highly volatile, news-sensitive instrument. Backtest   |
//| across multiple market regimes, forward-test on a demo account,    |
//| and only risk capital you can afford to lose. Past performance     |
//| does not guarantee future results.                                 |
//+------------------------------------------------------------------+
#property copyright "XAUUSD_MTF_RSI_MACD_BB_EA"
#property link      ""
#property version   "1.00"
#property strict
#property description "Multi-timeframe (M15/H1/H4 by default) RSI+MACD+Bollinger Bands+swing-structure confluence EA for XAUUSD, fixed 0.01 lot / 4 positions max, $6 stop, $3 trail. Educational use - backtest and demo-trade thoroughly before risking real capital."

#include <Trade\Trade.mqh>

//================================= INPUTS ====================================

input group "=== General ==="
input ulong   InpMagicNumber        = 20260914;    // Magic number
input int     InpSlippagePoints     = 30;           // Max slippage (points)
input bool    InpTradeXAUUSDOnly    = true;         // Require chart symbol to contain "XAU"
input int     InpMaxOpenPositions   = 4;            // Max simultaneous open positions (this EA/symbol)
input bool    InpAllowOpposite      = false;        // Allow a buy and a sell open at the same time

input group "=== Timeframes (TF1 must be the SHORTEST - it drives entry timing) ==="
input ENUM_TIMEFRAMES InpTF1        = PERIOD_M15;   // Timeframe 1 (lowest / entry-timing)
input ENUM_TIMEFRAMES InpTF2        = PERIOD_H1;    // Timeframe 2 (middle)
input ENUM_TIMEFRAMES InpTF3        = PERIOD_H4;    // Timeframe 3 (highest)
input double  InpWeightTF1          = 1.0;          // Combined-score weight for TF1
input double  InpWeightTF2          = 1.5;          // Combined-score weight for TF2
input double  InpWeightTF3          = 2.0;          // Combined-score weight for TF3

input group "=== RSI (criterion 1: level + rising/falling) ==="
input int     InpRsiPeriod          = 14;           // RSI period (same on all 3 timeframes)
input int     InpRsiLookback        = 2;             // Bars back to compare for rising/falling (1-2)
input double  InpRsiBuyLevel        = 47.0;          // BUY needs RSI above this (45-50 range)
input double  InpRsiSellLevel       = 47.0;          // SELL needs RSI below this (45-50 range)

input group "=== MACD (criterion 2: histogram sign / expansion) ==="
input int     InpMacdFast           = 12;            // MACD fast EMA
input int     InpMacdSlow           = 26;            // MACD slow EMA
input int     InpMacdSignal         = 9;             // MACD signal SMA

input group "=== Bollinger Bands (criterion 3: close vs middle band) ==="
input int     InpBandsPeriod        = 20;            // Bollinger Bands period
input double  InpBandsDeviation     = 2.0;            // Bollinger Bands deviation

input group "=== Swing structure (criterion 4: pivot low/breakdown) ==="
input int     InpSwingScanBars      = 30;             // Bars to scan for the most recent pivot low
input int     InpSwingPivotWidth    = 2;              // Bars required on each side to confirm a pivot

input group "=== Combining the 3 timeframes into one signal ==="
// 55.0 is deliberate, not rounded from 60: with the default weights below
// (1.0 / 1.5 / 2.0) a full 4/4 agreement on ONLY the two lowest-weighted
// timeframes (TF1+TF2) scores 55.6/100 - the weakest case that should still
// satisfy InpMinAgreeingTF = 2. Raising this above ~55.6 silently excludes
// that pairing even at full conviction; if you change the weights, re-check
// 100 * 4*(lowestTwoWeights) / (4*totalWeight) still clears this threshold.
input double  InpEntryThreshold     = 55.0;           // |combined score| (0-100) required to trade
input int     InpMinAgreeingTF      = 2;              // Timeframes at 3-4/4 conviction required (1-3)
input double  InpSwingBonusPoints   = 15.0;           // Bonus added when any TF confirms swing structure
input bool    InpShowDashboard      = true;           // Show the full per-timeframe breakdown on chart

input group "=== Risk Management ==="
input bool    InpUseFixedLot        = true;           // Trade a fixed lot size (ignore risk %)
input double  InpFixedLot           = 0.01;           // Fixed lot size when the above is true
input double  InpRiskPercent        = 0.2;            // Risk per trade (% of equity), if not fixed
input double  InpStopLossPips       = 60.0;           // Stop-loss, in pips (60 pips = $6.00 @ 0.01 lot)
input bool    InpUseTakeProfit      = false;          // Attach a fixed take-profit as well as the trail
input double  InpRiskRewardRatio    = 1.8;            // Take-profit = SL distance * this ratio, if used
input double  InpMaxLotSize         = 5.0;            // Hard cap on calculated lot size
input double  InpMaxDailyLossPct    = 1.0;            // Stop new trades after this % equity loss in a day
input bool    InpUseDailyTarget     = false;          // Hard stop for the day at the profit target
input double  InpDailyTargetPct     = 0.5;            // Daily profit target, % of the day's opening equity
input bool    InpCloseOnTarget      = true;           // Close open positions when the target is reached
input bool    InpLockDailyGains     = true;           // Keep trading, but protect a day that has run up
input double  InpLockAfterPct       = 0.5;            // Arm the lock once the day peaks above this %
input double  InpGiveBackPct        = 50.0;           // Stop if this % of the peak gain is given back
input int     InpMaxTradesPerDay    = 0;              // Max new entries per day (0 = unlimited)

input group "=== Trade Management (breakeven / trailing) ==="
input bool    InpUseBreakeven       = true;           // Move SL to breakeven once in enough profit
input double  InpBreakevenTriggerPips= 20.0;          // Move to breakeven once profit >= this many pips
input int     InpBreakevenBufferPts = 20;              // Points of buffer added at breakeven
input double  InpTrailStartPips     = 30.0;            // Start trailing once profit >= this many pips ($3)
input double  InpTrailPips          = 30.0;            // Trail distance, in pips (30 pips = $3.00)

input group "=== Session Filter (broker trading hours) ==="
input bool    InpUseBrokerSession   = true;            // Use the broker's own trading hours for this symbol
input int     InpEntryOpenBufferMin = 5;               // No new entries for N min after the session opens
input int     InpEntryCloseBufferMin= 30;              // No new entries in the last N min before it closes
input bool    InpFlattenBeforeClose = true;            // Close every position before the session closes
input int     InpFlattenBeforeCloseMin = 10;           // How many minutes before the close to flatten

input group "=== Session Filter (manual fallback, used when InpUseBrokerSession = false) ==="
input bool    InpUseSessionFilter   = true;            // Enable the manual session window
input double  InpSessionGmtOffset   = 4.0;             // Manual hours are in GMT+this (Oman = 4)
input int     InpSessionStartHour   = 6;               // Session start hour (in the zone above)
input int     InpSessionStartMin    = 0;               // Session start minute
input int     InpSessionEndHour     = 23;              // Session end hour (in the zone above)
input int     InpSessionEndMin      = 0;               // Session end minute
input bool    InpCloseBeforeWeekend = true;            // Flatten all positions before weekend close
input int     InpWeekendCloseHour   = 20;              // Friday hour (server time) to start flattening

input group "=== Spread Filter ==="
input int     InpMaxSpreadPoints    = 350;             // Max allowed spread (points) to open new trades

//================================= GLOBALS ====================================

CTrade         trade;

int            hRsi1 = INVALID_HANDLE, hMacd1 = INVALID_HANDLE, hBands1 = INVALID_HANDLE;
int            hRsi2 = INVALID_HANDLE, hMacd2 = INVALID_HANDLE, hBands2 = INVALID_HANDLE;
int            hRsi3 = INVALID_HANDLE, hMacd3 = INVALID_HANDLE, hBands3 = INVALID_HANDLE;

datetime       g_lastBarTime      = 0;
datetime       g_sessionCacheDay  = 0;      // broker session cache, refreshed daily
bool           g_sessionCacheValid= false;
int            g_sessionCacheFrom = 0;
int            g_sessionCacheTo   = 0;
datetime       g_currentDay       = 0;
double         g_dayStartEquity   = 0.0;
int            g_tradesToday      = 0;
bool           g_dailyLossHit     = false;
bool           g_dailyTargetHit   = false;
bool           g_dailyLockHit     = false;
double         g_dayPeakEquity    = 0.0;

// forward declarations: these are used before their definitions below
datetime SessionZoneTime();
datetime DateToDay(datetime t);
double   PipSize();
int      CountOpenPositions(int direction);
void     CloseAllPositions();

//+------------------------------------------------------------------+
//| Per-timeframe indicator snapshot and 4-criteria scoring            |
//+------------------------------------------------------------------+
struct TFSignal
{
   bool   valid;
   double rsiNow, rsiPrev;
   double macdHist1, macdHist2;
   double close1, bbMiddle1;
   bool   haveSwingLow;
   double swingLow;
   int    buyCount, sellCount;     // 0-4 each
   bool   swingHoldBuy;            // criterion 4, buy side
   bool   swingBreakSell;          // criterion 4, sell side
};

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTradeXAUUSDOnly && StringFind(_Symbol, "XAU") < 0)
   {
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: chart symbol '", _Symbol, "' does not contain 'XAU'. ",
            "Attach to a Gold (XAUUSD) chart, or disable InpTradeXAUUSDOnly to override.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   hRsi1   = iRSI(_Symbol, InpTF1, InpRsiPeriod, PRICE_CLOSE);
   hMacd1  = iMACD(_Symbol, InpTF1, InpMacdFast, InpMacdSlow, InpMacdSignal, PRICE_CLOSE);
   hBands1 = iBands(_Symbol, InpTF1, InpBandsPeriod, 0, InpBandsDeviation, PRICE_CLOSE);

   hRsi2   = iRSI(_Symbol, InpTF2, InpRsiPeriod, PRICE_CLOSE);
   hMacd2  = iMACD(_Symbol, InpTF2, InpMacdFast, InpMacdSlow, InpMacdSignal, PRICE_CLOSE);
   hBands2 = iBands(_Symbol, InpTF2, InpBandsPeriod, 0, InpBandsDeviation, PRICE_CLOSE);

   hRsi3   = iRSI(_Symbol, InpTF3, InpRsiPeriod, PRICE_CLOSE);
   hMacd3  = iMACD(_Symbol, InpTF3, InpMacdFast, InpMacdSlow, InpMacdSignal, PRICE_CLOSE);
   hBands3 = iBands(_Symbol, InpTF3, InpBandsPeriod, 0, InpBandsDeviation, PRICE_CLOSE);

   if(hRsi1==INVALID_HANDLE || hMacd1==INVALID_HANDLE || hBands1==INVALID_HANDLE ||
      hRsi2==INVALID_HANDLE || hMacd2==INVALID_HANDLE || hBands2==INVALID_HANDLE ||
      hRsi3==INVALID_HANDLE || hMacd3==INVALID_HANDLE || hBands3==INVALID_HANDLE)
   {
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: failed to create one or more indicator handles. Error=", GetLastError());
      return(INIT_FAILED);
   }

   if(InpTF1 >= InpTF2 || InpTF2 >= InpTF3)
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: WARNING - InpTF1 < InpTF2 < InpTF3 is expected "
            "(lowest to highest). Entry timing and the timeframe weights may not behave "
            "as intended otherwise.");

   if(InpUseFixedLot && InpFixedLot <= 0.0)
   {
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: InpFixedLot must be greater than 0 when "
            "InpUseFixedLot is true.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   if(InpMinAgreeingTF < 1 || InpMinAgreeingTF > 3)
   {
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: InpMinAgreeingTF must be 1, 2 or 3.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: stops - %.0f pip SL (%.2f price / ~$%.2f @ 0.01 lot), "
               "trailing starts at %.0f pips and trails %.0f pips behind (~$%.2f).",
               InpStopLossPips, InpStopLossPips * PipSize(), InpStopLossPips * PipSize(),
               InpTrailStartPips, InpTrailPips, InpTrailPips * PipSize());

   // Risk audit: how many losing trades does the daily cap actually absorb?
   // Sizing up without checking this is how the breaker quietly becomes the
   // strategy - it halts the day on ordinary variance rather than on a bad one.
   {
      double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
      double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double stopDist  = InpStopLossPips * PipSize();
      if(equity > 0.0 && tickValue > 0.0 && tickSize > 0.0)
      {
         double lots = InpUseFixedLot ? InpFixedLot
                                      : (equity * InpRiskPercent / 100.0)
                                        / ((stopDist / tickSize) * tickValue);
         double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
         if(lots < minLot) lots = minLot;
         double riskPerTrade = lots * (stopDist / tickSize) * tickValue;
         double riskAllSlots = riskPerTrade * InpMaxOpenPositions;
         double capMoney     = equity * InpMaxDailyLossPct / 100.0;
         double losses       = (riskPerTrade > 0.0) ? capMoney / riskPerTrade : 0.0;

         PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: risk audit - %.2f lots risks %.2f per position "
                     "(%.2f%% of %.2f equity); up to %d positions risk %.2f combined; the %.1f%% "
                     "daily cap absorbs %.1f losing trades.",
                     lots, riskPerTrade, 100.0*riskPerTrade/equity, equity,
                     InpMaxOpenPositions, riskAllSlots, InpMaxDailyLossPct, losses);
         if(losses < 3.0)
            PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: WARNING - the daily cap stops trading after "
                        "only %.1f losses. Reduce the lot size, raise InpMaxDailyLossPct, or fund "
                        "the account higher for this lot size.", losses);
      }
   }

   if(InpUseFixedLot)
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: fixed lot sizing, %.2f lots per position, up to %d "
                  "positions at once.", InpFixedLot, InpMaxOpenPositions);
   else
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: risk-based sizing, %.2f%% of equity per trade.",
                  InpRiskPercent);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   if(InpUseBrokerSession)
   {
      int fromSec, toSec;
      double serverOffset = (double)(TimeCurrent() - TimeGMT()) / 3600.0;
      if(BrokerSessionToday(fromSec, toSec))
         PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: broker session for %s today is "
                     "%02d:%02d-%02d:%02d server time (GMT%+.1f). Entries "
                     "%d min after the open until %d min before the close; "
                     "positions flattened %d min before it.",
                     _Symbol, fromSec / 3600, (fromSec % 3600) / 60,
                     toSec / 3600, (toSec % 3600) / 60, serverOffset,
                     InpEntryOpenBufferMin, InpEntryCloseBufferMin,
                     InpFlattenBeforeCloseMin);
      else
         PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: %s does not trade today per the broker's "
                     "schedule - no entries until it reopens.", _Symbol);
   }
   else if(InpUseSessionFilter)
   {
      MqlDateTime zs, ss;
      TimeToStruct(SessionZoneTime(), zs);
      TimeToStruct(TimeCurrent(), ss);
      double serverOffset = (double)(TimeCurrent() - TimeGMT()) / 3600.0;
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: session %02d:%02d-%02d:%02d in GMT%+.1f "
                  "(now %02d:%02d there). Broker server is GMT%+.1f, now %02d:%02d.",
                  InpSessionStartHour, InpSessionStartMin,
                  InpSessionEndHour, InpSessionEndMin, InpSessionGmtOffset,
                  zs.hour, zs.min, serverOffset, ss.hour, ss.min);
   }

   g_currentDay     = DateToDay(TimeCurrent());
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_tradesToday    = 0;
   g_dailyLossHit   = false;

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hRsi1   != INVALID_HANDLE) IndicatorRelease(hRsi1);
   if(hMacd1  != INVALID_HANDLE) IndicatorRelease(hMacd1);
   if(hBands1 != INVALID_HANDLE) IndicatorRelease(hBands1);
   if(hRsi2   != INVALID_HANDLE) IndicatorRelease(hRsi2);
   if(hMacd2  != INVALID_HANDLE) IndicatorRelease(hMacd2);
   if(hBands2 != INVALID_HANDLE) IndicatorRelease(hBands2);
   if(hRsi3   != INVALID_HANDLE) IndicatorRelease(hRsi3);
   if(hMacd3  != INVALID_HANDLE) IndicatorRelease(hMacd3);
   if(hBands3 != INVALID_HANDLE) IndicatorRelease(hBands3);
   Comment("");
}

//+------------------------------------------------------------------+
//| Helper: normalize a datetime down to the calendar day             |
//+------------------------------------------------------------------+
datetime DateToDay(datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   s.hour = 0; s.min = 0; s.sec = 0;
   return(StructToTime(s));
}

//+------------------------------------------------------------------+
//| Rolls the daily risk-tracking counters over at day change         |
//+------------------------------------------------------------------+
void UpdateDailyTracking()
{
   datetime today = DateToDay(TimeCurrent());
   if(today != g_currentDay)
   {
      g_currentDay     = today;
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_tradesToday    = 0;
      g_dailyLossHit   = false;
      g_dailyTargetHit = false;
      g_dailyLockHit   = false;
      g_dayPeakEquity  = g_dayStartEquity;
   }

   if(g_dayStartEquity <= 0.0) return;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double movePct = (equity - g_dayStartEquity) / g_dayStartEquity * 100.0;

   if(g_dayPeakEquity < g_dayStartEquity) g_dayPeakEquity = g_dayStartEquity;
   if(equity > g_dayPeakEquity) g_dayPeakEquity = equity;
   double peakPct = (g_dayPeakEquity - g_dayStartEquity) / g_dayStartEquity * 100.0;

   if(!g_dailyLossHit && -movePct >= InpMaxDailyLossPct)
   {
      g_dailyLossHit = true;
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: daily loss limit reached (%.2f%% <= -%.2f%%). "
                  "New entries suspended until next day.", movePct, InpMaxDailyLossPct);
   }

   // Daily profit lock: unlike the hard target this does NOT stop a winning
   // day. It arms once the day has peaked above InpLockAfterPct and only
   // halts trading if that peak gain is given back by InpGiveBackPct, so the
   // upside stays open while a day that ran up cannot round-trip to the loss
   // cap. A day peaking at +2.0% with 50% give-back floors at +1.0%.
   if(InpLockDailyGains && !g_dailyLockHit && peakPct >= InpLockAfterPct)
   {
      double floorPct = peakPct * (1.0 - InpGiveBackPct / 100.0);
      if(movePct <= floorPct)
      {
         g_dailyLockHit = true;
         PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: profit lock triggered - the day peaked at "
                     "+%.2f%% and gave back to +%.2f%% (floor +%.2f%%). Done for today.",
                     peakPct, movePct, floorPct);
         if(InpCloseOnTarget && CountOpenPositions(-1) > 0)
            CloseAllPositions();
      }
   }

   // Daily profit target: bank the day once it is made rather than giving it
   // back. Measured on equity, so floating profit counts - which is why
   // InpCloseOnTarget closes the open positions by default, turning that
   // floating gain into a realised one instead of leaving it exposed.
   if(InpUseDailyTarget && !g_dailyTargetHit && movePct >= InpDailyTargetPct)
   {
      g_dailyTargetHit = true;
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: daily profit target reached (+%.2f%% >= +%.2f%%). "
                  "No further entries today.", movePct, InpDailyTargetPct);
      if(InpCloseOnTarget && CountOpenPositions(-1) > 0)
      {
         PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: closing %d position(s) to bank the day.",
                     CountOpenPositions(-1));
         CloseAllPositions();
      }
   }
}

//+------------------------------------------------------------------+
//| Returns true exactly once per new bar on TF1 (the entry-timing TF)|
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, InpTF1, 0);
   if(t != g_lastBarTime)
   {
      g_lastBarTime = t;
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Session / weekday filter                                          |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Seconds elapsed today in SERVER time.                             |
//+------------------------------------------------------------------+
int ServerSecondsOfDay()
{
   MqlDateTime s;
   TimeToStruct(TimeCurrent(), s);
   return(s.hour * 3600 + s.min * 60 + s.sec);
}

//+------------------------------------------------------------------+
//| The broker's own trading hours for this symbol today.             |
//| Returns false when the symbol does not trade at all today (the    |
//| weekend). Sessions are reported by the broker in server time as   |
//| seconds from midnight, so this automatically tracks the broker's  |
//| GMT offset, its DST changes, gold's daily break and the early     |
//| close on Friday - none of which a fixed window can follow.        |
//| Where a day has several sessions, the earliest open and the       |
//| latest close are used.                                            |
//+------------------------------------------------------------------+
bool BrokerSessionToday(int &startSec, int &endSec)
{
   MqlDateTime s;
   TimeToStruct(TimeCurrent(), s);

   // The schedule only changes at the day boundary, and this is consulted on
   // every tick, so cache it rather than re-querying the terminal each time.
   datetime today = DateToDay(TimeCurrent());
   if(today == g_sessionCacheDay)
   {
      startSec = g_sessionCacheFrom;
      endSec   = g_sessionCacheTo;
      return(g_sessionCacheValid);
   }

   ENUM_DAY_OF_WEEK dow = (ENUM_DAY_OF_WEEK)s.day_of_week;

   bool found = false;
   int  lo = 0, hi = 0;
   for(uint i = 0; i < 8; i++)
   {
      datetime from, to;
      if(!SymbolInfoSessionTrade(_Symbol, dow, i, from, to)) break;
      int f = (int)from, t = (int)to;
      if(!found) { lo = f; hi = t; found = true; }
      else
      {
         if(f < lo) lo = f;
         if(t > hi) hi = t;
      }
   }
   g_sessionCacheDay   = today;
   g_sessionCacheValid = found;
   g_sessionCacheFrom  = lo;
   g_sessionCacheTo    = hi;

   if(!found) return(false);
   startSec = lo;
   endSec   = hi;
   return(true);
}

//+------------------------------------------------------------------+
//| Current time in the zone the session hours are expressed in.      |
//| Derived from GMT rather than server time, so the window means the |
//| same wall-clock hours whatever offset the broker runs on and      |
//| whether or not the broker observes DST.                           |
//+------------------------------------------------------------------+
datetime SessionZoneTime()
{
   return(TimeGMT() + (int)MathRound(InpSessionGmtOffset * 3600.0));
}

bool IsWithinSession()
{
   if(InpUseBrokerSession)
   {
      int fromSec, toSec;
      if(!BrokerSessionToday(fromSec, toSec))
         return(false);                       // the symbol does not trade today

      int now = ServerSecondsOfDay();
      return(now >= fromSec + InpEntryOpenBufferMin * 60 &&
             now <  toSec   - InpEntryCloseBufferMin * 60);
   }

   if(!InpUseSessionFilter) return(true);

   MqlDateTime s;
   TimeToStruct(SessionZoneTime(), s);
   int nowMin   = s.hour*60 + s.min;
   int startMin = InpSessionStartHour*60 + InpSessionStartMin;
   int endMin   = InpSessionEndHour*60   + InpSessionEndMin;

   if(startMin == endMin) return(true); // 24h session

   if(startMin < endMin)
      return(nowMin >= startMin && nowMin < endMin);
   else
      return(nowMin >= startMin || nowMin < endMin); // wraps midnight
}

//+------------------------------------------------------------------+
//| Spread filter                                                     |
//+------------------------------------------------------------------+
bool SpreadIsAcceptable()
{
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return(spread <= InpMaxSpreadPoints);
}

//+------------------------------------------------------------------+
//| Count this EA's open positions on this symbol                    |
//+------------------------------------------------------------------+
int CountOpenPositions(int direction /* -1=any, 0=buy, 1=sell */)
{
   int count = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      if(direction == -1) count++;
      else if(direction == 0 && type == POSITION_TYPE_BUY)  count++;
      else if(direction == 1 && type == POSITION_TYPE_SELL) count++;
   }
   return(count);
}

//+------------------------------------------------------------------+
//| Flatten all positions opened by this EA on this symbol            |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
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
//| Close-before-market-close protection.                             |
//|                                                                    |
//| With InpUseBrokerSession the EA flattens everything shortly before |
//| the broker's own session end for today. That covers the daily      |
//| close, gold's daily break and the weekly close on Friday with one  |
//| rule, because the broker reports Friday's earlier end itself.      |
//| Returns true when new entries should also be blocked.              |
//+------------------------------------------------------------------+
bool HandleSessionClose()
{
   if(InpUseBrokerSession)
   {
      int fromSec, toSec;
      if(!BrokerSessionToday(fromSec, toSec))
         return(true);        // no session today - block entries, nothing to close

      if(!InpFlattenBeforeClose) return(false);

      int now = ServerSecondsOfDay();
      if(now >= toSec - InpFlattenBeforeCloseMin * 60 && now < toSec)
      {
         if(CountOpenPositions(-1) > 0)
         {
            PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: flattening %d position(s) - the session "
                        "closes in %d minute(s).",
                        CountOpenPositions(-1), (toSec - now) / 60);
            CloseAllPositions();
         }
         return(true);        // and take nothing new into the close
      }
      return(false);
   }

   // ---- manual fallback: Friday-only weekend flatten ----
   if(!InpCloseBeforeWeekend) return(false);

   MqlDateTime s;
   TimeToStruct(SessionZoneTime(), s);
   if(s.day_of_week == FRIDAY && s.hour >= InpWeekendCloseHour)
   {
      if(CountOpenPositions(-1) > 0)
      {
         Print("XAUUSD_MTF_RSI_MACD_BB_EA: flattening all positions ahead of the weekend.");
         CloseAllPositions();
      }
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Position sizing                                                   |
//+------------------------------------------------------------------+
double CalcLotSize(double slDistance)
{
   double lotStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   // Fixed lot: trade the same size every time, whatever the account balance.
   // Risk then varies with the stop distance instead of the other way round.
   if(InpUseFixedLot)
   {
      double fixed = InpFixedLot;
      if(fixed < minLot) fixed = minLot;
      if(fixed > maxLot) fixed = maxLot;
      if(fixed > InpMaxLotSize) fixed = InpMaxLotSize;
      fixed = MathFloor(fixed / lotStep) * lotStep;   // respect the broker's step
      return(NormalizeDouble(fixed, 2));
   }

   double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * InpRiskPercent / 100.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tickValue <= 0.0 || tickSize <= 0.0 || slDistance <= 0.0)
      return(minLot);

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0.0) return(minLot);

   double lots = riskMoney / lossPerLot;

   // normalize to lot step
   lots = MathFloor(lots / lotStep) * lotStep;

   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   if(lots > InpMaxLotSize) lots = MathFloor(InpMaxLotSize / lotStep) * lotStep;

   return(NormalizeDouble(lots, 2));
}

//+------------------------------------------------------------------+
//| One pip in price terms. A pip is ten broker points, so on a       |
//| 2-digit gold feed (point 0.01) a pip is 0.10 and 60 pips is $6.00 |
//| per 0.01 lot (one lot = 100oz, so $1 of price = $1 per 0.01 lot). |
//+------------------------------------------------------------------+
double PipSize()
{
   return(SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 10.0);
}

//+------------------------------------------------------------------+
//| Fractal/pivot scan for the most recent swing low: a closed bar    |
//| whose low is strictly below the low of InpSwingPivotWidth bars on |
//| EITHER side of it (more recent side and older side). Scans back   |
//| from the last closed bar (shift 1) over InpSwingScanBars bars and |
//| returns the FIRST (i.e. most recent) pivot found.                 |
//+------------------------------------------------------------------+
bool FindRecentSwingLow(const ENUM_TIMEFRAMES tf, double &swingLowPrice)
{
   int pivotWidth = (InpSwingPivotWidth > 1) ? InpSwingPivotWidth : 1;
   int scanBars   = (InpSwingScanBars > pivotWidth + 1) ? InpSwingScanBars : pivotWidth + 1;
   int need       = scanBars + pivotWidth; // shift1..shift(need)

   double lowArr[];
   ArraySetAsSeries(lowArr, true);
   if(CopyLow(_Symbol, tf, 1, need, lowArr) < need) return(false);

   for(int i = pivotWidth; i < scanBars; i++)
   {
      bool isPivot = true;
      for(int k = 1; k <= pivotWidth; k++)
      {
         if(lowArr[i-k] <= lowArr[i] || lowArr[i+k] <= lowArr[i]) { isPivot = false; break; }
      }
      if(isPivot)
      {
         swingLowPrice = lowArr[i];
         return(true);
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Pull indicator data for one timeframe and score the 4 criteria    |
//| in both directions. buyCount/sellCount range 0-4.                 |
//+------------------------------------------------------------------+
bool EvaluateTimeframe(const ENUM_TIMEFRAMES tf, const int hRsi, const int hMacd,
                       const int hBands, TFSignal &s)
{
   s.valid = false;

   int lookback = (InpRsiLookback > 1) ? InpRsiLookback : 1;

   double rsiArr[];
   ArraySetAsSeries(rsiArr, true);
   int rsiNeed = lookback + 1;
   if(CopyBuffer(hRsi, 0, 1, rsiNeed, rsiArr) < rsiNeed) return(false);
   s.rsiNow  = rsiArr[0];
   s.rsiPrev = rsiArr[lookback];

   double macdMain[], macdSignal[];
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   if(CopyBuffer(hMacd, 0, 1, 2, macdMain) < 2) return(false);
   if(CopyBuffer(hMacd, 1, 1, 2, macdSignal) < 2) return(false);
   s.macdHist1 = macdMain[0] - macdSignal[0];
   s.macdHist2 = macdMain[1] - macdSignal[1];

   double bbMid[];
   ArraySetAsSeries(bbMid, true);
   if(CopyBuffer(hBands, 0, 1, 1, bbMid) < 1) return(false);
   s.bbMiddle1 = bbMid[0];

   double closeArr[];
   ArraySetAsSeries(closeArr, true);
   if(CopyClose(_Symbol, tf, 1, 1, closeArr) < 1) return(false);
   s.close1 = closeArr[0];

   s.haveSwingLow = FindRecentSwingLow(tf, s.swingLow);

   // ---- BUY: RSI>level & rising, MACD hist positive, close>mid band, ----
   // ---- structure holds above the swing low (no breakdown)           ----
   bool b1 = (s.rsiNow > InpRsiBuyLevel) && (s.rsiNow > s.rsiPrev);
   bool b2 = (s.macdHist1 > 0.0);
   bool b3 = (s.close1 > s.bbMiddle1);
   bool b4 = s.haveSwingLow && (s.close1 > s.swingLow);
   s.buyCount     = (b1?1:0) + (b2?1:0) + (b3?1:0) + (b4?1:0);
   s.swingHoldBuy = b4;

   // ---- SELL: RSI<level & falling, MACD hist negative & expanding, ----
   // ---- close<mid band, CONFIRMED close below the swing low        ----
   bool se1 = (s.rsiNow < InpRsiSellLevel) && (s.rsiNow < s.rsiPrev);
   bool se2 = (s.macdHist1 < 0.0) && (s.macdHist1 < s.macdHist2);
   bool se3 = (s.close1 < s.bbMiddle1);
   bool se4 = s.haveSwingLow && (s.close1 < s.swingLow);
   s.sellCount       = (se1?1:0) + (se2?1:0) + (se3?1:0) + (se4?1:0);
   s.swingBreakSell  = se4;

   s.valid = true;
   return(true);
}

//+------------------------------------------------------------------+
//| Combined multi-timeframe result                                   |
//+------------------------------------------------------------------+
struct MtfResult
{
   bool     valid;
   TFSignal tf1, tf2, tf3;
   int      net1, net2, net3;     // buyCount - sellCount, -4..+4, per TF
   double   combinedScore;        // -100..+100
   int      overallSign;          // +1 buy lean, -1 sell lean, 0 flat
   int      agreeingTF;           // TFs at |net|>=3 agreeing with overallSign
   bool     swingConfirmed;
   string   label;
};

//+------------------------------------------------------------------+
//| Pull and score all three timeframes, then combine into one signal |
//+------------------------------------------------------------------+
bool EvaluateMtf(MtfResult &r)
{
   r.valid = false;
   if(!EvaluateTimeframe(InpTF1, hRsi1, hMacd1, hBands1, r.tf1)) return(false);
   if(!EvaluateTimeframe(InpTF2, hRsi2, hMacd2, hBands2, r.tf2)) return(false);
   if(!EvaluateTimeframe(InpTF3, hRsi3, hMacd3, hBands3, r.tf3)) return(false);

   r.net1 = r.tf1.buyCount - r.tf1.sellCount;
   r.net2 = r.tf2.buyCount - r.tf2.sellCount;
   r.net3 = r.tf3.buyCount - r.tf3.sellCount;

   double totalWeight = InpWeightTF1 + InpWeightTF2 + InpWeightTF3;
   double weightedSum = r.net1*InpWeightTF1 + r.net2*InpWeightTF2 + r.net3*InpWeightTF3;
   double maxWeighted  = 4.0 * totalWeight;
   double score = (maxWeighted > 0.0) ? 100.0 * weightedSum / maxWeighted : 0.0;

   int sign = (score > 0.0) ? 1 : ((score < 0.0) ? -1 : 0);

   // Swing-structure confirmation on ANY timeframe, in the direction the
   // score already leans, is the strongest single piece of evidence - it
   // upgrades a "leaning" score toward a "confirmed" one.
   bool swingConfirmed = false;
   if(sign > 0)
      swingConfirmed = r.tf1.swingHoldBuy || r.tf2.swingHoldBuy || r.tf3.swingHoldBuy;
   else if(sign < 0)
      swingConfirmed = r.tf1.swingBreakSell || r.tf2.swingBreakSell || r.tf3.swingBreakSell;

   if(swingConfirmed)
      score += sign * InpSwingBonusPoints;

   if(score > 100.0) score = 100.0;
   if(score < -100.0) score = -100.0;

   r.combinedScore  = NormalizeDouble(score, 1);
   r.overallSign    = (r.combinedScore > 0.0) ? 1 : ((r.combinedScore < 0.0) ? -1 : 0);
   r.swingConfirmed = swingConfirmed;

   int agreeing = 0;
   if(r.overallSign > 0)
   {
      if(r.net1 >= 3) agreeing++;
      if(r.net2 >= 3) agreeing++;
      if(r.net3 >= 3) agreeing++;
   }
   else if(r.overallSign < 0)
   {
      if(r.net1 <= -3) agreeing++;
      if(r.net2 <= -3) agreeing++;
      if(r.net3 <= -3) agreeing++;
   }
   r.agreeingTF = agreeing;

   string dir = (r.overallSign > 0) ? "Buy" : ((r.overallSign < 0) ? "Sell" : "Neutral");
   if(r.overallSign == 0)
      r.label = "NEUTRAL";
   else if(agreeing >= 3)
      r.label = StringFormat("STRONG %s%s", dir, r.swingConfirmed ? " (confirmed)" : "");
   else if(agreeing == 2)
      r.label = StringFormat("MODERATE %s%s", dir, r.swingConfirmed ? " (confirmed)" : " (building)");
   else
      r.label = StringFormat("WEAK %s", dir);

   r.valid = true;
   return(true);
}

//+------------------------------------------------------------------+
//| Does the combined signal clear the configured entry bar?          |
//+------------------------------------------------------------------+
bool Qualifies(const MtfResult &r, const bool isBuy)
{
   if(isBuy  && r.overallSign <= 0) return(false);
   if(!isBuy && r.overallSign >= 0) return(false);
   if(MathAbs(r.combinedScore) < InpEntryThreshold) return(false);
   if(r.agreeingTF < InpMinAgreeingTF)               return(false);
   return(true);
}

//+------------------------------------------------------------------+
//| Chart dashboard: full per-timeframe breakdown, not just the total |
//+------------------------------------------------------------------+
void ShowDashboard(const MtfResult &r)
{
   if(!InpShowDashboard) return;

   string txt = "XAUUSD MTF RSI/MACD/BB EA\n";
   txt += StringFormat("TF1 %s: RSI %.1f (prev %.1f) | MACD hist %.4f (prev %.4f) | close %s mid %.2f | swingLow %s\n",
                        EnumToString(InpTF1), r.tf1.rsiNow, r.tf1.rsiPrev, r.tf1.macdHist1, r.tf1.macdHist2,
                        r.tf1.close1 > r.tf1.bbMiddle1 ? "above" : "below", r.tf1.bbMiddle1,
                        r.tf1.haveSwingLow ? DoubleToString(r.tf1.swingLow,2) : "n/a");
   txt += StringFormat("   -> buy %d/4  sell %d/4  net %+d\n", r.tf1.buyCount, r.tf1.sellCount, r.net1);

   txt += StringFormat("TF2 %s: RSI %.1f (prev %.1f) | MACD hist %.4f (prev %.4f) | close %s mid %.2f | swingLow %s\n",
                        EnumToString(InpTF2), r.tf2.rsiNow, r.tf2.rsiPrev, r.tf2.macdHist1, r.tf2.macdHist2,
                        r.tf2.close1 > r.tf2.bbMiddle1 ? "above" : "below", r.tf2.bbMiddle1,
                        r.tf2.haveSwingLow ? DoubleToString(r.tf2.swingLow,2) : "n/a");
   txt += StringFormat("   -> buy %d/4  sell %d/4  net %+d\n", r.tf2.buyCount, r.tf2.sellCount, r.net2);

   txt += StringFormat("TF3 %s: RSI %.1f (prev %.1f) | MACD hist %.4f (prev %.4f) | close %s mid %.2f | swingLow %s\n",
                        EnumToString(InpTF3), r.tf3.rsiNow, r.tf3.rsiPrev, r.tf3.macdHist1, r.tf3.macdHist2,
                        r.tf3.close1 > r.tf3.bbMiddle1 ? "above" : "below", r.tf3.bbMiddle1,
                        r.tf3.haveSwingLow ? DoubleToString(r.tf3.swingLow,2) : "n/a");
   txt += StringFormat("   -> buy %d/4  sell %d/4  net %+d\n", r.tf3.buyCount, r.tf3.sellCount, r.net3);

   txt += StringFormat("\nCombined score %+.1f/100 (need |score|>=%.0f)  agreeing TFs %d/3 (need >=%d)\n",
                        r.combinedScore, InpEntryThreshold, r.agreeingTF, InpMinAgreeingTF);
   txt += StringFormat("Signal: %s%s\n", r.label, r.swingConfirmed ? "  [swing confirmed]" : "");
   txt += "Score = strength of agreement across timeframes, NOT a win probability.";

   Comment(txt);
}

//+------------------------------------------------------------------+
//| Open a new position with fixed pip stop-loss (and optional TP)    |
//+------------------------------------------------------------------+
void OpenTrade(bool isBuy, const MtfResult &r)
{
   double slDistance = InpStopLossPips * PipSize();
   if(slDistance <= 0.0) return;

   double lots = CalcLotSize(slDistance);
   if(lots <= 0.0)
   {
      Print("XAUUSD_MTF_RSI_MACD_BB_EA: calculated lot size is 0, skipping entry.");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   double price = isBuy ? tick.ask : tick.bid;
   double sl    = isBuy ? price - slDistance : price + slDistance;
   double tp    = 0.0;
   if(InpUseTakeProfit)
   {
      double tpDistance = slDistance * InpRiskRewardRatio;
      tp = isBuy ? price + tpDistance : price - tpDistance;
   }

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   if(tp > 0.0) tp = NormalizeDouble(tp, digits);

   string comment = StringFormat("MTF-RSI-MACD-BB score %+.0f", r.combinedScore);
   bool ok = isBuy ? trade.Buy(lots, _Symbol, price, sl, tp, comment)
                    : trade.Sell(lots, _Symbol, price, sl, tp, comment);

   if(ok)
   {
      g_tradesToday++;
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: %s opened - %s. lots=%.2f price=%.2f sl=%.2f tp=%.2f "
                  "score=%+.1f agreeingTF=%d/3%s",
                  isBuy ? "BUY" : "SELL", r.label, lots, price, sl, tp, r.combinedScore, r.agreeingTF,
                  r.swingConfirmed ? " swing-confirmed" : "");
   }
   else
   {
      PrintFormat("XAUUSD_MTF_RSI_MACD_BB_EA: order failed (%s). retcode=%d desc=%s",
                  isBuy ? "BUY" : "SELL", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Breakeven + pip-distance trailing stop for open positions.        |
//| Runs every tick (independent of new-bar timing) so the trail      |
//| tightens as soon as price moves, not just once per bar.           |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   double trailStart = InpTrailStartPips * PipSize();
   double trailDist  = InpTrailPips * PipSize();
   double beTrigger  = InpBreakevenTriggerPips * PipSize();

   long   stopsLevelPts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double point         = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist   = stopsLevelPts * point;
   int    digits        = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      long   type       = PositionGetInteger(POSITION_TYPE);
      double openPrice  = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSl  = PositionGetDouble(POSITION_SL);
      double currentTp  = PositionGetDouble(POSITION_TP);

      if(type == POSITION_TYPE_BUY)
      {
         double profit = tick.bid - openPrice;
         double newSl  = currentSl;
         bool   haveSl = currentSl > 0.0;

         if(InpUseBreakeven && profit >= beTrigger)
         {
            double beSl = openPrice + InpBreakevenBufferPts * point;
            if(!haveSl || beSl > newSl) newSl = beSl;
         }
         if(profit >= trailStart)
         {
            double trailSl = tick.bid - trailDist;
            if(!haveSl || trailSl > newSl) newSl = trailSl;
         }

         newSl = NormalizeDouble(newSl, digits);
         if(newSl > 0.0 && (!haveSl || newSl > currentSl) && (tick.bid - newSl) >= minStopDist)
            trade.PositionModify(ticket, newSl, currentTp);
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double profit = openPrice - tick.ask;
         double newSl  = currentSl;
         bool   haveSl = currentSl > 0.0;

         if(InpUseBreakeven && profit >= beTrigger)
         {
            double beSl = openPrice - InpBreakevenBufferPts * point;
            if(!haveSl || beSl < newSl) newSl = beSl;
         }
         if(profit >= trailStart)
         {
            double trailSl = tick.ask + trailDist;
            if(!haveSl || trailSl < newSl) newSl = trailSl;
         }

         newSl = NormalizeDouble(newSl, digits);
         if(newSl > 0.0 && (!haveSl || newSl < currentSl) && (newSl - tick.ask) >= minStopDist)
            trade.PositionModify(ticket, newSl, currentTp);
      }
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   UpdateDailyTracking();

   // Trailing/breakeven management runs every tick, independent of new-bar
   // timing, so profit gets locked in as soon as price moves - not just
   // once per bar.
   ManageOpenPositions();

   bool sessionCloseBlock = HandleSessionClose();

   // Only look for new entries once per new bar on TF1 (the shortest
   // configured timeframe).
   if(!IsNewBar()) return;

   MtfResult r;
   if(!EvaluateMtf(r)) return;

   ShowDashboard(r);

   if(sessionCloseBlock) return;
   if(g_dailyLossHit) return;
   if(g_dailyTargetHit) return;
   if(g_dailyLockHit) return;
   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay) return;
   if(!IsWithinSession()) return;
   if(!SpreadIsAcceptable()) return;
   if(CountOpenPositions(-1) >= InpMaxOpenPositions) return;

   bool buySignal  = Qualifies(r, true);
   bool sellSignal = Qualifies(r, false);

   // A simultaneous buy and sell pays the spread twice and nets to nothing on
   // a netting account, so an opposing signal is skipped while a position is
   // open unless InpAllowOpposite is set.
   int openBuys  = CountOpenPositions(0);
   int openSells = CountOpenPositions(1);

   if(buySignal)
   {
      if(openSells > 0 && !InpAllowOpposite)
         Print("XAUUSD_MTF_RSI_MACD_BB_EA: BUY signal skipped - an opposing SELL is open.");
      else
         OpenTrade(true, r);
   }
   else if(sellSignal)
   {
      if(openBuys > 0 && !InpAllowOpposite)
         Print("XAUUSD_MTF_RSI_MACD_BB_EA: SELL signal skipped - an opposing BUY is open.");
      else
         OpenTrade(false, r);
   }
}
//+------------------------------------------------------------------+
