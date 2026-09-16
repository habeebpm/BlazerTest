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
    looks like)
  - a same-side-of-price check on SL/TP
  - the §10 two-loss standdown per setup type (Claude is told which types
    are blocked and asked to respect it; code enforces it regardless)
  - §9's 10-minute time-decay close
  - §8's position-sizing arithmetic, applied to *Claude's* stop distance

Nothing else is gated. There's no HTF "NO_TRADE" matrix silently vetoing a
setup, no rigid ATR-clamp formula overriding Claude's stop - those XTR
mechanics are now inputs to Claude's reasoning (still computed accurately in
Python/MQL5 and handed over as labeled hints), not code paths that decide.

```
MetaTrader 5 terminal                          Python process
------------------------                        ---------------
ClaudeSignalEA.mq5
  - every 60s: computes EMA9/21,          --->   claude_chart_data.txt
    RSI14, MACD-hist (M5/M15/H1),                        |
    ADX14/ATR14/Bollinger (M5 only)                       v
    + raw M1/M5/M15/H1 bars, from its            claude_signal_bot.py
    own indicator buffers                          - computes direction/regime
                                                       labels + setup hints
                                                       (advisory context only)
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
  - appends execution acks         --->   claude_trade_ack.txt
  - appends WIN/LOSS per setup     --->   claude_trade_outcomes.txt
    type when a position closes
    (fed back to Claude as recent
    performance context, and to
    the §10 standdown backstop)
```

Nothing here talks to the MetaTrader5 Python API - the two sides only share a
folder on disk, so the Python side needs no MetaTrader package, only network
access to Claude.

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
0.25-3.0).

## File formats

**Chart data** (`claude_chart_data.txt`, rewritten every export) - a header
line, then `##`-delimited sections:

```
#symbol=XAUUSD digits=2 point=0.01 tick_value=1.00 tick_size=0.01 volume_min=0.01 volume_max=50.00 volume_step=0.01 bid=2345.67 ask=2345.92 spread=25 equity=5000.00 exported=2026.09.16T12:00:00
##INDICATORS
tf,ema9,ema21,rsi14,macd_hist,adx14,atr14,bb_upper,bb_lower
M5,2345.10,2344.80,58.20,0.35,27.40,1.85,2347.00,2340.20
M15,2344.50,2343.90,55.10,0.20,NA,NA,NA,NA
H1,2340.00,2338.50,52.00,0.10,NA,NA,NA,NA
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
requires them on M5.

**Trade signal** (`claude_trade_signals.txt`, one line, overwritten each
cycle):

```
id,symbol,action,lot,sl,tp,timestamp,setup_type,reason
17,XAUUSD,BUY,0.02,2338.50,2352.30,2026-09-16T10:05:00Z,4.2,trend pullback with re-expanding MACD histogram and aligned HTF trend
```

`action` is `BUY`, `SELL`, `NONE` (no trade) or `CLOSE` (close every position
this EA holds - used both for an explicit close decision and, best-effort,
for §9 time-decay). `setup_type` is whatever Claude cited -
`4.1`/`4.2`/`4.3`/`discretionary` - carried into the position's comment
(`XTR#<id>#<setup_type>`) so the outcome log can attribute a WIN/LOSS back to
it. `reason` is Claude's own reasoning (truncated), not a template string.
`id` must increase on every write - the EA ignores anything at or below the
last id it processed. `sl`/`tp` are absolute prices; the EA rejects a signal
outright if they're on the wrong side of the current price or inside the
broker's minimum stop distance (on top of Python's own pre-write checks).

**Execution ack** (`claude_trade_ack.txt`, appended by the EA): one line per
processed signal - `timestamp,id,status,detail` where status is `EXECUTED`,
`REJECTED`, `SKIPPED`, `CLOSED` or `FAILED`.

**Trade outcomes** (`claude_trade_outcomes.txt`, appended by the EA when a
position it opened closes, for any reason - SL, TP, manual close, or a
time-decay `CLOSE`): `timestamp,signal_id,setup_type,direction,profit,outcome`
where outcome is `WIN` or `LOSS`. Python reads this every cycle both to
drive the §10 standdown backstop and to hand Claude its own recent
win/loss history as context for the next decision.

## What Claude decides vs. what code still enforces

| | Decided by |
|---|---|
| Direction (BUY/SELL/NONE) | Claude, every cycle, from live data |
| Setup rationale (4.1/4.2/4.3/discretionary) | Claude |
| Entry timing color, M1 sweep read | Claude |
| Stop-loss / take-profit prices | Claude |
| Confidence / conviction | Claude |
| Narrative reasoning (§13-style) | Claude |
| §2/§3 direction & regime *labels* shown to Claude | Python (`direction_for`, `regime_for`) - informational only |
| §4 setup *hints* shown to Claude | Python (`check_rsi_bounce`, `check_trend_pullback`, `check_liquidity_sweep`) - advisory only, not gates |
| §5 HTF grade shown to Claude | Python (`htf_conviction`) - advisory only |
| Side-of-price / stop-distance sanity check | Python, hard backstop on Claude's own numbers |
| §8 position sizing | Python, arithmetic from Claude's stop distance |
| §9 time-decay close | Python |
| §10 two-loss standdown | Python, hard backstop (Claude is also told about it) |
| §11 trade log / §13 report format | Python, populated with Claude's reasoning |
| §12 DXY correlation / macro-news feeds | Not wired up - exposed to Claude as explicit "not evaluated" flags rather than silently ignored |

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
- **DXY correlation and macro/news are not evaluated.** They are logged as
  explicit gaps in the overlay context rather than silently ignored; wiring
  up a real feed for either is a natural next step.
- **Time-decay and standdown persistence assume one long-running EA/bot
  pair.** An EA restart loses its in-memory position->setup-type mapping (a
  documented limitation in the EA's own comments); a Python bot restart
  reloads its state file, so standdown/pending tracking survives that side.
- **The signal file is trusted input to a live-trading EA.** Keep the shared
  folder private to processes you control; anything able to write to it can
  place trades through the EA's own validation only.
- Demo-test extensively before ever pointing this at a live account.
