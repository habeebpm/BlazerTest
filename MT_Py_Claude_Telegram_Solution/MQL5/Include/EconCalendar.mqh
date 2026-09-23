//+------------------------------------------------------------------+
//|                                          EconCalendar.mqh         |
//|                                                                    |
//| MT5's built-in economic calendar (MetaQuotes data - no API key, no |
//| extra download) for UnifiedTrader_EA.mq5 and                       |
//| ClaudeSMC_TradeManager.mq5:                                        |
//|   EconMaybeExport() - every few minutes, writes the recent and     |
//|     upcoming events for the watched currencies to a CSV in the     |
//|     shared Common\Files folder. ClaudeSMC_Trader's                 |
//|     python/econ_calendar.py reads it for its automatic news        |
//|     blackout and for the calendar context Claude sees.             |
//|   EconNewsBlock() - true while an event of at least the given      |
//|     importance is within the block window (skips new entries).     |
//|   EconSummary() - plain text for the Telegram "News" button.       |
//|                                                                    |
//| The calendar reports trade-server time; everything this file       |
//| writes or prints is converted to genuine UTC. The calendar is not  |
//| available in the Strategy Tester, and can be empty until the       |
//| terminal has synced it - every function then behaves as "no        |
//| events" (never blocks, never overwrites a good export with an       |
//| empty one) instead of failing.                                     |
//+------------------------------------------------------------------+
#property strict

enum ENUM_ECON_IMPORTANCE
{
   ECON_IMPORTANCE_LOW      = 1,   // Low and above
   ECON_IMPORTANCE_MODERATE = 2,   // Moderate and above
   ECON_IMPORTANCE_HIGH     = 3    // High only
};

struct EconEvent
{
   datetime timeUtc;
   string   currency;
   int      importance;   // 1 low, 2 moderate, 3 high, 0 none
   string   name;
   string   actual;       // "" until released
   string   forecast;
   string   previous;
   string   impact;       // effect of actual vs forecast on the currency: positive/negative/na
};

datetime g_econLastExport  = 0;
bool     g_econWarnedEmpty = false;

//+------------------------------------------------------------------+
//| Seconds to ADD to trade-server time to get UTC, rounded to the     |
//| nearest 15 minutes (brokers use whole/half/quarter-hour offsets).  |
//+------------------------------------------------------------------+
long EconServerToUtcOffset()
{
   long diff = (long)TimeGMT() - (long)TimeTradeServer();
   return((long)MathRound(diff / 900.0) * 900);
}

int EconImportanceRank(ENUM_CALENDAR_EVENT_IMPORTANCE imp)
{
   if(imp == CALENDAR_IMPORTANCE_HIGH)     return(3);
   if(imp == CALENDAR_IMPORTANCE_MODERATE) return(2);
   if(imp == CALENDAR_IMPORTANCE_LOW)      return(1);
   return(0);
}

string EconImportanceName(int rank)
{
   if(rank >= 3) return("high");
   if(rank == 2) return("moderate");
   if(rank == 1) return("low");
   return("none");
}

string EconImpactName(ENUM_CALENDAR_EVENT_IMPACT impact)
{
   if(impact == CALENDAR_IMPACT_POSITIVE) return("positive");
   if(impact == CALENDAR_IMPACT_NEGATIVE) return("negative");
   return("na");
}

//+------------------------------------------------------------------+
//| Gold is priced in USD: a USD-positive surprise usually weighs on    |
//| gold, a USD-negative one usually supports it. Only USD is mapped -  |
//| other currencies' effect on gold is too indirect to state.          |
//+------------------------------------------------------------------+
string EconGoldImpact(const string currency, const string impact)
{
   if(currency != "USD") return("");
   if(impact == "positive") return("USD-positive: bearish for gold");
   if(impact == "negative") return("USD-negative: bullish for gold");
   return("");
}

string EconTrimNumber(double v)
{
   string s = DoubleToString(v, 4);
   if(StringFind(s, ".") >= 0)
   {
      while(StringLen(s) > 0 && StringGetCharacter(s, StringLen(s) - 1) == '0')
         s = StringSubstr(s, 0, StringLen(s) - 1);
      if(StringLen(s) > 0 && StringGetCharacter(s, StringLen(s) - 1) == '.')
         s = StringSubstr(s, 0, StringLen(s) - 1);
   }
   return(s);
}

// Calendar values are stored x 1,000,000; LONG_MIN means "not published".
string EconValueText(long raw)
{
   if(raw == LONG_MIN) return("");
   return(EconTrimNumber(raw / 1000000.0));
}

