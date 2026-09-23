# Quick Start - the simple way

This gets you from nothing to a running setup using the **recommended
combination**:

- **UnifiedTrader_EA** (runs inside MetaTrader 5) - copies BUY/SELL signals
  from your Telegram channel, and manages the exits (stop-loss, profit lock,
  trailing stop) for *every* trade, Telegram's and Claude's.
- **Claude-SMC Trader** (a Python program on the same computer) - reads the
  gold chart every 15 minutes, asks Claude whether there's a good trade, and
  opens it if Claude is fully convinced.

This is the only combination where **all** trades follow the risk rules:
max **10%** loss per day, **2%** of your account risked per trade.

You do **not** need System A (XAUUSD Confluence EA), XTR_Export, or the
dashboard. `SETUP.md` covers everything else if you ever want it.

Plan on about an hour, then **1-2 weeks of watching in dry-run** before any
real order is sent.

---

## Step 1 - What you need

| Item | Where to get it |
|---|---|
| MT5 broker account, **demo first**, with XAUUSD | Your broker |
| A Windows PC or Windows VPS that stays on | MT5 and its Python package are Windows-only |
| MetaTrader 5 installed and logged in | Your broker's website |
| Python 3.10 or newer | <https://www.python.org/downloads/> - tick "Add to PATH" |
| A Telegram bot token | Message **@BotFather** in Telegram, send `/newbot` |
| An Anthropic API key (with credits) | <https://console.anthropic.com/> |

Never put passwords or keys inside these files - you'll type them into
MT5's input boxes or set them in the command window.

---

## Step 2 - Download and self-test (5 minutes)

Download this repository (Code -> Download ZIP, or `git clone`), unzip it,
then open **Command Prompt** in the folder and run:

```bat
cd MT_Py_Claude_Telegram_Solution\ClaudeSMC_Trader\python
pip install -r requirements.txt
python selftest.py
```

It should end with `ALL PASS`. This test needs no MT5, no internet and no
keys - it just proves the program works on your PC.

---

## Step 3 - Set up the Telegram bot (10 minutes)

1. **Add your bot to the signal channel as an admin.** If you can't (you
   don't own the channel), use the relay bridge in `SETUP.md` section 3b -
   it forwards the channel into a private group where your bot *is* admin.
2. **Find your own chat id** (for the pause buttons): send your bot any
   message in a private chat, then open this in a browser (put your token
   in): `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` - copy the
   number after `"chat":{"id":`.

---

## Step 4 - Install the EA in MetaTrader 5 (15 minutes)

1. In MT5: **File -> Open Data Folder**. Copy:
   - `UnifiedTrader/MQL5/Experts/UnifiedTrader_EA.mq5` -> `MQL5/Experts/`
   - `MQL5/Include/TelegramSMC_Common.mqh` -> `MQL5/Include/`
   - `MQL5/Include/EconCalendar.mqh` -> `MQL5/Include/`
   - `UnifiedTrader/MQL5/Presets/UnifiedTrader_EA_Default.set` -> `MQL5/Presets/`
2. **Tools -> Options -> Expert Advisors**: tick *Allow WebRequest* and add
   `https://api.telegram.org`.
3. Open `UnifiedTrader_EA.mq5` in MetaEditor and press **F7** (compile).
4. Open an **XAUUSD M15 chart**, drag the EA onto it, click **Load** in the
   Inputs tab and pick `UnifiedTrader_EA_Default.set`. Then change only:

   | Input | Set to |
   |---|---|
   | `InpEnableTelegramSignals` | `true` |
   | `InpEnableClaudeManagement` | `true` |
   | `InpBotToken` | your bot token |
   | `InpChannelId1` | leave `0` for now (see step 5) |
   | `InpControlChatId` | your own chat id from step 3 |
   | `InpDryRun` | leave **`true`** |

   Everything else is already set to the recommended rules (10% daily cap,
   2% per trade, max 5 trades each direction, $6 stop / $6 lock / $3 trail
   at 0.01 lot - scaled up automatically with your lot size).
