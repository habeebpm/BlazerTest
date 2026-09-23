# End-to-End Setup Guide

> **New here? Start with [`QUICKSTART.md`](QUICKSTART.md)** - one
> recommended setup (UnifiedTrader_EA + Claude-SMC Trader), start to end,
> in plain steps. This file is the full reference for every system.

The checklist that gets you from a fresh clone to something running. Each
per-system `README.md` is the full reference (every input, every edge
case, every "why") - this file is just the steps, in order.

## 0. What's here

Six independent systems live in this repo. None share code, config, or a
magic number, so you can run any combination on the same MT5 account
without them interfering.

| # | System | What it does | Needs | Full docs |
|---|---|---|---|---|
| A | **XAUUSD Confluence EA** *(optional - not needed with C or E; don't delete `python/mt5_client.py`, the Python copier uses it)* | Trades its own 3-indicator (trend/momentum/strength) confluence rule | MT5 (MQL5), or MT5 + Python | `README.md`, `python/README.md` |
| B | **Telegram SMC Copier** | Copies BUY/SELL signals from a Telegram channel into MT5, with SMC sanity checks | MT5 + bot token (MQL5), or MT5 + Python + Telegram account | `README.md` § "Telegram SMC Copier", `python/README.md` |
| B+ | **Trade Logger + Dashboard** | Generic CSV trade journal + a read-only web dashboard | MT5 (Logger EA), IIS + ASP.NET (Dashboard) | `ASPX/README.md` |
| C | **Claude-SMC Trader** | Builds a market-intelligence snapshot and asks Claude to validate a 3-confluence setup before trading | MT5 + Python + Anthropic API key | `ClaudeSMC_Trader/README.md` |
| D | **XTR_Export** | Read-only: exports MT5's own XAUUSD bars to CSV, optionally synced to Google Drive | MT5 + Python | `XTR_Export/README.md` |
| E | **UnifiedTrader_EA** | One EA combining unvalidated Telegram execution with Claude-SMC exit management - an alternative to B+C, not an addition | MT5 (+ Python for the Claude side) | `UnifiedTrader/README.md` |

**Pick one implementation per job.** Systems A and B each ship as *both*
an MQL5 EA and a Python script for the same job - run one, not both, on
the same account. System E replaces B+C, not adds to them; read its
README's "Honest limitations" before choosing it over running B and C
separately. Running multiple systems at once means their trades are
independent but correlated (all XAUUSD) - size accordingly.

---

## 1. Prerequisites

- A broker account with MT5, XAUUSD enabled. Use a **demo** account until
  each system's README says otherwise.
- MetaTrader 5 terminal installed (Windows native, or macOS via Wine/
  Parallels - MT5 has no native macOS build). See `README.md` § "Running
  on a laptop" if not using a VPS.
- Python 3.10+, only if you're using a Python component.
- Git:

  ```bash
  git clone <this-repo-url>
  cd MT_Py_Claude_Telegram_Solution
  ```

- Credentials, gathered as you go, never stored in this repo - always
  environment variables or MT5's own EA Inputs tab: MT5 login/password/
  server, a Telegram bot token and/or API id/hash (System B), an
  Anthropic API key (System C).

---

## 2. System A - XAUUSD Confluence EA

Pick **either** MQL5 **or** Python, not both.

**MQL5:**
1. Copy `MQL5/Experts/XAUUSD_Confluence_EA.mq5` into your terminal's
   `MQL5/Experts/`. Compile (F7).
2. Optionally load `MQL5/Presets/XAUUSD_Confluence_EA_Default.set`.
3. Drag onto an **XAUUSD** chart, one chart only. Tick "Allow Algo
   Trading".
4. Defaults: `InpMagicNumber=20260908`, `InpFixedLot=0.01`,
   `InpMaxOpenPositions=4`, `InpMinConfluences=2`,
   `InpRequireConfirm=true`. Full list in `README.md` § "Key inputs".

**Python:**
```bash
cd python
pip install -r requirements.txt
set MT5_LOGIN=12345678
set MT5_PASSWORD=your-password
set MT5_SERVER=YourBroker-Demo
```
(Leave the three unset to attach to an already-logged-in terminal.)
```bash
python trader.py --selftest    # strategy self-test, no MT5 needed
python trader.py --check       # connect, print symbol spec, exit
python trader.py               # live data, DRY-RUN
python trader.py --live        # actually trade
```
Useful flags: `--lots`, `--timeframe`, `--max-positions`,
`--max-trades-per-day`, `-v` - full list in `python/README.md` § "Run".

**Test offline:** `python trader.py --selftest && python test_integration.py`

**Before going live:** backtest with tick data in the Strategy Tester,
forward-test on demo for weeks, confirm your broker's XAUUSD contract spec
matches what the EA assumes - see `README.md` § "Before going live".

---

## 3. System B - Telegram SMC Copier

Pick **either** MQL5 **or** Python. Both need the relay bridge below if
you don't own/moderate the source channel.

### 3a. Get a bot token

1. Message **@BotFather**, send `/newbot`, copy the token.
2. Add the bot to the signal channel/group **as admin**. Can't do that
   (you don't own/moderate it)? Skip to 3b instead.
3. In MT5: **Tools -> Options -> Expert Advisors** -> allow WebRequest for
   `https://api.telegram.org`.

### 3b. If you don't own/admin the channel: relay bridge

Reads the channel as your own account (any member can), forwards each
message unmodified into a private group your bot *can* be admin of.

1. Create a new private Telegram group.
2. Add your bot (3a) to it as admin.
3. Get `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` from <https://my.telegram.org>.
4. ```bash
   cd python
   set TELEGRAM_API_ID=1234567
   set TELEGRAM_API_HASH=your-api-hash
   python telegram_relay_bridge.py --check   # logs in, lists every chat id it can see
   ```
5. ```bash
   set TELEGRAM_SOURCE_CHANNELS=@some_signal_channel,-1001234567890
   set TELEGRAM_RELAY_GROUP=-1009876543210
   python telegram_relay_bridge.py           # keep this running continuously
   ```
6. Point your copier (3c's `InpChannelId1`, or 3d's `TELEGRAM_CHANNELS`) at
   the **relay group's** chat id, not the original channel's.

### 3c. MQL5: `TelegramSMC_Copier.mq5`

1. Copy `MQL5/Experts/TelegramSMC_Copier.mq5` and
   `MQL5/Include/TelegramSMC_Common.mqh` into your terminal's `Experts/`
   and `Include/` folders. Compile.
2. Set `InpBotToken`. Leave `InpChannelId1`/`InpChannelId2` at `0` for the
   **first run** - every message the bot sees gets logged with its chat
   id, so you can identify the right one.
3. Attach to a chart (`InpTradeXAUUSDOnly=true` by default requires the
   chart symbol to contain "XAU" to actually trade).
4. Copy the right chat id into `InpChannelId1`/`InpChannelId2`, restart.
5. Leave `InpDryRun=true` until the log agrees with the channel.
6. Defaults: `InpMagicNumber=20260918`, `InpFixedLot=0.05`,
   `InpMaxOpenPositions=4`, `InpTp1Points=4.0`/`InpTrailPoints=3.0`
   (the message's own TP1/TP2/TP3 are logged but never used - only its
   SL is), `InpUseSmcFilter=true`. Full list in `README.md`.

### 3d. Python alternative: `telegram_copier.py`

```bash
cd python
pip install -r requirements.txt
set MT5_LOGIN=12345678
set MT5_PASSWORD=your-password
set MT5_SERVER=YourBroker-Demo
set TELEGRAM_API_ID=1234567
set TELEGRAM_API_HASH=your-api-hash
set TELEGRAM_CHANNELS=@some_signal_channel,-1001234567890
```
```bash
python telegram_copier.py --check      # connect, print spec + verifier settings
python telegram_copier.py              # listen live, DRY-RUN
python telegram_copier.py --live       # listen live, actually copy trades
```
Default magic is **20260920** (differs from the MQL5 EA's `20260918` -
update the Trade Logger's magic to match whichever you use). Flags:
`--symbol`, `--channels`, `--lots`, `--risk-percent`, `--min-risk-reward`,
`--min-sl-units`, `--max-sl-units`, `--allow-missing-sl`, `-v`.

**Test offline:**
```bash
python telegram_copier.py --selftest
python telegram_copier.py --replay sample_signals.txt
```

### 3f. Trade Logger (optional, recommended)

Never places/modifies an order - just watches trade history for one magic
number and appends CSV rows.

1. Copy `MQL5/Experts/TelegramSMC_TradeLogger.mq5` into `Experts/`,
   compile, attach to any chart.
2. Set `InpSymbol`/`InpMagicNumber` to match the EA you're watching (see
   the magic table in § 7).
3. Writes to `TelegramSMC_Results.csv`.

### 3g. Dashboard (optional, ASP.NET)

Read-only KPI dashboard over the Logger's CSVs. Needs IIS + ASP.NET (Web
Forms, not Core).

1. Copy `ASPX/`'s contents into an IIS site root.
2. Edit `Web.config`'s `SignalsCsvPath`/`ResultsCsvPath` to point at the
   real CSV paths (a UNC path if the dashboard runs on a different
   machine). Blank/broken paths fall back to bundled sample data.
3. Browse to `Dashboard.aspx`. No built-in auth - put it behind IIS auth
   or a network restriction. Details in `ASPX/README.md`.

---

## 4. System C - Claude-SMC Trader

### 4a. Get an Anthropic API key

<https://console.anthropic.com/> (or `ant auth login`), then:
```bash
set ANTHROPIC_API_KEY=sk-ant-...
```

### 4b. Python setup

```bash
cd ClaudeSMC_Trader/python
pip install -r requirements.txt
```
Either leave MT5 already logged in, or pass credentials explicitly:
`python main.py --login 12345678 --password your-password --server
YourBroker-Demo --terminal-path "C:/Program Files/MetaTrader 5/terminal64.exe"`.

```bash
python main.py --check     # connect, print spec + full config, no Claude call
python main.py --once -v   # one evaluation cycle, still dry-run
python main.py             # run continuously, dry-run by default
python main.py --live      # actually send orders
```
Logs to `logs/decisions.csv` (every evaluation, accepted or rejected, with
reasoning) and `logs/trades.csv` (every order sent), both tagged
`source=Claude_Sig`.

Defaults (see `python main.py --help` for every override): 2% of equity
risked per trade (`risk_percent`), 10% daily loss cap (`max_daily_loss_pct`,
also enforced as a budget across open positions), reference `fixed_lot=0.01`,
`max_open_positions_per_direction=5`, `sl_dollars=6.0`, `tp1_dollars=6.0`,
`trail_dollars=3.0` (dollars at the reference lot, scaled with the traded
lot), `magic=20260921`, `min_confluence_count=2` of 3,
`require_full_conviction=True`, `claude_model="claude-opus-5"`.

**Optional Telegram alert:** `--telegram-alert-bot-token`/
`--telegram-alert-chat-id` sends a message on every "full" conviction
verdict, executed or not. Off by default - see `ClaudeSMC_Trader/README.md`
§ "Telegram full-conviction alerts" for bot setup steps.

### 4c. MQL5: `ClaudeSMC_TradeManager.mq5`

Python decides *whether and when* to enter; this EA manages each open
position's exit tick-by-tick.

1. Copy `ClaudeSMC_Trader/MQL5/Experts/ClaudeSMC_TradeManager.mq5` into
   `Experts/`, compile.
2. Drag onto an XAUUSD chart. Confirm `InpMagicNumber` (default
   `20260921`) matches `config.py`'s `AdvisorConfig.magic`, and
   `InpReferenceLot` (default `0.01`) matches `fixed_lot`. Tick "Allow
   Algo Trading".
3. Leave `InpDryRun=true` until you trust the logged SL modifications.
4. **Both halves must run together** - Python places no broker
   take-profit by default, so without this EA a trade has only its
   initial $6 stop, no lock-in, no trail.

Exit style is `AdvisorConfig.exit_style` in `config.py` -
`"sl_to_tp1"` (default: lock SL at `tp1_dollars`, then trail
`trail_dollars`) or `"breakeven_r_decay"` (adds an earlier
move-to-breakeven step; set `InpExitStyle` to match on the MQL5 side too).
See `ClaudeSMC_Trader/README.md` § "Exit design" for the full comparison
and a third, comparison-only `"fixed_tp"` value.

**Test offline:** `python selftest.py` - entirely synthetic/faked, no MT5,
API key, or network needed.

### 4f. Backtest (optional)

```bash
cd ClaudeSMC_Trader/python

# Free: engine/exit-simulation plumbing only, no API key
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical

# Free: compare exit_style=sl_to_tp1 against fixed_tp on identical data
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical --compare

# Real backtest against Claude's actual judgment - costs money, asks first
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv
```
`--mechanical` is a free, non-LLM stand-in for testing the engine, not
Claude's judgment. CSV columns: `time,open,high,low,close,volume`.
`--from-mt5 --start ... --end ...` pulls history from a running terminal.

### 4g. If only one half is available

A Claude outage backs off automatically and logs why;
`ClaudeSMC_TradeManager.mq5` keeps managing open positions with no Claude
dependency, and the Telegram copier stack (System B) is unaffected either
way - see `ClaudeSMC_Trader/README.md` § "Running with only one side
available".

---

## 5. System D - XTR_Export (MT5 -> Google Drive)

Read-only - never places, modifies, or closes an order.

```bash
cd XTR_Export/python
pip install -r requirements.txt
python xtr_export.py --check    # connect, export once, exit
python xtr_export.py            # loop: export on every new M1 close
```
Writes `XAUUSD_M5.csv`/`_M15.csv`/`_H1.csv` (true UTC) and
`XAUUSD_manifest.json` to `--out-dir` (default `xtr_data/`).

**Get the files onto Drive - pick one:**
- MT5 machine has a desktop: install Google Drive for Desktop, point it at
  `--out-dir`. No code, no credentials in this repo.
- MT5 runs headless: `python xtr_export.py --upload-drive
  --drive-folder-id <id> --drive-credentials sa.json` - needs a one-time
  Google Cloud service account (`XTR_Export/README.md` § "Option B").

**Test offline:** `python selftest.py`

---

## 6. System E - UnifiedTrader_EA (Telegram + Claude, one EA)

Trades off System B's SMC filter and SL checks for a single combined risk
model - read `UnifiedTrader/README.md` before choosing this over B+C.

1. Copy `UnifiedTrader/MQL5/Experts/UnifiedTrader_EA.mq5` into `Experts/`
   and `MQL5/Include/TelegramSMC_Common.mqh` (from System B) into
   `Include/`. Compile.
2. Load `UnifiedTrader/MQL5/Presets/UnifiedTrader_EA_Default.set`. Both
   sources ship disabled - set `InpEnableTelegramSignals=true` and/or
   `InpEnableClaudeManagement=true`.
3. Telegram side: same bot setup as § 3a (`InpBotToken`,
   `InpChannelId1`/`InpChannelId2`).
4. Claude side: confirm `InpClaudeMagicNumber` matches
   `ClaudeSMC_Trader/python/config.py`'s `AdvisorConfig.magic` (both
   `20260921`), keep `python main.py` running - this EA only manages
   those exits, never opens them.
