<%@ Page Language="C#" EnableViewState="false" %>
<%@ Assembly Name="System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31BF3856AD364E35" %>
<%@ Import Namespace="System.Collections" %>
<%@ Import Namespace="System.Collections.Generic" %>
<%@ Import Namespace="System.Globalization" %>
<%@ Import Namespace="System.IO" %>
<%@ Import Namespace="System.Linq" %>
<%@ Import Namespace="System.Text" %>
<%@ Import Namespace="System.Web.Configuration" %>
<%@ Import Namespace="System.Web.Script.Serialization" %>
<script runat="server">
    // Reads GoldTrader\logs\status.json (written once a minute by start.bat's
    // program, app/status_report.py) and shows it. Read-only: nothing on this
    // page can place, change or close a trade. C# 5 only - IIS compiles this
    // page with the .NET Framework compiler.
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    static readonly string[] Sources = { "Claude", "Telegram" };
    protected Dictionary<string, object> R = new Dictionary<string, object>();
    protected bool HasData, IsSample, Stale;
    protected string DataPath = "", LoadError = "";
    protected double AgeMinutes;
    protected DateTime ReportTime = DateTime.UtcNow;   // "7 days", "30 days" count back from the report's own time

    void Page_Load(object sender, EventArgs e)
    {
        Response.Cache.SetCacheability(HttpCacheability.NoCache);
        Response.Cache.SetNoStore();
        if (Request.QueryString["logout"] == "1")
        {
            FormsAuthentication.SignOut();
            Response.Redirect("Login.aspx?out=1", false);
            Context.ApplicationInstance.CompleteRequest();
            return;
        }
        IsSample = Request.QueryString["sample"] == "1";
        string folder = WebConfigurationManager.AppSettings["LogsFolder"] ?? "";
        if (folder.Trim().Length == 0)
            folder = Path.GetFullPath(Path.Combine(Server.MapPath("~/"), "..", "logs"));
        DataPath = IsSample ? Server.MapPath("~/App_Data/status.sample.json") : Path.Combine(folder, "status.json");
        try
        {
            string json;
            // The program replaces the file while we may be reading it: share everything.
            using (FileStream fs = new FileStream(DataPath, FileMode.Open, FileAccess.Read,
                                                  FileShare.ReadWrite | FileShare.Delete))
            using (StreamReader reader = new StreamReader(fs, Encoding.UTF8))
                json = reader.ReadToEnd();
            JavaScriptSerializer js = new JavaScriptSerializer();
            js.MaxJsonLength = int.MaxValue;
            R = D(js.DeserializeObject(json));
            HasData = R.Count > 0;
        }
        catch (FileNotFoundException) { LoadError = "missing"; }
        catch (DirectoryNotFoundException) { LoadError = "missing"; }
        catch (UnauthorizedAccessException) { LoadError = "denied"; }
        catch (Exception ex) { LoadError = ex.Message; }
        if (HasData)
        {
            DateTime updated = ParseTime(S(R.ContainsKey("updated_utc") ? R["updated_utc"] : null));
            AgeMinutes = updated == DateTime.MinValue ? 9999 : (DateTime.UtcNow - updated).TotalMinutes;
            if (updated != DateTime.MinValue) ReportTime = updated;
            double staleAfter;
            if (!double.TryParse(WebConfigurationManager.AppSettings["StaleMinutes"], NumberStyles.Float, Inv, out staleAfter))
                staleAfter = 5;
            Stale = !IsSample && AgeMinutes > staleAfter;
        }
    }

    // ---- JSON helpers --------------------------------------------------
    static Dictionary<string, object> D(object o)
    {
        Dictionary<string, object> d = o as Dictionary<string, object>;
        return d ?? new Dictionary<string, object>();
    }
    static IList L(object o) { IList l = o as IList; return l ?? new object[0]; }
    static object G(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) ? v : null; }
    static string S(object o) { return o == null ? "" : Convert.ToString(o, Inv); }
    static double N(object o)
    {
        if (o == null) return 0;
        try { return Convert.ToDouble(o, Inv); } catch { return 0; }
    }
    static bool B(object o)
    {
        if (o is bool) return (bool)o;
        string s = S(o).Trim().ToLowerInvariant();
        return s == "true" || s == "1";
    }
    static DateTime ParseTime(string s)
    {
        DateTime t;
        DateTimeStyles utc = DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal;
        if (DateTime.TryParseExact(s, "yyyy.MM.dd HH:mm:ss", Inv, utc, out t)) return t;   // MT5 EA logs
        if (DateTime.TryParse(s, Inv, utc, out t)) return t;
        return DateTime.MinValue;
    }
    static string H(string s) { return HttpUtility.HtmlEncode(s ?? ""); }
    static string Money(double v) { return (v < 0 ? "-$" : "$") + Math.Abs(v).ToString("#,0.00", Inv); }
    static string Signed(double v) { return (v > 0 ? "+$" : v < 0 ? "-$" : "$") + Math.Abs(v).ToString("#,0.00", Inv); }
    static string Tone(double v) { return v > 0 ? "up" : v < 0 ? "down" : "flat"; }
    static string Clip(string s, int n) { s = s ?? ""; return s.Length <= n ? s : s.Substring(0, n) + "…"; }
    // Rendered in UTC; the script turns it into the phone's own time.
    static string Time(string iso)
    {
        DateTime t = ParseTime(iso);
        if (t == DateTime.MinValue) return "<span class=\"when\">" + H(iso) + "</span>";
        string utc = t.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", Inv);
        return "<time class=\"when\" datetime=\"" + utc + "\">" + t.ToString("dd MMM HH:mm", Inv) + " UTC</time>";
    }

    // ---- views -----------------------------------------------------------
    protected Dictionary<string, object> Account { get { return D(G(R, "account")); } }
    protected Dictionary<string, object> Day { get { return D(G(R, "day")); } }
    protected IList Closed { get { return L(G(R, "closed")); } }
    protected IList Positions { get { return L(G(R, "positions")); } }

    protected string Mode { get { return S(G(R, "mode")) == "live" ? "Live" : "Dry-run"; } }

    // "06:00-23:00 Oman time, Mon-Fri" (a report from before the Oman window says New York).
    protected string TradeHours
    {
        get
        {
            string hours = S(G(R, "trade_hours"));
            if (hours.Length == 0 && R.ContainsKey("trade_hours_ny")) return S(G(R, "trade_hours_ny")) + " New York time";
            if (hours.Length == 0) return "at any hour";
            string zone = S(G(R, "trade_zone")), days = S(G(R, "trade_days"));
            return hours + (zone.Length > 0 ? " " + zone + " time" : "") + (days.Length > 0 ? ", " + days : "");
        }
    }

    protected double TodayChange
    {
        get
        {
            double start = N(G(Day, "start_equity")), eq = N(G(Account, "equity"));
            return start > 0 && Account.ContainsKey("equity") ? eq - start : 0;
        }
    }
    protected double TodayPct
    {
        get { double start = N(G(Day, "start_equity")); return start > 0 ? TodayChange / start * 100.0 : 0; }
    }

    double SourcePnl(string source, int days, out int count, out int wins)
    {
        DateTime since = ReportTime.AddDays(-days);
        double total = 0; count = 0; wins = 0;
        foreach (object o in Closed)
        {
            Dictionary<string, object> t = D(o);
            if (S(G(t, "source")) != source || ParseTime(S(G(t, "time"))) < since) continue;
            double pnl = N(G(t, "pnl"));
            total += pnl; count++;
            if (pnl > 0) wins++;
        }
        return total;
    }

    static string VerdictClass(string verdict)
    {
        switch (verdict)
        {
            case "ON TRACK": return "chip good";
            case "STOP AND REVIEW": return "chip bad";
            case "NOT PROVEN YET": return "chip warn";
            default: return "chip";
        }
    }

    protected string SourceCards()
    {
        StringBuilder sb = new StringBuilder();
        Dictionary<string, object> sc = D(G(R, "scorecard"));
        foreach (string src in Sources)
        {
            int n7, w7, n30, w30;
            double p7 = SourcePnl(src, 7, out n7, out w7);
            double p30 = SourcePnl(src, 30, out n30, out w30);
            string verdict = S(G(D(G(sc, src)), "verdict"));
            sb.Append("<section class=\"card src\"><header><h3>").Append(src == "Claude" ? "Claude trades" : "Telegram signals")
              .Append("</h3>");
            if (verdict.Length > 0) sb.Append("<span class=\"").Append(VerdictClass(verdict)).Append("\">").Append(H(verdict)).Append("</span>");
            sb.Append("</header><dl class=\"kv\">")
              .Append("<div><dt>7 days</dt><dd class=\"").Append(Tone(p7)).Append("\">").Append(Signed(p7)).Append("</dd></div>")
              .Append("<div><dt>30 days</dt><dd class=\"").Append(Tone(p30)).Append("\">").Append(Signed(p30)).Append("</dd></div>")
              .Append("<div><dt>Trades 30 d</dt><dd>").Append(n30).Append("</dd></div>")
              .Append("<div><dt>Win rate</dt><dd>").Append(n30 > 0 ? Math.Round(100.0 * w30 / n30).ToString(Inv) + "%" : "–").Append("</dd></div>")
              .Append("</dl></section>");
        }
        return sb.ToString();
    }

    // Cumulative closed P&L over 30 days, oldest to newest, as a small line chart.
    protected string Spark(out double total)
    {
        DateTime since = ReportTime.AddDays(-30);
        List<KeyValuePair<DateTime, double>> pts = new List<KeyValuePair<DateTime, double>>();
        foreach (object o in Closed)
        {
            Dictionary<string, object> t = D(o);
            DateTime when = ParseTime(S(G(t, "time")));
            if (when >= since) pts.Add(new KeyValuePair<DateTime, double>(when, N(G(t, "pnl"))));
        }
        pts.Sort((a, b) => a.Key.CompareTo(b.Key));
        total = 0;
        if (pts.Count < 2) return "";
        List<double> cum = new List<double> { 0 };
        foreach (KeyValuePair<DateTime, double> p in pts) { total += p.Value; cum.Add(total); }
        double min = Math.Min(0, cum.Min()), max = Math.Max(0, cum.Max());
        if (max - min < 1e-9) max = min + 1;
        const double W = 320, Hh = 72, pad = 4;
        Func<int, double> X = i => pad + (W - 2 * pad) * i / (cum.Count - 1);
        Func<double, double> Y = v => pad + (Hh - 2 * pad) * (max - v) / (max - min);
        StringBuilder line = new StringBuilder();
        for (int i = 0; i < cum.Count; i++)
            line.Append(i == 0 ? "M" : " L").Append(X(i).ToString("0.#", Inv)).Append(',').Append(Y(cum[i]).ToString("0.#", Inv));
        string zero = Y(0).ToString("0.#", Inv);
        string area = line + " L" + X(cum.Count - 1).ToString("0.#", Inv) + "," + zero + " L" + X(0).ToString("0.#", Inv) + "," + zero + " Z";
        string tone = Tone(total);
        return "<svg class=\"spark " + tone + "\" viewBox=\"0 0 " + W.ToString(Inv) + " " + Hh.ToString(Inv) + "\" preserveAspectRatio=\"none\" role=\"img\" aria-label=\"Closed profit and loss, last 30 days\">"
             + "<line class=\"zero\" x1=\"0\" x2=\"" + W.ToString(Inv) + "\" y1=\"" + zero + "\" y2=\"" + zero + "\"/>"
             + "<path class=\"area\" d=\"" + area + "\"/><path class=\"line\" d=\"" + line + "\"/>"
             + "<circle class=\"dot\" r=\"3.5\" cx=\"" + X(cum.Count - 1).ToString("0.#", Inv) + "\" cy=\"" + Y(total).ToString("0.#", Inv) + "\"/></svg>";
    }

    protected string PositionsHtml()
    {
        if (Positions.Count == 0) return "<p class=\"empty\">No open positions.</p>";
        StringBuilder sb = new StringBuilder("<ul class=\"rows\">");
        foreach (object o in Positions)
        {
            Dictionary<string, object> p = D(o);
            double profit = N(G(p, "profit"));
            string dir = S(G(p, "direction")).ToUpperInvariant();
            sb.Append("<li><div class=\"top\"><span class=\"chip src-").Append(H(S(G(p, "source")).ToLowerInvariant())).Append("\">")
              .Append(H(S(G(p, "source")))).Append("</span><b class=\"dir ").Append(dir == "BUY" ? "buy" : "sell").Append("\">").Append(H(dir))
              .Append("</b><span>").Append(N(G(p, "volume")).ToString("0.00", Inv)).Append(" lot</span><span class=\"amt ")
              .Append(Tone(profit)).Append("\">").Append(Signed(profit)).Append("</span></div><div class=\"sub\">Entry ")
              .Append(N(G(p, "price_open")).ToString("0.00", Inv)).Append(" · SL ").Append(N(G(p, "sl")) > 0 ? N(G(p, "sl")).ToString("0.00", Inv) : "none")
              .Append(" · ").Append(Time(S(G(p, "time")))).Append("</div></li>");
        }
        return sb.Append("</ul>").ToString();
    }

    protected double Floating
    {
        get { double s = 0; foreach (object o in Positions) s += N(G(D(o), "profit")); return s; }
    }

    protected string TradesHtml()
    {
        if (Closed.Count == 0) return "<p class=\"empty\">No closed trades in the last 120 days.</p>";
        StringBuilder sb = new StringBuilder("<ul class=\"rows\" id=\"trades\">");
        int shown = 0;
        foreach (object o in Closed)
        {
            if (++shown > 150) break;
            Dictionary<string, object> t = D(o);
            double pnl = N(G(t, "pnl"));
            string src = S(G(t, "source")), dir = S(G(t, "direction")).ToUpperInvariant();
            sb.Append("<li data-source=\"").Append(H(src)).Append("\"><div class=\"top\"><span class=\"chip src-").Append(H(src.ToLowerInvariant()))
              .Append("\">").Append(H(src)).Append("</span><b class=\"dir ").Append(dir == "BUY" ? "buy" : "sell").Append("\">").Append(H(dir))
              .Append("</b><span>").Append(N(G(t, "volume")).ToString("0.00", Inv)).Append(" lot</span><span class=\"amt ").Append(Tone(pnl))
              .Append("\">").Append(Signed(pnl)).Append("</span></div><div class=\"sub\">").Append(Time(S(G(t, "time"))))
              .Append(" · #").Append(H(S(G(t, "ticket")))).Append("</div></li>");
        }
        return sb.Append("</ul>").ToString();
    }

    protected string DecisionsHtml()
    {
        IList list = L(G(R, "decisions"));
        if (list.Count == 0) return "<p class=\"empty\">No Claude evaluations yet. They happen inside the trading hours when 2 of 3 checks agree.</p>";
        StringBuilder sb = new StringBuilder("<ul class=\"rows\">");
        foreach (object o in list)
        {
            Dictionary<string, object> d = D(o);
            bool executed = B(G(d, "executed"));
            string dir = S(G(d, "direction")).ToUpperInvariant(), conv = S(G(d, "conviction"));
            sb.Append("<li><div class=\"top\"><b class=\"dir ").Append(dir == "BUY" ? "buy" : dir == "SELL" ? "sell" : "none").Append("\">")
              .Append(H(dir == "" ? "NONE" : dir)).Append("</b><span class=\"chip").Append(conv == "full" ? " gold" : "").Append("\">")
              .Append(H(conv)).Append("</span><span>").Append(H(S(G(d, "confluence_count")))).Append("/3</span>")
              .Append(executed ? "<span class=\"chip good\">Traded</span>" : "<span class=\"chip\">No trade</span>")
              .Append("</div><div class=\"sub\">").Append(Time(S(G(d, "time"))));
            string reason = S(G(d, "reject_reason"));
            if (!executed && reason.Length > 0) sb.Append(" · ").Append(H(Clip(reason, 160)));
            sb.Append("</div>");
            string why = S(G(d, "reasoning"));
            if (why.Length > 0) sb.Append("<details><summary>Reasoning</summary><p class=\"pre\">").Append(H(Clip(why, 2500))).Append("</p></details>");
            sb.Append("</li>");
        }
        return sb.Append("</ul>").ToString();
    }

    protected string SignalsHtml()
    {
        IList list = L(G(R, "signals"));
        if (list.Count == 0) return "<p class=\"empty\">No Telegram signals logged yet (MT5 &rarr; MQL5\\Files\\TelegramSMC_Signals.csv).</p>";
        StringBuilder sb = new StringBuilder("<ul class=\"rows\">");
        foreach (object o in list)
        {
            Dictionary<string, object> s = D(o);
            bool accepted = B(G(s, "accepted"));
            string dir = S(G(s, "direction")).ToUpperInvariant();
            string reason = S(G(s, "sanity_reason"));
            if (reason.Length == 0) reason = S(G(s, "smc_reason"));
            sb.Append("<li><div class=\"top\"><span class=\"chip\">").Append(H(S(G(s, "action")))).Append("</span>");
            if (dir.Length > 0) sb.Append("<b class=\"dir ").Append(dir == "BUY" ? "buy" : "sell").Append("\">").Append(H(dir)).Append("</b>");
            sb.Append(accepted ? "<span class=\"chip good\">Copied</span>" : "<span class=\"chip\">Not copied</span>");
            if (accepted && S(G(s, "lots")).Length > 0)
            {
                string otype = S(G(s, "order_type")).ToLowerInvariant();
                sb.Append("<span>").Append(H(S(G(s, "lots")))).Append(" lot").Append(otype.Length > 0 ? " · " + H(otype) + " order" : "").Append("</span>");
            }
            sb.Append("</div><div class=\"sub\">").Append(Time(S(G(s, "time_utc"))));
            if (!accepted && reason.Length > 0) sb.Append(" · ").Append(H(Clip(reason, 160)));
            sb.Append("</div>");
            string raw = S(G(s, "raw_text"));
            if (raw.Length > 0) sb.Append("<details><summary>Message</summary><p class=\"pre\">").Append(H(Clip(raw, 1200))).Append("</p></details>");
            sb.Append("</li>");
        }
        return sb.Append("</ul>").ToString();
    }

    protected string ScoreHtml()
    {
        Dictionary<string, object> sc = D(G(R, "scorecard"));
        if (sc.Count == 0) return "<p class=\"empty\">No scorecard yet - it needs closed trades.</p>";
        StringBuilder sb = new StringBuilder();
        foreach (string name in new[] { "Claude", "Telegram", "Combined" })
        {
            Dictionary<string, object> s = D(G(sc, name));
            if (s.Count == 0) continue;
            string verdict = S(G(s, "verdict"));
            int trades = (int)N(G(s, "trades"));
            sb.Append("<section class=\"card\"><header><h3>").Append(name).Append("</h3><span class=\"").Append(VerdictClass(verdict)).Append("\">")
              .Append(H(verdict)).Append("</span></header>");
            if (trades > 0)
            {
                double pf = N(G(s, "profit_factor"));
                sb.Append("<dl class=\"kv\">")
                  .Append("<div><dt>Trades</dt><dd>").Append(trades).Append("</dd></div>")
                  .Append("<div><dt>Win rate</dt><dd>").Append(N(G(s, "win_pct")).ToString("0.#", Inv)).Append("%</dd></div>")
                  .Append("<div><dt>Net</dt><dd class=\"").Append(Tone(N(G(s, "net_pnl")))).Append("\">").Append(Signed(N(G(s, "net_pnl")))).Append("</dd></div>")
                  .Append("<div><dt>Average</dt><dd>").Append(N(G(s, "avg_r")).ToString("+0.00;-0.00;0.00", Inv)).Append(" R</dd></div>")
                  .Append("<div><dt>Profit factor</dt><dd>").Append(G(s, "profit_factor") == null ? "–" : pf.ToString("0.00", Inv)).Append("</dd></div>")
                  .Append("<div><dt>Worst drawdown</dt><dd>").Append(N(G(s, "max_dd_r")).ToString("0.0", Inv)).Append(" R</dd></div>")
                  .Append("<div><dt>Chance of no edge</dt><dd>").Append(Math.Round(N(G(s, "p_no_edge")) * 100).ToString(Inv)).Append("%</dd></div>")
                  .Append("</dl>");
            }
            sb.Append("<p class=\"note\">").Append(H(S(G(s, "advice")))).Append("</p></section>");
        }
        return sb.ToString();
    }

    protected string RulesLine()
    {
        Dictionary<string, object> r = D(G(R, "rules"));
        if (r.Count == 0) return "";
        return N(G(r, "risk_percent")).ToString("0.##", Inv) + "% risk · $" + N(G(r, "sl_dollars")).ToString("0.##", Inv) + " stop · $"
             + N(G(r, "tp1_dollars")).ToString("0.##", Inv) + " lock · $" + N(G(r, "trail_dollars")).ToString("0.##", Inv) + " trail · "
             + N(G(r, "max_per_direction")).ToString("0", Inv) + " per direction · " + N(G(r, "max_daily_loss_pct")).ToString("0.##", Inv) + "% daily cap";
    }
