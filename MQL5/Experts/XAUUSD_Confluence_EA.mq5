//+------------------------------------------------------------------+
//|                                       XAUUSD_Confluence_EA.mq5   |
//|                                                                    |
//| Multi-indicator trend/momentum confluence EA for XAUUSD (Gold).   |
//|                                                                    |
//| STRATEGY SUMMARY                                                   |
//| ------------------------------------------------------------------ |
//| Three confluences vote on each direction, each at two strengths:   |
//|                                                                    |
//|  1. TREND    - pass: higher-timeframe EMA bias agrees AND the      |
//|                working-TF fast/slow EMAs are aligned with a cross  |
//|                inside InpCrossLookbackBars.                        |
//|                confirmed: the EMAs have separated by at least      |
//|                InpConfirmEmaGapAtr x ATR.                          |
//|  2. MOMENTUM - pass: MACD on the right side of its signal and      |
//|                turning, RSI past the midline but not extreme.      |
//|                confirmed: MACD histogram expanding AND RSI at      |
//|                least InpConfirmRsiMargin past the midline.         |
//|  3. STRENGTH - pass: ADX >= InpAdxMinLevel with +DI/-DI aligned.   |
//|                confirmed: ADX >= InpConfirmAdxLevel AND the DI     |
//|                spread is at least InpConfirmDiGap wide.            |
//|                                                                    |
//| A trade needs InpMinConfluences of the three (default 2) with at   |
//| least InpMinConfirmed of them confirmed (default 1), AND a setup    |
//| score of at least InpMinConfidence - currently 0, i.e. the score    |
//| gate is OFF, to reach a target of about 10 fills per day.           |
//|                                                                    |
//| Every other filter still applies, so this is the unscored version   |
//| of the strategy rather than an unfiltered one. Measured fills/day:  |
//|   gate 50: 4.7 (2 positions) 6.9 (4) 8.1 (6) - tops out near 9.3    |
//|   gate 45: 5.6               8.5     10.1                          |
//|   gate off:6.4              10.0     12.0                          |
//| Set InpMinConfidence = 50 to return to the selective ~4.7/day.      |
//|                                                                    |
//| Positions are correlated (same symbol and direction), so four at    |
//| 0.02 lots with a $6.00 stop risks roughly $32 together.            |
//|                                                                    |
//| The score measures how strongly the indicators agree - it is NOT   |
//| a probability that the trade wins.                                 |
//|                                                                    |
//| Bollinger Bands act as a VETO outside the vote (never buy at/above |
//| the upper band, never sell at/below the lower band). ATR drives    |
//| stop-loss/take-profit distances and position sizing. Session,      |
//| spread, daily-loss and max-trade filters protect against illiquid  |
//| hours, wide spreads and over-trading.                              |
//|                                                                    |
//| NOTE: at 2 of 3 the trend leg is not mandatory, so a trade can     |
//| open against the higher-timeframe trend. Set                       |
//| InpRequireTrendConfluence = true to prevent that.                  |
//|                                                                    |
//| Entries are evaluated once per closed working-timeframe bar (M5 by |
//| default). With InpMaxTradesPerDay = 0 there is no daily entry cap, |
//| so the brakes are InpMaxOpenPositions, the same-direction rule, the |
//| daily-loss circuit breaker and the session filter.                 |
//|                                                                    |
//| IMPORTANT DISCLAIMER                                               |
//| ------------------------------------------------------------------ |
//| No trading system - this one included - can guarantee profit.     |
//| XAUUSD is a highly volatile, news-sensitive instrument. Backtest   |
//| across multiple market regimes, forward-test on a demo account,    |
//| and only risk capital you can afford to lose. Past performance     |
//| does not guarantee future results.                                 |
//+------------------------------------------------------------------+
#property copyright "XAUUSD_Confluence_EA"
#property link      ""
#property version   "1.00"
#property strict
#property description "Multi-indicator (EMA/MACD/RSI/ADX/ATR/Bollinger) confluence EA for XAUUSD with ATR position sizing, trailing stop, session/spread/daily-loss filters. Educational use - backtest and demo-trade thoroughly before risking real capital."

