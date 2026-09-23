# Installation - start to end

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

## 3. Python

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
pip install -r requirements.txt
python selftest.py
```

Must end with `ALL PASS`.

## 4. MetaTrader 5

1. MT5 -> **File -> Open Data Folder**. Copy:

   | From | To |
   |---|---|
   | `UnifiedTrader/MQL5/Experts/UnifiedTrader_EA.mq5` | `MQL5/Experts/` |
   | `MQL5/Include/TelegramSMC_Common.mqh` | `MQL5/Include/` |
   | `MQL5/Include/EconCalendar.mqh` | `MQL5/Include/` |
   | `MQL5/Include/XtrBarExport.mqh` | `MQL5/Include/` |
   | `UnifiedTrader/MQL5/Presets/UnifiedTrader_EA_Default.set` | `MQL5/Presets/` |

2. **Tools -> Options -> Expert Advisors** -> tick *Allow WebRequest for
   listed URL* -> add `https://api.telegram.org`.
3. Open `UnifiedTrader_EA.mq5` in MetaEditor -> press **F7** -> must show
   **0 errors**.
4. Open an **XAUUSD M15** chart -> drag `UnifiedTrader_EA` onto it ->
   **Inputs** -> **Load** -> `UnifiedTrader_EA_Default.set`.
5. Set:

   | Input | Value |
   |---|---|
   | `InpEnableTelegramSignals` | `true` |
   | `InpEnableClaudeManagement` | `true` |
   | `InpBotToken` | your bot token |
   | `InpControlChatId` | your chat id |
   | `InpDryRun` | `true` (for now) |

6. Click **OK** -> turn on **Algo Trading**.
7. **Experts** tab -> find `message from chat <id>` / `omitted ... from chat <id>`
   for your signal channel -> copy that id.
8. EA **Inputs** -> `InpChannelId1` = that id (2nd/3rd channel:
   `InpChannelId2`, `InpChannelId3`) -> **OK**.

## 5. Claude program - checks

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
setx ANTHROPIC_API_KEY sk-ant-...
setx TELEGRAM_ALERT_BOT_TOKEN <your bot token>
setx TELEGRAM_ALERT_CHAT_ID <your chat id>
```

Close the window, open a new Command Prompt:

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
python main.py --check
python main.py --test-alert
python main.py --test-feeds
python main.py --test-news-check buy
python main.py --once -v
```

## 6. Go live on the demo account

1. MT5 title bar shows **Demo**.
2. EA **Inputs** -> `InpDryRun` = `false` -> **OK**.
3. ```bat
   cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
   python main.py --live --xtr-gate require_alignment --shared-cap-magic 20260922
   ```
   Leave it running.
4. First trade: check it appears in MT5 with an SL, the Telegram alert
   arrives, **Stats** answers.
5. Run 2-4 weeks (or 100+ trades) before anything else.

---

## 7. Optional - Relay bridge (not admin of the signal channel)

Runs inside the Claude program (`main_preset.ini`) - no extra window.

1. Create a private Telegram group -> add your bot as admin.
2. <https://my.telegram.org> -> API development tools -> copy `api_id`, `api_hash`.
3. ```bat
   setx TELEGRAM_API_ID <api_id>
   setx TELEGRAM_API_HASH <api_hash>
   ```
4. New Command Prompt (enter phone number + login code when asked):
   ```bat
   cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
   pip install -r requirements.txt
   python main.py --relay-login
   ```
   Copy the source channel and relay group ids it prints.
5. ```bat
   setx TELEGRAM_SOURCE_CHANNELS @channel1,@channel2,@channel3
   setx TELEGRAM_RELAY_GROUP <relay group id>
   ```
6. New Command Prompt -> run `python main.py --relay-login` again ->
   both chats must resolve.
7. Open `ClaudeSMC_Trader\python\main_preset.ini` -> `[relay_bridge]` ->
   `enabled = true` -> save -> restart the step 6 command (Ctrl+C, run again).
8. EA `InpChannelId1` = the relay group id.
9. Never also run `telegram_relay_bridge.py` on its own (double relaying).

## 8. Optional - Price export to Google Drive

**A - PC with Google Drive for Desktop (the EA does it, no Python):**

