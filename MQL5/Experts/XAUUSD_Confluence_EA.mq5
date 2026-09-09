//+------------------------------------------------------------------+
//|                                       XAUUSD_Confluence_EA.mq5   |
//|                                                                    |
//| Multi-indicator trend/momentum confluence EA for XAUUSD (Gold).   |
//|                                                                    |
//| STRATEGY SUMMARY                                                   |
//| ------------------------------------------------------------------ |
//| 1. Higher-timeframe EMA defines the macro trend bias (only trade   |
//|    with the higher timeframe trend).                               |
//| 2. Working-timeframe EMA fast/slow crossover supplies the entry    |
//|    trigger.                                                        |
//| 3. MACD confirms momentum is aligned with the trigger.             |
//| 4. RSI filters out overbought/oversold extremes against the trade. |
//| 5. ADX/DMI confirms the market is actually trending (filters out   |
//|    choppy / range-bound conditions that whipsaw crossover systems).|
//| 6. ATR drives volatility-adaptive stop-loss/take-profit distances  |
//|    and position sizing (risk a fixed % of equity per trade).       |
//| 7. Bollinger Bands act as an extra "don't chase price into the     |
//|    opposite band" filter.                                          |
//| 8. Session, spread, daily-loss and max-trade filters protect       |
//|    against illiquid hours, wide spreads and over-trading.          |
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
#property version   "1.10"
#property strict
#property description "Multi-indicator (EMA/MACD/RSI/ADX/ATR/Bollinger) confluence EA for XAUUSD with ATR position sizing, trailing stop, session/spread/daily-loss filters. Educational use - backtest and demo-trade thoroughly before risking real capital."

#include <Trade\Trade.mqh>

//================================= INPUTS ====================================

input group "=== General ==="
input ulong   InpMagicNumber        = 20260908;    // Magic number
input int     InpSlippagePoints     = 30;           // Max slippage (points)
input bool    InpTradeXAUUSDOnly    = true;         // Require chart symbol to contain "XAU"
input int     InpMaxOpenPositions   = 1;            // Max simultaneous open positions (this EA/symbol)

input group "=== Timeframes ==="
input ENUM_TIMEFRAMES InpWorkTF     = PERIOD_M15;   // Working timeframe (entries)
input ENUM_TIMEFRAMES InpTrendTF    = PERIOD_H4;    // Higher timeframe (trend bias)

input group "=== Trend Filter (higher timeframe EMA) ==="
input int      InpTrendEmaPeriod    = 200;          // Trend EMA period

input group "=== Entry Trigger (working timeframe EMA cross) ==="
input int      InpEmaFastPeriod     = 20;           // Fast EMA period
input int      InpEmaSlowPeriod     = 50;           // Slow EMA period

input group "=== Momentum Confirmation ==="
input int      InpMacdFast          = 12;           // MACD fast EMA
input int      InpMacdSlow          = 26;           // MACD slow EMA
input int      InpMacdSignal        = 9;            // MACD signal SMA
input int      InpRsiPeriod         = 14;           // RSI period
input double   InpRsiUpperBlock     = 70.0;          // Block BUY if RSI above this (overbought)
input double   InpRsiLowerBlock     = 30.0;          // Block SELL if RSI below this (oversold)
input double   InpRsiMidline        = 50.0;          // RSI midline used for directional bias

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
input int      InpMaxTradesPerDay   = 6;              // Max new entries per calendar day

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

input group "=== Remote Bridge (iOS Monitor/Control App) ==="
input bool     InpBridgeEnabled       = false;           // Enable remote bridge (heartbeat + remote control)
input string   InpBridgeURL           = "https://your-bridge.example.com"; // Bridge base URL (must be whitelisted in Tools>Options>Expert Advisors)
input string   InpBridgeApiKey        = "";               // Shared API key (sent as X-Api-Key header)
input string   InpBridgeAccountTag    = "";                // Optional label for this account/EA instance (defaults to account login)
input int      InpBridgeHeartbeatSec  = 30;                // Seconds between status reports pushed to the bridge
input int      InpBridgePollSec       = 15;                // Seconds between remote-control polls (enable/disable, flatten)
input bool     InpBridgeFailSafeOpen  = true;               // If the bridge is unreachable, keep trading enabled (true) or pause (false)

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