#include <Trade\Trade.mqh>

//================================= INPUTS ====================================

input group "=== General ==="
input ulong   InpMagicNumber        = 20260908;    // Magic number
input int     InpSlippagePoints     = 30;           // Max slippage (points)
input bool    InpTradeXAUUSDOnly    = true;         // Require chart symbol to contain "XAU"
input int     InpMaxOpenPositions   = 4;            // Max simultaneous open positions (this EA/symbol)
input bool    InpAllowOpposite      = false;        // Allow a buy and a sell open at the same time

input group "=== Timeframes ==="
input ENUM_TIMEFRAMES InpWorkTF     = PERIOD_M5;    // Working timeframe (entries evaluated at each close)
input ENUM_TIMEFRAMES InpTrendTF    = PERIOD_H4;    // Higher timeframe (trend bias)

input group "=== Trend Filter (higher timeframe EMA) ==="
input int      InpTrendEmaPeriod    = 200;          // Trend EMA period

input group "=== Entry Trigger (working timeframe EMA cross) ==="
input int      InpEmaFastPeriod     = 20;           // Fast EMA period
input int      InpEmaSlowPeriod     = 50;           // Slow EMA period
input int      InpCrossLookbackBars = 8;            // Cross must be this fresh (bars); 0 = alignment only

input group "=== Momentum Confirmation ==="
input int      InpMacdFast          = 12;           // MACD fast EMA
input int      InpMacdSlow          = 26;           // MACD slow EMA
input int      InpMacdSignal        = 9;            // MACD signal SMA
input int      InpRsiPeriod         = 14;           // RSI period
input double   InpRsiUpperBlock     = 70.0;          // Block BUY if RSI above this (overbought)
input double   InpRsiLowerBlock     = 30.0;          // Block SELL if RSI below this (oversold)
input double   InpRsiMidline        = 50.0;          // RSI midline used for directional bias

input group "=== Entry Rule (how many confluences are required) ==="
input int      InpMinConfluences    = 2;            // Confluences required to enter (1-3)
input bool     InpRequireConfirm    = true;         // Require confirmed confluence(s)
input int      InpMinConfirmed      = 1;            // Confirmed confluences required
input bool     InpRequireTrendConfluence = false;   // Trend must be one of the passing confluences
input double   InpConfirmEmaGapAtr  = 0.25;         // Trend confirm: EMA gap >= this x ATR
input double   InpConfirmRsiMargin  = 5.0;          // Momentum confirm: RSI this far past midline
input double   InpConfirmAdxLevel   = 28.0;         // Strength confirm: minimum ADX
input double   InpConfirmDiGap      = 8.0;          // Strength confirm: minimum |+DI - -DI|
input bool     InpUseBandsVeto      = true;         // Veto entries at/beyond the Bollinger band
input double   InpMinConfidence     = 0.0;          // Min setup score 0-100 to enter (0 = off)
input bool     InpShowConfidence    = true;         // Show the live score in the chart comment

input group "=== Trend-Strength Filter (ADX/DMI) ==="
input int      InpAdxPeriod         = 14;           // ADX period
input double   InpAdxMinLevel       = 22.0;          // Minimum ADX to allow new trades

input group "=== Volatility / Bands ==="
input int      InpAtrPeriod         = 14;           // ATR period
input int      InpBandsPeriod       = 20;            // Bollinger Bands period
input double   InpBandsDeviation    = 2.0;            // Bollinger Bands deviation

input group "=== Risk Management ==="
input double   InpRiskPercent       = 1.0;           // Risk per trade (% of equity)
input double   InpAtrSlMultiplier   = 1.8;            // Stop-loss = ATR * this multiplier
input double   InpRiskRewardRatio   = 1.8;            // Take-profit = SL distance * this ratio
input double   InpMaxLotSize        = 5.0;            // Hard cap on calculated lot size
input double   InpMaxDailyLossPct   = 3.0;            // Stop new trades after this % equity loss in a day
input int      InpMaxTradesPerDay   = 0;              // Max new entries per day (0 = unlimited)

