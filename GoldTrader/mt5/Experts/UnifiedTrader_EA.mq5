//+------------------------------------------------------------------+
//|                                         UnifiedTrader_EA.mq5       |
//|                                      part of the GoldTrader package|
//|                                                                    |
//| One EA, two trade sources, one shared risk model.                  |
//|                                                                    |
//| SOURCE 1 - TELEGRAM (InpEnableTelegramSignals): polls the Telegram |
//| Bot API (WebRequest) for up to three channels (InpChannelId1..3),  |
//| reads TRADE messages only (TsmcClassifyMessage - greetings, mood    |
//| posts, long messages, media and notices are omitted) and executes a|
//| parsed signal under InpTelegramMagicNumber. The message gives the  |
//| direction, the entry zone and - with InpTelegramUseSignalSl - the  |
//| stop: the signal's own SL when it is InpSignalSlMinDistance to     |
//| InpSignalSlMaxDistance from the entry, else the fixed InpSlDollars |
//| stop; the lot is sized from that stop so every trade still risks   |
//| InpRiskPercent. Its TPs are logged only. Still checked: allow-list,|
//| signal age, price run-away from the zone, the XTR M15/H1 filter,   |
//| the news blackout, the position cap and the daily caps. CLOSE /    |
//| CANCEL messages act on this EA's own Telegram positions only.      |
//|                                                                    |
//| SOURCE 2 - CLAUDE (InpEnableClaudeManagement): GoldTrader's        |
//| app/main.py decides and opens these entries (InpClaudeMagicNumber, |
//| must equal app/config.py AdvisorConfig.magic). This EA manages     |
//| their exits tick by tick.                                          |
//|                                                                    |
//| Exit management never depends on those two flags: any position     |
//| under either magic keeps being protected while the EA runs.        |
//|                                                                    |
//| EXITS (both sources): no broker take-profit. At +InpTp1Dollars the |
//| SL moves exactly to that level (locked profit), then trails        |
//| InpTrailDollars behind new highs/lows, tightening only. Dollars are|
//| at InpReferenceLot, converted with the live tick value/size, so    |
//| they are fixed price distances whatever lot is traded. Whether a   |
//| position is "locked" is derived from its own SL every tick - no    |
//| memory needed across restarts. InpExitStyle=EXIT_BREAKEVEN_R_DECAY |
//| (Claude positions only, keep in sync with AdvisorConfig.exit_style)|
//| adds a breakeven step at InpBreakevenAtrMult x ATR or after        |
//| InpDecayWindowMinutes, whichever comes first.                      |
//|                                                                    |
//| SHARED CAP: InpMaxPositionsPerDirection counts BOTH magics         |
//| together. main.py applies the same combined cap with               |
//| --shared-cap-magic 20260922 (start.bat does this).                 |
//|                                                                    |
//| REMOTE CONTROL (InpControlChatId - your private chat with the bot):|
//| reply-keyboard buttons, matched by exact text:                     |
//|   PauseHab / ResumeHab             all trades (pause closes them)  |
//|   PauseTelHab / ResumeTelHab       Telegram trades only            |
//|   PauseClaudeHab / ResumeClaudeHab Claude trades only - written to |
//|                    InpClaudePauseFilename in Common\Files, which   |
//|                    main.py reads every cycle (same PC required)    |
//|   Stats   equity, P/L, win % today / 7 / 30 days, daily budget     |
//|   News    economic calendar with gold impact                       |
//|   Why     Claude's last reasoning (InpLastVerdictFilename)         |
//| Pause states survive restarts (terminal Global Variables). With    |
//| InpControlChatId=0 remote control is off and a saved pause is not  |
//| enforced. InpNotifyTradeClosed messages every closed trade.        |
//| Position commands act on this chart's symbol only. Run ONE         |
//| instance per terminal (Telegram offset and pause state are         |
//| terminal-wide).                                                    |
//|                                                                    |
//| ECONOMIC CALENDAR (EconCalendar.mqh, MT5's own): new Telegram      |
//| entries are skipped around InpNewsMinImportance+ events for        |
//| InpNewsCurrencies; the calendar is exported to                     |
//| InpCalendarExportFile (Common\Files) for main.py's own blackout.   |
//|                                                                    |
//| PRICE FILES (XtrBarExport.mqh, InpXtrExport): closed M5/M15/H1     |
//| bars in UTC to Common\Files\XTR_Data every M1 close, optionally    |
//| copied to InpXtrExportCopyTo (e.g. a Google Drive folder; needs    |
//| Allow DLL imports).                                                |
//|                                                                    |
//| SETUP: GoldTrader/README.md. Allow WebRequest for                  |
//| https://api.telegram.org; with channel ids at 0 the Experts tab    |
//| shows each chat's id. Runs with InpDryRun=true until you turn it   |
//| off - demo-test first: Telegram signals are executed without any   |
//| structure validation of the message itself.                        |
//+------------------------------------------------------------------+
#property copyright "UnifiedTrader_EA"
#property link      ""
#property version   "1.00"
#property strict
#property description "One EA, two trade sources sharing one risk model: unvalidated Telegram signal execution (the signal's own stop within InpSignalSlMin/MaxDistance or a fixed $6 one, $6 lock, $3 trail, no SMC filter) and/or exit management for GoldTrader's Claude-decided positions (app/main.py). Toggle either or both. Educational use - demo-test with InpDryRun=true before risking real capital."

#include <Trade\Trade.mqh>
#include <TelegramSMC_Common.mqh>
#include <EconCalendar.mqh>
// Copy of the Drive price files to any folder (InpXtrExportCopyTo) uses
// kernel32 CopyFileW. Delete this line for a build with no DLL import.
#define XTR_EXPORT_COPY_DLL
#include <XtrBarExport.mqh>

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
#define GV_TELEGRAM_PAUSED "UnifiedTrader_EA_TelegramPaused"
#define GV_CLAUDE_PAUSED   "UnifiedTrader_EA_ClaudePaused"
#define GV_DAY_ANCHOR      "UnifiedTrader_EA_DayAnchor"
#define GV_DAY_START_EQ    "UnifiedTrader_EA_DayStartEquity"
#define GV_DAY_LOSS_HIT    "UnifiedTrader_EA_DailyLossHit"
#define GV_DAY_TRADES      "UnifiedTrader_EA_TradesToday"

// Source tag for this EA's Telegram rows (signal log, order comments) and
// the Trade Logger preset's InpSourceLabel - one name for the same trades.
#define UNIFIED_TELEGRAM_SOURCE "Telegram_Sig_Unified"

//================================= INPUTS ====================================

input group "=== Mode selection - enable either or both ==="
input bool    InpEnableTelegramSignals = false;   // Poll Telegram and execute signals (no SMC validation - see file header)
input bool    InpEnableClaudeManagement = false;  // Manage exits for GoldTrader's Claude-opened positions (app/main.py)

input group "=== Shared business rules - apply to BOTH sources ==="
input double  InpFixedLot               = 0.01;   // Traded lot for Telegram entries when InpUseRiskPercent=false
input double  InpReferenceLot           = 0.01;   // SL/TP1/trail dollars are priced at this lot, i.e. fixed PRICE distances - MUST match python AdvisorConfig.reference_lot
input bool    InpUseRiskPercent         = true;    // Size Telegram-sourced entries from equity instead of always InpFixedLot
input double  InpRiskPercent            = 2.0;     // InpUseRiskPercent only: risk this % of equity per trade (recommended: InpMaxDailyLossPct / 5)
input double  InpMaxLotSize             = 5.0;     // InpUseRiskPercent only: hard cap on a risk-sized lot
input int     InpMaxPositionsPerDirection = 5;     // SHARED cap, counted across BOTH magics together
input double  InpSlDollars              = 6.0;     // Initial stop-loss (USD at InpReferenceLot = a fixed price distance)
input double  InpTp1Dollars             = 6.0;     // Profit (USD at InpReferenceLot) that locks the SL in here (exact, no buffer)
input double  InpTrailDollars           = 3.0;     // Trailing distance (USD at InpReferenceLot) once locked/armed

input group "=== Identification ==="
input long    InpTelegramMagicNumber = 20260922;   // This EA's own Telegram-sourced trades
input long    InpClaudeMagicNumber   = 20260921;   // MUST match app/config.py AdvisorConfig.magic
input bool    InpDryRun              = true;       // Log what would happen; do not send/modify real orders

enum ENUM_EXIT_STYLE
{
   EXIT_SL_TO_TP1,          // Default - lock at InpTp1Dollars, then trail (see file header)
   EXIT_BREAKEVEN_R_DECAY   // Adds an earlier breakeven step, InpClaudeMagicNumber positions only - see below
};

input group "=== Claude-management exit style - InpClaudeMagicNumber positions ONLY, never Telegram's ==="
input ENUM_EXIT_STYLE InpExitStyle = EXIT_SL_TO_TP1;      // Must match app/config.py AdvisorConfig.exit_style
input double          InpBreakevenAtrMult   = 0.5;        // EXIT_BREAKEVEN_R_DECAY only: move SL to breakeven once profit reaches this x ATR
input ENUM_TIMEFRAMES InpAtrTimeframe       = PERIOD_M5;  // EXIT_BREAKEVEN_R_DECAY only: timeframe the ATR is read from
input int             InpAtrPeriod          = 14;         // EXIT_BREAKEVEN_R_DECAY only: ATR period
input double          InpDecayWindowMinutes = 15.0;       // EXIT_BREAKEVEN_R_DECAY only: force breakeven after this long even short of the ATR trigger

input group "=== Telegram Bot - only used if InpEnableTelegramSignals (see file header for setup) ==="
input string  InpBotToken          = "";           // Bot token from @BotFather
input long    InpChannelId1        = 0;            // Only copy signals from this chat id (0 = slot unused)
input long    InpChannelId2        = 0;            // ...and this one (0 = slot unused)
input long    InpChannelId3        = 0;            // ...and this one - max 3 channels (all 0 = ANY chat - dry-run discovery only)
input int     InpMaxMessageChars   = 400;          // Trade messages only: omit anything longer (0 = no limit)
input int     InpMaxCommandChars   = 60;           // A CLOSE/EXIT/CANCEL/BE command must be this short and trading words only
input bool    InpAcceptPhotoCaptions = false;      // Also read a signal posted as a photo caption (videos/audio/voice/stickers are always omitted)
input bool    InpLogSkippedMessages = false;       // Print every omitted non-trade message (always on while no channel id is set)
input int     InpPollSeconds       = 5;            // How often to poll Telegram for new messages
input int     InpHttpTimeoutMs     = 5000;         // WebRequest timeout (ms)
input int     InpMaxSignalAgeSec   = 180;          // Reject a signal/control command older than this many seconds (0 = no limit)
input bool    InpTradeXAUUSDOnly   = true;         // Require chart symbol to contain "XAU"
input int     InpMaxTradesPerDay   = 0;            // 0 = unlimited (Telegram-sourced trades only)
input double  InpMaxDailyLossPct   = 10.0;         // 0 = disabled; stop new Telegram-sourced entries after this % equity drawdown on the day
input int     InpMaxSpreadPoints   = 50;           // Skip a Telegram entry while the spread is above this many points (0 = off)
input bool    InpMarginGuard       = true;         // Skip a Telegram entry if free margin after it could not cover every open stop + its own
input string  InpTradeHours        = "06:00-23:00"; // New Telegram entries only inside these hours, local time below ("" = any time) - mirrored in app/config.py telegram_trade_windows
input double  InpTradeUtcOffsetHours = 4.0;        // That local time's offset from UTC: Oman = 4 (no daylight saving)
input bool    InpTradeWeekdaysOnly = true;         // New Telegram entries Monday-Friday only (local time)
input int     InpPendingExpiryMin  = 240;          // Cancel an unfilled pending order after N minutes (0 = never)

input group "=== Telegram Signal Sanity (pips; 1 pip = 10 broker points) ==="
input double  InpMaxEntryDeviationPips = 200.0;    // Reject if current price is this far outside the signaled zone
input bool    InpTelegramUseSignalSl  = true;      // Telegram entries: use the signal's own stop, lot resized to still risk InpRiskPercent (false = fixed InpSlDollars)
input double  InpSignalSlMinDistance  = 3.0;       // Signal stop used only if at least this far from the entry (price, gold $)...
input double  InpSignalSlMaxDistance  = 20.0;      // ...and at most this far - otherwise, or with no stop in the signal, the fixed InpSlDollars stop

input group "=== Remote control (optional) - see file header's REMOTE CONTROL section ==="
input long    InpControlChatId = 0;                // Your own DM chat id with this bot; 0 = disabled
input string  InpLastVerdictFilename = "claudesmc_last_verdict.txt"; // Why button: MUST match app/config.py's AdvisorConfig.last_verdict_filename
input string  InpClaudePauseFilename = "claudesmc_pause.txt";        // PauseClaudeHab/ResumeClaudeHab: MUST match app/config.py's AdvisorConfig.claude_pause_filename
input bool    InpNotifyTradeClosed   = true;                         // Message InpControlChatId on every closed trade (P/L, equity, today's win%)

