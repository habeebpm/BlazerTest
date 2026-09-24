# Backtest report - XAUUSD, 19 Sep 2025 - 23 Sep 2026 (one year)

> **Live trading hours changed (owner's choice):** new entries now run 06:00-23:00 Oman time, Monday-Friday, for Claude and Telegram alike. The report below tested the earlier New York hours (08:00-16:45 and 18:15-20:00); the test of the Oman window is in the section "Oman trading hours".

Real history from Twelve Data (XAU/USD 5-minute bars; M15 and H1 built from
them, H4/D1/W1 from the 4-hour series), converted to UTC, weekends and the
daily 17:00-18:00 New York break removed. 24,056 closed M15 bars evaluated,
no lookahead. Gold over the year: 3,658 -> 5,597 (end of January) -> 4,286 -
a big rally, a crash and a range: very different markets in one test.

**What this measures:** `backtest.py --mechanical` - the three-leg rules
Claude is told to apply, voted mechanically. It tests the rules, the exits,
the risk limits, the XTR gate and the entry tactics on real prices. **It is
not Claude's judgment** (Claude also reads SMC structure, levels, news and
the calendar).

Trading rules, unchanged: 2% risk per trade, 10% daily cap/budget, $6 SL /
$6 TP1 lock / $3 trail at the 0.01 reference lot, max 5 per direction,
starting equity $10,000, spread 25 points ($0.25), 80 points around the
daily reopen.

**R** = result per trade in multiples of the $6 stop (+1R = won what the
stop risks) - the fair comparison, because dollars compound with equity.
The year is split into three periods:

| Period | Role |
|---|---|
| **Sep 2025 - Mar 2026** | **Fresh.** Downloaded after every setting below was chosen - the honest test |
| Mar - Jul 2026 | Used to confirm the trading hours (previous report) |
| Aug - Sep 2026 | Where the trading-hours idea came from (first 7-week test) |

## Oman trading hours (live setting)

Same year, same data, same mechanical verdicts, only the entry hours
different (Claude's side - the Telegram side cannot be backtested: there is
no history of the channel's signals). 1R = the $6 stop.

| Entry hours | Trades / week | Win % | Avg R | Return | Max DD | Sep-Mar (fresh) | Mar-Jul | Aug-Sep | Chance of luck | Claude calls / week |
|---|---|---|---|---|---|---|---|---|---|---|
| **06:00-23:00 Oman, Mon-Fri** (live) | 8.3 | 40.5 | **-0.07** | **-58%** | **62%** | -0.07 | -0.04 | -0.16 | 86% | 155 (about $13) |
| 16:00-23:00 Oman, Mon-Fri (the positive part alone) | 2.9 | 41.7 | +0.07 | +8% | 27% | +0.11 | +0.11 | -0.20 | 30% | - |
| 08:00-16:45 + 18:15-20:00 New York (before) | 4.3 | 43.7 | +0.14 | +57% | 27% | +0.17 | +0.16 | +0.04 | 9% | 92 (about $8) |

At double spread: Oman -0.13R, -75%, drawdown 75%; New York +0.11R, +34%.

Where the Oman window's trades come from (entry hour, Oman time):

| Oman hours | Trades | Avg R |
|---|---|---|
| 06:00-12:00 (Tokyo / early London) | 192 | -0.14 |
| 12:00-16:00 (London) | 99 | -0.18 |
| 16:00-23:00 (New York morning) | 149 | +0.09 |

**Reading:** the extra hours are the Asian session and the London morning -
the same hours that lost in the original test. Negative in all three
periods, at normal and double spread. This is the mechanical stand-in, not
Claude's judgment (Claude is told to treat "Asian range chop" as a red
flag and may skip some of these), but the stand-in's New York hours were
the only setting positive in all three periods.

## Verdict