5. Tick **Algo Trading** on the toolbar. In the **Experts** tab at the
   bottom you'll see each Telegram message the bot receives, with its chat
   id. Copy the signal channel's id into `InpChannelId1` and restart the EA.
   (While `InpChannelId1` is `0`, the EA treats *any* chat as a signal
   source - that's why `InpDryRun` must stay `true` until this is done.)

Run **only one** copy of this EA per MT5 terminal.

---

## Step 5 - Start the Claude program (10 minutes)

In the same Command Prompt (still in `ClaudeSMC_Trader\python`):

```bat
set ANTHROPIC_API_KEY=sk-ant-...your key...
python main.py --check
python main.py --once -v
```

`--check` connects to MT5 and prints your settings. `--once` runs one full
evaluation and shows Claude's reasoning. Then start it for real (still
dry-run, no orders):

```bat
python main.py --shared-cap-magic 20260922
```

`--shared-cap-magic 20260922` makes Claude's trades and the Telegram trades
share one position limit and one 10% daily budget. Leave this window open -
closing it stops Claude from trading (the EA keeps managing open trades).

---

## Step 6 - Watch it in dry-run (1-2 weeks)

Nothing is traded yet. Every day, check:

- MT5 **Experts** tab: are Telegram signals read correctly? Would the
  entries, stops and trailing moves have been right?
- `ClaudeSMC_Trader\python\logs\decisions.csv`: why Claude took or skipped
  each setup.
- In Telegram, tap **Why** to see Claude's latest reasoning.

---

## Step 7 - Go live on the DEMO account

1. In the EA inputs set `InpDryRun=false`.
2. Restart the program with `--live`:

   ```bat
   python main.py --live --shared-cap-magic 20260922
   ```

Trade the demo for several more weeks. Only then repeat steps 4-7 on a real
account, starting small.

---

## Everyday use

**Telegram buttons** (in your private chat with the bot):

| Button | What it does |
|---|---|
| `PauseHab` | Close everything and stop all new trades |
| `ResumeHab` | Allow all new trades again |
| `PauseTelHab` | Close only Telegram trades and stop new ones |
| `ResumeTelHab` | Allow new Telegram trades again |
| `PauseClaudeHab` | Close only Claude's trades and stop new ones |
| `ResumeClaudeHab` | Allow new Claude trades again |
| `Stats` | Equity, balance, open P/L, and closed P/L + win % for today / 7 / 30 days |
| `News` | Economic calendar: recent releases and what they mean for gold, and what's coming up |
| `Why` | Show Claude's latest reasoning |

You also get a message every time a trade closes: its profit/loss, your
equity, and today's P/L and win %.

While Claude is paused, the Python program keeps running but skips each
check (no Claude cost, no trades); the EA keeps managing any open trades.

**Risk rules that are always on:**

| Rule | Value |
|---|---|
| Risk per trade | 2% of account |
| Max loss per day | 10% of account - no new trade is opened if it could push the day past 10%, counting trades already open |
| Max open trades | 5 per direction (Telegram + Claude together) |
| News filter | No new trades from 15 minutes before to 15 minutes after a high-impact USD event (NFP, CPI, FOMC...), from MT5's own economic calendar |
| Exit | Stop at $6, lock profit at $6, then trail $3 (per 0.01 lot, scaled with lot size) |

To change them: `--risk-percent 1` / `--max-daily-loss 5` on the Python
side, and `InpRiskPercent` / `InpMaxDailyLossPct` in the EA - **keep both
sides the same**.

---

## Optional extras (later)

- **Telegram alerts and daily summary:** add
  `--telegram-alert-bot-token <token> --telegram-alert-chat-id <your id>`
  to the `main.py` command.
- **Machine-learning advisor:** after about 30 closed **live** trades, run
  `python train_ml_model.py` once a week. It learns from your own trades
  and gives Claude an extra hint. It never places or blocks trades.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| "WebRequest not allowed" | Step 4.2 - add `https://api.telegram.org` |
| EA never sees channel messages | The bot isn't an admin there, or `InpChannelId1` is wrong - set it back to `0` and read the id from the Experts tab |
| Buttons don't work | `InpControlChatId` must be *your own* chat id, not the channel's |
| "Why" says no verdict yet | `main.py` isn't running, or hasn't finished its first cycle |
| Claude "out of credits", retries every 30 min | Add credits at console.anthropic.com - open trades are still managed meanwhile |
| "daily loss budget" in the logs | Working as intended: that trade could have taken the day past 10% |
| `python` not found | Reinstall Python with "Add to PATH" ticked |

More detail: `SETUP.md` (every system, every option) and the `README.md`
inside each folder.
