# XAUUSD Confluence EA (MQL5)

An MQL5 Expert Advisor that automates XAUUSD (Gold) trading using a
multi-indicator **confluence** strategy: trend, momentum and trend-strength
each vote on a direction, and a trade is taken when at least **2 of the 3**
agree with **at least one confirmed** at a stricter threshold — combined with
ATR-based risk management and several protective filters.

⚠️ **No EA can guarantee profit.** Gold is volatile and highly news-sensitive.
Backtest across several years and market regimes, forward-test on a demo
account, and never risk money you can't afford to lose. Past performance does
not predict future results.

## Files

- `MQL5/Experts/XAUUSD_Confluence_EA.mq5` — the EA. Copy into your terminal's
  `MQL5/Experts/` folder (File → Open Data Folder in MetaTrader 5), then
  compile in MetaEditor and attach to an **XAUUSD** chart.
- `MQL5/Presets/XAUUSD_Confluence_EA_Default.set` — a ready-made input preset
  matching the defaults below. Load it from the EA's Inputs tab (`Load`
  button) instead of retyping every parameter.
- `XAUUSD_Confluence_EA_QuickStart.pdf` — a printable one-page install/run
  cheat sheet.
- `python/` — a Python port of the same strategy that trades through the
  MetaTrader5 Python API (0.02 lots, 60-pip stop, 30-pip trailing stop, the
  same 2-of-3 entry rule, and it pauses itself while the market is closed).
  See `python/README.md`.

## Strategy logic

| Role | Indicator | Purpose |
|---|---|---|
| Macro trend bias | EMA(200) on a higher timeframe (default H4) | Sets the dominant-trend vote (one of the three confluences) |
| Entry trigger | EMA(20)/EMA(50) crossover on the working timeframe (default M5) | Times the entry to a fresh directional shift |
| Momentum confirmation | MACD(12,26,9) | Confirms momentum agrees with the crossover direction |
| Overbought/oversold filter | RSI(14) | Blocks buys above 70 / sells below 30; requires RSI on the correct side of 50 |
| Trend-strength filter | ADX/DMI(14) | Requires ADX ≥ 22 and +DI/-DI aligned, filtering out choppy/range-bound conditions that whipsaw crossover systems |
| Volatility / extension filter | Bollinger Bands(20, 2) | Avoids buying into an already-extended move at the upper band (or selling into the lower band) |
| Stops, targets & sizing | ATR(14) | Stop-loss = ATR × multiplier; take-profit = SL distance × risk:reward ratio; lot size computed from % equity risked |

These roll up into **three confluences** — trend (EMAs), momentum (MACD+RSI)
and strength (ADX/DMI) — each voting at a *pass* and a stricter *confirmed*
level. A trade opens when **at least 2 of the 3 pass and at least 1 is
confirmed** (`InpMinConfluences` / `InpMinConfirmed`), evaluated on the most
recently **closed** bar (no repainting), once per new bar. Bollinger Bands act
as a veto rather than a vote.

Entries are evaluated on each closed **M5** bar and must also score at least
**50/100** (`InpMinConfidence`), with up to **2 positions open at a time**
(same direction only) and **no daily trade cap** (`InpMaxTradesPerDay = 0`).

At 2 of 3 the trend leg is optional, so entries against the H4 trend become
possible — set `InpRequireTrendConfluence = true` to block them. Between the
2-of-3 rule and M5 entries this evaluates far more setups than the original
M15 version (~17/day before filtering), which the 50-point score gate trims to
roughly **9 trades/day** in testing.

**Running on a Mac?** MT5 for macOS runs this EA natively — that is the
simplest path, since the Python bot's `MetaTrader5` dependency is Windows-only.
See `python/README.md` → "Running on a Mac".

## Confidence score

Each evaluation produces a **0–100 setup score** per direction, shown live in
the chart comment (`InpShowConfidence`) and printed on entry. Each confluence
is worth a third: passing earns 60% of it, and the rest scales with how far
past its confirmation threshold the indicator sits. In testing the floor is
40, the median 50 and the practical ceiling the mid-80s.