input group "=== XTR HTF filter (Telegram entries) - never trade against a clear M15/H1 ==="
input bool            InpXtrHtfFilter = true;          // Skip a Telegram entry when an HTF below is CLEARLY against it
input ENUM_TIMEFRAMES InpXtrHtf1      = PERIOD_M15;    // HTF #1 - clear = EMA9 vs EMA21, RSI14 vs 50 and MACD(12,26,9) histogram ALL agree
input ENUM_TIMEFRAMES InpXtrHtf2      = PERIOD_H1;     // HTF #2 (2-of-3 agreement counts as mixed, never blocks)

input group "=== Economic calendar (MT5 built-in, no API key) - see EconCalendar.mqh ==="
input bool                 InpNewsFilter         = true;                  // Skip new Telegram entries near important news
input string               InpNewsCurrencies     = "USD";                 // Currencies to watch, comma-separated (gold is priced in USD)
input ENUM_ECON_IMPORTANCE InpNewsMinImportance  = ECON_IMPORTANCE_HIGH;  // Which events block entries (and are listed by the News button)
input int                  InpNewsBlockBeforeMin = 15;                    // Block new entries this many minutes before the event...
input int                  InpNewsBlockAfterMin  = 15;                    // ...and this many minutes after it
input string               InpCalendarExportFile = "econ_calendar.csv";   // Shared file GoldTrader reads - MUST match app/config.py.econ_calendar_filename ("" = no export)
input int                  InpCalendarRefreshMin = 5;                     // Re-export the calendar every N minutes

input group "=== Price export for Google Drive (XTR) - see XtrBarExport.mqh ==="
input bool   InpXtrExport       = true;        // Write closed M5/M15/H1 bars (UTC CSV + manifest) on every M1 close
input string InpXtrExportFolder = "XTR_Data";  // Folder inside Common\Files - add it to Google Drive for Desktop
input string InpXtrExportName   = "XAUUSD";    // File name prefix / manifest symbol (XAUUSD_M5.csv ...)
input int    InpXtrExportBars   = 200;         // Closed bars per file (50-5000)
input bool   InpXtrExportM1     = false;       // Also write <name>_M1.csv
input string InpXtrExportCopyTo = "";          // Also copy every file to this folder, e.g. G:\My Drive\MyMQChartDrive (needs "Allow DLL imports")

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
   double sl;          // the signal's stop - used only via TelegramStopDistance() (InpTelegramUseSignalSl)
   double tps[6];       // parsed for the log row only - never used
   int    tpCount;
};


//================================= GLOBALS ====================================

CTrade   trade;
long     g_lastUpdateId  = 0;
datetime g_currentDay    = 0;
int      g_tradesToday   = 0;
double   g_dayStartEquity = 0.0;             // InpMaxDailyLossPct only - see UpdateDailyTracking/DailyLossBreakerActive
bool     g_dailyLossHit   = false;           // latches for the rest of the day once InpMaxDailyLossPct is breached
int      g_atrHandle     = INVALID_HANDLE;   // EXIT_BREAKEVEN_R_DECAY only - see OnInit/OnDeinit
int      g_xtrH[10];                         // InpXtrHtfFilter: EMA9, EMA21, RSI14, MACD, EMA9(MACD) per HTF
bool     g_telegramPaused = false;           // PauseHab/PauseTelHab/ResumeHab/ResumeTelHab - see file header
bool     g_claudePaused   = false;           // PauseHab/PauseClaudeHab/ResumeHab/ResumeClaudeHab - see file header
bool     g_sentControlStartupMsg = false;    // one-shot: the buttons/keyboard intro, sent from TelegramPoll()
long     g_lastControlChatSentTo = 0;        // which chat it was last actually sent to, this session
string   g_notifyQueue[];                     // trade-closed messages, queued in OnTradeTransaction, sent from OnTimer

struct ClosedStatsT
{
   int    trades;
   int    wins;
   int    losses;
   double pnl;
};

// Forward declarations
double   BreakevenAtrDistance();
bool     BreakevenDue(double profit);
double   PipSize();
bool     IsAllowedChat(long chatId);
int      XtrClassify(int slot);
bool     XtrHtfOpposes(int dir, string &reason);
datetime DateToDay(datetime t);
void     UpdateDailyTracking();
void     SaveDayState();
bool     DailyLossBreakerActive();
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
double   FixedSlDistance();
double   PositionSizeLots(double slDist = 0.0);
double   OpenRiskMoney();
string   DailyRiskBudgetReason(double newLots, double slDist = 0.0);
string   MarginGuardReason(bool isBuy, double newLots, double slDist = 0.0);
bool     ParseHHMM(string s, int &minutes);
bool     ParseTradeHours(const string spec);
string   TradeHoursReason();
int      CountSameDirection(int direction);
void     ExpirePendingOrders();
int      ClosePositionsByMagic(long magic, const string &label);
int      CloseAllMine();
int      CancelAllPendingMine();
int      CloseAllClaudeMine();
void     SetTelegramPaused(bool paused);
void     SetClaudePaused(bool paused);
void     WriteClaudePauseFile();
void     SendControlReply(const string summary);
string   ReadLastVerdictFile();
void     ClosedStats(datetime fromServer, long magic, ClosedStatsT &st);
string   StatsLine(const string label, const ClosedStatsT &st);
string   BuildStatsText();
void     FlushNotifyQueue();
void     ProcessControlCommand(const string &rawText);
bool     PrecededByWord(const string &text, int idx, const string word);
bool     PlanCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                          double &orderPrice, bool &isPending, string &failType);
double   TelegramStopDistance(const SignalMsg &msg, bool isBuy, double orderPrice, string &note);
bool     PlaceCopiedOrder(bool isBuy, double orderPrice, bool isPending, double slDist, double lots,
                           string &outOrderType, long &outTicket, int &outRetcode);
