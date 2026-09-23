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
- `MQL5/Experts/TelegramSMC_Copier.mq5` — a separate EA that copies XAUUSD
  BUY/SELL zone calls posted in a Telegram channel into MT5, independent of
  the confluence strategy above. See "Telegram SMC Copier (MQL5)" below.
- `MQL5/Experts/TelegramSMC_TradeLogger.mq5` — a standalone trade-journal EA
  that logs every open/close for a given magic number to a CSV, decoupled
  from whatever EA is actually trading. See "Logging" under the same section.
- `ASPX/Dashboard.aspx` — a self-contained ASP.NET Web Forms page that reads
  those two CSVs and renders a read-only dashboard. See "Dashboard
  (ASP.NET)" below and `ASPX/README.md`.
- `python/` — a Python port of the same strategy that trades through the
  MetaTrader5 Python API (0.01 lots, 60-pip stop, 30-pip trailing stop, the
  same 2-of-3 entry rule, and it pauses itself while the market is closed).
  See `python/README.md`. It also includes `telegram_copier.py`, a separate
  tool that copies trade calls posted in Telegram chats into MT5 - each
  signal is parsed and run through a `SignalVerifier` (stale/duplicate/
  off-symbol/bad-stop/weak-reward signals are rejected with a logged reason)
  before anything is sent, and the fill is checked against what was verified
  afterwards. See "Telegram signal copier + verifier" in `python/README.md`.

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

## Telegram SMC Copier (MQL5)

`MQL5/Experts/TelegramSMC_Copier.mq5` is a separate, self-contained EA for a
different job: instead of generating its own signals, it copies XAUUSD
BUY/SELL zone calls posted in a Telegram channel — the kind that read like

```
XAUUSD BUY 4342-4339
TP1: 4349
TP2: 4356
TP3: 4364
StopLoss: 4335
```

into MT5, with an independent price-action check before anything is sent.
It does not share any code or state with the confluence EA above and can run
on the same or a different chart.

### How it reaches Telegram

MQL5 cannot read a Telegram channel directly, so the EA polls the [Telegram
Bot API](https://core.telegram.org/bots/api) with `WebRequest` — no Python
process and nothing outside MT5 in the normal case. One-time setup:

1. Talk to **@BotFather** in Telegram, `/newbot`, and copy the token it gives
   you into `InpBotToken`.
2. Add that bot to the signal channel/group **as an admin** — a bot only
   receives channel posts if it is one. **If you don't own/moderate the
   channel and can't get a bot added there**, see "Telegram relay bridge"
   in `python/README.md` — it reads the channel as your own account (just a
   member, no admin needed) and forwards each message into a private group
   you *do* own, which the bot can be admin of; everything from step 4
   onward is unchanged, just pointed at that relay group's id instead.
3. In MT5: **Tools → Options → Expert Advisors** → tick "Allow WebRequest for
   listed URL" and add `https://api.telegram.org` — WebRequest is refused
   otherwise, and the EA logs exactly this instruction if it happens.
4. Leave `InpChannelId1`/`InpChannelId2` at `0` for the first run and attach
   the EA: every message the bot can see is logged with its chat id. Copy up
   to two of those ids into `InpChannelId1` (and `InpChannelId2` for a second
   channel) and restart, so only those chats can trigger trades. Leaving both
   at `0` accepts signals from any chat the bot can see — fine for that first
   dry-run discovery run; the EA refuses to start live (`InpDryRun=false`)
   in that state. The Python copier likewise refuses `--live` without a
   `--channels`/`TELEGRAM_CHANNELS` allow-list.

The EA ships with **`InpDryRun = true`**. Nothing above logs-only behavior
happens until you set it to `false`, and that should only follow watching the
log agree with the channel for a while.

### Entry, exit and what the signal's own numbers are used for

- **Entry** is `InpFixedLot` (default 0.05) lots at the **upper bound** of the
  zone for a BUY, the **lower bound** for a SELL — the edge price reaches
  first. Whether that becomes a pending order or a market order is decided
  from the live price, not the message's wording: price still on the far
  side of the zone gets a BUY/SELL LIMIT at that bound; price already inside
  the zone gets a market order now; price already through the *whole* zone
  is skipped as stale. `InpPendingExpiryMin` cancels an unfilled limit order
  after a while, since a Smart-Money zone goes stale faster than a
  fixed-distance order would.
- **Stop-loss** is used exactly as given in the signal (after the sanity
  checks below).
- **Take-profit ignores the signal's TP1/TP2/TP3.** `InpTp1Points` (default
  `4.0`) is a real, broker-side take-profit set the moment the trade opens —
  it survives a disconnect. Once floating profit reaches that many **price
  units** (4.0 = $4.00 on XAUUSD — not a broker "point" of $0.01 and not a
  "pip" of $0.10), the fixed TP is dropped and an `InpTrailPoints` (default
  `3.0`) trailing stop takes over for the rest of the move, tightening only.
  The signal's own TP1/TP2/TP3 are logged for reference and otherwise
  ignored; only one take-profit level exists per MT5 position, so this EA
  does not split volume across partial targets.
- Sanity checks reject a signal before it reaches the market: no stop-loss
  (`InpMinSlDistancePips`/`InpMaxSlDistancePips` also catch a stop that's
  implausibly tight or wide — a likely fat-finger), and a current price that
  has drifted more than `InpMaxEntryDeviationPips` from the signaled zone.

### SMC validation (`InpUseSmcFilter`, on by default)

Independent of whatever the message *says* about market structure, the EA
checks the actual price history on `InpSmcTF` (default M15):