// "2026-09-23 13:30:00"
string EconTimeText(datetime t)
{
   string s = TimeToString(t, TIME_DATE|TIME_SECONDS);
   StringReplace(s, ".", "-");
   return(s);
}

string EconCsvQuote(const string v)
{
   string t = v;
   StringReplace(t, "\"", "\"\"");
   return("\"" + t + "\"");
}

int EconCurrencies(const string csv, string &out[])
{
   ArrayResize(out, 0);
   string parts[];
   int n = StringSplit(csv, ',', parts);
   for(int i = 0; i < n; i++)
   {
      string c = parts[i];
      StringTrimLeft(c);
      StringTrimRight(c);
      StringToUpper(c);
      if(StringLen(c) == 0)
         continue;
      int k = ArraySize(out);
      ArrayResize(out, k + 1);
      out[k] = c;
   }
   return(ArraySize(out));
}

//+------------------------------------------------------------------+
//| Every event for `currencies` (comma-separated, e.g. "USD,EUR")     |
//| whose UTC time falls in [fromUtc, toUtc], oldest first.            |
//+------------------------------------------------------------------+
int EconCollect(const string currencies, datetime fromUtc, datetime toUtc, EconEvent &out[])
{
   ArrayResize(out, 0);
   string cur[];
   if(EconCurrencies(currencies, cur) == 0)
      return(0);
   long off = EconServerToUtcOffset();
   datetime fromSrv = (datetime)((long)fromUtc - off);
   datetime toSrv   = (datetime)((long)toUtc - off);

   for(int c = 0; c < ArraySize(cur); c++)
   {
      MqlCalendarValue values[];
      ResetLastError();
      CalendarValueHistory(values, fromSrv, toSrv, NULL, cur[c]);
      for(int i = 0; i < ArraySize(values); i++)
      {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev))
            continue;
         EconEvent e;
         e.timeUtc    = (datetime)((long)values[i].time + off);
         e.currency   = cur[c];
         e.importance = EconImportanceRank(ev.importance);
         e.name       = ev.name;
         e.actual     = EconValueText(values[i].actual_value);
         e.forecast   = EconValueText(values[i].forecast_value);
         e.previous   = EconValueText(values[i].prev_value);
         e.impact     = EconImpactName(values[i].impact_type);
         int k = ArraySize(out);
         ArrayResize(out, k + 1);
         out[k] = e;
      }
   }

   int n = ArraySize(out);
   for(int i = 1; i < n; i++)
   {
      EconEvent key = out[i];
      int j = i - 1;
      while(j >= 0 && out[j].timeUtc > key.timeUtc)
      {
         out[j + 1] = out[j];
         j--;
      }
      out[j + 1] = key;
   }
   return(n);
}

//+------------------------------------------------------------------+
//| Writes events from `backHours` ago to `aheadDays` ahead to        |
//| Common\Files\<filename>. First line is "# exported_at_utc=...",    |
//| then a CSV header. An empty result is NOT written (the calendar     |
//| isn't synced yet) so a good earlier export is never wiped.          |
//+------------------------------------------------------------------+
bool EconExportCsv(const string filename, const string currencies, int backHours, int aheadDays)
{
   if(StringLen(filename) == 0)
      return(false);
   datetime nowUtc = TimeGMT();
   EconEvent events[];
   int n = EconCollect(currencies, nowUtc - backHours * 3600, nowUtc + aheadDays * 86400, events);
   if(n == 0)
   {
      if(!g_econWarnedEmpty)
         PrintFormat("EconCalendar: no calendar events for %s yet (calendar not synced, or no "
                     "connection to the MetaQuotes calendar) - export skipped; will retry.",
                     currencies);
      g_econWarnedEmpty = true;
      return(false);
   }
   g_econWarnedEmpty = false;

   int h = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      PrintFormat("EconCalendar: could not write %s (error %d).", filename, GetLastError());
      return(false);
   }
   FileWriteString(h, "# exported_at_utc=" + EconTimeText(nowUtc) + "\r\n");
   FileWriteString(h, "time_utc,currency,importance,event,actual,forecast,previous,impact\r\n");
   for(int i = 0; i < n; i++)
      FileWriteString(h, EconTimeText(events[i].timeUtc) + "," + events[i].currency + "," +
                         EconImportanceName(events[i].importance) + "," +
                         EconCsvQuote(events[i].name) + "," + events[i].actual + "," +
                         events[i].forecast + "," + events[i].previous + "," +
                         events[i].impact + "\r\n");
   FileClose(h);
   return(true);
}

