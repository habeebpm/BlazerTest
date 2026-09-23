# Installation - start to end

Everything runs from **one folder**: `MT_Py_Claude_Telegram_Solution`.
Double-click the `.bat` files there, or open a Command Prompt in that folder
(Explorer address bar -> type `cmd` -> Enter) and type the commands below.
No other folder is ever needed.

Required: steps 1-6. Optional: steps 7-13, any order, any time.

## 1. Get ready

1. MT5 installed, logged in to a **demo** account with XAUUSD.
2. Python 3.10+ installed (tick "Add to PATH").
3. Telegram: message **@BotFather** -> `/newbot` -> copy the bot token.
4. Anthropic API key from <https://console.anthropic.com/> (with credits).
5. Download this repository (Code -> Download ZIP) and unzip it.
6. Windows: **Settings -> System -> Power** -> Sleep = **Never**.

## 2. Telegram

1. Add your bot to the signal channel as **admin** (up to 3 channels).
   - Not your channel? Do step 7 (relay bridge) first.
2. Send your bot any message in a private chat.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   -> copy the number after `"chat":{"id":` = **your chat id**.

## 3. Install (Python + MT5 files)

1. Close MetaEditor (MT5 itself may stay open).
2. Double-click **`setup.bat`**
   (= `python solution.py setup` then `python solution.py install-mt5`).
   - Must show `ALL SELF-TESTS PASSED` and, per EA, `0 error(s)`.
   - "Several MT5 installations found": run
     `python solution.py install-mt5 --choice N`.
   - "MetaEditor not found": open `UnifiedTrader_EA` in MetaEditor -> **F7**.

## 4. Settings (asked once, in one go)

1. Double-click **`check.bat`**. The first start asks for every setting in
   one go - Anthropic API key, bot token, your chat id, and (only if you
   answer "yes" to the relay bridge) the relay settings. Secrets are typed
   hidden; Enter keeps a value, `-` skips it.
2. Say **Y** to the test message -> it must arrive in Telegram.
3. Saved permanently (also after a restart). Change anything later:
   **`keys.bat`**.

## 5. MetaTrader 5

1. **Tools -> Options -> Expert Advisors** -> tick *Allow WebRequest for
   listed URL* -> add `https://api.telegram.org`.
2. Open an **XAUUSD M15** chart -> Navigator -> drag `UnifiedTrader_EA`
   onto it -> **Inputs** -> **Load** -> `UnifiedTrader_EA_Default.set`.
3. Set:

   | Input | Value |
   |---|---|
   | `InpEnableTelegramSignals` | `true` |
   | `InpEnableClaudeManagement` | `true` |
   | `InpBotToken` | your bot token |
   | `InpControlChatId` | your chat id |
   | `InpDryRun` | `true` (for now) |

4. Click **OK** -> turn on **Algo Trading**.
5. **Experts** tab -> find `message from chat <id>` / `omitted ... from chat <id>`
   for your signal channel -> copy that id.
6. EA **Inputs** -> `InpChannelId1` = that id (2nd/3rd channel:
   `InpChannelId2`, `InpChannelId3`) -> **OK**.
7. Double-click **`check.bat`** -> connected to MT5, and every required
   line under **Saved settings** says `OK`.
8. ```bat
   python solution.py test-alert
   python solution.py test-feeds
   python solution.py test-news buy
   python solution.py once
   ```

## 6. Go live on the demo account

1. MT5 title bar shows **Demo**.
2. EA **Inputs** -> `InpDryRun` = `false` -> **OK**.
3. Double-click **`start.bat`** (runs
   `--live --xtr-gate require_alignment --shared-cap-magic 20260922`).
   Leave the window open.
4. First trade: check it appears in MT5 with an SL, the Telegram alert
   arrives, **Stats** answers.
5. Run 2-4 weeks (or 100+ trades) before anything else.

---

## 7. Optional - Relay bridge (not admin of the signal channel)

Runs inside `start.bat` - no extra window.

1. Create a private Telegram group -> add your bot as admin.
2. <https://my.telegram.org> -> API development tools -> copy `api_id`, `api_hash`.
3. `keys.bat` -> answer **y** to the relay bridge -> enter api_id,
   api_hash, the signal channel(s) and the relay group id (this also
   switches the bridge on in `main_preset.ini`). Unsure of an id? `-` skips.
4. Double-click **`relay_login.bat`** -> phone number + login code ->
   it prints every chat id (copy any you skipped, then `keys.bat` again).
5. `relay_login.bat` again -> both chats must resolve.
6. Close and restart `start.bat`.
7. EA `InpChannelId1` = the relay group id.

## 8. Optional - Price export to Google Drive

**A - PC with Google Drive for Desktop (the EA does it):**

1. Windows Explorer -> Google Drive (`G:`) -> create folder
   `MyMQChartDrive` -> copy its exact path from the address bar
   (usually `G:\My Drive\MyMQChartDrive`).
2. EA **Inputs**: `InpXtrExport` = `true`, `InpXtrExportCopyTo` = that path.
3. EA **Common** tab -> tick **Allow DLL imports** -> **OK**.
4. After a minute `XAUUSD_M5.csv`, `XAUUSD_M15.csv`, `XAUUSD_H1.csv`,
   `XAUUSD_manifest.json` appear there and update every minute.
5. Nothing there? **Experts** tab -> `XtrBarExport:` messages.

**B - VPS without a desktop (Python + service account):**