input group "=== Trade Management (breakeven / trailing) ==="
input double   InpBreakevenAtrMult  = 1.0;            // Move SL to breakeven once profit >= ATR * this
input int      InpBreakevenBufferPts= 20;             // Points of buffer added at breakeven
input double   InpTrailStartAtrMult = 1.5;            // Start trailing once profit >= ATR * this
input double   InpTrailAtrMult      = 1.2;            // Trail distance = ATR * this

input group "=== Session Filter (broker/server time) ==="
input bool     InpUseSessionFilter  = true;           // Enable session filter
input int      InpSessionStartHour  = 7;              // Session start hour (server time)
input int      InpSessionStartMin   = 0;               // Session start minute
input int      InpSessionEndHour    = 20;              // Session end hour (server time)
input int      InpSessionEndMin     = 0;                // Session end minute
input bool     InpCloseBeforeWeekend= true;             // Flatten all positions before weekend close
input int      InpWeekendCloseHour  = 20;               // Friday hour (server time) to start flattening

input group "=== Spread Filter ==="
input int      InpMaxSpreadPoints   = 350;              // Max allowed spread (points) to open new trades

//================================= GLOBALS ====================================

CTrade         trade;
int            hEmaTrend   = INVALID_HANDLE;
int            hEmaFast    = INVALID_HANDLE;
int            hEmaSlow    = INVALID_HANDLE;
int            hMacd       = INVALID_HANDLE;
int            hRsi        = INVALID_HANDLE;
int            hAdx        = INVALID_HANDLE;
int            hAtr        = INVALID_HANDLE;
int            hBands      = INVALID_HANDLE;

