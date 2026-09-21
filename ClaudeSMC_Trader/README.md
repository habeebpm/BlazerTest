# Claude-SMC Trader (MQL5 + Python + Claude)

A separate, self-contained XAUUSD trading solution: a Python service reads
the live MT5 chart, builds a broad "trading intelligence" snapshot, and asks
Claude to validate the three-confluence framework already used elsewhere in
this repo (see `../python/README.md`) - but with an LLM doing the judgment
call instead of a hard-coded if-statement. Only when Claude reports **full
conviction** does the system execute. A small MQL5 EA handles tick-by-tick
trade management (the arm-then-trail exit) so protection doesn't depend on
Python's poll loop catching a fast move.

This solution is independent of `../MQL5/` and `../python/` (the Telegram
copier stack) - it doesn't share config, magic numbers, or code with them,
so both can run on the same account at once without interfering.

## The trading rules (fixed, as requested)

| Rule | Value |
|---|---|
| Fixed lot size | **0.01** |
| Max concurrent same-direction positions | **5** |
| Stop-loss | **$6.00** (USD account risk, converted to a price distance via the broker's own tick value - see "Why dollars, not points" below) |
| Take-profit / trail-arm level | **$6.00** |
| Trailing distance once armed | **$3.00** |
| Execution trigger | Only Claude's **full conviction** verdicts (see below) |

Every value above is a default in `python/config.py` and overridable via CLI
flags on `python/main.py --help`.

## Architecture

```
MT5 terminal (chart data)
        |  MetaTrader5 Python package (local, same machine)
        v
python/mt5_gateway.py          <- bars, tick, symbol spec, positions
        |
        v
python/market_intel.py         <- indicators + SMC structure + price action +
        |                          session info, ALL computed from live bars
        v
python/claude_advisor.py       <- sends the snapshot to Claude, gets back a
        |                          structured ConfluenceVerdict
        v
python/executor.py             <- gates on confluence_count / conviction /
        |                          same-direction cap / daily cap, then
        |                          places the order (fixed lot, $6 SL, $6 TP)
        v
MT5 terminal (order placed)
        |
        v
MQL5/Experts/ClaudeSMC_TradeManager.mq5   <- runs INSIDE MT5, tick-by-tick,
                                              converts the $6 TP to a $3
                                              trail once armed. Places NO
                                              new trades - Python already did.
```

`python/main.py` is the orchestration loop: on every newly **closed** candle
on the primary timeframe (M15 by default), it rebuilds the snapshot, asks
Claude, and executes if the verdict clears every gate. It does not poll every
tick - trade management on already-open positions is the MQL5 EA's job,
specifically because a tick EA can react to a fast spike past the arm level
that a 30-second Python poll loop could miss entirely.

## What "all possible trading intelligence" means here

Every evaluation sends Claude a JSON snapshot (see `market_intel.py`)
containing:

- **Trend**: EMA20/EMA50/EMA200 on the primary timeframe, EMA200 macro bias
  on the trend timeframe (H4 by default), ATR14
- **Momentum**: RSI14, MACD(12,26,9) line/signal/histogram (current and
  previous, so Claude can see whether it's expanding), Stochastic(14,3,3)
- **Strength**: ADX14, +DI/-DI
- **Volatility**: ATR14, Bollinger Bands(20,2) %B and bandwidth
- **SMC structure**: liquidity-sweep detection (a stop-hunt-then-reclaim
  beyond a prior swing extreme - the same definition the Telegram-copier
  EA's SMC filter uses) and premium/discount zoning within the recent range
- **Price action**: the last closed candle's body/wick ratios, bullish/
  bearish engulfing, bullish/bearish pin bar
- **Session**: active session(s) (Asian/London/New York), day of week, hour
  (UTC), current spread
- **Raw data**: the last 20 closed candles' OHLCV, so Claude isn't limited to
  pre-digested numbers - it can look at the actual price action directly

Claude is told the exact pass/confirm thresholds for each of the three
confluence legs (copied verbatim from `../python/README.md`'s documented and
measured rule table), so it's applying the same proven framework - just with
room to down-weight a mechanically-passing setup that's structurally ugly
(say, a "confirmed" trend leg pointed straight into an unswept liquidity
pool) or up-weight one with strong SMC alignment. See the full prompt in
`python/claude_advisor.py`.

### Honest limitations

Not included, and worth knowing about before treating this as more complete
than it is:

- **No fundamentals or news.** No economic calendar, no headline feed. A
  scheduled NFP release or a surprise Fed statement is invisible to this
  system entirely.
- **No order flow / DOM / real volume.** MT5 only exposes tick volume (trade
  count, not traded size), which is what's used here - there's no Level 2
  data.
- **No cross-asset correlation.** DXY, real yields, and other gold drivers
  aren't fed in. This would be a natural extension (pull a DXY series
  alongside XAUUSD's and hand Claude both).
- **SMC detection is a simplified proxy** - rolling-window swing extremes and
  a pierce-then-reclaim rule, not full fractal/order-block/fair-value-gap
  analysis.
- **A parsed-cleanly, full-conviction verdict is not a guarantee of a good
  trade.** It means the numbers weren't obviously broken and Claude's own
  judgment, given everything above, was confident. Nothing here has been
  backtested against historical tick data.

## Why dollars, not points

`InpTpArmDollars`/`InpTrailDollars` on the MQL5 side and `sl_dollars`/
`tp_arm_dollars`/`trail_dollars` on the Python side are USD account-risk
amounts, not raw price units. Both sides convert dollars to a price distance
with the same broker-agnostic formula, using the symbol's own tick value and
tick size rather than assuming a fixed contract size:

```
price_distance = dollars * tick_size / (tick_value * lots)
```

This is exactly the same formula (inverted) the Telegram copier's
`position_size_for()` already uses in `../python/mt5_client.py` - so a "$6
stop" really does mean $6 of account risk at 0.01 lots on any broker,
regardless of XAUUSD's contract size there.

## Setup

### 1. Python side

```bash
cd python
pip install -r requirements.txt
```

Get an Anthropic API key (or run `ant auth login` if you use the Claude CLI)
and set it:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Sanity-check the connection and config without spending an API call:

```bash
python main.py --check
```

Run a single evaluation cycle (still dry-run by default - nothing is sent):

```bash
python main.py --once -v
```

Once you're happy with what it logs, run it continuously:

```bash
python main.py
```

Add `--live` only when you actually want real orders sent. Everything is
logged to `python/logs/decisions.csv` (every evaluation, accepted or
rejected, with Claude's reasoning) and `python/logs/trades.csv` (every order
actually placed).

### 2. MQL5 side

1. Copy `MQL5/Experts/ClaudeSMC_TradeManager.mq5` into your MT5
   `MQL5/Experts/` folder and compile (F7 in MetaEditor).
2. Drag it onto an XAUUSD chart. In the **Inputs** tab, confirm
   `InpMagicNumber` matches `python/config.py`'s `AdvisorConfig.magic`
   (both default to `20260921` - only change one if you change the other).
   Tick "Allow Algo Trading".
3. Leave `InpDryRun = true` until you've watched it log a few would-be
   trail modifications and trust the output, then flip it off.

Both halves need to be running for the full system to work as designed:
Python decides *whether and when* to enter; the MQL5 EA decides how each
open position's exit evolves. Python alone still protects every trade with
its initial $6 SL and $6 TP even if the MQL5 EA isn't running - you'd just
lose the trail-to-$3 behavior and each trade would simply hit its fixed $6
TP or $6 SL instead.

### Testing without a live account

```bash
cd python
python selftest.py
```

Runs entirely offline - synthetic price data exercises the real indicator
and SMC code, a fake MT5 gateway exercises `executor.py`'s gating logic, and
a fake Anthropic client exercises `claude_advisor.py`'s wiring. No MT5
terminal, no API key, no network needed. This is also the test to run after
changing any threshold or formula in this solution.
