# Claude signal pipeline (MQL5 <-> Python, file-based)

A separate, experimental automation path alongside the self-contained
`XAUUSD_Confluence_EA` / `trader.py` strategy documented in the main READMEs.
Instead of an indicator-only decision, this pipeline hands live chart data to
Claude for analysis and trades whatever it decides, through two plain-text
files:

```
MetaTrader 5 terminal                          Python process
------------------------                        ---------------
ClaudeSignalEA.mq5
  - every 60s: exports the last          --->   claude_chart_data.txt
    300 M1 bars + bid/ask/spread                        |
                                                          v
                                                claude_signal_bot.py
                                                  - parses bars
                                                  - computes a scenario
                                                    summary (trend/momentum/
                                                    strength/volatility/
                                                    extended-range read)
                                                  - sends both to Claude
                                                  - validates the reply
                                                          |
  - every 5s: reads a new signal          <---           v
    id and, if BUY/SELL, opens a                 claude_trade_signals.txt
    trade at the given SL/TP
  - every tick: trails open
    positions by a fixed $4
  - appends the outcome            --->   claude_trade_ack.txt
```

Nothing here talks to the MetaTrader5 Python API - the two sides only share a
folder on disk, which is also why it works on any OS the way the EA does
(the Python side needs no MetaTrader package, only network access to Claude).

## Why "XTR"

The request that produced this pipeline asked for analysis "based on XTR and
other advanced scenarios." XTR isn't a standard technical-analysis term, so
`claude_signal_bot.py` implements it as an **eXtended Trend/Range** read -
whether price is breaking out of, or sitting inside, its recent 20-bar range
(`range_scenario` in `build_scenario_summary()`) - combined with the usual
trend (EMA20/50/200), momentum (RSI/MACD), trend-strength (ADX/DMI) and
volatility (ATR, Bollinger width) confluences. All of that is computed in
Python and handed to Claude as structured context, not left for the model to
infer from raw candles alone. If a more specific meaning was intended, adjust
`build_scenario_summary()`.

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
MetaEditor, and attach it to a chart of the symbol you want traded. Key
inputs:

- `InpExportFileName` / `InpSignalFileName` / `InpAckFileName` - file names
  (not paths; the folder is chosen by `InpUseCommonFolder`).
- `InpExportTF` / `InpBarsToExport` / `InpExportIntervalSec` - what gets
  exported and how often ("every minute" is the 60s default).
- `InpSignalPollSec` - how often the EA checks for a new signal.
- `InpTrailingUSD` / `InpTrailStartUSD` - the fixed-dollar trailing stop
  (defaults to $4, per position, whatever the instrument or lot size - it's
  converted to a price distance from the symbol's tick value/size and the
  position's own volume). The stop only ever tightens.
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

`--dry-run` still calls Claude and logs the decision it would have written,
but never touches the signal file - run that first, the same way `trader.py`
is meant to be run without `--live` before it. Every cycle (analyzed or
rejected) is appended to `logs/claude_signals.csv`.

Useful flags: `--interval` (seconds between cycles, match the EA's export
interval), `--model`, `--min-confidence` (0-100, default 60), `--lot` (lot
size written into each signal), `--max-sl-distance` (reject any stop whose
distance from price, in price units, exceeds this - there is no default, so
set one appropriate to the symbol), `--bars-in-prompt` (how many recent bars
go to Claude, default 120).

## File formats

**Chart data** (`claude_chart_data.txt`, rewritten every export):

```
#symbol=XAUUSD digits=2 point=0.01 tick_value=1.00 tick_size=0.01 bid=2345.67 ask=2345.92 spread=25 timeframe=PERIOD_M1 exported=2026.09.16T12:00:00
time,open,high,low,close,tick_volume,spread
2026.09.16 10:00,2340.12,2341.00,2339.50,2340.80,120,20
...
```

**Trade signal** (`claude_trade_signals.txt`, one line, overwritten each
cycle):

```
id,symbol,action,lot,sl,tp,timestamp,reason
17,XAUUSD,BUY,0.01,2338.50,2352.30,2026-09-16T10:05:00Z,trend+breakout confluence
```

`action` is `BUY`, `SELL`, `NONE` (no trade) or `CLOSE` (close every position
this EA holds). `id` must increase on every write - the EA ignores anything
at or below the last id it processed, so it never double-executes a signal
and a corrupted/duplicate write is a no-op rather than a re-trade. `sl`/`tp`
are absolute prices; the EA rejects a signal outright if they're on the wrong
side of the current price or inside the broker's minimum stop distance.

**Execution ack** (`claude_trade_ack.txt`, appended by the EA): one line per
processed signal - `timestamp,id,status,detail` where status is `EXECUTED`,
`REJECTED`, `SKIPPED`, `CLOSED` or `FAILED`.

## Honest notes / risks

- **This is materially riskier than the indicator-only EA.** An LLM can
  misread a chart, hallucinate a price, or simply be wrong; `validate_signal`
  and the EA's own checks catch malformed or out-of-bounds output, not bad
  trading decisions. No win rate or edge is claimed or known.
- **No profitability claim is made.** Demo-test this extensively before
  ever pointing it at a live account, exactly as the main EA/bot recommend.
- **API cost and latency.** A cycle runs (and is billed) every `--interval`
  seconds regardless of whether the market is moving, and Claude's reply adds
  latency the file-poll loop doesn't otherwise have.
- **The signal file is trusted input to a live-trading EA.** Keep the shared
  folder private to processes you control; anything able to write to it can
  place trades through the EA's own validation only.
