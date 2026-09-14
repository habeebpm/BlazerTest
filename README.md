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
  MetaTrader5 Python API (0.01 lots, 60-pip stop, 30-pip trailing stop, the
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
| Stops & targets | ATR(14) | Stop-loss = ATR × multiplier; take-profit = SL distance × risk:reward ratio (lot size is fixed at 0.01 by default, not ATR-derived) |

These roll up into **three confluences** — trend (EMAs), momentum (MACD+RSI)
and strength (ADX/DMI) — each voting at a *pass* and a stricter *confirmed*
level. A trade opens when **at least 2 of the 3 pass and at least 1 is
confirmed** (`InpMinConfluences` / `InpMinConfirmed`), evaluated on the most
recently **closed** bar (no repainting), once per new bar. Bollinger Bands act
as a veto rather than a vote.

Entries are evaluated on each closed **M5** bar and must score at least
**45/100** (`InpMinConfidence`), with up to **4 positions open at a time**
(same direction only) and **no daily trade cap** (`InpMaxTradesPerDay = 0`) —
about **8.5 fills per day**.

**Every confluence and every confirmation is computed on M5.** The only
higher-timeframe input is the EMA(200) macro bias on `InpTrendTF` (H4), which
feeds the trend confluence's *pass* test, not its confirmation.

At 2 of 3 the trend leg is optional, so entries against the H4 trend become
possible — set `InpRequireTrendConfluence = true` to block them. Between the
2-of-3 rule, M5 entries and the 45-point gate this produces ~13.0 qualifying
signals/day, of which roughly **8.5 actually fill** — a signal only trades when
a position slot is free. `python/simulate.py` reports the difference.

**Running on a Mac?** MT5 for macOS runs this EA natively — that is the
simplest path, since the Python bot's `MetaTrader5` dependency is Windows-only.
See `python/README.md` → "Running on a Mac".

## Running on a laptop (MacBook Air included)

**CPU is not the constraint.** Per price tick the EA makes roughly 18 cached
terminal calls (account equity, one ATR value, a tick quote, a loop over at
most four positions). The indicator math — eight handles across M5 and H4 —
runs once per closed M5 bar, 288 times a day, and MetaTrader caches those
buffers itself. MT5's own charting costs more than this EA does. A fanless
MacBook Air runs it without noticing.

**Sleep is the constraint.** This matters far more than hardware:

| Protection | Where it lives | Survives sleep/quit/disconnect? |
|---|---|---|
| Stop-loss and take-profit | **Broker's server** | ✅ Yes |
| Trailing stop, breakeven | EA, client-side | ❌ No |
| Daily-loss breaker | EA, client-side | ❌ No |
| Weekend flatten | EA, client-side | ❌ No |
| New entries | EA, client-side | ❌ No |

So if the lid closes with a position open, your $6.00 stop still protects you —
the broker holds it — but the stop stops *trailing*, and locked-in profit stops
being locked in. Nothing runs again until MT5 is back.

To keep it running while the display is off:

- System Settings → **Lock Screen** → "Turn display off on power adapter" is
  fine, but System Settings → **Battery** → Options → "Prevent automatic
  sleeping on power adapter when the display is off" must be **on**.
- Or from Terminal: `caffeinate -dimsu` — keeps the machine awake until you
  Ctrl-C it.
- Keep it on the power adapter. A 13-hour session window (07:00–20:00 server
  time) will not survive on battery.
- For genuinely unattended 24/5 operation, a Windows VPS is the standard
  answer — it also removes the sleep, Wi-Fi and reboot problems entirely.

**Backtesting is where you will feel the machine.** Live trading is trivial;
the Strategy Tester is not. "Every tick based on real ticks" over multiple
years of M5 gold is genuinely heavy, MT5 for macOS runs under Wine, and a
fanless Air will thermal-throttle on a long run. Practical approach:

1. First pass with the **"1 minute OHLC"** model over 1–2 years — minutes, not
   hours, and enough to see whether the equity curve is a disaster.
2. Only then re-run the promising settings with **real ticks** over a shorter
   window to get honest spread and fill behaviour.
3. Avoid full **optimization** runs on the laptop — they multiply the work by
   the number of parameter combinations and will take many hours.
4. Real tick data for several years of XAUUSD is **several GB**; check you have
   the disk space before starting a long download.

**Other practical notes:** attach the EA to **one** chart only (a second
instance trades against the first); close chart windows you are not using, as
MT5's rendering costs more than the strategy; 8 GB of RAM is ample for live
trading but the tester with tick data is the one thing that will push it.

## Confidence score

Each evaluation produces a **0–100 setup score** per direction, shown live in
the chart comment (`InpShowConfidence`) and printed on entry. Each confluence
is worth a third: passing earns 60% of it, and the rest scales with how far
past its confirmation threshold the indicator sits. In testing the floor is
40, the median 50 and the practical ceiling the mid-80s.

**`InpMinConfidence = 45` — only setups scoring 45 or better are traded.**
The qualifying floor is 40, so this trims only the weakest band and leaves the
2-of-3 rule doing most of the work. Measured fills/day:

| Gate | 2 pos | 4 pos | 6 pos |
|---|---|---|---|
| 50 | 4.7 | 6.9 | 8.1 |
| **45 (shipped)** | 5.6 | **8.5** | 10.1 |
| off | 6.4 | 10.0 | 12.0 |

Raise to 50 for fewer, more selective entries; 45 with 6 positions reaches
~10/day if frequency matters more.

**This score is not a win probability.** It measures how strongly the
indicators agree at entry, not the odds of profit. No win rate for this EA is
known — see "Before going live" for how to measure one.

## Risk & trade management

- **Position sizing** — **fixed 0.01 lots** by default (`InpUseFixedLot` /
  `InpFixedLot`). Set `InpUseFixedLot = false` to size from a percentage of
  equity instead (`InpRiskPercent`), derived from the ATR-based stop distance
  and the symbol's tick value, capped by `InpMaxLotSize`.
  One XAUUSD lot is 100oz, so $1 of price is $1 per 0.01 lot: the $6.00 stop
  risks about **$6 per trade**, or **$24** with four positions open.
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
- `InpUseFixedLot`, `InpFixedLot`, `InpRiskPercent`, `InpAtrSlMultiplier`, `InpRiskRewardRatio`, `InpMaxLotSize` — sizing/stops.
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