5. Defaults: `InpRiskPercent=2.0` (risk-sized lots), `InpMaxDailyLossPct=10.0`
   (also a budget across open positions), reference `InpFixedLot=0.01`,
   `InpMaxPositionsPerDirection=5` (combined across both magics),
   `InpSlDollars=6.0`, `InpTp1Dollars=6.0`, `InpTrailDollars=3.0` (dollars at
   the reference lot, scaled with the traded lot).
6. Leave `InpDryRun=true` until you trust the logged behavior.

**Optional remote control:** set `InpControlChatId` to your own DM chat id
with the bot to enable `PauseHab`/`ResumeHab`/`PauseTelHab`/`ResumeTelHab`/
`PauseClaudeHab`/`ResumeClaudeHab`/`Why` - shown as tappable buttons in that
chat (typing the exact text also works): closing positions, pausing and
resuming new Telegram and/or Claude entries, and echoing Claude's latest
reasoning on demand. The Claude pause reaches `python/main.py` through a
shared file, so `main.py` must run on the same machine as MT5. See
`UnifiedTrader/README.md` § "Remote control".

**Making the shared cap symmetric (recommended):** this EA enforces the
combined cap for its own Telegram entries only - it can't intercept an
order Python places directly.
```bash
cd ClaudeSMC_Trader/python
python main.py --shared-cap-magic 20260922 ...   # matches InpTelegramMagicNumber
```
That wires up *which* positions get counted together - it does **not**
keep the two ceiling numbers in sync. Set `InpMaxPositionsPerDirection`
(MQL5) and `--max-positions` (Python) to the *same* value, or the cap
becomes asymmetric even with the magic wiring correct.

