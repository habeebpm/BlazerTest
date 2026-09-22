//+------------------------------------------------------------------+
//|                                         UnifiedTrader_EA.mq5       |
//|                                                                    |
//| One EA, two independent trade SOURCES, ONE shared risk model.     |
//| Toggle InpEnableTelegramSignals / InpEnableClaudeManagement        |
//| separately - run either alone, or both together on the same       |
//| chart/account. Both flags gate NEW ENTRIES ONLY (Telegram polling/ |
//| signal execution). EXIT MANAGEMENT IS NEVER GATED BY THEM: once a  |
//| position exists under InpTelegramMagicNumber or                    |
//| InpClaudeMagicNumber, this EA keeps protecting it (lock-then-trail |
//| below) for as long as it's running, even if the corresponding flag |
//| is later turned off - turning a source off only stops it from      |
//| taking NEW signals, it never abandons a position already open      |
//| under that source's magic number. See ManageAllPositions().        |
//|                                                                    |
//| SOURCE 1 - TELEGRAM (InpEnableTelegramSignals): this EA polls the |
//| Telegram Bot API itself (WebRequest, same mechanism as             |
//| ../../MQL5/Experts/TelegramSMC_Copier.mq5) and EXECUTES A SIGNAL   |
//| AS SOON AS IT PARSES, WITH NO SMC VALIDATION AND NO USE OF THE     |
//| MESSAGE'S OWN STOP-LOSS. This is a deliberate, documented          |
//| simplification from TelegramSMC_Copier.mq5, not an oversight - it  |
//| drops that EA's liquidity-sweep/premium-discount SMC gate AND its  |
//| SL-sanity checks (min/max SL distance) entirely, because this EA   |
//| never uses the message's own SL/TP numbers at all: every position, |
//| regardless of source, gets the SAME fixed risk (InpSlDollars/      |
//| InpTp1Dollars/InpTrailDollars below) - so a message's own stated   |
//| stop has nothing to sanity-check against. What IS still checked,   |
//| because it doesn't depend on the message's own numbers: the chat   |
//| is on the allow-list, the signal isn't stale (InpMaxSignalAgeSec), |
//| the current price hasn't run away from the signaled zone           |
//| (InpMaxEntryDeviationPips), and the shared position cap/daily-trade|
//| cap below. The message is used ONLY for direction (buy/sell) and   |
//| an entry zone (which edge of the zone price reaches first decides  |
//| pending vs market, exactly like TelegramSMC_Copier.mq5).           |
//|                                                                    |
//| SOURCE 2 - CLAUDE (InpEnableClaudeManagement): unlike Telegram,    |
//| this EA does NOT decide Claude-driven entries itself - MQL5 has no |
//| practical way to call the Claude API (no JSON library, and         |
//| reimplementing ../../ClaudeSMC_Trader/python/market_intel.py's     |
//| full indicator/SMC-structure stack in MQL5 just to ask an LLM a    |
//| question is not a reasonable trade). ../../ClaudeSMC_Trader/       |
//| python/main.py keeps deciding those entries exactly as it does     |
//| today, opening positions under InpClaudeMagicNumber. This EA's     |
//| only job for that magic number is EXIT management - tick-by-tick,  |
//| which Python's poll loop is too coarse-grained to do reliably,     |
//| identical in mechanism to ../../ClaudeSMC_Trader/MQL5/Experts/     |
//| ClaudeSMC_TradeManager.mq5 (this file's ManagePositionExit() is    |
//| that EA's ManagePosition(), reused verbatim). InpClaudeMagicNumber |
//| MUST match python/config.py's AdvisorConfig.magic (both default    |
//| 20260921) or this EA will simply never see the positions Python    |
//| opens.                                                              |
//|                                                                    |
//| SHARED EXIT DESIGN, BOTH SOURCES (exit_style="sl_to_tp1", matching |
//| ClaudeSMC_TradeManager.mq5): no broker take-profit is ever placed. |
//| The stop-loss is the only exit mechanism. Once floating profit     |
//| reaches InpTp1Dollars, the SL moves to EXACTLY that price - locking|
//| in that much profit, no more no less, in one deterministic step -  |
//| then trails InpTrailDollars behind new highs/lows from there,      |
//| tightening only. "Armed" (locked vs. still trailing) is derived    |
//| every tick from whether a position's OWN current SL has already    |
//| reached the lock level, never stored - this EA needs no memory     |
//| across ticks or restarts and stays correct even if reattached      |
//| mid-trade. Dollar amounts are converted to a price distance per     |
//| position using that position's own volume and the symbol's live    |
//| tick value/size (price_distance = dollars * tick_size /            |
//| (tick_value * volume)) - never an assumed contract size.            |
//|                                                                    |
//| SHARED POSITION CAP: InpMaxPositionsPerDirection (default 5) is a  |
//| SINGLE combined ceiling counted across BOTH InpTelegramMagicNumber |
//| and InpClaudeMagicNumber together, always - not 5 each. This is    |
//| enforced from THIS EA's side for new Telegram entries; the Python  |
//| side has its own equivalent, separately-configured cap via         |
//| AdvisorConfig.shared_cap_magic_numbers (see                        |
//| ../../ClaudeSMC_Trader/README.md) - set that to                    |
//| [InpTelegramMagicNumber] so Python's OWN gate also backs off once   |
//| Telegram-sourced positions fill the shared cap; without that, this |
//| EA still won't let Telegram open past the shared cap, but Python   |
//| could still open Claude-sourced trades past it independently,      |
//| since this EA cannot intercept orders Python places directly.      |
//|                                                                    |
//| WHAT'S DELIBERATELY OUT OF SCOPE (vs. TelegramSMC_Copier.mq5):      |
//| the SMC filter, SL-distance sanity checks (nothing to check - see  |
//| above), and the "move SL to breakeven" text command (the automatic |
//| lock-then-trail exit already gets every position to at least       |
//| breakeven-or-better once InpTp1Dollars is reached, faster and more |
//| consistently than a manual per-message command could). CLOSE/      |
//| CANCEL are still supported and act on this EA's own                |
//| InpTelegramMagicNumber positions/pending orders only - never on    |
//| InpClaudeMagicNumber ones, which are Python's to manage.            |
//|                                                                    |
//| TELEGRAM SETUP (only if InpEnableTelegramSignals): identical to    |
//| TelegramSMC_Copier.mq5's - @BotFather /newbot for InpBotToken, add  |
//| that bot to the channel as admin, Tools > Options > Expert Advisors|
//| > allow WebRequest for https://api.telegram.org, leave              |
//| InpChannelId1/InpChannelId2 at 0 for the first run to discover chat |
//| ids from the log, then set them and restart.                        |
//|                                                                    |
//| IMPORTANT DISCLAIMER: with InpEnableTelegramSignals on, this EA     |
//| executes real orders from Telegram text messages with NO liquidity- |
//| sweep/structure validation and NO check on the message's own SL -   |
//| a materially higher-risk configuration than TelegramSMC_Copier.mq5. |
//| Demo-test with InpDryRun=true until you trust the channel AND this  |
//| EA's logged behavior for every message it sees, in that order.      |
//| Runs with InpDryRun=true until you turn it off; nothing above this  |
//| line touches an order or a position.                                |
//+------------------------------------------------------------------+
#property copyright "UnifiedTrader_EA"
#property link      ""
#property version   "1.00"
#property strict
#property description "One EA, two trade sources sharing one risk model: unvalidated Telegram signal execution (fixed $6/$6/$3 SL/lock/trail, no SMC filter, no message-SL sanity check) and/or exit management for ClaudeSMC_Trader's Python-decided positions. Toggle either or both. Educational use - demo-test with InpDryRun=true before risking real capital."