datetime       g_lastBarTime      = 0;
datetime       g_currentDay       = 0;
double         g_dayStartEquity   = 0.0;
int            g_tradesToday      = 0;
bool           g_dailyLossHit     = false;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTradeXAUUSDOnly && StringFind(_Symbol, "XAU") < 0)
   {
      Print("XAUUSD_Confluence_EA: chart symbol '", _Symbol, "' does not contain 'XAU'. ",
            "Attach to a Gold (XAUUSD) chart, or disable InpTradeXAUUSDOnly to override.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   hEmaTrend = iMA(_Symbol, InpTrendTF, InpTrendEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   hEmaFast  = iMA(_Symbol, InpWorkTF,  InpEmaFastPeriod,  0, MODE_EMA, PRICE_CLOSE);
   hEmaSlow  = iMA(_Symbol, InpWorkTF,  InpEmaSlowPeriod,  0, MODE_EMA, PRICE_CLOSE);
   hMacd     = iMACD(_Symbol, InpWorkTF, InpMacdFast, InpMacdSlow, InpMacdSignal, PRICE_CLOSE);
   hRsi      = iRSI(_Symbol, InpWorkTF, InpRsiPeriod, PRICE_CLOSE);
   hAdx      = iADX(_Symbol, InpWorkTF, InpAdxPeriod);
   hAtr      = iATR(_Symbol, InpWorkTF, InpAtrPeriod);
   hBands    = iBands(_Symbol, InpWorkTF, InpBandsPeriod, 0, InpBandsDeviation, PRICE_CLOSE);

   if(hEmaTrend==INVALID_HANDLE || hEmaFast==INVALID_HANDLE || hEmaSlow==INVALID_HANDLE ||
      hMacd==INVALID_HANDLE || hRsi==INVALID_HANDLE || hAdx==INVALID_HANDLE ||
      hAtr==INVALID_HANDLE || hBands==INVALID_HANDLE)
   {
      Print("XAUUSD_Confluence_EA: failed to create one or more indicator handles. Error=", GetLastError());
      return(INIT_FAILED);
   }

   if(InpMinConfluences < 1 || InpMinConfluences > 3)
   {
      Print("XAUUSD_Confluence_EA: InpMinConfluences must be 1, 2 or 3.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpRequireConfirm && (InpMinConfirmed < 1 || InpMinConfirmed > InpMinConfluences))
   {
      Print("XAUUSD_Confluence_EA: InpMinConfirmed must be between 1 and InpMinConfluences.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

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
   if(hEmaTrend != INVALID_HANDLE) IndicatorRelease(hEmaTrend);
   if(hEmaFast  != INVALID_HANDLE) IndicatorRelease(hEmaFast);
   if(hEmaSlow  != INVALID_HANDLE) IndicatorRelease(hEmaSlow);
   if(hMacd     != INVALID_HANDLE) IndicatorRelease(hMacd);
   if(hRsi      != INVALID_HANDLE) IndicatorRelease(hRsi);
   if(hAdx      != INVALID_HANDLE) IndicatorRelease(hAdx);
   if(hAtr      != INVALID_HANDLE) IndicatorRelease(hAtr);
   if(hBands    != INVALID_HANDLE) IndicatorRelease(hBands);
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
   }

   if(!g_dailyLossHit && g_dayStartEquity > 0.0)
   {
      double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
      double lossPct  = (g_dayStartEquity - equity) / g_dayStartEquity * 100.0;
      if(lossPct >= InpMaxDailyLossPct)
      {
         g_dailyLossHit = true;
         Print("XAUUSD_Confluence_EA: daily loss limit reached (", DoubleToString(lossPct,2),
               "% >= ", DoubleToString(InpMaxDailyLossPct,2), "%). New entries suspended until next day.");
      }
   }
}

//+------------------------------------------------------------------+
//| Returns true exactly once per new bar on the working timeframe    |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, InpWorkTF, 0);
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
bool IsWithinSession()
{
   if(!InpUseSessionFilter) return(true);

   MqlDateTime s;
   TimeToStruct(TimeCurrent(), s);
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
//| Weekend flatten check (Friday close protection)                   |
//+------------------------------------------------------------------+
bool HandleWeekendFlatten()
{
   if(!InpCloseBeforeWeekend) return(false);

   MqlDateTime s;
   TimeToStruct(TimeCurrent(), s);
   if(s.day_of_week == FRIDAY && s.hour >= InpWeekendCloseHour)
   {
      if(CountOpenPositions(-1) > 0)
      {
         Print("XAUUSD_Confluence_EA: flattening all positions ahead of the weekend.");
         CloseAllPositions();
      }
      return(true); // block new entries after weekend-close hour on Fridays
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Position sizing: risk InpRiskPercent of equity on slDistance      |
//+------------------------------------------------------------------+
double CalcLotSize(double slDistance)
{
   double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * InpRiskPercent / 100.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

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
//| Snapshot of the indicator values used for a decision, taken from  |
//| the last two CLOSED bars (shift 1 = last closed, shift 2 = prior) |
//+------------------------------------------------------------------+
struct SignalData
{
   bool   valid;
   double trendEma, trendClose;
   double emaFast1, emaSlow1;
   bool   freshBullCross, freshBearCross;
   double macdMain1, macdSignal1, macdMain2, macdSignal2;
   double macdHist1, macdHist2;
   double rsi1;
   double adx1, plusDi1, minusDi1;
   double atr0; // latest ATR (used for sizing/stops), includes forming bar - fine for volatility measure
   double bandsUpper1, bandsLower1, bandsMiddle1;
   double close1;
};

//+------------------------------------------------------------------+
//| Pull all indicator buffers needed for a trading decision           |
//+------------------------------------------------------------------+
bool GetSignalData(SignalData &d)
{
   double buf[];
   d.valid = false;

   // --- Higher timeframe trend EMA (use last closed HTF bar) ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hEmaTrend, 0, 1, 1, buf) < 1) return(false);
   d.trendEma = buf[0];

   double closeBuf[];
   ArraySetAsSeries(closeBuf, true);
   if(CopyClose(_Symbol, InpTrendTF, 1, 1, closeBuf) < 1) return(false);
   d.trendClose = closeBuf[0];

   // --- Working timeframe fast/slow EMA over the cross-lookback window ---
   // At the exact bar the EMAs cross, ADX is still building and MACD has not
   // confirmed yet, so requiring the cross on the very last closed bar means
   // the three confluences almost never align. Accept a cross that happened
   // within InpCrossLookbackBars closed bars instead.
   int lookback = MathMax(0, InpCrossLookbackBars);
   int need     = lookback + 1;

   double fastArr[], slowArr[];
   ArraySetAsSeries(fastArr, true);
   ArraySetAsSeries(slowArr, true);
   if(CopyBuffer(hEmaFast, 0, 1, need, fastArr) < need) return(false);
   if(CopyBuffer(hEmaSlow, 0, 1, need, slowArr) < need) return(false);

   d.emaFast1 = fastArr[0];
   d.emaSlow1 = slowArr[0];

   if(lookback == 0)
   {
      // no cross required - EMA alignment alone satisfies the trend trigger
      d.freshBullCross = true;
      d.freshBearCross = true;
   }
   else
   {
      d.freshBullCross = false;
      d.freshBearCross = false;
      for(int i = 0; i < lookback; i++)
      {
         if(fastArr[i+1] <= slowArr[i+1] && fastArr[i] > slowArr[i]) d.freshBullCross = true;
         if(fastArr[i+1] >= slowArr[i+1] && fastArr[i] < slowArr[i]) d.freshBearCross = true;
      }
   }

   // --- MACD main(0)/signal(1) ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hMacd, 0, 1, 2, buf) < 2) return(false);
   d.macdMain1 = buf[0]; d.macdMain2 = buf[1];

   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hMacd, 1, 1, 2, buf) < 2) return(false);
   d.macdSignal1 = buf[0]; d.macdSignal2 = buf[1];

   d.macdHist1 = d.macdMain1 - d.macdSignal1;
   d.macdHist2 = d.macdMain2 - d.macdSignal2;

   // --- RSI ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hRsi, 0, 1, 1, buf) < 1) return(false);
   d.rsi1 = buf[0];

   // --- ADX main(0), +DI(1), -DI(2) ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hAdx, 0, 1, 1, buf) < 1) return(false);
   d.adx1 = buf[0];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hAdx, 1, 1, 1, buf) < 1) return(false);
   d.plusDi1 = buf[0];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hAdx, 2, 1, 1, buf) < 1) return(false);
   d.minusDi1 = buf[0];

   // --- ATR (latest value, incl. forming bar) for sizing/stops/trailing ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hAtr, 0, 0, 1, buf) < 1) return(false);
   d.atr0 = buf[0];

   // --- Bollinger Bands: upper(1), lower(2), middle(0) on last closed bar ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hBands, 1, 1, 1, buf) < 1) return(false);
   d.bandsUpper1 = buf[0];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hBands, 2, 1, 1, buf) < 1) return(false);
   d.bandsLower1 = buf[0];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hBands, 0, 1, 1, buf) < 1) return(false);
   d.bandsMiddle1 = buf[0];

   ArraySetAsSeries(closeBuf, true);
   if(CopyClose(_Symbol, InpWorkTF, 1, 1, closeBuf) < 1) return(false);
   d.close1 = closeBuf[0];

   d.valid = true;
   return(true);
}

