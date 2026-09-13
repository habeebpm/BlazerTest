# XAUUSD Confluence Bot (Python + MetaTrader 5)

Python port of the MQL5 EA. It watches XAUUSD and opens **0.02 lots** with a
**60-pip stop-loss ($6.00)** and a **30-pip trailing stop ($3.00)**, but only
when **all three confluences** agree on the same direction.

## The three confluences

| # | Confluence | Passes when (buy side) |
|---|---|---|
| 1 | **Trend** | H4 close > EMA(200) **and** EMA(20) > EMA(50) **and** that cross happened within the last `cross_lookback` bars |
| 2 | **Momentum** | MACD main > signal **and** MACD rising **and** 50 < RSI < 70 |
| 3 | **Strength** | ADX ≥ 22 **and** +DI > −DI |

Sell is the mirror image. A trade is taken **only at 3/3** — never 2/3. The log
prints the state every bar, e.g. `BUY[T/M/S=nYY 2/3] SELL[...] -> no trade`.

Guards that can still veto a 3/3 signal (spread, session, max positions,
max trades/day, daily-loss limit, weekend window) are deliberately kept
separate from the confluence count.

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
`--lots 0.02`, `--force` (trade despite preflight problems — not advised), `-v`.

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
- With the default `cross_lookback = 8`, expect roughly **one signal per day**
  on M15. Requiring the cross on the very last closed bar (the obvious reading)
  drops that to about **one signal per 2000 bars**, because ADX is still below
  its threshold at the moment the EMAs cross. That measurement is why the
  default is 8 in both this bot and the EA.
- No economic-calendar news filter. Gold moves violently on NFP/FOMC/CPI.
- A 60-pip stop with a 30-pip trail means the trail starts tightening as soon
  as the trade is 30 pips ($3.00) ahead, so many trades will exit near
  breakeven rather than running. That is the trade-off of a tight trail; widen
  `trail_start_units` if you would rather give winners more room.
- This is not a profitable-by-construction system. Demo-test it first.