#include <Trade\Trade.mqh>
#include <TelegramSMC_Common.mqh>

//================================= CONSTANTS ====================================
#define DIR_NONE        (-1)
#define DIR_BUY         0
#define DIR_SELL        1

#define ACTION_UNKNOWN   0
#define ACTION_OPEN      1
#define ACTION_CLOSE     2
#define ACTION_CANCEL    3
#define ACTION_MODIFY_SL 4

#define GV_LAST_UPDATE_ID "UnifiedTrader_EA_LastUpdateId"

//================================= INPUTS ====================================

input group "=== Mode selection - enable either or both ==="
input bool    InpEnableTelegramSignals = false;   // Poll Telegram and execute signals (no SMC validation - see file header)
input bool    InpEnableClaudeManagement = false;  // Manage exits for ClaudeSMC_Trader's Python-opened positions

input group "=== Shared business rules - apply to BOTH sources ==="
input double  InpFixedLot               = 0.01;   // Lot size for every Telegram-sourced entry
input int     InpMaxPositionsPerDirection = 5;     // SHARED cap, counted across BOTH magics together
input double  InpSlDollars              = 6.0;     // Initial stop-loss (USD-equivalent price distance)
input double  InpTp1Dollars             = 6.0;     // Floating profit that locks the SL in here (exact, no buffer)
input double  InpTrailDollars           = 3.0;     // Trailing distance once locked/armed

input group "=== Identification ==="
input long    InpTelegramMagicNumber = 20260922;   // This EA's own Telegram-sourced trades
input long    InpClaudeMagicNumber   = 20260921;   // MUST match python/config.py AdvisorConfig.magic
input bool    InpDryRun              = true;       // Log what would happen; do not send/modify real orders

input group "=== Telegram Bot - only used if InpEnableTelegramSignals (see file header for setup) ==="
input string  InpBotToken          = "";           // Bot token from @BotFather
input long    InpChannelId1        = 0;            // Only copy signals from this chat id (0 = slot unused)
input long    InpChannelId2        = 0;            // ...and this one (0 = slot unused; both 0 = ANY chat - unsafe, first-run only)
input int     InpPollSeconds       = 5;            // How often to poll Telegram for new messages
input int     InpHttpTimeoutMs     = 5000;         // WebRequest timeout (ms)
input int     InpMaxSignalAgeSec   = 180;          // Reject a signal older than this many seconds (0 = no limit)
input bool    InpTradeXAUUSDOnly   = true;         // Require chart symbol to contain "XAU"
input int     InpMaxTradesPerDay   = 0;            // 0 = unlimited (Telegram-sourced trades only)
input int     InpPendingExpiryMin  = 240;          // Cancel an unfilled pending order after N minutes (0 = never)

input group "=== Telegram Signal Sanity - kept from TelegramSMC_Copier.mq5 (pips; 1 pip = 10 broker points) ==="
input double  InpMaxEntryDeviationPips = 200.0;    // Reject if current price is this far outside the signaled zone

//================================= TYPES ====================================

struct SignalMsg
{
   int    action;
   int    direction;
   bool   symbolOk;
   bool   hasRange;
   double entryA;
   double entryB;
   bool   hasSl;
   double sl;          // parsed for the log row only - NEVER used to size an order, see file header
   double tps[6];       // parsed for the log row only - never used
   int    tpCount;
};

struct TgUpdate
{
   long   update_id;
   long   chat_id;
   long   date;
   bool   isEdited;
   string text;
};

//================================= GLOBALS ====================================

CTrade   trade;
long     g_lastUpdateId  = 0;
datetime g_currentDay    = 0;
int      g_tradesToday   = 0;

// Forward declarations
double   PipSize();
bool     IsAllowedChat(long chatId);
datetime DateToDay(datetime t);
void     UpdateDailyTracking();
bool     IsDigitCh(ushort ch);
bool     IsLetterCh(ushort ch);
bool     IsWordCh(ushort ch);
int      FindWholeWord(const string &text, const string &word, int startPos = 0);
int      FindTpLabel(const string &text, int fromPos, int &labelEnd);
int      FindSlLabel(const string &text, int fromPos, int &labelEnd);
bool     ExtractNumberAt(const string &text, int fromPos, int limitPos, int maxSkip,
                          double &value, int &endPos);