//+------------------------------------------------------------------+
//| Score one confluence out of maxPoints.                            |
//| A pass earns 60%; the rest scales with how far past the           |
//| confirmation threshold the indicator actually sits.               |
//+------------------------------------------------------------------+
double ComponentScore(const bool passed, const bool confirmed,
                      const double strength, const double maxPoints)
{
   if(!passed) return(0.0);
   double score = maxPoints * 0.60;
   if(confirmed)
   {
      double s = strength;
      if(s < 0.0) s = 0.0;
      if(s > 1.0) s = 1.0;
      score += maxPoints * 0.40 * s;
   }
   return(score);
}

//+------------------------------------------------------------------+
//| Count how many of the three confluences a direction collects,     |
//| and how many of those reach their stricter "confirmed" threshold. |
//+------------------------------------------------------------------+
void ScoreConfluences(const SignalData &d, const bool isBuy,
                      int &passCount, int &confirmedCount, bool &trendPassed,
                      double &confidence)
{
   passCount      = 0;
   confirmedCount = 0;
   trendPassed    = false;
   confidence     = 0.0;

   const double maxPoints = 100.0 / 3.0;   // each confluence is worth a third

   // ---- Confluence 1: TREND ----
   bool htfOk   = isBuy ? (d.trendClose > d.trendEma) : (d.trendClose < d.trendEma);
   bool cross   = isBuy ? d.freshBullCross : d.freshBearCross;
   bool aligned = isBuy ? (d.emaFast1 > d.emaSlow1) : (d.emaFast1 < d.emaSlow1);
   bool trend   = (htfOk && cross && aligned);
   if(trend)
   {
      passCount++;
      trendPassed = true;
      // confirmed when the EMAs have genuinely separated, not merely touched
      double emaGap    = MathAbs(d.emaFast1 - d.emaSlow1);
      double gapNeeded = InpConfirmEmaGapAtr * d.atr0;
      bool   trendConf = (emaGap >= gapNeeded);
      if(trendConf) confirmedCount++;
      double gapStrength = (gapNeeded > 0.0) ? (emaGap / gapNeeded - 1.0) : 0.0;
      confidence += ComponentScore(true, trendConf, gapStrength, maxPoints);
   }

   // ---- Confluence 2: MOMENTUM ----
   bool macdOk = isBuy ? (d.macdMain1 > d.macdSignal1 && d.macdMain1 > d.macdMain2)
                       : (d.macdMain1 < d.macdSignal1 && d.macdMain1 < d.macdMain2);
   bool rsiOk  = isBuy ? (d.rsi1 > InpRsiMidline && d.rsi1 < InpRsiUpperBlock)
                       : (d.rsi1 < InpRsiMidline && d.rsi1 > InpRsiLowerBlock);
   if(macdOk && rsiOk)
   {
      passCount++;
      // confirmed when the histogram is still expanding and RSI is decisively past 50
      bool histExpanding = isBuy ? (d.macdHist1 > d.macdHist2) : (d.macdHist1 < d.macdHist2);
      bool rsiDecisive   = isBuy ? (d.rsi1 >= InpRsiMidline + InpConfirmRsiMargin)
                                 : (d.rsi1 <= InpRsiMidline - InpConfirmRsiMargin);
      bool momConf = (histExpanding && rsiDecisive);
      if(momConf) confirmedCount++;
      double rsiDist     = MathAbs(d.rsi1 - InpRsiMidline);
      double rsiStrength = (rsiDist - InpConfirmRsiMargin) / 15.0;
      confidence += ComponentScore(true, momConf, rsiStrength, maxPoints);
   }

   // ---- Confluence 3: STRENGTH ----
   bool diOk = isBuy ? (d.plusDi1 > d.minusDi1) : (d.minusDi1 > d.plusDi1);
   if(d.adx1 >= InpAdxMinLevel && diOk)
   {
      passCount++;
      double diGap     = MathAbs(d.plusDi1 - d.minusDi1);
      bool   strConf   = (d.adx1 >= InpConfirmAdxLevel && diGap >= InpConfirmDiGap);
      if(strConf) confirmedCount++;
      double adxHeadroom = MathMax(InpConfirmAdxLevel - InpAdxMinLevel, 1.0);
      double adxStrength = (d.adx1 - InpConfirmAdxLevel) / adxHeadroom;
      double diStrength  = (InpConfirmDiGap > 0.0) ? (diGap / InpConfirmDiGap - 1.0) : 0.0;
      confidence += ComponentScore(true, strConf, MathMin(adxStrength, diStrength), maxPoints);
   }

   confidence = NormalizeDouble(confidence, 1);
}