1. Windows Explorer -> open the Google Drive drive (`G:`) -> create folder
   `MyMQChartDrive` -> copy its exact path from the address bar
   (usually `G:\My Drive\MyMQChartDrive`).
2. EA **Inputs**:

   | Input | Value |
   |---|---|
   | `InpXtrExport` | `true` |
   | `InpXtrExportCopyTo` | the path from step 1 |

3. EA **Common** tab -> tick **Allow DLL imports** -> **OK**.
4. After a minute, `XAUUSD_M5.csv`, `XAUUSD_M15.csv`, `XAUUSD_H1.csv`,
   `XAUUSD_manifest.json` appear in that folder and update every minute.
5. Nothing there? **Experts** tab -> look for `XtrBarExport:` messages.

**B - VPS without a desktop (Python + service account):**

```bat
cd MT_Py_Claude_Telegram_Solution\XTR_Export\python
pip install -r requirements.txt
python selftest.py
python xtr_export.py --check
```

1. <https://console.cloud.google.com/> -> **APIs & Services -> Library** ->
   enable **Google Drive API**.
2. **Credentials -> Create Credentials -> Service Account** -> open it ->
   **Keys -> Add Key -> JSON** -> save as `C:\keys\drive.json`.
3. Share the Drive folder with the service account's email -> **Editor**.
4. Copy the folder id from `drive.google.com/drive/folders/<id>`.
5. `ClaudeSMC_Trader\python\main_preset.ini` -> `[xtr_export]` ->
   `enabled = true`, replace `YOUR_FOLDER_ID` with the id -> save ->
   restart `main.py`.
6. EA `InpXtrExport` = `false`.

## 9. Optional - Trade Logger (trade journal CSV)

1. Copy `MQL5/Experts/TelegramSMC_TradeLogger.mq5` -> `MQL5/Experts/`,
   `MQL5/Presets/TelegramSMC_TradeLogger_Unified.set` -> `MQL5/Presets/`.
2. MetaEditor -> open it -> **F7** -> 0 errors.
3. Open a **second** XAUUSD chart -> drag `TelegramSMC_TradeLogger` onto it
   -> **Inputs** -> **Load** -> `TelegramSMC_TradeLogger_Unified.set` -> **OK**
   (logs Telegram trades 20260922 **and** Claude trades 20260921).
4. Output: `MQL5\Files\TelegramSMC_Results.csv`.

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
`enabled = true` -> save -> restart `main.py`. Retrains every 7 days
(`every_days`); output in `logs\ml_retrain.log`.

## 12. Optional - Conviction report (weekly)

`main_preset.ini` -> `[calibration_report]` -> `enabled = true` -> save ->
restart `main.py`. Output in `logs\calibration_report.log`.

## 13. Optional - Backtest (free, mechanical)

Export M5, M15, H1, H4, D1, W1 XAUUSD bars as CSV
(`time,open,high,low,close,volume`), then:

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
python backtest.py --bars-csv m15.csv --trend-csv h4.csv --daily-csv d1.csv --weekly-csv w1.csv --m5-csv m5.csv --h1-csv h1.csv --mechanical --compare --compare-xtr --xtr-gate require_alignment
```

Or straight from MT5:

```bat
python backtest.py --from-mt5 --start 2026-08-01 --end 2026-09-23 --mechanical --compare-xtr --xtr-gate require_alignment
```

---

## What stays running

| Window | Needed |
|---|---|
| MT5 + `UnifiedTrader_EA` (+ Trade Logger chart, step 9) | Always |
| `python main.py --live --xtr-gate require_alignment --shared-cap-magic 20260922` | Always - also runs whatever `main_preset.ini` switches on (steps 7, 8 B, 11, 12) |
| IIS | Step 10 only (runs as a Windows service) |

Never start `telegram_relay_bridge.py`, `xtr_export.py`, `train_ml_model.py`
or `calibration_report.py` by hand while `main.py` runs them. Never run
`python/telegram_copier.py` alongside `UnifiedTrader_EA` (duplicate trades).

## Real money (after a good demo)

1. MT5 -> log in to the real account (small balance).
2. EA `InpDryRun` = `true`; run `python main.py --xtr-gate require_alignment --shared-cap-magic 20260922` (no `--live`) for 1-2 days.
3. EA `InpDryRun` = `false`; restart with `--live`.

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
