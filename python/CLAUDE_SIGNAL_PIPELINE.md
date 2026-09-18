# XTR pipeline (MQL5 <-> Python, Claude does the real-time analysis)

A separate, experimental automation path alongside the self-contained
`XAUUSD_Confluence_EA` / `trader.py` strategy documented in the main READMEs.
It's built around the **XTR - XAUUSD Micro-Scalp Signal Logic Specification**,
a 10-minute-horizon scalp framework refined through live forward-testing
(trades 1-14). Section numbers below (§2, §7, ...) refer to that spec.

## The core design decision: Claude analyzes, code contains risk

An earlier version of this bot ran the XTR spec as deterministic Python
if/else logic end to end, with Claude bolted on only to write a cosmetic
note. That's backwards for a system whose entire premise is "send this to
Claude for analysis" - so this version inverts it:

- **Claude decides.** Every cycle, Claude gets the live multi-timeframe
  indicators and bars, the XTR setup archetypes and general professional
  trading principles as *knowledge* (system-prompt context, not a rule
  table it must satisfy), the current regime/HTF-alignment reads as
  *hints*, and which setup types are currently on standdown. From that it
  produces a direction, a setup rationale, a stop, a target, a confidence,
  and its reasoning - a real-time judgment call, not a lookup.
- **Code contains risk, it doesn't pick trades.** A single LLM call
  deciding live-money entries with zero guardrails is reckless regardless
  of how good the model is, so a few things stay mechanical - not because
  they're "the strategy," but because they're the kind of check any
  serious trading system keeps outside the model in the loop:
  - a sanity band on the proposed stop distance vs. ATR14(M5) (rejects a
    stop that's obviously mis-sized, without dictating what a *good* one
    looks like) - including outright rejecting the trade, not silently
    skipping the check, if ATR itself isn't available to check against
  - a same-side-of-price check on SL/TP
  - the §10 two-loss standdown per setup type (Claude is told which types
    are blocked and asked to respect it; code enforces it regardless)
  - a max-concurrent-signals cap (default 1 - XTR is a single 10-minute
    scalp by design, not a pyramiding system; Claude is shown how many
    signals are currently pending so it isn't surprised by the rejection)
  - §9's 10-minute time-decay close - targeted at the specific stale
    position via `CLOSE_ID`, not a blanket close of everything open
  - §8's position sizing - a fixed lot by default (`--fixed-lot`, 0.05
    unless you set it otherwise), or risk-based arithmetic applied to
    *Claude's* stop distance if you pass `--fixed-lot 0`
  - a daily circuit breaker (`--max-daily-loss-usd`/`--max-trades-per-day`,
    both disabled by default) - a blunt, whole-account halt for the rest of
    the UTC day, independent of setup type or standdown state, checked
    before spending an API call at all (see below)
  - a mechanical spread-widening gate (`--max-spread-mult`, disabled by
    default) - skips the cycle when the live spread is abnormally wide
    relative to its own recent rolling median, a cheap sign of a thin or
    illiquid moment worth sitting out

The constraints Claude is told about in its own system prompt (the ATR
band, the confidence floor, the concurrency cap) are interpolated from the
actual running config, not a hardcoded description - if you run with
`--min-atr-mult 0.5 --max-atr-mult 2.0`, Claude is told exactly that, not a
stale "0.25x-3x" left over from a different session's settings.

Nothing else is gated. There's no HTF "NO_TRADE" matrix silently vetoing a
setup, no rigid ATR-clamp formula overriding Claude's stop - those XTR
mechanics are now inputs to Claude's reasoning (still computed accurately in
Python/MQL5 and handed over as labeled hints), not code paths that decide.

## `TRADING_KNOWLEDGE` refinements from a backtest-informed review

`TRADING_KNOWLEDGE` (the system-prompt knowledge block in
`claude_signal_bot.py`) was revised to fold in a more detailed statement of
the XTR spec's own logic, surfaced by comparing this project's behavior
against a separate chat session that had been given the full spec. Three
concrete, code-verifiable changes went in:

- **4.3 (liquidity-sweep reversal) is no longer described as a standalone
  entry.** It previously said "a confluence booster for 4.1/4.2, or a
  smaller standalone entry" - now it's booster-only, matching the spec's
  stated intent that M1 is entry-timing refinement, never an independent
  signal on its own.
- **An explicit ATR sizing target (1.0-1.5x M5 ATR14)**, inside the
  existing enforced sanity band (`--min-atr-mult`/`--max-atr-mult`,
  0.25x-3.0x by default) rather than replacing it - the band stays the
  wide backstop it always was (reject anything wildly mis-sized), while
  the knowledge text now tells Claude what to actually aim for within it,
  not just what survives the check.
