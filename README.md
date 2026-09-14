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
- `MQL5/Experts/XAUUSD_MTF_RSI_MACD_BB_EA.mq5` — a second, independent EA:
  a multi-timeframe RSI/MACD/Bollinger Bands/swing-structure confluence
  strategy. See "XAUUSD Multi-Timeframe RSI/MACD/BB EA" below.
- `MQL5/Presets/XAUUSD_MTF_RSI_MACD_BB_EA_Default.set` — its matching input
  preset.

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
**50/100** (`InpMinConfidence`), within **the broker's own trading hours** for
the symbol, with up to **4 positions open at a time** (same direction only) and
**no daily trade cap** (`InpMaxTradesPerDay = 0`). Every position is closed
shortly **before the session closes**, so nothing is held through a market
close or over the weekend.

**Every confluence and every confirmation is computed on M5.** The only
higher-timeframe input is the EMA(200) macro bias on `InpTrendTF` (H4), which
feeds the trend confluence's *pass* test, not its confirmation.

At 2 of 3 the trend leg is optional, so entries against the H4 trend become
possible — set `InpRequireTrendConfluence = true` to block them. Between the
2-of-3 rule, M5 entries and the 50-point gate this produces ~9.3 qualifying
signals/day, of which roughly **4.6 actually fill** — a signal only trades when
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
- Keep it on the power adapter. A 17-hour session window (06:00–23:00 Oman)
  will not survive on battery.
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

**`InpMinConfidence = 50` — only setups scoring 50 or better are traded.**
The qualifying floor is 40, so this drops the weakest band. Measured fills/day
(24h; the session window trims these further — see below):

| Gate | 2 pos | 4 pos | 6 pos |
|---|---|---|---|
| **50 (shipped)** | 4.7 | **6.9** | 8.1 |
| 45 | 5.6 | 8.5 | 10.1 |
| off | 6.4 | 10.0 | 12.0 |

With the 06:00–23:00 Oman window applied, the shipped setting yields **4.6
fills/day** (gate 45 would give 5.7, the gate off 6.6).

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
- **Daily frame — downside capped, upside open.** `InpMaxDailyLossPct` (1.0%)
  halts a losing day. The upside is **not** capped: `InpUseDailyTarget` ships
  **off**, so a good day keeps running.
- **Profit lock** — what protects a day that has run up, without capping it.
  Once the day peaks above `InpLockAfterPct` (0.5%), trading stops if
  `InpGiveBackPct` (50%) of that peak gain is handed back. A day reaching
  +2.0% floors at +1.0% instead of round-tripping to the loss cap; a day
  reaching +5% floors at +2.5%. Set `InpUseDailyTarget = true` for a hard stop
  at +0.5% instead, trading magnitude for consistency.
- **Max trades/day** and **max concurrent positions** caps to prevent
  over-trading.
- **Spread filter** — skips new entries when the current spread exceeds
  `InpMaxSpreadPoints`.
- **Session filter — the broker's own trading hours.** With
  `InpUseBrokerSession` (default on) the EA reads the real session schedule for
  your symbol via `SymbolInfoSessionTrade`, so it tracks the broker's GMT
  offset, its DST changes, gold's daily break and the early close on Friday
  without any of it being configured. Entries are held off for
  `InpEntryOpenBufferMin` after the open (avoiding the wide opening spread) and
  stop `InpEntryCloseBufferMin` before the close. The schedule is logged at
  startup and cached per day.
  Set `InpUseBrokerSession = false` to fall back to a manual window, which is
  interpreted in `InpSessionGmtOffset` hours from GMT (default 06:00–23:00
  Oman) rather than broker time.
- **Flatten before the close** — `InpFlattenBeforeClose` closes every position
  `InpFlattenBeforeCloseMin` minutes before the session ends. Because the
  broker reports Friday's earlier end itself, one rule covers the daily close,
  the daily break and the weekend — nothing is carried through a close or over
  a weekend gap.
