# Backtest report - XAUUSD, 20 Mar - 23 Sep 2026 (six months)

Real history from Twelve Data (XAU/USD, 5-minute bars; M15 and H1 built
from them, H4/D1/W1 from the 4-hour series), converted to UTC. 12,269
closed M15 bars evaluated, no lookahead. Gold over the window:
4,645 -> 4,888 (April) -> 3,949 (end of June) -> 4,286, i.e. a rally, a
19% fall and a rebound - three different markets in one test.

**What this measures:** `backtest.py --mechanical` - the three-leg rules
Claude is told to apply, voted mechanically. It tests the rules, the exits,
the risk limits, the XTR gate and the entry tactics on real prices. **It is
not Claude's judgment** (Claude also reads SMC structure, levels, news and
the calendar).

Trading rules, unchanged: 2% risk per trade, 10% daily cap/budget, $6 SL /
$6 TP1 lock / $3 trail at the 0.01 reference lot, max 5 per direction,
starting equity $10,000, spread 25 points ($0.25) and 80 points around the
daily reopen.

**R** below = result per trade in multiples of the $6 stop (+1R = won as
much as the stop risks). It is the fair way to compare - the dollar return
compounds with equity and swings with a few large trades. Each variant is
also split into two periods: **Mar-Jul** and **Aug-Sep**.

## Verdict

1. **Trading hours are the tactic that works.** Entries only 08:00-16:45 and
   18:15-20:00 New York time (skipping the Asian session and the London
   morning) improved the average trade in **both** periods, under **every**
   XTR setting, also at double spread, and cut the worst drawdown by half or
   more. It also skips about half of the paid Claude calls. **On by
   default.**
2. The idea came from the earlier 7-week test (Aug-Sep: Asian and London
   entries lost). The Mar-Jul data was not used to form it and confirms it -
   that is the out-of-sample check.
3. **`require_alignment` + trading hours** (the new default) had the lowest
   risk: 25 trades in six months, +7.7%, max drawdown 9.5% (26% without the
   hours), positive in both periods, and still positive at double spread.
   It trades rarely - about one trade a week.
4. **No variant has a proven edge.** Even the best has a 29-31% chance that
   its true average trade is zero or negative (bootstrap). Treat these as
   risk-reduction results, not profit forecasts.
5. **Trend-strength filter (ADX >= 25) did not hold up.** Big gains in
   Mar-Jul, but on top of the trading hours it made Aug-Sep worse under
   every gate. Available (`--min-adx 25`), **off by default**.

## Results (spread 25 points, 80 at the reopen)

| XTR gate | Entry tactics | Trades | Win % | Return | Max DD | Profit factor | Avg R Mar-Jul (n) | Avg R Aug-Sep (n) |
|---|---|---|---|---|---|---|---|---|
| **require_alignment** *(default)* | none | 72 | 44.4 | +9.1% | 26.2% | 1.11 | +0.15 (55) | -0.10 (17) |
| **require_alignment** *(default)* | **hours + guards** *(default)* | **25** | **48.0** | **+7.7%** | **9.5%** | **1.30** | **+0.21 (16)** | **+0.09 (9)** |
| off | none | 330 | 42.4 | -2.6% | 103.7% | 0.99 | +0.06 (231) | +0.00 (99) |
| off | hours + guards | 133 | 47.4 | +25.5% | 39.6% | 1.15 | +0.17 (93) | +0.04 (40) |
| block_opposed | none | 173 | 41.6 | -28.3% | 52.2% | 0.83 | -0.07 (116) | -0.07 (57) |
| block_opposed | hours + guards | 66 | 47.0 | +1.4% | 23.8% | 1.02 | +0.00 (45) | +0.10 (21) |

"Hours + guards" = the defaults: trading hours, no new entry on Friday from
16:00 New York, no entry while the spread is above 50 points. Max DD is the
worst peak-to-trough fall of the equity curve in % of the starting $10,000
(it can pass 100% when profits made earlier are lost again).

## Stress test: double spread (50 points, 150 at the reopen)