**Test offline:** covered by `ClaudeSMC_Trader/python selftest.py`, which
also verifies `shared_cap_magic_numbers` wiring.

---

## 7. Magic number reference

Every EA/script defaults to a different magic number so they can coexist
without one system managing another's positions:

| System | Magic (default) | Comment tag |
|---|---|---|
| XAUUSD Confluence EA | `20260908` | `XAUUSD-Confluence-Py` (Python) |
| Telegram Copier - MQL5 | `20260918` | - |
| Telegram Copier - Python | `20260920` | `Telegram_Sig` |
| Claude-SMC Trader | `20260921` | `Claude_Sig` |
| UnifiedTrader_EA - Telegram half | `20260922` | `Telegram_Sig_Unified` |
| UnifiedTrader_EA - Claude half | `20260921` (must equal Claude-SMC Trader's) | `Claude_Sig` |

Change a magic number on one side, change it identically on the other side
of that system, and update the Trade Logger's `InpMagicNumber` if it's
watching that system.

---

## 8. Every offline test command

```bash
# System A (Confluence EA, Python port)
cd python && python trader.py --selftest && python test_integration.py

# System B (Telegram Copier, Python)
cd python && python telegram_copier.py --selftest
python telegram_copier.py --replay sample_signals.txt

# System C (Claude-SMC Trader) - also covers System E's Claude-side wiring
cd ClaudeSMC_Trader/python && python selftest.py

# System D (XTR_Export)
cd XTR_Export/python && python selftest.py
```
None need MT5, a broker connection, Telegram, Google, or Anthropic
credentials - run them after cloning, and after changing any threshold,
formula, or config default.

---

## 9. Troubleshooting

- **"WebRequest ... not allowed"** (System B, MQL5) - Tools -> Options ->
  Expert Advisors -> add `https://api.telegram.org`.
- **EA never sees the channel's posts** - bot isn't an admin there (use
  the relay bridge, § 3b), or `InpChannelId1`/`InpChannelId2` don't match
  the chat id logged with both left at `0`.
- **Claude-SMC Trader "out of credits" / backing off every 30 min** -
  expected (§ 4g) - add credits or run System B on its own meanwhile.
- **A Claude-SMC Trader position isn't trailing/locking profit** - confirm
  `ClaudeSMC_TradeManager.mq5` is actually attached, not just Python.
- **Dashboard shows only sample data** - `Web.config`'s CSV paths are
  blank or point at a file that doesn't exist yet.
- **Two systems' trades look mixed together** - check the `source`/
  `comment` column and the magic number (§ 7) - every log tags its origin.
- **UnifiedTrader_EA's shared cap doesn't hold Claude back** - set
  Python's `shared_cap_magic_numbers` to `[InpTelegramMagicNumber]` too
  (§ 6) - this EA alone can't intercept Python's own orders.
- **PauseHab/PauseClaudeHab closed Claude positions but a new one opened
  anyway** - `main.py` isn't seeing the pause file: run it on the same
  machine as MT5, and keep `InpClaudePauseFilename` equal to `config.py`'s
  `claude_pause_filename` (both default `claudesmc_pause.txt`).
- **PauseHab/ResumeHab don't seem to do anything** - `InpControlChatId`
  must be your own DM chat id with the bot, never `InpChannelId1`/
  `InpChannelId2` (OnInit refuses to start if they match) - see
  `UnifiedTrader/README.md` § "Remote control" for how to find it.
- **XTR_Export uploads fail with a quota/storage error** - the target
  Drive folder wasn't shared with the service account's email (a service
  account has no storage of its own).
- **XTR's indicator readings look off vs the MT5 chart** - check
  `XAUUSD_manifest.json`'s `broker_utc_offset_hours` against your broker's
  actual GMT offset; usually means this machine's clock is wrong.
