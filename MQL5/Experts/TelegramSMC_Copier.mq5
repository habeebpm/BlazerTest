//+------------------------------------------------------------------+
//|                                       TelegramSMC_Copier.mq5      |
//|                                                                    |
//| Copies XAUUSD trade calls posted in a Telegram channel/group into  |
//| MT5, with an independent Smart-Money-Concepts (SMC) price check    |
//| before anything is sent, and a fixed exit rule that ignores the    |
//| signal's own TP1/TP2/TP3 numbers.                                  |
//|                                                                    |
//| HOW IT TALKS TO TELEGRAM                                           |
//| ------------------------------------------------------------------ |
//| MQL5 cannot read a Telegram channel directly, so this EA polls the |
//| Telegram BOT API (https://api.telegram.org) with WebRequest - no   |
//| Python, no bridge file, nothing outside MT5. One-time setup:       |
//|   1. Talk to @BotFather in Telegram, /newbot, copy the token into  |
//|      InpBotToken.                                                  |
//|   2. Add that bot to the signal channel/group AS AN ADMIN (a bot   |
//|      only receives channel posts if it is an admin there).         |
//|   3. In MT5: Tools > Options > Expert Advisors > tick "Allow       |
//|      WebRequest for listed URL" and add                            |
//|      https://api.telegram.org - WebRequest is refused otherwise.   |
//|   4. Leave InpChannelId1/InpChannelId2 at 0 for the first run,      |
//|      attach the EA, and read the Experts log: every message the    |
//|      bot sees is logged with its chat id. Copy up to two of those   |
//|      ids into InpChannelId1/InpChannelId2 and restart - only        |
//|      signals from those chats will be copied.                      |
//| Runs with InpDryRun = true until you turn it off; nothing above    |
//| this line touches an order.                                        |
//|                                                                    |
//| WHAT A SIGNAL LOOKS LIKE                                            |
//| ------------------------------------------------------------------ |
//|   XAUUSD BUY 4342-4339        <- a price zone, either order         |
//|   TP1: 4349 / TP2 / TP3       <- logged, NOT used to manage exits  |
//|   StopLoss: 4335              <- used exactly as given              |
//| The parser also understands "Gold sell now 4387 - 4391", a single  |
//| price with no zone, CLOSE/EXIT, CANCEL, and "move SL to breakeven". |
//|                                                                    |
//| ENTRY: fixed InpFixedLot lots at the UPPER bound of the zone for a  |
//| BUY, the LOWER bound for a SELL (the edge of the zone price meets   |
//| first). Whether that becomes a BUY/SELL LIMIT or a market order is  |
//| decided from the live price, not the wording in the message:       |
//|   - price still on the far side of the zone -> a LIMIT order at    |
//|     that bound, waiting for the pullback;                          |
//|   - price already inside the zone           -> a market order now; |
//|   - price already through the WHOLE zone    -> skipped as stale.   |
//|                                                                    |
//| EXIT: ignores the signal's own TP1/TP2/TP3. InpTp1Points (default  |
//| 4.0) is a real, broker-side take-profit set the moment the trade    |
//| opens - it survives a disconnect. Once floating profit reaches      |
//| that many PRICE UNITS (4.0 = $4.00 on XAUUSD, not a broker "point"  |
//| of $0.01 and not a "pip" of $0.10) AND the trailing SL is actually  |
//| able to move there (see InpTrailPoints below), the fixed TP is      |
//| dropped in favour of an InpTrailPoints (default 3.0) trailing stop  |
//| for the rest of the move - it only ever tightens. If the broker's   |
//| own minimum stop distance is wider than InpTrailPoints, trailing    |
//| can never engage - OnInit warns about this - and the fixed TP is    |
//| correctly left in place rather than being dropped for nothing.      |
//|                                                                    |
//| SMC VALIDATION (InpUseSmcFilter, on by default)                    |
//| ------------------------------------------------------------------ |
//| Independent of whatever the message SAYS about market structure,   |
//| this checks the actual price history on InpSmcTF:                  |
//|  1. LIQUIDITY SWEEP - within the last InpSweepRecentBars closed     |
//|     bars, price must have pierced beyond the extreme of the prior   |
//|     InpSweepRefBars bars (below a prior low for a BUY, above a      |
//|     prior high for a SELL) and closed back on the right side of it  |
//|     - a stop-hunt-then-reclaim, not a clean breakout.               |
//|  2. PREMIUM/DISCOUNT - the entry must sit in the cheaper half of    |
//|     the combined swing range for a BUY, the richer half for a SELL. |
//|  3. The signal's own SL should sit beyond the swept extreme (the    |
//|     level that failed), not inside it.                              |
//| This is a deliberately simplified, fully computable proxy for SMC   |
//| entry logic (two rolling windows, not a full fractal/order-block    |
//| engine) - tune the window/pierce inputs, or turn InpUseSmcFilter    |
//| off, if it is too strict or too loose for the channel you follow.  |
//|                                                                    |
//| WHAT THIS EA DOES NOT DO                                            |
//| ------------------------------------------------------------------ |
//| It does not backtest (there is no historical Telegram feed - the   |
//| Strategy Tester has nothing to poll and this EA disables itself     |
//| there). It manages ONE position slot per fill; a signal's TP2/TP3   |
//| are logged, never split across partial closes. CLOSE/CANCEL act on |
//| every one of this EA's open positions/pending orders on the symbol |
//| - there is no per-setup ticket tracking, since the messages do not |
//| reference one.                                                      |
//|                                                                    |
//| IMPORTANT DISCLAIMER                                                |
//| ------------------------------------------------------------------ |
//| This EA executes real money orders from TEXT MESSAGES it does not   |
//| originate and cannot fully verify. The SMC filter and the sanity    |
//| checks (InpMinSlDistancePips, InpMaxSlDistancePips,                 |
//| InpMaxEntryDeviationPips) catch stale or obviously broken signals,  |
//| not bad calls. Demo-test with InpDryRun = true until you trust the  |
//| channel, the parser's log output for every message it sees, AND     |
//| this EA's behaviour, in that order.                                 |
//+------------------------------------------------------------------+
#property copyright "TelegramSMC_Copier"
#property link      ""
#property version   "1.00"
#property strict
#property description "Copies XAUUSD BUY/SELL zone signals from a Telegram channel (via the Bot API, no external bridge) into MT5, gated by an independent SMC liquidity-sweep/premium-discount check, with a fixed 4pt-arm/3pt-trail exit. Educational use - demo-test with InpDryRun=true before risking real capital."

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