- **4.1 stated as the primary/higher-conviction setup, 4.2 as opportunistic**
  and warranting a lower confidence than an equally clean 4.1, rather than
  co-equal archetypes.

**What did NOT go in, and why:** the other chat also cited specific
win-rate figures (a ~56% out-of-sample win rate for 4.1, ~44.8% for 4.2,
over ~25 test trades). Those are deliberately left out of
`TRADING_KNOWLEDGE`. The only performance figure actually on record in this
repo is different - a "4W-6L live sample" (4 wins, 6 losses, ~40%) from the
original spec's own §14, which doesn't obviously reconcile with a
56%-over-25-trades claim - and the original spec's full text isn't
preserved anywhere in this repo to check against, so there was no way to
verify whether those specific percentages are the spec's own documented
backtest numbers or something stated without a checkable source. Embedding
an unverified performance statistic into a live-trading system's own
decision-making prompt is exactly the kind of thing this project has
consistently avoided elsewhere (see "Honest notes / risks" below and
`BACKTEST.md`) - the *directional* priority (4.1 over 4.2) made it in,
because it's already consistent with what was here before and doesn't
depend on an unconfirmed number to be true. If you have the original XTR
spec's actual §14 text, or a real backtest run's own measured win rates,
those can replace this hedge with the real figures.

## The self-correction feedback loop

A one-shot analyst that never sees its own track record just repeats its
mistakes. So every cycle, before Claude decides anything, `analyze_with_claude`
hands it `state["trade_history"]` - its own past decisions (setup type,
reasoning, stop/target, and which of the three mechanical setup hints
actually fired that cycle - `hints_fired`, independent of the setup type
Claude chose) paired with what actually happened once the EA's outcome log
resolved them (`resolve_pending` mutates the matching entry to `WIN`/`LOSS`
as soon as it sees the outcome) - plus a fuller performance picture from
`summarize_recent_performance`: overall win rate/profit factor/avg win-loss,
the same breakdown per setup type, and a confidence-calibration table
(actual win rate observed in each confidence bucket Claude itself reported,
so it can notice if a claimed 80% confidence has actually been winning
closer to 50%, not just whether the last trade won or lost). The system
prompt's SELF-CORRECTION section asks Claude to read all of that before
deciding: if a pattern shows up (a setup type losing repeatedly, a
reasoning style that keeps misreading the regime, entries right as the HTF
flips against them, a confidence bucket running well below its own claimed
rate), say so and adjust, rather than repeat it next cycle. Claude's own
answer to "what am I adjusting, if anything" comes back as a
`self_correction` field alongside the trade decision, and gets written into
the signal's reason and the trade log's notes so it's visible in the
record, not just implicit in the number that came out.

`hints_fired` (also written to the trade log CSV) is telemetry, not another
gate: it lets you separate, after the fact, three different patterns that
otherwise look identical from `setup_type` alone - Claude agreeing with a
mechanical hint that fired, trading `discretionary` with no hint firing at
all, or going against what the hints show. Each is worth reading differently
when deciding whether an archetype is carrying or dragging the account.