void     ManagePositionExit(ulong ticket, long magic);
void     ManageAllPositions();
void     ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText);
bool     TelegramGetUpdates(string &jsonOut);
void     TelegramPoll();
string   JsonEscape(const string &s);
bool     TelegramSendMessage(long chatId, const string text);
string   DirToStr(int dir);
void     LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                       double entryLow, double entryHigh, const string &tpsJoined,
                       bool sanityPass, const string &sanityReason, bool accepted,
                       const string &orderType, double orderPrice, double lots, bool dryRun,
                       long orderTicket, int retcode, const string &rawText, double slUsed = 0.0);

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
   if(InpTelegramUseSignalSl && (InpSignalSlMinDistance <= 0.0 || InpSignalSlMaxDistance < InpSignalSlMinDistance))
   {
      Print("UnifiedTrader_EA: InpSignalSlMinDistance must be > 0 and InpSignalSlMaxDistance at least as large.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpNewsBlockBeforeMin < 0 || InpNewsBlockAfterMin < 0 || InpCalendarRefreshMin < 1)
   {
      Print("UnifiedTrader_EA: InpNewsBlockBeforeMin/InpNewsBlockAfterMin must be >= 0 and "
            "InpCalendarRefreshMin >= 1.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpSlDollars <= 0.0 || InpTp1Dollars <= 0.0 || InpTrailDollars <= 0.0 || InpFixedLot <= 0.0
      || InpReferenceLot <= 0.0)
   {
      Print("UnifiedTrader_EA: InpFixedLot, InpReferenceLot, InpSlDollars, InpTp1Dollars and "
            "InpTrailDollars must all be positive.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(!ParseTradeHours(InpTradeHours) || InpTradeUtcOffsetHours < -12.0 || InpTradeUtcOffsetHours > 14.0)
   {
      PrintFormat("UnifiedTrader_EA: InpTradeHours '%s' / InpTradeUtcOffsetHours %.2f not understood - use "
                  "e.g. 06:00-23:00 (or 06:00-12:00,14:00-23:00; empty = any time) and an offset "
                  "between -12 and 14 (Oman = 4).", InpTradeHours, InpTradeUtcOffsetHours);
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpTelegramMagicNumber == InpClaudeMagicNumber)
   {
      // Unconditional, not just under InpEnableTelegramSignals: the remote-
      // control commands (PauseTelHab/PauseClaudeHab/PauseHab) and
      // ManageAllPositions() both dispatch on these two magics whenever this
      // EA is running at all, regardless of which source(s) are enabled - a
      // collision here would make PauseTelHab close Claude-sourced positions
      // too (or vice versa), silently contradicting what each command
      // documents itself as touching.
      Print("UnifiedTrader_EA: InpTelegramMagicNumber and InpClaudeMagicNumber must differ - otherwise "
            "this EA cannot tell the two sources' positions apart.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(InpXtrExport && (InpXtrExportBars < 50 || InpXtrExportBars > 5000
                       || StringLen(InpXtrExportFolder) == 0 || StringLen(InpXtrExportName) == 0))
   {
      Print("UnifiedTrader_EA: InpXtrExportBars must be 50-5000 and InpXtrExportFolder/InpXtrExportName "
            "non-empty (or set InpXtrExport=false).");
      return(INIT_PARAMETERS_INCORRECT);
   }
   XtrExpReset();
   if(InpXtrExport && StringLen(InpXtrExportCopyTo) > 0 && !MQLInfoInteger(MQL_DLLS_ALLOWED))
      PrintFormat("UnifiedTrader_EA: InpXtrExportCopyTo=%s needs \"Allow DLL imports\" (EA Common tab) - "
                  "until then files stay in Common\\Files\\%s only.", InpXtrExportCopyTo, InpXtrExportFolder);
   ArrayInitialize(g_xtrH, INVALID_HANDLE);
   if(InpXtrHtfFilter && InpEnableTelegramSignals)
   {
      ENUM_TIMEFRAMES tfs[2];
      tfs[0] = InpXtrHtf1;
      tfs[1] = InpXtrHtf2;
      for(int k = 0; k < 2; k++)
      {
         g_xtrH[k * 5]     = iMA(_Symbol, tfs[k], 9, 0, MODE_EMA, PRICE_CLOSE);
         g_xtrH[k * 5 + 1] = iMA(_Symbol, tfs[k], 21, 0, MODE_EMA, PRICE_CLOSE);
         g_xtrH[k * 5 + 2] = iRSI(_Symbol, tfs[k], 14, PRICE_CLOSE);
         g_xtrH[k * 5 + 3] = iMACD(_Symbol, tfs[k], 12, 26, 9, PRICE_CLOSE);
         // The standard (EMA) signal line - MT5's own MACD signal is an SMA,
         // which would disagree with the spec and xtr_logic.py near zero.
         if(g_xtrH[k * 5 + 3] != INVALID_HANDLE)
            g_xtrH[k * 5 + 4] = iMA(_Symbol, tfs[k], 9, 0, MODE_EMA, g_xtrH[k * 5 + 3]);
      }
      for(int h = 0; h < 10; h++)
         if(g_xtrH[h] == INVALID_HANDLE)
         {
            Print("UnifiedTrader_EA: could not create the XTR HTF filter indicators - set "
                  "InpXtrHtfFilter=false to run without it.");
            return(INIT_FAILED);
         }
   }

   if(InpExitStyle == EXIT_BREAKEVEN_R_DECAY)
   {
      if(InpBreakevenAtrMult <= 0.0 || InpAtrPeriod <= 0 || InpDecayWindowMinutes <= 0.0)
      {
         Print("UnifiedTrader_EA: InpBreakevenAtrMult, InpAtrPeriod and InpDecayWindowMinutes must all "
               "be positive under EXIT_BREAKEVEN_R_DECAY.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      g_atrHandle = iATR(_Symbol, InpAtrTimeframe, InpAtrPeriod);
      if(g_atrHandle == INVALID_HANDLE)
      {
         Print("UnifiedTrader_EA: iATR() failed - cannot run EXIT_BREAKEVEN_R_DECAY.");
         return(INIT_FAILED);
      }
      Print("UnifiedTrader_EA: EXIT_BREAKEVEN_R_DECAY is active for InpClaudeMagicNumber positions only "
            "- InpTelegramMagicNumber positions always use EXIT_SL_TO_TP1 regardless of this setting.");
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
      if(InpChannelId1 == 0 && InpChannelId2 == 0 && InpChannelId3 == 0 && !InpDryRun)
      {
         // Live + no channel filter = anyone who finds this bot's username
         // could DM it a "BUY" and open a real trade. Refused outright.
         Print("UnifiedTrader_EA: REFUSING TO START - InpDryRun=false but InpChannelId1..3 are "
               "all 0, so ANY chat could send this bot a trade signal. Run once "
               "with InpDryRun=true, read the channel id from 'message from chat <id>' in the log, "
               "set InpChannelId1, then go live.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      if(InpChannelId1 == 0 && InpChannelId2 == 0 && InpChannelId3 == 0)
         Print("UnifiedTrader_EA: WARNING - InpChannelId1..3 are all 0, so signals from "
               "ANY chat this bot can see will be copied (dry-run only - live trading is refused in "
               "this state). Watch the log for 'message from chat <id>', set InpChannelId1 (and "
               "InpChannelId2/InpChannelId3 for more channels, 3 max), and restart.");
      else if((InpChannelId1 != 0 && (InpChannelId1 == InpChannelId2 || InpChannelId1 == InpChannelId3))
              || (InpChannelId2 != 0 && InpChannelId2 == InpChannelId3))
         Print("UnifiedTrader_EA: WARNING - two of InpChannelId1..3 are the same chat id; the "
               "duplicate slot is redundant.");
      Print("UnifiedTrader_EA: Telegram signal execution is ENABLED WITH NO SMC VALIDATION and NO use "
            "of the message's own stop-loss - see the file header. This is materially higher-risk than "
            "a validated signal copier.");
   }
   if(InpEnableClaudeManagement)
      PrintFormat("UnifiedTrader_EA: managing exits for magic=%I64d (must match app/config.py's "
                  "AdvisorConfig.magic).", InpClaudeMagicNumber);
   if(InpControlChatId != 0)
   {
      if(StringLen(InpBotToken) == 0)
      {
         Print("UnifiedTrader_EA: InpControlChatId is set but InpBotToken is empty - a bot token is "
               "needed to receive control-command DMs even if InpEnableTelegramSignals is off.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      if(InpChannelId1 == InpControlChatId || InpChannelId2 == InpControlChatId
         || InpChannelId3 == InpControlChatId)
      {
         Print("UnifiedTrader_EA: InpControlChatId must differ from InpChannelId1..3 - a "
               "message from the signal channel must never be treated as a control command.");
         return(INIT_PARAMETERS_INCORRECT);
      }
      PrintFormat("UnifiedTrader_EA: remote control ENABLED via chat %I64d (PauseHab/ResumeHab/"
                  "PauseTelHab/ResumeTelHab/PauseClaudeHab/ResumeClaudeHab/Why) - see file "
                  "header's REMOTE CONTROL section.",
                  InpControlChatId);
   }
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
      // Exact for every position, whatever its volume: TP1/trail are fixed
      // price distances (dollars at InpReferenceLot - see ManagePositionExit).
      double sampleTrailDist = InpTrailDollars * tickSize / (tickValue * InpReferenceLot);
      if(sampleTrailDist < stopsLevelPrice)
         PrintFormat("UnifiedTrader_EA: WARNING - InpTrailDollars=%.2f at the %.2f reference lot is a "
                     "%.5f price distance, tighter than this symbol's broker minimum stop distance "
                     "(%.5f). Once locked, the trailing stop may never be able to move - it will sit "
                     "at the InpTp1Dollars lock level instead, which is still a valid, protected exit, "
                     "just not a trailing one.",
                     InpTrailDollars, InpReferenceLot, sampleTrailDist, stopsLevelPrice);

      // Risk audit: how many losing Telegram-sourced trades does
      // InpMaxDailyLossPct actually absorb at the lot size that will really
      // be traded (InpFixedLot, or today's risk-sized lot if
      // InpUseRiskPercent)? Sizing up without checking this is how the
      // daily loss breaker quietly becomes the strategy - it halts the day
      // on ordinary variance rather than on a genuinely bad one. Purely
      // informational: never blocks trading on its own.
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity > 0.0 && InpMaxDailyLossPct > 0.0)
      {
         if(InpEnableTelegramSignals && InpTelegramUseSignalSl)
            PrintFormat("UnifiedTrader_EA: Telegram entries use the signal's own stop when it is $%.2f-$%.2f "
                        "from the entry (lot resized to keep %.2f%% risk), else the fixed $%.2f stop.",
                        InpSignalSlMinDistance, InpSignalSlMaxDistance, InpRiskPercent, InpSlDollars);
         double auditLots = PositionSizeLots();
         double slDistAudit = DollarsToPrice(InpSlDollars, InpReferenceLot);
         double riskMoney = auditLots * (slDistAudit / tickSize) * tickValue;
         double riskPct   = 100.0 * riskMoney / equity;
         double capMoney  = equity * InpMaxDailyLossPct / 100.0;
         double losses    = (riskMoney > 0.0) ? capMoney / riskMoney : 0.0;
         PrintFormat("UnifiedTrader_EA: risk audit - %.2f lots (%s) risks $%.2f (%.2f%% of $%.2f "
                     "equity); the %.1f%% daily loss breaker absorbs %.1f losing trades.",
                     auditLots, InpUseRiskPercent ? "risk-sized" : "fixed", riskMoney, riskPct, equity,
                     InpMaxDailyLossPct, losses);
         if(losses > 0.0 && losses < 3.0)
            PrintFormat("UnifiedTrader_EA: WARNING - the daily loss breaker stops new entries after "
                        "only %.1f losses. At several trades a day that can halt on ordinary variance "
                        "rather than a genuinely bad day. Reduce the lot size (or InpRiskPercent), "
                        "raise InpMaxDailyLossPct, or fund the account further.", losses);
      }
   }

   double gv;
   g_lastUpdateId = GlobalVariableGet(GV_LAST_UPDATE_ID, gv) ? (long)gv : 0;

   // Only ever true when InpControlChatId != 0 - deliberately NOT just
   // "whatever the persisted Global Variable says", because a real pause
   // set during an earlier session with remote control enabled must not
   // silently keep blocking every Telegram entry forever once
   // InpControlChatId is set back to 0: ResumeHab is only reachable
   // through InpControlChatId, so a stale g_telegramPaused=true with no
   // control chat configured would be a pause nothing could ever clear -
   // directly contradicting "InpControlChatId=0 disables this feature
   // entirely, no behavior change" (see file header). The persisted value
   // itself is left untouched either way, so re-enabling InpControlChatId
   // later correctly restores whatever real pause was last set.
   double gvPaused;
   g_telegramPaused = (InpControlChatId != 0)
                       && GlobalVariableGet(GV_TELEGRAM_PAUSED, gvPaused) && (gvPaused != 0.0);
   if(g_telegramPaused)
      Print("UnifiedTrader_EA: restored PAUSED state from a previous PauseHab/PauseTelHab - new "
            "Telegram entries remain BLOCKED until ResumeTelHab/ResumeHab (persisted across restarts "
            "- see file header's REMOTE CONTROL section).");

   // Same "only while InpControlChatId != 0" rule as g_telegramPaused just
   // above. Always rewrites the pause file from this state, so a file left
   // behind by an earlier session (or a dry-run test pause, which is never
   // persisted) can't keep app/main.py blocked with no resume reachable.
   double gvClaudePaused;
   g_claudePaused = (InpControlChatId != 0)
                     && GlobalVariableGet(GV_CLAUDE_PAUSED, gvClaudePaused) && (gvClaudePaused != 0.0);
   if(g_claudePaused)
      Print("UnifiedTrader_EA: restored PAUSED state from a previous PauseHab/PauseClaudeHab - new "
            "Claude entries remain BLOCKED until ResumeClaudeHab/ResumeHab.");
   WriteClaudePauseFile();

   trade.SetDeviationInPoints(30);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   // Restore today's anchor/latch/counter if this is a re-init on the same
   // trading day (timeframe switch, input edit, terminal restart) - resetting
   // them would re-anchor the 10% cap to an already-reduced equity and clear
   // a triggered breaker mid-day.
   g_currentDay = DateToDay(TimeCurrent());
   double gvDay, gvEq, gvHit, gvTrades;
   if(GlobalVariableGet(GV_DAY_ANCHOR, gvDay) && (datetime)(long)gvDay == g_currentDay
      && GlobalVariableGet(GV_DAY_START_EQ, gvEq) && gvEq > 0.0)
   {
      g_dayStartEquity = gvEq;
      g_dailyLossHit   = GlobalVariableGet(GV_DAY_LOSS_HIT, gvHit) && gvHit != 0.0;
      g_tradesToday    = GlobalVariableGet(GV_DAY_TRADES, gvTrades) ? (int)gvTrades : 0;
      PrintFormat("UnifiedTrader_EA: restored today's day-start equity %.2f%s (re-init on the same day).",
                  g_dayStartEquity, g_dailyLossHit ? " and the TRIGGERED daily loss breaker" : "");
   }
   else
   {
      g_tradesToday    = 0;
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_dailyLossHit   = false;
      SaveDayState();
   }

   // Only reset the one-shot startup-message flag when InpControlChatId
   // actually differs from whichever chat it was last sent to THIS
   // session - not on every OnInit(), which would resend it (and re-push
   // the keyboard) to the operator's phone on every unrelated input tweak
   // or recompile during live trading. Plain globals do NOT reset on their
   // own across an input-parameter-triggered reinit (no detach/reattach),
   // so without this check at all, switching InpControlChatId to a
   // different chat would instead leave that new chat never seeing the
   // startup message until it happened to receive some other confirmation
   // reply first - this reset only when it needs to.
   if(InpControlChatId != g_lastControlChatSentTo)
      g_sentControlStartupMsg = false;

   // The timer also has to run when ONLY remote control is wanted (Telegram
   // signal EXECUTION itself off) - ProcessSignal()'s own InpEnableTelegramSignals
   // check (see below) is what actually keeps that combination from opening
   // any position; the timer running is not itself permission to trade.
   if(InpEnableTelegramSignals || InpControlChatId != 0)
      EventSetTimer(MathMax(1, InpPollSeconds));

   PrintFormat("UnifiedTrader_EA: ready. symbol=%s lot=%s ref_lot=%.2f max_per_direction=%d (shared) "
               "telegram=%s (magic=%I64d, paused=%s) claude=%s (magic=%I64d, exit_style=%s "
               "breakeven_atr_mult=%.2f atr_period=%d decay_window_minutes=%.1f) sl=$%.2f tp1=$%.2f "
               "trail=$%.2f dryrun=%s control_chat=%s",
               _Symbol, InpUseRiskPercent ? StringFormat("%.1f%% risk", InpRiskPercent)
                                          : StringFormat("%.2f", InpFixedLot),
               InpReferenceLot, InpMaxPositionsPerDirection,
               InpEnableTelegramSignals ? "ON" : "off", InpTelegramMagicNumber,
               g_telegramPaused ? "true" : "false",
               InpEnableClaudeManagement ? "ON" : "off", InpClaudeMagicNumber,
               EnumToString(InpExitStyle), InpBreakevenAtrMult, InpAtrPeriod, InpDecayWindowMinutes,
               InpSlDollars, InpTp1Dollars, InpTrailDollars, InpDryRun ? "true" : "false",
               InpControlChatId != 0 ? "set" : "off");

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
   if(g_atrHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHandle);
   g_atrHandle = INVALID_HANDLE;   // globals survive a re-init - never reuse a released handle
   for(int h = 0; h < 10; h++)
   {
      if(g_xtrH[h] != INVALID_HANDLE)
         IndicatorRelease(g_xtrH[h]);
      g_xtrH[h] = INVALID_HANDLE;
   }
}

//+------------------------------------------------------------------+
//| XTR HTF class of slot 0 (InpXtrHtf1) or 1 (InpXtrHtf2) on its last |
//| CLOSED bar: 1 bullish, -1 bearish (EMA9 vs EMA21, RSI14 vs 50 and  |
//| the MACD histogram = line - EMA9(line) ALL agree), 0 mixed, -2 when |
//| the data isn't ready yet. Same rule as GoldTrader's           |
//| xtr_logic.py.                                                       |
//+------------------------------------------------------------------+
int XtrClassify(int slot)
{
   int base = slot * 5;
   double e9[1], e21[1], r[1], mLine[1], mSig[1];
   if(CopyBuffer(g_xtrH[base], 0, 1, 1, e9) != 1 || CopyBuffer(g_xtrH[base + 1], 0, 1, 1, e21) != 1
      || CopyBuffer(g_xtrH[base + 2], 0, 1, 1, r) != 1 || CopyBuffer(g_xtrH[base + 3], 0, 1, 1, mLine) != 1
      || CopyBuffer(g_xtrH[base + 4], 0, 1, 1, mSig) != 1)
      return(-2);
   bool emaBull  = (e9[0] > e21[0]);
   bool rsiBull  = (r[0] > 50.0);
   bool histBull = (mLine[0] - mSig[0] > 0.0);
   if(emaBull && rsiBull && histBull)
      return(1);
   if(!emaBull && !rsiBull && !histBull)
      return(-1);
   return(0);
}

//+------------------------------------------------------------------+
//| true (with `reason`) when M15 or H1 is CLEARLY against `dir`. An   |
//| HTF whose data isn't ready never blocks (logged).                  |
//+------------------------------------------------------------------+
bool XtrHtfOpposes(int dir, string &reason)
{
   reason = "";
   if(!InpXtrHtfFilter || g_xtrH[0] == INVALID_HANDLE)
      return(false);
   int want = (dir == DIR_BUY) ? 1 : -1;
   for(int k = 0; k < 2; k++)
   {
      int c = XtrClassify(k);
      ENUM_TIMEFRAMES tf = (k == 0) ? InpXtrHtf1 : InpXtrHtf2;
      string tfName = StringSubstr(EnumToString(tf), 7);
      if(c == -2)
      {
         PrintFormat("UnifiedTrader_EA: XTR HTF filter - %s data not ready, not blocking.", tfName);
         continue;
      }
      if(c == -want)
      {
         reason = StringFormat("XTR: %s clearly %s - never trade against a clear HTF", tfName,
                               c > 0 ? "bullish" : "bearish");
         return(true);
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| EXIT_BREAKEVEN_R_DECAY only (InpClaudeMagicNumber positions):      |
//| the ATR breakeven distance and whether the breakeven step is due.  |
//+------------------------------------------------------------------+
double BreakevenAtrDistance()
{
   if(g_atrHandle == INVALID_HANDLE)
      return(DBL_MAX);
   double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 0, 1, atrBuf) <= 0)
      return(DBL_MAX);
   return(InpBreakevenAtrMult * atrBuf[0]);
}

bool BreakevenDue(double profit)
{
   if(profit >= BreakevenAtrDistance())
      return(true);
   datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
   long decaySeconds = (long)(InpDecayWindowMinutes * 60);
   return((TimeCurrent() - openTime) >= decaySeconds);
}

//+------------------------------------------------------------------+
//| Small helpers                                                     |
//+------------------------------------------------------------------+
double PipSize() { return(SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 10.0); }

bool IsAllowedChat(long chatId)
{
   if(InpChannelId1 == 0 && InpChannelId2 == 0 && InpChannelId3 == 0) return(true);
   if(InpChannelId1 != 0 && chatId == InpChannelId1) return(true);
   if(InpChannelId2 != 0 && chatId == InpChannelId2) return(true);
   if(InpChannelId3 != 0 && chatId == InpChannelId3) return(true);
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
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_dailyLossHit   = false;
      SaveDayState();
      Print("UnifiedTrader_EA: new day - Telegram trade counter and daily loss breaker reset.");
   }
}

void SaveDayState()
{
   GlobalVariableSet(GV_DAY_ANCHOR, (double)(long)g_currentDay);
   GlobalVariableSet(GV_DAY_START_EQ, g_dayStartEquity);
   GlobalVariableSet(GV_DAY_LOSS_HIT, g_dailyLossHit ? 1.0 : 0.0);
   GlobalVariableSet(GV_DAY_TRADES, (double)g_tradesToday);
   GlobalVariablesFlush();
}

// InpMaxDailyLossPct==0 disables the check outright. Otherwise latches
// g_dailyLossHit for the rest of the broker server day once equity has dropped this
// many percent below g_dayStartEquity - existing open positions are left
// alone (this only ever withholds NEW Telegram-sourced entries, exactly
// like InpMaxTradesPerDay just above it in ProcessSignal), mirroring
// app/main.py's DayRoll.check_daily_limits() on the Claude side.
bool DailyLossBreakerActive()
{
   if(InpMaxDailyLossPct <= 0.0)
      return(false);
   if(g_dailyLossHit)
      return(true);
   if(g_dayStartEquity <= 0.0)
      return(false);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double movePct = (equity - g_dayStartEquity) / g_dayStartEquity * 100.0;
   if(-movePct >= InpMaxDailyLossPct)
   {
      g_dailyLossHit = true;
      SaveDayState();
      PrintFormat("UnifiedTrader_EA: daily loss breaker triggered (%.2f%% <= -%.2f%%) - no new "
                  "Telegram entries until the next server day.", movePct, InpMaxDailyLossPct);
      return(true);
   }
   return(false);
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
//| "STOPLOSS" / "STOP LOSS" / "STOP-LOSS" / "S/L" / whole-word "SL", |
//| else a bare "STOP" followed by a price ("Stop: 2350") - never the |
//| order type in "BUY STOP 2350" / "SELL STOP 2350".                 |
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
   int pos = fromPos;
   while(true)
   {
      idx = FindWholeWord(text, "STOP", pos);
      if(idx < 0) return(-1);
      // A label only when a price follows ("Stop: 4342.3", "stop at 4340") -
      // "stop hunt done", "don't stop" in commentary are not.
      double v;
      int    vEnd;
      if(!PrecededByWord(text, idx, "BUY") && !PrecededByWord(text, idx, "SELL")
         && ExtractNumberAt(text, idx + 4, StringLen(text), 10, v, vEnd) && v >= 100.0)
      {
         labelEnd = idx + 4;
         return(idx);
      }
      pos = idx + 4;
   }
   return(-1);
}

//+------------------------------------------------------------------+
//| True when `word` is the word right before idx on the same line    |
//| (only spaces or '-' between) - "SELL STOP", "BUY-STOP".            |
//+------------------------------------------------------------------+
bool PrecededByWord(const string &text, int idx, const string word)
{
   int j = idx - 1;
   while(j >= 0)
   {
      ushort ch = StringGetCharacter(text, j);
      if(ch != ' ' && ch != '-') break;
      j--;
   }
   int wlen  = StringLen(word);
   int start = j - wlen + 1;
   if(start < 0 || StringSubstr(text, start, wlen) != word)
      return(false);
   return(start == 0 || !IsWordCh(StringGetCharacter(text, start - 1)));
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
//| Best-effort parse. tps are logged only; sl is the signal's stop,  |
//| used by TelegramStopDistance() when InpTelegramUseSignalSl.       |
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
//| InpFixedLot, or a size derived from current equity when            |
//| InpUseRiskPercent is set - mirrors app/         |
//| executor.py's position_size(). The price distance InpSlDollars      |
//| implies at InpReferenceLot is held fixed (same value                |
//| PlaceCopiedOrder() already computes via DollarsToPrice(InpSlDollars,|
//| InpReferenceLot)), and the lot is solved for so that distance times |
//| that lot risks exactly InpRiskPercent% of current equity, then      |
//| clamped to [SYMBOL_VOLUME_MIN, SYMBOL_VOLUME_MAX, InpMaxLotSize]    |
//| and rounded down to the broker's own volume step.                   |
//+------------------------------------------------------------------+
double FixedSlDistance() { return(DollarsToPrice(InpSlDollars, InpReferenceLot)); }

// slDist: the trade's stop distance in price (0 = the fixed InpSlDollars
// stop) - a wider stop gets a smaller lot, so the trade still risks
// InpRiskPercent of equity.
double PositionSizeLots(double slDist = 0.0)
{
   if(!InpUseRiskPercent)
      return(InpFixedLot);

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(slDist <= 0.0)
      slDist = FixedSlDistance();
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(equity <= 0.0 || slDist <= 0.0 || tickValue <= 0.0 || tickSize <= 0.0)
      return(InpFixedLot);

   double lossPerLot = (slDist / tickSize) * tickValue;
   double lots = (equity * InpRiskPercent / 100.0) / lossPerLot;

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   // A plain MathFloor(lots/step) silently under-sizes by a whole step
   // whenever floating-point imprecision leaves the true ratio a hair
   // under an integer - the epsilon absorbs that without ever rounding a
   // genuinely-below-the-boundary value up a step (mirrors executor.
   // position_size()'s own fix).
   lots = MathFloor(lots / step + 0.000000001) * step;

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(lots < minLot)
   {
      // The broker's minimum lot is a hard floor - there is no smaller
      // order to place - so this clamps UP rather than skipping the
      // trade, but that silently risks more than InpRiskPercent% of
      // equity on a small account. Warn loudly rather than let that pass
      // quietly (mirrors executor.position_size()'s own warning).
      double actualRisk = minLot * lossPerLot;
      PrintFormat("UnifiedTrader_EA: WARNING - risk-sized lot (%.4f) is below the broker minimum "
                  "(%.2f) - using the minimum instead, which risks %.2f (%.2f%% of equity) rather "
                  "than the intended InpRiskPercent=%.2f%% (%.2f). Raise InpRiskPercent or fund "
                  "the account further to bring this back in line.",
                  lots, minLot, actualRisk, actualRisk / equity * 100.0, InpRiskPercent,
                  equity * InpRiskPercent / 100.0);
   }
   lots = MathMax(minLot, MathMin(lots, MathMin(maxLot, InpMaxLotSize)));
   return(NormalizeDouble(lots, 2));
}

//+------------------------------------------------------------------+
//| Money still at risk on this symbol, whatever placed it (Telegram, |
//| Claude, another EA, a manual trade) - the 10% cap is an ACCOUNT    |
//| cap: every open position from the current price to its stop, plus |
//| every pending order from its entry to its stop. A stop already    |
//| locked beyond the current price (profit protected) contributes 0; |
//| orders with no stop can't be priced and are skipped (this EA and  |
//| GoldTrader always set one). Mirrors app/ |
//| executor.py's open_risk_dollars().                                 |
//+------------------------------------------------------------------+
double OpenRiskMoney()
{
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   MqlTick tick;
   if(tickValue <= 0.0 || tickSize <= 0.0 || !SymbolInfoTick(_Symbol, tick))
      return(0.0);

   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0.0)
         continue;
      double dist = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? tick.bid - sl : sl - tick.ask;
      if(dist > 0.0)
         total += dist / tickSize * tickValue * PositionGetDouble(POSITION_VOLUME);
   }
   for(int j = OrdersTotal() - 1; j >= 0; j--)
   {
      ulong oticket = OrderGetTicket(j);
      if(oticket == 0 || OrderGetString(ORDER_SYMBOL) != _Symbol)
         continue;
      double osl = OrderGetDouble(ORDER_SL);
      if(osl <= 0.0)
         continue;
      double odist = MathAbs(OrderGetDouble(ORDER_PRICE_OPEN) - osl);
      total += odist / tickSize * tickValue * OrderGetDouble(ORDER_VOLUME_CURRENT);
   }
   return(total);
}

//+------------------------------------------------------------------+
//| Makes InpMaxDailyLossPct a real cap rather than only a trigger:   |
//| DailyLossBreakerActive() fires only AFTER equity is already down  |
//| the full %, and never closes anything - so several concurrent     |
//| risk-sized trades could otherwise all stop out together past it.  |
//| Returns "" if today's drawdown + OpenRiskMoney() + this new       |
//| entry's own risk fits within InpMaxDailyLossPct of the day's      |
//| starting equity, else the reason to skip the signal.              |
//+------------------------------------------------------------------+
string DailyRiskBudgetReason(double newLots, double slDist = 0.0)
{
   if(InpMaxDailyLossPct <= 0.0 || g_dayStartEquity <= 0.0)
      return("");
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(slDist <= 0.0)
      slDist = FixedSlDistance();
   if(tickValue <= 0.0 || tickSize <= 0.0 || slDist <= 0.0)
      return("");

   double newRisk   = slDist / tickSize * tickValue * newLots;
   double budget    = g_dayStartEquity * InpMaxDailyLossPct / 100.0;
   double drawdown  = MathMax(0.0, g_dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY));
   double committed = drawdown + OpenRiskMoney() + newRisk;
   if(committed > budget + 0.000000001)
      return(StringFormat("daily loss budget: drawdown + open risk + this trade = %.2f, over the "
                          "%.2f%% cap (%.2f)", committed, InpMaxDailyLossPct, budget));
   return("");
}

//+------------------------------------------------------------------+
//| Small accounts on high leverage: "" if, after this entry, the free |
//| margin still covers what every open stop plus this one could lose, |
//| else the reason to skip - so a run of losses reaches the stops, not |
//| the broker's margin call / stop-out. Mirrors app/executor.py's      |
//| margin_guard_reason(). Unknown margin never blocks.                 |
//+------------------------------------------------------------------+
string MarginGuardReason(bool isBuy, double newLots, double slDist = 0.0)
{
   if(!InpMarginGuard || newLots <= 0.0)
      return("");
   double price = SymbolInfoDouble(_Symbol, isBuy ? SYMBOL_ASK : SYMBOL_BID);
   double need = 0.0;
   if(price <= 0.0 || !OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, newLots, price, need))
      return("");
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(slDist <= 0.0)
      slDist = FixedSlDistance();
   if(tickValue <= 0.0 || tickSize <= 0.0 || slDist <= 0.0)
      return("");
   double worst = OpenRiskMoney() + slDist / tickSize * tickValue * newLots;
   double freeAfter = AccountInfoDouble(ACCOUNT_MARGIN_FREE) - need;
   if(freeAfter < worst)
      return(StringFormat("margin guard: free margin after this trade %.2f would not cover %.2f if every "
                          "open stop and this one were hit", freeAfter, worst));
   return("");
}

//+------------------------------------------------------------------+
//| Trading hours for NEW Telegram entries: InpTradeHours in the local|
//| time InpTradeUtcOffsetHours (Oman = UTC+4, no daylight saving),   |
//| Monday-Friday when InpTradeWeekdaysOnly. (Claude's entries have   |
//| their own tested window in app/config.py.) Close / breakeven /    |
//| cancel commands and the management of open positions (SL lock,    |
//| trail) run at any time: only new entries wait for the window.     |
//+------------------------------------------------------------------+
int g_tradeWinStart[];
int g_tradeWinEnd[];

bool ParseHHMM(string s, int &minutes)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   string hm[];
   if(StringSplit(s, ':', hm) != 2 || StringLen(hm[0]) < 1 || StringLen(hm[0]) > 2 || StringLen(hm[1]) != 2)
      return(false);
   for(int k = 0; k < 2; k++)
      for(int i = 0; i < StringLen(hm[k]); i++)
         if(!IsDigitCh(StringGetCharacter(hm[k], i)))
            return(false);
   int h = (int)StringToInteger(hm[0]);
   int m = (int)StringToInteger(hm[1]);
   if(h > 24 || m > 59 || h * 60 + m > 1440)
      return(false);
   minutes = h * 60 + m;
   return(true);
}

bool ParseTradeHours(const string spec)
{
   ArrayResize(g_tradeWinStart, 0);
   ArrayResize(g_tradeWinEnd, 0);
   string s = spec;
   StringTrimLeft(s);
   StringTrimRight(s);
   if(StringLen(s) == 0)
      return(true);                                  // any time
   string parts[];
   int n = StringSplit(s, ',', parts);
   for(int i = 0; i < n; i++)
   {
      string p = parts[i];
      StringTrimLeft(p);
      StringTrimRight(p);
      if(StringLen(p) == 0)
         continue;
      string ab[];
      int a = 0, b = 0;
      if(StringSplit(p, '-', ab) != 2 || !ParseHHMM(ab[0], a) || !ParseHHMM(ab[1], b))
         return(false);
      int k = ArraySize(g_tradeWinStart);
      ArrayResize(g_tradeWinStart, k + 1);
      ArrayResize(g_tradeWinEnd, k + 1);
      g_tradeWinStart[k] = a;
      g_tradeWinEnd[k]   = b;
   }
   return(ArraySize(g_tradeWinStart) > 0);
}

// "" when a new entry may be taken now, else why not.
string TradeHoursReason()
{
   long offset = (long)MathRound(InpTradeUtcOffsetHours * 3600.0);
   datetime local = (datetime)((long)TimeGMT() + offset);
   MqlDateTime t;
   TimeToStruct(local, t);
   string zone = "UTC" + (InpTradeUtcOffsetHours >= 0.0 ? "+" : "")
                 + DoubleToString(InpTradeUtcOffsetHours, MathMod(InpTradeUtcOffsetHours, 1.0) == 0.0 ? 0 : 1);
   if(InpTradeWeekdaysOnly && (t.day_of_week == 0 || t.day_of_week == 6))
      return(StringFormat("not a trading day (weekend, %s); new entries Monday-Friday only", zone));
   int n = ArraySize(g_tradeWinStart);
   if(n == 0)
      return("");
   int minute = t.hour * 60 + t.min;
   for(int i = 0; i < n; i++)
   {
      int a = g_tradeWinStart[i], b = g_tradeWinEnd[i];
      if(a <= b) { if(minute >= a && minute < b) return(""); }
      else if(minute >= a || minute < b) return("");  // a window over midnight, e.g. 20:00-02:00
   }
   return(StringFormat("outside trading hours (%02d:%02d %s; new entries only %s)", t.hour, t.min, zone, InpTradeHours));
}

//+------------------------------------------------------------------+
//| Open positions PLUS pending limit orders on this symbol/direction |
//| across BOTH magic numbers together - the shared cap - regardless  |
//| of which mode(s) are currently enabled (see file header's         |
//| "SHARED POSITION CAP"). Pending orders count too, not just filled |
//| positions: several wide-zone Telegram signals can each place a    |
//| BuyLimit/SellLimit that fills only much later, and if a single    |
//| fast move then swept through every zone at once, counting filled  |
//| positions alone would let far more than InpMaxPositionsPerDirection|
//| land simultaneously - the cap needs to bound WORST-CASE exposure   |
//| (positions + still-pending commitments), not just what's already   |
//| filled right now.                                                  |
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
   // Pending orders only ever come from this EA's own Telegram side
   // (PlaceCopiedOrder only ever sends BuyLimit/SellLimit or an immediate
   // market order, never a resting order under InpClaudeMagicNumber - see
   // the file header, Python always sends market orders) - but this counts
   // by magic like the position loop above rather than assuming that, so
   // it stays correct even if that ever changes.
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      long magic = (long)OrderGetInteger(ORDER_MAGIC);
      if(magic != InpTelegramMagicNumber && magic != InpClaudeMagicNumber) continue;
      long type = OrderGetInteger(ORDER_TYPE);
      int orderDir;
      if(type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY_STOP_LIMIT)
         orderDir = DIR_BUY;
      else if(type == ORDER_TYPE_SELL_LIMIT || type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL_STOP_LIMIT)
         orderDir = DIR_SELL;
      else
         continue;
      if(orderDir == direction) count++;
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
//| Shared loop for CloseAllMine()/CloseAllClaudeMine() below -       |
//| closes every open position on THIS symbol under `magic`. `label`  |
//| is cosmetic only (which side the dry-run/failure log line names). |
//| Returns how many were ACTUALLY closed (or, under InpDryRun, would |
//| be) - a failed trade.PositionClose() (requote, busy trade context, |
//| broker reject) is logged and does NOT count, so a PauseHab/        |
//| PauseTelHab/PauseClaudeHab summary never overstates how much       |
//| exposure is really gone during what's usually an emergency action. |
//+------------------------------------------------------------------+
int ClosePositionsByMagic(long magic, const string &label)
{
   int closed = 0;
   // The exit deal carries CTrade's magic - without this a Claude position
   // would close under the Telegram magic (the last one set), and Python's
   // magic-filtered closed-trade history (digests, ML labels) would lose it.
   trade.SetExpertMagicNumber(magic);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != magic) continue;
      if(InpDryRun)
      {
         PrintFormat("UnifiedTrader_EA: [DRY-RUN] would close %s ticket %I64u", label, ticket);
         closed++;
      }
      else if(trade.PositionClose(ticket))
         closed++;
      else
         PrintFormat("UnifiedTrader_EA: FAILED to close %s ticket %I64u (retcode=%d %s) - still "
                     "OPEN, check manually.", label, ticket, trade.ResultRetcode(),
                     trade.ResultRetcodeDescription());
   }
   return(closed);
}

//+------------------------------------------------------------------+
//| CLOSE/CANCEL (from the signal channel) and PauseHab/PauseTelHab   |
//| (from InpControlChatId) all act only on this EA's own Telegram-   |
//| sourced positions and pending orders - never on InpClaudeMagic    |
//| Number ones, which are Python's to manage (see file header).      |
//+------------------------------------------------------------------+
int CloseAllMine()
{
   return(ClosePositionsByMagic(InpTelegramMagicNumber, "Telegram"));
}

int CancelAllPendingMine()
{
   int cancelled = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) != InpTelegramMagicNumber) continue;
      if(InpDryRun)
      {
         PrintFormat("UnifiedTrader_EA: [DRY-RUN] would cancel pending order %I64u", ticket);
         cancelled++;
      }
      else if(trade.OrderDelete(ticket))
         cancelled++;
      else
         PrintFormat("UnifiedTrader_EA: FAILED to cancel pending order %I64u (retcode=%d %s) - "
                     "still PENDING, check manually.", ticket, trade.ResultRetcode(),
                     trade.ResultRetcodeDescription());
   }
   return(cancelled);
}

