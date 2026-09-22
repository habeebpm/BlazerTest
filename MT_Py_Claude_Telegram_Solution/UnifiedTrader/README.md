# UnifiedTrader_EA

One MQL5 EA, two independent trade sources, one shared risk model - built by
combining `../MQL5/Experts/TelegramSMC_Copier.mq5` and
`../ClaudeSMC_Trader/MQL5/Experts/ClaudeSMC_TradeManager.mq5` into a single
file, with two deliberate changes: **Telegram signals execute with no SMC
validation and no use of the message's own stop-loss**, and **both sources
share one combined position cap** instead of each having their own. Toggle
either source, or both, independently.

**The two toggles (`InpEnableTelegramSignals`/`InpEnableClaudeManagement`)
gate NEW entries only.** Exit management is never gated by them: once a
position exists under either magic number, this EA keeps protecting it
(lock-then-trail) for as long as it's running, even if you later turn that
source's toggle off - turning a source off only stops it taking new
signals, it never abandons a position already open under that source's
magic number.

This is a fourth EA, not a replacement for the two it's built from - it is
a materially higher-risk configuration for the Telegram side specifically
(see "Honest limitations" below), and exists as an explicit, opt-in
alternative for anyone who wants that tradeoff. `TelegramSMC_Copier.mq5`
(with its SMC filter and SL sanity checks) and `ClaudeSMC_TradeManager.mq5`
remain the recommended, more conservative defaults.

## The two sources

| | Telegram (`InpEnableTelegramSignals`) | Claude (`InpEnableClaudeManagement`) |
|---|---|---|
| Decides *whether* to trade | This EA, from a parsed Telegram message | `../ClaudeSMC_Trader/python/main.py` (unchanged - MQL5 can't practically call the Claude API, see below) |
| Validation before entry | Chat allow-list, signal age, price-vs-zone deviation, the shared position cap, daily trade cap. **No SMC filter. No check on the message's own SL/TP1/TP2/TP3** - none of that is used | Claude's own 3-confluence + full-conviction gate (unchanged) |
| Entry SL/TP | **Fixed `InpSlDollars`/no broker TP** - never the message's own numbers | Python's own fixed `sl_dollars`/no broker TP (unchanged) |
| Exit management | This EA (lock-then-trail only - `InpExitStyle` breakeven step never applies) | This EA (identical logic, different magic number; `InpExitStyle` opt-in breakeven step applies here) |
| Magic number | `InpTelegramMagicNumber` (default 20260922) | `InpClaudeMagicNumber` (must match `AdvisorConfig.magic`, default 20260921) |

**Why the Claude side still needs Python running.** MQL5 has no JSON
library and no practical way to build the market-intelligence snapshot
`market_intel.py` sends Claude (indicators + SMC structure across multiple
timeframes) - reimplementing all of that in MQL5 just to make one API call
isn't a reasonable trade. Python keeps deciding those entries exactly as
it does today; this EA's only job for that magic number is exit
management, identical in mechanism to `ClaudeSMC_TradeManager.mq5`.

## Shared exit design (both sources, identical logic)

`exit_style="sl_to_tp1"` (the default for both sources), same as
`ClaudeSMC_TradeManager.mq5`: no broker take-profit is ever placed - the
stop-loss is the only exit. Once floating profit reaches `InpTp1Dollars`
($6), the SL moves to **exactly** that price (a deterministic lock, no
buffer), then trails `InpTrailDollars` ($3) behind new highs/lows,
tightening only. "Armed" is derived every tick from the position's own
current SL, never stored - this EA needs no memory across ticks or
restarts.

**`InpExitStyle=EXIT_BREAKEVEN_R_DECAY` is a Claude-management-side option
only** - it applies exclusively to `InpClaudeMagicNumber` positions;
Telegram-sourced positions always use plain `sl_to_tp1` regardless of this
setting. It adds one earlier protective step before the lock above: once
floating profit reaches `InpBreakevenAtrMult` (0.5) x the position's own
`InpAtrPeriod`-bar ATR on `InpAtrTimeframe` (M5 by default), **or**
`InpDecayWindowMinutes` (15) have passed since entry - whichever happens
first, and only once price has actually moved far enough into profit to
place a valid stop there - the SL moves to **exactly** the entry price
(breakeven). A fast move can still jump straight past this step to the
full TP1 lock in one tick. Must match
`../ClaudeSMC_Trader/python/config.py`'s `AdvisorConfig.exit_style` and its
`breakeven_atr_mult`/`breakeven_atr_period`/`decay_window_minutes` - keep
both sides in sync by hand, the same way `InpTrailDollars`/`trail_dollars`
already have to be. See `../ClaudeSMC_Trader/README.md`'s "Exit design"
section for the full rationale.

## Shared position cap

`InpMaxPositionsPerDirection` (default 5) is counted across **both**
magic numbers together, always - not 5 each (open positions AND pending
Telegram limit orders both count, so a burst of pending zones can't fill
simultaneously past the cap). A 6th same-direction position/order is
blocked regardless of which source is trying to open it, as long as
either source's positions are visible to this EA (same account/symbol).

**This EA only enforces the cap for its own (Telegram) entries** - it
cannot intercept an order Python places directly. For the cap to also hold
Python back once Telegram-sourced positions fill it, set
`AdvisorConfig.shared_cap_magic_numbers = [20260922]` (or
`python main.py --shared-cap-magic 20260922`) to match
`InpTelegramMagicNumber`. Without that, this EA still won't let Telegram
open past the shared cap, but Claude could independently open up to 5 more
of its own.

**That wiring alone isn't enough - the two NUMBERS must match too.**
`shared_cap_magic_numbers` only tells Python's gate *which magics to
count*; it doesn't keep `InpMaxPositionsPerDirection` (this EA) and
`max_open_positions_per_direction`/`--max-positions` (Python) equal to
each other. If they differ, both sides are counting the same combined
position set but enforcing *different* ceilings - the lower one silently
wins for its own new entries while the higher one keeps opening past it.
`main.py` prints a warning reminder at startup whenever
`shared_cap_magic_numbers` is set, but can't verify the MQL5-side value
for you - check it by hand.

## Setup

1. Copy `MQL5/Experts/UnifiedTrader_EA.mq5` into your terminal's
   `MQL5/Experts/` folder. It needs `TelegramSMC_Common.mqh` too (for CSV
   logging) - copy `../MQL5/Include/TelegramSMC_Common.mqh` into your
   terminal's `MQL5/Include/` folder if it isn't there already (needed at
   compile time regardless of which source(s) you enable). Compile.
2. Load `MQL5/Presets/UnifiedTrader_EA_Default.set` from the Inputs tab.
   Both sources ship **disabled** - enable at least one:
   - **Telegram**: set `InpEnableTelegramSignals=true`, follow
     `../MQL5/README.md`'s Telegram Bot setup (same steps: @BotFather,
     add the bot as channel admin, allow WebRequest for
     `https://api.telegram.org`, discover channel ids with both slots at
     0 on the first run).
   - **Claude**: set `InpEnableClaudeManagement=true`. Confirm
     `InpClaudeMagicNumber` matches `../ClaudeSMC_Trader/python/config.py`'s
     `AdvisorConfig.magic` (both default 20260921), and that
     `python main.py` is running (dry-run or live) - this EA never
     opens Claude-sourced positions itself, only manages ones Python
     already opened. If Python's `AdvisorConfig.exit_style` is
     `"breakeven_r_decay"`, set `InpExitStyle=EXIT_BREAKEVEN_R_DECAY` here
     too and keep `InpBreakevenAtrMult`/`InpAtrPeriod`/
     `InpDecayWindowMinutes`/`InpAtrTimeframe` in sync with it by hand -
     otherwise leave `InpExitStyle` at its default.