bool     MentionsGold(const string &upperText);
void     ParseSignalText(const string &rawText, SignalMsg &msg);
double   DollarsToPrice(double dollars, double volume);
int      CountSameDirection(int direction);
void     ExpirePendingOrders();
void     CloseAllMine();
void     CancelAllPendingMine();
bool     PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                           string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode);
void     ManagePositionExit(ulong ticket, long magic);
void     ManageAllPositions();
void     ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText);
bool     TelegramGetUpdates(string &jsonOut);
void     ExtractUpdates(const string &json, TgUpdate &updates[]);
void     TelegramPoll();
void     StripUnicodeEscapes(string &s);
string   DirToStr(int dir);
void     LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                       double entryLow, double entryHigh, const string &tpsJoined,
                       bool sanityPass, const string &sanityReason, bool accepted,
                       const string &orderType, double orderPrice, double lots, bool dryRun,
                       long orderTicket, int retcode, const string &rawText);

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!InpEnableTelegramSignals && !InpEnableClaudeManagement)
      Print("UnifiedTrader_EA: WARNING - both InpEnableTelegramSignals and InpEnableClaudeManagement "
            "are false, so no NEW entries will ever be taken. This is a valid configuration (e.g. "
            "pausing new signals while this EA keeps managing exits for any positions still open "
            "under InpTelegramMagicNumber/InpClaudeMagicNumber from before - see ManageAllPositions, "
            "which is never gated by these flags), but if that's not what you intended, enable at "
            "least one.");
   if(InpSlDollars <= 0.0 || InpTp1Dollars <= 0.0 || InpTrailDollars <= 0.0 || InpFixedLot <= 0.0)
   {
      Print("UnifiedTrader_EA: InpFixedLot, InpSlDollars, InpTp1Dollars and InpTrailDollars must all be positive.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpEnableTelegramSignals && InpTelegramMagicNumber == InpClaudeMagicNumber)
   {
      Print("UnifiedTrader_EA: InpTelegramMagicNumber and InpClaudeMagicNumber must differ - otherwise "
            "this EA cannot tell the two sources' positions apart.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   if(InpEnableTelegramSignals)
   {
      if(InpTradeXAUUSDOnly && StringFind(_Symbol, "XAU") < 0)
      {
         Print("UnifiedTrader_EA: chart symbol '", _Symbol, "' does not contain 'XAU'. Attach to a "
               "Gold (XAUUSD) chart, or disable InpTradeXAUUSDOnly to override.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      if(StringLen(InpBotToken) == 0)
      {
         Print("UnifiedTrader_EA: InpEnableTelegramSignals is true but InpBotToken is empty. Create a "
               "bot with @BotFather, add it as an admin of the signal channel, and paste the token "
               "into InpBotToken.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      if(InpChannelId1 == 0 && InpChannelId2 == 0)
         Print("UnifiedTrader_EA: WARNING - InpChannelId1 and InpChannelId2 are both 0, so signals from "
               "ANY chat this bot can see will be copied. Watch the log for 'message from chat <id>', "
               "set InpChannelId1 (and InpChannelId2 for a second channel), and restart.");
      else if(InpChannelId1 != 0 && InpChannelId2 != 0 && InpChannelId1 == InpChannelId2)
         Print("UnifiedTrader_EA: WARNING - InpChannelId1 and InpChannelId2 are the same chat id; the "
               "second slot is redundant.");
      Print("UnifiedTrader_EA: Telegram signal execution is ENABLED WITH NO SMC VALIDATION and NO use "
            "of the message's own stop-loss - see the file header. This is materially higher-risk than "
            "TelegramSMC_Copier.mq5's default configuration.");
   }
   if(InpEnableClaudeManagement)
      PrintFormat("UnifiedTrader_EA: managing exits for magic=%I64d (must match python/config.py's "
                  "AdvisorConfig.magic).", InpClaudeMagicNumber);
   if(InpDryRun)
      Print("UnifiedTrader_EA: DRY-RUN mode - no real orders will be sent or positions modified. Set "
            "InpDryRun=false only after checking the log against every message/position it handles.");
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      Print("UnifiedTrader_EA: running in the Strategy Tester - there is no historical Telegram feed, "
            "so polling is disabled regardless of InpEnableTelegramSignals. This EA can only be "
            "meaningfully evaluated live/demo.");

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stopsLevelPrice = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(tickValue > 0.0 && tickSize > 0.0)
   {
      // Illustrative only, at InpFixedLot - the real per-tick check always
      // recomputes using each position's own volume (see DollarsToPrice).
      double sampleTrailDist = InpTrailDollars * tickSize / (tickValue * InpFixedLot);
      if(sampleTrailDist < stopsLevelPrice)
         PrintFormat("UnifiedTrader_EA: WARNING - at %.2f lots, InpTrailDollars=%.2f converts to a "
                     "%.5f price distance, tighter than this symbol's broker minimum stop distance "
                     "(%.5f). Once locked, the trailing stop may never be able to move for that "
                     "position size - it will sit at the InpTp1Dollars lock level instead, which is "
                     "still a valid, protected exit, just not a trailing one.",
                     InpFixedLot, InpTrailDollars, sampleTrailDist, stopsLevelPrice);
   }

   double gv;
   g_lastUpdateId = GlobalVariableGet(GV_LAST_UPDATE_ID, gv) ? (long)gv : 0;

   trade.SetDeviationInPoints(30);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_currentDay  = DateToDay(TimeCurrent());
   g_tradesToday = 0;

   if(InpEnableTelegramSignals)
      EventSetTimer(MathMax(1, InpPollSeconds));

   PrintFormat("UnifiedTrader_EA: ready. symbol=%s lot=%.2f max_per_direction=%d (shared) "
               "telegram=%s (magic=%I64d) claude=%s (magic=%I64d) sl=$%.2f tp1=$%.2f trail=$%.2f dryrun=%s",
               _Symbol, InpFixedLot, InpMaxPositionsPerDirection,
               InpEnableTelegramSignals ? "ON" : "off", InpTelegramMagicNumber,
               InpEnableClaudeManagement ? "ON" : "off", InpClaudeMagicNumber,
               InpSlDollars, InpTp1Dollars, InpTrailDollars, InpDryRun ? "true" : "false");

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   GlobalVariableSet(GV_LAST_UPDATE_ID, (double)g_lastUpdateId);
   Comment("");
}

//+------------------------------------------------------------------+
//| Small helpers                                                     |
//+------------------------------------------------------------------+
double PipSize() { return(SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 10.0); }

bool IsAllowedChat(long chatId)
{
   if(InpChannelId1 == 0 && InpChannelId2 == 0) return(true);
   if(InpChannelId1 != 0 && chatId == InpChannelId1) return(true);
   if(InpChannelId2 != 0 && chatId == InpChannelId2) return(true);
   return(false);
}

datetime DateToDay(datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   s.hour = 0; s.min = 0; s.sec = 0;
   return(StructToTime(s));
}

void UpdateDailyTracking()
{
   datetime today = DateToDay(TimeCurrent());
   if(today != g_currentDay)
   {
      g_currentDay  = today;
      g_tradesToday = 0;
      Print("UnifiedTrader_EA: new day - Telegram trade counter reset.");
   }
}

bool IsDigitCh(ushort ch)  { return(ch >= '0' && ch <= '9'); }
bool IsLetterCh(ushort ch) { return((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z')); }
bool IsWordCh(ushort ch)   { return(IsDigitCh(ch) || IsLetterCh(ch)); }

//+------------------------------------------------------------------+
//| Case-sensitive whole-word search (text is expected pre-uppercased)|
//+------------------------------------------------------------------+
int FindWholeWord(const string &text, const string &word, int startPos = 0)
{
   int wlen = StringLen(word);
   int tlen = StringLen(text);
   int pos  = startPos;
   while(pos <= tlen - wlen)
   {
      int idx = StringFind(text, word, pos);
      if(idx < 0) return(-1);
      bool leftOk  = (idx == 0) || !IsWordCh(StringGetCharacter(text, idx - 1));
      int  rightAt = idx + wlen;
      bool rightOk = (rightAt >= tlen) || !IsWordCh(StringGetCharacter(text, rightAt));
      if(leftOk && rightOk) return(idx);
      pos = idx + 1;
   }
   return(-1);
}

//+------------------------------------------------------------------+
//| "TP", "TP1", "TP2", ... (parsed for the log row only)             |
//+------------------------------------------------------------------+
int FindTpLabel(const string &text, int fromPos, int &labelEnd)
{
   int tlen = StringLen(text);
   int pos  = MathMax(0, fromPos);
   while(pos < tlen)
   {
      int idx = StringFind(text, "TP", pos);
      if(idx < 0) return(-1);
      bool leftOk = (idx == 0) || !IsLetterCh(StringGetCharacter(text, idx - 1));
      if(leftOk)
      {
         int j = idx + 2;
         int digits = 0;
         while(j < tlen && IsDigitCh(StringGetCharacter(text, j)) && digits < 2) { j++; digits++; }
         labelEnd = j;
         return(idx);
      }
      pos = idx + 1;
   }
   return(-1);
}

//+------------------------------------------------------------------+
//| "STOPLOSS" / "STOP LOSS" / "STOP-LOSS" / "S/L" / whole-word "SL"  |
//| (parsed for the log row only - never used to size an order)       |
//+------------------------------------------------------------------+
int FindSlLabel(const string &text, int fromPos, int &labelEnd)
{
   int idx = StringFind(text, "STOPLOSS", fromPos);
   if(idx >= 0) { labelEnd = idx + 8; return(idx); }
   idx = StringFind(text, "STOP LOSS", fromPos);
   if(idx >= 0) { labelEnd = idx + 9; return(idx); }
   idx = StringFind(text, "STOP-LOSS", fromPos);
   if(idx >= 0) { labelEnd = idx + 9; return(idx); }
   idx = StringFind(text, "S/L", fromPos);
   if(idx >= 0) { labelEnd = idx + 3; return(idx); }
   idx = FindWholeWord(text, "SL", fromPos);
   if(idx >= 0) { labelEnd = idx + 2; return(idx); }
   return(-1);
}

//+------------------------------------------------------------------+
//| Reads the first numeric token at/after fromPos, never past        |
//| limitPos, skipping up to maxSkip non-digit characters to find it. |
//+------------------------------------------------------------------+
bool ExtractNumberAt(const string &text, int fromPos, int limitPos, int maxSkip,
                      double &value, int &endPos)
{
   int tlen = MathMin(limitPos, StringLen(text));
   int i = MathMax(0, fromPos);
   int skipped = 0;
   while(i < tlen && skipped <= maxSkip)
   {
      if(IsDigitCh(StringGetCharacter(text, i))) break;
      i++; skipped++;
   }
   if(i >= tlen || !IsDigitCh(StringGetCharacter(text, i))) return(false);

   int start = i;
   bool sawDot = false;
   while(i < tlen)
   {
      ushort ch = StringGetCharacter(text, i);
      if(IsDigitCh(ch)) { i++; continue; }
      if(ch == '.' && !sawDot) { sawDot = true; i++; continue; }
      break;
   }
   string token = StringSubstr(text, start, i - start);
   if(StringLen(token) < 2) return(false);
   value  = StringToDouble(token);
   endPos = i;
   return(true);
}

bool MentionsGold(const string &upperText)
{
   string aliases[] = {"XAUUSD", "XAU/USD", "XAU-USD", "XAUUSDT", "GOLD"};
   for(int i = 0; i < ArraySize(aliases); i++)
      if(FindWholeWord(upperText, aliases[i]) >= 0) return(true);
   return(false);
}

//+------------------------------------------------------------------+
//| Best-effort parse - identical shape to TelegramSMC_Copier.mq5's   |
//| parser, kept for logging fidelity even though hasSl/sl/tps are    |
//| never used to size or place an order in THIS EA - see file header.|
//+------------------------------------------------------------------+
void ParseSignalText(const string &rawText, SignalMsg &msg)
{
   msg.action = ACTION_UNKNOWN; msg.direction = DIR_NONE; msg.symbolOk = false;
   msg.hasRange = false; msg.entryA = 0.0; msg.entryB = 0.0;
   msg.hasSl = false; msg.sl = 0.0; msg.tpCount = 0;

   string upper = rawText;
   StringToUpper(upper);
   int tlen = StringLen(upper);
   msg.symbolOk = MentionsGold(upper);

   if(FindWholeWord(upper, "CANCEL") >= 0) { msg.action = ACTION_CANCEL; return; }

   int buyPos  = FindWholeWord(upper, "BUY");  if(buyPos  < 0) buyPos  = FindWholeWord(upper, "LONG");
   int sellPos = FindWholeWord(upper, "SELL"); if(sellPos < 0) sellPos = FindWholeWord(upper, "SHORT");
   int msgDir = DIR_NONE;
   if(buyPos >= 0 || sellPos >= 0)
      msgDir = (buyPos >= 0 && (sellPos < 0 || buyPos < sellPos)) ? DIR_BUY : DIR_SELL;

   int closePos = FindWholeWord(upper, "CLOSE"); if(closePos < 0) closePos = FindWholeWord(upper, "EXIT");
   if(closePos >= 0 && msgDir == DIR_NONE) { msg.action = ACTION_CLOSE; return; }

   if(msgDir == DIR_NONE) { msg.action = ACTION_UNKNOWN; return; }

   msg.direction  = msgDir;
   int dirPos     = (msgDir == DIR_BUY) ? buyPos : sellPos;

   int tpLabelEnd0;
   int tpPos0        = FindTpLabel(upper, dirPos, tpLabelEnd0);
   int slLabelEndDir;
   int slPosFromDir  = FindSlLabel(upper, dirPos, slLabelEndDir);

   int cutoff = tlen;
   if(slPosFromDir >= 0) cutoff = MathMin(cutoff, slPosFromDir);
   if(tpPos0 >= 0)       cutoff = MathMin(cutoff, tpPos0);

   double a; int endPos;
   if(ExtractNumberAt(upper, dirPos + 3, cutoff, 30, a, endPos))
   {
      msg.entryA = a;
      double b2; int endPos2;
      if(ExtractNumberAt(upper, endPos, cutoff, 8, b2, endPos2))
      {
         msg.entryB   = b2;
         msg.hasRange = true;
      }
   }

   if(slPosFromDir >= 0)
   {
      double slVal; int slEnd;
      if(ExtractNumberAt(upper, slLabelEndDir, tlen, 10, slVal, slEnd))
      {
         msg.sl    = slVal;
         msg.hasSl = true;
      }
   }

   int scanPos = dirPos;
   int guard   = 0;
   while(msg.tpCount < 6 && guard < 50)
   {
      guard++;
      int labelEnd2;
      int found = FindTpLabel(upper, scanPos, labelEnd2);
      if(found < 0) break;
      double tpVal; int tpEnd;
      if(ExtractNumberAt(upper, labelEnd2, tlen, 6, tpVal, tpEnd))
      {
         msg.tps[msg.tpCount] = tpVal;
         msg.tpCount++;
         scanPos = tpEnd;
      }
      else
         scanPos = labelEnd2;
   }

   msg.action = ACTION_OPEN;
}

//+------------------------------------------------------------------+
//| dollars -> price distance for THIS symbol at the given volume,    |
//| via the broker's own tick value/size - see file header.           |
//+------------------------------------------------------------------+
double DollarsToPrice(double dollars, double volume)
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || volume <= 0.0)
      return(0.0);
   return(dollars * tickSize / (tickValue * volume));
}

//+------------------------------------------------------------------+
//| Open positions on this symbol/direction across BOTH magic         |
//| numbers together - the shared cap - regardless of which mode(s)   |
//| are currently enabled (see file header's "SHARED POSITION CAP").  |
//+------------------------------------------------------------------+
int CountSameDirection(int direction)
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      if(magic != InpTelegramMagicNumber && magic != InpClaudeMagicNumber) continue;
      long type = PositionGetInteger(POSITION_TYPE);
      int posDir = (type == POSITION_TYPE_BUY) ? DIR_BUY : DIR_SELL;
      if(posDir == direction) count++;
   }
   return(count);
}

//+------------------------------------------------------------------+
//| Cancel a Telegram-sourced pending order that has sat unfilled too |
//| long. Only ever touches InpTelegramMagicNumber.                    |
//+------------------------------------------------------------------+
void ExpirePendingOrders()
{
   if(InpPendingExpiryMin <= 0) return;
   datetime cutoff = TimeCurrent() - InpPendingExpiryMin * 60;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) != InpTelegramMagicNumber) continue;

      datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(setup > 0 && setup < cutoff)
      {
         PrintFormat("UnifiedTrader_EA: pending order %I64u unfilled for over %d min - cancelling.",
                     ticket, InpPendingExpiryMin);
         if(!InpDryRun) trade.OrderDelete(ticket);
         else PrintFormat("UnifiedTrader_EA: [DRY-RUN] would cancel pending order %I64u", ticket);
      }
   }
}

//+------------------------------------------------------------------+
//| CLOSE/CANCEL act only on this EA's own Telegram-sourced positions |
//| and pending orders - never on InpClaudeMagicNumber ones, which    |
//| are Python's to manage (see file header).                          |
//+------------------------------------------------------------------+
void CloseAllMine()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != InpTelegramMagicNumber) continue;
      if(InpDryRun) PrintFormat("UnifiedTrader_EA: [DRY-RUN] would close ticket %I64u", ticket);
      else trade.PositionClose(ticket);
   }
}

