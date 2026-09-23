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
| Which messages are read | **Trade messages only**, from up to 3 channels (`InpChannelId1`..`InpChannelId3`): a signal (buy/sell + price + gold or SL/TP, at most `InpMaxMessageChars`=400) or a short trading command (at most `InpMaxCommandChars`=60, trading words only). Greetings, mood posts, commentary, long messages, videos, audio, voice notes, stickers, documents and pinned-message notices are omitted before parsing - see `../SETUP.md` "Trade-only messages" | - |
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

## Remote control (optional): Pause / Resume buttons and Why

Seven plain-text commands, DM'd to this bot from your own Telegram account
(`InpControlChatId`) - a completely separate command path from trading
signals, matched by **exact** text (trimmed, case-insensitive), never
substring, since these close real positions. Also subject to
`InpMaxSignalAgeSec` like any trading signal: a command queued during a
long outage and only delivered once the EA reconnects is dropped as stale
rather than force-closing positions you may no longer want touched - just
resend it if it's still what you want.

**Shown as tappable buttons, not just typed text.** The EA sends a
Telegram reply-keyboard (the row of buttons under the message box) once
at startup and again with every confirmation reply, so you don't have to
type the command by hand. Tapping a button sends its label as an ordinary
message - identical to typing it - so nothing about how commands are
matched changes; typing the exact text still works too, from any
Telegram client (including ones where reply keyboards render oddly).

| Command | What it does |
|---|---|
| `PauseHab` | Closes every open position on this chart's symbol, under both magics, cancels Telegram pending orders there, and blocks new Telegram **and** Claude entries until `ResumeHab`. |
| `ResumeHab` | Re-enables new Telegram and Claude entries. Reopens nothing. |
| `PauseTelHab` | Closes this symbol's Telegram-sourced positions/orders only and blocks new Telegram entries until `ResumeTelHab`/`ResumeHab`. Claude untouched. |
| `ResumeTelHab` | Re-enables new Telegram entries only. |
| `PauseClaudeHab` | Closes this symbol's Claude-sourced (`InpClaudeMagicNumber`) positions and blocks new Claude entries until `ResumeClaudeHab`/`ResumeHab`. Telegram untouched. |
| `ResumeClaudeHab` | Re-enables new Claude entries only. |
| `Stats` | Equity, balance, open P/L, and closed P/L with win rate for today / 7 days / 30 days (total and per source), plus how much of the daily loss budget is used and whether each source is paused. |
| `News` | The economic calendar (MT5's own): releases from the last 12 hours with actual vs forecast and what that usually means for gold, and upcoming events in the next 24 hours. |
| `Why` | Echoes the latest Claude verdict's reasoning, read from the shared `Common\Files` text file `python/main.py` writes it to after every evaluation cycle (`InpLastVerdictFilename` - MUST match `config.py`'s `last_verdict_filename`, both default `claudesmc_last_verdict.txt`). Read-only - never touches a position or the pause state. Reports "No Claude verdict on file yet" if `main.py` hasn't run a cycle, or the filenames don't match. |

**Scope: this chart's symbol only**, like every other position-management
function in this file (`CloseAllMine`/`CancelAllPendingMine`/
`ManageAllPositions`) - a position opened by hand on a different symbol is
untouched by any of the four position-affecting commands (`Why` doesn't
touch positions at all).

