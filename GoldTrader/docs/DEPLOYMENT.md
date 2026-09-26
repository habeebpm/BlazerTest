# GoldTrader - deployment guide

One page, top to bottom, from an empty PC to trading on demo. It follows
the order of the [`README`](../README.md) steps and adds what to check after
each one. You're ready when every **You should see** line is true. Details
and troubleshooting are in [`REFERENCE.md`](REFERENCE.md).

## 0. At a glance

| | Value | Set in |
|---|---|---|
| Symbol / chart | XAUUSD, M15 | MT5 |
| Telegram signal hours | **06:00-23:00 Oman time, Mon-Fri** | EA `InpTradeHours=06:00-23:00`, `InpTradeUtcOffsetHours=4.0`, `InpTradeWeekdaysOnly=true` |
| Claude hours | **16:00-00:45 and 02:15-04:00 Oman** (summer), 17:00-01:45 and 03:15-05:00 (winter) = 08:00-16:45 and 18:15-20:00 New York | `app/config.py` `trade_windows` (New York time, follows daylight saving by itself) |
| Friday | Claude: no new entry from 16:00 New York (00:00 Oman in summer); Telegram: last entries at 23:00 Oman | `app/config.py` `friday_cutoff_ny`; EA hours |
| Claude trades when | 2 of 3 checks agree (1 confirmed) **and** Claude says `full` | `app/config.py` |
| Risk per trade | 2% of equity | EA + `app/config.py` |
| Stop / lock / trail | Claude: $6 SL, locked at +$6, then trailed $3 (at the 0.01 reference lot) | EA + `app/config.py` |
| Telegram exits | two halves, same stop: half A closes at +$4 (take-profit); half B goes to break-even at +$4, then trails $3 | EA `InpTelegramSplit=true`, `InpTelegramTp1Dollars=4.0`, `InpTelegramTrailDollars=3.0` |
| Telegram stop | the signal's own stop when $3-$20 from the entry (lot resized to keep 2% risk), else $6 | EA `InpTelegramUseSignalSl=true`, `InpSignalSlMinDistance=3.0`, `InpSignalSlMaxDistance=20.0` |
| Positions | max 5 per direction, both sources together | EA + `app/config.py` |
| Daily loss cap | 10% of the day's starting equity | EA + `app/config.py` |
| Magic numbers | Claude 20260921, Telegram 20260922 | EA + `start.bat` |
| Spread limit | 50 points | EA + `app/config.py` |
| Open trades | managed around the clock, whatever the hours | EA |

The rule values are the same in the EA preset and in `app/config.py`; the
self-test (`setup.bat`) fails if they drift apart. **Don't change them.**

## 1. Before you start

- [ ] MT5 installed, logged in to a **demo** account, XAUUSD in Market Watch with moving prices.
- [ ] Gold spread 25-30 points or less (Market Watch -> spread column).
- [ ] Your broker's leverage for gold is known (at 1:20 a $1,000 account holds one trade at a time).
- [ ] Python 3.12+ 64-bit from python.org, **"Add python.exe to PATH"** ticked.
- [ ] Windows: Settings -> System -> Power -> **Sleep = Never**; Settings -> Time -> **Sync now**.
- [ ] Telegram bot from @BotFather (`/newbot`) -> bot token.
- [ ] Your chat id: message the bot once, open `https://api.telegram.org/bot<TOKEN>/getUpdates` -> the number after `"chat":{"id":`.
- [ ] A **private** Telegram group (e.g. "Gold relay") with your bot as **admin**.
- [ ] Your own Telegram account has **joined** the signal channel(s).
- [ ] `api_id` and `api_hash` from <https://my.telegram.org> -> API development tools.
- [ ] Anthropic API key from <https://console.anthropic.com/>, account has credits.

Put the GoldTrader folder somewhere local, e.g. `C:\GoldTrader` - **not**
inside Google Drive, Dropbox or OneDrive (it will hold your keys).

## 2. Install - `setup.bat`

Close MetaEditor, then double-click `setup.bat`.

**You should see:** `ALL SELF-TESTS PASSED`, then both EAs compiled with
`0 error(s)`. `keys.txt` now exists in the GoldTrader folder.

If MT5 is not found it asks for the data folder (MT5: File -> Open Data
Folder).

## 3. Keys and Telegram - `check.bat`

Double-click `check.bat` and answer:

