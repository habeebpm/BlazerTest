//+------------------------------------------------------------------+
//|                                   TelegramSMC_Common.mqh          |
//|                                                                    |
//| Shared CSV-logging helpers for TelegramSMC_Copier.mq5 and          |
//| TelegramSMC_TradeLogger.mq5 - two independent EAs. This header is  |
//| a code-sharing convenience only, not a runtime link between them:  |
//| either can run without the other. The Copier writes one row per    |
//| Telegram message it evaluates to TSMC_SIGNALS_FILE; the Logger      |
//| writes one row per position open/close to TSMC_RESULTS_FILE. The   |
//| two are joined, if you want to, by matching order_ticket between   |
//| them - nothing here merges them automatically.                     |
//+------------------------------------------------------------------+
#property strict

#define TSMC_SIGNALS_FILE   "TelegramSMC_Signals.csv"
#define TSMC_RESULTS_FILE   "TelegramSMC_Results.csv"

// This EA (TelegramSMC_Copier.mq5) only ever copies Telegram signals, so its
// own order comment and Signals-log rows always carry this literal tag - see
// TelegramSMC_TradeLogger.mq5's InpSourceLabel for the reusable-EA case,
// where the source isn't fixed at compile time.
#define TSMC_SIGNAL_SOURCE  "Telegram_Sig"

// "source" is deliberately the LAST column in both headers, not inserted
// after time_utc: an already-running deployment's log file keeps whatever
// header it was created with (TsmcOpenCsvForAppend below never rewrites an
// existing header), so a column inserted in the middle would silently shift
// every field after it - for every reader that keys off column NAME, not
// position (the ASPX dashboard, csv.DictReader, ...) - the moment this
// build starts appending longer rows under that stale header. Appending at
// the end instead means an old header just doesn't expose "source" for
// that file (a missing feature) rather than corrupting every other column
// (silent data loss) - see TsmcOpenCsvForAppend's mismatch warning below.
#define TSMC_SIGNALS_HEADER "time_utc,chat_id,action,direction,symbol_ok,entry_low,entry_high,sl,tps,smc_used,smc_pass,smc_reason,sanity_pass,sanity_reason,accepted,order_type,order_price,lots,dry_run,order_ticket,retcode,raw_text,source"
#define TSMC_RESULTS_HEADER "time_utc,event,position_id,order_ticket,symbol,magic,direction,volume,price,sl,tp,profit,swap,commission,net_profit,close_reason,duration_min,price_move,comment,source"

//+------------------------------------------------------------------+
//| Quote a field per RFC4180 (wrap in double quotes, double any       |
//| embedded quotes). Always quoting is simpler than deciding when     |
//| it's needed and just as valid, and safely handles raw Telegram     |
//| text that contains commas, quotes, or anything else.               |
//+------------------------------------------------------------------+
string TsmcCsvField(const string value)
{
   string t = value;
   StringReplace(t, "\"", "\"\"");
   return("\"" + t + "\"");
}

