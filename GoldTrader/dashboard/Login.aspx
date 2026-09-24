<%@ Page Language="C#" EnableViewState="false" %>
<%@ Import Namespace="System.IO" %>
<%@ Import Namespace="System.Security.Cryptography" %>
<%@ Import Namespace="System.Web.Caching" %>
<script runat="server">
    // One password for the owner. App_Data\password.txt holds
    // "pbkdf2-sha256$<iterations>$<salt base64>$<hash base64>", written by
    // python goldtrader.py dashboard-password. The password itself is never stored.
    const int MaxFailures = 5;
    static readonly TimeSpan LockTime = TimeSpan.FromMinutes(15);
    protected string Message = "", Info = "";
    protected bool NoPassword = false;

    static byte[] Pbkdf2Sha256(byte[] password, byte[] salt, int iterations, int length)
    {
        using (HMACSHA256 hmac = new HMACSHA256(password))
        {
            byte[] result = new byte[length];
            int offset = 0;
            for (int block = 1; offset < length; block++)
            {
                byte[] input = new byte[salt.Length + 4];
                System.Buffer.BlockCopy(salt, 0, input, 0, salt.Length);
                input[salt.Length] = (byte)(block >> 24);
                input[salt.Length + 1] = (byte)(block >> 16);
                input[salt.Length + 2] = (byte)(block >> 8);
                input[salt.Length + 3] = (byte)block;
                byte[] u = hmac.ComputeHash(input);
                byte[] t = (byte[])u.Clone();
                for (int i = 1; i < iterations; i++)
                {
                    u = hmac.ComputeHash(u);
                    for (int k = 0; k < t.Length; k++) t[k] ^= u[k];
                }
                int n = Math.Min(t.Length, length - offset);
                System.Buffer.BlockCopy(t, 0, result, offset, n);
                offset += n;
            }
            return result;
        }
    }

    static bool SameBytes(byte[] a, byte[] b)
    {
        int diff = a.Length ^ b.Length;
        for (int i = 0; i < a.Length && i < b.Length; i++) diff |= a[i] ^ b[i];
        return diff == 0;
    }

    string StoredHash()
    {
        string path = Server.MapPath("~/App_Data/password.txt");
        if (!File.Exists(path)) return "";
        return File.ReadAllText(path).Trim();
    }

    bool Verify(string password, string stored)
    {
        string[] parts = stored.Split('$');
        if (parts.Length != 4 || parts[0] != "pbkdf2-sha256") return false;
        int iterations;
        if (!int.TryParse(parts[1], out iterations) || iterations < 1000) return false;
        byte[] salt = Convert.FromBase64String(parts[2]);
        byte[] expected = Convert.FromBase64String(parts[3]);
        byte[] actual = Pbkdf2Sha256(System.Text.Encoding.UTF8.GetBytes(password), salt, iterations, expected.Length);
        return SameBytes(actual, expected);
    }

    string FailKey() { return "gtdash-fail-" + Request.UserHostAddress; }

    int Failures()
    {
        object o = HttpRuntime.Cache[FailKey()];
        return o == null ? 0 : (int)o;
    }

    void Page_Load(object sender, EventArgs e)
    {
        Response.Cache.SetCacheability(HttpCacheability.NoCache);
        Response.Cache.SetNoStore();
        string stored = StoredHash();
        NoPassword = stored.Length == 0;
        if (Request.QueryString["out"] == "1")
            Info = "Signed out.";
        if (Request.HttpMethod != "POST" || NoPassword) return;

        if (Failures() >= MaxFailures)
        {
            Message = "Too many wrong passwords. Try again in 15 minutes.";
            return;
        }
        string password = Request.Form["password"] ?? "";
        bool ok = false;
        try { ok = password.Length > 0 && Verify(password, stored); }
        catch (FormatException) { ok = false; }
        if (ok)
        {
            HttpRuntime.Cache.Remove(FailKey());
            FormsAuthentication.SetAuthCookie("owner", true);
            Response.Redirect("Default.aspx", false);
            Context.ApplicationInstance.CompleteRequest();
            return;
        }
        int failures = Failures() + 1;
        HttpRuntime.Cache.Insert(FailKey(), failures, null, DateTime.UtcNow.Add(LockTime), Cache.NoSlidingExpiration);
        System.Threading.Thread.Sleep(1000);
        Message = failures >= MaxFailures
            ? "Too many wrong passwords. Try again in 15 minutes."
            : "Wrong password.";
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
<meta name="apple-mobile-web-app-title" content="GoldTrader">
<title>GoldTrader sign in</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23101820'/%3E%3Cpath d='M6 22l6-7 5 4 9-11' fill='none' stroke='%23e2b04a' stroke-width='3'/%3E%3C/svg%3E">
<style>
:root { --bg:#eef1f4; --surface:#fff; --ink:#131c24; --ink-2:#56636f; --rule:#d3dae1; --gold:#94670a; --red:#b3321f; --focus:#1d5fa8; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --bg:#0b1117; --surface:#141d26; --ink:#e6edf3; --ink-2:#98a8b6; --rule:#263442; --gold:#e2b04a; --red:#f07560; --focus:#6aa8ef; } }
* { box-sizing: border-box; }
body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--ink);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; padding:24px 16px; }
form { width:100%; max-width:360px; background:var(--surface); border:1px solid var(--rule); border-radius:10px; padding:24px; display:grid; gap:14px; }
h1 { margin:0; font-size:22px; letter-spacing:.01em; }
h1 span { color:var(--gold); }
p { margin:0; color:var(--ink-2); font-size:15px; }
label { font-size:14px; font-weight:600; }
input { width:100%; font:inherit; padding:12px; border:1px solid var(--rule); border-radius:8px; background:var(--bg); color:var(--ink); }
button { font:inherit; font-weight:700; padding:12px; border:0; border-radius:8px; background:var(--gold); color:#fff; cursor:pointer; }
@media (prefers-color-scheme: dark) { button { color:#1a1203; } }
input:focus-visible, button:focus-visible { outline:2px solid var(--focus); outline-offset:2px; }
.msg { color:var(--red); font-weight:600; }
code { font-size:13px; }
</style>
</head>
<body>
<form method="post" action="Login.aspx" autocomplete="on">
  <h1>Gold<span>Trader</span></h1>
  <% if (NoPassword) { %>
    <p class="msg">No password is set yet.</p>
    <p>On the trading PC, open the GoldTrader folder and run <code>python goldtrader.py dashboard-password</code>, then reload this page.</p>
  <% } else { %>
    <p>Reports for your gold trading account.</p>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Sign in</button>
  <% } %>
  <% if (Info.Length > 0 && Message.Length == 0) { %><p role="status"><%= HttpUtility.HtmlEncode(Info) %></p><% } %>
  <% if (Message.Length > 0) { %><p class="msg" role="alert"><%= HttpUtility.HtmlEncode(Message) %></p><% } %>
</form>
</body>
</html>
