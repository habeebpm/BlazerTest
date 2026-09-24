# GoldTrader - setup guide

Everything is in this folder. You only double-click the `.bat` files.

| File | What it does |
|---|---|
| `setup.bat` | Installs everything and puts the EAs into MT5 |
| `check.bat` | First time: asks your settings. Later: checks everything |
| `start.bat` | Runs the system - leave its window open |
| `settings.bat` | Change a setting later |

## Before you start (once)

1. **MetaTrader 5** logged in to a **demo** account with XAUUSD.
2. **Python 3.12+ (64-bit)** from python.org - tick **"Add python.exe to PATH"**.
3. **Telegram bot:** @BotFather -> `/newbot` -> copy the **bot token**.
4. **Your chat id:** message your bot, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` -> copy the number after `"chat":{"id":`.
5. **Anthropic API key** from <https://console.anthropic.com/>.
6. Make your bot an **admin** of your signal channel (not your channel? see Relay below).
7. Windows: **Sleep = Never**.

## Step 1 - Install

Close MetaEditor -> double-click **`setup.bat`** -> wait for
`ALL SELF-TESTS PASSED` and `0 error(s)`.
Run it again after every update.

## Step 2 - Settings

Double-click **`check.bat`** -> answer the questions -> **Y** to the test
message (it must arrive in Telegram). Saved for good.

## Step 3 - MT5

1. **Tools -> Options -> Expert Advisors** -> tick **Allow WebRequest** -> add
   `https://api.telegram.org`.
2. Open **XAUUSD M15** -> drag **UnifiedTrader_EA** on it -> **Inputs** ->
   **Load** `UnifiedTrader_EA_Default.set`.
3. Fill in `InpBotToken` and `InpControlChatId` (your chat id).
4. **Common** tab -> tick **Allow DLL imports** -> **OK** -> **Algo Trading** on.
5. **Experts** tab -> copy the id from `message from chat <id>` (post in your
   channel if nothing shows) -> **Inputs** -> `InpChannelId1` = that id.

## Step 4 - Start

1. EA **Inputs** -> `InpDryRun` = `false`.
2. Double-click **`start.bat`**. Done.

## What happens then

| | |
|---|---|
| Telegram signals | Copied when they arrive |
| Claude trades | 08:00-16:45 and 18:15-20:00 New York time, about 4 a week |
| Every trade | 2% risk, stop $6, locked at +$6, then trailed $3; max 5 per direction |
| Protection | 10% daily loss cap, spread limit, news pause, margin guard, no Friday-evening entries |
| Every week | A scorecard in Telegram: TOO EARLY / NOT PROVEN YET / ON TRACK / STOP AND REVIEW |

Check your broker's leverage for gold: at 1:20 a $1,000 account holds one
trade at a time (the margin guard handles it).

## Every day

- Keep **MT5** and the **`start.bat`** window open (it restarts itself if MT5 or the internet drops).
- After a PC restart: open MT5 (Algo Trading on) -> `start.bat`.

| Telegram button | Does |
|---|---|
| `PauseHab` / `ResumeHab` | Stop (closes all) / resume everything |
| `PauseTelHab` / `ResumeTelHab` | Telegram-signal trades only |
| `PauseClaudeHab` / `ResumeClaudeHab` | Claude trades only |
| `Stats` / `News` / `Why` | Results, economic calendar, Claude's last reasoning |

## Going live

Only after 2-4 weeks of demo with the scorecard ON TRACK: log MT5 into the
real account, keep the same settings, start small.

---

## Optional

**Relay** (a channel you are not admin of): make a private group with your
bot as admin -> <https://my.telegram.org> -> API development tools -> copy
`api_id` + `api_hash` -> `settings.bat` -> **y** to relay -> `relay_login.bat`
-> `InpChannelId1` = the group id -> restart `start.bat`.

**Price files to Google Drive:** already copied to `G:\MyDrive\MyMQChartDrive`.
If your Drive folder path is different, change `InpXtrExportCopyTo`; to turn
it off, leave it empty.

**Trade journal:** second XAUUSD chart -> **TelegramSMC_TradeLogger** ->
**Load** `TelegramSMC_TradeLogger_Unified.set`.

Details, options and troubleshooting: [`docs/REFERENCE.md`](docs/REFERENCE.md).
Backtest: [`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md).
