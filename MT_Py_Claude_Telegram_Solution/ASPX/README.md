# TelegramSMC Dashboard (ASP.NET / ASPX)

A single, self-contained Web Forms page that reads the two CSV logs written
by `MQL5/Experts/TelegramSMC_Copier.mq5` and
`MQL5/Experts/TelegramSMC_TradeLogger.mq5` and renders a read-only dashboard:
KPI tiles, a cumulative P/L curve, a close-reason breakdown, an
accepted/rejected outcome split, and the latest signals and trades as
tables. It never writes to either CSV.

## Files

| File | Role |
|---|---|
| `Dashboard.aspx` | The whole page - markup, C# (inline `<script runat="server">`, no code-behind DLL), and client-side JS/SVG charts |
| `Web.config` | `appSettings` for the CSV paths, targetFramework, and a couple of defensive IIS settings |
| `App_Data/TelegramSMC_Signals.sample.csv` | Bundled sample data, shown (with a banner) whenever `SignalsCsvPath` isn't set or doesn't resolve to a real file |
| `App_Data/TelegramSMC_Results.sample.csv` | Same, for the results log |

## Requirements

- IIS (or IIS Express) with **ASP.NET (.NET Framework, Web Forms)** enabled -
  this is a classic `.aspx` page, not ASP.NET Core / Razor Pages. Any
  Windows Server with the "Web Server (IIS)" role plus "ASP.NET 4.8" under
  Application Development has what it needs.
- No database, no NuGet packages, no compiled assembly to publish - drop
  the four files above into a site (or a virtual directory/application) and
  it runs. ASP.NET compiles the page on first request.

## Deploying

1. Copy the `ASPX/` folder's contents into an IIS site or application root
   (keep `App_Data/` alongside `Dashboard.aspx` and `Web.config`).
2. Open `Web.config` and set the two `appSettings`:

   ```xml
   <add key="SignalsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Signals.csv" />
   <add key="ResultsCsvPath" value="C:\...\MQL5\Files\TelegramSMC_Results.csv" />
   ```

   **If the dashboard runs on the same Windows machine as the MT5
   terminal**, point these straight at the terminal's data folder - in MT5,
   File → Open Data Folder → `MQL5\Files\`. Note the two EAs can end up
   writing to *different* folders: `TelegramSMC_Copier.mq5` always writes
   `TelegramSMC_Signals.csv` to this terminal's own `MQL5\Files\`, while
   `TelegramSMC_TradeLogger.mq5` writes `TelegramSMC_Results.csv` there too
   **unless** its `InpUseCommonFolder` input is set to `true`, in which case
   it goes to the shared `Common\Files\` folder instead (the Copier has no
   such option). If results seem to never show up, check that setting
   first.

   **If the dashboard runs elsewhere** (a separate web server, a VPS), the
   page needs some way to see those files:
   - a UNC path (`\\MT5-BOX\C$\Users\...\MQL5\Files\...`) that the
     application pool's identity account can read, or
   - a scheduled task on the MT5 machine that copies/syncs the two CSVs to
     wherever the site's `App_Data` (or another readable folder) lives, on
     whatever interval you want the dashboard to refresh at.

   Leaving either setting blank, or pointing it at a path that doesn't
   exist, falls back to the bundled sample CSVs under `App_Data` - the page
   always renders something, with a banner explaining it's sample data.
3. Browse to `Dashboard.aspx`. There's a manual **Refresh** button (each
   request re-reads the CSVs fresh - nothing is cached) and a **Toggle
   theme** button (remembered per-browser via `localStorage`).

## What it shows

- **KPI row** - signals received and accepted-rate, trades closed and how
  many positions are still open, net P/L, win rate with average win/loss
  size, and average holding time.
- **Cumulative net P/L** - a line chart of running total across closed
  trades in close-time order, with a hover crosshair and tooltip.
- **Close reasons** - a bar per reason (Stop Loss / Take Profit / Manual /
  EA-or-Script / …), read straight from the `close_reason` column, which
  the Logger EA itself fills from MT5's own `DEAL_REASON` - never guessed
  from price.
- **Signal outcomes** - accepted vs rejected, plus the top 5 rejection
  reasons (from `sanity_reason`/`smc_reason`) so you can see at a glance
  what's filtering signals out most often.
- **Recent Signals / Recent Trades** - the latest 50 rows of each CSV,
  newest first, with the full message/reason text available on hover
  (`title` attribute) even where the table cell truncates it.

## Security notes

- The page only ever **reads** the two CSVs - there is no upload, edit, or
  delete path anywhere in it.
- All CSV field values are HTML-escaped before being written into the page
  (`Html()`/`HtmlAttr()` in the server script), so a Telegram message
  containing `<`, `>`, `&`, or `"` can't inject markup.
- `Web.config` blocks direct HTTP access to `*.csv` and `*.config` as
  defense in depth; `App_Data` is denied by ASP.NET's own default handler
  mapping regardless.
- This dashboard has **no authentication of its own**. If it's reachable
  from anywhere untrusted, put it behind IIS Windows Authentication, an
  reverse-proxy auth layer, or a network restriction (it's a small internal
  tool, not designed to be public) - it reads trading data and, indirectly
  via the raw message text, whatever your signal channel posts.

## Honest limitations

- **No live push.** This is a plain server-rendered page - it re-reads the
  CSVs on every HTTP request, not on a timer or a websocket. Use the
  Refresh button, a browser auto-refresh extension, or add a
  `<meta http-equiv="refresh">` yourself if you want it to update
  unattended.
- **No pagination.** Recent Signals / Recent Trades cap at the latest 50
  rows each; the KPIs and charts, however, are computed over the *entire*
  file every time, so they stay accurate however large the logs get.
- **Single account/EA pair per page.** If you run more than one
  Copier+Logger pair (different symbols, different magic numbers), point
  separate copies of this page (different `Web.config` `appSettings`) at
  each, or duplicate `Dashboard.aspx` under a different name.
