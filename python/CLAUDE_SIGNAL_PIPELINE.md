# XTR pipeline (MQL5 <-> Python, file-based)

A separate, experimental automation path alongside the self-contained
`XAUUSD_Confluence_EA` / `trader.py` strategy documented in the main READMEs.
It implements the **XTR - XAUUSD Micro-Scalp Signal Logic Specification**: a
10-minute-horizon, mostly-mechanical signal system refined through live
forward-testing (trades 1-14). Section numbers below (§2, §7, ...) refer to
that spec.

```
MetaTrader 5 terminal                          Python process
------------------------                        ---------------
ClaudeSignalEA.mq5
  - every 60s: computes EMA9/21,          --->   claude_chart_data.txt
    RSI14, MACD-hist (M5/M15/H1),                        |
    ADX14/ATR14/Bollinger (M5 only)                       v
    + raw M1/M5/M15/H1 bars, from its            claude_signal_bot.py
    own indicator buffers                          §2  direction per TF
                                                     §3  regime (M5 ADX)
                                                     §4  setup detection
                                                     §5  HTF conviction filter
                                                     §7  SL/TP sizing
                                                     §8  position sizing
                                                     §9  time-decay tracking
                                                     §10 two-loss standdown
                                                     -> Claude for the §12
                                                        discretionary overlay
                                                        ONLY (never the
                                                        direction/SL/TP)
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
    (read back for §10)
```

Nothing here talks to the MetaTrader5 Python API - the two sides only share a
folder on disk, so the Python side needs no MetaTrader package, only network
access to Claude.

## Why MQL5 computes the indicators, not Python

Earlier drafts of this pipeline had Python recompute EMA/RSI/MACD/ADX from
the ~300 raw bars the EA exported. That is a bad idea here: the spec only
fetches 30 candles per timeframe, which is nowhere near enough history for
Python to reconstruct an accurate EMA/RSI/ADX from scratch (those need a long
warm-up to converge). So the division of labor is:

- **MQL5** computes every indicator (§1) from its own full price history via
  `iMA`/`iRSI`/`iMACD`/`iADX`/`iATR`/`iBands`, reads the value on the last
  **closed** bar (shift 1, so nothing repaints), and exports the numbers -
  not the raw series Python would need to rederive them.
- **Python** runs the entire rule engine (§2-§11) mechanically against those
  numbers, and separately asks Claude for the qualitative overlay (§12) and
  the narrative report (§13) - never for the direction, setup type, stop or
  target, all of which are already decided by code before Claude is called.
  NO_TRADE cycles skip the Claude call entirely.

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
- `InpMacdHistBarsM5` - how much M5 MACD-histogram history is exported for
  the §4.2 re-expansion gate (10 bars by default).