This sits above, not instead of, the mechanical two-loss standdown: the
standdown is a hard, guaranteed brake after exactly two losses; the
self-correction loop is Claude noticing a *qualitative* pattern earlier or
differently than a bare counter would ("both of the last two 4.2s entered
right as HTF flipped" is worth catching before a formal standdown triggers).
Claude is also explicitly told it isn't limited to the three named XTR
archetypes - a live setup that doesn't fit 4.1/4.2/4.3 can be described on
its own terms with `setup_type: "discretionary"`, rather than forced into a
label that doesn't fit or dropped to NONE out of caution alone. That's the
"generative" half of this design: real-time synthesis of what the current
data plus recent history actually support, not a fixed three-way classifier.

```
MetaTrader 5 terminal                          Python process
------------------------                        ---------------
ClaudeSignalEA.mq5
  - every 60s: computes EMA9/21,          --->   claude_chart_data.txt
    RSI14, MACD-hist (M5/M15/H1),                        |
    ADX14/ATR14/Bollinger (M5 only),                      v
    m5_dir/m15_dir/h1_dir + htf_align             claude_signal_bot.py
    (mechanical 2-of-3 pre-filter)                  - htf_align == NONE? skip -
    + raw M1/M5/M15/H1 bars, from its                 no Claude call, no cost
    own indicator buffers                           - else: computes direction/
                                                       regime labels + setup
                                                       hints (advisory context)
                                                     - builds a prompt with the
                                                       XTR knowledge + live data
                                                       + standdown/outcome state
                                                     - Claude decides action,
                                                       setup, sl, tp, confidence
                                                     - code validates: side of
                                                       price, ATR sanity band,
                                                       confidence floor,
                                                       standdown backstop
                                                     - position size computed
                                                       from Claude's stop
                                                          |
  - every 5s: reads a new signal          <---           v
    id and, if BUY/SELL, opens a                 claude_trade_signals.txt
    trade at the given SL/TP, tagged
    with its setup type
  - every tick: trails open
    positions by a fixed $4
  - appends execution acks         --->   claude_trade_ack.txt --> read every cycle;
  - appends WIN/LOSS per setup     --->   claude_trade_outcomes.txt   an EXECUTED ack fires
    type when a position closes             |                        a Telegram message
    (fed back to Claude as recent           v                        (fill notification)
    performance context, and to      Telegram Bot API
    the §10 standdown backstop)      (if TELEGRAM_BOT_TOKEN/
                                       TELEGRAM_CHAT_ID are set)
```

Nothing here talks to the MetaTrader5 Python API - the two sides only share a
folder on disk, so the Python side needs no MetaTrader package, only network
access to Claude (and, optionally, to Telegram's Bot API for fill
notifications - see "Telegram fill notifications" below).

## The mechanical HTF pre-filter (cost control)

Every earlier section describes Claude being called on every M5 cycle. That
was true until this pre-filter was added: `ClaudeSignalEA.mq5` now computes
`m5_dir`/`m15_dir`/`h1_dir` (BULLISH/BEARISH/MIXED - the same rule as
`direction_for()`) and `htf_align` (BUY/SELL/NONE - BUY/SELL when at least
2 of the 3 agree, NONE otherwise), and exports them in the header.
`run_once` reads `htf_align` immediately after confirming there's a new M5
bar and, when it's `NONE`, **skips the Claude call entirely** - no prompt
built, no tokens spent, before `analyze_with_claude` is ever reached.

**This reads the CURRENT bar on MT5, not the last closed one - on purpose,
and unlike everything else exported.** `DirectionLabel()` defaults to
`shift=0` (the live, still-forming bar) for exactly these three fields;
`WriteIndicatorRow()` (the `##INDICATORS` data Claude actually analyzes)
stays on `shift=1`, the last CLOSED bar, as it always has. That split is
deliberate: the gate only decides whether to spend money on *this* cycle,
and gets re-evaluated fresh next cycle regardless - reacting to a live
value that can still move before the bar closes costs nothing to get
"wrong" for one cycle. A real trading decision repainting mid-bar would be
a genuine problem; a cost pre-filter flickering for a few seconds isn't.

**Why the EA computes it, not Python** (even though `claude_signal_bot.py`
already has every value needed to compute this itself for free): a second,
independent Python implementation of the same rule is exactly the kind of
thing that silently drifts from the original over time - a threshold tweak
in one place and not the other, and the gate stops matching what a human
watching the EA's own logs would expect. Instead there's one authoritative
computation (`HtfAlignment()` in the EA, mirrored - on closed bars, see the
caveat below - by `htf_gate_from_directions()` in Python for the one place
with no live EA to ask: the backtest harness), and `run_once` just reads it.

**Measured cost impact - with an honest caveat.** Running the backtest
harness against the real 2-day XAUUSD sample in `backtest_data/`, gated
vs. `--no-htf-gate`, on identical data:

| | Cycles that would call Claude | Reduction |
|---|---|---|
| Gate on (default) | 137 | **45.2%** |
| Gate off | 250 | - |

That number is real, but it measures the **closed-bar** version of the
gate (`htf_gate_from_directions()` on `closed_bars_as_of()` data) - a
historical M1-bar dataset has no concept of "30 seconds into a still-
forming bar," so the backtest harness cannot reproduce the live EA's
`shift=0` reading; there is nothing to replay it against. The live gate,
reading the current bar, will skip and pass somewhat differently cycle to
cycle than this measurement - most likely similarly often (it's the same
rule one bar-width fresher), but 45.2% is evidence for "this kind of gate
meaningfully cuts calls," not a guaranteed live figure. Watch
`state["htf_gate_skips"]` on your own live runs for the number that
actually applies to you. At the ~$0.01/cycle estimate in `BACKTEST.md`,
this measured reduction is the difference between roughly $2.50 and $1.37
for this sample window - the saving scales with however much of your
session the gate spends skipping, live or backtested.

**The performance trade-off - read this before leaving the gate on
blindly.** The gate is directionally-blind to *why* a setup might fire:

- **4.2 (trend-continuation pullback)** wants the timeframes aligned by
  definition, so the gate rarely costs it anything real.
- **4.1 (RSI-extreme bounce)** is mean-reversion - it's allowed to fire
  in a *ranging* regime specifically because the higher timeframes often
  *aren't* cleanly trending either way. The gate doesn't veto this as
  often as you'd guess (chop tends to read MIXED rather than falsely
  aligned, and MIXED still counts toward "not NONE" as long as one other
  timeframe is clearly bearish/bullish alongside a trending M5) - but it
  can, and does, cut some.