</script>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#101820">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="GoldTrader">
<title>GoldTrader</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23101820'/%3E%3Cpath d='M6 22l6-7 5 4 9-11' fill='none' stroke='%23e2b04a' stroke-width='3'/%3E%3C/svg%3E">
<style>
:root {
  --bg:#edf0f3; --surface:#ffffff; --surface-2:#f4f6f8; --ink:#121a22; --ink-2:#56636f; --rule:#d6dde4;
  --gold:#94670a; --gold-soft:#f6ecd6; --up:#177245; --up-soft:#e2f3ea; --down:#b3321f; --down-soft:#fbe7e3;
  --warn:#955800; --warn-soft:#fdf0d8; --buy:#1f5fa8; --sell:#8a3aa0; --focus:#1d5fa8;
}
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark;
    --bg:#0b1117; --surface:#131c25; --surface-2:#182330; --ink:#e6edf3; --ink-2:#98a8b6; --rule:#243241;
    --gold:#e2b04a; --gold-soft:#2b2412; --up:#4cc38a; --up-soft:#11291d; --down:#f07560; --down-soft:#321a16;
    --warn:#f0a832; --warn-soft:#2e2413; --buy:#6aa8ef; --sell:#c79be0; --focus:#6aa8ef; }
}
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:0 16px calc(32px + env(safe-area-inset-bottom,0px)); }
.wrap { max-width:720px; margin:0 auto; display:grid; gap:14px; }
.bar { position:sticky; top:0; z-index:5; margin:0 -16px; padding:calc(10px + env(safe-area-inset-top,0px)) 16px 0; background:var(--bg); border-bottom:1px solid var(--rule); }
.bar .in { max-width:720px; margin:0 auto; display:grid; gap:8px; }
.brand { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.brand h1 { margin:0 auto 0 0; font-size:19px; letter-spacing:.01em; }
.brand h1 span { color:var(--gold); }
nav.tabs { display:flex; }
nav.tabs a { flex:1 1 auto; text-align:center; padding:9px 4px; font-size:14.5px; color:var(--ink-2); text-decoration:none; font-weight:600; border-bottom:3px solid transparent; }
nav.tabs a[aria-current="page"] { color:var(--ink); border-bottom-color:var(--gold); }
a:focus-visible, button:focus-visible, summary:focus-visible { outline:2px solid var(--focus); outline-offset:2px; border-radius:4px; }
.chip { display:inline-block; font-size:12px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; padding:3px 8px; border-radius:999px;
  background:var(--surface-2); color:var(--ink-2); border:1px solid var(--rule); white-space:nowrap; }
.chip.good { background:var(--up-soft); color:var(--up); border-color:transparent; }
.chip.bad { background:var(--down-soft); color:var(--down); border-color:transparent; }
.chip.warn { background:var(--warn-soft); color:var(--warn); border-color:transparent; }
.chip.gold { background:var(--gold-soft); color:var(--gold); border-color:transparent; }
.chip.src-claude { color:var(--gold); }
.chip.src-telegram { color:var(--buy); }
.panel { display:grid; gap:14px; padding-top:14px; }
.panel[hidden] { display:none; }
.card { background:var(--surface); border:1px solid var(--rule); border-radius:10px; padding:14px 16px; display:grid; gap:10px; min-width:0; }
.card > header { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; }
h2, h3 { margin:0; font-size:15px; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-2); }
.hero .eq { font-size:clamp(30px, 9vw, 42px); font-weight:800; letter-spacing:-.01em; font-variant-numeric:tabular-nums; line-height:1.05; }
.hero .today { font-size:17px; font-weight:700; font-variant-numeric:tabular-nums; }
.meta { color:var(--ink-2); font-size:14px; font-variant-numeric:tabular-nums; }
.state { display:flex; gap:6px; flex-wrap:wrap; }
.up { color:var(--up); } .down { color:var(--down); } .flat { color:var(--ink-2); }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:14px; }
dl.kv { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:10px 14px; margin:0; }
dl.kv dt { font-size:12px; color:var(--ink-2); text-transform:uppercase; letter-spacing:.04em; }
dl.kv dd { margin:0; font-weight:700; font-size:16px; font-variant-numeric:tabular-nums; }
svg.spark { width:100%; height:72px; display:block; }
svg.spark .zero { stroke:var(--rule); stroke-width:1; stroke-dasharray:3 3; }
svg.spark .area { fill:var(--gold-soft); stroke:none; }
svg.spark .line { fill:none; stroke:var(--gold); stroke-width:2; vector-effect:non-scaling-stroke; }
svg.spark .dot { fill:var(--gold); }
svg.spark.down .line { stroke:var(--down); } svg.spark.down .dot { fill:var(--down); } svg.spark.down .area { fill:var(--down-soft); }
ul.rows { list-style:none; margin:0; padding:0; display:grid; }
ul.rows > li { padding:10px 0; border-top:1px solid var(--rule); display:grid; gap:3px; min-width:0; }
ul.rows > li:first-child { border-top:0; padding-top:0; }
.top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-variant-numeric:tabular-nums; }
.top .amt { margin-left:auto; font-weight:800; }
.dir { font-size:13px; letter-spacing:.04em; }
.dir.buy { color:var(--buy); } .dir.sell { color:var(--sell); } .dir.none { color:var(--ink-2); }
.sub { color:var(--ink-2); font-size:13.5px; overflow-wrap:anywhere; }
details summary { cursor:pointer; color:var(--ink-2); font-size:13.5px; }
.pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:6px 0 0; font-size:13.5px; background:var(--surface-2); border-radius:6px; padding:8px 10px; }
.empty, .note { margin:0; color:var(--ink-2); }
.filters { display:flex; gap:6px; flex-wrap:wrap; }
.filters button { font:inherit; font-size:13px; font-weight:600; padding:6px 12px; border-radius:999px; border:1px solid var(--rule); background:var(--surface); color:var(--ink-2); cursor:pointer; }
.filters button[aria-pressed="true"] { background:var(--ink); color:var(--surface); border-color:var(--ink); }
.banner { border-radius:10px; padding:12px 14px; font-weight:600; }
.banner.bad { background:var(--down-soft); color:var(--down); }
.banner.warn { background:var(--warn-soft); color:var(--warn); }
.banner p { margin:4px 0 0; font-weight:400; color:var(--ink); }
footer { color:var(--ink-2); font-size:13px; display:flex; flex-wrap:wrap; gap:6px 14px; justify-content:space-between; padding-top:6px; overflow-wrap:anywhere; }
footer a { color:var(--ink-2); }
code { font-size:12.5px; overflow-wrap:anywhere; }
@media (prefers-reduced-motion: no-preference) { .panel { animation:fade .15s ease-out; } @keyframes fade { from { opacity:.6 } to { opacity:1 } } }
</style>
</head>
<body>
<div class="bar"><div class="in">
  <div class="brand">
    <h1>Gold<span>Trader</span></h1>
    <% if (HasData) { %>
      <span class="chip <%= Mode == "Live" ? "gold" : "" %>"><%= Mode %></span>
      <span class="chip <%= Stale ? "bad" : "good" %>" id="age" data-updated="<%= H(S(G(R, "updated_utc"))) %>"><%= Stale ? "Not updating" : "Updated" %></span>
    <% } %>
  </div>
  <nav class="tabs" aria-label="Sections">
    <a href="#overview">Overview</a><a href="#trades">Trades</a><a href="#claude">Claude</a><a href="#signals">Signals</a><a href="#score">Score</a>
  </nav>