- `InpExportIntervalSec` - export cadence (60s, "every minute").
- `InpSignalPollSec` - how often the EA checks for a new signal.
- `InpTrailingUSD` / `InpTrailStartUSD` - the fixed-dollar trailing stop
  (defaults to $4, per position, whatever the instrument or lot size - it's
  converted to a price distance from the symbol's tick value/size and the
  position's own volume). The stop only ever tightens. This is separate
  from, and layered on top of, the §7 initial SL/TP - the spec's own
  "breakeven at ~1R" idea (§7) is not implemented; only the flat $4 trail is.
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

`--dry-run` still runs the full rule engine and (for a graded signal) still
calls Claude for the overlay note, but never writes the signal file - run
that first, the same way `trader.py` is meant to be run without `--live`
before it. Every decided cycle (a graded signal, not every NO_TRADE) is
appended to `logs/xtr_trades.csv` using the exact §11 schema.

Useful flags: `--interval` (seconds between cycles, match the EA's export
interval), `--model`, `--risk-percent` (§8, default 2.0), `--fallback-equity`
(used for sizing only if the EA's exported equity reads 0, e.g. in testing),
`--time-decay-seconds` (§9, default 600).

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
##BARS_M5
...
##BARS_M15
...
##BARS_H1
...
```

`adx14`/`atr14`/`bb_upper`/`bb_lower` are `NA` for M15/H1, since §1 only
requires them on M5.

**Trade signal** (`claude_trade_signals.txt`, one line, overwritten each
cycle):

```
id,symbol,action,lot,sl,tp,timestamp,setup_type,reason
17,XAUUSD,BUY,0.02,2338.50,2352.30,2026-09-16T10:05:00Z,4.2,trend pullback + MACD re-expansion
```

`action` is `BUY`, `SELL`, `NONE` (no trade) or `CLOSE` (close every position
this EA holds - used both for an explicit close decision and, best-effort,
for §9 time-decay). `setup_type` is `4.1`/`4.2`/`4.3`, carried into the
position's comment (`XTR#<id>#<setup_type>`) so the outcome log can
attribute a WIN/LOSS back to it. `id` must increase on every write - the EA
ignores anything at or below the last id it processed. `sl`/`tp` are
absolute prices; the EA rejects a signal outright if they're on the wrong
side of the current price or inside the broker's minimum stop distance.

**Execution ack** (`claude_trade_ack.txt`, appended by the EA): one line per
processed signal - `timestamp,id,status,detail` where status is `EXECUTED`,
`REJECTED`, `SKIPPED`, `CLOSED` or `FAILED`.

**Trade outcomes** (`claude_trade_outcomes.txt`, appended by the EA when a
position it opened closes, for any reason - SL, TP, manual close, or a
time-decay `CLOSE`): `timestamp,signal_id,setup_type,direction,profit,outcome`
where outcome is `WIN` or `LOSS`. Python reads this every cycle to drive the
§10 two-loss standdown per setup type.

## What's implemented mechanically vs. left to Claude

| Spec section | Implementation |
|---|---|
| §1 data/indicators | MQL5, from its own indicator buffers |
| §2 directional determination | Python, `direction_for()` |
| §3 regime | Python, `regime_for()` (M5 ADX >= 25) |
| §4.1 RSI-extreme bounce | Python, `check_rsi_bounce()` |
| §4.2 trend-continuation pullback + MACD gate | Python, `check_trend_pullback()` / `macd_reexpanding()` |
| §4.3 liquidity-sweep reversal | Python, `check_liquidity_sweep()` (M1 swing break + reclaim) |
| §5 HTF conviction filter | Python, `htf_conviction()` (exact table) |
| §6 M1 entry timing | Not separately implemented - the EA executes at market on receipt; the sweep detection in §4.3 is M1-based |
| §7 SL/TP | Python, `compute_stop_loss()` / `compute_take_profit()`; the "round number / prior swing / opposite BB band" TP nudge only tries a whole-dollar handle and the opposite BB band, not the prior-swing option |
| §7 breakeven-at-1R trail | Not implemented; the EA's own fixed $4 trail (unrelated to the spec) runs instead |
| §8 position sizing | Python, `position_size()`, from the EA's exported account equity |
| §9 time-decay | Python tracks each signal's issue time and force-closes after `--time-decay-seconds` - **best-effort**: the file protocol has no per-signal close selector, so an expiry closes every position the EA holds, not just the stale one |
| §10 two-loss standdown | Python, `update_standdown()`/`standdown_gate()`, per setup type, persisted in the bot's state file |
| §11 trade log | `logs/xtr_trades.csv`, exact column set |
| §12 discretionary overlay | Session-timing and round-number context computed in Python (`build_overlay_context()`); DXY correlation and macro/news are exposed as explicit "not evaluated" flags - no feed is wired up for either. Claude adds a one-line qualitative note and may flag `caution` (which downgrades FULL to REDUCED), called only on a graded signal, never on NO_TRADE |
| §13 output format | `format_report()`, printed/logged every cycle |
| §14 performance baseline | Documentation only - nothing in code claims or measures a win rate |

## Honest notes / risks

- **This is materially riskier than the indicator-only EA.** The mechanical
  rules are deterministic and testable (see `--selftest`), but no
  profitability claim is made - §14's own numbers are a small out-of-sample
  backtest and a 4W-6L live sample, not a proven edge.
- **DXY correlation and macro/news are not evaluated.** They are logged as
  explicit gaps in the overlay context rather than silently ignored; wiring
  up a real feed for either is a natural next step.
- **Time-decay and standdown persistence assume one long-running EA/bot
  pair.** An EA restart loses its in-memory position->setup-type mapping (a
  documented limitation in the EA's own comments); a Python bot restart
  reloads its state file, so standdown/pending tracking survives that side.
- **API cost and latency.** A cycle's rule engine runs every `--interval`
  seconds regardless of the market; only a graded (non-NO_TRADE) cycle calls
  Claude, which keeps cost down but still adds latency to that cycle.
- **The signal file is trusted input to a live-trading EA.** Keep the shared
  folder private to processes you control; anything able to write to it can
  place trades through the EA's own validation only.
- Demo-test extensively before ever pointing this at a live account.
