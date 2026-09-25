# GoldTrader - setup guide

Everything is in this folder. You only double-click the `.bat` files.

Deploying for the first time? Follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md):
the same steps with what to check after each one, plus updating and going live.

| File | What it does |
|---|---|
| `setup.bat` | Installs everything and puts the EAs into MT5 |
| `check.bat` | First time: asks your settings. Later: checks everything |
| `start.bat` | Runs the system - leave its window open |
| `settings.bat` | Change a setting later |
| `relay_login.bat` | Telegram login for the relay (only if you skipped it in Step 2) |
| `keys.txt` | All your keys and ids in one private file (made by `setup.bat`) |
| `dashboard_setup.bat` | Optional: your reports on your phone (run as administrator) |

## Before you start (once)

1. **MetaTrader 5** logged in to a **demo** account with XAUUSD.
2. **Python 3.12+ (64-bit)** from python.org - tick **"Add python.exe to PATH"**.
3. **Telegram bot:** @BotFather -> `/newbot` -> copy the **bot token**.
4. **Your chat id:** message your bot, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` -> copy the number after `"chat":{"id":`.
5. **Anthropic API key** from <https://console.anthropic.com/>.
6. **Relay group:** in Telegram create a **private group** (e.g. "Gold relay") and
   add your bot as **admin**. Signals are copied into it - this works for any
   signal channel you can read, you don't need to own it.
7. **Telegram API id:** <https://my.telegram.org> -> API development tools ->
   copy **api_id** and **api_hash**.
8. Windows: **Sleep = Never**.

## Step 1 - Install

Close MetaEditor -> double-click **`setup.bat`** -> wait for
`ALL SELF-TESTS PASSED` and `0 error(s)`.
Run it again after every update.

## Step 2 - Settings

Double-click **`check.bat`** and answer the questions:
- API key, bot token, your chat id;
- relay: **Enter** (yes), then api_id, api_hash, your signal channel(s)
  (`@name`, up to 3) and your relay group (`-` if you don't know its id yet);
- **Enter** to log the relay in to Telegram now (phone number + code, once) -
  it lists your chats with their ids; type the relay group's id if asked;
- **Y** to the test message (it must arrive in Telegram).

Everything is saved in **`keys.txt`** in this folder. At the end it tells
you the number for `InpChannelId1`.

You can also open `keys.txt` in Notepad and type the values after the `=`
(one per line, e.g. `ANTHROPIC_API_KEY=sk-ant-...`), save, then run
`check.bat`. **Keep it private** - don't share it or put this folder in
Google Drive/Dropbox/OneDrive; it is never uploaded to git.

## Step 3 - MT5

1. **Tools -> Options -> Expert Advisors** -> tick **Allow WebRequest** -> add
   `https://api.telegram.org`.
2. Open **XAUUSD M15** -> drag **UnifiedTrader_EA** on it -> **Inputs** ->
   **Load** `UnifiedTrader_EA_Default.set`.
3. Fill in `InpBotToken` and `InpControlChatId` (your chat id).
4. **Common** tab -> tick **Allow DLL imports** -> **OK** -> **Algo Trading** on.
5. **Inputs** -> `InpChannelId1` = your **relay group id** (from Step 2, e.g.
   `-1001234567890`).

## Step 4 - Start

1. EA **Inputs** -> `InpDryRun` = `false`.
2. Double-click **`start.bat`**. Done.

## What happens then

| | |
|---|---|
| Trading hours | **Telegram signals: 06:00-23:00 Oman time, Mon-Fri.** **Claude: 16:00-00:45 and 02:15-04:00 Oman time** (summer; one hour later in winter) - its backtested best hours. Open trades are managed around the clock |
| Telegram signals | Your channel -> relay group -> EA, copied within seconds |
| Claude trades | When 2 of 3 checks agree and Claude says "full" |
| Every trade | 2% risk, stop $6, locked at +$6, then trailed $3; max 5 per direction. **Telegram trades use the signal's own stop** when it is $3-$20 from the entry - the lot is made smaller or larger so the trade still risks 2% |
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

**Without the relay** (only if your bot is admin of the signal channel
itself): answer **n** to the relay in Step 2, then set `InpChannelId1` to the
channel's id (the EA's **Experts** tab shows it as `message from chat <id>`).

**Price files to Google Drive:** already copied to `G:\MyDrive\MyMQChartDrive`.
If your Drive folder path is different, change `InpXtrExportCopyTo`; to turn
it off, leave it empty.

**Reports on your phone:** right-click `dashboard_setup.bat` -> Run as
administrator, choose a password, then open `http://<PC name>:8080` - at
home on the same Wi-Fi, or anywhere with Tailscale on the PC and the phone.
Equity, open trades, results per source, Claude's decisions, Telegram
signals and the scorecard, refreshed every minute. Read-only.

**Trade journal in Google Drive:** automatic. Every few minutes
`start.bat` writes three CSV files into `G:\MyDrive\MyMQChartDrive\GoldTrader`
(your Drive folder): every closed trade of both sources, Claude's decisions
and the Telegram signal log. Ask Claude to analyse them straight from Drive -
no uploading. Different Drive path: `--journal-folder "X:\path"` in
`start.bat`.

**MT5 trade journal:** second XAUUSD chart -> **TelegramSMC_TradeLogger** ->
**Load** `TelegramSMC_TradeLogger_Unified.set`.

Step by step with checks: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
Details, options and troubleshooting: [`docs/REFERENCE.md`](docs/REFERENCE.md).
Backtest: [`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md).
