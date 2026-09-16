# Backtesting the XTR pipeline

Short answer: **yes, it's possible, with one honest caveat** — a *real* backtest
(one that reflects what Claude would actually have decided) has to call the
real Claude API for every historical decision point, since that's the whole
point of this pipeline (see `CLAUDE_SIGNAL_PIPELINE.md`). There's no rule
table left to replay for free the way `simulate.py` replays the other,
mechanical EA. What this harness (`backtest_xtr.py`) gives you is everything
*around* that: real historical data, the exact production decision/
validation/sizing code (not a reimplementation), and historically-accurate
trade-outcome simulation — so a real run costs API calls, not engineering.

## What's actually in this repo right now

- **`backtest_xtr.py`** — the harness. Resamples M1 bars into M5/M15/H1,
  computes the same MT5-matching indicators the EA exports live (via
  `indicators.py`), builds a `ChartSnapshot` at each historical M5 close with
  no look-ahead, and calls `claude_signal_bot.py`'s own `analyze_with_claude`
  / `validate_decision` / `position_size` / `register_trade_history` /
  `update_standdown` functions directly — the same functions the live loop
  uses, imported, not copied. It then simulates each trade's outcome
  (SL/TP/trailing-stop touch or time-decay) by scanning forward through the
  M1 path.
- **`backtest_data/xauusd_1min_sample.csv`** — 2,500 real M1 XAU/USD bars
  (2026-09-14 23:12 through 2026-09-16 16:51 UTC), fetched live via the
  Twelve Data API, in the exact format the harness expects. Real market
  data, not synthetic — good for a first real run.
- **Verified, end to end**: `python backtest_xtr.py --selftest` passes
  (resampling, the no-look-ahead boundary logic, outcome simulation
  including the trailing stop, and the full replay loop wired to the real
  `bot.analyze_with_claude` with only the network call itself mocked). A
  `--stub` run against the real sample CSV above completed cleanly — 250
  cycles evaluated, 5 trades taken, a sensible equity curve. **Neither of
  those is a performance backtest** — see the next section.

## What I could not do in this session, and why

I did **not** run a real Claude-powered backtest, because doing so needs an
`ANTHROPIC_API_KEY` this session doesn't have. That's the one real blocker —
not the data (fetched above), not the engineering (built and tested above).

I also did not fabricate stand-in performance numbers. `--stub` mode exists
so the harness's *mechanics* could be verified without a key, but its
decisions come from a content-blind function that just takes whichever
mechanical hint fires (see `stub_decision()` in `backtest_xtr.py`) — it
applies no judgment, is not a strategy, and its win rate/P&L mean nothing
about how Claude would actually have traded this data. Every place it
appears (CLI output, the module docstring, the function's own docstring)
says so explicitly, specifically so a `--stub` result never gets mistaken
for a real one down the line.

## How to run a real backtest

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# sanity check on a handful of cycles first - cheap, fast, confirms the setup
python backtest_xtr.py --csv backtest_data/xauusd_1min_sample.csv --max-cycles 10 -v

# the full sample file (see the cost estimate below before running this)
python backtest_xtr.py --csv backtest_data/xauusd_1min_sample.csv --out trades.csv
```

Bring your own data for a longer or different window: any M1 OHLC CSV with a
header `time,open,high,low,close`, ascending chronological order, `time`
parseable by pandas. `python backtest_xtr.py --selftest` needs neither a key
nor a data file.

Useful flags: `--max-cycles N` (hard cap on API calls — the real cost
control), `--start-equity`, `--risk-percent`, `--min-confidence`,
`--min-atr-mult`/`--max-atr-mult`, `--max-concurrent-signals`,
`--time-decay-seconds`, `--trail-usd`, `--spread` (fixed synthetic spread in
price units), `--warmup-bars` (M5 bars skipped for indicator warm-up,
default 250), `--out` (write the trade log to CSV).

## Cost estimate (measured, not guessed)

I built a real snapshot from the sample data and measured the actual prompt
`build_analysis_prompt` produces: **~2,950 tokens per cycle** (~1,490 system
+ ~1,460 user; grows somewhat as trade history accumulates — recent-trades
context is capped at 8 entries). Output is capped at 700 tokens but a
typical decision runs well under that. At Claude Sonnet 5 pricing ($2.00 /
$10.00 per 1M input/output tokens):

| Scope | Cycles | Rough cost |
|---|---|---|
| Sanity check (`--max-cycles 10`) | 10 | ~$0.10 |
| The bundled 2-day sample, full run | ~250 | ~$2.50 |
| One month of M1 data | ~8,000 | ~$75-85 |

These are ballpark figures from a token/char estimate, not
`messages.count_tokens` — close enough to plan a run, not precise enough to
budget a large one exactly. **Always set `--max-cycles`** until you've seen
one real run's actual `usage` and cost.

## Simplifications this harness makes (disclosed, not hidden)

- **Fixed synthetic spread** (`--spread`, default $0.25), not the live
  historical spread — Twelve Data's free time series doesn't include it.
- **OHLC-only intrabar ordering.** A bar's high/low don't tell you which
  came first. `simulate_outcome()` resolves an ambiguous bar stop-first
  (conservative) and applies that bar's trailing-stop update before
  checking the stop against it — standard, slightly conservative choices
  for bar-level backtesting, not a claim about the true tick path. See the
  function's own docstring.
- **No weekend/holiday gap handling beyond what's in the data** — if your
  CSV has a gap, the harness just resamples across it; it doesn't know the
  market was closed.
- **The self-correction loop sees only this run's own history.** Realistic
  in spirit (it's exactly what a live run would build up over time from a
  cold start), but a backtest starting cold means the first several cycles
  get no performance history to react to — by design, not a bug.
- **Claude's real-time judgment isn't perfectly deterministic.** Two runs
  over the same data with the same model can produce different decisions.
  A backtest here is a sample of how Claude tends to decide on this data,
  not a single ground-truth answer.