// --- Remote bridge state (iOS monitor/control app talks to a bridge server; the EA never accepts inbound connections) ---
datetime       g_lastHeartbeatAt     = 0;
datetime       g_lastControlPollAt  = 0;
bool           g_remoteTradingEnabled = true;   // last-known "trading allowed" flag from the bridge
bool           g_remoteFlattenPending = false;  // set by the bridge to request an immediate flatten-all
bool           g_bridgeReachable      = false;  // did the last poll/heartbeat succeed
string         g_bridgeLastError      = "";

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

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_currentDay     = DateToDay(TimeCurrent());
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_tradesToday    = 0;
   g_dailyLossHit   = false;

   g_remoteTradingEnabled = true;
   g_remoteFlattenPending = false;
   g_bridgeReachable      = false;
   g_bridgeLastError      = "";

   if(InpBridgeEnabled && !MQLInfoInteger(MQL_TESTER))
   {
      EventSetTimer(5); // OnTimer fires every 5s; heartbeat/poll are throttled internally to their own intervals
      PollBridgeControl();   // pick up the current remote state immediately instead of waiting for the first timer tick
      SendBridgeHeartbeat();
   }

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(InpBridgeEnabled) EventKillTimer();

   if(hEmaTrend != INVALID_HANDLE) IndicatorRelease(hEmaTrend);
   if(hEmaFast  != INVALID_HANDLE) IndicatorRelease(hEmaFast);
   if(hEmaSlow  != INVALID_HANDLE) IndicatorRelease(hEmaSlow);
   if(hMacd     != INVALID_HANDLE) IndicatorRelease(hMacd);
   if(hRsi      != INVALID_HANDLE) IndicatorRelease(hRsi);
   if(hAdx      != INVALID_HANDLE) IndicatorRelease(hAdx);
   if(hAtr      != INVALID_HANDLE) IndicatorRelease(hAtr);
   if(hBands    != INVALID_HANDLE) IndicatorRelease(hBands);
}

//+------------------------------------------------------------------+
//| Timer: drives the remote bridge heartbeat + control poll on their |
//| own cadence, independent of tick volume (so it keeps reporting    |
//| even on a quiet symbol/session).                                  |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!InpBridgeEnabled) return;

   if(TimeCurrent() - g_lastControlPollAt >= InpBridgePollSec)
      PollBridgeControl();

   if(TimeCurrent() - g_lastHeartbeatAt >= InpBridgeHeartbeatSec)
      SendBridgeHeartbeat();

   if(g_remoteFlattenPending)
   {
      Print("XAUUSD_Confluence_EA: remote flatten-all command received from bridge.");
      CloseAllPositions();
      g_remoteFlattenPending = false;
      SendBridgeHeartbeat(); // report the result right away instead of waiting for the next interval
   }
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
   double emaFast1, emaFast2, emaSlow1, emaSlow2;
   double macdMain1, macdSignal1, macdMain2, macdSignal2;
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

   // --- Working timeframe fast/slow EMA, last two closed bars ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hEmaFast, 0, 1, 2, buf) < 2) return(false);
   d.emaFast1 = buf[0]; d.emaFast2 = buf[1];

   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hEmaSlow, 0, 1, 2, buf) < 2) return(false);
   d.emaSlow1 = buf[0]; d.emaSlow2 = buf[1];

   // --- MACD main(0)/signal(1) ---
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hMacd, 0, 1, 2, buf) < 2) return(false);
   d.macdMain1 = buf[0]; d.macdMain2 = buf[1];

   ArraySetAsSeries(buf, true);
   if(CopyBuffer(hMacd, 1, 1, 2, buf) < 2) return(false);
   d.macdSignal1 = buf[0]; d.macdSignal2 = buf[1];

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
//| Evaluate long/short confluence signals                            |
//+------------------------------------------------------------------+
void EvaluateSignals(const SignalData &d, bool &buySignal, bool &sellSignal)
{
   buySignal  = false;
   sellSignal = false;

   bool htfBullish = d.trendClose > d.trendEma;
   bool htfBearish = d.trendClose < d.trendEma;

   bool bullishCross = (d.emaFast2 <= d.emaSlow2) && (d.emaFast1 > d.emaSlow1);
   bool bearishCross = (d.emaFast2 >= d.emaSlow2) && (d.emaFast1 < d.emaSlow1);

   bool macdBullish = (d.macdMain1 > d.macdSignal1) && (d.macdMain1 > d.macdMain2);
   bool macdBearish = (d.macdMain1 < d.macdSignal1) && (d.macdMain1 < d.macdMain2);

   bool rsiOkBuy  = (d.rsi1 > InpRsiMidline) && (d.rsi1 < InpRsiUpperBlock);
   bool rsiOkSell = (d.rsi1 < InpRsiMidline) && (d.rsi1 > InpRsiLowerBlock);

   bool trendingMarket = d.adx1 >= InpAdxMinLevel;
   bool dmiBullish     = d.plusDi1  > d.minusDi1;
   bool dmiBearish     = d.minusDi1 > d.plusDi1;

   bool notAtUpperBand = d.close1 < d.bandsUpper1; // don't chase price already at/above the upper band
   bool notAtLowerBand = d.close1 > d.bandsLower1; // don't chase price already at/below the lower band

   buySignal  = htfBullish && bullishCross && macdBullish && rsiOkBuy  &&
                trendingMarket && dmiBullish && notAtUpperBand;

   sellSignal = htfBearish && bearishCross && macdBearish && rsiOkSell &&
                trendingMarket && dmiBearish && notAtLowerBand;
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
//| Remote bridge helpers                                             |
//| ------------------------------------------------------------------|
//| The EA cannot accept inbound connections (MT5 terminals only make |
//| outbound requests via WebRequest), so an iOS app can't talk to it |
//| directly. Instead the EA periodically PUSHes a status heartbeat   |
//| to a small bridge server (see /bridge in the repo) and POLLs the  |
//| same server for remote-control flags the app has set. Add the    |
//| bridge's domain to Tools > Options > Expert Advisors > "Allow     |
//| WebRequest for listed URL" or these calls fail with error 4060.   |
//+------------------------------------------------------------------+
string JsonEscape(const string s)
{
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\r", "");
   return(out);
}

string BridgeAccountTag()
{
   if(StringLen(InpBridgeAccountTag) > 0) return(InpBridgeAccountTag);
   return(IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)));
}