3. Drag onto an XAUUSD chart, tick "Allow Algo Trading" (and "Allow
   WebRequest" is already covered by step 1 if using Telegram).
4. Leave `InpDryRun=true` until you trust the logged behavior.

## Honest limitations

- **Telegram execution has materially less protection than
  `TelegramSMC_Copier.mq5`.** No liquidity-sweep/premium-discount check, and
  the message's own stop-loss (however implausible) is never even read for
  sizing - only direction and an entry zone are used. A message that
  merely parses as "BUY XAUUSD around 2350" trades exactly like one with a
  carefully reasoned setup. Demo-test until you trust the channel's call
  quality itself, not just this EA's mechanics.
- **The shared position cap is only truly symmetric if you also configure
  Python's `shared_cap_magic_numbers`** - see above. This EA alone can
  only gate its own new entries, not intercept another process's.
- **CLOSE/CANCEL act only on Telegram-sourced positions/orders**, never on
  Claude-sourced ones - those remain Python's (and, for exits,
  `ManagePositionExit`'s) to manage.
- **No "move SL to breakeven" text command**, unlike `TelegramSMC_Copier.mq5`
  - the automatic lock-then-trail exit already gets every position to
  breakeven-or-better once `InpTp1Dollars` is reached, generally faster
  and more consistently than a manual per-message command would. Note that
  even the automated `EXIT_BREAKEVEN_R_DECAY` step (see above) never
  applies to Telegram-sourced positions - it's Claude-management-side only.
- **No backtest** - same reason as `TelegramSMC_Copier.mq5`: there's no
  historical Telegram feed to replay, and this EA disables Telegram
  polling inside the Strategy Tester. The Claude-management half is, in
  principle, testable the way `ClaudeSMC_Trader/python/backtest.py`
  already is (it simulates the identical lock-then-trail logic), just not
  through this MQL5 file directly.