#define GV_LAST_UPDATE_ID "TelegramSMC_Copier_LastUpdateId"

//================================= INPUTS ====================================

input group "=== Telegram Bot (see the file header for setup steps) ==="
input string  InpBotToken          = "";              // Bot token from @BotFather
input long    InpChannelId1        = 0;                // Only copy signals from this chat id (0 = slot unused)
input long    InpChannelId2        = 0;                // ...and this one (0 = slot unused; both 0 = ANY chat - unsafe, first-run only)
input int     InpPollSeconds       = 5;                // How often to poll Telegram for new messages
input int     InpHttpTimeoutMs     = 5000;             // WebRequest timeout (ms)
input int     InpMaxSignalAgeSec   = 180;              // Reject a signal older than this many seconds (0 = no limit)

input group "=== Safety ==="
input bool    InpDryRun            = true;             // Log what would happen; do not send real orders
input bool    InpTradeXAUUSDOnly   = true;              // Require chart symbol to contain "XAU"

input group "=== Execution ==="
input double  InpFixedLot          = 0.05;              // Fixed lot size for every copied signal
input ulong   InpMagicNumber       = 20260918;
input int     InpSlippagePoints    = 30;
input int     InpMaxOpenPositions  = 4;                 // Cap on this EA's simultaneous positions + pending orders
input int     InpMaxTradesPerDay   = 0;                 // 0 = unlimited
input int     InpPendingExpiryMin  = 240;               // Cancel an unfilled pending order after N minutes (0 = never)

input group "=== Signal Sanity (pips; 1 pip = 10 broker points, see PipSize()) ==="
input double  InpMinSlDistancePips    = 10.0;           // Reject a signal whose SL is tighter than this
input double  InpMaxSlDistancePips    = 250.0;          // Reject a signal whose SL is wider than this (fat-finger guard)
input double  InpMaxEntryDeviationPips= 200.0;          // Reject if current price is this far outside the signaled zone

input group "=== Take-profit / Trailing - PRICE UNITS, e.g. XAUUSD dollars ==="
input double  InpTp1Points         = 4.0;               // Broker-side TP1 AND the trailing "arm" threshold
input double  InpTrailPoints       = 3.0;               // Trailing distance kept behind price once armed

input group "=== SMC Validation ==="
input bool    InpUseSmcFilter          = true;          // Require the checks below before copying a signal
input ENUM_TIMEFRAMES InpSmcTF         = PERIOD_M15;    // Timeframe the swing/sweep structure is read from
input int     InpSweepRecentBars       = 20;            // Window (closed bars) searched for the sweep itself
input int     InpSweepRefBars          = 30;            // Earlier window establishing the level that gets swept
input double  InpSweepMinPiercePips    = 3.0;           // Minimum wick-through distance to count as a sweep
input bool    InpRequirePremiumDiscount= true;          // BUY must sit in the discount half, SELL in the premium half
input bool    InpRequireSlBeyondSweep  = true;          // Signal SL must sit beyond the swept extreme

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
   double sl;
   double tps[6];
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

// Forward declarations: several of these are called before their definitions
// below (e.g. OnInit() calls DateToDay()), so declare every signature here
// rather than relying on the order they happen to be defined in.
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
bool     ValidateSignalSanity(const SignalMsg &msg, bool isBuy, double lowerBound,
                               double upperBound, string &reason);
bool     SmcValidate(bool isBuy, double entryPrice, double signalSl, string &reason);
int      CountActiveSlots();
void     ExpirePendingOrders();
void     CloseAllMine();
void     CancelAllPendingMine();
void     BreakevenAllMine();
bool     PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound, double sl,
                           string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode);
