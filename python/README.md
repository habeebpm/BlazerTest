# XAUUSD Confluence Bot (Python + MetaTrader 5)

Python port of the MQL5 EA. It watches XAUUSD and opens **0.01 lots** with a
**60-pip stop-loss ($6.00)** and a **30-pip trailing stop ($3.00)**, but only
when at least **2 of the 3 confluences** agree and **at least one of them is
confirmed**. Entries are evaluated on every **M5 candle close**, with up to
**4 positions open at a time** (same direction only), **no daily trade cap**,
and a **setup score of 50 or better** required, inside a **06:00–23:00 Oman
(GMT+4)** session — about **4.6 fills per day**. Positions are closed shortly
before the session ends, so nothing is carried through a market close.

> **Session note:** the MQL5 EA reads the broker's *real* trading hours via
> `SymbolInfoSessionTrade` (tracking broker DST, gold's daily break and
> Friday's early close automatically). The MetaTrader5 Python API exposes no
> session schedule, so this bot uses the configured GMT-offset window instead.

> **On a Mac?** The `MetaTrader5` Python package is Windows-only. See
> [Running on a Mac](#running-on-a-mac) — the MQL5 EA is the simplest route.

## The three confluences

Each confluence votes at two strengths — a **pass** and a stricter
**confirmed** (buy side shown; sell is the mirror):

| # | Confluence | Passes when | Confirmed when |
|---|---|---|---|
| 1 | **Trend** | H4 close > EMA(200), EMA(20) > EMA(50), cross within `cross_lookback` bars | EMAs separated by ≥ `0.25 × ATR` |
| 2 | **Momentum** | MACD > signal and rising, 50 < RSI < 70 | MACD histogram expanding **and** RSI ≥ 55 |
| 3 | **Strength** | ADX ≥ 22, +DI > −DI | ADX ≥ 28 **and** DI gap ≥ 8 |

### The entry rule

A trade needs **≥ 2 of 3 passes** with **≥ 1 confirmed**. The confirmation tier
is what stops two barely-passing readings (ADX 22.1 alongside RSI 50.4) from
opening a position — in testing it rejects **38% of raw 2/3 signals**.

A **Bollinger veto** sits outside the vote: no buying at/above the upper band,
no selling at/below the lower band, no matter how many confluences agree.

Every bar logs the state, e.g. `BUY[T/M/S=Cyn 2/3 conf=1]` — `C` confirmed,
`y` passed, `n` failed.

### Confidence score (0–100)

Every evaluation carries a **setup score** for each direction, logged each bar
(`BUY[T/M/S=Cyn 2/3 conf=1 score=61%]`) and recorded in `logs/trades.csv`.

Each confluence is worth a third of the score. Passing earns 60% of that
third; the remaining 40% is earned continuously by how far *past* its
confirmation threshold the indicator actually sits — so a barely-confirmed ADX
of 28.1 scores well below an ADX of 40 with a wide DI spread.

Measured over 276 qualifying signals:

| Score | Signals | Meaning |
|---|---|---|
| 40–49 | 132 (48%) | the weakest setups that still qualify |
| 50–59 | 101 (37%) | |
| 60–69 | 23 (8%) | |
| 70–79 | 18 (7%) | |
| 80+ | 2 (1%) | rare, near-unanimous evidence |

Floor is **40** (two passes, one barely confirmed — the minimum that qualifies)
and the practical ceiling is mid-80s; a perfect 100 needs all three confluences
confirmed *and* every indicator far past its threshold.

**The shipped gate is `min_confidence = 50`** — setups scoring below 50 are
skipped. The qualifying floor is 40, so this drops the weakest band.

**Every confirmation is evaluated on the working timeframe (M5).** The EMA gap
uses the M5 EMAs and M5 ATR; the MACD histogram and RSI are M5; the ADX and DI
spread are M5. The only higher-timeframe input in the whole strategy is the
EMA(200) macro bias on `trend_timeframe` (H4), and it feeds the trend
confluence's *pass* test, never a confirmation.

Score ceilings, which explain what a gate actually selects for:

| Setup | Best possible score |
|---|---|
| 2/3 passing, 1 confirmed | 53.3 |
| 3/3 passing, 0 confirmed | 60.0 |
| 2/3 passing, 2 confirmed | 66.7 |
| 3/3 passing, 1 confirmed | 73.3 |
| 3/3 passing, 3 confirmed | 100.0 |

Since an unconfirmed leg is worth only 20 points, a high gate quietly selects
for 3-of-3 setups: at 65 only 6 of 26 survivors were 2-of-3, versus 121 of 144
at 50. Pick the gate against how often you want to trade and how much you want
the 2-of-3 rule to matter:

Fills per day by gate and position cap (all other filters on):

| Gate | 2 pos | 3 pos | 4 pos | 5 pos | 6 pos |
|---|---|---|---|---|---|
| 50 | 4.7 | 5.9 | 6.9 | 7.6 | 8.1 |
| 45 | 5.6 | 7.3 | 8.5 | 9.5 | 10.1 |
| **off (shipped)** | 6.4 | 8.4 | **10.0** | 11.2 | 12.0 |

Those figures are for 24-hour trading. With the shipped **06:00–23:00 Oman**
session applied, actual fills are:

| Window | Hours | Gate 50 | Gate 45 | Gate off |
|---|---|---|---|---|
| 07:00–20:00 (old) | 13 | 3.6 | 4.4 | 5.1 |
| **06:00–23:00 (shipped)** | **17** | **4.6** | 5.7 | 6.6 |
| no session filter | 24 | 6.9 | 8.5 | 10.0 |

Where the shipped 9.3 qualifying signals/day go: 3.5 blocked by the session
window, 1.1 by the position cap, 0.1 by an opposing trade, leaving **4.6 fills**
— roughly 23 trades a week.

**Signals are not trades.** A signal only becomes a fill when a position slot
is free and nothing opposing is open, and slots stay occupied until the stop
or trail is hit — about 41 M5 bars (3.4 hours) on average at the shipped
distances. Roughly half of all qualifying signals never reach the market.
`simulate.py` reports fills rather than signals; prefer its numbers.

At 50 the qualifying floor of 40 means the gate drops the weakest band of
setups while leaving the 2-of-3 rule doing real work. Raise it to 55 or 65 to
trade less and more selectively.

> ### ⚠️ What this score is NOT
>
> It is **not** a probability that the trade wins, and not an edge estimate.
> It measures how strongly the indicators agree at entry — nothing more. A
> 75% setup is not "75% likely to profit"; it means the evidence was strong
> by this EA's own definition. Only a backtest on real tick data can produce
> a win rate or expectancy, and none has been run — see "Honest notes".

### What dropping to 2/3 costs you

Measured across five independent 900-bar synthetic series:

| Rule | Signals | ≈ trades/day (M15) |
|---|---|---|
| 3/3 (the old rule) | 24 | 0.77 |
| 2/3, no confirmation | 293 | 9.38 |
| **2/3 + ≥1 confirmed** | **179** | **5.73** |
| 2/3 + ≥2 confirmed | 30 | 0.96 |

Moving from M15 to M5 multiplies that again — same rule, 3x the bars:

| Timeframe | bars/signal | ≈ trades/day |
|---|---|---|
| M15 | 18.3 | 5.2 |
| **M5 (default)** | **16.3** | **17.7** |

Two consequences worth knowing:

1. **There is no daily trade cap** (`max_trades_per_day = 0`). At ~17 signals
   a day on M5, the only brakes left are `max_open_positions = 2`, the
   same-direction rule, the session window and the **3% daily-loss circuit
   breaker** — which becomes your main protection. At 0.02 lots a $6.00 stop
   risks about $12 per trade, so size the account accordingly, or set
   `--max-trades-per-day` back to a number.
2. **The trend leg is no longer mandatory**, so momentum + strength can open a
   trade *against* the H4 trend — roughly 1 signal in 20 in testing. Set
   `require_trend_confluence = True` (or `--require-trend`) to force the trend
   confluence to be one of the two passing ones; that removes counter-trend
   entries entirely.

Guards that can still veto a qualifying signal (spread, session, max positions,
max trades/day, daily-loss limit, weekend window) stay separate from the
confluence count.

## Distance units

Gold distances get quoted three ways, so `distance_unit` makes the choice
explicit. With the shipped defaults of 60 / 30:

| `distance_unit` | 1 unit | SL | Trail |
|---|---|---|---|
| `point` (MT5 point) | 0.01 | $0.60 | $0.30 |
| **`pip` (default)** | 0.10 | **$6.00** | **$3.00** |
| `usd` | 1.00 | $60.00 | $30.00 |

A typical XAUUSD spread is 15–40 points ($0.15–$0.40), so the default 60-pip
stop clears it with roughly 15x of margin and the 30-pip trail with about 7x.

`preflight_check()` still re-validates this against your broker's live spread
and minimum stop distance at startup and refuses to trade live if the stop is
too tight to survive — an earlier 6-*point* ($0.06) configuration is correctly
rejected:

```
Stop-loss distance 0.06 is smaller than the current spread 0.25 (25 points).
A buy would be stopped out the instant it opens.
```

Override without editing files: `--unit pip --sl-units 60 --trail-units 30`.

## When the market is closed

The bot handles a shut session on its own — useful since you cannot test
against live quotes right now:

- **Detection is timezone-safe.** It watches whether the quote timestamp
  *advances*, rather than comparing the broker's server-time stamp to your
  local clock (brokers commonly run GMT+2/+3, so "the tick is 3 hours old"
  usually means a timezone gap, not a closed market).
- **No orders are sent** while quotes are frozen; entries report
  `market is closed` and trailing-stop modifications are skipped rather than
  fired off to be rejected.
- **It resumes by itself** the moment quotes start moving again — no restart.
- **Preflight demotes spread complaints to warnings** while closed, because a
  closed-market spread reading is stale or artificially padded. Re-run
  `--check` once the session opens for a real verdict.
- If an order is ever rejected with `TRADE_RETCODE_MARKET_CLOSED`, that is
  logged as a plain warning, not an error.

So right now you can run `--selftest`, `test_integration.py`, `--check` and
`--signal`, and leave the bot running in dry-run; it will pick up quotes when
the market reopens.

## Running on a Mac

`pip install MetaTrader5` fails on macOS: the package publishes Windows-only
wheels and drives the terminal over Windows IPC. Four ways round it, easiest
first:

1. **Run the MQL5 EA instead — recommended.** MetaQuotes ships MT5 for macOS,
   and `MQL5/Experts/XAUUSD_Confluence_EA.mq5` runs natively inside it with the
   same strategy, the same 2-of-3 rule, M5 entries, 2 concurrent positions and
   no daily cap. No Python involved. Load
   `MQL5/Presets/XAUUSD_Confluence_EA_Default.set` and you are done.
2. **Windows VM** — Parallels, VMware Fusion or UTM, with MT5 + Python inside
   the VM. The full Python bot then works unchanged.
3. **Windows VPS** — the usual choice for running a bot 24/7, and it keeps
   trading while your Mac is asleep.
4. **Wine/CrossOver bottle** — install Windows Python into the same bottle as
   MT5 and `pip install MetaTrader5` there. Works, but the fiddliest option.

**What does run natively on your Mac:** everything except live trading. The
strategy, indicators and both test suites are pure pandas/numpy:

```bash
pip install pandas numpy
python trader.py --selftest      # strategy + entry-rule + distance checks
python test_integration.py       # full order/trailing path against a mock MT5
python simulate.py --compare     # fills/day, holding time and spread cost
python simulate.py --csv bars.csv   # ...against your own exported M5 bars
```

Those two cover the entry rule, the trailing-stop maths and the order
construction, so you can develop and verify strategy changes on the Mac and
only need Windows (or the EA) to place real orders.

## Install (Windows — MT5 required)

```bat
pip install -r requirements.txt
set MT5_LOGIN=12345678
set MT5_PASSWORD=your-password
set MT5_SERVER=YourBroker-Demo
```

Credentials are read from the environment and never stored in the repo. Leave
them unset to attach to an already-running, already-logged-in terminal.

## Run

```bat
python trader.py --selftest        :: strategy self-test, no MT5 needed
python test_integration.py         :: full run against a mock MT5, no MT5 needed
python trader.py --check           :: connect, print symbol spec + preflight, exit
python trader.py --signal          :: evaluate the 3 confluences once, exit
python trader.py                   :: live data, DRY-RUN (no orders sent)
python trader.py --live            :: actually trade, 60-pip stop / 30-pip trail
```

Entries are checked once per closed M5 bar; the 5-second poll only services
trailing stops. Override with `--timeframe M15`, `--max-positions 1`,
`--max-trades-per-day 10`.

`--live` is opt-in by design: without it the bot logs every decision and the
exact order it *would* have sent, which is how you should run it first.

Useful flags: `--unit {point,pip,usd}`, `--sl-units 60`, `--trail-units 30`,
`--lots 0.02`, `--min-confluences {1,2,3}`, `--min-confirmed {1,2,3}`,
`--no-confirmation`, `--require-trend`, `--timeframe M5`, `--max-positions 2`,
`--max-trades-per-day 0`, `--force` (trade despite preflight problems — not
advised), `-v`.

## Files

| File | Role |
|---|---|
| `config.py` | All settings; lot size (0.01), distances, unit handling |
| `indicators.py` | EMA/MACD/RSI/ADX/ATR/Bollinger, matched to MT5's formulas |
| `strategy.py` | The three confluences (pure pandas, no MT5 import) |
| `mt5_client.py` | Connection, rates, preflight, order send, trailing modify |
| `trader.py` | Main loop, guards, CLI |
| `selftest.py` | Strategy/indicator/trailing checks |
| `test_integration.py` | Drives the bot against a stub MetaTrader5 module |
| `simulate.py` | Counts actual fills (not signals) under the position rules |
| `claude_signal_bot.py` | Separate, experimental pipeline: hands live multi-timeframe data exported by `MQL5/Experts/ClaudeSignalEA.mq5`, plus the "XTR" setup archetypes and general trading principles as knowledge, to Claude for real-time analysis; Claude decides direction/setup/SL/TP, Python enforces only risk-containment backstops (stop-distance sanity, two-loss standdown, time-decay, position sizing) and writes the resulting signal back for the EA to execute. See `CLAUDE_SIGNAL_PIPELINE.md`. |

`indicators.py` follows MT5's conventions, not the textbook ones, so the Python
bot and the MQL5 EA agree: MACD's signal line is an **SMA**, ATR uses an **SMA**
of True Range, RSI/ADX use **Wilder** smoothing, Bollinger uses a **population**
standard deviation.

Output goes to `logs/trader.log` and every entry is appended to
`logs/trades.csv`.

## Honest notes

- Trades are evaluated once per closed working-timeframe bar and use only
  closed-bar values — no repainting.
- With `cross_lookback = 8`, the 2-of-3 rule, M5 entries, the 50 gate and the
  06:00–23:00 Oman window, expect ~**9.3 qualifying signals and ~4.6 actual
  fills per day**.
- **Session hours are GMT-offset based, not broker time.** `session_gmt_offset`
  defaults to 4.0 (Oman/GST, no DST), so the window means the same wall-clock
  hours whatever offset your broker runs on and regardless of broker DST. The gap is the
  position cap plus holding time; run `python simulate.py` to see it broken
  down. M5 is noisier than M15, so more setups will be marginal.
- **Trade frequency has a hard ceiling.** With the quality filters on, fills
  asymptote at ~9.3/day however high you raise `max_open_positions` — signal
  supply, not concurrency, is the limit. Targeting substantially more than
  that means weakening the entry criteria, which is a different strategy
  rather than a tuning change.
- Up to four positions may be open at once, but only in the same direction; an
  opposing signal is skipped rather than hedged (`allow_opposite_positions`).
  **They are correlated** — same symbol, same way — so an adverse move loses on
  all of them together. One XAUUSD lot is 100oz, so $1 of price is $1 per 0.01
  lot: a $6.00 stop risks **$6 per trade, $24 across four positions**. The 3%
  daily-loss breaker allows 5 losing trades on a $1,000 account, 2.5 on $500.
- At ~4.6 fills/day the spread costs about **$1.15/day, ~$25/month** at a
  $0.25 spread and 0.01 lots. Run `python simulate.py` to recompute for your spread. Requiring the cross on the very last closed bar (the obvious reading)
  drops that to about **one signal per 2000 bars**, because ADX is still below
  its threshold at the moment the EMAs cross. That measurement is why the
  default is 8 in both this bot and the EA.
- No economic-calendar news filter. Gold moves violently on NFP/FOMC/CPI.
- A 60-pip stop with a 30-pip trail means the trail starts tightening as soon
  as the trade is 30 pips ($3.00) ahead, so many trades will exit near
  breakeven rather than running. That is the trade-off of a tight trail; widen
  `trail_start_units` if you would rather give winners more room.
- **No profitability claim is being made, and no win rate is known.** Every
  number in this README describes *behaviour* (how often it signals, how
  strong the setups are), never profit. All of it was measured on synthetic
  random-walk series, which are useful for verifying logic and frequency but
  say nothing about whether the strategy makes money — a random walk has no
  edge to find by construction.
- To get a real figure, run the MT5 Strategy Tester on your broker's XAUUSD
  with "Every tick based on real ticks" over several years, then read the
  **Profit Factor**, **Expected Payoff**, **max drawdown** and trade count from
  the report. Validate on a period you did not optimise over. That report is
  the only trustworthy answer to "how confident should I be in this EA".
- This is not a profitable-by-construction system. Demo-test it first.