//+------------------------------------------------------------------+
//| PauseClaudeHab/PauseHab only - closes InpClaudeMagicNumber        |
//| positions. Unlike CloseAllMine() there is no matching "cancel      |
//| pending" step: Python always sends market orders, never a resting |
//| order under this magic (see file header) - nothing to cancel.     |
//+------------------------------------------------------------------+
int CloseAllClaudeMine()
{
   return(ClosePositionsByMagic(InpClaudeMagicNumber, "Claude"));
}

//+------------------------------------------------------------------+
//| Sets g_telegramPaused for THIS run (so the pause gate in           |
//| ProcessSignal is actually exercisable while dry-run testing this   |
//| feature, per SETUP.md's own "leave InpDryRun=true until you trust  |
//| it" workflow), but under InpDryRun never persists it to the        |
//| terminal Global Variable - so a restart/reattach, or flipping      |
//| InpDryRun to false and reattaching, always starts unpaused rather   |
//| than silently inheriting a pause that was only ever a dry-run test. |
//| Outside InpDryRun, persists immediately (not deferred to OnDeinit)  |
//| so a real pause survives a crash/kill, not just a clean restart.    |
//+------------------------------------------------------------------+
void SetTelegramPaused(bool paused)
{
   g_telegramPaused = paused;
   if(InpDryRun)
   {
      PrintFormat("UnifiedTrader_EA: [DRY-RUN] telegram-paused=%s for this run only - new Telegram "
                  "OPEN signals will be gated accordingly while this run lasts, but nothing is "
                  "persisted, so a restart/reattach (or flipping InpDryRun off) starts unpaused.",
                  paused ? "true" : "false");
      return;
   }
   GlobalVariableSet(GV_TELEGRAM_PAUSED, paused ? 1.0 : 0.0);
   GlobalVariablesFlush();
}

