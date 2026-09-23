# GoldTrader - setup guide

Everything is in this one folder. Double-click the `.bat` files here; nothing
else ever needs to be opened, copied or typed into another folder.

| File | What it does |
|---|---|
| `setup.bat` | Installs everything, tests it, copies + compiles the EAs in MT5 |
| `check.bat` | First time: asks all your settings. Then: checks MT5 + settings |
| `start.bat` | Runs the system. Leave its window open |
| `settings.bat` | Change a setting later (API key, Telegram ids, relay) |
| `relay_login.bat` | Only if you use the relay bridge (step 7) |
| `settings.ini` | Optional programs on/off (ML and reports are already on) |

## 1. Before you start

1. **MetaTrader 5** installed, logged in to a **demo** account that has XAUUSD.
2. **Python 3.12 or newer, 64-bit** from python.org - tick **"Add python.exe to PATH"**.
3. **Telegram bot:** message **@BotFather** -> `/newbot` -> copy the **bot token**.
4. **Your chat id:** send your bot any message, then open
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` -> the number after
   `"chat":{"id":` is **your chat id**.
5. Add the bot as **admin** of your signal channel (up to 3 channels).
   Not your channel? Do step 7 afterwards.
6. **Anthropic API key** from <https://console.anthropic.com/> (with credits).
7. Windows **Settings -> System -> Power -> Sleep = Never**.

## 2. Install

1. Close MetaEditor (MT5 may stay open).
2. Double-click **`setup.bat`**. Wait for `ALL SELF-TESTS PASSED` and
   `0 error(s)` for both EAs.
   - "Several MT5 installations found": run `python goldtrader.py install-mt5 --choice 1`
     (or 2 ...) in this folder.
   - "MetaEditor not found": open `UnifiedTrader_EA` in MetaEditor and press **F7**.

## 3. Settings (asked once)

1. Double-click **`check.bat`**. It asks, in one go: API key, bot token,
   chat id, and whether you use the relay bridge. Secrets are typed hidden.
2. Answer **Y** to the test message - it must arrive in Telegram.
3. It then checks MT5 (open MT5 first if it says it cannot reach it).

Saved permanently - also after a PC restart.

## 4. MetaTrader 5

1. **Tools -> Options -> Expert Advisors** -> tick **Allow WebRequest** ->
   add `https://api.telegram.org` -> OK.
2. Open an **XAUUSD M15** chart -> Navigator -> drag **UnifiedTrader_EA**
   onto it -> **Inputs** -> **Load** -> `UnifiedTrader_EA_Default.set`.
3. Fill in:

   | Input | Value |
   |---|---|
   | `InpBotToken` | your bot token |
   | `InpControlChatId` | your chat id |

4. **Common** tab -> tick **Allow DLL imports** (only for the Google Drive
   copy, step 8) -> **OK** -> turn on **Algo Trading**.
5. **Experts** tab -> find `message from chat <id>` for your signal channel
   (post something there if nothing shows) -> copy that id.
6. EA **Inputs** -> `InpChannelId1` = that id (more channels:
   `InpChannelId2`, `InpChannelId3`) -> **OK**.

## 5. Start (demo)

1. EA **Inputs** -> `InpDryRun` = `false` -> **OK**.
2. Double-click **`start.bat`**. Leave the window open.
3. First trade: it appears in MT5 with an SL, the Telegram alert arrives,
   the **Stats** button answers.
4. Let it run 2-4 weeks (or 100+ trades) before judging it.

That's it. The ML advisor and the weekly report run by themselves.

## 6. Every day

- Keep **MT5** and the **`start.bat`** window open.
- If `start.bat` stops (MT5 closed, internet gone), it restarts itself every
  minute. Close its window or press Ctrl+C to stop it.
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

### 7. Relay bridge (a signal channel you are NOT admin of)

1. Create a private Telegram group -> add your bot as admin.
2. <https://my.telegram.org> -> API development tools -> copy `api_id` and `api_hash`.
3. `settings.bat` -> answer **y** to the relay bridge -> enter api_id,
   api_hash, the channel(s) (`@name`) and the relay group id (`-` if unknown yet).
4. `relay_login.bat` -> phone number + login code -> it prints every chat id
   (then `settings.bat` again for any you skipped).
5. Restart `start.bat`.
6. EA `InpChannelId1` = the relay group id.

### 8. Price files to Google Drive (for XTR)

1. Explorer -> Google Drive -> create folder `MyMQChartDrive` -> copy its
   exact path from the address bar (usually `G:\My Drive\MyMQChartDrive`).
2. EA **Inputs** -> `InpXtrExportCopyTo` = that path -> **OK**
   (Allow DLL imports from step 4.4 must be ticked).
3. Within a minute `XAUUSD_M5.csv`, `XAUUSD_M15.csv`, `XAUUSD_H1.csv` and
   `XAUUSD_manifest.json` appear there, updated every minute.

### 9. Trade journal + web dashboard

1. Second XAUUSD chart -> drag **TelegramSMC_TradeLogger** -> **Inputs** ->
   **Load** -> `TelegramSMC_TradeLogger_Unified.set` -> OK.
2. Dashboard (Windows IIS): see `docs/REFERENCE.md`.

### 10. Real money (only after a good demo)

1. MT5 -> real account (small balance). EA `InpDryRun` = `true`.
2. Edit `start.bat` -> remove `--live` -> run 1-2 days, check the logs.
3. EA `InpDryRun` = `false`, put `--live` back -> restart `start.bat`.

---

More detail (how it decides, every safety limit, troubleshooting):
[`docs/REFERENCE.md`](docs/REFERENCE.md). Backtest results:
[`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md).