- **Weekend flatten** — retained for the manual-window fallback; the broker
  session path handles it through the flatten-before-close rule above.

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
- `InpUseDailyTarget`, `InpDailyTargetPct`, `InpCloseOnTarget`, `InpMaxDailyLossPct` — the daily frame.
- `InpMaxTradesPerDay`, `InpMaxOpenPositions` — trade-frequency guards.
- `InpUseBrokerSession`, `InpEntryOpenBufferMin`, `InpEntryCloseBufferMin`, `InpFlattenBeforeClose`, `InpFlattenBeforeCloseMin` — broker-session timing.
- `InpUseSessionFilter`, `InpSessionGmtOffset`, `InpSessionStartHour/Min`, `InpSessionEndHour/Min`, `InpCloseBeforeWeekend` — manual-window fallback.
- `InpMaxSpreadPoints` — spread guard.

## Scaling the lot size

**Let the size follow the account rather than adjusting it by hand.** Set
`InpUseFixedLot = false` and the EA sizes every trade from equity and the stop
distance via `InpRiskPercent` (0.2%) — the lot rises as the balance does and
falls back after a drawdown, so risk stays a constant fraction of the account.

**0.2% is not arbitrary.** It is the 1.0% daily loss cap divided by five, so
five losing trades are absorbed before the day halts. That matters more than it
sounds: at ~5 trades a day, a size whose cap only absorbs one or two losses
turns the breaker into the strategy — it halts on ordinary variance instead of
on a bad day.

| Account | 0.01 lots risks | = % of account | Losses before the cap halts |
|---|---|---|---|
| $1,000 | $6.00 | 0.60% | **1.7** — halts most days |
| $2,000 | $6.00 | 0.30% | 3.3 |
| **$3,000** | $6.00 | **0.20%** | **5.0** — healthy |
| $10,000 | $18.00 (0.03 lots) | 0.18% | 5.6 |

Below about $3,000 the broker's 0.01 minimum forces more than 0.2% — you
cannot size down further, so small accounts necessarily run hotter.

The EA audits this at startup and warns you when the cap absorbs fewer than
three losses:

```
risk audit - 0.01 lots risks 6.00 (0.60% of 1000.00 equity);
the 1.0% daily cap absorbs 1.7 losing trades.
WARNING - the daily cap stops trading after only 1.7 losses. At ~5 trades a
day that will halt on ordinary variance.
```

## Sizing for a daily percentage

At the broker minimum of 0.01 lots you cannot size down, only up the account,
so **account size sets the daily percentage**. Per-day return at 4.8 fills, a
$6 stop and 1.8R targets, *if* the strategy proves to have an edge:

| Win rate | $/day | $1,000 | $2,000 | $3,000 | $5,000 |
|---|---|---|---|---|---|
| 45% | +$6.29 | 0.63% | 0.31% | 0.21% | 0.13% |
| 50% | +$10.32 | 1.03% | 0.52% | 0.34% | 0.21% |
| 55% | +$14.35 | 1.44% | 0.72% | 0.48% | 0.29% |
| 60% | +$18.38 | 1.84% | 0.92% | 0.61% | 0.37% |

A 0.5%/day target is the natural output of 0.01 lots on roughly a **$2,900**
account at a 55% win rate. On a smaller account the same lot size aims higher
and carries proportionally more risk — which is what the daily target is for:
below about $2,000, a single winner at 1.8R ($10.80) already exceeds 0.5% of
the account, so the EA becomes a "one good trade and stop" system.

**None of these rows is a prediction.** The win rate is the unknown; the table
only says what each one would imply. Measure it in the Strategy Tester first.

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

## XAUUSD Multi-Timeframe RSI/MACD/BB EA

A second, independent Expert Advisor in this repo:
`MQL5/Experts/XAUUSD_MTF_RSI_MACD_BB_EA.mq5`. It shares no signal logic with
the Confluence EA above — same risk-management conventions (fixed lot,
fixed-pip stop/trail, broker-session filter, daily-loss breaker), completely
different entry decision.