- **4.3 (liquidity-sweep reversal)** is the one this gate is genuinely in
  tension with: a sweep is a *reversal against the recent move*, which is
  exactly when the higher timeframes are still reading the old direction.
  A sweep setup whose HTFs haven't flipped yet gets skipped before Claude
  ever sees it - not rejected by Claude's judgment, just never asked.

In the measured run above, all 5 trades the stub decider found still fired
even with the gate on - none were lost in this particular window. That's
reassuring but not a guarantee: it's one sample, one (non-Claude) decider,
and a window where the trades that happened to occur also happened to pass
the gate. Whether real Claude would find (and lose) more 4.1/4.3 setups
under the gate than this sample suggests is exactly what a real
`ANTHROPIC_API_KEY`-backed run, gated vs. `--no-htf-gate` over the same
window, would tell you - see `BACKTEST.md`.

**Bottom line:** leave the gate on if API cost is the binding constraint and
you're comfortable trading away some contrarian setups for it; pass
`--no-htf-gate` (Python) if you'd rather Claude see every cycle and pay for
it. Either way, `state["htf_gate_skips"]` (live) and
`report["htf_gate_skips"]`/`report["htf_gate_skip_rate_pct"]` (backtest) let
you see exactly how much the gate is actually doing on your own data, rather
than trusting the number above to generalize.

**`htf_strength`.** The EA also exports `htf_strength` (2 or 3 - how many of
the 3 timeframes actually agreed; 0 when `htf_align` is `NONE`) alongside
`htf_align`. It gates nothing further - the pass/fail decision is still the
same binary 2-of-3 check - it's handed to Claude as context so a bare
majority (2/3, one timeframe MIXED or opposed) reads differently from
unanimous agreement (3/3) in its own reasoning, rather than collapsing both
into the same "aligned" signal.

## Two more mechanical, opt-in risk controls

Both are disabled by default (0/unset) - existing behavior doesn't change
unless you turn them on - and both gate the Claude call itself, the same
place the HTF pre-filter does, so a tripped one costs nothing further in API
spend either.

**Daily circuit breaker** (`--max-daily-loss-usd`, `--max-trades-per-day`).
A blunt, whole-account halt for the rest of the UTC day once either limit
trips - unlike the §10 two-loss standdown (which is per setup type and
clears once M5 returns to MIXED), this doesn't care which setup type is
involved or why; it exists because nothing else in this system stops a bad
session from compounding across *different* setup types one small loss at a
time. `state["daily"]` (`trade_count`, `pnl`) is updated every cycle from
the EA's ack/outcome logs and resets automatically at UTC midnight;
`state["daily_breaker_skips"]` tracks how often it fired. Pick numbers that
reflect your actual risk tolerance for a single day, not the per-trade risk
% alone - `--risk-percent 2` with no daily cap still allows an unbounded
losing streak in one session.

**Mechanical spread-widening gate** (`--max-spread-mult`). The EA already
exports the live spread every cycle; `update_spread_history` keeps a
rolling window of the last 20 readings in state (there's no live EA to ask
for a "normal" spread the way there is for `htf_align`), and the gate skips
the cycle when the current spread exceeds `--max-spread-mult` times the
window's median - a cheap, broker-agnostic way to sit out a thin/illiquid
moment (rollover, a news spike) regardless of what the indicators say. It
needs at least 10 samples before it will ever trip (a cold history never
gates), and it's opt-in rather than on-by-default because "abnormal" is
broker- and session-specific - watch `state["spread_history"]` for a while
before picking a multiplier. `state["spread_gate_skips"]` tracks how often
it fired.

## News check (economic calendar awareness)

