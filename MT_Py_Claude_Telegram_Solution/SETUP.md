# End-to-End Setup Guide

A single walk-through for everything in this repo, in the order you'd
actually do it. Each per-system `README.md` is the full reference (every
input, every edge case); this file is the checklist that gets you from a
fresh clone to something running, live-account cautions included.

## 0. What's here, and how the pieces fit together

Four independent systems live in this repo. None of them share code,
config, or a magic number with any other, so you can run one, several, or
all of them on the same MT5 account/chart without them interfering with
each other. Pick what you need:

| # | System | What it does | Needs | Full docs |
|---|---|---|---|---|
| A | **XAUUSD Confluence EA** | Generates its own trades from a 3-indicator (trend/momentum/strength) confluence rule | MT5 only (MQL5), or MT5 + Python | `README.md` (MQL5), `python/README.md` (Python port) |
| B | **Telegram SMC Copier** | Copies BUY/SELL signals posted in a Telegram channel into MT5, after a price-action/SMC sanity check | MT5 + a Telegram bot (MQL5), or MT5 + Python + a Telegram account (Python) | `README.md` § "Telegram SMC Copier", `python/README.md` § "Telegram signal copier" |
| B+ | **Trade Logger + Dashboard** | Generic CSV trade journal (any EA/magic) plus a read-only web dashboard over it | MT5 (Logger EA), IIS + ASP.NET (Dashboard) | `ASPX/README.md` |
| C | **Claude-SMC Trader** | Builds a full market-intelligence snapshot (indicators + SMC structure) and asks Claude to validate a 3-confluence setup before trading | MT5 + Python + an Anthropic API key | `ClaudeSMC_Trader/README.md` |

**Only System A or C generates its own trade ideas.** System B copies
someone else's. Running more than one trading system (A, B, C) on the
*same account and symbol* at once means their positions are independent of
each other but still correlated (all XAUUSD) - each system's own
`max_open_positions`/daily-loss guard only sees its own trades, not the
others'. Size accordingly if you run more than one.

**Pick one implementation per system, not both.** System A and System B
each ship as *both* a native MQL5 EA and a Python script that do the same
job - they're alternatives (e.g. so you can run on a headless Linux box via
Python instead of a Windows MT5 terminal running the EA), not meant to run
simultaneously against the same account, since they'd duplicate every
trade.

---

## 1. Prerequisites

- **A broker account with MetaTrader 5, XAUUSD enabled.** Use a **demo**
  account for everything in this guide until "Before going live" in each
  system's README says otherwise.
- **MetaTrader 5 terminal installed** on Windows (native) or macOS (via
  Wine/Parallels - MT5 itself doesn't run natively on macOS). A Windows VPS
  is the standard choice for unattended 24/5 operation; see `README.md`
  § "Running on a laptop" for what does and doesn't survive a lid closing
  or the machine sleeping.
- **Python 3.10+** if you're using any of the Python components (trader.py,
  telegram_copier.py, ClaudeSMC_Trader/python/main.py, or any selftest).
  Not needed if you're only running MQL5 EAs.
- **Git**, to clone this repo:

  ```bash
  git clone <this-repo-url>
  cd MT_Py_Claude_Telegram_Solution
  ```

- Credentials you'll gather along the way, never stored in this repo,
  always passed via environment variables or MT5's own EA Inputs tab:
  MT5 login/password/server, a Telegram bot token and/or API id/hash
  (System B), an Anthropic API key (System C).

---

## 2. System A - XAUUSD Confluence EA

Pick **either** 2a (MQL5, no Python needed) **or** 2b (Python port).

### 2a. MQL5 native EA