**Run only one instance of this EA per terminal, on any symbol.** This was
already true before remote control existed - the Telegram update-id
cursor is a terminal-wide Global Variable, not scoped per chart - and
remains true for the new pause state too (the same mechanism): a second
running instance, even on a different symbol or with its own bot token,
shares both with this one and will corrupt them, including silently
pausing or resuming a chart nobody ever sent a command to. (The daily
trade counter is unaffected - that one's plain per-instance memory.)

**How the Claude pause works.** Claude's entries are placed by
`python/main.py`, not by this EA, so the EA can't refuse them directly.
Instead it writes `paused` or `running` to a small text file in MT5's
shared `Common\Files` folder (`InpClaudePauseFilename`, default
`claudesmc_pause.txt` - MUST match `config.py`'s `claude_pause_filename`),
the reverse of how the **Why** button reads Claude's verdict. `main.py`
checks it at the start of every cycle and, while it says `paused`, skips
the whole evaluation - no Claude API call, no new order. Open positions
keep being managed by this EA either way. Like **Why**, this needs
`main.py` on the **same machine** as the MT5 terminal. The Claude pause
is persisted exactly like the Telegram one (a terminal Global Variable,
never in dry-run, dormant while `InpControlChatId=0`), and the file is
rewritten from that state every time the EA starts, so a stale file
can't leave Claude blocked with no Resume button reachable.

**Setup:**
1. Set `InpControlChatId` to your own DM chat id with this bot - **never**
   `InpChannelId1`..`InpChannelId3` (OnInit refuses to start if they match).
   To find it: message the bot directly (not the signal channel) once,
   then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser
   and read `"chat":{"id": ...}` from that message's entry.
2. `InpBotToken` must be set even if `InpEnableTelegramSignals` is false -
   a bot token is needed to receive the control DM regardless of whether
   Telegram signal *execution* is on.
3. Leaving `InpControlChatId=0` (the default) disables this feature
   entirely - no behavior change, nothing extra polled.

The pause state (new Telegram entries blocked or not) is saved to a
terminal Global Variable the moment it changes, the same mechanism already
used for the Telegram update-id cursor - it survives a restart/reattach
rather than silently resetting to "resumed". This still holds even if you
later set `InpControlChatId` back to `0`: a real pause set earlier goes
dormant (never enforced) rather than silently blocking every Telegram
entry forever with no `ResumeHab` reachable to clear it - setting
`InpControlChatId` back to a real chat restores whatever pause was last
actually set, unchanged.

**Trade-closed notices** (`InpNotifyTradeClosed`, default on, needs
`InpControlChatId`): every closed Telegram or Claude trade sends a short
message - the trade's net P/L (commissions included), current equity, and
today's closed P/L with win rate.

## Economic calendar (news filter)

Uses MT5's own built-in economic calendar (Calendar tab - MetaQuotes data,
no API key or extra download). With `InpNewsFilter` (default on), a new
Telegram entry is skipped from `InpNewsBlockBeforeMin` (15) minutes before
to `InpNewsBlockAfterMin` (15) after any `InpNewsMinImportance` (high)
event for `InpNewsCurrencies` (USD). The EA also writes the calendar every
`InpCalendarRefreshMin` (5) minutes to `InpCalendarExportFile` in the shared
`Common\Files` folder; ClaudeSMC_Trader's `python/econ_calendar.py` reads it
to apply the same blackout to Claude's entries and to show Claude the
events and their impact on gold. The calendar isn't available in the
Strategy Tester, and can be empty for a few minutes after the terminal
starts - then nothing is blocked. Needs `MQL5/Include/EconCalendar.mqh`.

## Optional risk features (Telegram-sourced entries only)

On by default (recommended values - see `ClaudeSMC_Trader/README.md`'s
"Risk parameters" section for the full standard-practice reasoning). Set
either input to `0`/`false` to opt back out; neither is required:

- **Daily loss circuit breaker** (`InpMaxDailyLossPct`, default `10.0`,
  `0` = disabled): withholds new Telegram-sourced entries once the account
  is down this many percent on the broker's server day (Python's breaker uses the UTC day), latched until the next day.
  Existing open positions are never touched. It is also a real **budget**,
  not just a trigger: a new Telegram entry is skipped if today's drawdown +
  what every open position/pending order on the symbol (any magic -
  Claude's, manual trades, other EAs) still risks to its stop + the new trade's own risk would exceed the cap - so several
  concurrent trades can't jointly stop out past it. Mirrors
  `ClaudeSMC_Trader`'s own `max_daily_loss_pct` (Python side).
- **Equity-scaled lot sizing** (`InpUseRiskPercent` default `true`,
  `InpRiskPercent` default `2.0`, `InpMaxLotSize`): sizes each
  Telegram-sourced trade from current equity instead of always
  `InpFixedLot`, holding risk a constant fraction of the account. `2.0`
  is `InpMaxDailyLossPct / 5`, so the daily breaker absorbs ~5 losing
  trades before halting, and sits inside the conventional 1-2%-per-trade
  risk-management band. `InpSlDollars`, `InpTp1Dollars` and
  `InpTrailDollars` are all dollar amounts *at the reference lot*
  `InpReferenceLot` (default `0.01`, MUST equal `ClaudeSMC_Trader`'s
  `reference_lot`), i.e. fixed price distances. `InpFixedLot` is only the
  traded lot when `InpUseRiskPercent=false`, and changing it never moves
  the stop - only the traded volume
  changes, so a risk-sized lot risks and locks proportionally more money
  with the same SL : TP1 : trail shape (e.g. at 0.33 lots: ~$200 risked,
  ~$200 locked at TP1). If the risk-sized lot would round below the
  broker's minimum, it's clamped up to that minimum with a logged warning
  (there's no smaller order to place) rather than silently over-risking a
  small account with no signal that it happened. When `InpMaxDailyLossPct`
  is also set, `OnInit()` logs a startup risk audit: the actual lot size,
  money risked, and how many losing trades the daily cap absorbs at that
  size - warning if it's fewer than 3 (the breaker would then double as
  the strategy, halting on ordinary variance rather than a genuinely bad
  day).

## Setup

1. Copy `MQL5/Experts/UnifiedTrader_EA.mq5` into your terminal's
   `MQL5/Experts/` folder. It needs two includes too - copy
   `../MQL5/Include/TelegramSMC_Common.mqh` (CSV logging) and
   `../MQL5/Include/EconCalendar.mqh` (economic calendar) into your
   terminal's `MQL5/Include/` folder (needed at compile time regardless of
   which source(s) you enable). Compile.
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
- **CLOSE/CANCEL (from the signal channel) act only on Telegram-sourced
  positions/orders**, never on Claude-sourced ones - those remain
  Python's (and, for exits, `ManagePositionExit`'s) to manage. The
  separate remote-control `PauseClaudeHab`/`PauseHab` commands (see above)
  are the one deliberate exception - they close Claude-sourced positions
  too, but only ever from `InpControlChatId`, never the signal channel.
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
- **Telegram network calls can briefly delay live position management.**
  MQL5's `WebRequest` is synchronous, and `OnTick()` (where
  `ManageAllPositions()` trails/locks every open position) is serialized
  behind `OnTimer()` (where all Telegram polling and sending happens) on
  this EA's own event thread - a slow `getUpdates` poll or a remote-control
  confirmation reply can delay that tick's position management by however
  long the request takes, up to its own timeout. This isn't new to remote
  control (`getUpdates` has always worked this way whenever Telegram
  signals are enabled) - each confirmation reply just adds one more such
  call, capped at 3 seconds *per reply* (deliberately lower than a
  user-raised `InpHttpTimeoutMs`, which governs polling reliability
  instead) - so if more than one control command lands in the same
  `InpPollSeconds` window, each gets its own confirmation send and its own
  up-to-3s wait, one after another, not a single shared 3s bound for the
  whole tick.
