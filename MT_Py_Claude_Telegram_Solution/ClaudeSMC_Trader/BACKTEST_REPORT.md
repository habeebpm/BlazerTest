# Backtest report - XAUUSD, 6 Aug - 23 Sep 2026

Real history (Twelve Data XAU/USD, M5/M15/H1/H4, converted to UTC, weekend
filler bars removed). 3,277 closed M15 bars evaluated, no lookahead.
Gold over the window: 4,055 -> 4,673 -> 4,289 (buy-and-hold -0.15%) - a
wide two-way range, not a trend.

**What this measures:** `backtest.py --mechanical` - the three-leg rules
Claude is told to apply, voted mechanically. It tests the rules, exits,
risk and XTR gate on real prices. **It is not Claude's judgment** (Claude
also reads SMC structure, levels, news and calendar).

Live rules, unchanged: 2% risk per trade, 10% daily cap/budget, $6 SL /
$6 TP1 lock / $3 trail at the 0.01 reference lot, max 5 per direction,
starting equity $10,000, spread 25 points ($0.25).

## Ranking

| # | Exit style | XTR gate | Trades | Win % | Return | Max DD | Profit factor | P(edge <= 0) |
|---|---|---|---|---|---|---|---|---|
| 1 | fixed_tp | require_alignment | 19 | 52.6 | **+1.2%** | **6.2%** | 1.07 | 0.42 |
| 2 | sl_to_tp1 | require_alignment | 19 | 47.4 | -1.0% | 7.4% | 0.95 | 0.56 |
| 3 | sl_to_tp1 | off | 107 | 46.7 | -7.4% | 21.8% | 0.93 | 0.64 |
| 4 | sl_to_tp1 | block_opposed *(live default)* | 59 | 44.1 | -11.4% | 23.0% | 0.81 | 0.78 |
| 5 | fixed_tp | block_opposed | 58 | 44.8 | -13.5% | 22.7% | 0.77 | 0.83 |
| 6 | fixed_tp | off | 106 | 46.2 | -17.8% | 27.1% | 0.82 | 0.85 |

P(edge <= 0) = share of 5,000 bootstrap resamples with expectancy <= 0.
**No variant shows a statistically reliable edge** - every 95% confidence
interval for expectancy spans zero. #1 and #2 differ by about one trade.

## Findings

1. **`require_alignment` is the clear risk winner.** It cuts max drawdown
   from ~22% to ~7% (Monte Carlo 95th percentile 38% -> 13%) and the
   longest losing streak from 11 to 3-4, at roughly break-even. It trades
   rarely (19 trades in 7 weeks).
2. **`block_opposed` (the live default) did not help here.** Graded
   per trade on the ungated run, entries XTR calls "opposed" (pullbacks
   against M15/H1) won 53.7% and made +$1,440. The real loser was
   "extended chase" (37 trades, -$2,873) and "reduced" conviction (48
   trades, -$1,804).
3. **`sl_to_tp1` beats `fixed_tp`** with no gate or `block_opposed` (+10.4
   and +2.1 points): the trail makes the average win $200 vs $169. With
   `require_alignment`, `fixed_tp` came out ahead, but that sample is only
   19 trades, too few to call.
4. **Time of day:** 17:00-22:00 UTC was the only profitable window in every
   variant (+$2,599 ungated). Asian (22-07) and London (07-12) lost.
   This is a pattern to watch in the demo, not a rule yet.
5. **Very cost-sensitive.** At 50-point spread the ungated run goes from
   -7.4% to -21.5%. A $6 stop is about one M15 ATR on gold at $4,300, so
   spread and slippage are a big share of each trade. Use a raw-spread/ECN
   account, 25 points or less.
6. **Risk controls held.** Worst day -$966 (-9.7%), inside the 10% cap.
   Longest losing streak 11 at 2% risk; Monte Carlo 95th-percentile
   drawdown 38% (ungated) vs 13% (`require_alignment`).
7. **Buys lost in every variant**; sells were flat to positive (except
   `fixed_tp` ungated) - consistent with the down-leg from 4,673 in late
   August.

## Engine fixes found by this review (in this commit)

| Severity | Finding | Fix |
|---|---|---|
| High | Sell stops fired on the bid (25 points late) and gap-through stops filled at the stop price - flattered results by ~22 points of return (+15.2% -> -7.4%) | Sell stop/lock/trail on the ask; a bar opening through a stop fills at the open |
| Medium | `reset()` demanded 300 D1 **and 300 W1** bars (~6 years) - a short CSV or `--from-mt5` range could not start | D1/W1 need only 5 bars (they feed previous day/week levels) |
| Medium | XTR gate could not be backtested | `--m5-csv`/`--h1-csv`/`--xtr-gate`/`--compare-xtr`, same order as live: stand-down learns from closed trades, entry filter only |
| Low | Backtest `decisions.csv` stamped with wall-clock time | Stamped with the replayed bar time |
| Low | README said risk % and daily cap are off in backtests - they use the live defaults | Corrected |
| Low | Summary lacked profit factor / expectancy / % figures | Added |

Lot size, SL, TP and trail rules were not changed.

## Recommendations

1. **Don't read these numbers as the live result.** Measure Claude itself:
   a paid Claude backtest on two weeks (~900 bars, about $45-135), or 2-4
   weeks on demo, the XTR gate choice decided from those results.
2. For demo, consider `python main.py --xtr-gate require_alignment ...`
   (EA side unchanged): about a third of the drawdown in this sample. It is
   an entry filter only.
3. Keep `sl_to_tp1` as the exit style (better in the larger samples).
4. Log the entry hour in the demo and review the 17-22 UTC effect after
   100+ trades before acting on it.

## Reproduce

```bash
cd python
python backtest.py --bars-csv m15.csv --trend-csv h4.csv --daily-csv d1.csv \
    --weekly-csv w1.csv --m5-csv m5.csv --h1-csv h1.csv --mechanical \
    --compare --compare-xtr --xtr-gate require_alignment
# stress: add --spread-points 50
```