1. Copy `MQL5/Experts/XAUUSD_Confluence_EA.mq5` into your terminal's
   `MQL5/Experts/` folder (MT5: File -> Open Data Folder ->
   `MQL5\Experts\`).
2. Open it in MetaEditor and compile (F7) - confirm 0 errors.
3. Optionally load `MQL5/Presets/XAUUSD_Confluence_EA_Default.set` from the
   EA's Inputs tab instead of retyping every parameter.
4. Drag the EA onto an **XAUUSD** chart. Tick "Allow Algo Trading" (both
   the toolbar button and the EA's own Common tab checkbox).
5. Defaults: `InpMagicNumber = 20260908`, `InpUseFixedLot = true` at
   `InpFixedLot = 0.01`, `InpMaxOpenPositions = 4`, `InpMinConfluences = 2`,
   `InpRequireConfirm = true`. Full list of every input in `README.md`
   § "Key inputs".
6. Attach to **one chart only** - a second instance on another chart trades
   against the first with the same magic number.
7. Read `README.md` § "Confidence score" and § "Risk & trade management"
   before changing anything from the shipped defaults.

### 2b. Python port (alternative to 2a, not in addition to it)

```bash
cd python
pip install -r requirements.txt
```

```bat
:: Windows - credentials from the environment, never hardcoded
set MT5_LOGIN=12345678
set MT5_PASSWORD=your-password
set MT5_SERVER=YourBroker-Demo
```

Leave the three unset to attach to an already-running, already-logged-in
terminal instead.

```bash
python trader.py --selftest    # strategy self-test, no MT5 needed
python trader.py --check       # connect, print symbol spec + preflight, exit
python trader.py --signal      # evaluate the 3 confluences once, exit
python trader.py               # live data, DRY-RUN - logs orders, sends none
python trader.py --live        # actually trade
```

`--live` is opt-in by design - always run without it first and read what it
logs. Useful overrides: `--lots 0.02`, `--timeframe M15`,
`--max-positions 1`, `--max-trades-per-day 10`, `-v`. Full flag list in
`python/README.md` § "Run".

### 2c. Test without a live account (either path)

```bash
cd python
python trader.py --selftest
python test_integration.py     # full run against a mock MT5, no MT5 needed
```

### 2d. Before going live

`README.md` § "Before going live" - backtest with tick data across
multiple years in the Strategy Tester (this is the only way to get a real
win rate/profit factor/drawdown; none is claimed in advance), forward-test
on demo for several weeks, start small, and confirm your broker's XAUUSD
contract spec (tick value/size, stops level, filling mode) matches what the
EA assumes.

---

## 3. System B - Telegram SMC Copier

Pick **either** 3a (MQL5, bot-token based) **or** 3d (Python, Telethon
based). Both need step 3b if you don't own/moderate the source channel.

### 3a. MQL5 native EA - get a bot token

1. Message **@BotFather** in Telegram, send `/newbot`, follow the prompts,
   and copy the token it gives you (looks like `123456:ABC-...`).
2. Add that bot to the signal channel/group **as an admin** - a bot only
   receives channel posts if it's one. **If you can't add a bot to the
   channel** (you don't own/moderate it), skip to step 3b instead of
   continuing here.
3. In MT5: **Tools -> Options -> Expert Advisors** -> tick "Allow
   WebRequest for listed URL" and add `https://api.telegram.org` exactly.
   The EA logs this exact instruction if WebRequest is refused.

### 3b. If you don't own/admin the source channel: relay bridge

This reads the channel as your own Telegram account (a regular member sees
every post, no admin rights needed) and forwards each message, unmodified,
into a private group you create and fully control - which the bot *can* be
admin of.

1. Create a **new private Telegram group** (any name), just for this
   relay.
2. Add your bot (from 3a step 1) to that group as **admin**, zero
   permissions needed.
3. Get `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` from
   <https://my.telegram.org> (API development tools) - a personal API app
   credential, not a bot token.
4. ```bash
   cd python
   set TELEGRAM_API_ID=1234567
   set TELEGRAM_API_HASH=your-api-hash
   python telegram_relay_bridge.py --check
   ```
   Logs in (prompts once for phone number + login code) and prints every
   chat id this account can see, so you can identify the source channel.
   If `TELEGRAM_SOURCE_CHANNELS`/`TELEGRAM_RELAY_GROUP` aren't set yet,
   `--check` just lists chats - that's expected on the first run.
5. ```bash
   set TELEGRAM_SOURCE_CHANNELS=@some_signal_channel,-1001234567890
   set TELEGRAM_RELAY_GROUP=-1009876543210
   python telegram_relay_bridge.py
   ```
   Keep this running continuously (same machine as MT5, or anywhere with
   network access) - if it stops, no new signals reach the relay group.
6. Point whichever copier you use (3a's `InpChannelId1`, or 3d's
   `TELEGRAM_CHANNELS`) at the **relay group's** chat id (the id `--check`
   printed for it in step 4), not the original channel's.

### 3c. MQL5: configure `TelegramSMC_Copier.mq5`