1. **Trading hours passed the fresh test.** On Sep-Mar, which played no part
   in choosing them, entries only 08:00-16:45 and 18:15-20:00 New York time
   improved the average trade under the gates that trade often: no gate
   -0.01R -> **+0.17R**, block_opposed +0.12R -> **+0.31R**. Across the whole
   year the hours roughly halved the drawdown under every gate and skip
   about half of the paid Claude calls.
2. **`require_alignment` failed the fresh test** (-0.64R on 11 trades) and is
   about zero over the year. It is no longer the default.
3. **New default: XTR gate off + trading hours** (the XTR reading stays in
   Claude's snapshot as context). Picked by a rule fixed before looking at
   the stress results: best total R per unit of drawdown at double spread,
   positive in at least 2 of 3 periods. It was positive in **all three**
   periods (+0.17 / +0.16 / +0.04R), made +57% on the year (+34% at double
   spread) and has the lowest chance of luck of any setting: **9%**.
4. **The price is bigger swings.** Worst drawdown 27% from the peak (32% at
   double spread) and about 4 Claude trades a week. `require_alignment`
   (`--xtr-gate require_alignment` in `start.bat`) swings about half as much
   (13%) but trades once a week and showed no edge.
5. **Still not proof.** 9% chance of luck is the best result so far, not a
   guarantee; May alone made half the year's R. Claude's own judgment is
   untested - the demo scorecard is the real test.

## Results (spread 25 points, 80 at the reopen)

| XTR gate | Entry tactics | Trades | Win % | Return | Max DD (from peak) | Profit factor | Avg R | Sep-Mar (fresh) | Mar-Jul | Aug-Sep | Chance of luck |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **off** *(default)* | **hours + guards** *(default)* | **229** | **43.7** | **+57.2%** | **26.9%** | **1.17** | **+0.14** | **+0.17 (79)** | **+0.16 (110)** | **+0.04 (40)** | **9%** |
| off | none | 589 | 41.6 | -22.6% | 56.1% | 0.96 | +0.02 | -0.01 (227) | +0.03 (259) | +0.03 (103) | 41% |
| block_opposed | hours + guards | 109 | 45.9 | +17.6% | 21.6% | 1.14 | +0.11 | +0.31 (37) | -0.04 (51) | +0.10 (21) | 24% |
| block_opposed | none | 300 | 43.0 | -19.6% | 45.2% | 0.93 | -0.01 | +0.12 (114) | -0.12 (126) | -0.03 (60) | 56% |
| require_alignment | hours + guards | 38 | 42.1 | -3.5% | 13.2% | 0.91 | -0.03 | -0.64 (11) | +0.29 (18) | +0.09 (9) | 57% |
| require_alignment | none | 113 | 42.5 | -3.3% | 24.9% | 0.97 | +0.01 | -0.15 (38) | +0.15 (58) | -0.10 (17) | 48% |

"Hours + guards" = trading hours, no new entry on Friday from 16:00 New
York, no entry while the spread is above 50 points. "Chance of luck" =
bootstrap chance that the true average trade is zero or negative.

## Stress test: double spread (50 points, 150 at the reopen)

| XTR gate | Entry tactics | Return | Max DD | Avg R | Sep-Mar | Mar-Jul | Aug-Sep | Total R / worst drawdown in R |
|---|---|---|---|---|---|---|---|---|
| **off** | **hours + guards** | **+33.8%** | **32.0%** | **+0.11** | **+0.13** | **+0.14** | **-0.03** | **24.2 / 19.1 = 1.27** |
| block_opposed | hours + guards | +5.6% | 26.0% | +0.05 | +0.26 | -0.06 | -0.04 | 5.7 / 14.4 = 0.40 |
| require_alignment | hours + guards | -4.2% | 13.3% | -0.04 | -0.65 | +0.28 | +0.08 | negative |
| off | none | -65.7% | 67.7% | -0.06 | -0.11 | -0.00 | -0.10 | negative |

Without the trading hours every gate loses at double spread.

## Tried and not switched on

| Idea | Result |
|---|---|
| ADX >= 25 (trend strength) on top of the hours | Better Mar-Jul, worse Aug-Sep (-0.11R, -0.27R at double spread) - off (`--min-adx 25` to try) |
| Other hour windows (start 07:00, evening from 19:00 or to 21:00, US only) | Worse in the six-month test |
| No Friday cutoff | More profit only from two +9R trades over one weekend gap - kept on |
| EMA extension, RSI level, D1/H4 bias, stacking, round numbers | No pattern that held in both periods |

## Checks on the backtest itself

- **Independent re-simulation:** every one of the 229 trades of the default
  setting was replayed by a separate, minimal exit simulator written from
  scratch - entry price, exit price, exit reason and exit time matched
  229/229. Every entry was inside the trading hours and before the Friday
  cutoff.
- The EA's live lock/trail logic (bid for buys, ask for sells, $6 lock, $3
  trail, fixed price distances) matches the simulator; the simulator trails
  one bar later, which is slightly conservative.