| XTR gate | Entry tactics | Trades | Return | Max DD | Avg R Mar-Jul | Avg R Aug-Sep |
|---|---|---|---|---|---|---|
| require_alignment | none | 72 | +10.5% | 29.3% | +0.17 | -0.12 |
| require_alignment | hours + guards | 25 | +6.8% | 9.6% | +0.20 | +0.08 |
| off | none | 318 | -28.1% | 100.4% | +0.04 | -0.13 |
| off | hours + guards | 131 | +13.4% | 47.6% | +0.15 | -0.03 |
| block_opposed | none | 169 | -38.2% | 57.5% | -0.09 | -0.18 |
| block_opposed | hours + guards | 65 | -6.0% | 28.6% | -0.02 | -0.04 |

The hours still improve every gate. `require_alignment` + hours is the only
setting positive in both periods at double spread.

## Tried and not switched on

| Idea | Result |
|---|---|
| ADX >= 25 (trend strength) | Mar-Jul much better, but on top of the hours Aug-Sep got worse under every gate (require_alignment +0.09 -> -0.02 R) - off |
| Start at 07:00 New York (London overlap) | Worse under every gate (require_alignment -2.7%) |
| Evening window from 19:00 | Aug-Sep worse (require_alignment -0.15 R) |
| Evening window to 21:00 | Worse under every gate |
| US session only (no evening) | Worse (require_alignment -4.2%) |
| No Friday cutoff | More profit in this sample - from two +9R trades held over **one** weekend gap. A gap the other way loses several times the $6 stop, so the cutoff stays on |
| Other entry features (extension from EMA, RSI level, D1/H4 bias, stacking, round numbers) | No pattern that held in both periods |

Moving an hours edge by one hour made results worse each time: the finding
"not the Asian session, not the London morning" is robust; the exact edges
are the best of those tried, not a guarantee.

## Engine fixes found in this round

| Severity | Finding | Fix |
|---|---|---|
| High | The data vendor fills gold's daily break (17:00-18:00 New York) with flat fake bars; entries "traded" in a closed market. The earlier 7-week report was affected | Break hour removed from the data before testing |
| High | A signal on the last bar before the daily break or the weekend was filled at the reopen price; live, the order is refused (market closed) | Refused in the backtest too |
| Medium | Spread was constant all day; real gold spreads are several times wider at the reopen | `--rollover-spread-points` (default 80) from 16:55 to 18:15 New York |
| Medium | The snapshot sent to Claude carried today's date in a backtest (wall clock, not the replayed bar) | Uses the replay clock |
| Low | Session labels for Claude (Asian/London/New York) were fixed UTC hours - one hour wrong for half the year | Each session in its own time zone (daylight saving followed) |

Earlier rounds (still in place): sell stops on the ask, gap fills at the
open, bid/ask pricing with one spread per round trip, short D1/W1 warm-up,
XTR stand-down per setup and direction, validated state records, replay-time
stamps on decision logs.

## Recommendations

1. Run the defaults on **demo** for 2-4 weeks. Expect few Claude trades
   (about one a week) - that is the tactic working, not a fault.
2. Judge after 100+ trades across both sources, not after a week.
3. Keep a raw-spread account: the $6 stop is about one M15 ATR.
4. A paid Claude backtest of two weeks in the trading hours (~450 bars) is
   the next real test of Claude's own judgment.

## Reproduce

In the GoldTrader folder (CSV paths in full):

```bat
python goldtrader.py backtest --bars-csv C:\data\m15.csv --trend-csv C:\data\h4.csv --daily-csv C:\data\d1.csv --weekly-csv C:\data\w1.csv --m5-csv C:\data\m5.csv --h1-csv C:\data\h1.csv --mechanical
```

That runs the live defaults (require_alignment + trading hours + guards).
Compare without the tactics: add `--trade-hours any --friday-cutoff off
--max-spread 0`; other gates: `--compare-xtr --xtr-gate block_opposed`;
stress: `--spread-points 50 --rollover-spread-points 150`. Straight from
MT5 instead of CSVs: `--from-mt5 --start 2026-03-16 --end 2026-09-23`.