void CancelAllPendingMine()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) != InpTelegramMagicNumber) continue;
      if(InpDryRun) PrintFormat("UnifiedTrader_EA: [DRY-RUN] would cancel pending order %I64u", ticket);
      else trade.OrderDelete(ticket);
   }
}

//+------------------------------------------------------------------+
//| Places a Telegram-sourced order: LIMIT if price hasn't reached    |
//| the zone, MARKET if already inside it, skipped if already through |
//| it (identical zone logic to TelegramSMC_Copier.mq5's). SL is the  |
//| FIXED InpSlDollars distance, never the message's own SL. No       |
//| broker TP (tp=0.0, sl_to_tp1 exit design - see file header).      |
//+------------------------------------------------------------------+
bool PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                       string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode)
{
   outOrderType  = "";
   outOrderPrice = 0.0;
   outTicket     = 0;
   outRetcode    = 0;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("UnifiedTrader_EA: no tick available, cannot place order.");
      outOrderType = "NO_TICK";
      return(false);
   }

   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double buffer    = 3.0 * point;
   double zoneEntry = isBuy ? upperBound : lowerBound;
   string comment   = TSMC_SIGNAL_SOURCE;

   double orderPrice;
   bool   isPending;

   if(isBuy)
   {
      if(tick.ask > zoneEntry + buffer)      { orderPrice = zoneEntry; isPending = true; }
      else if(tick.ask < lowerBound - buffer)
      {
         PrintFormat("UnifiedTrader_EA: BUY zone %.2f-%.2f already breached (ask=%.2f) - stale, skipping.",
                     lowerBound, upperBound, tick.ask);
         outOrderType = "STALE_SKIPPED";
         return(false);
      }
      else { orderPrice = tick.ask; isPending = false; }
   }
   else
   {
      if(tick.bid < zoneEntry - buffer)      { orderPrice = zoneEntry; isPending = true; }
      else if(tick.bid > upperBound + buffer)
      {
         PrintFormat("UnifiedTrader_EA: SELL zone %.2f-%.2f already breached (bid=%.2f) - stale, skipping.",
                     lowerBound, upperBound, tick.bid);
         outOrderType = "STALE_SKIPPED";
         return(false);
      }
      else { orderPrice = tick.bid; isPending = false; }
   }

   orderPrice = NormalizeDouble(orderPrice, digits);
   double slDist = DollarsToPrice(InpSlDollars, InpFixedLot);
   if(slDist <= 0.0)
   {
      // DollarsToPrice() returns 0.0 if the broker isn't fully quoting tick
      // value/size yet (startup/reconnect hiccup) - sending an order with
      // sl==orderPrice in that case would be an instant stop-out (or an
      // outright broker rejection for a zero-distance stop), defeating the
      // fixed-risk design this whole EA depends on. Refuse instead, exactly
      // like ManagePositionExit() already refuses to touch a position under
      // the same condition.
      Print("UnifiedTrader_EA: DollarsToPrice() returned 0 (tick value/size not available yet) - "
            "refusing to place an order with an undefined stop-loss distance.");
      outOrderType = "NO_TICK_VALUE";
      return(false);
   }
   double sl = isBuy ? orderPrice - slDist : orderPrice + slDist;
   sl = NormalizeDouble(sl, digits);
   double tp = 0.0;   // no broker TP - see file header's shared exit design

   outOrderType  = isPending ? "LIMIT" : "MARKET";
   outOrderPrice = orderPrice;

   if(InpDryRun)
   {
      PrintFormat("UnifiedTrader_EA: [DRY-RUN] would place %s %s %.2f lots @ %.2f sl=%.2f "
                  "(no broker TP - locks at $%.2f via SL)",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", InpFixedLot, orderPrice, sl,
                  InpTp1Dollars);
      return(true);
   }

   trade.SetExpertMagicNumber(InpTelegramMagicNumber);
   bool ok;
   if(isPending)
      ok = isBuy ? trade.BuyLimit(InpFixedLot, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
                 : trade.SellLimit(InpFixedLot, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else
      ok = isBuy ? trade.Buy(InpFixedLot, _Symbol, orderPrice, sl, tp, comment)
                 : trade.Sell(InpFixedLot, _Symbol, orderPrice, sl, tp, comment);

   outRetcode = (int)trade.ResultRetcode();
   outTicket  = (long)trade.ResultOrder();

   if(ok)
   {
      g_tradesToday++;
      PrintFormat("UnifiedTrader_EA: %s %s placed - %.2f lots @ %.2f sl=%.2f ticket=%I64u",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", InpFixedLot, orderPrice, sl,
                  outTicket);
   }
   else
      PrintFormat("UnifiedTrader_EA: order failed. retcode=%d desc=%s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());

   return(true);
}

//+------------------------------------------------------------------+
//| Lock-then-trail exit for one position - reused verbatim from      |
//| ../../ClaudeSMC_Trader/MQL5/Experts/ClaudeSMC_TradeManager.mq5's  |
//| ManagePosition(), generalized to take the expected magic number   |
//| as a parameter so the identical logic serves both sources - see   |
//| file header's "SHARED EXIT DESIGN".                                |
//+------------------------------------------------------------------+
void ManagePositionExit(ulong ticket, long magic)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if((long)PositionGetInteger(POSITION_MAGIC) != magic)
      return;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      return;

   double volume = PositionGetDouble(POSITION_VOLUME);
   double tp1Dist = DollarsToPrice(InpTp1Dollars, volume);
   double trailDist = DollarsToPrice(InpTrailDollars, volume);
   if(tp1Dist <= 0.0 || trailDist <= 0.0)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minStopDist = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   long type = PositionGetInteger(POSITION_TYPE);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentSl = PositionGetDouble(POSITION_SL);
   double currentTp = PositionGetDouble(POSITION_TP);

   bool changeSl = false;
   double newSl = currentSl;

   if(type == POSITION_TYPE_BUY)
   {
      double lockLevel = NormalizeDouble(openPrice + tp1Dist, digits);
      bool armed = (currentSl > 0.0 && currentSl >= lockLevel - point);
      if(!armed)
      {
         double profit = tick.bid - openPrice;
         if(profit >= tp1Dist && (tick.bid - lockLevel) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.bid - trailDist, digits);
         if(candidate > currentSl && (tick.bid - candidate) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }
   else if(type == POSITION_TYPE_SELL)
   {
      double lockLevel = NormalizeDouble(openPrice - tp1Dist, digits);
      bool armed = (currentSl > 0.0 && currentSl <= lockLevel + point);
      if(!armed)
      {
         double profit = openPrice - tick.ask;
         if(profit >= tp1Dist && (lockLevel - tick.ask) >= minStopDist)
         {
            newSl = lockLevel;
            changeSl = true;
         }
      }
      else
      {
         double candidate = NormalizeDouble(tick.ask + trailDist, digits);
         if(candidate < currentSl && (candidate - tick.ask) >= minStopDist)
         {
            newSl = candidate;
            changeSl = true;
         }
      }
   }
   else
   {
      return;
   }

   // No managed position should ever carry a broker take-profit under this
   // exit design - clear any leftover unconditionally, even on a tick where
   // the SL itself isn't changing yet (see ClaudeSMC_TradeManager.mq5's own
   // header for the race condition this avoids).
   if(changeSl || currentTp != 0.0)
   {
      double slToSend = changeSl ? newSl : currentSl;
      if(InpDryRun)
         PrintFormat("UnifiedTrader_EA: [DRY-RUN] would modify %s ticket %I64u (magic=%I64d) sl %.2f -> %.2f%s",
                     type == POSITION_TYPE_BUY ? "BUY" : "SELL", ticket, magic, currentSl, slToSend,
                     currentTp != 0.0 ? " (clearing stale broker TP)" : "");
      else
         trade.PositionModify(ticket, slToSend, 0.0);
   }
}

//+------------------------------------------------------------------+
//| Manages every open position on this symbol under EITHER configured|
//| magic number - one pass over PositionsTotal(). Deliberately NOT   |
//| gated on InpEnableTelegramSignals/InpEnableClaudeManagement: those |
//| flags control whether NEW signals/entries are taken from a source, |
//| never whether a position already open under that source's magic   |
//| number keeps being protected. Gating exit management on the same   |
//| flag that gates new entries would orphan every already-open        |
//| position the instant an operator flips a source off mid-trade -    |
//| neither TelegramSMC_Copier.mq5 nor ClaudeSMC_TradeManager.mq5 has   |
//| an "off" switch that can abandon a live position's stop-loss like  |
//| that, and this EA shouldn't either.                                |
//+------------------------------------------------------------------+
void ManageAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long magic = (long)PositionGetInteger(POSITION_MAGIC);
      if(magic == InpTelegramMagicNumber)
         ManagePositionExit(ticket, InpTelegramMagicNumber);
      else if(magic == InpClaudeMagicNumber)
         ManagePositionExit(ticket, InpClaudeMagicNumber);
   }
}

string DirToStr(int dir)
{
   if(dir == DIR_BUY)  return("BUY");
   if(dir == DIR_SELL) return("SELL");
   return("");
}

//+------------------------------------------------------------------+
//| Appends one row to TelegramSMC_Signals.csv per Telegram message   |
//| evaluated - same file/header as TelegramSMC_Copier.mq5, so the    |
//| ASPX dashboard reads either EA's output without changes. SMC      |
//| columns are always "not applicable" here - this EA never runs an  |
//| SMC check (see file header).                                       |
//+------------------------------------------------------------------+
void LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                   double entryLow, double entryHigh, const string &tpsJoined,
                   bool sanityPass, const string &sanityReason, bool accepted,
                   const string &orderType, double orderPrice, double lots, bool dryRun,
                   long orderTicket, int retcode, const string &rawText)
{
   int handle = TsmcOpenCsvForAppend(TSMC_SIGNALS_FILE, TSMC_SIGNALS_HEADER, false);
   if(handle == INVALID_HANDLE) return;

   string ts  = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
   string line = ts + "," +
                 IntegerToString(chatId) + "," +
                 TsmcCsvField(action) + "," +
                 TsmcCsvField(direction) + "," +
                 (symbolOk ? "1" : "0") + "," +
                 DoubleToString(entryLow, 2) + "," +
                 DoubleToString(entryHigh, 2) + "," +
                 DoubleToString(0.0, 2) + "," +                        // sl - not applicable, see header
                 TsmcCsvField(tpsJoined) + "," +
                 "0" + "," +                                           // smc_used - never true here
                 "0" + "," +                                           // smc_pass - not applicable
                 TsmcCsvField("not applicable - unvalidated execution mode") + "," +
                 (sanityPass ? "1" : "0") + "," +
                 TsmcCsvField(sanityReason) + "," +
                 (accepted ? "1" : "0") + "," +
                 TsmcCsvField(orderType) + "," +
                 DoubleToString(orderPrice, 2) + "," +
                 DoubleToString(lots, 2) + "," +
                 (dryRun ? "1" : "0") + "," +
                 IntegerToString(orderTicket) + "," +
                 IntegerToString(retcode) + "," +
                 TsmcCsvField(rawText) + "," +
                 TsmcCsvField(TSMC_SIGNAL_SOURCE);

   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Dispatch a parsed signal. OPEN signals: allow-list, staleness      |
//| (checked in TelegramPoll), symbol, direction, daily cap, the       |
//| SHARED position cap, entry price, and price-deviation-from-zone    |
//| - then straight to PlaceCopiedOrder. NO SMC check, NO message-SL   |
//| check (see file header for why). CLOSE/CANCEL still supported;     |
//| a breakeven ("move SL to breakeven") message is deliberately NOT   |
//| recognized here - see file header's "OUT OF SCOPE".                |
//+------------------------------------------------------------------+
void ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText)
{
   if(!IsAllowedChat(chatId))
   {
      PrintFormat("UnifiedTrader_EA: ignoring message from chat %I64d (allowed: %I64d, %I64d)",
                  chatId, InpChannelId1, InpChannelId2);
      return;
   }

   string dirStr = DirToStr(msg.direction);

   if(msg.action == ACTION_CLOSE)
   {
      Print("UnifiedTrader_EA: CLOSE signal - closing Telegram-sourced positions and pending orders.");
      CloseAllMine();
      CancelAllPendingMine();
      LogSignalRow(chatId, "CLOSE", "", msg.symbolOk, 0, 0, "", true, "", true, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.action == ACTION_CANCEL)
   {
      Print("UnifiedTrader_EA: CANCEL signal - cancelling Telegram-sourced pending orders.");
      CancelAllPendingMine();
      LogSignalRow(chatId, "CANCEL", "", msg.symbolOk, 0, 0, "", true, "", true, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.action != ACTION_OPEN)
   {
      Print("UnifiedTrader_EA: message did not parse as an actionable signal - ignoring.");
      LogSignalRow(chatId, "UNKNOWN", "", msg.symbolOk, 0, 0, "", true, "", false, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }

   string tpList = "";
   for(int i = 0; i < msg.tpCount; i++)
      tpList += (i > 0 ? "|" : "") + DoubleToString(msg.tps[i], 2);

   if(!msg.symbolOk)
   {
      Print("UnifiedTrader_EA: OPEN signal does not mention XAUUSD/GOLD - ignoring.");
      LogSignalRow(chatId, "OPEN", dirStr, false, msg.entryA, msg.entryB, tpList,
                   true, "no XAUUSD/GOLD mention", false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.direction != DIR_BUY && msg.direction != DIR_SELL)
   {
      Print("UnifiedTrader_EA: OPEN signal has no clear BUY/SELL direction - ignoring.");
      LogSignalRow(chatId, "OPEN", "", msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, "no BUY/SELL direction", false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   UpdateDailyTracking();
   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay)
   {
      string r = StringFormat("max Telegram trades/day reached (%d)", InpMaxTradesPerDay);
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   int sameDir = CountSameDirection(msg.direction);
   if(sameDir >= InpMaxPositionsPerDirection)
   {
      string r = StringFormat("shared %s position cap reached (%d/%d across both magics)",
                               dirStr, sameDir, InpMaxPositionsPerDirection);
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   bool   isBuy = (msg.direction == DIR_BUY);
   double lowerBound, upperBound;
   if(msg.hasRange) { lowerBound = MathMin(msg.entryA, msg.entryB); upperBound = MathMax(msg.entryA, msg.entryB); }
   else              { lowerBound = msg.entryA; upperBound = msg.entryA; }

   if(lowerBound <= 0.0)
   {
      Print("UnifiedTrader_EA: OPEN signal has no usable entry price - ignoring.");
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, "no usable entry price", false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   double entryPrice = isBuy ? upperBound : lowerBound;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("UnifiedTrader_EA: no tick, cannot evaluate signal.");
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                   true, "no tick available", false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   double mid     = (tick.ask + tick.bid) / 2.0;
   double devPips = MathAbs(mid - entryPrice) / PipSize();
   if(devPips > InpMaxEntryDeviationPips)
   {
      string r = StringFormat("price %.2f is %.1f pips from the zone (max %.1f)",
                               mid, devPips, InpMaxEntryDeviationPips);
      PrintFormat("UnifiedTrader_EA: %s - skipping.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                   true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   PrintFormat("UnifiedTrader_EA: executing %s XAUUSD %.2f-%.2f from chat %I64d - NO SMC validation, "
               "fixed $%.2f SL/$%.2f lock/$%.2f trail (signal's own SL/TPs logged only, not used: "
               "sl=%s tps=%s)",
               isBuy ? "BUY" : "SELL", lowerBound, upperBound, chatId, InpSlDollars, InpTp1Dollars,
               InpTrailDollars, msg.hasSl ? DoubleToString(msg.sl, 2) : "none",
               msg.tpCount > 0 ? tpList : "none");

   string outOrderType  = "";
   double outOrderPrice = 0.0;
   long   outTicket     = 0;
   int    outRetcode    = 0;
   bool   placed = PlaceCopiedOrder(isBuy, lowerBound, upperBound,
                                     outOrderType, outOrderPrice, outTicket, outRetcode);

   LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                true, "", placed, outOrderType, outOrderPrice, InpFixedLot, InpDryRun,
                outTicket, outRetcode, rawText);
}

//+------------------------------------------------------------------+
//| Telegram Bot API: getUpdates (short poll, timeout=0) + a minimal  |
//| field scraper - identical mechanism to TelegramSMC_Copier.mq5.     |
//+------------------------------------------------------------------+
bool TelegramGetUpdates(string &jsonOut)
{
   string url = "https://api.telegram.org/bot" + InpBotToken + "/getUpdates?timeout=0&offset=" +
                IntegerToString(g_lastUpdateId + 1) + "&limit=20";
   char   post[];
   char   result[];
   string resultHeaders;
   ArrayResize(post, 0);

   ResetLastError();
   int rc = WebRequest("GET", url, "", InpHttpTimeoutMs, post, result, resultHeaders);
   if(rc == -1)
   {
      PrintFormat("UnifiedTrader_EA: WebRequest failed, error=%d. If this is a permissions error, add "
                  "https://api.telegram.org to Tools > Options > Expert Advisors > 'Allow WebRequest "
                  "for listed URL'.", GetLastError());
      return(false);
   }
   if(rc != 200)
   {
      PrintFormat("UnifiedTrader_EA: Telegram HTTP status %d", rc);
      return(false);
   }
   jsonOut = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return(true);
}

//+------------------------------------------------------------------+
//| Same \uXXXX-escape handling as TelegramSMC_Copier.mq5 - see its    |
//| own comment for why this matters for whole-word boundary checks.  |
//+------------------------------------------------------------------+
void StripUnicodeEscapes(string &s)
{
   int pos = 0;
   while(true)
   {
      int idx = StringFind(s, "\\u", pos);
      if(idx < 0) break;
      int slen = StringLen(s);
      bool allHex = (idx + 6 <= slen);
      if(allHex)
      {
         for(int k = 2; k <= 5; k++)
         {
            ushort c = StringGetCharacter(s, idx + k);
            bool hex = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f');
            if(!hex) { allHex = false; break; }
         }
      }
      if(allHex)
      {
         string before = StringSubstr(s, 0, idx);
         string after  = StringSubstr(s, idx + 6);
         s = before + " " + after;
         pos = idx + 1;
      }
      else
         pos = idx + 2;
   }
}

//+------------------------------------------------------------------+
//| Pulls update_id / chat.id / date / text out of a getUpdates JSON  |
//| body - identical field scraper to TelegramSMC_Copier.mq5's.        |
//+------------------------------------------------------------------+
void ExtractUpdates(const string &json, TgUpdate &updates[])
{
   ArrayResize(updates, 0);
   int pos  = 0;
   int jlen = StringLen(json);

   while(true)
   {
      int idIdx = StringFind(json, "\"update_id\"", pos);
      if(idIdx < 0) break;
      int colon = StringFind(json, ":", idIdx);
      if(colon < 0) break;
      long updateId = StringToInteger(StringSubstr(json, colon + 1));

      int nextIdIdx = StringFind(json, "\"update_id\"", idIdx + 1);
      int segEnd    = (nextIdIdx < 0) ? jlen : nextIdIdx;
      string segment = StringSubstr(json, idIdx, segEnd - idIdx);

      TgUpdate u;
      u.update_id = updateId;
      u.isEdited  = (StringFind(segment, "\"edited_message\"") >= 0) ||
                    (StringFind(segment, "\"edited_channel_post\"") >= 0);
      u.chat_id   = 0;
      u.date      = 0;
      u.text      = "";

      int chatIdx = StringFind(segment, "\"chat\"");
      if(chatIdx >= 0)
      {
         int idInChat = StringFind(segment, "\"id\"", chatIdx);
         if(idInChat >= 0)
         {
            int c2 = StringFind(segment, ":", idInChat);
            if(c2 >= 0) u.chat_id = StringToInteger(StringSubstr(segment, c2 + 1));
         }
      }

      int dateIdx = StringFind(segment, "\"date\"");
      if(dateIdx >= 0)
      {
         int c2 = StringFind(segment, ":", dateIdx);
         if(c2 >= 0) u.date = StringToInteger(StringSubstr(segment, c2 + 1));
      }

      int textIdx = StringFind(segment, "\"text\"");
      if(textIdx >= 0)
      {
         int c2 = StringFind(segment, ":", textIdx);
         if(c2 >= 0)
         {
            int slen = StringLen(segment);
            int p = c2 + 1;
            while(p < slen && StringGetCharacter(segment, p) == ' ') p++;
            if(p < slen && StringGetCharacter(segment, p) == '"')
            {
               p++;
               int startStr = p;
               while(p < slen)
               {
                  ushort ch = StringGetCharacter(segment, p);
                  if(ch == '\\') { p += 2; continue; }
                  if(ch == '"') break;
                  p++;
               }
               string raw = StringSubstr(segment, startStr, p - startStr);
               StripUnicodeEscapes(raw);
               string placeholder = CharToString((uchar)1);
               StringReplace(raw, "\\\\", placeholder);
               StringReplace(raw, "\\n", " ");
               StringReplace(raw, "\\r", " ");
               StringReplace(raw, "\\t", " ");
               StringReplace(raw, "\\\"", "\"");
               StringReplace(raw, placeholder, "\\");
               u.text = raw;
            }
         }
      }

      int n = ArraySize(updates);
      ArrayResize(updates, n + 1);
      updates[n] = u;

      pos = segEnd;
   }
}

//+------------------------------------------------------------------+
//| One polling cycle: fetch, parse, act, advance the offset.         |
//+------------------------------------------------------------------+
void TelegramPoll()
{
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION)) return;

   string json;
   if(!TelegramGetUpdates(json)) return;

   if(StringFind(json, "\"ok\":false") >= 0)
   {
      PrintFormat("UnifiedTrader_EA: Telegram API returned an error: %s", json);
      return;
   }

   TgUpdate updates[];
   ExtractUpdates(json, updates);

   for(int i = 0; i < ArraySize(updates); i++)
   {
      if(updates[i].update_id > g_lastUpdateId) g_lastUpdateId = updates[i].update_id;
      if(updates[i].isEdited) continue;
      if(StringLen(updates[i].text) == 0) continue;

      long age = (long)TimeGMT() - updates[i].date;
      if(InpMaxSignalAgeSec > 0 && updates[i].date > 0 && age > InpMaxSignalAgeSec)
      {
         PrintFormat("UnifiedTrader_EA: message from chat %I64d is %ds old (> %ds) - stale, skipping.",
                     updates[i].chat_id, age, InpMaxSignalAgeSec);
         continue;
      }

      PrintFormat("UnifiedTrader_EA: message from chat %I64d: %s", updates[i].chat_id, updates[i].text);

      SignalMsg msg;
      ParseSignalText(updates[i].text, msg);
      ProcessSignal(msg, updates[i].chat_id, updates[i].text);
   }

   if(ArraySize(updates) > 0)
      GlobalVariableSet(GV_LAST_UPDATE_ID, (double)g_lastUpdateId);
}

//+------------------------------------------------------------------+
//| Expert tick function - manages whichever source(s) are enabled.   |
//| Entries come from Telegram via OnTimer, never from price ticks.   |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageAllPositions();
}

//+------------------------------------------------------------------+
//| Expert timer function - only set up when InpEnableTelegramSignals |
//| is true (see OnInit). Polls Telegram and does light upkeep.        |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDailyTracking();
   ExpirePendingOrders();
   TelegramPoll();
}
//+------------------------------------------------------------------+