void     ManageOpenPositions();
void     ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText);
bool     TelegramGetUpdates(string &jsonOut);
void     ExtractUpdates(const string &json, TgUpdate &updates[]);
void     TelegramPoll();
void     StripUnicodeEscapes(string &s);
string   DirToStr(int dir);
void     LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                       double entryLow, double entryHigh, double sl, const string &tpsJoined,
                       bool smcUsed, bool smcPass, const string &smcReason,
                       bool sanityPass, const string &sanityReason, bool accepted,
                       const string &orderType, double orderPrice, double lots, bool dryRun,
                       long orderTicket, int retcode, const string &rawText);

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTradeXAUUSDOnly && StringFind(_Symbol, "XAU") < 0)
   {
      Print("TelegramSMC_Copier: chart symbol '", _Symbol, "' does not contain 'XAU'. "
            "Attach to a Gold (XAUUSD) chart, or disable InpTradeXAUUSDOnly to override.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(StringLen(InpBotToken) == 0)
   {
      Print("TelegramSMC_Copier: InpBotToken is empty. Create a bot with @BotFather, add it as "
            "an admin of the signal channel, and paste the token into InpBotToken.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpChannelId1 == 0 && InpChannelId2 == 0)
      Print("TelegramSMC_Copier: WARNING - InpChannelId1 and InpChannelId2 are both 0, so signals "
            "from ANY chat this bot can see will be copied. Watch the log for 'message from chat "
            "<id>', set InpChannelId1 (and InpChannelId2 if you follow a second channel) to those "
            "values, and restart.");
   else if(InpChannelId1 != 0 && InpChannelId2 != 0 && InpChannelId1 == InpChannelId2)
      Print("TelegramSMC_Copier: WARNING - InpChannelId1 and InpChannelId2 are the same chat id; "
            "the second slot is redundant.");
   if(InpDryRun)
      Print("TelegramSMC_Copier: DRY-RUN mode - no real orders will be sent. Set InpDryRun=false "
            "only after checking the log against every message the channel posts.");
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      Print("TelegramSMC_Copier: running in the Strategy Tester - there is no historical Telegram "
            "feed, so polling is disabled. This EA can only be meaningfully evaluated live/demo.");

   double stopsLevelPrice = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(InpTrailPoints < stopsLevelPrice)
      PrintFormat("TelegramSMC_Copier: WARNING - InpTrailPoints (%.2f) is tighter than this symbol's "
                  "broker minimum stop distance (%.2f). The trailing stop will never be able to move, "
                  "so a position's take-profit is kept in place instead of being dropped for a trail "
                  "that can't engage (see ManageOpenPositions). Widen InpTrailPoints to at least "
                  "%.2f to actually enable trailing.", InpTrailPoints, stopsLevelPrice, stopsLevelPrice);

   double gv;
   g_lastUpdateId = GlobalVariableGet(GV_LAST_UPDATE_ID, gv) ? (long)gv : 0;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_currentDay  = DateToDay(TimeCurrent());
   g_tradesToday = 0;

   EventSetTimer(MathMax(1, InpPollSeconds));

   PrintFormat("TelegramSMC_Copier: ready. symbol=%s lot=%.2f magic=%I64u dryrun=%s "
               "smc_filter=%s tp1=%.2f trail=%.2f poll=%ds",
               _Symbol, InpFixedLot, InpMagicNumber, InpDryRun ? "true" : "false",
               InpUseSmcFilter ? "true" : "false", InpTp1Points, InpTrailPoints, InpPollSeconds);

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

//+------------------------------------------------------------------+
//| True if chatId is one of the (up to two) configured channels, or  |
//| if neither slot is configured (both 0 - accepts any chat, with    |
//| the OnInit warning above making that opt-in rather than silent).  |
//+------------------------------------------------------------------+
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
      Print("TelegramSMC_Copier: new day - trade counter reset.");
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
//| "TP", "TP1", "TP2", ... - a word-start "TP" with an optional 1-2  |
//| digit label number directly attached (no space).                  |
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
//| Reads the first numeric token (>= 2 digits, one optional decimal  |
//| point) found at or after fromPos, skipping up to maxSkip          |
//| non-digit characters to find its start, never reading at/past     |
//| limitPos.                                                          |
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

//+------------------------------------------------------------------+
//| Does the message mention gold at all?                             |
//+------------------------------------------------------------------+
bool MentionsGold(const string &upperText)
{
   string aliases[] = {"XAUUSD", "XAU/USD", "XAU-USD", "XAUUSDT", "GOLD"};
   for(int i = 0; i < ArraySize(aliases); i++)
      if(FindWholeWord(upperText, aliases[i]) >= 0) return(true);
   return(false);
}

//+------------------------------------------------------------------+
//| Best-effort parse of a raw Telegram message into a SignalMsg.     |
//| Deliberately permissive: it extracts what it can and leaves       |
//| msg.action=ACTION_UNKNOWN / hasSl=false / hasRange=false rather    |
//| than guessing. ProcessSignal() decides whether the result is safe |
//| to trade, not this function.                                      |
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

   // Deliberately NOT matching a bare whole-word "BE" here: it is too common
   // inside ordinary commentary ("this could be...") to safely use as a
   // trigger, and misfiring would silently drop a real BUY/SELL signal.
   bool hasBE = (StringFind(upper, "BREAK EVEN") >= 0) || (StringFind(upper, "BREAKEVEN") >= 0);
   int slLabelEnd;
   int slPosAny = FindSlLabel(upper, 0, slLabelEnd);

   // A breakeven mention only means "this whole message IS a breakeven
   // instruction" when there's no actual trade data alongside it - e.g.
   // "BUY GOLD 4342 SL 4335 TP 4349, move SL to breakeven after TP1" is an
   // open signal that happens to mention breakeven management, not a
   // standalone "move SL to breakeven" command, and must not have its
   // entry/SL/TP discarded.
   bool hasTradeData = false;
   if(msgDir != DIR_NONE)
   {
      if(slPosAny >= 0)
      {
         double tmpVal; int tmpEnd;
         if(ExtractNumberAt(upper, slLabelEnd, tlen, 10, tmpVal, tmpEnd)) hasTradeData = true;
      }
      if(!hasTradeData)
      {
         int dirPosTmp = (msgDir == DIR_BUY) ? buyPos : sellPos;
         double tmpVal2; int tmpEnd2;
         if(ExtractNumberAt(upper, dirPosTmp + 3, tlen, 30, tmpVal2, tmpEnd2)) hasTradeData = true;
      }
   }

   if(hasBE && slPosAny >= 0 && !(msgDir != DIR_NONE && hasTradeData))
   {
      msg.action    = ACTION_MODIFY_SL;
      msg.direction = msgDir;
      return;
   }

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
         scanPos = labelEnd2;   // e.g. "TP: open" - no number, keep scanning past it
   }

   msg.action = ACTION_OPEN;
}

//+------------------------------------------------------------------+
//| Basic sanity checks independent of SMC: SL present, on the        |
//| correct side, and a plausible distance.                           |
//+------------------------------------------------------------------+
bool ValidateSignalSanity(const SignalMsg &msg, bool isBuy, double lowerBound, double upperBound,
                           string &reason)
{
   if(!msg.hasSl) { reason = "no stop-loss found in the message"; return(false); }

   if(isBuy && msg.sl >= lowerBound)
   {
      reason = StringFormat("SL %.2f is not below the buy zone %.2f-%.2f", msg.sl, lowerBound, upperBound);
      return(false);
   }
   if(!isBuy && msg.sl <= upperBound)
   {
      reason = StringFormat("SL %.2f is not above the sell zone %.2f-%.2f", msg.sl, lowerBound, upperBound);
      return(false);
   }

   double entryRef = isBuy ? upperBound : lowerBound;
   double slDist    = MathAbs(entryRef - msg.sl);
   double slPips    = slDist / PipSize();

   if(slPips < InpMinSlDistancePips)
   {
      reason = StringFormat("SL distance %.1f pips is below the minimum %.1f", slPips, InpMinSlDistancePips);
      return(false);
   }
   if(slPips > InpMaxSlDistancePips)
   {
      reason = StringFormat("SL distance %.1f pips exceeds the maximum %.1f - looks like a fat-finger",
                             slPips, InpMaxSlDistancePips);
      return(false);
   }
   return(true);
}

//+------------------------------------------------------------------+
//| Independent SMC check: liquidity sweep + premium/discount, read   |
//| from live price history on InpSmcTF - see the file header.        |
//+------------------------------------------------------------------+
bool SmcValidate(bool isBuy, double entryPrice, double signalSl, string &reason)
{
   int total = InpSweepRecentBars + InpSweepRefBars;
   double highs[], lows[], closes[];
   ArraySetAsSeries(highs, true);
   ArraySetAsSeries(lows,  true);
   ArraySetAsSeries(closes, true);

   if(CopyHigh(_Symbol, InpSmcTF, 1, total, highs) < total ||
      CopyLow(_Symbol,  InpSmcTF, 1, total, lows)  < total ||
      CopyClose(_Symbol, InpSmcTF, 1, total, closes) < total)
   {
      reason = StringFormat("not enough %s history to validate SMC structure", EnumToString(InpSmcTF));
      return(false);
   }

   double pierce      = InpSweepMinPiercePips * PipSize();
   double lastClose    = closes[0];
   double rangeHigh    = highs[ArrayMaximum(highs, 0, total)];
   double rangeLow     = lows[ArrayMinimum(lows, 0, total)];
   double equilibrium  = (rangeHigh + rangeLow) / 2.0;

   if(isBuy)
   {
      double refLow   = lows[ArrayMinimum(lows, InpSweepRecentBars, InpSweepRefBars)];
      double sweepLow = lows[ArrayMinimum(lows, 0, InpSweepRecentBars)];
      bool   swept    = (sweepLow < refLow - pierce) && (lastClose > refLow);

      if(!swept)
      {
         reason = StringFormat("no recent bullish liquidity sweep (recent low %.2f vs prior level "
                                "%.2f, last close %.2f)", sweepLow, refLow, lastClose);
         return(false);
      }
      if(InpRequirePremiumDiscount && entryPrice > equilibrium)
      {
         reason = StringFormat("BUY entry %.2f sits in the premium half of %.2f-%.2f (equilibrium "
                                "%.2f) - not a discount entry", entryPrice, rangeLow, rangeHigh, equilibrium);
         return(false);
      }
      if(InpRequireSlBeyondSweep && signalSl >= sweepLow)
      {
         reason = StringFormat("signal SL %.2f does not sit beyond the swept low %.2f", signalSl, sweepLow);
         return(false);
      }
      reason = StringFormat("bullish sweep confirmed (swept %.2f below prior %.2f, reclaimed at "
                             "%.2f); entry %.2f is in the discount half of %.2f-%.2f",
                             sweepLow, refLow, lastClose, entryPrice, rangeLow, rangeHigh);
      return(true);
   }
   else
   {
      double refHigh   = highs[ArrayMaximum(highs, InpSweepRecentBars, InpSweepRefBars)];
      double sweepHigh = highs[ArrayMaximum(highs, 0, InpSweepRecentBars)];
      bool   swept     = (sweepHigh > refHigh + pierce) && (lastClose < refHigh);

      if(!swept)
      {
         reason = StringFormat("no recent bearish liquidity sweep (recent high %.2f vs prior level "
                                "%.2f, last close %.2f)", sweepHigh, refHigh, lastClose);
         return(false);
      }
      if(InpRequirePremiumDiscount && entryPrice < equilibrium)
      {
         reason = StringFormat("SELL entry %.2f sits in the discount half of %.2f-%.2f (equilibrium "
                                "%.2f) - not a premium entry", entryPrice, rangeLow, rangeHigh, equilibrium);
         return(false);
      }
      if(InpRequireSlBeyondSweep && signalSl <= sweepHigh)
      {
         reason = StringFormat("signal SL %.2f does not sit beyond the swept high %.2f", signalSl, sweepHigh);
         return(false);
      }
      reason = StringFormat("bearish sweep confirmed (swept %.2f above prior %.2f, reclaimed at "
                             "%.2f); entry %.2f is in the premium half of %.2f-%.2f",
                             sweepHigh, refHigh, lastClose, entryPrice, rangeLow, rangeHigh);
      return(true);
   }
}

//+------------------------------------------------------------------+
//| Count this EA's open positions AND pending orders on this symbol  |
//+------------------------------------------------------------------+
int CountActiveSlots()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      count++;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      count++;
   }
   return(count);
}

