# GoldTrader - reference

The setup steps are in [`../README.md`](../README.md) (step by step with
checks: [`DEPLOYMENT.md`](DEPLOYMENT.md)). This page explains
what runs, how it decides, every safety limit, where files are, and what to
do when something goes wrong.

## What runs

| Part | Where | Does |
|---|---|---|
| **UnifiedTrader_EA** | MT5 chart | Copies Telegram trade signals (magic 20260922); manages exits of every position (Telegram and Claude's, magic 20260921); Telegram buttons; economic-calendar export; price files for Drive |
| **Trading program** (`app/main.py`, via `start.bat`) | Python | Each closed M15 bar inside the trading hours: builds a market snapshot, asks Claude for a verdict, applies the gates, sends Claude's entries |
| **Relay bridge** (`relay/`, via `start.bat`) | Python | The signal path: your own Telegram account copies the trade messages of your signal channel(s) into your private relay group, where the EA's bot reads them (`InpChannelId1` = the relay group). Works for any channel you can read - one that blocks forwarding gets its text copied instead |
| Drive export (`drive_export/`) | Python, optional (VPS) | Price files to Google Drive without Drive for Desktop |
| ML retrain, conviction report, **scorecard** | Python, automatic | Daily / weekly, see `settings.ini`; the scorecard goes to your Telegram |
| TelegramSMC_TradeLogger | MT5 chart, optional | Trade journal CSV (both sources) |
| Dashboard (`dashboard/`) | IIS, optional | Password-protected phone page: equity, positions, trades, Claude's decisions, Telegram signals, scorecard (read-only) |

`start.bat` runs the trading program and every companion switched on in
`settings.ini`. It restarts the program after a crash or when MT5 was not
reachable (every 60 s), and switches off the console's QuickEdit so a click
in the window cannot freeze it.

## Trading rules and risk parameters (fixed)

| Rule | Value |
|---|---|
| Risk per trade | 2% of equity (lot sized from it) |
| Stop loss | $6 at the 0.01 reference lot (a fixed price distance). **Telegram trades:** the signal's own stop when it is on the right side and $3-$20 from the entry (EA `InpTelegramUseSignalSl`, `InpSignalSlMinDistance`, `InpSignalSlMaxDistance`); otherwise, or with no stop in the signal, the $6 stop |
| Lot | sized so the stop risks 2% of equity - a wider signal stop gives a smaller lot (e.g. $14 stop: 0.71 lot at $50,000 instead of 1.66), so every trade still risks 2% |
| TP1 | at +$6 the SL is locked there (no broker TP) |
| Trail | $3 behind price after TP1 |
| Positions per direction | max 5, shared by both sources |
| Daily loss cap | 10% of the day's starting equity - no new entries after it; each entry must also fit the remaining 10% budget including open risk. The day is the broker's server day (17:00 New York at most gold brokers) for both the EA and Python |
| Margin guard | An entry is skipped if, after it, the free margin could not cover every open stop plus its own (both sources) - so a losing run reaches the stops, not the broker's margin call |

Python (`app/config.py`) and the EA preset already carry the same numbers,
magic numbers and shared file names (checked by `goldtrader.py test`).

## How a Claude entry is decided

1. **Before calling Claude** (no cost if blocked): pause switch
   (`PauseClaudeHab`), daily cap, **trading hours, Friday cutoff and spread
   guard** (see Entry tactics below), news blackout (MT5 economic calendar,
   15 min around high-impact USD events), daily trade limit (off by
   default), the XTR pre-check (M15 and H1 clearly against each other =
   nothing to trade), the ADX trend filter if switched on, and the
   **pre-screen**: the three legs below are computed from the same numbers
   Claude gets; if fewer than 2 agree or none is confirmed, no trade is
   possible under the rules, so Claude is not asked (about half the calls
   saved; `--no-prescreen` to ask anyway).
2. **Snapshot:** M15/H4/D1/W1 indicators, smart-money structure (order
   blocks, fair-value gaps, liquidity sweeps, premium/discount), key levels,
   session, calendar, the XTR M5/M15/H1 alignment read, recent performance
   and - once trained - the ML win probability.
3. **Claude's verdict:** trend / momentum / strength legs, conviction
   (`full` / `partial` / `none`), reasoning. Only `full` trades.
4. **XTR alignment reading** (`--xtr-gate`, default `off`): each timeframe
   is bullish/bearish only when EMA9 vs EMA21, RSI14 vs 50 and the MACD
   histogram all agree. By default it is evidence for Claude only (the
   best setting over a year of prices). Optional gates: `block_opposed`
   refuses entries against a clear M15/H1 and the two failing momentum
   patterns, and after 2 losses in a row on the same setup and direction
   within 1 ATR stands that setup down; `require_alignment` additionally
   needs the M5 trigger and one agreeing HTF (half the swings, but about one
   trade a week and no edge in the backtest). Never changes lot, SL or TP.
5. **Breaking-news check** (free RSS feeds, one short Claude call) right
   before the order - a surprise against the trade blocks it; feeds down =
   trades anyway and says so.
6. Order sent; Telegram alert with entry, SL, TP1 and Claude's targets.

Telegram signals (EA side) are copied when they parse as a trade and no
clear M15/H1 is against them (`InpXtrHtfFilter`), inside the same position
cap, daily cap, news filter and spread limit (`InpMaxSpreadPoints`, 50).
Their stop is the signal's own (`SL`, `Stop loss`, `Stop:`) when it is on
the right side and $3-$20 from the entry, with the lot sized from it so
the trade still risks 2%; the daily budget and the margin guard check that
real stop and lot. Otherwise the $6 stop. The lock (+$6) and trail ($3)
are the same for every trade; the signal's targets are logged only. With
a wide signal stop the $6 lock is less than 1R (a $14 stop: +0.43R), so
those trades need a higher win rate - the scorecard and the journal
measure each trade against its own stop. `InpTelegramUseSignalSl=false`
goes back to the $6 stop for every trade.
Greetings, mood posts, long commentary, videos, audio and stickers are
ignored. Telegram's trading hours: a signal posted outside 06:00-23:00
Oman time or at the weekend is logged "outside trading hours" and not
copied (`InpTradeHours`, `InpTradeUtcOffsetHours`,
`InpTradeWeekdaysOnly`). Close, breakeven and cancel messages work at any
hour, and open trades are managed around the clock.

## Entry tactics (when Claude may trade)

Tested on a year of real prices, including six months downloaded after the
settings were chosen ([`BACKTEST_REPORT.md`](BACKTEST_REPORT.md)).
They only decide *whether* an entry is allowed - lot, SL, TP1, trail and the
position cap never change.

| Tactic | Default | Why |
|---|---|---|
| Trading hours | 08:00-16:45 and 18:15-20:00 **New York time** = 16:00-00:45 and 02:15-04:00 **Oman time** in summer, an hour later in winter | The best window in a year of backtests: +0.14R a trade, positive in all three periods; 06:00-23:00 Oman lost -0.07R (the Asian and London-morning hours) |
| Friday cutoff | no new entry from 16:00 New York on Friday | A $6 stop cannot protect a position over the weekend gap |
| Spread guard | no entry above 50 points | Reopen and news spikes; 50 points is already 8% of the $6 risk |
| Trend filter | off (`--min-adx 25` to try it) | Helped Mar-Jul, not Aug-Sep |

Two windows, both Monday-Friday in Oman terms:

| Source | Window | Set in |
|---|---|---|
| Telegram signals | 06:00-23:00 Oman time (UTC+4, no daylight saving) | EA inputs `InpTradeHours`, `InpTradeUtcOffsetHours`, `InpTradeWeekdaysOnly` |
| Claude | 16:00-00:45 and 02:15-04:00 Oman time (summer), 17:00-01:45 and 03:15-05:00 (winter) | `app/config.py` `trade_windows` in New York time, which follows US daylight saving by itself |

Claude's Sunday 18:15 New York slot is Monday morning in Oman. Outside its
hours the log says `No evaluation this cycle (no Claude call): outside
trading hours` - that is normal. Try other Claude hours in `start.bat`
(`GT_ARGS`): `--trade-hours "08:00-12:00"` (`any` = all day), `--friday-cutoff off`, `--max-spread 40`,
`--min-adx 25`. A mistyped value stops `start.bat` with a clear message
instead of restarting.

## Settings

- **Keys / ids** in **`keys.txt`** (GoldTrader folder, one `NAME=value`
  per line, editable in Notepad): `ANTHROPIC_API_KEY`,
  `TELEGRAM_ALERT_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`, and for the relay
  `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SOURCE_CHANNELS`,
  `TELEGRAM_RELAY_GROUP` (optional `MT5_PASSWORD`). Created by `setup.bat`
  (pre-filled with any key already set on the PC), filled by the first-start
  questions / `settings.bat`, shown masked by `check.bat`. Read by the
  trading program, the relay, the scorecard and the backtest on every start;
  a value there wins over the same Windows environment variable, an empty
  one leaves it alone. Change a value -> restart `start.bat`. The EA does not
  read it: `InpBotToken`, `InpControlChatId` and `InpChannelId1` are still
  typed in its inputs (same values). Private: `.gitignore` keeps it out of
  git - never share it or keep the folder in a synced cloud folder.
- **`settings.ini`:** companion programs on/off (`enabled`), extra options
  (`args`), schedule (`every_days`).
- **`start.bat`:** the trading program's options (`GT_ARGS`). Everything
  `python goldtrader.py start --help` lists can go there.
- **EA inputs:** in MT5 (preset `UnifiedTrader_EA_Default.set`).

## Files

| Where | What |
|---|---|
| `keys.txt` | Your keys and ids (private, never uploaded) |
| `logs/decisions.csv` | Every Claude evaluation, executed or not |
| `logs/trades.csv` | Every order sent |
| `logs/ml_snapshots.csv`, `logs/ml_win_probability_model.joblib` | ML data + model (~0.3 KB per trade, ~50 KB model) |
| `logs/ml_retrain.log`, `logs/calibration_report.log`, `logs/scorecard.log` | Output of the automatic jobs |
| `logs/day_state.json`, `logs/xtr_state.json`, `logs/services_state.json` | State kept across restarts |
| `logs/status.json` | The dashboard's data, rewritten once a minute |
| `logs/replay/` (and Drive `GoldTrader_replay_*`) | Signal-history replay: summary, simulated trades, messages, M1 prices |
| `logs/journal/` and Drive `MyMQChartDrive\GoldTrader\` | Trade journal: `GoldTrader_trades.csv`, `GoldTrader_claude_decisions.csv`, `GoldTrader_telegram_signals.csv` |
| `dashboard/App_Data/password.txt` | The dashboard password's salted hash (never uploaded) |
| MT5 `MQL5\Files\TelegramSMC_Signals.csv` / `..._Results.csv` | EA signal log / trade journal |
| MT5 `Common\Files\XTR_Data\` | Price files (and your Drive folder if set) |
| `relay/tg_relay_bridge.session` | The relay's Telegram login |

Disk: about 400 MB of Python packages in total (170 MB of it for ML).

## Small accounts ($1,000)

Same rules, same code - the numbers below are what they mean at $1,000
(one-year mechanical backtest, gold ~$4,300, 25-point spread).

| Item | At $1,000 |
|---|---|
| Lot per trade | 0.03 (risks $18 = 1.8%); 0.02 below $900, 0.01 below $600; under $300 even 0.01 is more than 2% |
| Spread cost per trade | about $0.75 (4% of the risk) - keep the spread at 25-30 points or less |
| Margin per 0.03 lot | 1:500 about $26 - 1:100 about $129 - **1:20 about $645** (retail limit for gold in Australia, EU, UK) |
| Backtest, up to 5 per direction (leverage 1:100+) | +58% in the year, worst drawdown 24%, 230 trades |
| Backtest, one position at a time (what 1:20 allows) | +46%, worst drawdown 14%, 155 trades (about 3 a week) |
| Claude calls | about 96 a week with the pre-screen (205 without) |
| Claude cost (Opus 5, estimate) | about $8 a week with the pre-screen, $17 without; the log shows the real tokens per call (`Claude call: ... tokens`) |

**The API bill is what decides a $1,000 account.** The backtest's average
gain is about $11 a week at this size, so Opus 5 costs roughly 75% of it
with the pre-screen and more than all of it without. A year simulated 4,000
times (backtest trades resampled, real lot steps, API cost taken weekly):

| Setup | Median after a year | Chance of ending below $1,000 |
|---|---|---|
| No API cost (reference) | $1,636 | 12% |
| Opus 5, pre-screen on (default) | $1,045 | 47% |
| Opus 5, pre-screen off | $399 | 85% |
| Sonnet 5, pre-screen on (`--model claude-sonnet-5`, untested judgment) | $1,405 | 23% |
| Opus 5, pre-screen on, if the real edge is half the backtest's | $730 | 73% |

Options, safest first: run only the Telegram-signal side (no API cost) and
keep Claude paused (`PauseClaudeHab`) until the account is about $3,000+;
or run Claude on demo with the same $1,000 balance for 2-4 weeks and read
the real cost from the log and the scorecard; or try a cheaper model with
`--model`. The margin guard keeps a small high-risk account from a margin
call; with 1:20 it simply allows one gold position at a time.

## Weekly scorecard (is it working?)

Every 7 days (and any time with `python goldtrader.py scorecard`) the real
closed trades of the last 120 days are read from MT5 - Claude's, the
Telegram signals' and both together - and sent to your Telegram: trades,
win %, net, average result in **R** (1R = the stop the trade was opened with: $6, or a Telegram signal's own stop), profit factor,
worst drawdown and the chance that there is no real edge. The verdict uses
rules fixed before any demo trade:

| Verdict | When | What to do |
|---|---|---|
| TOO EARLY | fewer than 30 trades | keep running |
| NOT PROVEN YET | anything in between | keep running |
| ON TRACK | average R above 0 and at most 20% chance of no edge | demo is working; go small on real money only after that |
| STOP AND REVIEW | 80%+ chance of no edge, or a 10R drawdown (about 20% at 2% risk) | pause that source (`PauseClaudeHab` / `PauseTelHab`) and look at why |

## Broker clock

MT5 gives every bar, tick and deal time on the broker's server clock. The
program converts them to real UTC: by default it assumes the usual gold
broker clock (New York + 7 hours, i.e. UTC+2 in winter, UTC+3 in summer),
and a live tick confirms it or switches to your broker's fixed offset (the
log says `Broker server clock: ...`). The trading hours use your PC's UTC
clock (Python and the EA alike), so keep Windows time synced (Settings -> Time -> Sync now).

## Trade journal in Google Drive

Every 5 minutes the trading program writes three CSV files into
`logs\journal` and copies them to `MyMQChartDrive\GoldTrader` in your Google Drive
(found by itself: `G:\My Drive\...` or `G:\MyDrive\...`, any drive letter)
(created if `MyMQChartDrive` exists; Drive for Desktop uploads them). Only a
file that changed is rewritten.

| File | One row per | Columns |
|---|---|---|
| `GoldTrader_trades.csv` | closed trade, Claude and Telegram, rebuilt from MT5's own history | ticket, source, direction, lots, open/close time (UTC and Oman), minutes open, entry, exit, first stop and its distance (1R), exit reason (stop loss / closed by the EA / closed by you (PC, phone, web) / stop out), move in $, result in R, profit, swap, commission, net, note |
| `GoldTrader_claude_decisions.csv` | Claude evaluation | a copy of `logs/decisions.csv` |
| `GoldTrader_telegram_signals.csv` | Telegram message the EA received | a copy of the EA's signal log: action, direction, copied or why not |

The note `stop moved by hand` marks an exit on a stop the EA never sets:
the EA opens at the first stop (-1R) and only moves it to the +$6 lock or
beyond, so an exit between 80% of the way to the first stop and 70% of the
way to the lock was a stop you moved. R is measured against each trade's
own first stop (`stop_distance`). To analyse, ask Claude to read the files from your
Google Drive. Other Drive path: `--journal-folder "X:\path"` in `start.bat`
(`off` = logs only). Trading never waits on it or fails because of it.

## Bitcoin (BTCUSD) instance

`start.bat` (`goldtrader.py start-all`) runs gold and a second, independent
Bitcoin instance in one window (`BTC_ARGS`; `off` = gold only), each its own
program with its own restart and `instance.lock`, with `--profile btc`
(`app/profiles.py`): BTCUSD, magic 20260931, `logs\btc\`, `BTC_` journal
files, no Telegram side; its own ML retrain, conviction report and scorecard
jobs (no relay or price export). Stop 1.0 x M15 ATR14 within
0.20%-2.0% of price; **BTCTrader_EA** locks at +1R and trails 0.5R, where R is
each trade's own opening stop; 2% risk, 5% daily cap, 3 per direction (while
gold is closed, Friday 17:00 - Sunday 18:00 New York: 10% and 5), 24/7,
spread limit 0.06% of price. Claude and the news check get Bitcoin-specific
wording and crypto feeds; gold's prompts are unchanged. Telegram:
`PauseBtcHab` / `ResumeBtcHab` (UnifiedTrader_EA inputs `InpBtcMagicNumber`,
`InpBtcPauseFilename`); `PauseHab` / `ResumeHab` include BTC.
`backtest_btc.bat` = `backtest --profile btc --from-mt5 --months 12
--mechanical --to-drive`. BTCTrader_EA also writes `BTCUSD_M5/M15/H1.csv`
(5000 bars each) into the Drive price folder, like gold's; `backtest
--csv-folder <folder>` replays those files (`--csv-prefix`, default the
symbol + `_`; `BTC_prices_` for `--to-drive` files; H4/D1/W1 built from H1
when absent). Full guide: [`BTC.md`](BTC.md).

## Signal-history replay (test a provider in hours)

`replay_signals.bat` (or `python goldtrader.py replay-signals --months 3`):

1. Reads the signal channel's past messages with your own Telegram login
   (`relay\tg_relay_bridge.session`; asks for phone + code once if there is
   none). The chat: `--chat @name` (or id), else `TELEGRAM_SOURCE_CHANNELS`
   in `keys.txt`, else a numbered list to pick from (`--pick` always shows it).
2. Loads XAUUSD M1 prices for the period from MT5 (MT5 must be open).
3. Replays every message the way UnifiedTrader_EA reads it - same message
   filter and parser, Oman hours, M15/H1 filter, $20 zone check, limit /
   market / stale, 240-min pending expiry, 5 per direction, 10% daily cap
   and budget, 2% lot sizing, $6 lock and $3 trail, CLOSE / CANCEL messages.
4. Three versions: `fixed` ($6 stop), `signal` (the signal's stop when
   $3-$20 away - the EA as shipped) and `provider` (reference only: the
   signal's own stop and first target, no lock/trail).
5. Writes `logs\replay\` and copies it to Drive as `GoldTrader_replay_summary.txt`,
   `_trades.csv`, `_messages.csv`, `_prices_M1.csv`.

Options: `--months 6`, `--equity 50000`, `--spread 0.30`, `--no-htf`,
`--messages-file` / `--prices-file` (replay saved files, no Telegram/MT5).
Not modelled: news blackout, slippage beyond the spread, margin guard,
edited messages; on M1 bars a bar that touches both the stop and the
lock/target counts as the stop (pessimistic). If MT5 returns fewer M1
bars than the period, it says so: Tools -> Options -> Charts -> Max bars in
chart = Unlimited, restart MT5, scroll an M1 chart back, run again.

## Price files for XTR (Drive)

Every M1 close the EA writes the last 200 closed M5/M15/H1 bars (BTCTrader_EA:
5000 bars, `BTCUSD_` files, same folder)
(`datetime,open,high,low,close,volume`, datetime in true UTC, ascending) plus
`XAUUSD_manifest.json` into `Common\Files\XTR_Data`, and with
`InpXtrExportCopyTo` also into your Drive folder (needs Allow DLL imports -
only Windows' own file copy is used). A CSV is rewritten only when it has a
new bar; the manifest every minute. If the PC clock disagrees with the
broker by more than 2 minutes off a clean time zone, that minute is skipped
rather than mislabeled.

**VPS without Drive for Desktop:** Google Cloud Console -> enable the
Google Drive API -> service account -> JSON key -> share the Drive folder
with the service account's email (Editor) -> test:
`python goldtrader.py xtr-export --check --upload-drive --drive-folder-id <id> --drive-credentials C:\keys\drive.json`
-> `settings.ini [xtr_export] enabled = true` with those options -> EA
`InpXtrExport = false`.

## Dashboard on your phone (optional)

A web page on this PC's IIS: equity and today's change, open positions,
closed trades per source, Claude's recent decisions (and why no trade),
the Telegram signal log (and why a signal was not copied) and the live
scorecard. Read-only - nothing on it can trade. It refreshes every minute
and turns red ("Not updating") if `start.bat` stops.

1. **Right-click `dashboard_setup.bat` -> Run as administrator.** It turns
   on IIS + ASP.NET 4.8, creates the site `GoldTrader` on port **8080**
   pointing at this `dashboard` folder, gives the site read access to
   `dashboard` and `logs` only (never `keys.txt`), opens port 8080 to your
   home network and Tailscale only, and asks for the page's password.
2. Open `http://localhost:8080` on the PC and sign in. Without data yet,
   "See the page with sample data" shows what it will look like.
3. **Phone at home:** `http://<PC name>:8080` on the same Wi-Fi.
4. **Phone anywhere:** install **Tailscale** (free) on the PC and the
   phone, sign in to both with the same account, then open
   `http://<PC name>:8080`. The connection is encrypted and nothing is
   opened to the internet. Don't forward port 8080 on your router: that
   exposes the page to everyone, over plain HTTP.

Change the password: `python goldtrader.py dashboard-password`. Five wrong
passwords lock sign-in from that device for 15 minutes. The data comes from
`logs/status.json`, written by the trading program once a minute; if the
site is not in `GoldTrader\dashboard`, set `LogsFolder` in
`dashboard\Web.config`.

## Backtest

`python goldtrader.py backtest --from-mt5 --start 2026-03-16 --end 2026-09-23 --mechanical`
(free; runs the live defaults; the mechanical stand-in votes the same three
legs Claude is told to use - it tests the rules, not Claude's judgment).
Add `--trade-hours any --friday-cutoff off --max-spread 0` to compare
without the tactics. Results on a year of real prices:
[`BACKTEST_REPORT.md`](BACKTEST_REPORT.md).

## Honest limitations

- No backtest of Claude itself yet - the demo weeks are the real test.
- Over a year the defaults (no gate + trading hours) were positive in all
  three periods with a 9% chance of luck - the best result, not proof. The
  worst drawdown was 27% from the peak (32% at double spread).
- Very cost-sensitive: the $6 stop is about one M15 ATR - use a broker with
  gold spread of 25 points or less.
- The backtest is bar-level (not tick-level); spread is constant apart
  from the daily reopen; news spikes are not modelled.
- The EA's compile, WebRequest and DLL permissions, and the Telegram/Google
  logins can only be verified on your own PC.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python was not found" | Install Python 3.12+ 64-bit, tick "Add python.exe to PATH", reopen |
| "Cannot reach MetaTrader 5" | Start MT5, log in, wait for prices, XAUUSD in Market Watch; `start.bat` retries by itself |
| No trades for hours | Normal outside the windows (Telegram 06:00-23:00 Oman; Claude from 16:00 Oman) and at the weekend; Claude also needs 2 of 3 checks and a "full" verdict |
| A Telegram signal was not copied | The dashboard's Signals tab / the EA's Experts tab says why (e.g. "outside trading hours") |
| "Setting not understood" | A typo in `start.bat` `GT_ARGS` (times as HH:MM, e.g. `06:00-23:00`) |
| `check.bat` shows `MISSING` | `settings.bat` |
| No Telegram alert | `settings.bat` -> send the test message; you must have messaged the bot once |
| EA: "InpBotToken is empty" | EA Inputs -> bot token |
| No signals copied | `InpChannelId1` = the relay group id; bot is admin of the relay group; the `start.bat` window shows `Companion programs: relay_bridge` and no relay error; `relay_login.bat` lists the ids again |
| Drive folder empty | EA Common tab -> Allow DLL imports; exact path from Explorer; Experts tab `XtrBarExport:` lines |
| Relay "not logged in" | `relay_login.bat` |
| Dashboard "Not updating" | `start.bat` or MT5 stopped on the PC |
| Dashboard "No report yet" / page error on the PC | Run `dashboard_setup.bat` as administrator again; `start.bat` must be running |
| Phone cannot open the dashboard | Same Wi-Fi or Tailscale on both; `http://` not `https://`; port 8080 |
| Relay "Source channel ... not found" / "Relay group ... not found" | Join the channel with the same Telegram account; check the ids in `keys.txt` against the list `relay_login.bat` prints. Ids (`-100...`) and `@names` both work |
| News check "0 of 5 feeds" | Firewall/antivirus blocking the feeds; trading continues without it |
| Compile errors in `setup.bat` | Close MetaEditor and run `setup.bat` again; send the error line |
| Change a key | `settings.bat` |
| Everything off quickly | Telegram `PauseHab` (closes + stops), then close `start.bat` |

Commands (in this folder): `python goldtrader.py setup | install-mt5 |
settings | check | test-alert | test-feeds | test-news buy | relay-login |
once | start | backtest | replay-signals | scorecard | xtr-export | dashboard-password |
update | drive-copy | test`. `update` (= `update.bat`) installs the newest
version of the solution's GitHub branch: your keys, logins, logs and edited
start/settings files are kept, a version that fails its self-tests is rolled
back (backups in `logs\update_backup\`), and the installed version is
remembered in `logs\update_state.json`. `drive-copy` (also run by `setup.bat` after its tests pass)
mirrors the solution to `G:\My Drive\MyTraderbyClaude\GoldTrader` - code,
EAs, presets, launchers, docs, `settings.ini`; never `keys.txt`, `*.session`,
the dashboard password or `logs\`. `--to <folder>` if Drive is elsewhere.