//+------------------------------------------------------------------+
//| Opens filename for appending: creates it with the given header    |
//| row if it doesn't exist yet (or is empty), otherwise seeks to the |
//| end so existing rows are preserved. Returns INVALID_HANDLE on      |
//| failure (logged). useCommon writes to the shared Common\Files      |
//| folder (visible to every terminal on the machine) instead of this  |
//| terminal's own MQL5\Files.                                         |
//+------------------------------------------------------------------+
int TsmcOpenCsvForAppend(const string filename, const string header, bool useCommon)
{
   // This function runs on EVERY logged row (every Telegram message
   // evaluated, every position open/close), so the stale-header warning
   // below must only ever print once per filename per EA run - a "|"-
   // delimited list of filenames already warned about, remembered across
   // calls via `static`, is what makes that "once" instead of "every row"
   // (mirrors telegram_copier.py's _warned_stale_signal_log_header flag).
   static string warnedFiles = "|";

   int flags = FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ;
   if(useCommon) flags |= FILE_COMMON;

   int handle = FileOpen(filename, flags);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("TelegramSMC: could not open %s for logging, error=%d", filename, GetLastError());
      return(INVALID_HANDLE);
   }

   if(FileSize(handle) == 0)
   {
      FileWriteString(handle, header + "\r\n");
      return(handle);
   }

   // Read-only check, never modifies the file: warn (once per filename) if
   // the file's actual first line doesn't match what this build would write
   // today, so a schema change (a column added to header/*) is visible in
   // the log instead of silently degrading - see the header comments above.
   string existingHeader = FileReadString(handle);
   if(existingHeader != header && StringFind(warnedFiles, "|" + filename + "|") < 0)
   {
      warnedFiles += filename + "|";
      PrintFormat("TelegramSMC: %s already exists with a different header than this build writes - "
                  "any new column (e.g. \"source\") won't be readable by name for this file until you "
                  "rename/delete it and let a fresh one be created. Existing data and columns are not "
                  "affected - new rows are simply longer than the old header describes.", filename);
   }

   FileSeek(handle, 0, SEEK_END);
   return(handle);
}
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| TELEGRAM UPDATES: a small real JSON walker (not substring search). |
//| Only the message's OWN top-level "text" is ever read - never a     |
//| nested one - so a pinned-message notice, or a reply quoting an old |
//| signal, can't replay that signal as a new trade. Media posts       |
//| (video, audio, voice, sticker, document, poll, ...) and service    |
//| messages are marked skipped; a photo's caption is read only when   |
//| acceptPhotoCaptions is true. Shared by UnifiedTrader_EA.mq5 and    |
//| TelegramSMC_Copier.mq5.                                            |
//+------------------------------------------------------------------+
struct TsmcTgUpdate
{
   long   update_id;
   long   chat_id;
   long   date;
   bool   isEdited;
   string text;      // unescaped; "" when skipped
   string skip;      // "" = readable text message, else why it is omitted
};

int TsmcJsonSkipWs(const string &js, int p)
{
   int n = StringLen(js);
   while(p < n)
   {
      ushort c = StringGetCharacter(js, p);
      if(c != ' ' && c != '\n' && c != '\r' && c != '\t')
         break;
      p++;
   }
   return(p);
}

// js[p] must be '"'. Returns the index just past the closing quote (or -1)
// and the still-escaped content in `raw`.
int TsmcJsonReadString(const string &js, int p, string &raw)
{
   int n = StringLen(js);
   raw = "";
   if(p >= n || StringGetCharacter(js, p) != '"')
      return(-1);
   int q = p + 1;
   while(q < n)
   {
      ushort c = StringGetCharacter(js, q);
      if(c == '\\') { q += 2; continue; }
      if(c == '"')
      {
         raw = StringSubstr(js, p + 1, q - p - 1);
         return(q + 1);
      }
      q++;
   }
   return(-1);
}

// Returns the index just past the JSON value starting at p (or -1).
int TsmcJsonSkipValue(const string &js, int p)
{
   int n = StringLen(js);
   p = TsmcJsonSkipWs(js, p);
   if(p >= n)
      return(-1);
   string tmp;
   ushort c = StringGetCharacter(js, p);
   if(c == '"')
      return(TsmcJsonReadString(js, p, tmp));
   if(c == '{' || c == '[')
   {
      int depth = 0;
      while(p < n)
      {
         ushort d = StringGetCharacter(js, p);
         if(d == '"')
         {
            p = TsmcJsonReadString(js, p, tmp);
            if(p < 0)
               return(-1);
            continue;
         }
         if(d == '{' || d == '[')
            depth++;
         else if(d == '}' || d == ']')
         {
            depth--;
            if(depth == 0)
               return(p + 1);
         }
         p++;
      }
      return(-1);
   }
   while(p < n)   // number / true / false / null
   {
      ushort d = StringGetCharacter(js, p);
      if(d == ',' || d == '}' || d == ']' || d == ' ' || d == '\n' || d == '\r' || d == '\t')
         break;
      p++;
   }
   return(p);
}

// Scans the object starting at objStart. Returns where the value of the
// TOP-LEVEL member `key` starts (-1 if absent); `keys` receives every
// top-level key as "|k1|k2|".
int TsmcJsonFindMember(const string &js, int objStart, const string key, string &keys)
{
   keys = "|";
   int n = StringLen(js);
   int p = TsmcJsonSkipWs(js, objStart);
   if(p >= n || StringGetCharacter(js, p) != '{')
      return(-1);
   p++;
   int found = -1;
   while(p < n)
   {
      p = TsmcJsonSkipWs(js, p);
      if(p >= n)
         break;
      ushort c = StringGetCharacter(js, p);
      if(c == '}')
         break;
      if(c == ',')
      {
         p++;
         continue;
      }
      string k;
      p = TsmcJsonReadString(js, p, k);
      if(p < 0)
         break;
      p = TsmcJsonSkipWs(js, p);
      if(p >= n || StringGetCharacter(js, p) != ':')
         break;
      p = TsmcJsonSkipWs(js, p + 1);
      keys += k + "|";
      if(found < 0 && k == key)
         found = p;
      p = TsmcJsonSkipValue(js, p);
      if(p < 0)
         break;
   }
   return(found);
}

