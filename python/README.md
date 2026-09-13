# XAUUSD Confluence Bot (Python + MetaTrader 5)

Python port of the MQL5 EA. It watches XAUUSD and opens **0.02 lots** with a
**60-pip stop-loss ($6.00)** and a **30-pip trailing stop ($3.00)**, but only
when at least **2 of the 3 confluences** agree and **at least one of them is
confirmed**.

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

### What dropping to 2/3 costs you

Measured across five independent 900-bar synthetic series:

| Rule | Signals | ≈ trades/day (M15) |
|---|---|---|
| 3/3 (the old rule) | 24 | 0.77 |
| 2/3, no confirmation | 293 | 9.38 |
| **2/3 + ≥1 confirmed (default)** | **179** | **5.73** |
| 2/3 + ≥2 confirmed | 30 | 0.96 |

Two consequences worth knowing:

1. **`max_trades_per_day = 6` is now the binding constraint on busy days** at
   ~5.7 signals/day. Raise it if you want every signal taken.
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

`--live` is opt-in by design: without it the bot logs every decision and the
exact order it *would* have sent, which is how you should run it first.

Useful flags: `--unit {point,pip,usd}`, `--sl-units 60`, `--trail-units 30`,
`--lots 0.02`, `--min-confluences {1,2,3}`, `--min-confirmed {1,2,3}`,
`--no-confirmation`, `--require-trend`, `--force` (trade despite preflight
problems — not advised), `-v`.

## Files

| File | Role |
|---|---|
| `config.py` | All settings; lot size, distances, unit handling |
| `indicators.py` | EMA/MACD/RSI/ADX/ATR/Bollinger, matched to MT5's formulas |
| `strategy.py` | The three confluences (pure pandas, no MT5 import) |
| `mt5_client.py` | Connection, rates, preflight, order send, trailing modify |
| `trader.py` | Main loop, guards, CLI |
| `selftest.py` | Strategy/indicator/trailing checks |
| `test_integration.py` | Drives the bot against a stub MetaTrader5 module |

`indicators.py` follows MT5's conventions, not the textbook ones, so the Python
bot and the MQL5 EA agree: MACD's signal line is an **SMA**, ATR uses an **SMA**
of True Range, RSI/ADX use **Wilder** smoothing, Bollinger uses a **population**
standard deviation.

Output goes to `logs/trader.log` and every entry is appended to
`logs/trades.csv`.

## Honest notes

- Trades are evaluated once per closed working-timeframe bar and use only
  closed-bar values — no repainting.
- With `cross_lookback = 8` and the 2-of-3 rule, expect roughly **6 signals per
  day** on M15 (one per day under the old 3-of-3 rule). Requiring the cross on the very last closed bar (the obvious reading)
  drops that to about **one signal per 2000 bars**, because ADX is still below
  its threshold at the moment the EMAs cross. That measurement is why the
  default is 8 in both this bot and the EA.
- No economic-calendar news filter. Gold moves violently on NFP/FOMC/CPI.
- A 60-pip stop with a 30-pip trail means the trail starts tightening as soon
  as the trade is 30 pips ($3.00) ahead, so many trades will exit near
  breakeven rather than running. That is the trade-off of a tight trail; widen
  `trail_start_units` if you would rather give winners more room.
- This is not a profitable-by-construction system. Demo-test it first.
