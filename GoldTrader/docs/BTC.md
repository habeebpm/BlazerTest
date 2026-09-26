# GoldTrader for Bitcoin (BTCUSD)

A second, independent instance of the same system for BTCUSD. No Telegram
signals: every entry is Claude's, with the same brain as gold - the 2-of-3
pre-screen, Claude's "full" verdict, smart-money structure, M15/H1
alignment, the economic-calendar blackout, the breaking-news check (crypto
feeds), the ML advisor, the daily cap and budget and the margin guard - and
Bitcoin's own trading rules. Gold keeps running exactly as before.

| Part | Gold | Bitcoin |
|---|---|---|
| Program | `start.bat` (`GT_ARGS`) | the same `start.bat`, same window (`BTC_ARGS`, runs `--profile btc`) |
| EA | UnifiedTrader_EA on XAUUSD | **BTCTrader_EA** on BTCUSD |
| Entries | Claude + Telegram signals | Claude only |
| Magic number | 20260921 (Claude), 20260922 (Telegram) | 20260931 |
| Files | `logs\` | `logs\btc\` |
| Journal in Drive | `GoldTrader_*` | `BTC_*` |

## Bitcoin's rules (app/profiles.py)

Gold's tested shape - a stop of about one M15 ATR, the profit locked at +1R,
then trailed 0.5R - sized from Bitcoin's own volatility instead of fixed
dollars, so it works at any BTC price:

| Rule | Bitcoin | (Gold) |
|---|---|---|
| Stop | 1.0 x ATR14 on M15, kept between 0.20% and 2.0% of the price | $6 |
| Lock | at +1R the stop moves there (R = the trade's own stop) | +$6 |
| Trail | 0.5R behind price after the lock | $3 |
| Risk per trade | 2% of equity (lot sized from the stop) | 2% |
| Daily loss cap | **5%** of the account's day-start equity (see below); **10% while gold is closed** | 10% |
| Positions per direction | 3 (the 5% budget allows about 2 at once); **5 while gold is closed** | 5 |
| Trading hours | around the clock, 7 days (no Friday cutoff) | New York windows |
| Spread limit | 0.06% of the price (thin weekend / news spreads) | 50 points |
| Lot cap | 5 lots | 5 lots |

**Saturday and Sunday: Bitcoin at full allowance.** From gold's Friday close
(17:00 New York = Saturday 01:00 Oman) to its Sunday reopen (18:00 New York =
Monday 02:00 Oman) the account trades only Bitcoin, so BTC gets the allowance
gold uses on weekdays: **10% daily cap and 5 positions per direction**. Risk
per trade (2%), stop, lock and trail stay the same. The log says `Gold market
closed: weekend allowance on` / `Gold market open: back to 5% daily cap`; the
dashboard's rules line shows `ON NOW`. Positions opened at the weekend keep
running after gold reopens; the weekday 5% budget then simply allows no new
BTC entry until their risk fits. Change it in `app/profiles.py`
(`weekend_max_daily_loss_pct`, `weekend_max_positions_per_direction`; 0 = off).

**Both daily caps read the whole account.** Gold and BTC share one MT5
account, so each cap measures the account's equity, not its own trades: BTC
stops opening once the account is 5% down on the day (from either market),
gold (Claude and Telegram) at 10%. Neither closes open trades; each also
refuses an entry when today's loss + **every open stop on the account (gold
and BTC)** + the new stop would pass its cap - so the account's worst day is
bounded by the larger cap (10%), not the sum. In practice: on weekdays BTC
opens little while gold already has several trades at risk, and on a Monday
gold may wait until BTC's weekend trades are locked or closed. The margin
guard counts every open stop on the account too.

Also scaled to Bitcoin's price: a liquidity sweep must pierce 5% of an M15
ATR (gold: 3 pips), and a market order may fill up to 2000 points ($20) from
the requested price (gold: 30 points).

These are a principled starting point, **not yet backtested on real BTC
prices** (see "Test it" below). Change one in `start.bat` `BTC_ARGS`, e.g.
`--max-daily-loss 4`, `--risk-percent 1`, `--max-positions 2`.

What Claude is told about Bitcoin: it trades 24/7 with thin weekend and
late-US liquidity; stop hunts and liquidation cascades around round numbers
($1,000 / $5,000 / $10,000 marks) and the previous day/week highs and lows are
routine; it behaves like a high-beta risk asset with a loose link to the
dollar; hawkish US data usually pressures it. The breaking-news check looks
for crypto shocks too: exchange hacks or insolvencies, stablecoin de-pegs,
SEC / ETF decisions, crypto bans, large holders moving coins.

## Set it up (after gold is running)

1. Copy in the update and run `setup.bat` - it installs and compiles
   **BTCTrader_EA** too (`0 error(s)` for all three EAs).
2. MT5: add your broker's Bitcoin symbol to Market Watch (BTCUSD, or with a
   suffix such as BTCUSDm). Check its contract (usually 1 lot = 1 BTC), spread
   and leverage for crypto.
3. Open a **BTCUSD chart** (any timeframe) -> drag **BTCTrader_EA** on it ->
   Inputs -> **Load** `BTCTrader_EA_Default.set` -> **Common** tab -> tick
   **Allow DLL imports** -> OK, Algo Trading on. It starts in dry-run
   (`InpDryRun=true`). **You should see** within a few minutes, in your Drive
   folder next to gold's `XAUUSD_` files: `BTCUSD_M5.csv`, `BTCUSD_M15.csv`,
   `BTCUSD_H1.csv`, `BTCUSD_manifest.json` (the preset copies to
   `G:\My Drive\MyMQChartDrive`, as gold's does - change
   `InpXtrExportCopyTo` if yours differs).
4. Gold chart: reopen UnifiedTrader_EA's inputs and check
   `InpBtcMagicNumber = 20260931` (it is, after the update) - your Telegram
   buttons get a new row: **PauseBtcHab / ResumeBtcHab**.
5. First run in dry-run: open `start.bat` in Notepad, change
   `set BTC_ARGS=--live` to `set BTC_ARGS=` plus `--symbol BTCUSDm` if your
   broker uses a suffix (empty = dry-run), save, double-click it. Gold and
   Bitcoin run in the same window; every line starts with `GOLD |` or `BTC |`.
   **You should see** `BTC  | ... Profile btc: BTCUSD, magic 20260931, logs
   ...\logs\btc` and, each M15 bar, a Claude call or the reason there was none.
6. Go live on demo: `set BTC_ARGS=--live` back in `start.bat`,
   `InpDryRun=false` on BTCTrader_EA. Gold only: `set BTC_ARGS=off`.

## Every day

- Dashboard: the **Gold | BTC** switch at the top (appears once the BTC
  instance has written its first report).
- Telegram: `PauseBtcHab` closes BTC trades and stops new ones,
  `ResumeBtcHab` restarts; `PauseHab` / `ResumeHab` now cover BTC too.
- Journal in Drive: `BTC_trades.csv`, `BTC_claude_decisions.csv`.
- The BTC instance runs its own scheduled jobs from `settings.ini` (ML
  retrain, conviction report, weekly scorecard - Claude only, in
  `logs\btc\`); the Telegram relay and price export run only with gold.
- A second copy of the same instance never starts (each holds
  `logs\instance.lock` / `logs\btc\instance.lock`): an old `start_btc.bat`
  opened next to the new `start.bat` just says `already running`.
- Claude cost: Bitcoin is evaluated 24/7 (168 hours a week vs gold's 52), so
  expect roughly 2-3 times gold's call count; the log shows the real tokens
  per call (`Claude call: ... tokens`).

## Test it on your broker's BTC prices

**Chart data in Google Drive, like gold's.** BTCTrader_EA keeps the last
5000 closed bars of M5 / M15 / H1 in Drive (about 17 / 52 / 208 days), UTC,
refreshed each bar - Claude reads them from Drive, and they replay directly:

    python goldtrader.py backtest --profile btc --csv-folder "G:\My Drive\MyMQChartDrive" --mechanical --yes

(H4, D1 and W1 are built from H1; after the warm-up that is about 7 weeks of
BTC.) For a longer test, double-click **`backtest_btc.bat`** (MT5 open). It runs the last 12 months of
your broker's BTCUSD prices through the BTC rules (the mechanical three legs -
free, no Claude calls) and copies the summary, every trade and the price
history to Google Drive (`BTC_backtest_*`, `BTC_prices_*`). Then ask Claude
to analyse it - with the prices in Drive, the stop, lock, trail, hours and
spread limit can be tuned on real data before any real money.

## Limits

- No backtest on real BTC data yet: the Drive price files (or
  `backtest_btc.bat`) make one possible - then demo first.
- Weekend gaps: the stop is a price level, so a weekend spike can fill it
  worse; the 0.06% spread limit keeps entries out of the thinnest spreads.
- Many brokers give crypto low leverage (1:2 to 1:20); the margin guard then
  simply allows fewer positions.
- Claude's analysis of Bitcoin has not been tested live - the scorecard
  (`TOO EARLY` until 30 trades) is the test, as for gold.