//+------------------------------------------------------------------+
//| Claude-side twin of SetTelegramPaused(): same dry-run rule (the    |
//| state applies for this run but is never persisted), plus it writes |
//| the pause file app/main.py reads - see WriteClaudePauseFile().   |
//+------------------------------------------------------------------+
void SetClaudePaused(bool paused)
{
   g_claudePaused = paused;
   if(InpDryRun)
      PrintFormat("UnifiedTrader_EA: [DRY-RUN] claude-paused=%s for this run only - not persisted, "
                  "so a restart/reattach starts unpaused.", paused ? "true" : "false");
   else
   {
      GlobalVariableSet(GV_CLAUDE_PAUSED, paused ? 1.0 : 0.0);
      GlobalVariablesFlush();   // survive a crash/VPS reboot, not just a clean exit
   }
   WriteClaudePauseFile();
}

//+------------------------------------------------------------------+
//| Writes "paused" or "running" to InpClaudePauseFilename in the      |
//| shared Common\Files folder - the only place app/main.py can read |
//| (the reverse direction of the Why button's verdict file). main.py   |
//| skips its evaluation while it says "paused"; a missing file means    |
//| "running". An empty filename disables the hand-off.                 |
//+------------------------------------------------------------------+
void WriteClaudePauseFile()
{
   // Never from the Strategy Tester/optimizer: FILE_COMMON there is the REAL
   // shared folder, so a test pass (which never sees the live pause) would
   // write "running" and silently lift a live PauseClaudeHab.
   if(StringLen(InpClaudePauseFilename) == 0 || MQLInfoInteger(MQL_TESTER)
      || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   // Atomic (temp file + rename): app/main.py must never catch it empty.
   if(!CommonFileWriteAtomic(InpClaudePauseFilename, g_claudePaused ? "paused" : "running"))
      PrintFormat("UnifiedTrader_EA: WARNING - could not write the Claude pause file %s (error %d) - "
                  "app/main.py will not see the current pause state.",
                  InpClaudePauseFilename, GetLastError());
}

//+------------------------------------------------------------------+
//| Common tail for every ProcessControlCommand() branch below - logs |
//| `summary` to the terminal (prefixed) and sends it, unprefixed, to |
//| InpControlChatId as the command's confirmation reply. A single     |
//| shared place for this pairing so the two can never drift apart     |
//| between branches (e.g. one gaining a retry or rate-limit guard      |
//| the others don't).                                                  |
//+------------------------------------------------------------------+
void SendControlReply(const string summary)
{
   Print("UnifiedTrader_EA: " + summary);
   TelegramSendMessage(InpControlChatId, summary);
}

//+------------------------------------------------------------------+
//| Why button: reads the shared Common\Files text file               |
//| app/main.py writes the latest Claude verdict to (see            |
//| mt5_gateway.write_common_file(), config.py's last_verdict_filename |
//| - MUST match InpLastVerdictFilename). MQL5's file sandbox           |
//| otherwise only sees THIS EA's own MQL5/Files directory, never       |
//| Python's decisions.csv directly - the shared Common\Files folder is |
//| the one place both processes can read/write, which is why this      |
//| cross-process hand-off exists at all rather than parsing the CSV.   |
//| Never raises: FileOpen() failing (file not written yet, filename    |
//| mismatch, GoldTrader not running) just means no verdict text, |
//| reported to the operator as such rather than as a crash.            |
//+------------------------------------------------------------------+
string ReadLastVerdictFile()
{
   if(StringLen(InpLastVerdictFilename) == 0)
      return("");
   int handle = FileOpen(InpLastVerdictFilename, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return("");
   string text = "";
   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      text += (StringLen(text) > 0 ? "\n" : "") + line;
   }
   FileClose(handle);
   return(text);
}

//+------------------------------------------------------------------+
//| Closed-trade stats for positions of `magic` on this symbol whose  |
//| exit deal is at or after `fromServer` (trade-server time). Net    |
//| P/L per position = profit + swap + commission + fee over ALL its  |
//| deals, so an entry-side commission is counted too. Positions are |
//| identified by their ENTRY deal's magic (exit deals triggered by   |
//| SL/TP are not relied on to carry it), from a history window that  |
//| starts 30 days earlier so entries opened before `fromServer` are  |
//| still found.                                                       |
//+------------------------------------------------------------------+
void ClosedStats(datetime fromServer, long magic, ClosedStatsT &st)
{
   st.trades = 0;
   st.wins   = 0;
   st.losses = 0;
   st.pnl    = 0.0;
   if(!HistorySelect(fromServer - 30 * 86400, TimeCurrent() + 3600))
      return;

   int total = HistoryDealsTotal();
   ulong ourPositions[];
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0 || HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN
         || HistoryDealGetInteger(deal, DEAL_MAGIC) != magic)
         continue;
      int k = ArraySize(ourPositions);
      ArrayResize(ourPositions, k + 1);
      ourPositions[k] = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   }

   int n = ArraySize(ourPositions);
   double pnl[];
   bool   closedInWindow[];
   ArrayResize(pnl, n);
   ArrayResize(closedInWindow, n);
   for(int k = 0; k < n; k++)
   {
      pnl[k] = 0.0;
      closedInWindow[k] = false;
   }
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      ulong pos = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      int idx = -1;
      for(int k = 0; k < n; k++)
         if(ourPositions[k] == pos) { idx = k; break; }
      if(idx < 0)
         continue;
      pnl[idx] += HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP)
                  + HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if((entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
         && (datetime)HistoryDealGetInteger(deal, DEAL_TIME) >= fromServer)
         closedInWindow[idx] = true;
   }
   for(int k = 0; k < n; k++)
   {
      if(!closedInWindow[k])
         continue;
      st.trades++;
      st.pnl += pnl[k];
      if(pnl[k] > 0.0)      st.wins++;
      else if(pnl[k] < 0.0) st.losses++;
   }
}

