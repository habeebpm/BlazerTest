# XAUUSD Confluence EA (MQL5)

An MQL5 Expert Advisor that automates XAUUSD (Gold) trading using a
multi-indicator **confluence** strategy: trades are only taken when trend,
momentum, trend-strength and volatility indicators all line up, combined with
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
- `bridge/` — a small server that lets the iOS app below monitor and remotely
  control the EA.
- `ios/` — the iOS app (SwiftUI + XcodeGen project).

## iOS app: monitoring and remote control

There is no way for a third-party iOS app to run an MQL5 EA directly.
MetaTrader's algo-trading engine only exists inside the MetaTrader
**terminal** — desktop, or a broker's cloud-hosted terminal — and MT5's own
official iOS app is a manual-trading client with no support for running
custom Expert Advisors. Apple's platform has no path to execute arbitrary
third-party MQL5 code either. So "an iOS app that runs the EA automatically"
is built as:

```
XAUUSD_Confluence_EA  --heartbeat-->   bridge server  <--status/control--  iOS app
(MT5 terminal, always                  (bridge/,           (ios/,
 on — a VPS or a                        self-hosted)        SwiftUI)
 desktop left running)  <--poll control--
```

- The **EA** (`MQL5/Experts/XAUUSD_Confluence_EA.mq5`) keeps doing exactly
  what it always did — evaluating signals and managing trades autonomously,
  every tick, with no phone in the loop. It now also *optionally* (off by
  default, `InpBridgeEnabled`) pushes a status heartbeat and polls for two
  remote-control flags via MQL5's outbound-only `WebRequest`.
- The **bridge** (`bridge/`) is a small server you deploy (VPS, Fly.io,
  Render, etc.) that relays between the EA (which can only make outbound
  calls) and the app (which can't reach a terminal directly).
- The **iOS app** (`ios/`) polls the bridge to show live account
  equity/balance, open positions, and daily-loss/trade-count state, and can
  remotely pause/resume new entries or flatten all positions. It never
  computes signals or sends orders itself — that stays in the EA, so live
  behavior always matches what you backtested.

See `bridge/README.md` and `ios/README.md` for setup. The EA works exactly
as before if you never touch the new `=== Remote Bridge ===` inputs.

## Strategy logic

| Role | Indicator | Purpose |
|---|---|---|
| Macro trend bias | EMA(200) on a higher timeframe (default H4) | Only trade in the direction of the dominant trend |
| Entry trigger | EMA(20)/EMA(50) crossover on the working timeframe (default M15) | Times the entry to a fresh directional shift |
| Momentum confirmation | MACD(12,26,9) | Confirms momentum agrees with the crossover direction |
| Overbought/oversold filter | RSI(14) | Blocks buys above 70 / sells below 30; requires RSI on the correct side of 50 |
| Trend-strength filter | ADX/DMI(14) | Requires ADX ≥ 22 and +DI/-DI aligned, filtering out choppy/range-bound conditions that whipsaw crossover systems |
| Volatility / extension filter | Bollinger Bands(20, 2) | Avoids buying into an already-extended move at the upper band (or selling into the lower band) |
| Stops, targets & sizing | ATR(14) | Stop-loss = ATR × multiplier; take-profit = SL distance × risk:reward ratio; lot size computed from % equity risked |

A trade is only opened once **all** of the above align on the most recently
**closed** bar (no repainting), and only once per new bar on the working
timeframe.

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
- `InpMacdFast/Slow/Signal`, `InpRsiPeriod`, `InpRsiUpperBlock/LowerBlock` — momentum filters.
- `InpAdxPeriod`, `InpAdxMinLevel` — trend-strength filter.
- `InpAtrPeriod`, `InpBandsPeriod`, `InpBandsDeviation` — volatility filters.
- `InpRiskPercent`, `InpAtrSlMultiplier`, `InpRiskRewardRatio`, `InpMaxLotSize` — sizing/stops.
- `InpMaxDailyLossPct`, `InpMaxTradesPerDay`, `InpMaxOpenPositions` — trade-frequency guards.
- `InpUseSessionFilter`, `InpSessionStartHour/Min`, `InpSessionEndHour/Min`, `InpCloseBeforeWeekend` — timing filters.
- `InpMaxSpreadPoints` — spread guard.

## Before going live

1. **Backtest** in the MT5 Strategy Tester with tick data (every tick / real
   ticks) across multiple years, including trending and ranging periods.
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