//+------------------------------------------------------------------+
//| Call from OnTick/OnTimer: exports at most once per refreshMin.     |
//| Never in the Strategy Tester - FILE_COMMON there is the REAL shared |
//| folder, so a test run would overwrite the live export.              |
//+------------------------------------------------------------------+
void EconMaybeExport(const string filename, const string currencies, int refreshMin)
{
   if(StringLen(filename) == 0 || MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   datetime now = TimeLocal();
   if(g_econLastExport != 0 && (long)(now - g_econLastExport) < (long)MathMax(1, refreshMin) * 60)
      return;
   g_econLastExport = now;
   EconExportCsv(filename, currencies, 24, 7);
}

//+------------------------------------------------------------------+
//| True (with a description) if an event of at least minImportance   |
//| for `currencies` is scheduled within [now - afterMin,              |
//| now + beforeMin]. Always false in the Strategy Tester (no data).   |
//+------------------------------------------------------------------+
bool EconNewsBlock(const string currencies, int minImportance, int beforeMin, int afterMin,
                   string &desc)
{
   desc = "";
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      return(false);
   datetime nowUtc = TimeGMT();
   EconEvent events[];
   int n = EconCollect(currencies, nowUtc - afterMin * 60, nowUtc + beforeMin * 60, events);
   for(int i = 0; i < n; i++)
   {
      if(events[i].importance < minImportance)
         continue;
      desc = StringFormat("%s %s (%s impact) at %s UTC", events[i].currency, events[i].name,
                          EconImportanceName(events[i].importance), EconTimeText(events[i].timeUtc));
      return(true);
   }
   return(false);
}

string EconMinutesText(long minutes)
{
   if(minutes < 60)
      return(StringFormat("%dm", (int)minutes));
   return(StringFormat("%dh%02dm", (int)(minutes / 60), (int)(minutes % 60)));
}

//+------------------------------------------------------------------+
//| Plain-text summary for the Telegram "News" button: released events |
//| from the last `hoursBack` (actual vs forecast and what it means for |
//| gold) and upcoming ones in the next `hoursAhead`, at least          |
//| minImportance, at most 10 of each.                                  |
//+------------------------------------------------------------------+
string EconSummary(const string currencies, int minImportance, int hoursBack, int hoursAhead)
{
   datetime nowUtc = TimeGMT();
   EconEvent events[];
   int n = EconCollect(currencies, nowUtc - hoursBack * 3600, nowUtc + hoursAhead * 3600, events);
   string recent = "";
   string upcoming = "";
   int nRecent = 0;
   int nUpcoming = 0;
   for(int i = 0; i < n; i++)
   {
      if(events[i].importance < minImportance)
         continue;
      string when = StringSubstr(EconTimeText(events[i].timeUtc), 5, 11);   // "09-23 13:30"
      if(events[i].timeUtc <= nowUtc)
      {
         if(StringLen(events[i].actual) == 0 || nRecent >= 10)
            continue;
         string gold = EconGoldImpact(events[i].currency, events[i].impact);
         recent += StringFormat("\n%s %s %s [%s]: actual %s vs forecast %s (prev %s)%s",
                                when, events[i].currency, events[i].name,
                                EconImportanceName(events[i].importance), events[i].actual,
                                StringLen(events[i].forecast) > 0 ? events[i].forecast : "-",
                                StringLen(events[i].previous) > 0 ? events[i].previous : "-",
                                StringLen(gold) > 0 ? " -> " + gold : "");
         nRecent++;
      }
      else
      {
         if(nUpcoming >= 10)
            continue;
         long mins = ((long)events[i].timeUtc - (long)nowUtc) / 60;
         upcoming += StringFormat("\n%s (in %s) %s %s [%s]%s", when, EconMinutesText(mins),
                                  events[i].currency, events[i].name,
                                  EconImportanceName(events[i].importance),
                                  StringLen(events[i].forecast) > 0 ? ", forecast " + events[i].forecast : "");
         nUpcoming++;
      }
   }
   if(n == 0)
      return("No calendar data yet - MT5's economic calendar hasn't synced (check the terminal's "
             "Calendar tab), or this is the Strategy Tester.");
   string text = StringFormat("Economic calendar (%s, %s+ impact, times UTC)", currencies,
                              EconImportanceName(minImportance));
   text += "\n\nReleased (last " + IntegerToString(hoursBack) + "h):" +
           (nRecent > 0 ? recent : "\nnone");
   text += "\n\nUpcoming (next " + IntegerToString(hoursAhead) + "h):" +
           (nUpcoming > 0 ? upcoming : "\nnone");
   return(text);
}