long TsmcJsonLongAt(const string &js, int p)
{
   return(p < 0 ? 0 : StringToInteger(StringSubstr(js, p, 24)));
}

int TsmcHexDigit(ushort c)
{
   if(c >= '0' && c <= '9') return((int)c - '0');
   if(c >= 'a' && c <= 'f') return((int)c - 'a' + 10);
   if(c >= 'A' && c <= 'F') return((int)c - 'A' + 10);
   return(-1);
}

// JSON string escapes -> text. Line breaks/tabs become spaces; any
// non-ASCII \uXXXX (emoji, symbols) becomes a space.
string TsmcJsonUnescape(const string &raw)
{
   string out = "";
   int n = StringLen(raw);
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(raw, i);
      if(c != '\\' || i + 1 >= n)
      {
         out += ShortToString(c);
         continue;
      }
      ushort e = StringGetCharacter(raw, i + 1);
      i++;
      if(e == 'n' || e == 'r' || e == 't')
         out += " ";
      else if(e == 'u' && i + 4 < n)
      {
         int code = 0;
         bool okHex = true;
         for(int h = 1; h <= 4; h++)
         {
            int v = TsmcHexDigit(StringGetCharacter(raw, i + h));
            if(v < 0) { okHex = false; break; }
            code = code * 16 + v;
         }
         if(okHex)
            i += 4;
         out += (okHex && code >= 32 && code < 127) ? ShortToString((ushort)code) : " ";
      }
      else
         out += ShortToString(e);   // \" \\ \/
   }
   return(out);
}

// Fills `u` from the update object that starts at updStart.
void TsmcParseUpdate(const string &js, int updStart, bool acceptPhotoCaptions, TsmcTgUpdate &u)
{
   u.update_id = 0; u.chat_id = 0; u.date = 0; u.isEdited = false; u.text = ""; u.skip = "";
   string keys, mkeys, ignore;
   u.update_id = TsmcJsonLongAt(js, TsmcJsonFindMember(js, updStart, "update_id", keys));

   string container = "";
   if(StringFind(keys, "|message|") >= 0)                  container = "message";
   else if(StringFind(keys, "|channel_post|") >= 0)        container = "channel_post";
   else if(StringFind(keys, "|edited_message|") >= 0)      container = "edited_message";
   else if(StringFind(keys, "|edited_channel_post|") >= 0) container = "edited_channel_post";
   if(container == "")
   {
      u.skip = "not a message update";
      return;
   }
   u.isEdited = (StringFind(container, "edited_") == 0);
   int msg = TsmcJsonFindMember(js, updStart, container, ignore);
   if(msg < 0 || StringGetCharacter(js, msg) != '{')
   {
      u.skip = "malformed message";
      return;
   }
   int chat = TsmcJsonFindMember(js, msg, "chat", mkeys);
   if(chat >= 0)
      u.chat_id = TsmcJsonLongAt(js, TsmcJsonFindMember(js, chat, "id", ignore));
   u.date = TsmcJsonLongAt(js, TsmcJsonFindMember(js, msg, "date", ignore));

   // Media / service posts: omitted, whatever their caption says.
   string media[] = {"video", "video_note", "audio", "voice", "animation", "sticker", "document",
                     "poll", "dice", "location", "venue", "contact", "game", "story", "invoice"};
   for(int m = 0; m < ArraySize(media); m++)
      if(StringFind(mkeys, "|" + media[m] + "|") >= 0)
      {
         u.skip = media[m];
         return;
      }
   string field = "text";
   if(StringFind(mkeys, "|photo|") >= 0)
   {
      if(!acceptPhotoCaptions || StringFind(mkeys, "|caption|") < 0)
      {
         u.skip = "photo";
         return;
      }
      field = "caption";
   }
   int t = TsmcJsonFindMember(js, msg, field, ignore);
   string raw;
   if(t < 0 || TsmcJsonReadString(js, t, raw) < 0)
   {
      u.skip = "no text (service message: pin, join, title change...)";
      return;
   }
   u.text = TsmcJsonUnescape(raw);
}