//+------------------------------------------------------------------+
//| Cancel a pending order that has sat unfilled too long - an SMC     |
//| zone goes stale faster than a fixed-distance order would.          |
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
      if((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;

      datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(setup > 0 && setup < cutoff)
      {
         PrintFormat("TelegramSMC_Copier: pending order %I64u unfilled for over %d min - cancelling.",
                     ticket, InpPendingExpiryMin);
         if(!InpDryRun) trade.OrderDelete(ticket);
         else PrintFormat("TelegramSMC_Copier: [DRY-RUN] would cancel pending order %I64u", ticket);
      }
   }
}

void CloseAllMine()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
      if(InpDryRun) PrintFormat("TelegramSMC_Copier: [DRY-RUN] would close ticket %I64u", ticket);
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
      if((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
      if(InpDryRun) PrintFormat("TelegramSMC_Copier: [DRY-RUN] would cancel pending order %I64u", ticket);
      else trade.OrderDelete(ticket);
   }
}

void BreakevenAllMine()
{
   double point         = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits        = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long   stopsLevelPts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDist   = stopsLevelPts * point;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentTp = PositionGetDouble(POSITION_TP);
      bool   isBuy     = (type == POSITION_TYPE_BUY);
      double current   = isBuy ? tick.bid : tick.ask;
      double distance  = isBuy ? (current - openPrice) : (openPrice - current);

      if(distance < minStopDist)
      {
         PrintFormat("TelegramSMC_Copier: breakeven skipped for ticket %I64u - only %.2f in profit "
                     "(need %.2f to clear the broker's minimum stop distance)",
                     ticket, distance, minStopDist);
         continue;
      }
      double be = NormalizeDouble(openPrice, digits);
      if(InpDryRun)
         PrintFormat("TelegramSMC_Copier: [DRY-RUN] would move ticket %I64u SL to breakeven %.2f", ticket, be);
      else
         trade.PositionModify(ticket, be, currentTp);
   }
}