1. <https://console.cloud.google.com/> -> **APIs & Services -> Library** ->
   enable **Google Drive API**.
2. **Credentials -> Create Credentials -> Service Account** -> open it ->
   **Keys -> Add Key -> JSON** -> save as `C:\keys\drive.json`.
3. Share the Drive folder with the service account's email -> **Editor**.
4. Copy the folder id from `drive.google.com/drive/folders/<id>`.
5. ```bat
   python solution.py xtr-export --check --upload-drive --drive-folder-id <id> --drive-credentials C:\keys\drive.json
   ```
6. `ClaudeSMC_Trader\python\main_preset.ini` -> `[xtr_export]` ->
   `enabled = true`, replace `YOUR_FOLDER_ID` -> save -> restart `start.bat`.
7. EA `InpXtrExport` = `false`.

## 9. Optional - Trade Logger (trade journal CSV)

Already installed by `setup.bat`.

1. Open a **second** XAUUSD chart -> drag `TelegramSMC_TradeLogger` onto it
   -> **Inputs** -> **Load** -> `TelegramSMC_TradeLogger_Unified.set` -> **OK**
   (logs Telegram trades 20260922 **and** Claude trades 20260921).
2. Output: `MQL5\Files\TelegramSMC_Results.csv`.

## 10. Optional - Web dashboard (Windows IIS)

Needs step 9.

1. **Control Panel -> Programs -> Turn Windows features on or off** ->
   tick **Internet Information Services** and
   **World Wide Web Services -> Application Development -> ASP.NET 4.8** -> OK.
2. Copy everything in `ASPX\` to `C:\inetpub\wwwroot\dashboard\`.
3. Edit `Web.config`:
   ```xml
   <add key="SignalsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Signals.csv" />
   <add key="ResultsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Results.csv" />
   ```
   (`...` = MT5 **File -> Open Data Folder** path.)
4. **IIS Manager** -> right-click `dashboard` -> **Convert to Application**.
5. Browse to `http://localhost/dashboard/Dashboard.aspx`.
6. Do not expose it to the internet (no login built in).

## 11. Optional - ML advisor (after ~30 closed demo trades)

`ClaudeSMC_Trader\python\main_preset.ini` -> `[ml_retrain]` ->
`enabled = true` -> save -> restart `start.bat`. Retrains every 7 days;
output in `ClaudeSMC_Trader\python\logs\ml_retrain.log`.

## 12. Optional - Conviction report (weekly)

`main_preset.ini` -> `[calibration_report]` -> `enabled = true` -> save ->
restart `start.bat`. Output in `ClaudeSMC_Trader\python\logs\calibration_report.log`.

## 13. Optional - Backtest (free, mechanical)

```bat
python solution.py backtest --from-mt5 --start 2026-08-01 --end 2026-09-23 --mechanical --compare-xtr --xtr-gate require_alignment
```

Or from CSV files (`time,open,high,low,close,volume`; full paths):

```bat
python solution.py backtest --bars-csv C:\data\m15.csv --trend-csv C:\data\h4.csv --daily-csv C:\data\d1.csv --weekly-csv C:\data\w1.csv --m5-csv C:\data\m5.csv --h1-csv C:\data\h1.csv --mechanical --compare --compare-xtr --xtr-gate require_alignment
```

---

## What stays running

| Window | Needed |
|---|---|
| MT5 + `UnifiedTrader_EA` (+ Trade Logger chart, step 9) | Always |
| `start.bat` | Always - also runs whatever `main_preset.ini` switches on (steps 7, 8 B, 11, 12) |
| IIS | Step 10 only (runs as a Windows service) |

Never start the relay bridge, `xtr_export.py`, `train_ml_model.py` or
`calibration_report.py` by hand while `start.bat` runs them. Never run
`python/telegram_copier.py` alongside `UnifiedTrader_EA` (duplicate trades).

## Updating to a new version

1. Download + unzip over the old folder (your `main_preset.ini` changes:
   note them first).
2. Close MetaEditor -> `setup.bat` (old MT5 files are kept as `.bak`).
3. MT5: remove the EA from the chart, drag it on again -> **Inputs** ->
   **Load** the preset -> re-enter bot token, chat id, channel ids.
4. Restart `start.bat`.

## After a PC restart

Nothing to re-enter: keys are saved permanently in your Windows user
account; MT5 reopens its charts with the EA and its inputs; the relay login
and `main_preset.ini` are files.

1. Start MT5 (log in if asked) -> check **Algo Trading** is on.
2. `check.bat` -> every required line under **Saved settings** says `OK`.
3. `start.bat`.

Change a key: `keys.bat`. Remove one: `reg delete HKCU\Environment /v NAME /f`.

## Real money (after a good demo)

1. MT5 -> log in to the real account (small balance).
2. EA `InpDryRun` = `true`; edit `start.bat` -> remove `--live` -> run it for 1-2 days.
3. EA `InpDryRun` = `false`; put `--live` back in `start.bat` -> restart it.

## Telegram buttons

| Pause (closes + stops) | Resume (allows new trades) | Scope |
|---|---|---|
| `PauseHab` | `ResumeHab` | All trades |
| `PauseTelHab` | `ResumeTelHab` | Telegram trades |
| `PauseClaudeHab` | `ResumeClaudeHab` | Claude trades |

| Button | Shows |
|---|---|
| `Stats` | Equity, P/L, win % |
| `News` | Economic calendar |
| `Why` | Claude's last reasoning |