1. Copy `MQL5/Experts/TelegramSMC_Copier.mq5` (and
   `MQL5/Include/TelegramSMC_Common.mqh`) into your terminal's
   `MQL5/Experts/` and `MQL5/Include/` folders. Compile.
2. Set `InpBotToken` to the token from 3a. Leave
   `InpChannelId1`/`InpChannelId2` at `0` for the **first run only**.
3. Attach to a chart (any symbol chart works for reading Telegram, but
   `InpTradeXAUUSDOnly = true` by default requires the chart symbol to
   contain "XAU" before it will actually trade). Every message the bot can
   see gets logged with its chat id.
4. Copy the id of the channel you want (or the relay group's id from 3b)
   into `InpChannelId1` (and `InpChannelId2` for a second source), restart
   the EA. Leaving both at `0` accepts signals from **any** chat the bot
   can see - fine for that first discovery run, not recommended after.
5. Leave `InpDryRun = true` (the shipped default) until you've watched the
   log agree with the channel for a while.
6. Key defaults worth knowing before going live: `InpMagicNumber = 20260918`,
   `InpFixedLot = 0.05`, `InpMaxOpenPositions = 4`,
   `InpTp1Points = 4.0` / `InpTrailPoints = 3.0` (broker-side TP that's
   dropped in favor of a trail once armed - **the signal's own TP1/TP2/TP3
   are logged but ignored**, only the SL is taken from the message),
   `InpUseSmcFilter = true` (liquidity-sweep + premium/discount check on
   `InpSmcTF`, default M15). Full list and rationale in `README.md`
   § "Telegram SMC Copier (MQL5)".

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

The first `--live` run prompts once for your phone number + login code,
then caches a session file (`TELEGRAM_SESSION`, default
`tg_copier.session`) so later runs don't ask again.

```bash
python telegram_copier.py --check      # connect to MT5, print spec + verifier settings, exit
python telegram_copier.py              # listen live, DRY-RUN
python telegram_copier.py --live       # listen live, actually copy trades
```

Default magic number is **20260920** (`copier_config.py`) - note this
differs from the MQL5 EA's default of 20260918; if you switch between the
two implementations, update whichever magic number the Trade Logger (3f)
is watching to match. Useful flags: `--symbol`, `--channels`, `--lots`,
`--risk-percent`, `--min-risk-reward`, `--min-sl-units`, `--max-sl-units`,
`--allow-missing-sl`, `-v`. Full verifier check list in `python/README.md`
§ "Telegram signal copier + verifier".

### 3e. Test without Telegram or MT5

```bash
cd python
python telegram_copier.py --selftest                     # parser + verifier + engine checks
python telegram_copier.py --replay sample_signals.txt    # parse+verify a batch of sample messages, no MT5/Telegram
```

### 3f. Trade Logger (optional, recommended, generic across all systems)

A separate EA - it never places/modifies/closes an order, only watches
trade history for one magic number and appends CSV rows.

1. Copy `MQL5/Experts/TelegramSMC_TradeLogger.mq5` into `MQL5/Experts/`,
   compile, attach to **any** chart (it reads account-wide history, not
   chart ticks - any symbol chart works).
2. Load `MQL5/Presets/TelegramSMC_TradeLogger_Default.set` or set
   `InpSymbol`/`InpMagicNumber` to match whatever EA is actually trading:
   - Confluence EA (System A): `20260908`
   - Copier MQL5 EA (System B, 3c): `20260918`
   - Copier Python tool (System B, 3d): `20260920`
   - Claude-SMC Trader (System C): `20260921`, and set
     `InpSourceLabel = "Claude_Sig"` to keep that log distinguishable.
3. Writes to `TelegramSMC_Results.csv` (this terminal's `MQL5\Files`, or
   the shared `Common\Files` if `InpUseCommonFolder = true`).

### 3g. Dashboard (ASP.NET, optional)

Reads `TelegramSMC_Signals.csv` (from 3c) and `TelegramSMC_Results.csv`
(from 3f) and renders a read-only KPI/chart dashboard. Requires IIS with
ASP.NET (.NET Framework, Web Forms) - not ASP.NET Core.

1. Copy the `ASPX/` folder's contents into an IIS site/application root,
   keeping `App_Data/` alongside `Dashboard.aspx` and `Web.config`.
2. Edit `Web.config`'s two `appSettings`:
   ```xml
   <add key="SignalsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Signals.csv" />
   <add key="ResultsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Results.csv" />
   ```
   If the dashboard runs on a different machine from MT5, use a UNC path
   the app pool's identity can read, or sync the CSVs there on a schedule.
   Leaving a path blank/broken falls back to the bundled sample CSVs with a
   banner - the page always renders something.
3. Browse to `Dashboard.aspx`. Manual Refresh button (nothing is cached);
   no authentication of its own - put it behind IIS auth or a network
   restriction if it's reachable from anywhere untrusted. Full details in
   `ASPX/README.md`.

---

## 4. System C - Claude-SMC Trader

### 4a. Get an Anthropic API key

Get a key from <https://console.anthropic.com/> (or run `ant auth login`
if you use the Claude CLI), then:

```bash
set ANTHROPIC_API_KEY=sk-ant-...
```

### 4b. Python setup

```bash
cd ClaudeSMC_Trader/python
pip install -r requirements.txt
```

Unlike System A/B, this system has no environment-variable credential
loader - either leave MT5 already logged in (the terminal stays connected,
`main.py` just attaches to it), or pass credentials explicitly:
`python main.py --login 12345678 --password your-password --server
YourBroker-Demo --terminal-path "C:/Program Files/MetaTrader 5/terminal64.exe"`.

```bash
python main.py --check     # connect to MT5, print symbol spec + full config, no Claude call
python main.py --once -v   # one evaluation cycle, still dry-run - nothing is sent
python main.py             # run continuously, dry-run by default
python main.py --live      # actually send orders
```

Everything is logged to `logs/decisions.csv` (every evaluation, accepted
or rejected, with Claude's reasoning) and `logs/trades.csv` (every order
actually placed) - both tag every row `source=Claude_Sig`.

Defaults (all overridable, see `python main.py --help`):
`fixed_lot = 0.01`, `max_open_positions_per_direction = 5`,
`sl_dollars = 6.0`, `tp1_dollars = 6.0`, `trail_dollars = 3.0`,
`magic = 20260921`, `min_confluence_count = 2` of 3,
`require_full_conviction = True` (only Claude's "full conviction" verdicts
execute), `claude_model = "claude-opus-5"`.

### 4c. MQL5: `ClaudeSMC_TradeManager.mq5`

Python decides *whether and when* to enter; this EA decides how each open
position's exit evolves tick-by-tick (Python's poll loop is too
coarse-grained to catch a fast move in time).

1. Copy `ClaudeSMC_Trader/MQL5/Experts/ClaudeSMC_TradeManager.mq5` into
   your terminal's `MQL5/Experts/`, compile.
2. Drag onto an XAUUSD chart. Confirm `InpMagicNumber` (default
   `20260921`) matches `config.py`'s `AdvisorConfig.magic` - only change
   one if you change the other. Tick "Allow Algo Trading".
3. Leave `InpDryRun = true` (shipped default) until you've watched it log
   a few would-be SL modifications and trust the output.
4. **Both halves must run together.** Under the default exit design (see
   4d), Python places **no broker take-profit at all** - if this EA isn't
   running, a trade is protected by nothing but its initial $6 stop-loss,
   with no lock-in and no trail.

### 4d. Exit design: `exit_style`

`AdvisorConfig.exit_style` in `config.py`, two values:

- **`"sl_to_tp1"` (default, live)** - no broker TP is ever placed; the
  stop-loss is the only exit. Once floating profit reaches `tp1_dollars`
  ($6), the EA locks the SL to exactly that price, then trails
  `trail_dollars` ($3) behind new highs/lows.
- **`"fixed_tp"` (comparison only, not implemented in the live EA)** - the
  original design: a real broker TP at `entry + tp1_dollars`, which sits
  at the exact same price the trail would arm at, so the standing TP order
  almost always wins that race and the trail rarely engages. Kept so
  `backtest.py --compare` has something concrete to measure `sl_to_tp1`
  against - see `ClaudeSMC_Trader/README.md` § "What the backtest found"
  for the actual comparison numbers.

Override with `main.py --exit-style sl_to_tp1|fixed_tp`.

### 4e. Test without a live account

```bash
cd ClaudeSMC_Trader/python
python selftest.py
```

Entirely offline - synthetic price data exercises the real indicator/SMC
code, a fake MT5 gateway exercises `executor.py`'s gating, a fake
Anthropic client exercises `claude_advisor.py`'s wiring. No MT5 terminal,
no API key, no network needed.