</div></div>

<div class="wrap">
<% if (IsSample) { %>
  <div class="banner warn">Sample data - not your account.<p>This shows what the page looks like. <a href="Default.aspx">Back to your data</a>.</p></div>
<% } %>
<% if (!HasData) { %>
  <section class="panel" id="overview">
    <div class="card">
      <h3>No report yet</h3>
      <% if (LoadError == "denied") { %>
        <p class="note">The web server may not read the logs folder. Run <code>dashboard_setup.bat</code> again as administrator; it grants read access to that folder only.</p>
      <% } else if (LoadError == "missing") { %>
        <p class="note">The trading program writes this report once a minute while <code>start.bat</code> runs. Start it, wait a minute, then reload.</p>
      <% } else { %>
        <p class="note">The report could not be read: <%= H(LoadError) %></p>
      <% } %>
      <p class="note">Looking for: <code><%= H(DataPath) %></code></p>
      <p class="note"><a href="Default.aspx?sample=1">See the page with sample data</a></p>
    </div>
  </section>
<% } else { %>
  <% if (Stale) { %>
    <div class="banner bad">The report is <%= AgeMinutes >= 120 ? Math.Round(AgeMinutes / 60).ToString(Inv) + " hours" : Math.Round(AgeMinutes).ToString(Inv) + " minutes" %> old.
      <p>The trading program is probably not running. Check the <code>start.bat</code> window and MT5 on the PC.</p></div>
  <% } %>
  <% foreach (object p in L(G(R, "problems"))) { %>
    <div class="banner warn">Part of the report is missing: <%= H(S(p)) %></div>
  <% } %>

  <section class="panel" id="overview" aria-label="Overview">
    <div class="card hero">
      <h2>Equity</h2>
      <div class="eq"><%= Account.ContainsKey("equity") ? Money(N(G(Account, "equity"))) : "–" %></div>
      <div class="today <%= Tone(TodayChange) %>">Today <%= Signed(TodayChange) %> (<%= TodayPct.ToString("+0.00;-0.00;0.00", Inv) %>%)</div>
      <div class="meta">Balance <%= Money(N(G(Account, "balance"))) %> · Free margin <%= Money(N(G(Account, "margin_free"))) %><%= Account.ContainsKey("leverage") ? " · 1:" + N(G(Account, "leverage")).ToString("0", Inv) : "" %></div>
      <div class="state">
        <span class="chip <%= B(G(R, "claude_paused")) ? "warn" : "good" %>">Claude <%= B(G(R, "claude_paused")) ? "paused" : "on" %></span>
        <span class="chip <%= B(G(Day, "daily_loss_hit")) ? "bad" : "" %>"><%= B(G(Day, "daily_loss_hit")) ? "Daily loss cap hit" : "Daily cap OK" %></span>
        <span class="chip">Claude trades today <%= N(G(Day, "claude_trades")).ToString("0", Inv) %></span>
      </div>
    </div>

    <% double spTotal; string spark = Spark(out spTotal); %>
    <div class="card">
      <header><h2>Closed trades, 30 days</h2><b class="<%= Tone(spTotal) %>"><%= Signed(spTotal) %></b></header>
      <%= spark.Length > 0 ? spark : "<p class=\"empty\">The chart appears after two closed trades.</p>" %>
    </div>

    <div class="grid2"><%= SourceCards() %></div>

    <div class="card">
      <header><h2>Open positions (<%= Positions.Count %>)</h2><% if (Positions.Count > 0) { %><b class="<%= Tone(Floating) %>"><%= Signed(Floating) %></b><% } %></header>
      <%= PositionsHtml() %>
    </div>
  </section>

  <section class="panel" id="trades" aria-label="Trades" hidden>
    <div class="card">
      <header><h2>Closed trades</h2>
        <div class="filters" role="group" aria-label="Filter trades">
          <button type="button" data-filter="" aria-pressed="true">All</button>
          <button type="button" data-filter="Claude" aria-pressed="false">Claude</button>
          <button type="button" data-filter="Telegram" aria-pressed="false">Telegram</button>
        </div>
      </header>
      <%= TradesHtml() %>
    </div>
  </section>

  <section class="panel" id="claude" aria-label="Claude" hidden>
    <div class="card">
      <header><h2>Claude's last word</h2><span class="chip <%= B(G(R, "claude_paused")) ? "warn" : "good" %>"><%= B(G(R, "claude_paused")) ? "Paused" : "On" %></span></header>
      <% string last = S(G(R, "last_verdict")); %>
      <%= last.Length > 0 ? "<p class=\"pre\">" + H(Clip(last, 3000)) + "</p>" : "<p class=\"empty\">Nothing yet.</p>" %>
      <p class="note">New entries only <%= H(TradeHours) %> - Claude and Telegram alike.</p>
    </div>
    <div class="card"><h2>Recent evaluations</h2><%= DecisionsHtml() %></div>
  </section>

  <section class="panel" id="signals" aria-label="Telegram signals" hidden>
    <div class="card"><h2>Telegram signals</h2><%= SignalsHtml() %></div>
  </section>

  <section class="panel" id="score" aria-label="Scorecard" hidden>
    <%= ScoreHtml() %>
    <p class="note">Rules, unchanged: <%= H(RulesLine()) %>. Go live only after 30+ trades ON TRACK.</p>
  </section>
<% } %>