The XTR logic's output flow includes a "News check" step before the
signal itself. `ClaudeSignalEA.mq5` implements it using MT5's own built-in
Economic Calendar (`CalendarValueHistory`/`CalendarEventById`) - no
external feed or API key needed, but genuinely broker-dependent: the
calendar is a service MetaQuotes provides through the broker's server, and
some demo/ECN servers don't populate it at all.

Each export cycle (when `InpEnableNewsCheck` is on, the default), the EA
looks for the nearest upcoming and the most recent past
`CALENDAR_IMPORTANCE_HIGH` event for `InpNewsCurrency` (default `USD` -
gold is USD-quoted and dominated by USD macro data: NFP, CPI, FOMC, PPI,
and similar), within `InpNewsLookaheadMin`/`InpNewsLookbackMin` minutes
(both default 60). This is advisory context handed to Claude via
`macro_news_context()` and a new `TRADING_KNOWLEDGE` principle - it does
**not** gate anything in code, consistent with everything else in this
design ("Claude analyzes, code contains risk" above): the knowledge text
tells Claude to stand fully aside ahead of a HIGH-importance event due
within roughly 15-20 minutes, and to require unusually clean confluence
in the 15-20 minutes just after one fires, but this is Claude's judgment
call to make each cycle, not a code-level blackout window.

**Two honest caveats, not hidden:**

- **This is untested against a real calendar.** The Economic Calendar API
  is less commonly used than the indicator functions the rest of this EA
  relies on, and (like everything else in `ClaudeSignalEA.mq5`) it was
  written and reviewed without access to MetaEditor or a compiler in this
  session - the function signatures were verified against MQL5's own
  documentation before writing this, which is more than the rest of the
  EA's review got, but "verified against docs" is still not "compiled and
  run against a live calendar." Test this specifically - e.g. watch an
  export around a known NFP/CPI release time on a demo account - before
  trusting it.
- **"No event found" and "calendar unavailable" are indistinguishable.**
  `CalendarValueHistory` returns the same empty result whether there's
  genuinely no HIGH-importance USD event in the window or the broker's
  server just doesn't populate the calendar at all. The EA has no way to
  tell those apart without a much wider sanity query, so it doesn't try -
  `news_next_min`/`news_recent_min` export `-1` either way, and
  `macro_news_context()` reports a single honest "not evaluated" string
  rather than guessing which case it is. If you want to know which one
  you're actually getting, check the terminal's own Toolbox → Calendar tab
  on the same account.

The backtest harness (`backtest_xtr.py`) has no historical calendar data
to replay, so this is always the "not evaluated" case there - see
BACKTEST.md.

## Why MQL5 still computes the indicators, not Python

The spec only fetches 30 candles per timeframe - nowhere near enough history
for Python to accurately reconstruct an EMA/RSI/ADX from scratch (those need
a long warm-up to converge). So MQL5 computes every indicator (§1) from its
own full price history via `iMA`/`iRSI`/`iMACD`/`iADX`/`iATR`/`iBands`, reads
the value on the last **closed** bar (shift 1, so nothing repaints), and
exports the numbers - accurate inputs for Claude's analysis, not a short
series it would have to rederive them from.

## Setup

**1. Both files must live in a folder both processes can reach.** The EA
writes with `FILE_COMMON`, so by default that's the MT5 terminal's shared
folder, not this terminal's own sandboxed `MQL5/Files`:

- Windows: `%APPDATA%\MetaQuotes\Terminal\Common\Files`
- Wine bottle running MT5 for Mac: the equivalent path inside the bottle's
  `drive_c/users/<user>/AppData/Roaming/MetaQuotes/Terminal/Common/Files`

Pass that folder to the Python side with `--data-dir`. (Set
`InpUseCommonFolder = false` on the EA and point `--data-dir` at the
terminal's own `MQL5/Files` instead if you'd rather not use the shared
folder - only useful when the Python process runs on the very same machine
under the same user as that one terminal.)

**2. Install the EA.** Copy `MQL5/Experts/ClaudeSignalEA.mq5` into your
terminal's `MQL5/Experts/` folder (File -> Open Data Folder), compile it in
MetaEditor, and attach it to a chart of the symbol you want traded (the spec
is written for XAUUSD, but nothing hardcodes the symbol). Key inputs:

- `InpBarsM1/M5/M15/H1` - bars exported per timeframe (30 each, per §1).
- `InpMacdHistBarsM5` - M5 MACD-histogram history exported as extra context
  for Claude's momentum read (10 bars by default).