- Command-line reproduction of the default: `goldtrader.py backtest ...
  --mechanical` on the same CSVs gives the same result.

## Fixes found in this round

| Severity | Finding | Fix |
|---|---|---|
| High | Live: MT5 bar, tick and deal times are the broker's server clock (UTC+2/+3), used as if UTC - Claude's session labels were 2-3 h off, and `--from-mt5` backtests judged the trading hours on the wrong clock | Converted to real UTC in the gateway (New York + 7h broker clock by default, a live tick confirms or corrects it) |
| High | Live: deal history was requested up to "now UTC" - on a UTC+3 server the last ~3 hours of closed trades were missing (XTR stand-down, Claude's recent performance, digests) | The window now runs a day past now |
| Medium | The EA's 10% daily cap resets at server midnight, Python's at UTC midnight - two different days for one "shared" cap | Python uses the same trading day (server midnight, learned from ticks; 17:00 New York when unknown) - also in the backtest |
| Medium | Gate "off" was documented as "context for Claude" but gave Claude no XTR reading at all, while the prompt said opposed entries are refused | The reading is always sent with the active gate; the prompt tells Claude what is enforced |
| Medium | Claude stopping for good (no credits, bad key, retired model) was only logged | One Telegram alert per reason; a retired model name gets its own message |
| Low | Backtest drawdown % was measured against the starting equity (could exceed 100%) | Measured from the running peak |
| Low | Weekend filter of the test data used fixed UTC hours | Follows New York time (winter/summer) |

Earlier rounds (still in place): vendor filler bars in the daily break
removed, no fills while the market is closed, wider reopen spread, sell stops
on the ask, gap fills at the open, one spread per round trip, replay clock
for Claude's snapshot, session labels per time zone.

## What to watch on demo

The weekly Telegram scorecard (see `REFERENCE.md`) compares the demo with
this backtest. Expect roughly: 4 Claude trades a week, win rate low-to-mid
40s %, average around +0.1R to +0.2R, losing months in between. After 30+
trades its verdict (ON TRACK / NOT PROVEN YET / STOP AND REVIEW) is the one
to act on.

## Reproduce

In the GoldTrader folder (CSV paths in full):

```bat
python goldtrader.py backtest --bars-csv C:\data\m15.csv --trend-csv C:\data\h4.csv --daily-csv C:\data\d1.csv --weekly-csv C:\data\w1.csv --m5-csv C:\data\m5.csv --h1-csv C:\data\h1.csv --mechanical
```

That runs the live defaults. Without the tactics: add `--trade-hours any
--friday-cutoff off --max-spread 0`; another gate: `--xtr-gate
require_alignment`; stress: `--spread-points 50 --rollover-spread-points
150`. Straight from MT5: `--from-mt5 --start 2025-09-15 --end 2026-09-23`.
