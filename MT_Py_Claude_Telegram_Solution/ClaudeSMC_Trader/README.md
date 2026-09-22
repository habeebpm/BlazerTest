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

`python/backtest.py` walks the same `market_intel`/`claude_advisor`/
`executor` path against historical bars instead of a live MT5 connection,
simulating both entries and the exit logic `ClaudeSMC_TradeManager.mq5`
would apply, and reports trade-by-trade results - see "Backtesting" below.

## What "all possible trading intelligence" means here

Every evaluation sends Claude a JSON snapshot (see `market_intel.py`)
containing:

- **Trend**: EMA20/EMA50/EMA200 on the primary timeframe, EMA200 macro bias
  on the trend timeframe (H4 by default), ATR14
- **Momentum**: RSI14, MACD(12,26,9) line/signal/histogram (current and
  previous, so Claude can see whether it's expanding), a 6-bar MACD
  histogram window plus whether the current bar is declining from that
  window's own peak (`macd_hist_shape` - see the extended-entry check
  below), Stochastic(14,3,3)
- **Strength**: ADX14, +DI/-DI
- **Volatility**: ATR14, Bollinger Bands(20,2) %B and bandwidth
- **SMC structure**:
  - liquidity-sweep detection (a stop-hunt-then-reclaim beyond a prior swing
    extreme - the same definition the Telegram-copier EA's SMC filter uses)
  - premium/discount zoning within the recent range
  - **market structure**: fractal swing highs/lows, each labeled HH/LH or
    HL/LL against the one before it, the trend that implies, and the most
    recent structural break - **BOS** (Break of Structure: a fresh close past
    the last confirmed swing level, continuing the existing trend) or
    **CHoCH** (Change of Character: the same kind of break, but against the
    prevailing trend - the first sign of a possible reversal)
  - **order blocks**: the last opposite-colored candle immediately before a
    displacement move (range ≥1.5x ATR14, ≥60% body) - the standard ICT
    definition - plus whether price is currently trading back inside that
    zone (a classic entry trigger)
  - **fair value gaps**: still-open 3-candle imbalances (a gap counts as open
    only until a later bar trades back through it)
- **Reference levels**: previous day's and week's high/low (PDH/PDL, PWH/PWL)
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

**Extended-entry momentum check.** If Claude judges an entry as already
stretched/chasing (price noticeably extended from its EMAs, RSI14 already
elevated toward the 70/30 block zone rather than freshly crossing into
confluence), momentum passing the mechanical MOMENTUM test above isn't
enough on its own - it also needs to still be *building*, not just present.
`macd_hist_shape.declining_from_peak` tells Claude whether the current
histogram bar has already ticked down from its recent high/low, even though
it may still be on the trade's side of zero. For an entry judged extended,
`declining_from_peak: true` is a NO TRADE (or "partial" at best, never
"full") - wait for a pullback/reset rather than chase decelerating
momentum. This does *not* apply to a fresh, early-stage move that isn't
extended. Added after reviewing real trade history: a losing chase entry
had momentum already peaking and rolling over at entry, while the winning
chase entries all had momentum still accelerating.

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
- **SMC detection is a simplified proxy, not a full ICT toolkit.** Structure,
  order blocks and FVGs are real, mechanically-defined detections now (see
  above) - but still missing: **kill zones** (precise ICT session-timing
  windows and the "Judas swing" concept - session tagging is coarse by
  comparison), **Optimal Trade Entry** (Fibonacci 62-79% retracement zones),
  **equal-highs/lows liquidity clustering** (the sweep detector uses raw
  rolling extremes, not clustered equal levels), **Power of Three**
  (Accumulation/Manipulation/Distribution), and true **multi-timeframe
  top-down bias** (only two timeframes feed in - primary + one higher-TF
  EMA200 bias - not a full weekly→daily→4h→15m nested read).
- **A parsed-cleanly, full-conviction verdict is not a guarantee of a good
  trade.** It means the numbers weren't obviously broken and Claude's own
  judgment, given everything above, was confident. See "Backtesting" below
  for how to actually measure this against history rather than take it on
  faith - and for a real finding a backtest surfaced about the exit design,
  and why `exit_style="sl_to_tp1"` is the shipped default because of it.

## Why dollars, not points

`InpTp1Dollars`/`InpTrailDollars` on the MQL5 side and `sl_dollars`/
`tp1_dollars`/`trail_dollars` on the Python side are USD account-risk
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

## Exit design: `exit_style` in `config.py`

Every position opens with a `$6` stop-loss. What happens from there is
controlled by `AdvisorConfig.exit_style`, with three values:

- **`"sl_to_tp1"` (default, and implemented live by both
  `ClaudeSMC_TradeManager.mq5` and `UnifiedTrader_EA.mq5`'s Claude-management
  half).** Python places **no broker take-profit at all** (`tp_price=0.0`) -
  the stop-loss is the only thing that can ever close the position. Once
  floating profit reaches `tp1_dollars` ($6), the EA moves the SL to
  *exactly* that price in one deterministic step, locking in that much
  profit. From then on it trails `trail_dollars` ($3) behind new highs/lows,
  tightening only, for the rest of the move. "Armed" (locked vs. trailing)
  is never stored anywhere - the EA derives it every tick from whether the
  position's own current SL has already reached the lock level, so it needs
  no memory across ticks or restarts.
- **`"breakeven_r_decay"` (also implemented live, opt-in via each EA's
  `InpExitStyle`).** Adds one earlier protective step before the
  `sl_to_tp1` lock above: once floating profit reaches `breakeven_atr_mult`
  (0.5) x the position's own M5 ATR, **or** `decay_window_minutes` (15) have
  passed since entry - whichever happens first, and only once price has
  actually moved far enough into profit to place a valid stop there - the SL
  moves to *exactly* the entry price (breakeven), no earlier. A fast move
  can still jump straight from unprotected to the full TP1 lock in one tick
  (the lock check always runs first). This came from comparing manual trade
  history against the mechanical exits: `tp1_dollars` at $6 (1.0R) was
  rarely reached fast enough, and a plain time-or-profit breakeven step got
  trades to at least no-loss sooner than waiting for the full TP1 lock.
  `breakeven_atr_period` (14) sets the ATR's own period; the MQL5 side reads
  ATR from `InpAtrTimeframe` (default M5) - keep that timeframe and all
  three dollar/period values in sync with the EA's own
  `InpBreakevenAtrMult`/`InpAtrPeriod`/`InpDecayWindowMinutes` inputs by
  hand, the same way `trail_dollars`/`InpTrailDollars` already have to be
  kept in sync. On `UnifiedTrader_EA.mq5` specifically, this style only ever
  applies to `InpClaudeMagicNumber` positions - Telegram-sourced positions
  always use plain `sl_to_tp1` regardless of `InpExitStyle`.
- **`"fixed_tp"` (the original design, kept only as a comparison baseline -
  not implemented in either live MQL5 EA).** A real broker take-profit is
  placed at `entry + tp1_dollars` - the exact same price the trail would
  arm at. See "What the backtest found" below for why that's a problem and
  why `sl_to_tp1` replaced it as the default.

Override with `main.py --exit-style sl_to_tp1|breakeven_r_decay|fixed_tp`
(or `backtest.py --exit-style` / `--compare`, see "Backtesting" below;
`backtest.py` has no simulation of `breakeven_r_decay` yet - it's live-only,
see "Honest limitations of the backtest itself"). `breakeven_r_decay`'s own
three fields have matching CLI flags: `--breakeven-atr-mult`,
`--breakeven-atr-period`, `--decay-window-minutes`.

**If neither MQL5 EA is running**, a position opened under any `exit_style`
except `fixed_tp` has *only* its initial $6 stop-loss protecting it - no
broker take-profit exists to fall back on, and nothing will ever move the SL
to breakeven, lock in profit, or trail. Both halves need to be running for
the full exit design to work; see "2. MQL5 side" below.

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
   If you're using `exit_style="breakeven_r_decay"`, also set
   `InpExitStyle` to `EXIT_BREAKEVEN_R_DECAY` and keep
   `InpBreakevenAtrMult`/`InpAtrPeriod`/`InpDecayWindowMinutes` (and
   `InpAtrTimeframe`) in sync with `breakeven_atr_mult`/
   `breakeven_atr_period`/`decay_window_minutes` in `config.py` by hand -
   otherwise leave `InpExitStyle` at its default (`EXIT_SL_TO_TP1`). Tick
   "Allow Algo Trading".
3. Leave `InpDryRun = true` until you've watched it log a few would-be
   trail modifications and trust the output, then flip it off.

Both halves need to be running for the full system to work as designed:
Python decides *whether and when* to enter; the MQL5 EA decides how each
open position's exit evolves. Under the default `exit_style="sl_to_tp1"`,
Python places no broker take-profit at all - if the MQL5 EA isn't running,
a trade is protected by nothing but its initial $6 SL, with no lock-in and
no trail (see "Exit design" above).

### 3. Telegram full-conviction alerts (optional)

`python/telegram_alert.py` sends a one-way Telegram message every time
Claude issues a **"full"** conviction verdict - whether or not the trade
actually executes (`executor.gate()` can still reject it: position cap,
daily trade limit, confluence floor - the message says so either way). This
is send-only and completely independent of the Telegram signal-copying
stack elsewhere in this repo (`../python/`, `../MQL5/`,
`../UnifiedTrader/`'s own Telegram side) - it never reads a channel, never
places an order, and a bad token here can't affect trading either direction.

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   and follow the prompts to get a bot token.
2. Start a chat with your new bot (search its username, send it any
   message) so it's allowed to message you back.
3. Find your chat id: open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser right after
   step 2 and read `"chat":{"id": ...}` from the JSON response.
4. Pass both to `main.py`:

```bash
python main.py --telegram-alert-bot-token <TOKEN> --telegram-alert-chat-id <CHAT_ID>
```

(or set `AdvisorConfig.telegram_alert_bot_token`/`telegram_alert_chat_id`
directly in `config.py`). Leave either blank and alerts are simply off -
`send_alert()` is a no-op, and nothing else about the system changes. A
dedicated bot (not one already used by the Telegram copier stack or
`UnifiedTrader_EA.mq5`) is recommended, purely to keep this alert traffic
out of that stack's own chat. `--check` prints `telegram_alerts=on|off`
(never the token itself) so you can confirm it's wired up without spending
a Claude API call.

A Telegram outage, bad token, or rate limit here is caught and logged as a
warning - it never raises, never blocks `run_once()`, and never affects
whether a trade executes. This alert is not simulated in `backtest.py`
(`run_backtest()` never calls it), so a historical replay never spams your
chat even if these two fields are set.

## Running with only one side available

This solution and the Telegram copier stack (`../python/`, `../MQL5/`) share
nothing - no config, no magic number, no code. That's deliberate: either can
run completely on its own, and each one failing has no effect on the other.

**If Claude is out of credits (or the API key is bad, or you're rate-limited,
or the network's down):** `main.py`'s poll loop catches this specifically
(`claude_advisor.ClaudeUnavailableError`) and does not crash. It logs exactly
why, and:
- For something that clears on its own (rate limit, a 5xx, a network blip),
  it just retries at the normal poll interval.
- For something that needs you to act (out of credits, a bad API key,
  access denied), it backs off to checking every 30 minutes instead of
  hammering the same failure, and says so in the log.

Either way, no new signals get evaluated until Claude is reachable again -
but `ClaudeSMC_TradeManager.mq5` keeps managing any already-open positions'
exits on its own (it has no Claude dependency at all), and the Telegram
copier stack is entirely unaffected since it never calls Claude in the
first place. Practically: if you want trading to continue on Telegram
signals while sorting out a Claude billing issue, just leave the Telegram
copier's MQL5 EA (and/or `telegram_copier.py`) running and either stop
`main.py` or let it idle in its 30-minute backoff - nothing you do to one
system reaches the other.

**If Telegram is unavailable** (relay bridge down, bot token revoked, rate
limited by Telegram): the reverse holds - `TelegramSMC_Copier.mq5` and/or
`telegram_copier.py` will simply stop seeing new messages and log that, and
this solution's `main.py` keeps evaluating and trading on Claude's
confluence calls exactly as before, since it never touches Telegram.

### Testing without a live account

```bash
cd python
python selftest.py
```

Runs entirely offline - synthetic price data exercises the real indicator
and SMC code, a fake MT5 gateway exercises `executor.py`'s gating logic, a
fake Anthropic client exercises `claude_advisor.py`'s wiring, and a fake
HTTP poster exercises `telegram_alert.py`'s send/no-op/failure-handling
logic. No MT5 terminal, no API key, no network needed. This is also the
test to run after changing any threshold or formula in this solution.

## Backtesting

`python/backtest.py` replays historical bars through the exact same code
path live trading uses - `market_intel.build_feature_snapshot`,
`claude_advisor.get_verdict` (the real Claude API, by default), and
`executor.gate`/`execute` - then simulates each position's exit per
`exit_style` (see "Exit design" above) against the bars that follow, and
reports a trade log plus summary stats (win rate, net $, avg win/loss, max
drawdown).

> **COST WARNING:** by default this calls the real Claude API once per
> evaluated bar. A few months of M15 history is thousands of candles -
> thousands of real API calls. It prints an estimate and asks you to
> confirm before spending anything (`--yes` skips the prompt once you trust
> the estimate). Use `--mechanical` first - a free, non-LLM stand-in that
> applies the exact same pass/confirm rules Claude is told to use - to
> check the engine itself (data lines up, gating works, exits look right)
> before paying to test Claude's actual judgment.

```bash
cd python

# Free: test the engine/exit-simulation plumbing, no API key needed
# (defaults to exit_style=sl_to_tp1, the live default)
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical

# Free: run BOTH exit styles side by side against identical bars and an
# identical Claude/mechanical verdict stream - the direct way to measure
# sl_to_tp1 against the original fixed_tp design (see the findings below)
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical --compare

# Real backtest - calls the real Claude API, costs money, asks first
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv

# Or pull history straight from a running MT5 terminal instead of CSVs
python backtest.py --from-mt5 --start 2026-01-01 --end 2026-04-01
```

CSV format: columns `time,open,high,low,close,volume`, one row per CLOSED
historical bar, ascending. Export these from MT5 (or any data source) for
the primary timeframe (M15), the trend timeframe (H4), D1, and W1.

**The single most important property of any backtest is no lookahead bias.**
`backtest.HistoricalGateway` only ever exposes a bar once its own CLOSE time
has passed relative to the moment being simulated - not just its open time,
which matters a lot for the higher timeframes (an H4 bar that opened 10
minutes ago hasn't closed yet and must stay invisible). This is covered by
dedicated tests in `selftest.py`, not just asserted in a comment.

### What the backtest found, immediately

An earlier version of this system had a real design flaw the backtest
surfaced: `tp_arm_dollars` was used as **both** the fixed take-profit
distance and the trail-arm threshold - same config value, same computed
price. The instant price reached the level that would arm the trail, it had
also just reached the fixed TP, and in live trading a standing broker TP
order fires the moment price touches it, essentially always winning that
race before the EA's own trailing logic gets a chance to act. Practically:
that design behaved close to a flat $6 take-profit; the trail almost never
got to engage.

**That's why `exit_style="sl_to_tp1"` is now the default** (see "Exit
design" above): no broker take-profit is placed at all, so there's nothing
for the broker to fill ahead of the EA moving the stop-loss - the race is
gone by construction, not by tuning. The original design is kept as
`exit_style="fixed_tp"`, purely so `backtest.py --compare` has something
concrete to measure the change against.

**Running `--compare --mechanical` against three synthetic M15 datasets**
(random-walk-with-drift, alternating trend/consolidation regimes, and a
deliberately strong/low-noise trending series - `--mechanical` votes on the
same three legs Claude is told to use, so treat this as an engine
comparison, not a performance claim about Claude's own judgment) gives both
styles the *identical* signal stream (one shared verdict per bar - see
`run_backtest_compare`'s docstring) so the only thing that can differ is
how each style manages an already-open position:

| Dataset | Trades | Win rate | Net P&L | Max drawdown (`sl_to_tp1` vs `fixed_tp`) |
|---|---|---|---|---|
| Random-walk-with-drift | 18 / 18 | 22.2% / 22.2% | -$40.46 / -$40.46 | $40.46 / $40.46 (identical) |
| Trend/consolidation regimes | 20 / 20 | 55.0% / 55.0% | -$1.06 / -$1.06 | $30.00 / $30.00 (identical) |
| Strong trend, low noise | 51 / 51 | 76.5% / 76.5% | $131.75 / $131.75 | **$25.66 / $30.00** |

On the first two datasets the trade sequence, win/loss counts and net P&L
came out **byte-for-byte identical** between the two styles - this noise/
drift ratio rarely lets price run meaningfully past `tp1_dollars` before
reversing, so the trail never gets a chance to capture anything a flat TP
wouldn't have. Only the third, deliberately strong-trending dataset showed
any divergence at all, and even there the trade sequence and net P&L were
identical - the only difference was a smoother equity curve (`sl_to_tp1`'s
max drawdown was about 15% lower). **Read this as "no worse, and measurably
better on a strongly-trending sample, never actually worse in three
synthetic tries" - not as a proven edge.** These are synthetic price series,
not real market history; the honest conclusion is that `sl_to_tp1` removes
a real, structural race condition (see above) with no observed downside,
which alone justifies it as the default even before it shows a P&L
difference on real data. Run `--compare` against real exported history (or
`--from-mt5`) before drawing any stronger conclusion.

### Honest limitations of the backtest itself

- **No `breakeven_r_decay` simulation.** `backtest.py` only simulates
  `sl_to_tp1` and `fixed_tp` - `breakeven_r_decay` is live-only, implemented
  in the MQL5 EAs (see "Exit design" above). Passing it to `backtest.py`
  raises rather than silently mis-simulating it as `sl_to_tp1`.
- **Bar-level, not tick-level.** Exit checks use each bar's high/low, not
  genuine intrabar sequencing - see `HistoricalGateway.manage_positions`'s
  docstring for the specific, documented ordering assumption (a trail that
  arms on a bar is only tested for a stop-out starting the bar after).
- **Synthetic spread**, not the real historical spread at each moment -
  `--from-mt5` still uses a configured constant, not point-in-time spread
  history (MT5 doesn't expose that for arbitrary past dates either).
- **No slippage modeling** - fills happen at exactly the next bar's open.
