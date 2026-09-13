# XAUUSD Confluence Bot (Python + MetaTrader 5)

Python port of the MQL5 EA. It watches XAUUSD and opens **0.02 lots** with a
**6-unit stop-loss** and a **3-unit trailing stop**, but only when **all three
confluences** agree on the same direction.

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

## ⚠️ Read this about "points" before going live

"Point" is ambiguous on gold and getting it wrong costs real money:

| `distance_unit` | 1 unit | Your 6 / 3 becomes |
|---|---|---|
| `point` (MT5 point, the literal reading) | 0.01 | SL **$0.06**, trail **$0.03** |
| `pip` | 0.10 | SL $0.60, trail $0.30 |
| `usd` | 1.00 | SL **$6.00**, trail **$3.00** |

A typical XAUUSD spread is 15–40 points (**$0.15–$0.40**), so a literal 6-point
stop sits *inside the spread* — the broker rejects the order, or you are stopped
out the instant you are filled. `preflight_check()` verifies this against your
broker's live spread and minimum stop distance at startup and **refuses to trade
live** rather than bleed money. Verified behaviour:

```
Stop-loss distance 0.06 is smaller than the current spread 0.25 (25 points).
A buy would be stopped out the instant it opens.
```

If that is not what you meant, run with `--unit usd` for a $6.00 stop / $3.00
trail, which is a sane gold configuration.

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
python trader.py --live --unit usd :: actually trade, $6 stop / $3 trail
```

`--live` is opt-in by design: without it the bot logs every decision and the
exact order it *would* have sent, which is how you should run it first.

Useful flags: `--unit {point,pip,usd}`, `--lots 0.02`, `--force` (trade despite
preflight problems — not advised), `-v`.

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
- This is not a profitable-by-construction system. Demo-test it first.
