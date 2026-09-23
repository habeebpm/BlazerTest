# GoldTrader - reference

The setup steps are in [`../README.md`](../README.md). This page explains
what runs, how it decides, every safety limit, where files are, and what to
do when something goes wrong.

## What runs

| Part | Where | Does |
|---|---|---|
| **UnifiedTrader_EA** | MT5 chart | Copies Telegram trade signals (magic 20260922); manages exits of every position (Telegram and Claude's, magic 20260921); Telegram buttons; economic-calendar export; price files for Drive |
| **Trading program** (`app/main.py`, via `start.bat`) | Python | Each closed M15 bar inside the trading hours: builds a market snapshot, asks Claude for a verdict, applies the gates, sends Claude's entries |
| Relay bridge (`relay/`) | Python, optional | Forwards trade messages from a channel you are not admin of into your own group |
| Drive export (`drive_export/`) | Python, optional (VPS) | Price files to Google Drive without Drive for Desktop |
| ML retrain, conviction report | Python, automatic | Daily / weekly, see `settings.ini` |
| TelegramSMC_TradeLogger | MT5 chart, optional | Trade journal CSV (both sources) |
| Dashboard (`dashboard/`) | IIS, optional | Web page over the journal CSVs |

`start.bat` runs the trading program and every companion switched on in
`settings.ini`. It restarts the program after a crash or when MT5 was not
reachable (every 60 s), and switches off the console's QuickEdit so a click
in the window cannot freeze it.

## Trading rules and risk parameters (fixed)

| Rule | Value |
|---|---|
| Risk per trade | 2% of equity (lot sized from it) |
| Stop loss | $6 at the 0.01 reference lot (a fixed price distance) |
| TP1 | at +$6 the SL is locked there (no broker TP) |
| Trail | $3 behind price after TP1 |
| Positions per direction | max 5, shared by both sources |
| Daily loss cap | 10% of the day's starting equity - no new entries after it; each entry must also fit the remaining 10% budget including open risk |

Python (`app/config.py`) and the EA preset already carry the same numbers,
magic numbers and shared file names (checked by `goldtrader.py test`).

## How a Claude entry is decided

1. **Before calling Claude** (no cost if blocked): pause switch
   (`PauseClaudeHab`), daily cap, **trading hours, Friday cutoff and spread
   guard** (see Entry tactics below), news blackout (MT5 economic calendar,
   15 min around high-impact USD events), daily trade limit (off by
   default), the XTR pre-check (M15 and H1 clearly against each other =
   nothing to trade) and, if switched on, the ADX trend filter.
2. **Snapshot:** M15/H4/D1/W1 indicators, smart-money structure (order
   blocks, fair-value gaps, liquidity sweeps, premium/discount), key levels,
   session, calendar, the XTR M5/M15/H1 alignment read, recent performance
   and - once trained - the ML win probability.
3. **Claude's verdict:** trend / momentum / strength legs, conviction
   (`full` / `partial` / `none`), reasoning. Only `full` trades.
4. **XTR alignment gate** (`--xtr-gate`, default `require_alignment`):
   each timeframe is bullish/bearish only when EMA9 vs EMA21, RSI14 vs 50 and
   the MACD histogram all agree. `block_opposed` refuses entries against a
   clear M15/H1 and the two failing momentum patterns;
   `require_alignment` additionally needs the M5 trigger and one agreeing
   HTF (the lowest-risk setting in the backtest). After 2 losses in a row on
   the same setup and direction within 1 ATR, that setup stands down until a
   decisive breakout or a higher timeframe turns. Never changes lot, SL or TP.
5. **Breaking-news check** (free RSS feeds, one short Claude call) right
   before the order - a surprise against the trade blocks it; feeds down =
   trades anyway and says so.
6. Order sent; Telegram alert with entry, SL, TP1 and Claude's targets.

Telegram signals (EA side) are copied when they parse as a trade and no
clear M15/H1 is against them (`InpXtrHtfFilter`), inside the same position
cap, daily cap, news filter and spread limit (`InpMaxSpreadPoints`, 50).
Greetings, mood posts, long commentary, videos, audio and stickers are
ignored. The trading hours apply to Claude's entries only - the signal
provider chooses their own timing.

## Entry tactics (when Claude may trade)

Tested on six months of real prices ([`BACKTEST_REPORT.md`](BACKTEST_REPORT.md)).
They only decide *whether* an entry is allowed - lot, SL, TP1, trail and the
position cap never change.

| Tactic | Default | Why |
|---|---|---|
| Trading hours | 08:00-16:45 and 18:15-20:00 **New York time** | Asian-session and London-morning entries lost in every test; these hours halved the drawdown under every XTR setting and skip about half of the paid Claude calls |
| Friday cutoff | no new entry from 16:00 New York on Friday | A $6 stop cannot protect a position over the weekend gap |
| Spread guard | no entry above 50 points | Reopen and news spikes; 50 points is already 8% of the $6 risk |
| Trend filter | off (`--min-adx 25` to try it) | Helped Mar-Jul, not Aug-Sep |

New York time follows US daylight saving by itself. In your clock: UTC
12:00-20:45 and 22:15-24:00 from March to early November, one hour later in
winter. Outside the hours the log says `No evaluation this cycle (no Claude
call): outside trading hours` - that is normal.

Change them in `start.bat` (`GT_ARGS`): `--trade-hours "08:00-16:45"`
(`any` = all day), `--friday-cutoff off`, `--max-spread 40`,
`--min-adx 25`. A mistyped value stops `start.bat` with a clear message
instead of restarting.

## Settings

- **Keys / ids** (saved permanently in your Windows user account):
  `ANTHROPIC_API_KEY`, `TELEGRAM_ALERT_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`,
  and for the relay `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
  `TELEGRAM_SOURCE_CHANNELS`, `TELEGRAM_RELAY_GROUP`. Asked by the first
  start; change with `settings.bat`; see them with `check.bat`.
- **`settings.ini`:** companion programs on/off (`enabled`), extra options
  (`args`), schedule (`every_days`).
- **`start.bat`:** the trading program's options (`GT_ARGS`). Everything
  `python goldtrader.py start --help` lists can go there.
- **EA inputs:** in MT5 (preset `UnifiedTrader_EA_Default.set`).

## Files

| Where | What |
|---|---|
| `logs/decisions.csv` | Every Claude evaluation, executed or not |
| `logs/trades.csv` | Every order sent |
| `logs/ml_snapshots.csv`, `logs/ml_win_probability_model.joblib` | ML data + model (~0.3 KB per trade, ~50 KB model) |
| `logs/ml_retrain.log`, `logs/calibration_report.log` | Output of the automatic jobs |
| `logs/day_state.json`, `logs/xtr_state.json`, `logs/services_state.json` | State kept across restarts |
| MT5 `MQL5\Files\TelegramSMC_Signals.csv` / `..._Results.csv` | EA signal log / trade journal |
| MT5 `Common\Files\XTR_Data\` | Price files (and your Drive folder if set) |
| `relay/tg_relay_bridge.session` | The relay's Telegram login |

Disk: about 400 MB of Python packages in total (170 MB of it for ML).

## Price files for XTR (Drive)

Every M1 close the EA writes the last 200 closed M5/M15/H1 bars
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

## Dashboard (optional)

1. Windows features: **Internet Information Services** + **ASP.NET 4.8**.
2. Copy `dashboard\` to `C:\inetpub\wwwroot\dashboard\`.
3. `Web.config`: `SignalsCsvPath` / `ResultsCsvPath` = the two MT5
   `MQL5\Files\TelegramSMC_*.csv` files (File -> Open Data Folder).
4. IIS Manager -> `dashboard` -> Convert to Application -> open
   `http://localhost/dashboard/Dashboard.aspx`. No login: keep it off the internet.

## Backtest

`python goldtrader.py backtest --from-mt5 --start 2026-03-16 --end 2026-09-23 --mechanical`
(free; runs the live defaults; the mechanical stand-in votes the same three
legs Claude is told to use - it tests the rules, not Claude's judgment).
Add `--trade-hours any --friday-cutoff off --max-spread 0` to compare
without the tactics. Results on six months of real prices:
[`BACKTEST_REPORT.md`](BACKTEST_REPORT.md).

## Honest limitations

- No backtest of Claude itself yet - the demo weeks are the real test.
- On six months no setting showed a statistically reliable edge; the
  defaults (`require_alignment` + trading hours) had the lowest drawdown
  (9.5%) and were positive in both halves, on only 25 trades.
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
| No Claude trades for hours | Normal outside 08:00-16:45 / 18:15-20:00 New York and on Friday evening; about one Claude trade a week is expected |
| "Setting not understood" | A typo in `start.bat` `GT_ARGS` (times as HH:MM, e.g. `08:00-16:45`) |
| `check.bat` shows `MISSING` | `settings.bat` |
| No Telegram alert | `settings.bat` -> send the test message; you must have messaged the bot once |
| EA: "InpBotToken is empty" | EA Inputs -> bot token |
| EA ignores the channel | `InpChannelId1` = the id from the Experts tab; bot is admin there (or use the relay) |
| Drive folder empty | EA Common tab -> Allow DLL imports; exact path from Explorer; Experts tab `XtrBarExport:` lines |
| Relay "not logged in" | `relay_login.bat` |
| News check "0 of 5 feeds" | Firewall/antivirus blocking the feeds; trading continues without it |
| Compile errors in `setup.bat` | Close MetaEditor and run `setup.bat` again; send the error line |
| Change a key | `settings.bat` |
| Everything off quickly | Telegram `PauseHab` (closes + stops), then close `start.bat` |

Commands (in this folder): `python goldtrader.py setup | install-mt5 |
settings | check | test-alert | test-feeds | test-news buy | relay-login |
once | start | backtest | xtr-export | test`.