// getUpdates JSON -> updates[]. Returns how many updates were found.
int TsmcExtractUpdates(const string &json, TsmcTgUpdate &updates[], bool acceptPhotoCaptions)
{
   ArrayResize(updates, 0);
   string keys;
   int res = TsmcJsonFindMember(json, 0, "result", keys);
   int n = StringLen(json);
   if(res < 0 || res >= n || StringGetCharacter(json, res) != '[')
      return(0);
   int p = res + 1;
   while(p < n)
   {
      p = TsmcJsonSkipWs(json, p);
      if(p >= n)
         break;
      ushort c = StringGetCharacter(json, p);
      if(c == ']')
         break;
      if(c == ',')
      {
         p++;
         continue;
      }
      if(c != '{')
         break;
      int end = TsmcJsonSkipValue(json, p);
      if(end < 0)
         break;
      TsmcTgUpdate u;
      TsmcParseUpdate(json, p, acceptPhotoCaptions, u);
      int k = ArraySize(updates);
      ArrayResize(updates, k + 1);
      updates[k] = u;
      p = end;
   }
   return(ArraySize(updates));
}

//+------------------------------------------------------------------+
//| TRADE-ONLY MESSAGE FILTER - the same rules as python/              |
//| message_filter.py (keep both in sync). Returns TSMC_MSG_SIGNAL,    |
//| TSMC_MSG_COMMAND or TSMC_MSG_SKIP (+ why):                          |
//|  SIGNAL : <= maxChars, BUY/SELL/LONG/SHORT + a price-shaped number |
//|           (decimals or >= 100) + GOLD/XAU... or an SL/TP/ENTRY     |
//|           label;                                                    |
//|  COMMAND: <= maxCommandChars, a command (CLOSE/EXIT/CANCEL/        |
//|           BREAKEVEN/BE, or SL with MOVE/ENTRY) made ONLY of        |
//|           trading words - "Close all gold trades now" passes,      |
//|           "Good morning! Close your charts and relax" does not.    |
//|  SKIP   : greetings, mood posts, commentary, promos, emoji-only,   |
//|           anything longer than the limits.                          |
//+------------------------------------------------------------------+
#define TSMC_MSG_SKIP     0
#define TSMC_MSG_SIGNAL   1
#define TSMC_MSG_COMMAND  2

#define TSMC_COMMAND_VOCAB "|CLOSE|EXIT|CANCEL|BREAKEVEN|BREAK|EVEN|BE|MOVE|SET|PUT|SL|S|L|STOP|LOSS|STOPLOSS|RISK|FREE|TO|ENTRY|AT|ALL|NOW|HERE|IT|THIS|THAT|THE|A|AN|REST|REMAINING|TRADE|TRADES|POSITION|POSITIONS|ORDER|ORDERS|PENDING|LIMIT|LIMITS|SIGNAL|SIGNALS|RUNNING|OPEN|GOLD|XAUUSD|XAU|USD|BUY|BUYS|SELL|SELLS|LONG|LONGS|SHORT|SHORTS|TP|TAKE|PROFIT|PROFITS|SECURE|BOOK|HALF|PARTIAL|PARTIALS|PARTIALLY|FULL|FULLY|MARKET|PRICE|PIPS|PIP|POINTS|PTS|IN|ON|OF|FOR|WITH|AND|OR|YOUR|OUR|MY|GUYS|PLEASE|PLS|EVERYONE|TEAM|MANUALLY|EARLY|QUICK|QUICKLY|IMMEDIATELY|ASAP|VIP|UPDATE|ALERT|"

bool TsmcIsDigitCh(ushort c) { return(c >= '0' && c <= '9'); }

bool TsmcIsNumberWord(const string &w)
{
   int n = StringLen(w);
   if(n == 0 || !TsmcIsDigitCh(StringGetCharacter(w, 0)))
      return(false);
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(w, i);
      if(!TsmcIsDigitCh(c) && c != '.')
         return(false);
   }
   return(true);
}

