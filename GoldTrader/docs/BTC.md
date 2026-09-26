# GoldTrader for Bitcoin (BTCUSD)

A second, independent instance of the same system for BTCUSD. No Telegram
signals: every entry is Claude's, with the same brain as gold - the 2-of-3
pre-screen, Claude's "full" verdict, smart-money structure, M15/H1
alignment, the economic-calendar blackout, the breaking-news check (crypto
feeds), the ML advisor, the daily cap and budget and the margin guard - and
Bitcoin's own trading rules. Gold keeps running exactly as before.

| Part | Gold | Bitcoin |
|---|---|---|
| Program | `start.bat` | `start_btc.bat` (`--profile btc`) |
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
| Daily loss cap | **5%** - gold 10% + BTC 5% = at most 15% in one day | 10% |
| Positions per direction | 3 (the 5% budget allows about 2 at once) | 5 |
| Trading hours | around the clock, 7 days (no Friday cutoff) | New York windows |
| Spread limit | 0.06% of the price (thin weekend / news spreads) | 50 points |
| Lot cap | 5 lots | 5 lots |

These are a principled starting point, **not yet backtested on real BTC
prices** (see "Test it" below). Change one in `start_btc.bat` `GT_ARGS`, e.g.
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
   Inputs -> **Load** `BTCTrader_EA_Default.set` -> OK, Algo Trading on. It
   starts in dry-run (`InpDryRun=true`).
4. Gold chart: reopen UnifiedTrader_EA's inputs and check
   `InpBtcMagicNumber = 20260931` (it is, after the update) - your Telegram
   buttons get a new row: **PauseBtcHab / ResumeBtcHab**.
5. First run in dry-run: open `start_btc.bat` in Notepad, remove `--live`,
   add `--symbol BTCUSDm` if your broker uses a suffix, save, double-click it.
   **You should see** `Profile btc: BTCUSD, magic 20260931, logs ...\logs\btc`
   and, each M15 bar, a Claude call or the reason there was none.
6. Go live on demo: `--live` back in `start_btc.bat`, `InpDryRun=false` on
   BTCTrader_EA. Keep both windows (`start.bat` and `start_btc.bat`) open.

## Every day

- Dashboard: the **Gold | BTC** switch at the top (appears once
  `start_btc.bat` has written its first report).
- Telegram: `PauseBtcHab` closes BTC trades and stops new ones,
  `ResumeBtcHab` restarts; `PauseHab` / `ResumeHab` now cover BTC too.
- Journal in Drive: `BTC_trades.csv`, `BTC_claude_decisions.csv`.
- Claude cost: Bitcoin is evaluated 24/7 (168 hours a week vs gold's 52), so
  expect roughly 2-3 times gold's call count; the log shows the real tokens
  per call (`Claude call: ... tokens`).

## Test it on your broker's BTC prices

Double-click **`backtest_btc.bat`** (MT5 open). It runs the last 12 months of
your broker's BTCUSD prices through the BTC rules (the mechanical three legs -
free, no Claude calls) and copies the summary, every trade and the price
history to Google Drive (`BTC_backtest_*`, `BTC_prices_*`). Then ask Claude
to analyse it - with the prices in Drive, the stop, lock, trail, hours and
spread limit can be tuned on real data before any real money.

## Limits

- No backtest on real BTC data yet: run `backtest_btc.bat` and demo first.
- Weekend gaps: the stop is a price level, so a weekend spike can fill it
  worse; the 0.06% spread limit keeps entries out of the thinnest spreads.
- Many brokers give crypto low leverage (1:2 to 1:20); the margin guard then
  simply allows fewer positions.
- Claude's analysis of Bitcoin has not been tested live - the scorecard
  (`TOO EARLY` until 30 trades) is the test, as for gold.