string StatsLine(const string label, const ClosedStatsT &st)
{
   if(st.trades == 0)
      return(label + ": no closed trades");
   return(StringFormat("%s: %+.2f over %d trade(s), %dW/%dL, win %.0f%%", label, st.pnl, st.trades,
                       st.wins, st.losses, 100.0 * st.wins / st.trades));
}

//+------------------------------------------------------------------+
//| Stats button: equity, balance, open P/L, closed P/L and win rate  |
//| for today (trade-server day), 7 and 30 days, per source, plus how |
//| much of the daily loss budget is used.                             |
//+------------------------------------------------------------------+
string BuildStatsText()
{
   UpdateDailyTracking();
   string ccy     = AccountInfoString(ACCOUNT_CURRENCY);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   int    openTel = 0, openClaude = 0;
   double floatTel = 0.0, floatClaude = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      double fl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(magic == InpTelegramMagicNumber)    { openTel++;    floatTel += fl; }
      else if(magic == InpClaudeMagicNumber) { openClaude++; floatClaude += fl; }
   }

   ClosedStatsT tel, cla, all;
   datetime periods[3];
   string   labels[3] = {"Today", "Last 7 days", "Last 30 days"};
   periods[0] = g_currentDay;
   periods[1] = TimeCurrent() - 7 * 86400;
   periods[2] = TimeCurrent() - 30 * 86400;

   string text = StringFormat("%s account - %s UTC%s\nEquity %.2f %s | Balance %.2f | "
                              "Open P/L %+.2f (%d Telegram, %d Claude)",
                              _Symbol, EconTimeText(TimeGMT()), InpDryRun ? " (EA in DRY-RUN)" : "",
                              equity, ccy, balance, floatTel + floatClaude, openTel, openClaude);
   for(int p = 0; p < 3; p++)
   {
      ClosedStats(periods[p], InpTelegramMagicNumber, tel);
      ClosedStats(periods[p], InpClaudeMagicNumber, cla);
      all.trades = tel.trades + cla.trades;
      all.wins   = tel.wins + cla.wins;
      all.losses = tel.losses + cla.losses;
      all.pnl    = tel.pnl + cla.pnl;
      text += "\n\n" + StatsLine(labels[p], all);
      if(all.trades > 0)
         text += "\n  " + StatsLine("Telegram", tel) + "\n  " + StatsLine("Claude", cla);
   }

   if(InpMaxDailyLossPct > 0.0 && g_dayStartEquity > 0.0)
   {
      double lostPct = 100.0 * MathMax(0.0, g_dayStartEquity - equity) / g_dayStartEquity;
      double riskPct = 100.0 * OpenRiskMoney() / g_dayStartEquity;
      text += StringFormat("\n\nDaily loss budget: %.1f%% lost + %.1f%% at risk of the %.1f%% cap%s",
                           lostPct, riskPct, InpMaxDailyLossPct,
                           g_dailyLossHit ? " (breaker TRIGGERED)" : "");
   }
   text += StringFormat("\nNew entries: Telegram %s, Claude %s",
                        g_telegramPaused ? "PAUSED" : "running", g_claudePaused ? "PAUSED" : "running");
   return(text);
}

//+------------------------------------------------------------------+
//| Sends any queued trade-closed messages (see OnTradeTransaction).  |
//| Called from OnTimer, never from OnTradeTransaction itself, so a    |
//| slow WebRequest never runs inside the trade event.                 |
//+------------------------------------------------------------------+
void FlushNotifyQueue()
{
   int n = ArraySize(g_notifyQueue);
   if(n == 0)
      return;
   // ONE message per timer tick, however many trades closed: each
   // WebRequest blocks this thread (OnTick's trailing waits behind it), so
   // a burst of stop-outs must not become a burst of sequential sends.
   string combined = "";
   for(int i = 0; i < n; i++)
   {
      string part = (StringLen(combined) > 0 ? "\n\n" : "") + g_notifyQueue[i];
      if(StringLen(combined) + StringLen(part) > 3500)
      {
         combined += StringFormat("\n\n(+%d more - tap Stats for totals)", n - i);
         break;
      }
      combined += part;
   }
   ArrayResize(g_notifyQueue, 0);
   TelegramSendMessage(InpControlChatId, combined);
}

//+------------------------------------------------------------------+
//| Remote control commands from InpControlChatId - see file header's |
//| REMOTE CONTROL section for the full semantics of each. Matched by  |
//| EXACT text (trimmed, case-insensitive), not substring - unlike     |
//| trading-signal parsing, a false-positive match here closes real    |
//| positions.                                                          |
//+------------------------------------------------------------------+
void ProcessControlCommand(const string &rawText)
{
   string cmd = rawText;
   StringTrimLeft(cmd);
   StringTrimRight(cmd);
   StringToUpper(cmd);

   if(cmd == "PAUSEHAB")
   {
      Print("UnifiedTrader_EA: PauseHab received - closing this symbol's positions under both "
            "magics and pausing new Telegram and Claude entries.");
      int telClosed    = CloseAllMine();
      int telCancelled = CancelAllPendingMine();
      int claudeClosed = CloseAllClaudeMine();
      SetTelegramPaused(true);
      SetClaudePaused(true);
      SendControlReply(StringFormat(
                  "PauseHab %s %d Telegram position(s), %d pending order(s), %d Claude "
                  "position(s). New Telegram AND Claude entries are now BLOCKED until ResumeHab.",
                  InpDryRun ? "[DRY-RUN] would clear" : "done - cleared",
                  telClosed, telCancelled, claudeClosed));
      return;
   }
   if(cmd == "RESUMEHAB")
   {
      SetTelegramPaused(false);
      SetClaudePaused(false);
      SendControlReply("ResumeHab done - new Telegram and Claude entries re-enabled (Telegram still "
                        "subject to InpEnableTelegramSignals). Nothing was reopened.");
      return;
   }
   if(cmd == "RESUMETELHAB")
   {
      SetTelegramPaused(false);
      SendControlReply("ResumeTelHab done - new Telegram entries re-enabled (still subject to "
                        "InpEnableTelegramSignals). Claude's pause state is unchanged.");
      return;
   }
   if(cmd == "RESUMECLAUDEHAB")
   {
      SetClaudePaused(false);
      SendControlReply("ResumeClaudeHab done - new Claude entries re-enabled from app/main.py's "
                        "next evaluation cycle. Telegram's pause state is unchanged.");
      return;
   }
   if(cmd == "PAUSETELHAB")
   {
      Print("UnifiedTrader_EA: PauseTelHab received - closing Telegram-sourced positions/orders and "
            "pausing new Telegram entries. Claude-sourced positions are untouched.");
      int telClosed    = CloseAllMine();
      int telCancelled = CancelAllPendingMine();
      SetTelegramPaused(true);
      SendControlReply(StringFormat(
                  "PauseTelHab %s %d position(s), %d pending order(s). New Telegram entries are "
                  "now BLOCKED until ResumeTelHab or ResumeHab.",
                  InpDryRun ? "[DRY-RUN] would clear" : "done - cleared",
                  telClosed, telCancelled));
      return;
   }
   if(cmd == "PAUSECLAUDEHAB")
   {
      Print("UnifiedTrader_EA: PauseClaudeHab received - closing Claude-sourced positions and "
            "pausing new Claude entries. Telegram-sourced positions and its new-entry state are "
            "untouched.");
      int claudeClosed = CloseAllClaudeMine();
      SetClaudePaused(true);
      SendControlReply(StringFormat(
                  "PauseClaudeHab %s %d Claude position(s). New Claude entries are now BLOCKED "
                  "until ResumeClaudeHab or ResumeHab (app/main.py skips its evaluation while "
                  "paused).",
                  InpDryRun ? "[DRY-RUN] would close" : "done - closed",
                  claudeClosed));
      return;
   }
   if(cmd == "STATS")
   {
      SendControlReply(BuildStatsText());
      return;
   }
   if(cmd == "NEWS")
   {
      SendControlReply(EconSummary(InpNewsCurrencies, (int)InpNewsMinImportance, 12, 24));
      return;
   }
   if(cmd == "WHY")
   {
      string verdictText = ReadLastVerdictFile();
      if(StringLen(verdictText) == 0)
         SendControlReply("No Claude verdict on file yet - either GoldTrader (start.bat) "
                           "hasn't completed an evaluation cycle since this EA started, or "
                           "InpLastVerdictFilename doesn't match config.py's last_verdict_filename.");
      else
         SendControlReply("Last Claude verdict:\n" + verdictText);
      return;
   }
   SendControlReply(StringFormat(
               "Unrecognized control command: '%s' - tap a button below, or send exactly one of "
               "PauseHab / ResumeHab / PauseTelHab / ResumeTelHab / PauseClaudeHab / "
               "ResumeClaudeHab / Stats / News / Why.", rawText));
}

//+------------------------------------------------------------------+
//| Where a Telegram-sourced order goes: LIMIT at the zone edge if     |
//| price hasn't reached the zone, MARKET if already inside it; false  |
//| (failType STALE_SKIPPED / NO_TICK) if price is already through it. |
//+------------------------------------------------------------------+
bool PlanCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                      double &orderPrice, bool &isPending, string &failType)
{
   failType   = "";
   orderPrice = 0.0;
   isPending  = false;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("UnifiedTrader_EA: no tick available, cannot place order.");
      failType = "NO_TICK";
      return(false);
   }
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double buffer    = 3.0 * point;
   double zoneEntry = isBuy ? upperBound : lowerBound;

   if(isBuy)
   {
      if(tick.ask > zoneEntry + buffer)      { orderPrice = zoneEntry; isPending = true; }
      else if(tick.ask < lowerBound - buffer)
      {
         PrintFormat("UnifiedTrader_EA: BUY zone %.2f-%.2f already breached (ask=%.2f) - stale, skipping.",
                     lowerBound, upperBound, tick.ask);
         failType = "STALE_SKIPPED";
         return(false);
      }
      else orderPrice = tick.ask;
   }
   else
   {
      if(tick.bid < zoneEntry - buffer)      { orderPrice = zoneEntry; isPending = true; }
      else if(tick.bid > upperBound + buffer)
      {
         PrintFormat("UnifiedTrader_EA: SELL zone %.2f-%.2f already breached (bid=%.2f) - stale, skipping.",
                     lowerBound, upperBound, tick.bid);
         failType = "STALE_SKIPPED";
         return(false);
      }
      else orderPrice = tick.bid;
   }
   orderPrice = NormalizeDouble(orderPrice, digits);
   return(true);
}