<footer>
  <span><%= HasData ? H(S(G(R, "symbol"))) + " · " : "" %>Refreshes every minute</span>
  <a href="Default.aspx?logout=1">Sign out</a>
</footer>
</div>

<script>
(function () {
  var tabs = document.querySelectorAll("nav.tabs a"), panels = document.querySelectorAll(".panel");
  function show() {
    var id = (location.hash || "#overview").slice(1), found = false;
    for (var i = 0; i < panels.length; i++) { var on = panels[i].id === id; panels[i].hidden = !on; if (on) found = true; }
    if (!found && panels.length) { panels[0].hidden = false; id = panels[0].id; }
    for (var j = 0; j < tabs.length; j++) {
      if (tabs[j].getAttribute("href") === "#" + id) tabs[j].setAttribute("aria-current", "page"); else tabs[j].removeAttribute("aria-current");
    }
  }
  window.addEventListener("hashchange", function () { show(); window.scrollTo(0, 0); });
  show();

  // Times in the phone's own time zone.
  var times = document.querySelectorAll("time[datetime]");
  for (var k = 0; k < times.length; k++) {
    var d = new Date(times[k].getAttribute("datetime"));
    if (!isNaN(d)) times[k].textContent = d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  // Trades filter.
  var buttons = document.querySelectorAll(".filters button"), rows = document.querySelectorAll("#trades li");
  for (var b = 0; b < buttons.length; b++) buttons[b].addEventListener("click", function () {
    var f = this.getAttribute("data-filter");
    for (var x = 0; x < buttons.length; x++) buttons[x].setAttribute("aria-pressed", buttons[x] === this ? "true" : "false");
    for (var r = 0; r < rows.length; r++) rows[r].hidden = f !== "" && rows[r].getAttribute("data-source") !== f;
  });

  // "Updated 40 s ago".
  var age = document.getElementById("age");
  function tick() {
    if (!age || age.className.indexOf("bad") >= 0) return;
    var t = new Date(age.getAttribute("data-updated"));
    if (isNaN(t)) return;
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    age.textContent = s < 90 ? "Updated " + s + " s ago" : "Updated " + Math.round(s / 60) + " min ago";
  }
  tick(); setInterval(tick, 5000);

  // Fresh data every minute while the page is on screen (not while reading an open item).
  setInterval(function () {
    if (document.visibilityState === "visible" && !document.querySelector("details[open]")) location.reload();
  }, 60000);
})();
</script>
</body>
</html>