1. **Liquidity sweep** — within the last `InpSweepRecentBars` closed bars,
   price must have pierced beyond the extreme of the prior
   `InpSweepRefBars` bars (below a prior low for a BUY, above a prior high
   for a SELL) and closed back on the right side of it — a stop-hunt-then-
   reclaim, not a clean breakout.
2. **Premium/discount** — the entry must sit in the cheaper half of the
   combined swing range for a BUY, the richer half for a SELL.
3. The signal's own stop-loss should sit beyond the swept extreme (the level
   that failed), not inside it.

This is a deliberately simplified, fully computable proxy for SMC entry
logic — two rolling min/max windows, not a full fractal/order-block engine.
Tune `InpSweepRecentBars`/`InpSweepRefBars`/`InpSweepMinPiercePips`, or turn
`InpUseSmcFilter` off, if it is too strict or too loose for the channel you
follow. Every check, pass or fail, is printed to the Experts log with the
actual price levels it compared.

### Logging: every signal, and a separate EA for results

`TelegramSMC_Copier.mq5` appends one row to **`TelegramSMC_Signals.csv`** for
*every* Telegram message it evaluates — accepted or not, and why — to this
terminal's `MQL5\Files`. Columns: `time_utc, chat_id, action, direction,
symbol_ok, entry_low, entry_high, sl, tps, smc_used, smc_pass, smc_reason,
sanity_pass, sanity_reason, accepted, order_type, order_price, lots,
dry_run, order_ticket, retcode, raw_text`. A rejected signal still gets a
row — `accepted=0` with the reason in `sanity_reason` or `smc_reason` — so
the file is a complete record of what the channel posted, not just what
traded.

**Trade results are logged by a separate EA on purpose:**
`MQL5/Experts/TelegramSMC_TradeLogger.mq5`. It does not place, modify or
close a single order, and it never talks to Telegram — it only *watches*
this account's trade history for a given `InpMagicNumber`/`InpSymbol` (via
`OnTradeTransaction`) and appends one row per position **open** and one row
per **close** to `TelegramSMC_Results.csv`: `time_utc, event, position_id,
order_ticket, symbol, magic, direction, volume, price, sl, tp, profit,
swap, commission, net_profit, close_reason, duration_min, price_move,
comment`. `close_reason` comes straight from MT5's own deal history
(`DEAL_REASON_SL`, `DEAL_REASON_TP`, `DEAL_REASON_CLIENT`, …) rather than
being guessed from price, and `duration_min`/`price_move` are computed by
looking up the position's own opening deal in history — which works
correctly even for a position that was already open before this EA was
attached, since nothing here depends on anything remembered in memory.

Being a separate EA is deliberate, not incidental: it keeps recording
results for whatever has that magic number/symbol regardless of whether the
Copier is running, being restarted, or replaced, and it can be attached to
any chart (it reads account-wide history, not chart ticks). Load
`MQL5/Presets/TelegramSMC_TradeLogger_Default.set` and set
`InpMagicNumber`/`InpSymbol` to match whatever EA is actually trading
(20260918 / XAUUSD match the Copier's own defaults).

The two files are **not automatically merged** — join them yourself on
`order_ticket` (the Copier logs the order it placed; the Logger logs
`DEAL_ORDER`, the order that generated each deal) if you want the original
signal text next to its eventual profit and close reason.

### Honest limitations

- **No backtest.** There is no historical Telegram feed to replay, so the
  Copier EA disables its own polling inside the Strategy Tester. It can only
  be meaningfully evaluated live or on a demo account.
- **One symbol, one magic number, no per-setup tracking.** CLOSE, CANCEL and
  "move SL to breakeven" messages act on *every* position/pending order this
  EA currently has open on the chart's symbol, because the messages carry no
  ticket or setup id to act on selectively.
- **The Logger EA writes no OPEN row for a position opened before it was
  attached** (there is no on-init backfill), though that position's eventual
  CLOSE row is still complete, since it is computed from history at the
  moment of the close rather than from anything remembered in memory. A
  partial close is logged as its own CLOSE row with that deal's own volume
  and profit, not merged into one final row per position.
- **The parser is best-effort, not a general NLP engine.** It looks for
  BUY/SELL/LONG/SHORT, a price or price range, SL/STOPLOSS/S-L, and
  TP/TP1/TP2/…, and leaves anything it can't make sense of alone rather than
  guessing. Unusual phrasing may simply be logged as "did not parse as an
  actionable signal."
- This executes real orders from text messages it did not originate and
  cannot fully verify the reasoning behind. The SMC filter and sanity checks
  catch stale or obviously broken signals, not bad trading calls. Demo-test
  with `InpDryRun = true` until you trust the channel, the parser's log
  output for every message it sees, **and** this EA's behavior, in that
  order.

## Dashboard (ASP.NET)

`ASPX/Dashboard.aspx` is a single, self-contained Web Forms page (classic
.NET Framework, not Core) that reads the two CSVs above -
`TelegramSMC_Signals.csv` and `TelegramSMC_Results.csv` - and renders a
read-only dashboard: KPI tiles (signals received/accepted, trades closed,
net P/L, win rate, average duration), a cumulative-P/L line chart, a
close-reason breakdown, an accepted/rejected outcome split, top rejection
reasons, and the latest 50 signals and trades as tables. It never writes to
either CSV. Ships with bundled sample data under `ASPX/App_Data/` so it
renders something meaningful before you've pointed it at real logs - see
`ASPX/README.md` for IIS deployment, pointing `Web.config` at the live CSV
paths (same-machine or via a UNC path/sync job), and security notes.