//+------------------------------------------------------------------+
//| The Telegram trade's stop distance (price): the signal's own stop  |
//| when InpTelegramUseSignalSl and it is on the right side, between   |
//| InpSignalSlMinDistance and InpSignalSlMaxDistance from orderPrice; |
//| otherwise the fixed InpSlDollars stop. `note` says which and why.  |
//+------------------------------------------------------------------+
double TelegramStopDistance(const SignalMsg &msg, bool isBuy, double orderPrice, string &note)
{
   double fixedDist = FixedSlDistance();
   note = "";
   if(!InpTelegramUseSignalSl)
      return(fixedDist);
   if(!msg.hasSl || msg.sl <= 0.0)
   {
      note = StringFormat("no stop in the signal - fixed $%.2f stop", fixedDist);
      return(fixedDist);
   }
   double dist = isBuy ? orderPrice - msg.sl : msg.sl - orderPrice;
   if(dist <= 0.0)
   {
      note = StringFormat("signal stop %.2f is on the wrong side of the entry %.2f - fixed $%.2f stop",
                          msg.sl, orderPrice, fixedDist);
      return(fixedDist);
   }
   if(dist < InpSignalSlMinDistance || dist > InpSignalSlMaxDistance)
   {
      note = StringFormat("signal stop %.2f is $%.2f from the entry (allowed $%.2f-$%.2f) - fixed $%.2f stop",
                          msg.sl, dist, InpSignalSlMinDistance, InpSignalSlMaxDistance, fixedDist);
      return(fixedDist);
   }
   note = StringFormat("signal stop %.2f ($%.2f from the entry)", msg.sl, dist);
   return(dist);
}

