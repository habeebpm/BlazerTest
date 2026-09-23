# XTR_Export

Exports XAUUSD M5/M15/H1 bars from a running MT5 terminal into the exact
CSV shape XTR (an external Claude-chat trade-analysis workflow, run
outside this repo) asked for, refreshed on every new **M1** candle close, and
optionally pushed straight to a Google Drive folder so XTR can read it
there instead of pulling from Twelve Data.

This is a fourth, independent solution alongside the Telegram Copier, the
XAUUSD Confluence EA, and the Claude-SMC Trader - it shares no code, config,
or magic number with any of them (it doesn't even place trades; it only
reads bars). See the top-level `SETUP.md` for how it fits with the rest of
this repo.

## What it produces

One CSV per timeframe in `--out-dir` (default `xtr_data/`):

```
XAUUSD_M5.csv
XAUUSD_M15.csv
XAUUSD_H1.csv
XAUUSD_manifest.json
```

Each CSV: `datetime,open,high,low,close,volume` - one row per **closed**
bar (the still-forming bar is always dropped), latest 200 bars by default
(`--bars`, within XTR's requested 150-200), sorted ascending, no gaps, no
duplicate timestamps, OHLC rounded to the symbol's own quote precision.

**`datetime` is genuine UTC**, not broker-server wall clock. MT5's own
`copy_rates_*` hands back bar times as the broker server's own wall clock,
numerically encoded as if it were a UTC epoch - which is essentially never
actually true (gold/CFD brokers commonly run EET/EEST, UTC+2/+3, and shift
with their own DST calendar). Silently treating that raw value as UTC is
exactly the kind of mislabeled-timestamp bug that would quietly throw off
every EMA/MACD reading downstream. `exporter.detect_broker_utc_offset_seconds()`
recovers the broker's true offset automatically - by comparing the
broker's own live tick time against this machine's real clock, re-checked
every export cycle - and converts every timestamp before writing, so
nothing reading these files ever has to know or guess the broker's offset.
That offset is still recorded in `XAUUSD_manifest.json` for anyone who
wants to double check it.

`XAUUSD_manifest.json` (written alongside the CSVs, refreshed every
cycle):

```json
{
  "symbol": "XAUUSD",
  "datetime_timezone": "UTC",
  "datetime_format": "%Y-%m-%d %H:%M:%S (UTC, no offset suffix)",
  "quote_digits": 2,
  "broker_utc_offset_hours": 3.0,
  "bars_per_file": {"M5": 200, "M15": 200, "H1": 200},
  "exported_at_utc": "2026-09-22 12:05:00"
}
```

## Setup

```bash
cd XTR_Export/python
pip install -r requirements.txt
```

Same connection convention as the rest of this repo - set MT5 credentials
or leave MT5 already logged in:

```bash
python xtr_export.py --check     # connect, print quote precision, export once, exit
python xtr_export.py --once      # one export cycle, exit
python xtr_export.py             # loop: export on every new M1 candle close
```

**Export pace:** the export runs each time an **M1** bar closes. Every
file still holds closed bars only. Be clear on what this gains: a new
M5/M15/H1 bar closes on the same tick as an M1 bar, so it reaches its file
just as fast with `--trigger-timeframe M5` - within `--poll-seconds`
either way. What the M1 trigger adds is a **once-a-minute heartbeat**
(`exported_at_utc` in the manifest proves the exporter is alive) and, if
you want minute-level data, a current M1 file: add it with
`--timeframes M1,M5,M15,H1` (writes an extra `XAUUSD_M1.csv`, refreshed
every minute).

With `--upload-drive`, a file is only re-uploaded when its content changed:
the manifest every minute, `XAUUSD_M5.csv` every 5 minutes, M15 every 15,
H1 hourly - far inside the Drive API's free quota. Google Drive for
Desktop (Option A below) likewise syncs only what changed.

Useful flags: `--symbol` (default XAUUSD), `--timeframes M5,M15,H1`
(default, all three required - XTR's HTF-alignment grading needs all of
them), `--trigger-timeframe M1` (default), `--bars 200`, `--out-dir xtr_data`,
`--poll-seconds 5`, `--login`/
`--password`/`--server`/`--terminal-path`, `-v`.

### Test without a live account

```bash
cd XTR_Export/python
python selftest.py
```

Entirely offline - a fake gateway exercises the UTC-offset detection, bar
formatting/rounding/truncation, the full export pipeline, and the Drive
create-vs-update logic (via a fake Drive service, no
google-api-python-client install needed for this). No MT5 terminal, no
Google credentials, no network.

## Getting the files onto Google Drive

Two options, pick based on where MT5 runs:

### Option A - MT5 machine has a desktop (recommended if available)

Install **Google Drive for Desktop** and point it at `--out-dir`. Zero
code, zero credentials to manage in this repo - it mirrors continuously
and survives restarts, which is the simplest way to satisfy "always
copy". Share that synced Drive folder with whoever/whatever reads XTR's
data.

### Option B - MT5 runs headless (a VPS with no GUI)

`--upload-drive` pushes every export straight to a Drive folder via the
Drive API, using a service account (no interactive browser login - it
runs unattended in the poll loop, so there's no human available to click
"Allow" when a token expires the way the regular OAuth flow would need).

One-time setup:

1. In the [Google Cloud Console](https://console.cloud.google.com/),
   create a project (or reuse one), then **APIs & Services > Library** ->
   enable the **Google Drive API**.
2. **APIs & Services > Credentials > Create Credentials > Service
   Account**. Give it any name. After creating it, open it, go to **Keys
   > Add Key > Create new key > JSON**, and download the key file - this
   is `--drive-credentials`.
3. **Share the target Drive folder** with the service account's email
   address (looks like `xtr-export@your-project.iam.gserviceaccount.com`,
   shown on the service account's page), Editor access. This step is not
   optional - a service account has no storage quota of its own, so an
   upload into an unshared location fails.
4. Copy the target folder's id from its Drive URL
   (`drive.google.com/drive/folders/<this-part>`) - this is
   `--drive-folder-id`.

```bash
python xtr_export.py --upload-drive \
    --drive-folder-id 1AbCdEfGhIjKlMnOpQrStUvWxYz \
    --drive-credentials /path/to/service-account.json
```

Every export UPDATEs the same Drive file (a small `.drive_ids.json` cache
in `--out-dir` remembers each file's id after its first upload) rather
than creating a new copy each cycle - so the link shared with XTR never
goes stale, and the folder doesn't fill with thousands of near-duplicates.

## Honest limitations

- **Freshness is tied to M1 candle closes** (`--trigger-timeframe`),
  checked every `--poll-seconds` (default 5s) - not truly tick-by-tick. A candle that closes right after
  a poll is picked up on the next one, so worst-case staleness is about
  one poll interval past the actual close, not zero.
- **The broker-UTC-offset detection assumes this machine's own clock is
  correct** (NTP-synced) - if it's wrong, the "true UTC" conversion is
  wrong by the same amount, silently. Worth a sanity check against a known
  clock the first time this runs on a new machine. It also assumes the
  broker's own tick is genuinely recent - `detect_broker_utc_offset_seconds()`
  raises rather than exporting under an offset computed from a stale tick
  (e.g. the market is closed, so the last trade was hours or days ago),
  which shows up as an "Error during export cycle" log line and a skipped
  export until a live tick is available again - not silent bad data, but
  also not a continuously-updating file while the market's shut.
- **A DST transition mid-window is not retroactively corrected.** The
  offset is detected fresh every export cycle specifically so a seasonal
  clock change on the broker's server (EET/EEST brokers commonly shift
  twice a year) is picked up promptly for NEW bars - but it's applied
  uniformly to the whole `--bars` window on each export, so the older
  bars that were originally recorded under the OLD offset get relabeled
  with the NEW one until they age out of the window (up to ~8 days for
  the H1 file at the default `--bars 200`). There's no way to recover the
  true historical offset after the fact from what MT5 exposes. If this
  matters for your use case, treat the day of a broker DST change as a
  known rough patch, or shrink `--bars` on H1 to reduce the affected
  window.
- **No retry/backoff on Drive upload failures** beyond the poll loop's own
  "log and try again next cycle" - a sustained Drive outage means stale
  files there (though the local CSVs in `--out-dir` keep updating
  regardless) until it recovers.
- **Read-only against MT5.** This never places, modifies, or closes an
  order - it only reads bars, exactly like `ClaudeSMC_Trader/python/
  backtest.py`'s historical-data path, just live instead of from a CSV.