### Strategy logic

Three timeframes (`InpTF1 < InpTF2 < InpTF3`, default **M15 / H1 / H4**) are
each scored independently on the same four criteria, then combined into one
weighted signal.

**BUY, per timeframe (count how many of the 4 hold):**
1. RSI above `InpRsiBuyLevel` (45–50) **and** rising vs. `InpRsiLookback`
   bars back.
2. MACD histogram positive (this also covers "just crossed up" — a fresh
   cross makes the histogram positive on the bar it happens).
3. Close above the Bollinger middle band.
4. Close still above the most recent swing low — structure intact, no
   breakdown.

**SELL, per timeframe (count how many of the 4 hold):**
1. RSI below `InpRsiSellLevel` (45–50) **and** falling.
2. MACD histogram negative **and** expanding (more negative than the prior
   closed bar).
3. Close below the Bollinger middle band.
4. **Confirmed** close below the most recent swing low — a wick through it
   does not count, the bar must close through it.

Each timeframe keeps a `buyCount` and a `sellCount` (0–4 each) and takes
whichever side has more, giving a net score from **-4** (outright sell) to
**+4** (outright buy). Swing structure is a simple fractal/pivot scan: over
the last `InpSwingScanBars` closed bars, a bar counts as a pivot low when its
low sits below the low of `InpSwingPivotWidth` bars on both sides of it; the
most recent one found is "the most recent swing low" used by criterion 4 on
**both** the buy and the sell side, exactly as specified — the sell side
deliberately reuses the swing low (not a swing high) as its breakdown
reference.

### Combining the three timeframes

```
combinedScore = 100 x (net1*w1 + net2*w2 + net3*w3) / (4 x (w1+w2+w3))
```

a **-100..+100** scale, where ±100 means all three timeframes hit a clean
4/4 in the same direction. Higher timeframes carry more weight by default
(`InpWeightTF3 = 2.0 > InpWeightTF2 = 1.5 > InpWeightTF1 = 1.0`), so H4
matters more than M15.

Swing-structure confirmation (criterion 4) on **any** timeframe, in the
direction the score already leans, is the strongest single piece of
evidence: it adds `InpSwingBonusPoints` (default 15) to the combined score —
the mechanism that upgrades a "leaning" score to a "confirmed" one.

"Agreeing timeframes" counts how many of the three sit at conviction
(`|net| >= 3`, i.e. 3 or 4 of 4 criteria) on the same side as the overall
score:

| Agreeing TFs | Meaning | Label |
|---|---|---|
| 3 | full alignment | **STRONG** Buy/Sell |
| 2 | partial alignment | **MODERATE** Buy/Sell ("building"/"weakening") |
| 0–1 | no alignment | **NEUTRAL** |

A trade fires only when **both** gates pass: `|combinedScore| >=
InpEntryThreshold` (default **55**) **and** `agreeingTF >= InpMinAgreeingTF`
(default 2 of 3). `InpShowDashboard` prints the full per-timeframe RSI/MACD
histogram/Bollinger/swing-low breakdown plus the combined score live on the
chart, and every entry logs it to the Experts tab.

**Why 55, not a round 60:** with the default weights (1.0/1.5/2.0), a clean
4/4 agreement on only the two *lowest*-weighted timeframes (TF1+TF2, e.g.
M15+H1, with TF3 neutral) scores **55.6/100** — the weakest case that should
still satisfy `InpMinAgreeingTF = 2`. A threshold above ~55.6 silently shuts
that pairing out even at full conviction, contradicting "2 of 3 agree ⇒
moderate signal." If you change the timeframe weights, recheck that
`100 × 4×(sum of the two lowest weights) / (4×total weight)` still clears
whatever threshold you set — otherwise the two entry gates can end up
inconsistent with each other.

### Risk management (same dollar figures as requested)

