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

1. Create a private Telegram group -> add your bot as admin.
2. <https://my.telegram.org> -> API development tools -> copy `api_id`, `api_hash`.
3. ```bat
   cd MT_Py_Claude_Telegram_Solution\python
   pip install -r requirements.txt
   setx TELEGRAM_API_ID <api_id>
   setx TELEGRAM_API_HASH <api_hash>
   ```
4. New Command Prompt:
   ```bat
   cd MT_Py_Claude_Telegram_Solution\python
   python telegram_relay_bridge.py --check
   setx TELEGRAM_SOURCE_CHANNELS @channel1,@channel2,@channel3
   setx TELEGRAM_RELAY_GROUP <relay group id>
   ```
5. New Command Prompt:
   ```bat
   cd MT_Py_Claude_Telegram_Solution\python
   python telegram_relay_bridge.py
   ```
   Leave it running.
6. EA `InpChannelId1` = the relay group id.

## 8. Optional - Price export to Google Drive (XTR_Export)

```bat
cd MT_Py_Claude_Telegram_Solution\XTR_Export\python
pip install -r requirements.txt
python selftest.py
python xtr_export.py --check
```

**A - PC with a desktop:**

1. Install **Google Drive for Desktop**.
2. Add folder `C:\XTR_Data` to Drive sync.
3. ```bat
   python xtr_export.py --out-dir C:\XTR_Data
   ```
   Leave it running.

**B - VPS without a desktop:**

1. <https://console.cloud.google.com/> -> **APIs & Services -> Library** ->
   enable **Google Drive API**.
2. **Credentials -> Create Credentials -> Service Account** -> open it ->
   **Keys -> Add Key -> JSON** -> save as `C:\keys\drive.json`.
3. Share the Drive folder with the service account's email -> **Editor**.
4. Copy the folder id from `drive.google.com/drive/folders/<id>`.
5. ```bat
   python xtr_export.py --upload-drive --drive-folder-id <id> --drive-credentials C:\keys\drive.json
   ```
   Leave it running.

## 9. Optional - Trade Logger (trade journal CSV)

1. Copy `MQL5/Experts/TelegramSMC_TradeLogger.mq5` -> `MQL5/Experts/`,
   `MQL5/Presets/TelegramSMC_TradeLogger_Default.set` -> `MQL5/Presets/`.
2. MetaEditor -> open it -> **F7** -> 0 errors.
3. Open a **second** XAUUSD chart -> drag `TelegramSMC_TradeLogger` onto it
   -> **Inputs** -> **Load** the preset.
4. Set `InpMagicNumber` = `20260922` -> **OK**.
5. Output: `MQL5\Files\TelegramSMC_Results.csv`.

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

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
python train_ml_model.py
```

Repeat once a week. No restart needed.

## 12. Optional - Conviction report (weekly)

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
python calibration_report.py
```

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
| `python main.py --live --xtr-gate require_alignment --shared-cap-magic 20260922` | Always |
| `python telegram_relay_bridge.py` | Step 7 only |
| `python xtr_export.py ...` | Step 8 only |
| IIS | Step 10 only (runs as a Windows service) |

Never run `python/telegram_copier.py` alongside `UnifiedTrader_EA`
(duplicate trades).

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