**`InpMinConfidence = 50` — the EA only trades setups scoring 50 or higher.**
The qualifying floor is 40, so this drops the weakest band of setups while
leaving the 2-of-3 rule meaningful: it kept 52% of signals in testing (~9.2
trades/day on M5, against 17.7 ungated), and 84% of those entries were 2-of-3
setups. Raise it to 55 (19% kept) or 65 (9% kept, and mostly 3-of-3, since an
unconfirmed leg is only worth 20 points) to trade less and more selectively,
or 0 to disable the gate.

**This score is not a win probability.** It measures how strongly the
indicators agree at entry, not the odds of profit. No win rate for this EA is
known — see "Before going live" for how to measure one.

## Risk & trade management

- **Position sizing** — risk a fixed percentage of account equity per trade
  (`InpRiskPercent`), derived from the ATR-based stop distance and the
  symbol's tick value, capped by `InpMaxLotSize`.
- **Breakeven** — stop is moved to entry + buffer once price has moved
  `InpBreakevenAtrMult × ATR` in profit.
- **ATR trailing stop** — once profit passes `InpTrailStartAtrMult × ATR`,
  the stop trails at `InpTrailAtrMult × ATR` behind price (only ever
  tightens).
- **Daily loss circuit breaker** — new entries stop for the rest of the
  calendar day once the account has drawn down `InpMaxDailyLossPct` % of the
  equity recorded at the start of that day.
- **Max trades/day** and **max concurrent positions** caps to prevent
  over-trading.
- **Spread filter** — skips new entries when the current spread exceeds
  `InpMaxSpreadPoints`.
- **Session filter** — restricts new entries to a configurable server-time
  window (defaults to the London/US liquidity overlap) to avoid thin,
  choppy Asian-session price action.
- **Weekend flatten** — optionally closes all open positions ahead of the
  Friday close to avoid weekend gap risk.

## Key inputs

All parameters are exposed as EA inputs (grouped in MetaTrader's Inputs tab)
so they can be optimized in the Strategy Tester:

- `InpWorkTF` / `InpTrendTF` — entry and trend timeframes.
- `InpTrendEmaPeriod`, `InpEmaFastPeriod`, `InpEmaSlowPeriod` — trend/entry EMAs.
- `InpMinConfluences`, `InpRequireConfirm`, `InpMinConfirmed`, `InpRequireTrendConfluence` — the entry rule.
- `InpConfirmEmaGapAtr`, `InpConfirmRsiMargin`, `InpConfirmAdxLevel`, `InpConfirmDiGap` — confirmation thresholds.
- `InpMacdFast/Slow/Signal`, `InpRsiPeriod`, `InpRsiUpperBlock/LowerBlock` — momentum filters.
- `InpAdxPeriod`, `InpAdxMinLevel` — trend-strength filter.
- `InpAtrPeriod`, `InpBandsPeriod`, `InpBandsDeviation` — volatility filters.
- `InpRiskPercent`, `InpAtrSlMultiplier`, `InpRiskRewardRatio`, `InpMaxLotSize` — sizing/stops.
- `InpMaxDailyLossPct`, `InpMaxTradesPerDay`, `InpMaxOpenPositions` — trade-frequency guards.
- `InpUseSessionFilter`, `InpSessionStartHour/Min`, `InpSessionEndHour/Min`, `InpCloseBeforeWeekend` — timing filters.
- `InpMaxSpreadPoints` — spread guard.

## Before going live

1. **Backtest** in the MT5 Strategy Tester with tick data (every tick / real
   ticks) across multiple years, including trending and ranging periods. This
   is the only way to obtain a real win rate, profit factor and drawdown for
   this EA — none is claimed or known in advance, and the setup score is a
   measure of indicator agreement, not of expected profit.
2. **Optimize cautiously** — avoid over-fitting a handful of parameters to
   one historical window; validate on out-of-sample data.
3. **Forward-test on a demo account** for at least several weeks.
4. **Start small** on a live account (minimum lot / low risk %) and monitor
   behavior around high-impact news (NFP, FOMC, CPI) — this EA does not
   include an economic-calendar news filter; consider disabling trading
   manually around major releases or extending the session filter.
5. Confirm your broker's XAUUSD contract specification (tick value, tick
   size, stops level, filling mode) matches the assumptions used for lot
   sizing and order placement.
