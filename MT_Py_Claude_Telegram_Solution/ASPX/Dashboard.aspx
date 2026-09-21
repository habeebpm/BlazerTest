<%@ Page Language="C#" AutoEventWireup="true" EnableViewState="false" EnableSessionState="False" ResponseEncoding="utf-8" %>
<%@ Assembly Name="System.Configuration" %>
<%@ Import Namespace="System" %>
<%@ Import Namespace="System.IO" %>
<%@ Import Namespace="System.Text" %>
<%@ Import Namespace="System.Collections.Generic" %>
<%@ Import Namespace="System.Globalization" %>
<%@ Import Namespace="System.Linq" %>
<script runat="server">
    // ==========================================================================
    // TelegramSMC Dashboard - reads TelegramSMC_Signals.csv and
    // TelegramSMC_Results.csv (written by TelegramSMC_Copier.mq5 and
    // TelegramSMC_TradeLogger.mq5) and renders a read-only summary.
    //
    // Point SignalsCsvPath / ResultsCsvPath in Web.config's <appSettings> at the
    // live files - typically the MT5 terminal's MQL5\Files folder, or a UNC
    // path/scheduled copy if this site runs on a different machine than the
    // terminal. If either configured path is missing, the bundled sample data
    // under App_Data is shown instead, with a banner saying so - the page never
    // renders as broken on a fresh deployment.
    //
    // No database, no write access to either CSV: this page only reads them,
    // fresh, on every request.
    // ==========================================================================

    class SignalRow
    {
        public DateTime Time;
        public string Source, ChatId, Action, Direction, SmcReason, SanityReason, OrderType, OrderTicket, Retcode, RawText, Tps;
        public bool SymbolOk, SmcUsed, SmcPass, SanityPass, Accepted, DryRun;
        public double EntryLow, EntryHigh, Sl, OrderPrice, Lots;
    }

    class ResultRow
    {
        public DateTime Time;
        public string Source, EventName, PositionId, OrderTicket, Symbol, Magic, Direction, CloseReason, Comment;
        public double Volume, Price, Sl, Tp, Profit, Swap, Commission, NetProfit, DurationMin, PriceMove;
    }

    // ---------------- minimal RFC4180 CSV reader ----------------
    // The EA always double-quotes data fields (embedded quotes doubled) but
    // writes the header row as a plain unquoted line, so this handles a field
    // that may or may not be quoted, not just one style.
    static List<string> ParseCsvLine(string line)
    {
        var fields = new List<string>();
        var sb = new StringBuilder();
        bool inQuotes = false;
        int i = 0;
        while (i < line.Length)
        {
            char c = line[i];
            if (inQuotes)
            {
                if (c == '"')
                {
                    if (i + 1 < line.Length && line[i + 1] == '"') { sb.Append('"'); i += 2; continue; }
                    inQuotes = false; i++; continue;
                }
                sb.Append(c); i++;
            }
            else
            {
                if (c == '"' && sb.Length == 0) { inQuotes = true; i++; continue; }
                if (c == ',') { fields.Add(sb.ToString()); sb.Length = 0; i++; continue; }
                sb.Append(c); i++;
            }
        }
        fields.Add(sb.ToString());
        return fields;
    }

    static List<Dictionary<string, string>> ReadCsv(string path)
    {
        var rows = new List<Dictionary<string, string>>();
        if (string.IsNullOrEmpty(path) || !File.Exists(path)) return rows;
        using (var reader = new StreamReader(path, Encoding.UTF8))
        {
            string headerLine = reader.ReadLine();
            if (headerLine == null) return rows;
            var headers = ParseCsvLine(headerLine);
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                if (line.Length == 0) continue;
                var fields = ParseCsvLine(line);
                var row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                for (int i = 0; i < headers.Count; i++)
                    row[headers[i].Trim()] = i < fields.Count ? fields[i] : "";
                rows.Add(row);
            }
        }
        return rows;
    }

    static string S(Dictionary<string, string> row, string key)
    {
        string v;
        return row.TryGetValue(key, out v) ? v : "";
    }

    static double D(Dictionary<string, string> row, string key)
    {
        double v;
        return double.TryParse(S(row, key), NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : 0.0;
    }

    static bool B(Dictionary<string, string> row, string key)
    {
        return S(row, key) == "1";
    }

    // MT5's TimeToString(TIME_DATE|TIME_SECONDS) format is "yyyy.MM.dd HH:mm:ss".
    static DateTime T(Dictionary<string, string> row, string key)
    {
        DateTime dt;
        string v = S(row, key);
        if (DateTime.TryParseExact(v, "yyyy.MM.dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out dt))
            return dt;
        DateTime.TryParse(v, CultureInfo.InvariantCulture, DateTimeStyles.None, out dt);
        return dt;
    }

    // ---------------- tiny hand-rolled JSON writer ----------------
    // Avoids depending on an extra serializer assembly reference for what is
    // only ever a handful of flat numbers/strings.
    static string JStr(string s)
    {
        if (s == null) s = "";
        var sb = new StringBuilder();
        sb.Append('"');
        foreach (char c in s)
        {
            if (c == '"') sb.Append("\\\"");
            else if (c == '\\') sb.Append("\\\\");
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\r') { }
            else if (c < 0x20) sb.Append(' ');
            else sb.Append(c);
        }
        sb.Append('"');
        return sb.ToString();
    }

    static string JNum(double d)
    {
        if (double.IsNaN(d) || double.IsInfinity(d)) return "0";
        return d.ToString("0.####", CultureInfo.InvariantCulture);
    }

    static string Money(double v)
    {
        return (v >= 0 ? "+$" : "-$") + Math.Abs(v).ToString("0.00", CultureInfo.InvariantCulture);
    }

    static string FormatDuration(double minutes)
    {
        int total = (int)Math.Round(minutes);
        if (total < 0) total = 0;
        int h = total / 60, m = total % 60;
        return h > 0 ? (h + "h " + m + "m") : (m + "m");
    }

    static string PrettyReason(string raw)
    {
        if (string.IsNullOrEmpty(raw)) return "Other";
        string s = raw;
        if (s.StartsWith("DEAL_REASON_", StringComparison.OrdinalIgnoreCase)) s = s.Substring("DEAL_REASON_".Length);
        switch (s.ToUpperInvariant())
        {
            case "SL": return "Stop Loss";
            case "TP": return "Take Profit";
            case "CLIENT": return "Manual";
            case "EXPERT": return "EA / Script";
            case "SO": return "Stop Out";
            case "ROLLOVER": return "Rollover";
            default: return s.Length > 0 ? (char.ToUpperInvariant(s[0]) + s.Substring(1).ToLowerInvariant()) : "Other";
        }
    }

    static string Html(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
    }

    static string HtmlAttr(string s)
    {
        return Html(s).Replace("\"", "&quot;");
    }

    static string Truncate(string s, int max)
    {
        if (string.IsNullOrEmpty(s)) return "-";
        s = s.Trim();
        return s.Length <= max ? s : s.Substring(0, max - 1) + "…";
    }

    // ---------------- page state, filled in by Page_Load ----------------
    List<SignalRow> _signals = new List<SignalRow>();
    List<ResultRow> _results = new List<ResultRow>();
    bool _usingSampleData;

    string SignalsHtml, TradesHtml, RejectionsHtml;
    string EquityJson, CloseReasonJson;
    int OutcomeAccepted, OutcomeRejected;
    string KpiSignals, KpiAccepted, KpiAcceptedPct, KpiClosed, KpiOpenNow, KpiNetProfit, KpiNetProfitClass,
           KpiWinRate, KpiWinLoss, KpiAvgDuration;
    string AsOf, DataSourceNote;

    protected void Page_Load(object sender, EventArgs e)
    {
        string signalsPath = ResolveConfiguredPath("SignalsCsvPath", "~/App_Data/TelegramSMC_Signals.sample.csv");
        string resultsPath = ResolveConfiguredPath("ResultsCsvPath", "~/App_Data/TelegramSMC_Results.sample.csv");

        bool signalsMissing = !File.Exists(signalsPath);
        bool resultsMissing = !File.Exists(resultsPath);
        if (signalsMissing) signalsPath = Server.MapPath("~/App_Data/TelegramSMC_Signals.sample.csv");
        if (resultsMissing) resultsPath = Server.MapPath("~/App_Data/TelegramSMC_Results.sample.csv");
        _usingSampleData = signalsMissing || resultsMissing;

        LoadSignals(signalsPath);
        LoadResults(resultsPath);

        BuildKpis();
        BuildCharts();
        BuildTables();

        AsOf = DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + " UTC";
        DataSourceNote = _usingSampleData
            ? "Showing bundled sample data - set SignalsCsvPath / ResultsCsvPath in Web.config to your EA's live CSV files."
            : "Live data from the configured CSV paths.";
    }

    string ResolveConfiguredPath(string appSettingKey, string fallbackVirtualPath)
    {
        string configured = System.Configuration.ConfigurationManager.AppSettings[appSettingKey];
        if (string.IsNullOrEmpty(configured)) configured = fallbackVirtualPath;
        if (configured.StartsWith("~")) return Server.MapPath(configured);
        return configured; // an absolute filesystem or UNC path to the MT5 Files folder
    }

    void LoadSignals(string path)
    {
        foreach (var row in ReadCsv(path))
        {
            _signals.Add(new SignalRow
            {
                Time = T(row, "time_utc"),
                // Older logs written before the source column existed default
                // to Telegram_Sig - this dashboard only ever reads CSVs this
                // system's own EA writes, so that default is always correct.
                Source = string.IsNullOrEmpty(S(row, "source")) ? "Telegram_Sig" : S(row, "source"),
                ChatId = S(row, "chat_id"),
                Action = S(row, "action"),
                Direction = S(row, "direction"),
                SymbolOk = B(row, "symbol_ok"),
                EntryLow = D(row, "entry_low"),
                EntryHigh = D(row, "entry_high"),
                Sl = D(row, "sl"),
                Tps = S(row, "tps"),
                SmcUsed = B(row, "smc_used"),
                SmcPass = B(row, "smc_pass"),
                SmcReason = S(row, "smc_reason"),
                SanityPass = B(row, "sanity_pass"),
                SanityReason = S(row, "sanity_reason"),
                Accepted = B(row, "accepted"),
                OrderType = S(row, "order_type"),
                OrderPrice = D(row, "order_price"),
                Lots = D(row, "lots"),
                DryRun = B(row, "dry_run"),
                OrderTicket = S(row, "order_ticket"),
                Retcode = S(row, "retcode"),
                RawText = S(row, "raw_text"),
            });
        }
    }

    void LoadResults(string path)
    {
        foreach (var row in ReadCsv(path))
        {
            _results.Add(new ResultRow
            {
                Time = T(row, "time_utc"),
                Source = string.IsNullOrEmpty(S(row, "source")) ? "Telegram_Sig" : S(row, "source"),
                EventName = S(row, "event"),
                PositionId = S(row, "position_id"),
                OrderTicket = S(row, "order_ticket"),
                Symbol = S(row, "symbol"),
                Magic = S(row, "magic"),
                Direction = S(row, "direction"),
                Volume = D(row, "volume"),
                Price = D(row, "price"),
                Sl = D(row, "sl"),
                Tp = D(row, "tp"),
                Profit = D(row, "profit"),
                Swap = D(row, "swap"),
                Commission = D(row, "commission"),
                NetProfit = D(row, "net_profit"),
                CloseReason = S(row, "close_reason"),
                DurationMin = D(row, "duration_min"),
                PriceMove = D(row, "price_move"),
                Comment = S(row, "comment"),
            });
        }
    }

    void BuildKpis()
    {
        int totalSignals = _signals.Count;
        int accepted = _signals.Count(s => s.Accepted);
        double acceptedPct = totalSignals > 0 ? (100.0 * accepted / totalSignals) : 0.0;

        var closes = _results.Where(r => r.EventName == "CLOSE").OrderBy(r => r.Time).ToList();
        var opens = _results.Where(r => r.EventName == "OPEN").ToList();
        var closedPositionIds = new HashSet<string>(closes.Select(r => r.PositionId));
        int openNow = opens.Count(o => !closedPositionIds.Contains(o.PositionId));

        int closedCount = closes.Count;
        double netTotal = closes.Sum(r => r.NetProfit);
        int wins = closes.Count(r => r.NetProfit > 0);
        int losses = closedCount - wins;
        double winRate = closedCount > 0 ? (100.0 * wins / closedCount) : 0.0;
        double avgWin = wins > 0 ? closes.Where(r => r.NetProfit > 0).Average(r => r.NetProfit) : 0.0;
        double avgLoss = losses > 0 ? closes.Where(r => r.NetProfit <= 0).Average(r => r.NetProfit) : 0.0;
        double avgDuration = closedCount > 0 ? closes.Average(r => r.DurationMin) : 0.0;

        KpiSignals = totalSignals.ToString(CultureInfo.InvariantCulture);
        KpiAccepted = accepted.ToString(CultureInfo.InvariantCulture);
        KpiAcceptedPct = totalSignals > 0 ? (acceptedPct.ToString("0.#", CultureInfo.InvariantCulture) + "% accepted") : "no signals yet";
        KpiClosed = closedCount.ToString(CultureInfo.InvariantCulture);
        KpiOpenNow = openNow.ToString(CultureInfo.InvariantCulture) + " open now";
        KpiNetProfit = Money(netTotal);
        KpiNetProfitClass = netTotal >= 0 ? "kpi-good" : "kpi-critical";
        KpiWinRate = closedCount > 0 ? (winRate.ToString("0.#", CultureInfo.InvariantCulture) + "%") : "-";
        KpiWinLoss = closedCount > 0
            ? ("avg " + Money(avgWin) + " / " + Money(avgLoss))
            : "no closed trades yet";
        KpiAvgDuration = closedCount > 0 ? FormatDuration(avgDuration) : "-";
    }

    void BuildCharts()
    {
        var closes = _results.Where(r => r.EventName == "CLOSE").OrderBy(r => r.Time).ToList();

        var eq = new StringBuilder("[");
        double cum = 0;
        for (int i = 0; i < closes.Count; i++)
        {
            cum += closes[i].NetProfit;
            if (i > 0) eq.Append(",");
            eq.Append("{\"t\":").Append(JStr(closes[i].Time.ToString("MMM d, HH:mm", CultureInfo.InvariantCulture)))
              .Append(",\"v\":").Append(JNum(cum)).Append("}");
        }
        eq.Append("]");
        EquityJson = eq.ToString();

        var reasonCounts = closes
            .GroupBy(r => PrettyReason(r.CloseReason))
            .Select(g => new { Label = g.Key, Count = g.Count() })
            .OrderByDescending(x => x.Count)
            .ToList();
        var cr = new StringBuilder("[");
        for (int i = 0; i < reasonCounts.Count; i++)
        {
            if (i > 0) cr.Append(",");
            cr.Append("{\"label\":").Append(JStr(reasonCounts[i].Label)).Append(",\"count\":").Append(reasonCounts[i].Count).Append("}");
        }
        cr.Append("]");
        CloseReasonJson = cr.ToString();

        OutcomeAccepted = _signals.Count(s => s.Accepted);
        OutcomeRejected = _signals.Count - OutcomeAccepted;
    }

    void BuildTables()
    {
        var recentSignals = _signals.OrderByDescending(s => s.Time).Take(50).ToList();
        var sb = new StringBuilder();
        foreach (var s in recentSignals)
        {
            string statusClass = s.Accepted ? "pill-good" : "pill-critical";
            string statusLabel = s.Accepted ? "Accepted" : "Rejected";
            string reason = !s.Accepted
                ? (!string.IsNullOrEmpty(s.SanityReason) ? s.SanityReason
                   : (!string.IsNullOrEmpty(s.SmcReason) && s.SmcReason != "not required" ? s.SmcReason : ""))
                : "";
            string dirClass = s.Direction == "BUY" ? "dir-buy" : (s.Direction == "SELL" ? "dir-sell" : "");
            sb.Append("<tr>");
            sb.Append("<td class=\"nowrap muted\">").Append(Html(s.Time.ToString("MMM d HH:mm", CultureInfo.InvariantCulture))).Append("</td>");
            sb.Append("<td><span class=\"pill pill-source\">").Append(Html(s.Source)).Append("</span></td>");
            sb.Append("<td>").Append(Html(s.Action)).Append("</td>");
            sb.Append("<td class=\"").Append(dirClass).Append("\">").Append(Html(string.IsNullOrEmpty(s.Direction) ? "-" : s.Direction)).Append("</td>");
            sb.Append("<td><span class=\"pill ").Append(statusClass).Append("\">").Append(statusLabel).Append("</span></td>");
            sb.Append("<td class=\"muted\" title=\"").Append(HtmlAttr(reason)).Append("\">").Append(Html(Truncate(reason, 56))).Append("</td>");
            sb.Append("<td class=\"muted\" title=\"").Append(HtmlAttr(s.RawText)).Append("\">").Append(Html(Truncate(s.RawText, 46))).Append("</td>");
            sb.Append("</tr>");
        }
        SignalsHtml = sb.Length > 0 ? sb.ToString() : "<tr><td colspan=\"7\" class=\"empty-state\">No signals recorded yet.</td></tr>";

        var recentTrades = _results.Where(r => r.EventName == "CLOSE").OrderByDescending(r => r.Time).Take(50).ToList();
        var sb3 = new StringBuilder();
        foreach (var r in recentTrades)
        {
            bool win = r.NetProfit > 0;
            string cls = win ? "text-good" : "text-critical";
            string dirClass = r.Direction == "BUY" ? "dir-buy" : (r.Direction == "SELL" ? "dir-sell" : "");
            sb3.Append("<tr>");
            sb3.Append("<td class=\"nowrap muted\">").Append(Html(r.Time.ToString("MMM d HH:mm", CultureInfo.InvariantCulture))).Append("</td>");
            sb3.Append("<td><span class=\"pill pill-source\">").Append(Html(r.Source)).Append("</span></td>");
            sb3.Append("<td class=\"").Append(dirClass).Append("\">").Append(Html(r.Direction)).Append("</td>");
            sb3.Append("<td class=\"num\">").Append(r.Volume.ToString("0.00", CultureInfo.InvariantCulture)).Append("</td>");
            sb3.Append("<td class=\"num\">").Append(r.Price.ToString("0.00", CultureInfo.InvariantCulture)).Append("</td>");
            sb3.Append("<td class=\"num ").Append(cls).Append("\">").Append(Money(r.NetProfit)).Append("</td>");
            sb3.Append("<td class=\"muted\">").Append(Html(PrettyReason(r.CloseReason))).Append("</td>");
            sb3.Append("<td class=\"num muted\">").Append(FormatDuration(r.DurationMin)).Append("</td>");
            sb3.Append("</tr>");
        }
        TradesHtml = sb3.Length > 0 ? sb3.ToString() : "<tr><td colspan=\"8\" class=\"empty-state\">No closed trades yet.</td></tr>";

        var reasons = _signals.Where(s => !s.Accepted)
            .Select(s => !string.IsNullOrEmpty(s.SanityReason) ? s.SanityReason
                         : (!string.IsNullOrEmpty(s.SmcReason) && s.SmcReason != "not required" ? s.SmcReason : "unspecified"))
            .GroupBy(r => r)
            .Select(g => new { Reason = g.Key, Count = g.Count() })
            .OrderByDescending(x => x.Count)
            .Take(5)
            .ToList();
        var sb4 = new StringBuilder();
        foreach (var r in reasons)
            sb4.Append("<li><span class=\"reason-count\">").Append(r.Count).Append("</span><span class=\"reason-text\">")
               .Append(Html(Truncate(r.Reason, 88))).Append("</span></li>");
        RejectionsHtml = sb4.Length > 0 ? sb4.ToString() : "<li class=\"muted\">No rejected signals yet.</li>";
    }
</script>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>TelegramSMC Dashboard</title>
<script>
  // Applied before paint to avoid a light-to-dark flash on load.
  (function () {
    try {
      var stored = localStorage.getItem('tsmc-theme');
      if (stored === 'dark' || stored === 'light') document.documentElement.setAttribute('data-theme', stored);
    } catch (e) { }
  })();
</script>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page-plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
    --series-2: #eb6834;
    --series-3: #1baf7a;
    --series-4: #eda100;
    --status-good: #0ca30c;
    --status-critical: #d03b3b;
    --success-text: #006300;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page-plane: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --gridline: #2c2c2a;
      --baseline: #383835;
      --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
      --series-2: #d95926;
      --series-3: #199e70;
      --series-4: #c98500;
      --success-text: #0ca30c;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page-plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --gridline: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
    --series-4: #c98500;
    --success-text: #0ca30c;
  }

  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--page-plane);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--series-1); }

  .wrap { max-width: 1180px; margin: 0 auto; padding: 20px 20px 60px; }

  .topbar {
    display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
    gap: 8px 20px; margin-bottom: 4px;
  }
  .topbar h1 { font-size: 20px; margin: 0; font-weight: 700; letter-spacing: -0.01em; }
  .topbar .meta { color: var(--text-muted); font-size: 12.5px; }
  .topbar .actions { display: flex; align-items: center; gap: 10px; }
  .btn {
    appearance: none; border: 1px solid var(--border); background: var(--surface-1);
    color: var(--text-primary); border-radius: 6px; padding: 6px 12px; font-size: 12.5px;
    cursor: pointer; font-family: inherit;
  }
  .btn:hover { border-color: var(--text-muted); }

  .banner {
    background: var(--surface-1); border: 1px solid var(--border); border-left: 3px solid var(--series-4);
    border-radius: 6px; padding: 9px 14px; font-size: 12.5px; color: var(--text-secondary);
    margin: 14px 0 20px;
  }

  .kpi-row {
    display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px;
    margin-bottom: 22px;
  }
  @media (max-width: 980px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 620px) { .kpi-row { grid-template-columns: repeat(2, 1fr); } }
  .kpi {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 14px 12px;
  }
  .kpi-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin-bottom: 6px; }
  .kpi-value { font-size: 24px; font-weight: 700; line-height: 1.1; }
  .kpi-sub { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
  .kpi-good .kpi-value { color: var(--success-text); }
  .kpi-critical .kpi-value { color: var(--status-critical); }

  .panel {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px 18px; margin-bottom: 16px;
  }
  .panel h2 { font-size: 13.5px; margin: 0 0 4px; font-weight: 700; }
  .panel .panel-sub { font-size: 12px; color: var(--text-muted); margin-bottom: 12px; }

  .grid-2 { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; }
  @media (max-width: 820px) { .grid-2 { grid-template-columns: 1fr; } }

  .chart-box { position: relative; }
  .chart-svg { width: 100%; height: auto; display: block; overflow: visible; }
  .chart-baseline { stroke: var(--baseline); stroke-width: 1; }
  .chart-area { fill: var(--series-1); opacity: 0.08; stroke: none; }
  .chart-line { fill: none; stroke: var(--series-1); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .chart-dot { fill: var(--series-1); }
  .chart-hover-dot { fill: var(--series-1); stroke: var(--surface-1); stroke-width: 2; }
  .chart-crosshair { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 2 3; }
  .chart-hit { fill: transparent; cursor: crosshair; }
  .chart-tooltip {
    position: absolute; transform: translate(-50%, -115%); pointer-events: none;
    background: var(--text-primary); color: var(--surface-1); border-radius: 6px;
    padding: 6px 9px; font-size: 11.5px; white-space: nowrap; box-shadow: 0 2px 8px rgba(0,0,0,0.18);
  }
  .tt-title { color: var(--text-muted); font-size: 10.5px; margin-bottom: 2px; }
  :root[data-theme="dark"] .tt-title, :root:where(:not([data-theme="light"])) .chart-tooltip .tt-title { color: #c3c2b7; }
  .tt-value { font-weight: 700; font-variant-numeric: tabular-nums; }

  .legend-row { display: flex; gap: 18px; margin-top: 12px; font-size: 12.5px; flex-wrap: wrap; }
  .legend-item { display: inline-flex; align-items: center; gap: 6px; color: var(--text-secondary); }
  .legend-item b { color: var(--text-primary); font-variant-numeric: tabular-nums; }
  .swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .swatch-good { background: var(--status-good); }
  .swatch-critical { background: var(--status-critical); }

  .reason-bars { display: flex; flex-direction: column; gap: 10px; }
  .reason-bar-row { display: grid; grid-template-columns: 88px 1fr 34px; align-items: center; gap: 10px; }
  .reason-bar-label { font-size: 12.5px; color: var(--text-secondary); }
  .reason-bar-track { background: var(--gridline); border-radius: 4px; height: 14px; overflow: hidden; }
  .reason-bar-fill { display: block; height: 100%; border-radius: 4px; }
  .reason-bar-count { font-size: 12.5px; text-align: right; font-variant-numeric: tabular-nums; color: var(--text-secondary); }

  .rejections-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .rejections-list li { display: flex; gap: 10px; align-items: baseline; font-size: 12.5px; }
  .reason-count {
    flex: 0 0 auto; background: var(--gridline); color: var(--text-secondary); border-radius: 10px;
    padding: 1px 8px; font-size: 11px; font-variant-numeric: tabular-nums; min-width: 18px; text-align: center;
  }
  .reason-text { color: var(--text-secondary); }

  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  thead th {
    text-align: left; font-weight: 600; color: var(--text-muted); text-transform: uppercase;
    font-size: 10.5px; letter-spacing: 0.03em; padding: 0 10px 8px; border-bottom: 1px solid var(--gridline);
  }
  tbody td { padding: 8px 10px; border-bottom: 1px solid var(--gridline); vertical-align: top; }
  tbody tr:last-child td { border-bottom: none; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.nowrap { white-space: nowrap; }
  .muted { color: var(--text-secondary); }
  .text-good { color: var(--success-text); font-weight: 600; }
  .text-critical { color: var(--status-critical); font-weight: 600; }
  .dir-buy { color: var(--status-good); font-weight: 600; }
  .dir-sell { color: var(--status-critical); font-weight: 600; }
  .empty-state { color: var(--text-muted); text-align: center; padding: 18px 0 !important; }

  .pill {
    display: inline-block; padding: 2px 9px; border-radius: 10px; font-size: 11px; font-weight: 600;
  }
  .pill-good { background: rgba(12,163,12,0.13); color: var(--success-text); }
  .pill-critical { background: rgba(208,59,59,0.13); color: var(--status-critical); }
  /* Source is an identity tag (which system generated the row), not a
     good/bad judgment, so it uses the categorical series color rather than
     the status palette above - keeps it visually distinct from Accepted/
     Rejected at a glance. */
  .pill-source { background: rgba(42,120,214,0.13); color: var(--series-1); font-weight: 600; }

  .table-scroll { overflow-x: auto; }
  footer.foot { text-align: center; color: var(--text-muted); font-size: 11.5px; margin-top: 26px; }
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar">
    <h1>TelegramSMC Dashboard</h1>
    <div class="actions">
      <span class="meta">As of <%= AsOf %></span>
      <button type="button" class="btn" onclick="location.reload()">Refresh</button>
      <button type="button" class="btn" id="theme-toggle" onclick="toggleTheme()">Toggle theme</button>
    </div>
  </div>

  <% if (_usingSampleData) { %>
  <div class="banner"><%= Html(DataSourceNote) %></div>
  <% } %>

  <div class="kpi-row">
    <div class="kpi">
      <div class="kpi-label">Signals Received</div>
      <div class="kpi-value"><%= KpiSignals %></div>
      <div class="kpi-sub"><%= KpiAcceptedPct %></div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Accepted</div>
      <div class="kpi-value"><%= KpiAccepted %></div>
      <div class="kpi-sub">copied to MT5</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Trades Closed</div>
      <div class="kpi-value"><%= KpiClosed %></div>
      <div class="kpi-sub"><%= KpiOpenNow %></div>
    </div>
    <div class="kpi <%= KpiNetProfitClass %>">
      <div class="kpi-label">Net P/L</div>
      <div class="kpi-value"><%= KpiNetProfit %></div>
      <div class="kpi-sub">all closed trades</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value"><%= KpiWinRate %></div>
      <div class="kpi-sub"><%= KpiWinLoss %></div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg Duration</div>
      <div class="kpi-value"><%= KpiAvgDuration %></div>
      <div class="kpi-sub">per closed trade</div>
    </div>
  </div>

  <div class="panel">
    <h2>Cumulative Net P/L</h2>
    <div class="panel-sub">Running total across closed trades, in order of close time</div>
    <div id="equity-chart" class="chart-box"></div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <h2>Close Reasons</h2>
      <div class="panel-sub">Why each closed trade exited</div>
      <div id="reason-chart"></div>
    </div>
    <div class="panel">
      <h2>Signal Outcomes</h2>
      <div class="panel-sub">Every message evaluated, accepted vs rejected</div>
      <div id="outcome-chart"></div>
      <h2 style="margin-top:18px;">Top Rejection Reasons</h2>
      <ul class="rejections-list"><%= RejectionsHtml %></ul>
    </div>
  </div>

  <div class="panel">
    <h2>Recent Signals</h2>
    <div class="panel-sub">Latest 50 messages evaluated, accepted or not</div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Time</th><th>Source</th><th>Action</th><th>Dir</th><th>Status</th><th>Reason</th><th>Message</th></tr></thead>
        <tbody><%= SignalsHtml %></tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Recent Trades</h2>
    <div class="panel-sub">Latest 50 closed trades</div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Time</th><th>Source</th><th>Dir</th><th class="num">Lots</th><th class="num">Price</th><th class="num">Net P/L</th><th>Close Reason</th><th class="num">Duration</th></tr></thead>
        <tbody><%= TradesHtml %></tbody>
      </table>
    </div>
  </div>

  <footer class="foot">TelegramSMC_Copier.mq5 + TelegramSMC_TradeLogger.mq5 &middot; read-only, no write access to either CSV</footer>
</div>

<script>
  var EQUITY_DATA = <%= EquityJson %>;
  var CLOSE_REASON_DATA = <%= CloseReasonJson %>;
  var OUTCOME_ACCEPTED = <%= OutcomeAccepted %>;
  var OUTCOME_REJECTED = <%= OutcomeRejected %>;

  function svgEl(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function renderEquityChart(containerId, data) {
    var container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!data.length) {
      container.innerHTML = '<div class="empty-state">No closed trades yet.</div>';
      return;
    }
    var W = 1100, H = 260, padL = 60, padR = 16, padT = 16, padB = 30;
    var innerW = W - padL - padR, innerH = H - padT - padB;

    var values = data.map(function (d) { return d.v; });
    var minV = Math.min(0, Math.min.apply(null, values));
    var maxV = Math.max(0, Math.max.apply(null, values));
    if (minV === maxV) { minV -= 1; maxV += 1; }
    var range = maxV - minV;

    function xAt(i) { return padL + (data.length === 1 ? innerW / 2 : (innerW * i) / (data.length - 1)); }
    function yAt(v) { return padT + innerH - ((v - minV) / range) * innerH; }

    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, 'class': 'chart-svg', role: 'img',
      'aria-label': 'Cumulative net profit over closed trades' });

    var zeroY = yAt(0);
    svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: zeroY, y2: zeroY, 'class': 'chart-baseline' }));

    var areaD = 'M' + xAt(0) + ',' + zeroY;
    for (var i = 0; i < data.length; i++) areaD += ' L' + xAt(i) + ',' + yAt(data[i].v);
    areaD += ' L' + xAt(data.length - 1) + ',' + zeroY + ' Z';
    svg.appendChild(svgEl('path', { d: areaD, 'class': 'chart-area' }));

    var lineD = '';
    for (var j = 0; j < data.length; j++) lineD += (j === 0 ? 'M' : ' L') + xAt(j) + ',' + yAt(data[j].v);
    svg.appendChild(svgEl('path', { d: lineD, 'class': 'chart-line' }));

    var lastI = data.length - 1;
    svg.appendChild(svgEl('circle', { cx: xAt(lastI), cy: yAt(data[lastI].v), r: 4, 'class': 'chart-dot' }));

    var crosshair = svgEl('line', { x1: 0, x2: 0, y1: padT, y2: H - padB, 'class': 'chart-crosshair', style: 'display:none' });
    var hoverDot = svgEl('circle', { r: 4, 'class': 'chart-hover-dot', style: 'display:none' });
    svg.appendChild(crosshair);
    svg.appendChild(hoverDot);

    var hitArea = svgEl('rect', { x: padL, y: padT, width: innerW, height: innerH, 'class': 'chart-hit' });
    svg.appendChild(hitArea);
    container.appendChild(svg);

    var tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    tooltip.style.display = 'none';
    container.appendChild(tooltip);

    hitArea.addEventListener('mousemove', function (evt) {
      var rect = svg.getBoundingClientRect();
      var scaleX = W / rect.width;
      var mx = (evt.clientX - rect.left) * scaleX;
      var idx = Math.round(((mx - padL) / innerW) * (data.length - 1));
      idx = Math.max(0, Math.min(data.length - 1, idx));
      var px = xAt(idx), py = yAt(data[idx].v);
      crosshair.setAttribute('x1', px); crosshair.setAttribute('x2', px);
      crosshair.style.display = '';
      hoverDot.setAttribute('cx', px); hoverDot.setAttribute('cy', py);
      hoverDot.style.display = '';
      var v = data[idx].v;
      tooltip.style.display = '';
      tooltip.style.left = (px / W * 100) + '%';
      tooltip.style.top = (py / H * 100) + '%';
      tooltip.innerHTML = '<div class="tt-title">' + data[idx].t + '</div><div class="tt-value">' +
        (v >= 0 ? '+' : '-') + '$' + Math.abs(v).toFixed(2) + '</div>';
    });
    hitArea.addEventListener('mouseleave', function () {
      crosshair.style.display = 'none';
      hoverDot.style.display = 'none';
      tooltip.style.display = 'none';
    });
  }

  function renderOutcomeChart(containerId, accepted, rejected) {
    var container = document.getElementById(containerId);
    container.innerHTML = '';
    var total = accepted + rejected;
    if (total === 0) {
      container.innerHTML = '<div class="empty-state">No signals yet.</div>';
      return;
    }
    var W = 420, H = 44, gap = 2, barY = 6, barH = 28;
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, 'class': 'chart-svg' });
    var acceptedW = Math.max(0, (accepted / total) * W - gap / 2);
    var rejectedW = Math.max(0, (rejected / total) * W - gap / 2);
    svg.appendChild(svgEl('rect', { x: 0, y: barY, width: acceptedW, height: barH, rx: 4, style: 'fill:var(--status-good)' }));
    svg.appendChild(svgEl('rect', { x: acceptedW + gap, y: barY, width: rejectedW, height: barH, rx: 4, style: 'fill:var(--status-critical)' }));
    container.appendChild(svg);

    var acceptedPct = Math.round((accepted / total) * 100);
    var legend = document.createElement('div');
    legend.className = 'legend-row';
    legend.innerHTML =
      '<span class="legend-item"><span class="swatch swatch-good"></span>Accepted <b>' + accepted + '</b> (' + acceptedPct + '%)</span>' +
      '<span class="legend-item"><span class="swatch swatch-critical"></span>Rejected <b>' + rejected + '</b> (' + (100 - acceptedPct) + '%)</span>';
    container.appendChild(legend);
  }

  function renderReasonBars(containerId, items) {
    var container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!items.length) {
      container.innerHTML = '<div class="empty-state">No closed trades yet.</div>';
      return;
    }
    var palette = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
    var display = items.slice(0, 4);
    if (items.length > 4) {
      var otherCount = 0;
      for (var k = 4; k < items.length; k++) otherCount += items[k].count;
      display.push({ label: 'Other', count: otherCount });
    }
    var max = 0;
    for (var m = 0; m < display.length; m++) if (display[m].count > max) max = display[m].count;

    var list = document.createElement('div');
    list.className = 'reason-bars';
    for (var i = 0; i < display.length; i++) {
      var d = display[i];
      var pct = max > 0 ? (d.count / max) * 100 : 0;
      var row = document.createElement('div');
      row.className = 'reason-bar-row';
      row.innerHTML =
        '<span class="reason-bar-label">' + d.label + '</span>' +
        '<span class="reason-bar-track"><span class="reason-bar-fill" style="width:' + pct + '%;background:' + palette[Math.min(i, 3)] + '"></span></span>' +
        '<span class="reason-bar-count">' + d.count + '</span>';
      list.appendChild(row);
    }
    container.appendChild(list);
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme');
    var next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('tsmc-theme', next); } catch (e) { }
  }

  renderEquityChart('equity-chart', EQUITY_DATA);
  renderReasonBars('reason-chart', CLOSE_REASON_DATA);
  renderOutcomeChart('outcome-chart', OUTCOME_ACCEPTED, OUTCOME_REJECTED);
</script>
</body>
</html>