//+------------------------------------------------------------------+
//| Sends the planned Telegram order: stop slDist from orderPrice, the |
//| lot already sized from that stop (PositionSizeLots(slDist)). No    |
//| broker TP (tp=0.0, lock-then-trail exit design - see file header). |
//+------------------------------------------------------------------+
bool PlaceCopiedOrder(bool isBuy, double orderPrice, bool isPending, double slDist, double lots,
                       string &outOrderType, long &outTicket, int &outRetcode)
{
   outOrderType = "";
   outTicket    = 0;
   outRetcode   = 0;
   int    digits  = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string comment = UNIFIED_TELEGRAM_SOURCE;
   if(slDist <= 0.0)
   {
      // DollarsToPrice() returns 0.0 if the broker isn't fully quoting tick
      // value/size yet (startup/reconnect hiccup) - sending an order with
      // sl==orderPrice in that case would be an instant stop-out (or an
      // outright broker rejection for a zero-distance stop), defeating the
      // fixed-risk design this whole EA depends on. Refuse instead, exactly
      // like ManagePositionExit() already refuses to touch a position under
      // the same condition.
      Print("UnifiedTrader_EA: stop distance is 0 (tick value/size not available yet) - "
            "refusing to place an order with an undefined stop-loss distance.");
      outOrderType = "NO_TICK_VALUE";
      return(false);
   }
   double sl = NormalizeDouble(isBuy ? orderPrice - slDist : orderPrice + slDist, digits);
   double tp = 0.0;   // no broker TP - see file header's shared exit design

   outOrderType = isPending ? "LIMIT" : "MARKET";

   if(InpDryRun)
   {
      PrintFormat("UnifiedTrader_EA: [DRY-RUN] would place %s %s %.2f lots @ %.2f sl=%.2f "
                  "(no broker TP - locks at $%.2f via SL)",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", lots, orderPrice, sl,
                  InpTp1Dollars);
      return(true);
   }

   trade.SetExpertMagicNumber(InpTelegramMagicNumber);
   bool ok;
   if(isPending)
      ok = isBuy ? trade.BuyLimit(lots, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
                 : trade.SellLimit(lots, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else
      ok = isBuy ? trade.Buy(lots, _Symbol, orderPrice, sl, tp, comment)
                 : trade.Sell(lots, _Symbol, orderPrice, sl, tp, comment);

   outRetcode = (int)trade.ResultRetcode();
   outTicket  = (long)trade.ResultOrder();

   if(ok)
   {
      g_tradesToday++;
      SaveDayState();
      PrintFormat("UnifiedTrader_EA: %s %s placed - %.2f lots @ %.2f sl=%.2f ticket=%I64u",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", lots, orderPrice, sl,
                  outTicket);
   }
   else
   {
      PrintFormat("UnifiedTrader_EA: order failed. retcode=%d desc=%s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());
      outOrderType = "REJECTED";
   }

   // false on a broker rejection: the signal log (and the dashboard) must
   // never show "copied" for an order that does not exist.
   return(ok);
}

//+------------------------------------------------------------------+
//| Lock-then-trail exit for one position of either source (see the   |
//| file header's EXITS). EXIT_BREAKEVEN_R_DECAY's extra breakeven step|
//| only ever                                                          |
//| applies when magic == InpClaudeMagicNumber (see useBreakevenDecay  |
//| below) - Telegram-sourced positions always use plain lock-then-    |
//| trail, regardless of InpExitStyle.                                  |
//+------------------------------------------------------------------+
void ManagePositionExit(ulong ticket, long magic)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if((long)PositionGetInteger(POSITION_MAGIC) != magic)
      return;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      return;

   // At the REFERENCE lot (InpReferenceLot), not this position's own volume -
   // the SL is already a fixed price distance (InpSlDollars at
   // InpReferenceLot); converting TP1/trail at a risk-sized position's real
   // volume would shrink them as the lot grows (risking e.g. $200 at the
   // stop to lock only $6). Fixed price distances keep the SL : TP1 : trail
   // shape identical at every lot size, for both sources.
   double tp1Dist = DollarsToPrice(InpTp1Dollars, InpReferenceLot);
   double trailDist = DollarsToPrice(InpTrailDollars, InpReferenceLot);
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

   // EXIT_BREAKEVEN_R_DECAY is a Claude-management-side setting only (see the
   // input group comment) - Telegram-sourced positions always use the plain
   // lock-then-trail shape below, regardless of InpExitStyle.
   bool useBreakevenDecay = (InpExitStyle == EXIT_BREAKEVEN_R_DECAY && magic == InpClaudeMagicNumber);

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
         else if(useBreakevenDecay)
         {
            bool atBreakeven = (currentSl > 0.0 && currentSl >= openPrice - point);
            if(!atBreakeven && (tick.bid - openPrice) >= minStopDist && BreakevenDue(profit))
            {
               newSl = NormalizeDouble(openPrice, digits);
               changeSl = true;
            }
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
         else if(useBreakevenDecay)
         {
            bool atBreakeven = (currentSl > 0.0 && currentSl <= openPrice + point);
            if(!atBreakeven && (openPrice - tick.ask) >= minStopDist && BreakevenDue(profit))
            {
               newSl = NormalizeDouble(openPrice, digits);
               changeSl = true;
            }
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
   // the SL itself isn't changing yet (a standing broker TP would race the
   // lock/trail and win).
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
//| position the instant an operator flips a source off mid-trade.     |
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
//| evaluated (read by the dashboard); "source" = UNIFIED_TELEGRAM_SOURCE.|
//| SMC columns are always "not applicable" here - this EA never runs  |
//| an SMC check (see file header).                                    |
//+------------------------------------------------------------------+
void LogSignalRow(long chatId, const string &action, const string &direction, bool symbolOk,
                   double entryLow, double entryHigh, const string &tpsJoined,
                   bool sanityPass, const string &sanityReason, bool accepted,
                   const string &orderType, double orderPrice, double lots, bool dryRun,
                   long orderTicket, int retcode, const string &rawText, double slUsed = 0.0)
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
                 DoubleToString(slUsed, 2) + "," +                     // the stop the order was sent with (0 = none sent)
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
                 TsmcCsvField(UNIFIED_TELEGRAM_SOURCE);

   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Dispatch a parsed signal. OPEN signals: allow-list, staleness      |
//| (checked in TelegramPoll), symbol, direction, daily cap, the       |
//| SHARED position cap, entry price, and price-deviation-from-zone    |
//| - then the order plan, its stop (TelegramStopDistance), the lot    |
//| sized from that stop, the daily budget and the margin guard with   |
//| that stop and lot, and PlaceCopiedOrder. NO SMC check. CLOSE/CANCEL|
//| still supported;                                                   |
//| a breakeven ("move SL to breakeven") message is deliberately NOT   |
//| recognized here - see file header's "OUT OF SCOPE".                |
//+------------------------------------------------------------------+
void ProcessSignal(const SignalMsg &msg, long chatId, const string &rawText)
{
   if(!InpEnableTelegramSignals)
   {
      // Checked FIRST, before IsAllowedChat() - the timer can now run for
      // InpControlChatId alone (see OnInit) even with Telegram signal
      // EXECUTION fully off, and IsAllowedChat() treats
      // InpChannelId1..3 all 0 (the shipped default - the exact
      // state a control-only setup is left in) as "allow ANY chat", a
      // deliberate first-run discovery behavior for setting UP Telegram
      // signals. Without this check first, that combination would let a
      // message from ANY chat - not just the intended signal channel, not
      // just InpControlChatId - reach CLOSE/CANCEL below and close real
      // positions. When Telegram signal execution is off, nothing from any
      // chat may reach ProcessSignal's CLOSE/CANCEL/OPEN handling, full stop.
      return;
   }
   if(!IsAllowedChat(chatId))
   {
      PrintFormat("UnifiedTrader_EA: ignoring message from chat %I64d (allowed: %I64d, %I64d, %I64d)",
                  chatId, InpChannelId1, InpChannelId2, InpChannelId3);
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
   if(g_telegramPaused)
   {
      Print("UnifiedTrader_EA: new Telegram entries are PAUSED (PauseHab/PauseTelHab) - ignoring "
            "signal. Send ResumeTelHab or ResumeHab to re-enable.");
      LogSignalRow(chatId, "OPEN", "", msg.symbolOk, msg.entryA, msg.entryB, "",
                   true, "paused via PauseHab/PauseTelHab", false, "", 0, 0, InpDryRun, 0, 0, rawText);
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
   string hoursReason = TradeHoursReason();
   if(hoursReason != "")
   {
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", hoursReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, hoursReason, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   string xtrReason;
   if(XtrHtfOpposes(msg.direction, xtrReason))
   {
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", xtrReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, xtrReason, false, "", 0, 0, InpDryRun, 0, 0, rawText);
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
   if(DailyLossBreakerActive())
   {
      string r = StringFormat("daily loss breaker triggered (max %.2f%%)", InpMaxDailyLossPct);
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   string newsDesc;
   if(InpNewsFilter && EconNewsBlock(InpNewsCurrencies, (int)InpNewsMinImportance,
                                     InpNewsBlockBeforeMin, InpNewsBlockAfterMin, newsDesc))
   {
      string r = "news blackout: " + newsDesc;
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", r);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, r, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   int spreadPts = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPoints > 0 && spreadPts > InpMaxSpreadPoints)
   {
      string r = StringFormat("spread %d points is above the %d-point limit", spreadPts, InpMaxSpreadPoints);
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

   // Where the order goes, its stop (the signal's or the fixed one) and the
   // lot sized from that stop - then the daily budget and the margin guard
   // are checked with exactly that stop and lot.
   double orderPrice;
   bool   isPending;
   string planFail;
   if(!PlanCopiedOrder(isBuy, lowerBound, upperBound, orderPrice, isPending, planFail))
   {
      string r = (planFail == "STALE_SKIPPED") ? "price already beyond the zone (stale)" : "no price available";
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                   true, r, false, planFail, 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   string stopNote;
   double slDist = TelegramStopDistance(msg, isBuy, orderPrice, stopNote);
   double lots   = PositionSizeLots(slDist);

   string budgetReason = DailyRiskBudgetReason(lots, slDist);
   if(budgetReason != "")
   {
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", budgetReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                   true, budgetReason, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }
   string marginReason = MarginGuardReason(isBuy, lots, slDist);
   if(marginReason != "")
   {
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", marginReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                   true, marginReason, false, "", 0, 0, InpDryRun, 0, 0, rawText);
      return;
   }

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double slUsed = NormalizeDouble(isBuy ? orderPrice - slDist : orderPrice + slDist, digits);
   PrintFormat("UnifiedTrader_EA: executing %s XAUUSD %.2f-%.2f from chat %I64d - NO SMC validation, "
               "stop %.2f (%s), %.2f lots, $%.2f lock/$%.2f trail (signal TPs logged only: %s)",
               isBuy ? "BUY" : "SELL", lowerBound, upperBound, chatId, slUsed,
               stopNote != "" ? stopNote : StringFormat("fixed $%.2f", slDist), lots,
               InpTp1Dollars, InpTrailDollars, msg.tpCount > 0 ? tpList : "none");

   string outOrderType  = "";
   long   outTicket     = 0;
   int    outRetcode    = 0;
   bool   placed = PlaceCopiedOrder(isBuy, orderPrice, isPending, slDist, lots,
                                     outOrderType, outTicket, outRetcode);
   string reason = "";
   if(!placed)
   {
      if(outOrderType == "REJECTED")           reason = StringFormat("order rejected by broker (retcode %d)", outRetcode);
      else if(outOrderType == "NO_TICK_VALUE") reason = "tick value not available yet";
      else                                     reason = "no price available";
   }
   else
      reason = "stop: " + (stopNote != "" ? stopNote : StringFormat("fixed $%.2f", slDist));

   LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                true, reason, placed, outOrderType, orderPrice, placed ? lots : 0.0, InpDryRun,
                outTicket, outRetcode, rawText, placed ? slUsed : 0.0);
}

//+------------------------------------------------------------------+
//| Telegram Bot API: getUpdates (short poll, timeout=0) + a minimal  |
//| field scraper.                                                     |
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
//| Minimal JSON string escaping for TelegramSendMessage()'s outbound |
//| body - only what this file ever actually sends needs covering     |
//| (plain English status text: fixed words plus %d counts), but      |
//| covers backslash/quote/newline/CR defensively regardless. Order    |
//| matters: backslash MUST be escaped first, or the backslashes       |
//| introduced by the later replacements would themselves get escaped. |
//+------------------------------------------------------------------+
string JsonEscape(const string &s)
{
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\r", "\\r");
   return(out);
}

//+------------------------------------------------------------------+
//| Sends `text` to `chatId` with the Pause/Resume/Stats/News/Why      |
//| reply keyboard attached, so the buttons stay                       |
//| visible in Telegram - a reply keyboard persists client-side once   |
//| shown, so re-attaching it on every message (rather than once at    |
//| startup only) is redundant but harmless, and simplest to reason    |
//| about. Never called for anything except InpControlChatId (command  |
//| replies and trade-closed notices). A failure here (bad token,      |
//| network down, rate limited) is logged and never raised - it must   |
//| never affect whether a pause/close actually happened, only whether |
//| the operator sees a Telegram confirmation of it.                   |
//+------------------------------------------------------------------+
bool TelegramSendMessage(long chatId, const string text)
{
   // A fixed, short cap - not InpHttpTimeoutMs - on purpose: MQL5's
   // WebRequest is synchronous and this EA's OnTick() (where
   // ManageAllPositions() trails/locks every open position) is serialized
   // behind OnTimer() on the same event thread, so any slow WebRequest call
   // here delays live position management for open trades too. A
   // confirmation reply is strictly lower priority than that - it's sent
   // AFTER the pause/close it confirms has already happened - so it should
   // never get to hold up OnTick() for as long as a user might reasonably
   // set InpHttpTimeoutMs (which governs the more important getUpdates
   // polling reliability instead). This caps each individual call, not the
   // whole OnTimer() tick - if a getUpdates batch ever contains more than
   // one control command (e.g. two commands sent within the same
   // InpPollSeconds window), each gets its own confirmation send and its
   // own up-to-this-cap wait, one after another.
   int sendTimeoutMs = (int)MathMin(InpHttpTimeoutMs, 3000);
   string keyboardJson =
      "{\"keyboard\":[[\"PauseHab\",\"ResumeHab\"],[\"PauseTelHab\",\"ResumeTelHab\"],"
      "[\"PauseClaudeHab\",\"ResumeClaudeHab\"],[\"Stats\",\"News\",\"Why\"]],"
      "\"resize_keyboard\":true,\"is_persistent\":true}";
   string body = StringFormat("{\"chat_id\":%I64d,\"text\":\"%s\",\"reply_markup\":%s}",
                               chatId, JsonEscape(text), keyboardJson);

   string url     = "https://api.telegram.org/bot" + InpBotToken + "/sendMessage";
   string headers = "Content-Type: application/json\r\n";
   char   post[];
   char   result[];
   string resultHeaders;
   StringToCharArray(body, post, 0, WHOLE_ARRAY, CP_UTF8);
   // StringToCharArray() appends a trailing NUL the body itself doesn't
   // need - WebRequest would otherwise POST one extra byte.
   if(ArraySize(post) > 0 && post[ArraySize(post) - 1] == 0)
      ArrayResize(post, ArraySize(post) - 1);

   ResetLastError();
   int rc = WebRequest("POST", url, headers, sendTimeoutMs, post, result, resultHeaders);
   if(rc == -1)
   {
      PrintFormat("UnifiedTrader_EA: sendMessage WebRequest failed, error=%d - the button keyboard/"
                  "confirmation won't reach Telegram, but this never affects what the command "
                  "actually did.", GetLastError());
      return(false);
   }
   if(rc != 200)
   {
      PrintFormat("UnifiedTrader_EA: Telegram sendMessage HTTP status %d", rc);
      return(false);
   }
   return(true);
}


//+------------------------------------------------------------------+
//| One polling cycle: fetch, parse, act, advance the offset.         |
//+------------------------------------------------------------------+
void TelegramPoll()
{
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION)) return;

   if(InpControlChatId != 0 && !g_sentControlStartupMsg)
   {
      // Sent here, not from OnInit(), on purpose: OnInit() must stay fast
      // and network-independent (every attach/reattach/parameter change
      // would otherwise block on a WebRequest for up to InpHttpTimeoutMs),
      // and this placement inherits the MQL_TESTER/MQL_OPTIMIZATION guard
      // above for free - an optimization run firing this hundreds/
      // thousands of times (once per OnInit() per pass) would otherwise
      // either flood the operator's real chat or waste wall-clock time on
      // timeouts with no network access. Set the flag before attempting so
      // a failed send (logged inside TelegramSendMessage()) is tried at
      // most once per (session, InpControlChatId value) pair, not retried
      // every poll cycle forever - see OnInit()'s g_lastControlChatSentTo
      // check for the one case this does get sent again mid-session:
      // InpControlChatId itself changing to a chat it wasn't already sent to.
      g_sentControlStartupMsg = true;
      g_lastControlChatSentTo = InpControlChatId;
      TelegramSendMessage(InpControlChatId,
         StringFormat("UnifiedTrader_EA is listening on %s. Tap a button, or type its text.%s",
                      _Symbol, g_telegramPaused ? "\n\n(Telegram entries are currently PAUSED.)" : ""));
   }

   string json;
   if(!TelegramGetUpdates(json)) return;

   if(StringFind(json, "\"ok\":false") >= 0)
   {
      PrintFormat("UnifiedTrader_EA: Telegram API returned an error: %s", json);
      return;
   }

   TsmcTgUpdate updates[];
   TsmcExtractUpdates(json, updates, InpAcceptPhotoCaptions);
   // While no channel id is set, omitted messages are still printed WITH
   // their chat id - that log is how the channel's id is found.
   bool logSkipped = InpLogSkippedMessages
                     || (InpChannelId1 == 0 && InpChannelId2 == 0 && InpChannelId3 == 0);

   for(int i = 0; i < ArraySize(updates); i++)
   {
      if(updates[i].update_id > g_lastUpdateId) g_lastUpdateId = updates[i].update_id;
      if(updates[i].isEdited) continue;
      bool fromControl = (InpControlChatId != 0 && updates[i].chat_id == InpControlChatId);
      if(StringLen(updates[i].skip) > 0 || StringLen(updates[i].text) == 0)
      {
         // Videos, audio, voice notes, stickers, documents, polls, photos
         // (unless InpAcceptPhotoCaptions), pins/joins and other service posts.
         if(logSkipped && !fromControl && updates[i].chat_id != 0)
            PrintFormat("UnifiedTrader_EA: omitted %s from chat %I64d",
                        StringLen(updates[i].skip) > 0 ? updates[i].skip : "an empty message",
                        updates[i].chat_id);
         continue;
      }

      // Control commands go through InpMaxSignalAgeSec too, same as trading
      // signals - a PauseHab queued during a long outage and only delivered
      // once the EA reconnects could otherwise force-close real, currently-
      // healthy positions the operator no longer intends to touch. Dropping
      // it and logging clearly (so the operator can just resend it if still
      // needed) is the safer failure mode than silently acting on a stale
      // command with no chance to reconsider.
      long age = (long)TimeGMT() - updates[i].date;
      // An undated message can't be proven fresh - treated as stale.
      bool isStale = (InpMaxSignalAgeSec > 0 && (updates[i].date <= 0 || age > InpMaxSignalAgeSec));

      if(fromControl)
      {
         if(isStale)
            PrintFormat("UnifiedTrader_EA: control command '%s' is %ds old (> %ds) - STALE, ignoring "
                        "(resend it if it's still what you want).", updates[i].text, age, InpMaxSignalAgeSec);
         else
            ProcessControlCommand(updates[i].text);
         continue;
      }

      // Trade messages only: greetings, mood posts, commentary, promos and
      // long messages never reach the parser - its CLOSE/CANCEL keywords
      // would otherwise read "Good morning! Close your charts" as close-all.
      string text = updates[i].text;
      string why;
      if(TsmcClassifyMessage(text, InpMaxMessageChars, InpMaxCommandChars, why) == TSMC_MSG_SKIP)
      {
         if(logSkipped)
            PrintFormat("UnifiedTrader_EA: omitted non-trade message from chat %I64d (%s): %s",
                        updates[i].chat_id, why, StringSubstr(text, 0, 60));
         continue;
      }

      if(isStale)
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
   {
      GlobalVariableSet(GV_LAST_UPDATE_ID, (double)g_lastUpdateId);
      // To disk now: after a crash, a lost offset would make Telegram
      // re-deliver this batch and a signal younger than
      // InpMaxSignalAgeSec would be traded a second time.
      GlobalVariablesFlush();
   }
}

//+------------------------------------------------------------------+
//| Expert tick function - manages whichever source(s) are enabled.   |
//| Entries come from Telegram via OnTimer, never from price ticks.   |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageAllPositions();
   EconMaybeExport(InpCalendarExportFile, InpNewsCurrencies, InpCalendarRefreshMin);
   XtrExpMaybeExport(InpXtrExport, _Symbol, InpXtrExportFolder, InpXtrExportName, InpXtrExportBars,
                     InpXtrExportM1, InpXtrExportCopyTo);
}

//+------------------------------------------------------------------+
//| Expert timer function - only set up when InpEnableTelegramSignals |
//| or InpControlChatId != 0 (see OnInit); the latter alone still      |
//| polls Telegram (for control commands), but TelegramPoll()'s own    |
//| ProcessSignal() call refuses to open a position when               |
//| InpEnableTelegramSignals is false. Also does light upkeep.         |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDailyTracking();
   ExpirePendingOrders();
   EconMaybeExport(InpCalendarExportFile, InpNewsCurrencies, InpCalendarRefreshMin);
   XtrExpMaybeExport(InpXtrExport, _Symbol, InpXtrExportFolder, InpXtrExportName, InpXtrExportBars,
                     InpXtrExportM1, InpXtrExportCopyTo);
   TelegramPoll();
   FlushNotifyQueue();
}

//+------------------------------------------------------------------+
//| InpNotifyTradeClosed: queues a message for every closing deal of   |
//| either magic on this symbol - the trade's own net P/L (all of its  |
//| deals, commissions included), current equity, and today's totals. |
//| Only queued here; FlushNotifyQueue() sends from OnTimer.           |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;
   if(!InpNotifyTradeClosed || InpControlChatId == 0 || StringLen(InpBotToken) == 0)
      return;
   if(MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY)
      return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol)
      return;
   ulong  posId  = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   long   dealType = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   double volume = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double price  = HistoryDealGetDouble(trans.deal, DEAL_PRICE);

   // Source = the position's ENTRY deal magic (an SL/TP-triggered exit
   // deal isn't relied on to carry it).
   if(!HistorySelectByPosition(posId))
      return;
   long   magic = -1;
   double net   = 0.0;
   for(int i = 0; i < HistoryDealsTotal(); i++)
   {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
         magic = HistoryDealGetInteger(d, DEAL_MAGIC);
      net += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
             + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
   }
   string source;
   if(magic == InpTelegramMagicNumber)    source = "Telegram";
   else if(magic == InpClaudeMagicNumber) source = "Claude";
   else return;

   UpdateDailyTracking();
   ClosedStatsT tel, cla, all;
   ClosedStats(g_currentDay, InpTelegramMagicNumber, tel);
   ClosedStats(g_currentDay, InpClaudeMagicNumber, cla);
   all.trades = tel.trades + cla.trades;
   all.wins   = tel.wins + cla.wins;
   all.losses = tel.losses + cla.losses;
   all.pnl    = tel.pnl + cla.pnl;

   // A closing SELL deal closes a BUY position, and vice versa.
   string msgText = StringFormat("Closed %s %s %.2f lot @ %.2f: %+.2f %s\nEquity %.2f\n%s",
                                 source, dealType == DEAL_TYPE_SELL ? "BUY" : "SELL", volume, price,
                                 net, AccountInfoString(ACCOUNT_CURRENCY),
                                 AccountInfoDouble(ACCOUNT_EQUITY), StatsLine("Today", all));
   int n = ArraySize(g_notifyQueue);
   if(n >= 20)
      return;   // a burst (e.g. PauseHab closing everything) - Stats has the totals
   ArrayResize(g_notifyQueue, n + 1);
   g_notifyQueue[n] = msgText;
}
//+------------------------------------------------------------------+