- `InpExportIntervalSec` - export cadence (60s, "every minute").
- `InpSignalPollSec` - how often the EA checks for a new signal.
- `InpTrailingUSD` / `InpTrailStartUSD` - the fixed-dollar trailing stop
  (defaults to $4, per position, whatever the instrument or lot size - it's
  converted to a price distance from the symbol's tick value/size and the
  position's own volume). The stop only ever tightens. This is separate
  from, and layered on top of, Claude's initial SL/TP.
- `InpMaxOpenPositions` - a hard ceiling so a malfunctioning analysis loop
  can't compound risk indefinitely.

**3. Run the Python bot.**

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

python claude_signal_bot.py --selftest                 # offline checks, no key/MT5 needed
python claude_signal_bot.py --data-dir "<common files path>" --once --dry-run -v
python claude_signal_bot.py --data-dir "<common files path>"          # live loop
```

An API key is required even for `--dry-run` now - Claude *is* the analysis,
not an optional add-on, so there's no meaningful "mechanical fallback" mode.
`--dry-run` still calls Claude and runs full validation, but never writes the
signal file; run it first, the same way `trader.py` is meant to be run
without `--live` before it. Every decided cycle (a graded signal, not every
NO_TRADE) is appended to `logs/xtr_trades.csv` using the §11 schema, with
Claude's own reasoning in the `notes` column.

Useful flags: `--interval` (seconds between cycles, match the EA's export
interval), `--model`, `--risk-percent` (§8, default 2.0), `--fallback-equity`
(used for sizing only if the EA's exported equity reads 0, e.g. in testing),
`--time-decay-seconds` (§9, default 600), `--min-confidence` (reject
Claude's own stated confidence below this, default 60), `--min-atr-mult` /
`--max-atr-mult` (the sanity band on stop distance vs. ATR14(M5), default
0.25-3.0), `--max-concurrent-signals` (pending signals allowed at once,
default 1 - raise it deliberately if you want this system to run more than
one XTR scalp concurrently, which is not how the spec is written),
`--no-htf-gate` (disable the mechanical 2-of-3 HTF pre-filter - see below;
on by default).

## Telegram fill notifications (optional)

A message is sent the moment the EA's ack log reports a signal `EXECUTED` -
that's a real fill, since the EA only ever trades at market, never queues a
pending order. Nothing else notifies (not `NONE`, not rejections, not
outcomes) - just the one event the task asked for. Uses Telegram's Bot API
directly over HTTPS via the standard library (`urllib`), so no extra
dependency and nothing added to `requirements.txt`.

**Setup:**

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`,
   follow the prompts. You get a bot token that looks like
   `123456789:AAG...`.
2. Start a chat with your new bot (search its username, hit Start) - a bot
   can't message you until you've messaged it first.
3. Get your chat id: message [@userinfobot](https://t.me/userinfobot) (or
   call `https://api.telegram.org/bot<token>/getUpdates` after step 2 and
   read `message.chat.id` from the JSON).
4. Set both as environment variables (or pass them on the command line):

```bash
export TELEGRAM_BOT_TOKEN=123456789:AAG...
export TELEGRAM_CHAT_ID=987654321

python claude_signal_bot.py --data-dir "<common files path>" --notify-test
```

`--notify-test` sends one message and exits - confirm it arrives before
trusting the live loop to notify you. Leave both unset and notifications
are silently skipped; nothing else about the pipeline depends on them.

A filled-signal message looks like:

```
XAUUSD signal FILLED (id 17)
BUY - setup 4.2 - confidence 78
Entry 2338.42  SL 2335.10  TP 2344.60
Reasoning: trend pullback with re-expanding MACD histogram, HTF aligned
Broker: ticket=90210394 price=2338.44 lots=0.05 setup=4.2
Time: 2026.09.16 10:05:12
```

The "Entry/SL/TP/Reasoning" lines come from this bot's own record of the
decision (`state["trade_history"]`, keyed by signal id); "Broker" is the
EA's own ack detail - the actual fill price/lot/ticket, straight from
MetaTrader. If the bot's state was reset since the signal was written (a
restart, a cleared state file), the message falls back to just the broker
line rather than failing.

## File formats

**Chart data** (`claude_chart_data.txt`, rewritten every export) - a header
line, then `##`-delimited sections:

```
#symbol=XAUUSD digits=2 point=0.01 tick_value=1.00 tick_size=0.01 volume_min=0.01 volume_max=50.00 volume_step=0.01 bid=2345.67 ask=2345.92 spread=25 equity=5000.00 m5_dir=BULLISH m15_dir=BULLISH h1_dir=MIXED htf_align=BUY htf_strength=2 news_next_min=23 news_recent_min=-1 exported=2026.09.16T12:00:00
##INDICATORS
tf,ema9,ema21,rsi14,macd_hist,adx14,atr14,bb_upper,bb_lower
M5,2345.10,2344.80,58.20,0.35,27.40,1.85,2347.00,2340.20
M15,2344.50,2343.90,55.10,0.20,NA,NA,NA,NA
H1,2340.00,2338.50,52.00,0.10,NA,NA,NA,NA
##NEWS
when,minutes,currency,name
NEXT,23,USD,Non-Farm Payrolls
RECENT,-1,USD,NA
##MACD_HIST_M5
value
0.10
0.15
...
##BARS_M1
time,open,high,low,close
2026.09.16 10:31,2340.12,2341.00,2339.50,2340.80
...
##BARS_M5 / ##BARS_M15 / ##BARS_H1
...
```

`adx14`/`atr14`/`bb_upper`/`bb_lower` are `NA` for M15/H1, since §1 only
requires them on M5. The header's `m5_dir`/`m15_dir`/`h1_dir`/`htf_align`/
`htf_strength` read the **current, still-forming bar** on each timeframe
(the mechanical cost pre-filter, see above) - everything else in this file,
including the `##INDICATORS` values just below, is the last **CLOSED** bar.
The two can legitimately disagree (e.g. `m5_dir=BULLISH` here while
`##INDICATORS` still shows the prior, not-yet-updated M5 close) - that's
expected, not a bug. `htf_strength` is 2 or 3 (how many of the 3 timeframes
agreed) and 0 whenever `htf_align=NONE`.

`news_next_min`/`news_recent_min` (-1 = none) and `##NEWS` are the "News
check" step (see below) - `-1`/`NA` means either "checked, nothing found
in the lookahead/lookback window" or "the calendar isn't populated on this
broker's server at all"; the EA can't tell those two apart (see next
section), so this file doesn't claim to either.

**Trade signal** (`claude_trade_signals.txt`, one line, overwritten each
cycle):

```
id,symbol,action,lot,sl,tp,timestamp,setup_type,reason
17,XAUUSD,BUY,0.02,2338.50,2352.30,2026-09-16T10:05:00Z,4.2,trend pullback with re-expanding MACD histogram and aligned HTF trend
```

`action` is `BUY`, `SELL`, `NONE` (no trade), `CLOSE`/`CLOSE_ALL` (close every
position this EA holds - available for an explicit full flatten, not used by
this bot itself) or `CLOSE_ID` (close only the one position opened for a
specific earlier signal - what §9 time-decay actually issues; the `lot`
column is reused to carry that original signal's id, since the position is
looked up by it rather than by lot size for this action). `setup_type` is
whatever Claude cited - `4.1`/`4.2`/`4.3`/`discretionary` - carried into the
position's comment (`XTR#<id>#<setup_type>`) so the outcome log can
attribute a WIN/LOSS back to it. `reason` is Claude's own reasoning
(truncated), not a template string. `id` must increase on every write - the
EA ignores anything at or below the last id it processed. `sl`/`tp` are
absolute prices; the EA rejects a signal outright if they're on the wrong
side of the current price or inside the broker's minimum stop distance (on
top of Python's own pre-write checks).

**Execution ack** (`claude_trade_ack.txt`, appended by the EA): one line per
processed signal - `timestamp,id,status,detail` where status is `EXECUTED`,
`REJECTED`, `SKIPPED`, `CLOSED` or `FAILED`.

**Trade outcomes** (`claude_trade_outcomes.txt`, appended by the EA when a
position it opened closes, for any reason - SL, TP, manual close, or a
time-decay `CLOSE_ID`): `timestamp,signal_id,setup_type,direction,profit,outcome`
where outcome is `WIN` or `LOSS`. Python reads this every cycle both to
drive the §10 standdown backstop and to resolve the matching entry in
`state["trade_history"]` - the record that powers the self-correction loop
above - from `PENDING` to `WIN`/`LOSS`.

## What Claude decides vs. what code still enforces