//+------------------------------------------------------------------+
//| Build the JSON array of this EA's open positions on this symbol   |
//+------------------------------------------------------------------+
string BuildPositionsJson()
{
   string json = "[";
   bool first = true;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      if(!first) json += ",";
      first = false;

      long type = PositionGetInteger(POSITION_TYPE);
      json += StringFormat(
         "{\"ticket\":%I64u,\"type\":\"%s\",\"volume\":%.2f,\"openPrice\":%.2f,\"sl\":%.2f,\"tp\":%.2f,\"profit\":%.2f}",
         ticket,
         type == POSITION_TYPE_BUY ? "buy" : "sell",
         PositionGetDouble(POSITION_VOLUME),
         PositionGetDouble(POSITION_PRICE_OPEN),
         PositionGetDouble(POSITION_SL),
         PositionGetDouble(POSITION_TP),
         PositionGetDouble(POSITION_PROFIT));
   }
   json += "]";
   return(json);
}

//+------------------------------------------------------------------+
//| Push a status heartbeat to the bridge: account + EA state         |
//+------------------------------------------------------------------+
void SendBridgeHeartbeat()
{
   g_lastHeartbeatAt = TimeCurrent();

   string url = InpBridgeURL + "/api/heartbeat";
   string body = StringFormat(
      "{\"accountTag\":\"%s\",\"login\":%I64u,\"broker\":\"%s\",\"currency\":\"%s\",\"symbol\":\"%s\","
      "\"magicNumber\":%I64u,\"balance\":%.2f,\"equity\":%.2f,\"margin\":%.2f,\"freeMargin\":%.2f,"
      "\"tradesToday\":%d,\"dailyLossHit\":%s,\"remoteTradingEnabled\":%s,\"positions\":%s,\"eaVersion\":\"1.1\",\"ts\":%I64d}",
      JsonEscape(BridgeAccountTag()),
      (ulong)AccountInfoInteger(ACCOUNT_LOGIN),
      JsonEscape(AccountInfoString(ACCOUNT_COMPANY)),
      JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)),
      JsonEscape(_Symbol),
      InpMagicNumber,
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      g_tradesToday,
      g_dailyLossHit ? "true" : "false",
      g_remoteTradingEnabled ? "true" : "false",
      BuildPositionsJson(),
      (long)TimeCurrent());

   char   data[], result[];
   string resultHeaders;
   StringToCharArray(body, data, 0, StringLen(body));

   string headers = "Content-Type: application/json\r\nX-Api-Key: " + InpBridgeApiKey + "\r\n";
   ResetLastError();
   int status = WebRequest("POST", url, headers, 5000, data, result, resultHeaders);

   if(status == -1)
   {
      g_bridgeReachable = false;
      g_bridgeLastError = "WebRequest error " + IntegerToString(GetLastError()) + " (is the bridge URL whitelisted?)";
      Print("XAUUSD_Confluence_EA: heartbeat failed - ", g_bridgeLastError);
   }
   else if(status >= 200 && status < 300)
   {
      g_bridgeReachable = true;
      g_bridgeLastError = "";
   }
   else
   {
      g_bridgeReachable = false;
      g_bridgeLastError = "HTTP " + IntegerToString(status);
      Print("XAUUSD_Confluence_EA: heartbeat rejected by bridge - ", g_bridgeLastError);
   }
}