1. Anthropic API key, bot token, your chat id.
2. Relay: **Enter** (yes) -> `api_id`, `api_hash`, signal channel(s) (`@name` or `-100...` id, up to 3) and the relay group (`-` if you don't know its id yet).
3. **Enter** to log the relay in now: phone number + the code Telegram sends. It lists your chats with their ids; type the relay group's id if asked.
4. **Y** to the test message.

Everything is saved in `keys.txt`. Never paste these values into a chat
or an email, and never share the file.

**You should see:** the test message in Telegram; at the end the number to
use for `InpChannelId1` (the relay group, e.g. `-1001234567890`). Run
`check.bat` again: every key `OK`, no `MISSING`, and `Connected` to MT5.

## 4. MT5 and the EA

1. Tools -> Options -> Expert Advisors -> tick **Allow WebRequest** -> add `https://api.telegram.org`.
2. Open an **XAUUSD M15** chart -> drag **UnifiedTrader_EA** onto it.
3. **Inputs** -> **Load** `UnifiedTrader_EA_Default.set`.
4. Fill in `InpBotToken`, `InpControlChatId` (your chat id) and `InpChannelId1` (the relay group id).
5. Check the preset loaded:
   - `InpTradeHours=06:00-23:00`, `InpTradeUtcOffsetHours=4.0`, `InpTradeWeekdaysOnly=true`
   - `InpTelegramUseSignalSl=true`, `InpSignalSlMinDistance=3.0`, `InpSignalSlMaxDistance=20.0`
   - `InpRiskPercent=2.0`, `InpSlDollars=6.0`, `InpTp1Dollars=6.0`, `InpTrailDollars=3.0`, `InpMaxPositionsPerDirection=5`, `InpMaxDailyLossPct=10.0`
   - `InpTelegramMagicNumber=20260922`, `InpClaudeMagicNumber=20260921`
6. **Common** tab -> tick **Allow DLL imports** -> **OK**.
7. Toolbar -> **Algo Trading** on (green).

**You should see:** in the Experts tab, the EA starts with no error. Send
`Stats` to your bot: it answers with equity and the buttons (Pause/Resume,
Stats, News, Why).

The preset ships with `InpDryRun=true`: the EA only logs what it would do.
Leave it until step 7.

## 5. Dashboard on your phone (optional)

1. Right-click `dashboard_setup.bat` -> **Run as administrator**. It turns on IIS, creates the site `GoldTrader` on port 8080, gives it read access to `dashboard` and `logs` only, opens port 8080 to your home network and Tailscale only, and asks for a password (10+ characters). If Windows asks for a restart, restart and run it again.
2. On the PC: `http://localhost:8080` -> sign in.
3. Phone at home: `http://<PC name>:8080` on the same Wi-Fi.
4. Phone anywhere: install **Tailscale** on the PC and the phone (same account), then `http://<PC name>:8080`. **Don't** forward port 8080 on your router.

**You should see:** the sign-in page, then the report. Before `start.bat`
has run it says "No report yet" - that's normal.

The password is stored only as a salted hash in
`dashboard\App_Data\password.txt`. Change it with
`python goldtrader.py dashboard-password`.

## 6. First start in dry-run

To watch everything without real orders first:

1. Open `start.bat` in Notepad and remove `--live` from `GT_ARGS` (keep `--shared-cap-magic 20260922`).
2. Double-click `start.bat`.

**You should see** in the `start.bat` window:
- `Companion programs: relay_bridge ...` and `Relaying 1 source chat(s)` (no `not found`);
- `Broker server clock: ...`;
- inside Claude's hours, `Claude call: ... tokens` when 2 of 3 checks agree, otherwise `No evaluation this cycle (no Claude call): ...` with the reason;
- outside Claude's hours, `outside trading hours` - that's normal.

Your channel's next trade message appears in the relay group within seconds,
and the EA's Experts tab logs it (copied, or why not). The dashboard's
**Signals** and **Claude** tabs show the same.

## 7. Take off on demo

1. EA **Inputs** -> `InpDryRun` = `false` -> OK.
2. `start.bat`: `GT_ARGS` back to `--live --shared-cap-magic 20260922`. Close the window and start it again.

**You should see:** the next copied signal or `full` verdict turns into a
position with the right magic number (20260922 Telegram, 20260921 Claude),
its SL set. Claude's entries also send a Telegram alert.

## 7b. Trade journal in Google Drive

Nothing to set up if Drive for Desktop is running with the `MyMQChartDrive`
folder (the EA's price files already go there). A few minutes after
`start.bat` starts, **You should see** `MyMQChartDrive\GoldTrader` in Drive
with `GoldTrader_trades.csv`, `GoldTrader_claude_decisions.csv` and
`GoldTrader_telegram_signals.csv` (Bitcoin: `BTC_trades.csv`, ...). Ask
Claude to analyse them from Drive.

**Logs in Drive** (for Claude to read - no copying text by hand): everything
the `start.bat` window shows is also saved to `logs\run\start_<date>.log`
(one file a day, 7 kept), and every 5 minutes (and when it stops) copied to
`G:\My Drive\MyTraderbyClaude\Logs` with `autostart.log`, the ML /
calibration / scorecard job logs (`gold_...`, `btc_...`) and today's MT5
Experts log (`MT5_Experts_<date>.log` - every EA message). Never keys or
passwords. Just tell Claude "check the logs".

## 7c. Bitcoin (optional)

A second instance for BTCUSD with its own rules and no Telegram signals:
BTCTrader_EA on a BTCUSD chart; `start.bat` runs it in the same window as gold (`BTC_ARGS`). Steps and checks:
[`BTC.md`](BTC.md).

## 7d. Autostart - after a restart and if the window closes

Double-click **`autostart.bat`** once. **You should see** `Autostart ON`. From
then on (a Windows scheduled task "GoldTrader", no window of its own):

- at every Windows sign-in, after 1 minute, `start.bat` opens by itself; it
  starts MT5 too if MT5 is not open yet (MT5 reopens its charts and EAs; keep
  **Algo Trading** on when you close MT5);
- if the GoldTrader window closes unexpectedly (closed by mistake, crashed),
  it is reopened within 5 minutes - never a second copy;
- stopping **on purpose** stays stopped: **Ctrl+C** in the window, or
  **`stop.bat`** and then close the window. Double-click `start.bat` to run
  again. A mistyped setting in `start.bat` also pauses it (no restart loop);
- `update.bat` pauses it while updating and re-arms it at the end - GoldTrader
  then reopens by itself within 5 minutes.

Unattended restarts (Windows Update at night) also need Windows to sign in by
itself: Settings -> Accounts -> Sign-in options -> **Use my sign-in info to
automatically finish setting up after an update** = On. For every restart,
Windows automatic sign-in (`netplwiz`) does it, but then anyone at the PC is
signed in - your choice. Check: `python goldtrader.py autostart status`;
undo: `python goldtrader.py autostart off`. Each automatic start is listed in
`logs\autostart.log`.

## 8. Every day

- Keep **MT5** and the **`start.bat` window** open. It restarts the program by itself after an MT5 or internet drop.
- After a PC restart: open MT5 -> Algo Trading on -> `start.bat`, in that order -
  or let the autostart do it (7d).
- Glance at the dashboard or send `Stats`. A red "Not updating" on the dashboard means `start.bat` or MT5 stopped.
- An alert "Claude entries STOPPED" means no credits or a bad API key: fix it (`settings.bat`), then restart `start.bat`. Telegram signals keep working meanwhile.

| Telegram button | Does |
|---|---|
| `PauseHab` / `ResumeHab` | **Emergency stop**: closes all and stops new entries / resume |
| `PauseTelHab` / `ResumeTelHab` | Telegram-signal entries only |
| `PauseClaudeHab` / `ResumeClaudeHab` | Claude entries only (open trades still managed) |
| `Stats` / `News` / `Why` | Results, economic calendar, Claude's last reasoning |

## 9. Every week: the scorecard

Every 7 days the scorecard arrives in Telegram (also on the dashboard,
and any time with `python goldtrader.py scorecard`), separately for Claude
and Telegram signals:

| Verdict | Do |
|---|---|
| TOO EARLY (under 30 trades) | keep running |
| NOT PROVEN YET | keep running |
| ON TRACK | the demo is working |
| STOP AND REVIEW | pause that source (`PauseClaudeHab` / `PauseTelHab`) and look at why |

Also compare Claude's cost with its gain: about 92 calls a week, roughly
$8 at Opus 5 in the backtest - the log shows the real tokens per call.

## 10. Going live

Only after **2-4 weeks of demo with ON TRACK and 30+ trades** - never on
one good week.

1. Log MT5 into the real account. Same EA, same preset, same `start.bat`.
2. Check the real account's gold spread and leverage again.
3. Start small. Under about $3,000 the Claude bill is the biggest cost:
   the safest start is Telegram signals only, with `PauseClaudeHab`.
4. Keep the weekly scorecard routine.

## 11. Updating to a new version

`start.bat` says so when there is one: `*** An update is available (...) -
press Ctrl+C here, then double-click update.bat. ***`

1. Press **Ctrl+C** in the `start.bat` window (or `stop.bat`) - open trades stay
   managed by the EAs. `update.bat` refuses while one is still running.
2. Close MetaEditor, double-click **`update.bat`**. It downloads the latest
   version from GitHub and installs it:
   - lists what changed, then `N file(s) updated`;
   - never touches `keys.txt`, `logs\`, `relay\*.session`,
     `dashboard\App_Data\password.txt` or files of your own;
   - `start.bat` / `settings.ini`: if you changed one, yours
     is KEPT and the new one saved next to it as `<name>.new`;
   - runs every self-test -> `ALL SELF-TESTS PASSED`; if a test fails, the
     previous version is put back automatically, and the reason is saved to
     `logs\update.log` + `logs\selftest_*.log` and copied to Google Drive
     `MyTraderbyClaude\Logs` - tell Claude "check the logs";
   - overwrites the copy in Google Drive (`Google Drive copy:
     G:\My Drive\MyTraderbyClaude\GoldTrader - N updated ...`);
   - recompiles the EAs in MT5 -> `0 error(s)` (MT5 reloads them on the
     charts by itself).
   `GoldTrader is up to date` = nothing to do.

   Without internet access to GitHub, the old way still works: copy the new
   files over the folder (keep the files listed above) and run `setup.bat`.
4. In MT5, re-open the EA's inputs and check your values are still there
   (`InpBotToken`, `InpControlChatId`, `InpChannelId1` = the relay group id,
   `InpChannelId2`/`3` = 0, `InpDryRun`). New inputs of an update start at the
   preset's values - compare with step 4 above.
5. Start `start.bat`.

**The September 2026 update adds** (check after updating):

| What | Check |
|---|---|
| Telegram trades use the signal's own stop ($3-$20 away), lot resized to keep 2% risk | EA inputs `InpTelegramUseSignalSl=true`, `InpSignalSlMinDistance=3.0`, `InpSignalSlMaxDistance=20.0`; Experts tab at start: "Telegram entries use the signal's own stop ..." |
| Trade journal in Google Drive | `start.bat` log: `Trade journal every 5 min -> Google Drive MyMQChartDrive\GoldTrader (found automatically)`, then `Trade journal: Google Drive folder found - G:\My Drive\MyMQChartDrive\GoldTrader`; the folder appears in Drive within minutes |
| Dashboard shows the stop used on each copied signal | Signals tab: "stop: signal stop ..." or "stop: fixed $6.00" |
| Telegram signals as two halves: +$4 take-profit / break-even + $3 trail | EA inputs `InpTelegramSplit=true`, `InpTelegramTp1Dollars=4.0`, `InpTelegramTrailDollars=3.0`; Experts tab at start: "Telegram entries split in two halves ..."; each copied signal shows two positions (one with a TP); Signals tab: "split: ... lots TP +$4.00, ... lots break-even + $3.00 trail" |

## 12. What stays on your PC only

| File | Why |
|---|---|
| `keys.txt` | API key, bot token, Telegram ids |
| `relay\tg_relay_bridge.session` | Your Telegram login for the relay |
| `dashboard\App_Data\password.txt` | The dashboard password's hash |

All three are in `.gitignore` and never uploaded - neither to GitHub nor to
the Google Drive copy (`MyTraderbyClaude\GoldTrader` holds only code, EAs,
presets, launchers, docs and `settings.ini`; `logs\` stays here too). The
dashboard site can read only the `dashboard` and `logs` folders.

## 13. Stop everything

`PauseHab` in Telegram (closes all positions, stops new entries), then
**Ctrl+C** in the `start.bat` window (or `stop.bat`, then close it) - just
closing the window counts as unexpected, and the autostart (if on) reopens
it. Remove the EA from the chart to stop the Telegram side completely.