bool TsmcIsTpWord(const string &w)   // TP1 .. TP99
{
   int n = StringLen(w);
   if(n < 3 || n > 4 || StringSubstr(w, 0, 2) != "TP")
      return(false);
   for(int i = 2; i < n; i++)
      if(!TsmcIsDigitCh(StringGetCharacter(w, i)))
         return(false);
   return(true);
}

int TsmcClassifyMessage(const string &text, int maxChars, int maxCommandChars, string &reason)
{
   string t = text;
   StringTrimLeft(t);
   StringTrimRight(t);
   int len = StringLen(t);
   if(len == 0) { reason = "empty"; return(TSMC_MSG_SKIP); }
   if(maxChars > 0 && len > maxChars)
   {
      reason = StringFormat("long message (%d chars > %d)", len, maxChars);
      return(TSMC_MSG_SKIP);
   }
   string up = t;
   StringToUpper(up);

   // Words: runs of A-Z/0-9, keeping a '.' that sits between two digits.
   string words[];
   int nw = 0;
   string cur = "";
   for(int i = 0; i <= len; i++)
   {
      ushort c = (i < len) ? StringGetCharacter(up, i) : (ushort)' ';
      bool alnum = (c >= 'A' && c <= 'Z') || TsmcIsDigitCh(c);
      bool decDot = (c == '.' && StringLen(cur) > 0 && TsmcIsDigitCh(StringGetCharacter(cur, StringLen(cur) - 1))
                     && i + 1 < len && TsmcIsDigitCh(StringGetCharacter(up, i + 1)));
      if(alnum || decDot)
      {
         cur += ShortToString(c);
         continue;
      }
      if(StringLen(cur) > 0)
      {
         ArrayResize(words, nw + 1);
         words[nw++] = cur;
         cur = "";
      }
   }
   if(nw == 0) { reason = "no words (emoji/sticker-style message)"; return(TSMC_MSG_SKIP); }

   bool hasDir = false, hasSymbol = false, hasLabel = false, hasPrice = false, hasCmd = false;
   bool hasBreak = false, hasEven = false, hasSl = false, hasMove = false, hasEntry = false;
   for(int w = 0; w < nw; w++)
   {
      string x = words[w];
      if(x == "BUY" || x == "SELL" || x == "LONG" || x == "SHORT") hasDir = true;
      if(x == "GOLD" || StringFind(x, "XAU") == 0)                 hasSymbol = true;
      if(x == "SL" || x == "TP" || x == "ENTRY" || x == "STOPLOSS" || x == "TARGET" || TsmcIsTpWord(x))
         hasLabel = true;
      if(TsmcIsNumberWord(x) && (StringFind(x, ".") >= 0 || StringToDouble(x) >= 100.0))
         hasPrice = true;
      if(x == "CLOSE" || x == "EXIT" || x == "CANCEL" || x == "BREAKEVEN" || x == "BE") hasCmd = true;
      if(x == "BREAK") hasBreak = true;
      if(x == "EVEN")  hasEven = true;
      if(x == "SL")    hasSl = true;
      if(x == "MOVE")  hasMove = true;
      if(x == "ENTRY") hasEntry = true;
   }
   if(hasDir && hasPrice && (hasSymbol || hasLabel))
   {
      reason = "buy/sell + price + symbol or SL/TP";
      return(TSMC_MSG_SIGNAL);
   }
   if(hasCmd || (hasBreak && hasEven) || (hasSl && (hasMove || hasEntry)))
   {
      if(maxCommandChars > 0 && len > maxCommandChars)
      {
         reason = StringFormat("command word inside a long message (%d chars > %d)", len, maxCommandChars);
         return(TSMC_MSG_SKIP);
      }
      for(int w = 0; w < nw; w++)
      {
         string x = words[w];
         if(TsmcIsNumberWord(x) || TsmcIsTpWord(x))
            continue;
         if(StringFind(TSMC_COMMAND_VOCAB, "|" + x + "|") < 0)
         {
            reason = "command word in ordinary chat (e.g. '" + x + "')";
            return(TSMC_MSG_SKIP);
         }
      }
      reason = "short trading command";
      return(TSMC_MSG_COMMAND);
   }
   reason = hasDir ? "mentions buy/sell without a price and a symbol or SL/TP"
                   : "not a trade message (greeting, mood, commentary, promo...)";
   return(TSMC_MSG_SKIP);
}
//+------------------------------------------------------------------+