| | Decided by |
|---|---|
| Whether Claude is even called this cycle | MQL5 (`htf_align`, mechanical 2-of-3 pre-filter), hard gate, `--no-htf-gate` to disable; plus the opt-in daily circuit breaker and spread gate (both disabled by default) - see above |
| Direction (BUY/SELL/NONE) | Claude, on every cycle the gate lets through, from live data |
| Setup rationale (4.1/4.2/4.3/discretionary) | Claude |
| Entry timing color, M1 sweep read | Claude |
| Stop-loss / take-profit prices | Claude |
| Confidence / conviction | Claude |
| Narrative reasoning (§13-style) | Claude |
| Self-correction note (what it's adjusting based on its own track record) | Claude, informed by `state["trade_history"]` (Python maintains the record, Claude interprets it) |
| §2/§3 direction & regime *labels* shown to Claude | Python (`direction_for`, `regime_for`) - informational only |
| §4 setup *hints* shown to Claude | Python (`check_rsi_bounce`, `check_trend_pullback`, `check_liquidity_sweep`) - advisory only, not gates |
| §5 HTF grade shown to Claude | Python (`htf_conviction`) - advisory only |
| Side-of-price / stop-distance sanity check | Python, hard backstop on Claude's own numbers (fails safe - rejects the trade if ATR itself isn't available to check against) |
| §8 position sizing | A fixed lot (`--fixed-lot`, defaults to 0.05, always used regardless of stop distance) - or Python's risk-based arithmetic from Claude's stop distance if you pass `--fixed-lot 0`; either way, still clamped to the broker's min/max/step |
| §9 time-decay close | Python, targeted at the specific stale position (`CLOSE_ID`), not everything open |
| §10 two-loss standdown | Python, hard backstop (Claude is also told about it) |
| Max concurrent signals | Python, hard backstop, default 1 (Claude is shown current pending count and told not to stack) |
| Daily loss/trade-count circuit breaker | Python, hard backstop, opt-in (`--max-daily-loss-usd`/`--max-trades-per-day`, both 0/disabled by default) |
| Mechanical spread-widening gate | Python, hard backstop, opt-in (`--max-spread-mult`, 0/disabled by default) |
| §11 trade log / §13 report format | Python, populated with Claude's reasoning + which mechanical hints actually fired (`hints_fired`) |
| §12 macro-news check | MQL5's built-in Economic Calendar (`InpEnableNewsCheck`, on by default) - advisory context, not a gate; see "News check" above |
| §12 DXY correlation | Not wired up - exposed to Claude as an explicit "not evaluated" flag rather than silently ignored |

## Honest notes / risks

- **This is a genuinely different risk profile than a rule-based bot.**
  Claude's read can vary cycle to cycle even on similar data, which is the
  point (real judgment, not a lookup table) but also means behavior is
  less perfectly reproducible than the old deterministic version. The
  backstop checks bound the *damage* of a bad call (mis-sized stop, wrong
  side of price, ignoring a standdown); they do not bound whether the
  *decision itself* was a good trade.
  Nothing runs the strategy through a real backtest; §14's own numbers are
  a small out-of-sample sample and a 4W-6L live sample, not a proven edge.
- **API cost and latency are now on every cycle**, not just graded signals -
  Claude is doing the actual read every time there's a new M5 bar, whether
  or not it ends up trading.
- **DXY correlation is still not evaluated** - logged as an explicit gap in
  the overlay context rather than silently ignored; wiring up a real feed
  is a natural next step. Macro/news IS now evaluated via MT5's built-in
  Economic Calendar (see "News check" above), but only as advisory context
  - untested against a real calendar in this session, and unable to tell
  "no event" apart from "calendar unavailable on this broker."
- **Time-decay and standdown persistence assume one long-running EA/bot
  pair.** An EA restart loses its in-memory signal-id -> position mapping (a
  documented limitation in the EA's own comments) - a `CLOSE_ID` for a
  position opened before that restart won't find a match and is logged as
  `SKIPPED` rather than closing anything by mistake; the position's own SL/TP
  still protects it, it just won't be time-decay-closed. A Python bot restart
  reloads its state file, so standdown/pending tracking survives that side.
- **The signal file is trusted input to a live-trading EA.** Keep the shared
  folder private to processes you control; anything able to write to it can
  place trades through the EA's own validation only.
- **The Telegram bot token is a credential.** Anyone who has it can send
  messages as your bot (though not read your account or place trades - the
  bot only ever calls `sendMessage`). Keep it in an environment variable,
  not in a committed file, the same as `ANTHROPIC_API_KEY`.
- Demo-test extensively before ever pointing this at a live account.

## Backtesting

See `BACKTEST.md` - a real backtest replays historical bars through this
pipeline's actual decision code (`backtest_xtr.py`) and needs an
`ANTHROPIC_API_KEY`, since Claude's real-time judgment is the thing being
tested and there's no deterministic rule table left to replay for free.