//+------------------------------------------------------------------+
//| Places the copied order: LIMIT if price hasn't reached the zone,  |
//| MARKET if it's already inside it, skipped if already through it.  |
//| Reports what happened via the out-parameters so ProcessSignal can |
//| log one complete row per signal regardless of which path was      |
//| taken. Returns false only for the "stale, zone already breached"  |
//| case - true otherwise, even for a dry-run log-only line or a live |
//| order the broker rejected (outRetcode/outTicket show which).      |
//+------------------------------------------------------------------+
bool PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound, double sl,
                       string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode)
{
   outOrderType  = "";
   outOrderPrice = 0.0;
   outTicket     = 0;
   outRetcode    = 0;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("TelegramSMC_Copier: no tick available, cannot place order.");
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
         PrintFormat("TelegramSMC_Copier: BUY zone %.2f-%.2f already breached (ask=%.2f) - stale, skipping.",
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
         PrintFormat("TelegramSMC_Copier: SELL zone %.2f-%.2f already breached (bid=%.2f) - stale, skipping.",
                     lowerBound, upperBound, tick.bid);
         outOrderType = "STALE_SKIPPED";
         return(false);
      }
      else { orderPrice = tick.bid; isPending = false; }
   }

   orderPrice = NormalizeDouble(orderPrice, digits);
   double tp  = isBuy ? orderPrice + InpTp1Points : orderPrice - InpTp1Points;
   tp = NormalizeDouble(tp, digits);
   sl = NormalizeDouble(sl, digits);

   outOrderType  = isPending ? "LIMIT" : "MARKET";
   outOrderPrice = orderPrice;

   if(InpDryRun)
   {
      PrintFormat("TelegramSMC_Copier: [DRY-RUN] would place %s %s %.2f lots @ %.2f sl=%.2f tp=%.2f",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", InpFixedLot, orderPrice, sl, tp);
      return(true);
   }

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
      PrintFormat("TelegramSMC_Copier: %s %s placed - %.2f lots @ %.2f sl=%.2f tp=%.2f ticket=%I64u",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", InpFixedLot, orderPrice, sl, tp,
                  outTicket);
   }
   else
      PrintFormat("TelegramSMC_Copier: order failed. retcode=%d desc=%s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());

   return(true);
}