- **0.01 fixed lots** per position (`InpFixedLot`), up to **4 positions**
  open at once (`InpMaxOpenPositions`) — four 0.01-lot positions, not one
  4.00-lot position.
- **$6.00 stop-loss** per position — `InpStopLossPips = 60` pips. One XAUUSD
  lot is 100oz, so at 0.01 lots $1 of price is $1 of P/L: 60 pips (0.10
  price per pip on a 2-digit gold feed) is a $6.00 stop.
- **$3.00 trailing stop** — once a position is `InpTrailStartPips` (30 pips
  = $3.00) in profit, the stop trails `InpTrailPips` (30 pips = $3.00)
  behind price and only ever tightens. An optional breakeven step
  (`InpUseBreakeven`) moves the stop to entry + buffer first, at a smaller
  `InpBreakevenTriggerPips` (20 pips ≈ $2.00).
- No fixed take-profit by default (`InpUseTakeProfit = false`) — the
  position is meant to be managed by the trailing stop, letting profit run;
  turn it on for a fixed `InpRiskRewardRatio`-based target instead.
- Four positions open at once therefore risk up to **$24.00** combined
  before any trailing has locked in profit — the same figure as the
  Confluence EA above, for the same reason.
- Every other protection (broker-session filter, spread filter, daily-loss
  circuit breaker, profit lock, flatten-before-close, manual session
  fallback) is identical in behavior to the Confluence EA's — see "Risk &
  trade management" above for the full explanation of each.

### Key inputs

- `InpTF1` / `InpTF2` / `InpTF3`, `InpWeightTF1/2/3` — the three timeframes
  and their weight in the combined score. **`InpTF1` must be the shortest**
  — it drives entry timing (evaluated once per closed `InpTF1` bar).
- `InpRsiPeriod`, `InpRsiLookback`, `InpRsiBuyLevel`, `InpRsiSellLevel` —
  criterion 1.
- `InpMacdFast/Slow/Signal` — criterion 2.
- `InpBandsPeriod`, `InpBandsDeviation` — criterion 3.
- `InpSwingScanBars`, `InpSwingPivotWidth` — criterion 4 / swing detection.
- `InpEntryThreshold`, `InpMinAgreeingTF`, `InpSwingBonusPoints` — how the
  three timeframes combine into a trade decision.
- `InpUseFixedLot`, `InpFixedLot`, `InpRiskPercent`, `InpMaxLotSize` —
  sizing (fixed 0.01 lots by default; switch to risk-% sizing the same way
  as the Confluence EA).
- `InpStopLossPips`, `InpUseTakeProfit`, `InpRiskRewardRatio` — stop/target.
- `InpUseBreakeven`, `InpBreakevenTriggerPips`, `InpBreakevenBufferPts`,
  `InpTrailStartPips`, `InpTrailPips` — trade management.
- `InpMaxOpenPositions`, `InpAllowOpposite`, `InpMaxTradesPerDay` —
  trade-frequency guards.
- `InpMaxDailyLossPct`, `InpUseDailyTarget`, `InpDailyTargetPct`,
  `InpCloseOnTarget`, `InpLockDailyGains`, `InpLockAfterPct`,
  `InpGiveBackPct` — the daily frame (identical semantics to the Confluence
  EA's).
- `InpUseBrokerSession`, `InpEntryOpenBufferMin`, `InpEntryCloseBufferMin`,
  `InpFlattenBeforeClose`, `InpFlattenBeforeCloseMin`,
  `InpUseSessionFilter`, `InpSessionGmtOffset`, `InpSessionStartHour/Min`,
  `InpSessionEndHour/Min`, `InpCloseBeforeWeekend`, `InpWeekendCloseHour` —
  session timing.
- `InpMaxSpreadPoints` — spread guard.

As with the Confluence EA: no win rate, profit factor or drawdown is known
or claimed for this strategy in advance. Backtest with tick data across
multiple regimes, forward-test on demo, and only then consider live capital.