### 4f. Backtest (optional, before trusting this with real money)

```bash
cd ClaudeSMC_Trader/python

# Free: test the engine/exit-simulation plumbing, no API key needed
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical

# Free: compare exit_style=sl_to_tp1 against fixed_tp on identical data
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv --mechanical --compare

# Real backtest against Claude's actual judgment - costs money, asks first
python backtest.py --bars-csv m15.csv --trend-csv h4.csv \
    --daily-csv d1.csv --weekly-csv w1.csv
```

`--mechanical` is a free, non-LLM stand-in - it tests the engine (data
lines up, gating works, exits look right), not Claude's actual judgment.
CSV columns: `time,open,high,low,close,volume`, one row per closed
historical bar. `--from-mt5 --start ... --end ...` pulls history straight
from a running terminal instead of CSVs.

### 4g. Running with only one half of the stack available

`main.py`'s poll loop catches a Claude outage specifically
(`claude_advisor.ClaudeUnavailableError`) and doesn't crash: something
that clears on its own (rate limit, a 5xx, a network blip) just retries at
the normal poll interval; something that needs manual action (out of
credits, a bad API key, access denied) backs off to checking every 30
minutes and says so in the log. Either way,
`ClaudeSMC_TradeManager.mq5` keeps managing any already-open positions on
its own (no Claude dependency at all), and the wholly independent Telegram
copier stack (System B) is completely unaffected. Symmetrically, if
Telegram/the relay bridge goes down, System C keeps evaluating and trading
on Claude's calls exactly as before, since it never touches Telegram.

---

## 5. Running more than one system at once - magic number reference

Every EA/script below defaults to a **different** magic number
specifically so they can coexist on the same account without one system
managing another's positions:

| System | Magic (default) | Comment tag |
|---|---|---|
| XAUUSD Confluence EA (2a/2b) | `20260908` | `XAUUSD-Confluence-Py` (Python) |
| Telegram Copier - MQL5 (3c) | `20260918` | - |
| Telegram Copier - Python (3d) | `20260920` | `Telegram_Sig` |
| Claude-SMC Trader (4b/4c) | `20260921` | `Claude_Sig` |

If you change a magic number on one side (Python config or an EA's
`InpMagicNumber`), change it identically on the other side of that same
system, and update the Trade Logger's `InpMagicNumber` (3f) if you're
using it to watch that system.

---

## 6. Quick reference - every offline test command

```bash
# System A (Confluence EA, Python port)
cd python && python trader.py --selftest && python test_integration.py

# System B (Telegram Copier, Python)
cd python && python telegram_copier.py --selftest
python telegram_copier.py --replay sample_signals.txt

# System C (Claude-SMC Trader)
cd ClaudeSMC_Trader/python && python selftest.py
```

None of these need MT5, a broker connection, Telegram credentials, or an
Anthropic API key - run them after cloning, and again after changing any
threshold, formula, or config default, before touching a live/demo
account.

---

## 7. Troubleshooting quick reference

- **"WebRequest ... not allowed"** (System B, MQL5) - Tools -> Options ->
  Expert Advisors -> add `https://api.telegram.org` to the allowed URL
  list.
- **EA never sees the channel's posts** - the bot isn't an admin there
  (or you don't have admin rights to add it - use the relay bridge, § 3b),
  or `InpChannelId1`/`InpChannelId2` don't match the chat id logged when
  both were left at `0`.
- **Claude-SMC Trader "out of credits" / backing off every 30 min** -
  expected behavior (§ 4g), not a bug; add credits at
  <https://console.anthropic.com/> or keep running System B on its own in
  the meantime.
- **A position under Claude-SMC Trader isn't trailing/locking profit** -
  confirm `ClaudeSMC_TradeManager.mq5` is actually attached and running
  (not just Python) - see § 4c step 4.
- **Dashboard shows only sample data** - `Web.config`'s `SignalsCsvPath`/
  `ResultsCsvPath` is blank or points at a file that doesn't exist yet
  (the Logger EA hasn't written one, or the path is wrong for this
  machine).
- **Two systems' trades look mixed together in one log** - check the
  `source`/`comment` column (`Telegram_Sig` vs `Claude_Sig` vs the
  Confluence EA's own comment) and the magic number (§ 5 table) - every
  log in this repo tags its origin for exactly this reason.