//+------------------------------------------------------------------+
//| TP1-arm / trail-thereafter management for open positions.         |
//| InpTp1Points/InpTrailPoints are PRICE UNITS (dollars on XAUUSD),   |
//| not broker points and not pips - see the file header.             |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   double point         = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits        = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long   stopsLevelPts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDist   = stopsLevelPts * point;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSl = PositionGetDouble(POSITION_SL);
      double currentTp = PositionGetDouble(POSITION_TP);

      double newSl    = currentSl;
      bool   changeSl = false;

      if(type == POSITION_TYPE_BUY)
      {
         double profit = tick.bid - openPrice;
         if(profit >= InpTp1Points)
         {
            double candidate = NormalizeDouble(tick.bid - InpTrailPoints, digits);
            if((currentSl <= 0.0 || candidate > currentSl) && (tick.bid - candidate) >= minStopDist)
            {
               newSl = candidate;
               changeSl = true;
            }
         }
         // Only drop the TP once the trailing SL has actually taken its
         // place THIS tick - tying it to profit alone (rather than to
         // changeSl) would zero the TP even on a broker/symbol where
         // minStopDist is wider than InpTrailPoints, where changeSl can
         // never become true; the position would then have neither a
         // working TP nor a trailing SL for the rest of its life.
         bool changeTp = changeSl && (currentTp != 0.0);
         if(changeSl || changeTp)
         {
            if(InpDryRun)
               PrintFormat("TelegramSMC_Copier: [DRY-RUN] would modify BUY ticket %I64u sl %.2f -> %.2f, "
                           "tp -> none", ticket, currentSl, changeSl ? newSl : currentSl);
            else
               trade.PositionModify(ticket, changeSl ? newSl : currentSl, 0.0);
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double profit = openPrice - tick.ask;
         if(profit >= InpTp1Points)
         {
            double candidate = NormalizeDouble(tick.ask + InpTrailPoints, digits);
            bool   haveSl    = currentSl > 0.0;
            if((!haveSl || candidate < currentSl) && (candidate - tick.ask) >= minStopDist)
            {
               newSl = candidate;
               changeSl = true;
            }
         }
         bool changeTp = changeSl && (currentTp != 0.0);
         if(changeSl || changeTp)
         {
            if(InpDryRun)
               PrintFormat("TelegramSMC_Copier: [DRY-RUN] would modify SELL ticket %I64u sl %.2f -> %.2f, "
                           "tp -> none", ticket, currentSl, changeSl ? newSl : currentSl);
            else
               trade.PositionModify(ticket, changeSl ? newSl : currentSl, 0.0);
         }
      }
   }
}

string DirToStr(int dir)
{
   if(dir == DIR_BUY)  return("BUY");
   if(dir == DIR_SELL) return("SELL");
   return("");
}