//+------------------------------------------------------------------+
//| Does a direction clear the configured entry bar?                  |
//+------------------------------------------------------------------+
bool Qualifies(const int passCount, const int confirmedCount,
               const bool trendPassed, const bool vetoed, const double confidence)
{
   if(vetoed)                                            return(false);
   if(confidence < InpMinConfidence)                     return(false);
   if(passCount < InpMinConfluences)                     return(false);
   if(InpRequireConfirm && confirmedCount < InpMinConfirmed) return(false);
   if(InpRequireTrendConfluence && !trendPassed)         return(false);
   return(true);
}

//+------------------------------------------------------------------+
//| Evaluate long/short confluence signals                            |
//+------------------------------------------------------------------+
void EvaluateSignals(const SignalData &d, bool &buySignal, bool &sellSignal)
{
   buySignal  = false;
   sellSignal = false;

   int    buyPass, buyConfirmed, sellPass, sellConfirmed;
   bool   buyTrend, sellTrend;
   double buyScore, sellScore;
   ScoreConfluences(d, true,  buyPass,  buyConfirmed,  buyTrend,  buyScore);
   ScoreConfluences(d, false, sellPass, sellConfirmed, sellTrend, sellScore);

   // Bollinger veto sits outside the vote: never chase an already-extended move
   bool buyVetoed  = (InpUseBandsVeto && d.close1 >= d.bandsUpper1);
   bool sellVetoed = (InpUseBandsVeto && d.close1 <= d.bandsLower1);

   if(buyVetoed)  buyScore  = 0.0;
   if(sellVetoed) sellScore = 0.0;

   bool buyOk  = Qualifies(buyPass,  buyConfirmed,  buyTrend,  buyVetoed,  buyScore);
   bool sellOk = Qualifies(sellPass, sellConfirmed, sellTrend, sellVetoed, sellScore);

   if(InpShowConfidence)
      Comment(StringFormat(
         "XAUUSD Confluence EA\n"
         "BUY  score %.0f%%  (%d/3, %d confirmed)%s\n"
         "SELL score %.0f%%  (%d/3, %d confirmed)%s\n"
         "need >= %d/3, %d confirmed, score >= %.0f%%\n"
         "score = setup quality, NOT a win probability",
         buyScore,  buyPass,  buyConfirmed,  buyVetoed  ? "  [VETO]" : "",
         sellScore, sellPass, sellConfirmed, sellVetoed ? "  [VETO]" : "",
         InpMinConfluences, InpRequireConfirm ? InpMinConfirmed : 0, InpMinConfidence));

   // if both directions qualify the picture is contradictory - stand aside
   buySignal  = (buyOk  && !sellOk);
   sellSignal = (sellOk && !buyOk);

   if(buySignal || sellSignal)
   {
      PrintFormat("XAUUSD_Confluence_EA: %s entry - setup score %.0f%% | "
                  "%d/3 confluences, %d confirmed (need %d/3, %d confirmed, score >= %.0f%%)",
                  buySignal ? "BUY" : "SELL",
                  buySignal ? buyScore : sellScore,
                  buySignal ? buyPass : sellPass,
                  buySignal ? buyConfirmed : sellConfirmed,
                  InpMinConfluences, InpRequireConfirm ? InpMinConfirmed : 0, InpMinConfidence);
   }
}

