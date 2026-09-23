# Installation - start to end

## 1. Get ready

1. MT5 installed, logged in to a **demo** account with XAUUSD.
2. Python 3.10+ installed (tick "Add to PATH").
3. Telegram: message **@BotFather** -> `/newbot` -> copy the bot token.
4. Anthropic API key from <https://console.anthropic.com/> (with credits).
5. Download this repository (Code -> Download ZIP) and unzip it.

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
3. Open `UnifiedTrader_EA.mq5` in MetaEditor -> press **F7**.
4. Open an **XAUUSD M15** chart -> drag `UnifiedTrader_EA` onto it ->
   **Inputs** -> **Load** -> `UnifiedTrader_EA_Default.set`.
5. Set:

   | Input | Value |
   |---|---|
   | `InpEnableTelegramSignals` | `true` |
   | `InpEnableClaudeManagement` | `true` |
   | `InpBotToken` | your bot token |
   | `InpControlChatId` | your chat id |
   | `InpDryRun` | `true` |

6. Click **OK** -> turn on **Algo Trading**.
7. **Experts** tab -> find `message from chat <id>` / `omitted ... from chat <id>`
   for your signal channel -> copy that id.
8. EA **Inputs** -> `InpChannelId1` = that id (2nd/3rd channel:
   `InpChannelId2`, `InpChannelId3`) -> **OK**.

## 5. Claude program

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
python main.py --shared-cap-magic 20260922
```

Leave the last one running.

## 6. Dry-run (1-2 weeks)

- Telegram: tap **Stats**, **News**, **Why**.
- MT5 **Experts** tab: check each signal.
- `ClaudeSMC_Trader\python\logs\decisions.csv`: check Claude's decisions.

## 7. Relay bridge (only if you are not admin of the channel)

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

## 8. Go live (demo first)

1. EA **Inputs** -> `InpDryRun` = `false` -> **OK**.
2. Stop the Claude program (Ctrl+C), then:
   ```bat
   python main.py --live --shared-cap-magic 20260922
   ```
3. Weeks on demo -> repeat steps 4-8 on a real account with a small balance.

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
