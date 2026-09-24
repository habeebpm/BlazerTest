# GoldTrader - setup guide

Everything is in this one folder. You only ever double-click the `.bat`
files here.

| File | What it does |
|---|---|
| `setup.bat` | Installs everything, tests it, puts the EAs into MT5 |
| `check.bat` | First time: asks your settings. Later: checks MT5 + settings |
| `start.bat` | Runs the system - leave its window open |
| `settings.bat` | Change a setting later (API key, Telegram ids, relay) |

## You need (once)

1. **MetaTrader 5**, logged in to a **demo** account with XAUUSD.
2. **Python 3.12+ (64-bit)** from python.org - tick **"Add python.exe to PATH"**.
3. **Telegram bot token:** Telegram -> **@BotFather** -> `/newbot` -> copy the token.
4. **Your chat id:** send your bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` - the number after
   `"chat":{"id":` is your chat id.
5. **Anthropic API key** (with credits) from <https://console.anthropic.com/>.
6. Your bot added as **admin** of your signal channel (not your channel? see
   Relay bridge below).
7. Windows: **Settings -> System -> Power -> Sleep = Never**.

Starting with a small account (about $1,000)? Read **Small accounts** in
`docs/REFERENCE.md` first - at that size the Claude API bill decides the result.

## Step 1 - Install

Close MetaEditor (MT5 may stay open) -> double-click **`setup.bat`** -> wait
for `ALL SELF-TESTS PASSED` and `0 error(s)` for both EAs.

- "Several MT5 installations found": run
  `python goldtrader.py install-mt5 --choice 1` in this folder.
- "MetaEditor not found": open `UnifiedTrader_EA` in MetaEditor, press **F7**.

## Step 2 - Your settings

Double-click **`check.bat`**. It asks everything in one go (API key, bot
token, chat id, relay yes/no). Answer **Y** to the test message - it must
arrive in Telegram. Saved permanently, also after a restart.

## Step 3 - MT5

1. **Tools -> Options -> Expert Advisors** -> tick **Allow WebRequest** ->
   add `https://api.telegram.org` -> OK.
2. Open an **XAUUSD M15** chart -> drag **UnifiedTrader_EA** onto it ->
   **Inputs** -> **Load** -> `UnifiedTrader_EA_Default.set`.
3. Fill in `InpBotToken` (bot token) and `InpControlChatId` (your chat id).
4. **Common** tab -> tick **Allow DLL imports** (only for the Google Drive
   copy below) -> **OK** -> turn on **Algo Trading**.
5. **Experts** tab -> find `message from chat <id>` for your signal channel
   (post something in it if nothing shows) -> EA **Inputs** ->
   `InpChannelId1` = that id -> **OK**.

## Step 4 - Start (demo)

1. EA **Inputs** -> `InpDryRun` = `false` -> **OK**.
2. Double-click **`start.bat`** and leave the window open.

Done. Machine learning and the weekly report run by themselves.

## What to expect

- **Telegram signals** are copied when they arrive (news, spread and XTR
  filters still apply).
- **Claude trades only 08:00-16:45 and 18:15-20:00 New York time** (UTC
  12:00-20:45 and 22:15-24:00 in summer, one hour later in winter), never
  on Friday evening, never when the spread is wide. Outside those hours the
  log says `outside trading hours` - that is normal, and it costs nothing.
- About **4 Claude trades a week**, with losing weeks and months in between
  (the one-year backtest's worst fall was 27% from the peak).
- Every trade: a stop of $6 (at 0.01 lot), locked at +$6, then trailed $3.
- Every week a **scorecard** arrives in Telegram with a plain verdict:
  TOO EARLY / NOT PROVEN YET / ON TRACK / STOP AND REVIEW (rules in
  `docs/REFERENCE.md`). Judge it by that, not by a single day.

## Every day

- Keep **MT5** and the **`start.bat`** window open. If MT5 or the internet
  drops, `start.bat` restarts itself every minute.
- **After a PC restart:** open MT5 (Algo Trading on) -> `start.bat`.

**Telegram buttons:**

| Button | Does |
|---|---|
| `PauseHab` / `ResumeHab` | Pause (closes + stops) / resume everything |
| `PauseTelHab` / `ResumeTelHab` | Telegram-signal trades only |
| `PauseClaudeHab` / `ResumeClaudeHab` | Claude trades only |
| `Stats` / `News` / `Why` | Equity + win %, economic calendar, Claude's last reasoning |

---

## Optional

**Relay bridge** (signal channel you are NOT admin of): create a private
Telegram group with your bot as admin -> <https://my.telegram.org> -> API
development tools -> copy `api_id` + `api_hash` -> `settings.bat` -> answer
**y** to the relay -> `relay_login.bat` (phone + code; it prints the chat
ids) -> EA `InpChannelId1` = the relay group id -> restart `start.bat`.

**Price files to Google Drive:** the preset already copies them to
`G:\MyDrive\MyMQChartDrive` (needs Allow DLL imports, step 3.4). Create that
folder in Google Drive; if Explorer shows a different path (e.g.
`G:\My Drive\MyMQChartDrive`), put that path in EA `InpXtrExportCopyTo`.
Files appear within a minute and update every minute. Not wanted: make
`InpXtrExportCopyTo` empty.

**Trade journal:** second XAUUSD chart -> **TelegramSMC_TradeLogger** ->
**Load** `TelegramSMC_TradeLogger_Unified.set`. Web dashboard: see
`docs/REFERENCE.md`.

**Real money** (only after a good demo): real account (small balance), EA
`InpDryRun` = `true`, remove `--live` in `start.bat`, run 1-2 days, check
the logs -> `InpDryRun` = `false`, put `--live` back, restart `start.bat`.

---

How it decides, every limit, all options, troubleshooting:
[`docs/REFERENCE.md`](docs/REFERENCE.md). One-year backtest:
[`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md).