//+------------------------------------------------------------------+
//| Open a new position sized and protected per the risk settings     |
//+------------------------------------------------------------------+
void OpenTrade(bool isBuy, double atr)
{
   double slDistance = atr * InpAtrSlMultiplier;
   double tpDistance = slDistance * InpRiskRewardRatio;
   if(slDistance <= 0.0) return;

   double lots = CalcLotSize(slDistance);
   if(lots <= 0.0)
   {
      Print("XAUUSD_Confluence_EA: calculated lot size is 0, skipping entry.");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   double price = isBuy ? tick.ask : tick.bid;
   double sl    = isBuy ? price - slDistance : price + slDistance;
   double tp    = isBuy ? price + tpDistance : price - tpDistance;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   string comment = "XAUUSD-Confluence";
   bool ok = isBuy ? trade.Buy(lots, _Symbol, price, sl, tp, comment)
                    : trade.Sell(lots, _Symbol, price, sl, tp, comment);

   if(ok)
   {
      g_tradesToday++;
      PrintFormat("XAUUSD_Confluence_EA: %s opened. lots=%.2f price=%.2f sl=%.2f tp=%.2f atr=%.2f",
                  isBuy ? "BUY" : "SELL", lots, price, sl, tp, atr);
   }
   else
   {
      PrintFormat("XAUUSD_Confluence_EA: order failed (%s). retcode=%d desc=%s",
                  isBuy ? "BUY" : "SELL", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Breakeven + ATR trailing stop management for open positions       |
//+------------------------------------------------------------------+
void ManageOpenPositions(double atr)
{
   if(atr <= 0.0) return;

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

         // Breakeven
         if(profit >= InpBreakevenAtrMult * atr)
         {
            double beSl = openPrice + InpBreakevenBufferPts * point;
            if(beSl > newSl) newSl = beSl;
         }
         // Trailing
         if(profit >= InpTrailStartAtrMult * atr)
         {
            double trailSl = tick.bid - InpTrailAtrMult * atr;
            if(trailSl > newSl) newSl = trailSl;
         }

         newSl = NormalizeDouble(newSl, digits);
         if(newSl > currentSl && (tick.bid - newSl) >= minStopDist)
         {
            trade.PositionModify(ticket, newSl, currentTp);
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double profit = openPrice - tick.ask;
         double newSl  = currentSl;
         bool   haveSl = currentSl > 0.0;

         if(profit >= InpBreakevenAtrMult * atr)
         {
            double beSl = openPrice - InpBreakevenBufferPts * point;
            if(!haveSl || beSl < newSl) newSl = beSl;
         }
         if(profit >= InpTrailStartAtrMult * atr)
         {
            double trailSl = tick.ask + InpTrailAtrMult * atr;
            if(!haveSl || trailSl < newSl) newSl = trailSl;
         }

         newSl = NormalizeDouble(newSl, digits);
         if(newSl > 0.0 && (!haveSl || newSl < currentSl) && (newSl - tick.ask) >= minStopDist)
         {
            trade.PositionModify(ticket, newSl, currentTp);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   UpdateDailyTracking();

   // Pull latest ATR once per tick for trade management (trailing/breakeven runs every tick).
   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(hAtr, 0, 0, 1, atrBuf) == 1)
      ManageOpenPositions(atrBuf[0]);

   bool weekendBlock = HandleWeekendFlatten();

   // Only look for new entries once per new working-timeframe bar.
   if(!IsNewBar()) return;

   if(weekendBlock) return;
   if(g_dailyLossHit) return;
   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay) return;
   if(!IsWithinSession()) return;
   if(!SpreadIsAcceptable()) return;
   if(CountOpenPositions(-1) >= InpMaxOpenPositions) return;

   SignalData d;
   if(!GetSignalData(d)) return;

   bool buySignal, sellSignal;
   EvaluateSignals(d, buySignal, sellSignal);

   // A simultaneous buy and sell pays the spread twice and nets to nothing on
   // a netting account, so an opposing signal is skipped while a position is
   // open unless InpAllowOpposite is set.
   int openBuys  = CountOpenPositions(0);
   int openSells = CountOpenPositions(1);

   if(buySignal)
   {
      if(openSells > 0 && !InpAllowOpposite)
         Print("XAUUSD_Confluence_EA: BUY signal skipped - an opposing SELL is open.");
      else
         OpenTrade(true, d.atr0);
   }
   else if(sellSignal)
   {
      if(openBuys > 0 && !InpAllowOpposite)
         Print("XAUUSD_Confluence_EA: SELL signal skipped - an opposing BUY is open.");
      else
         OpenTrade(false, d.atr0);
   }
}
//+------------------------------------------------------------------+