//+------------------------------------------------------------------+
//| Tiny helper: read a top-level JSON boolean field without a full   |
//| JSON parser (the control response has a small, fixed shape).      |
//+------------------------------------------------------------------+
bool JsonReadBool(const string json, const string key, bool defaultValue)
{
   string needle = "\"" + key + "\"";
   int pos = StringFind(json, needle);
   if(pos < 0) return(defaultValue);
   int colon = StringFind(json, ":", pos + StringLen(needle));
   if(colon < 0) return(defaultValue);
   string rest = StringSubstr(json, colon + 1, 8);
   StringTrimLeft(rest);
   if(StringFind(rest, "true") == 0) return(true);
   if(StringFind(rest, "false") == 0) return(false);
   return(defaultValue);
}

//+------------------------------------------------------------------+
//| Poll the bridge for remote-control flags set from the iOS app:    |
//| tradingEnabled (pause/resume new entries) and flattenAll (close   |
//| everything now). Fail-safe behavior is configurable: if the       |
//| bridge is unreachable, either keep trading (default) or pause.    |
//+------------------------------------------------------------------+
void PollBridgeControl()
{
   g_lastControlPollAt = TimeCurrent();

   string url = InpBridgeURL + "/api/control?accountTag=" + BridgeAccountTag();
   char   data[], result[];
   string resultHeaders;
   string headers = "X-Api-Key: " + InpBridgeApiKey + "\r\n";

   ResetLastError();
   int status = WebRequest("GET", url, headers, 5000, data, result, resultHeaders);

   if(status == -1)
   {
      g_bridgeReachable = false;
      g_bridgeLastError = "WebRequest error " + IntegerToString(GetLastError()) + " (is the bridge URL whitelisted?)";
      g_remoteTradingEnabled = InpBridgeFailSafeOpen; // fail-safe: keep trading unless configured to pause
      return;
   }

   if(status < 200 || status >= 300)
   {
      g_bridgeReachable = false;
      g_bridgeLastError = "HTTP " + IntegerToString(status);
      g_remoteTradingEnabled = InpBridgeFailSafeOpen;
      return;
   }

   g_bridgeReachable = true;
   g_bridgeLastError = "";

   string body = CharArrayToString(result);
   bool wasEnabled = g_remoteTradingEnabled;
   g_remoteTradingEnabled = JsonReadBool(body, "tradingEnabled", true);
   bool flatten = JsonReadBool(body, "flattenAll", false);
   if(flatten) g_remoteFlattenPending = true;

   if(wasEnabled != g_remoteTradingEnabled)
      PrintFormat("XAUUSD_Confluence_EA: remote trading %s via bridge.", g_remoteTradingEnabled ? "ENABLED" : "DISABLED");
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
   if(InpBridgeEnabled && !g_remoteTradingEnabled) return; // paused remotely from the iOS app
   if(g_tradesToday >= InpMaxTradesPerDay) return;
   if(!IsWithinSession()) return;
   if(!SpreadIsAcceptable()) return;
   if(CountOpenPositions(-1) >= InpMaxOpenPositions) return;

   SignalData d;
   if(!GetSignalData(d)) return;

   bool buySignal, sellSignal;
   EvaluateSignals(d, buySignal, sellSignal);

   if(buySignal && CountOpenPositions(0) == 0)
      OpenTrade(true, d.atr0);
   else if(sellSignal && CountOpenPositions(1) == 0)
      OpenTrade(false, d.atr0);
}
//+------------------------------------------------------------------+