//+------------------------------------------------------------------+
//| Appends one row to TelegramSMC_Signals.csv for every Telegram      |
//| message this EA evaluates - accepted or not - so the signals log   |
//| is a complete record, not just the ones that traded.               |
//+------------------------------------------------------------------+
void LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                   double entryLow, double entryHigh, double sl, const string &tpsJoined,
                   bool smcUsed, bool smcPass, const string &smcReason,
                   bool sanityPass, const string &sanityReason, bool accepted,
                   const string &orderType, double orderPrice, double lots, bool dryRun,
                   long orderTicket, int retcode, const string &rawText)
{
   int handle = TsmcOpenCsvForAppend(TSMC_SIGNALS_FILE, TSMC_SIGNALS_HEADER, false);
   if(handle == INVALID_HANDLE) return;

   string ts  = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
   string line = ts + "," +
                 TsmcCsvField(TSMC_SIGNAL_SOURCE) + "," +
                 IntegerToString(chatId) + "," +
                 TsmcCsvField(action) + "," +
                 TsmcCsvField(direction) + "," +
                 (symbolOk ? "1" : "0") + "," +
                 DoubleToString(entryLow, 2) + "," +
                 DoubleToString(entryHigh, 2) + "," +
                 DoubleToString(sl, 2) + "," +
                 TsmcCsvField(tpsJoined) + "," +
                 (smcUsed ? "1" : "0") + "," +
                 (smcPass ? "1" : "0") + "," +
                 TsmcCsvField(smcReason) + "," +
                 (sanityPass ? "1" : "0") + "," +
                 TsmcCsvField(sanityReason) + "," +
                 (accepted ? "1" : "0") + "," +
                 TsmcCsvField(orderType) + "," +
                 DoubleToString(orderPrice, 2) + "," +
                 DoubleToString(lots, 2) + "," +
                 (dryRun ? "1" : "0") + "," +
                 IntegerToString(orderTicket) + "," +
                 IntegerToString(retcode) + "," +
                 TsmcCsvField(rawText);

   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Dispatch a parsed signal: management actions act broadly (this EA |
//| tracks one symbol/magic, and messages carry no per-setup ticket), |
//| OPEN signals go through sanity + SMC validation before copying.   |
//| Every branch logs exactly one row to TelegramSMC_Signals.csv.      |
//+------------------------------------------------------------------+
void ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText)
{
   if(!IsAllowedChat(chatId))
   {
      PrintFormat("TelegramSMC_Copier: ignoring message from chat %I64d (allowed: %I64d, %I64d)",
                  chatId, InpChannelId1, InpChannelId2);
      return;   // not one of this EA's chats - deliberately not logged as a signal at all
   }

   // Computed once: MQL5 requires an actual variable for a reference-type
   // argument (LogSignalRow's direction parameter), not a function's return
   // value directly - DirToStr(msg.direction) inline fails to compile.
   string dirStr = DirToStr(msg.direction);

   if(msg.action == ACTION_CLOSE)
   {
      Print("TelegramSMC_Copier: CLOSE signal - closing positions and cancelling pending orders.");
      CloseAllMine();
      CancelAllPendingMine();
      LogSignalRow(chatId, "CLOSE", "", msg.symbolOk, 0, 0, 0, "", false, false, "",
                   true, "", true, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.action == ACTION_CANCEL)
   {
      Print("TelegramSMC_Copier: CANCEL signal - cancelling pending orders.");
      CancelAllPendingMine();
      LogSignalRow(chatId, "CANCEL", "", msg.symbolOk, 0, 0, 0, "", false, false, "",
                   true, "", true, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.action == ACTION_MODIFY_SL)
   {
      Print("TelegramSMC_Copier: breakeven signal - moving SL to entry where profit allows.");
      BreakevenAllMine();
      LogSignalRow(chatId, "MODIFY_SL", dirStr, msg.symbolOk, 0, 0, 0, "",
                   false, false, "", true, "", true, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.action != ACTION_OPEN)
   {
      Print("TelegramSMC_Copier: message did not parse as an actionable signal - ignoring.");
      LogSignalRow(chatId, "UNKNOWN", "", msg.symbolOk, 0, 0, 0, "", false, false, "",
                   true, "", false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   string tpList = "";
   for(int i = 0; i < msg.tpCount; i++)
      tpList += (i > 0 ? "|" : "") + DoubleToString(msg.tps[i], 2);

   if(!msg.symbolOk)
   {
      Print("TelegramSMC_Copier: OPEN signal does not mention XAUUSD/GOLD - ignoring.");
      LogSignalRow(chatId, "OPEN", dirStr, false, msg.entryA, msg.entryB, msg.sl,
                   tpList, false, false, "", true, "no XAUUSD/GOLD mention", false, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }
   if(msg.direction != DIR_BUY && msg.direction != DIR_SELL)
   {
      Print("TelegramSMC_Copier: OPEN signal has no clear BUY/SELL direction - ignoring.");
      LogSignalRow(chatId, "OPEN", "", msg.symbolOk, msg.entryA, msg.entryB, msg.sl, tpList,
                   false, false, "", true, "no BUY/SELL direction", false, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }

   UpdateDailyTracking();
   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay)
   {
      string r = StringFormat("max trades/day reached (%d)", InpMaxTradesPerDay);
      PrintFormat("TelegramSMC_Copier: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB,
                   msg.sl, tpList, false, false, "", true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   if(CountActiveSlots() >= InpMaxOpenPositions)
   {
      string r = StringFormat("max open positions/orders reached (%d)", InpMaxOpenPositions);
      PrintFormat("TelegramSMC_Copier: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB,
                   msg.sl, tpList, false, false, "", true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   bool   isBuy = (msg.direction == DIR_BUY);
   double lowerBound, upperBound;
   if(msg.hasRange) { lowerBound = MathMin(msg.entryA, msg.entryB); upperBound = MathMax(msg.entryA, msg.entryB); }
   else              { lowerBound = msg.entryA; upperBound = msg.entryA; }

   if(lowerBound <= 0.0)
   {
      Print("TelegramSMC_Copier: OPEN signal has no usable entry price - ignoring.");
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB,
                   msg.sl, tpList, false, false, "", true, "no usable entry price", false, "",
                   0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   double entryPrice = isBuy ? upperBound : lowerBound;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("TelegramSMC_Copier: no tick, cannot evaluate signal.");
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound,
                   msg.sl, tpList, false, false, "", true, "no tick available", false, "",
                   0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   double mid     = (tick.ask + tick.bid) / 2.0;
   double devPips = MathAbs(mid - entryPrice) / PipSize();
   if(devPips > InpMaxEntryDeviationPips)
   {
      string r = StringFormat("price %.2f is %.1f pips from the zone (max %.1f)",
                               mid, devPips, InpMaxEntryDeviationPips);
      PrintFormat("TelegramSMC_Copier: %s - skipping.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound,
                   msg.sl, tpList, false, false, "", true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   string sanityReason;
   bool sanityOk = ValidateSignalSanity(msg, isBuy, lowerBound, upperBound, sanityReason);
   if(!sanityOk)
   {
      PrintFormat("TelegramSMC_Copier: signal rejected - %s", sanityReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound,
                   msg.sl, tpList, false, false, "", false, sanityReason, false, "", 0, 0,
                   InpDryRun, 0, 0, rawText);
      return;
   }

   bool   smcUsed   = InpUseSmcFilter;
   bool   smcOk     = true;
   string smcReason = "not required";
   if(InpUseSmcFilter)
   {
      smcOk = SmcValidate(isBuy, entryPrice, msg.sl, smcReason);
      PrintFormat("TelegramSMC_Copier: SMC check %s - %s", smcOk ? "PASSED" : "FAILED", smcReason);
      if(!smcOk)
      {
         LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound,
                      msg.sl, tpList, true, false, smcReason, true, sanityReason, false, "", 0, 0,
                      InpDryRun, 0, 0, rawText);
         return;
      }
   }

   PrintFormat("TelegramSMC_Copier: copying %s XAUUSD %.2f-%.2f sl=%.2f from chat %I64d "
               "(signal TPs logged only, not used: %s)",
               isBuy ? "BUY" : "SELL", lowerBound, upperBound, msg.sl, chatId,
               msg.tpCount > 0 ? tpList : "none given");

   string outOrderType  = "";
   double outOrderPrice = 0.0;
   long   outTicket     = 0;
   int    outRetcode    = 0;
   bool   placed = PlaceCopiedOrder(isBuy, lowerBound, upperBound, msg.sl,
                                     outOrderType, outOrderPrice, outTicket, outRetcode);

   LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound,
                msg.sl, tpList, smcUsed, smcOk, smcReason, true, sanityReason, placed,
                outOrderType, outOrderPrice, InpFixedLot, InpDryRun, outTicket, outRetcode, rawText);
}

//+------------------------------------------------------------------+
//| Telegram Bot API: getUpdates (short poll, timeout=0) + a minimal  |
//| field scraper - no external JSON library, only the fields used.   |
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
      PrintFormat("TelegramSMC_Copier: WebRequest failed, error=%d. If this is a permissions "
                  "error, add https://api.telegram.org to Tools > Options > Expert Advisors > "
                  "'Allow WebRequest for listed URL'.", GetLastError());
      return(false);
   }
   if(rc != 200)
   {
      PrintFormat("TelegramSMC_Copier: Telegram HTTP status %d", rc);
      return(false);
   }
   jsonOut = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return(true);
}

//+------------------------------------------------------------------+
//| Some JSON producers escape non-ASCII characters (emoji included)  |
//| as \uXXXX rather than sending raw UTF-8 bytes. Left alone, the    |
//| literal hex digits (which include letters) would corrupt the      |
//| whole-word boundary checks used throughout parsing - e.g. an      |
//| emoji sitting right before "XAUUSD" could make FindWholeWord miss |
//| it. Replacing each valid \uXXXX run with a single space is a      |
//| no-op when the text is already raw UTF-8 (nothing to match) and   |
//| fixes the boundary either way.                                    |
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
//| body by scanning for the field names directly - no general JSON   |
//| parser, only the handful of fields this EA needs.                 |
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
               string placeholder = CharToString((uchar)1);   // stand-in for a literal backslash
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
      PrintFormat("TelegramSMC_Copier: Telegram API returned an error: %s", json);
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
         PrintFormat("TelegramSMC_Copier: message from chat %I64d is %ds old (> %ds) - stale, skipping.",
                     updates[i].chat_id, age, InpMaxSignalAgeSec);
         continue;
      }

      PrintFormat("TelegramSMC_Copier: message from chat %I64d: %s", updates[i].chat_id, updates[i].text);

      SignalMsg msg;
      ParseSignalText(updates[i].text, msg);
      ProcessSignal(msg, updates[i].chat_id, updates[i].text);
   }

   if(ArraySize(updates) > 0)
      GlobalVariableSet(GV_LAST_UPDATE_ID, (double)g_lastUpdateId);
}

//+------------------------------------------------------------------+
//| Expert tick function - only trade management; entries come from   |
//| Telegram via OnTimer, not from price ticks.                       |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenPositions();
}

//+------------------------------------------------------------------+
//| Expert timer function - polls Telegram and does light upkeep.     |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDailyTracking();
   ExpirePendingOrders();
   TelegramPoll();
}
//+------------------------------------------------------------------+
