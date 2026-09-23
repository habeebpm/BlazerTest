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
//| SHARED EXIT DESIGN, BOTH SOURCES: no broker take-profit is ever    |
//| placed. The stop-loss is the only exit mechanism. Once floating    |
//| profit reaches InpTp1Dollars, the SL moves to EXACTLY that price - |
//| locking in that much profit, no more no less, in one deterministic |
//| step - then trails InpTrailDollars behind new highs/lows from      |
//| there, tightening only. "Armed" (locked vs. still trailing) is     |
//| derived every tick from whether a position's OWN current SL has    |
//| already reached the lock level, never stored - this EA needs no    |
//| memory across ticks or restarts and stays correct even if          |
//| reattached mid-trade. Dollar amounts are USD AT InpReferenceLot,   |
//| converted with the symbol's live tick value/size                   |
//| (price_distance = dollars * tick_size / (tick_value * ref lot))    |
//| - never an assumed contract size. SL, TP1 and trail are therefore  |
//| fixed price distances whatever lot is traded, so risk-% sizing     |
//| scales risk and locked profit together. This is                    |
//| InpExitStyle=EXIT_SL_TO_TP1 (the default) and it's                 |
//| the ONLY style Telegram-sourced positions ever use.                |
//|                                                                    |
//| InpExitStyle=EXIT_BREAKEVEN_R_DECAY adds one earlier protective    |
//| step before the above, and applies ONLY to InpClaudeMagicNumber    |
//| positions (mirrors python/config.py AdvisorConfig.exit_style -     |
//| keep the two in sync by hand): once floating profit reaches         |
//| InpBreakevenAtrMult x this position's own InpAtrPeriod-bar ATR on   |
//| InpAtrTimeframe, OR InpDecayWindowMinutes have passed since the     |
//| position opened (whichever happens first) - and only if price has  |
//| actually moved far enough into profit to place a valid stop there  |
//| - the SL moves to EXACTLY the entry price (breakeven). A fast move |
//| can still jump straight past this step to the full TP1 lock in one |
//| tick (the lock check always runs first). See                       |
//| ../../ClaudeSMC_Trader/MQL5/Experts/ClaudeSMC_TradeManager.mq5's   |
//| own file header for the full design rationale - this EA's copy is  |
//| identical logic, just scoped to InpClaudeMagicNumber only.          |
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
//| REMOTE CONTROL (InpControlChatId, optional): nine plain-text        |
//| commands, DM'd to this bot from InpControlChatId ONLY (a private    |
//| 1:1 chat, never the signal channel/group) - a completely separate   |
//| command path from trading-signal parsing, matched by EXACT text     |
//| (trimmed, case-insensitive), not substring, since these close real  |
//| positions. Shown as a Telegram reply-keyboard (tappable buttons     |
//| under the message box) rather than requiring you to type them:      |
//| TelegramSendMessage() attaches the keyboard once, on the first poll |
//| tick after this EA starts (from TelegramPoll(), not OnInit() -      |
//| keeps EA attach/reattach fast and network-independent), and again   |
//| on every command's confirmation reply. A button tap is delivered by |
//| Telegram as an ordinary text message equal to the button's label,   |
//| so it reaches ProcessControlCommand() exactly like typing the same  |
//| text would; nothing about the matching logic below                  |
//| knows or cares whether a command was tapped or typed.               |
//|   PauseHab        - closes every open position on THIS CHART'S       |
//|                      SYMBOL under both magics, and cancels Telegram   |
//|                      pending orders there, then blocks new Telegram   |
//|                      AND new Claude entries until ResumeHab.         |
//|   ResumeHab        - re-enables new Telegram and Claude entries.     |
//|                      Reopens nothing.                                |
//|   PauseTelHab      - closes this symbol's Telegram-sourced           |
//|                      positions/orders only and blocks new Telegram   |
//|                      entries until ResumeTelHab/ResumeHab.           |
//|   ResumeTelHab     - re-enables new Telegram entries only.           |
//|   PauseClaudeHab   - closes this symbol's Claude-sourced             |
//|                      (InpClaudeMagicNumber) positions and blocks new |
//|                      Claude entries until ResumeClaudeHab/ResumeHab. |
//|   ResumeClaudeHab  - re-enables new Claude entries only.             |
//|   Stats            - equity, balance, open P/L, closed P/L and win%  |
//|                      for today / 7 / 30 days (per source), and how   |
//|                      much of the daily loss budget is used.          |
//|   News             - economic calendar: recent releases (actual vs   |
//|                      forecast, what it means for gold) and upcoming  |
//|                      events (see ECONOMIC CALENDAR below).           |
//|  InpNotifyTradeClosed also sends a message to InpControlChatId for    |
//|  every closed trade: its net P/L, current equity and today's win%.   |
//|  Claude pause mechanics: this EA can't place or refuse Python's       |
//|  orders directly, so it writes "paused"/"running" to the shared       |
//|  Common\Files text file InpClaudePauseFilename (MUST match config.py's|
//|  claude_pause_filename); python/main.py reads it every cycle and      |
//|  skips its whole evaluation (no Claude call, no order) while paused.  |
//|   Why              - echoes the latest Claude verdict's reasoning,   |
//|                      read from the shared Common\Files text file      |
//|                      python/main.py writes it to (see ReadLastVerdict|
//|                      File(), InpLastVerdictFilename - MUST match     |
//|                      config.py's last_verdict_filename). Read-only:  |
//|                      never touches a position or the pause state.    |
//|                      "No Claude verdict on file yet" if main.py       |
//|                      hasn't run a cycle, or the filenames don't match.|
//| SCOPE: like every other position-management function in this file    |
//| (CloseAllMine/CancelAllPendingMine/ManageAllPositions), the four      |
//| position-affecting commands only ever touch positions/orders on the  |
//| symbol of the chart this EA instance is attached to - a position     |
//| opened by hand on a different symbol is untouched. RUN ONLY ONE       |
//| INSTANCE OF THIS EA PER                                               |
//| TERMINAL, on any symbol - this was already true before remote        |
//| control existed (GV_LAST_UPDATE_ID is a terminal-wide Global          |
//| Variable, not scoped per chart) and remains true for the new pause    |
//| state too (GV_TELEGRAM_PAUSED, same mechanism): a second running      |
//| instance, even on a different symbol or with its own InpBotToken,     |
//| shares BOTH of those with this one and will corrupt them - including  |
//| silently pausing/resuming a chart nobody sent a command to. (This     |
//| does NOT affect g_currentDay/g_tradesToday - those are plain          |
//| per-instance memory, never written to a Global Variable.)              |
//| Claude entries are blocked through the pause file above, which only  |
//| works when python/main.py runs on the SAME machine as this terminal  |
//| (the same requirement as the Why button). The pause states           |
//| (Telegram / Claude entries blocked or not) are saved to terminal Global|
//| Variable, the same mechanism InpControlChatId=0 already uses for     |
//| g_lastUpdateId, so it survives a restart/reattach rather than        |
//| silently resetting to "resumed". Leaving InpControlChatId=0 (the     |
//| default) disables this feature entirely - no behavior change - this  |
//| holds even if a REAL pause was set during an earlier session with    |
//| remote control enabled: that pause goes dormant (never enforced,     |
//| never logged) while InpControlChatId=0, rather than silently         |
//| blocking every Telegram entry forever with no ResumeHab reachable    |
//| to clear it. Setting InpControlChatId back to a real chat restores   |
//| whatever pause was last actually set, unchanged.                     |
//|                                                                    |
//| ECONOMIC CALENDAR (EconCalendar.mqh - MT5's own calendar, no API   |
//| key): with InpNewsFilter (default on) a new Telegram entry is       |
//| skipped from InpNewsBlockBeforeMin before to InpNewsBlockAfterMin   |
//| after any InpNewsMinImportance+ event for InpNewsCurrencies (USD by |
//| default). The calendar is also exported every InpCalendarRefreshMin |
//| to InpCalendarExportFile in the shared Common\Files folder, where   |
//| ClaudeSMC_Trader's python/econ_calendar.py applies the same blackout|
//| to Claude's entries and shows the events to Claude.                 |
//|                                                                    |
//| TELEGRAM SETUP (only if InpEnableTelegramSignals): identical to    |
//| TelegramSMC_Copier.mq5's - @BotFather /newbot for InpBotToken, add  |
//| that bot to the channel as admin, Tools > Options > Expert Advisors|
//| > allow WebRequest for https://api.telegram.org, leave              |
//| InpChannelId1..3 at 0 for the first run to discover chat ids from   |
//| the log, then set up to THREE of them and restart. Only TRADE       |
//| messages are read (TsmcClassifyMessage in TelegramSMC_Common.mqh):  |
//| greetings, mood posts, long messages, videos, audio, voice notes,   |
//| stickers and pinned-message notices are omitted before parsing.     |
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
#include <EconCalendar.mqh>
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

// Deliberately NOT TelegramSMC_Common.mqh's TSMC_SIGNAL_SOURCE ("Telegram_Sig")
// - that constant's own doc comment reserves it for TelegramSMC_Copier.mq5
// specifically. This EA's Telegram side has a materially different (lower)
// validation bar - no SMC filter, no use of the message's own SL (see file
// header) - so if both EAs ever ran in the same terminal, sharing one tag
// would make their very-different-risk-profile rows indistinguishable in
// TelegramSMC_Signals.csv/the ASPX dashboard. Still lands in the SAME file
// (TSMC_SIGNALS_FILE below) for one unified view - just tagged separately.
#define UNIFIED_TELEGRAM_SOURCE "Telegram_Sig_Unified"

//================================= INPUTS ====================================

input group "=== Mode selection - enable either or both ==="
input bool    InpEnableTelegramSignals = false;   // Poll Telegram and execute signals (no SMC validation - see file header)
input bool    InpEnableClaudeManagement = false;  // Manage exits for ClaudeSMC_Trader's Python-opened positions

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
input long    InpClaudeMagicNumber   = 20260921;   // MUST match python/config.py AdvisorConfig.magic
input bool    InpDryRun              = true;       // Log what would happen; do not send/modify real orders

enum ENUM_EXIT_STYLE
{
   EXIT_SL_TO_TP1,          // Default - lock at InpTp1Dollars, then trail (see file header)
   EXIT_BREAKEVEN_R_DECAY   // Adds an earlier breakeven step, InpClaudeMagicNumber positions only - see below
};

input group "=== Claude-management exit style - InpClaudeMagicNumber positions ONLY, never Telegram's ==="
input ENUM_EXIT_STYLE InpExitStyle = EXIT_SL_TO_TP1;      // Must match python/config.py AdvisorConfig.exit_style
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
input int     InpPendingExpiryMin  = 240;          // Cancel an unfilled pending order after N minutes (0 = never)

input group "=== Telegram Signal Sanity - kept from TelegramSMC_Copier.mq5 (pips; 1 pip = 10 broker points) ==="
input double  InpMaxEntryDeviationPips = 200.0;    // Reject if current price is this far outside the signaled zone

input group "=== Remote control (optional) - see file header's REMOTE CONTROL section ==="
input long    InpControlChatId = 0;                // Your own DM chat id with this bot; 0 = disabled
input string  InpLastVerdictFilename = "claudesmc_last_verdict.txt"; // Why button: MUST match python/config.py's AdvisorConfig.last_verdict_filename
input string  InpClaudePauseFilename = "claudesmc_pause.txt";        // PauseClaudeHab/ResumeClaudeHab: MUST match python/config.py's AdvisorConfig.claude_pause_filename
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
input string               InpCalendarExportFile = "econ_calendar.csv";   // Shared file ClaudeSMC_Trader reads - MUST match python config.econ_calendar_filename ("" = no export)
input int                  InpCalendarRefreshMin = 5;                     // Re-export the calendar every N minutes

input group "=== Price export for Google Drive (XTR) - see XtrBarExport.mqh ==="
input bool   InpXtrExport       = true;        // Write closed M5/M15/H1 bars (UTC CSV + manifest) on every M1 close
input string InpXtrExportFolder = "XTR_Data";  // Folder inside Common\Files - add it to Google Drive for Desktop
input string InpXtrExportName   = "XAUUSD";    // File name prefix / manifest symbol (XAUUSD_M5.csv ...)
input int    InpXtrExportBars   = 200;         // Closed bars per file (50-5000)
input bool   InpXtrExportM1     = false;       // Also write <name>_M1.csv

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
double   PositionSizeLots();
double   OpenRiskMoney();
string   DailyRiskBudgetReason(double newLots);
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
bool     PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                           string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode,
                           double &outLots);
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
            "TelegramSMC_Copier.mq5's default configuration.");
   }
   if(InpEnableClaudeManagement)
      PrintFormat("UnifiedTrader_EA: managing exits for magic=%I64d (must match python/config.py's "
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
   // persisted) can't keep python/main.py blocked with no resume reachable.
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
//| the data isn't ready yet. Same rule as ClaudeSMC_Trader's           |
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
//| EXIT_BREAKEVEN_R_DECAY only (InpClaudeMagicNumber positions) -    |
//| identical to ClaudeSMC_TradeManager.mq5's own copy of these two   |
//| functions; see that file for the full design rationale.           |
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
//|                                                                    |
//| NOTE ON DUPLICATION: everything from here down through             |
//| ParseSignalText/TelegramGetUpdates/ExtractUpdates/TelegramPoll is   |
//| copied, not shared, from ../../MQL5/Experts/TelegramSMC_Copier.mq5 |
//| - deliberately, not an oversight. Factoring it into a shared .mqh   |
//| (the way TelegramSMC_Common.mqh already does for CSV logging)      |
//| would mean editing that already-shipped, independently-deployed EA |
//| just to extract code for this one - real regression risk to a       |
//| production file for a change with zero runtime behavior difference,|
//| and there's no MQL5 compiler in this environment to verify either   |
//| file still builds afterward. The tradeoff: a future fix to this     |
//| parser (this repo's history already has one - a hyphenated-label    |
//| bug once missed in exactly this kind of duplicated code) must be    |
//| applied to BOTH this file and TelegramSMC_Copier.mq5 by hand, or    |
//| the two EAs' signal interpretation will silently diverge on the     |
//| same input text. Check both whenever you touch parsing here.       |
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
// python/main.py's DayRoll.check_daily_limits() on the Claude side.
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
//| InpFixedLot, or a size derived from current equity when            |
//| InpUseRiskPercent is set - mirrors ClaudeSMC_Trader/python/         |
//| executor.py's position_size(). The price distance InpSlDollars      |
//| implies at InpReferenceLot is held fixed (same value                |
//| PlaceCopiedOrder() already computes via DollarsToPrice(InpSlDollars,|
//| InpReferenceLot)), and the lot is solved for so that distance times |
//| that lot risks exactly InpRiskPercent% of current equity, then      |
//| clamped to [SYMBOL_VOLUME_MIN, SYMBOL_VOLUME_MAX, InpMaxLotSize]    |
//| and rounded down to the broker's own volume step.                   |
//+------------------------------------------------------------------+
double PositionSizeLots()
{
   if(!InpUseRiskPercent)
      return(InpFixedLot);

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double slDist = DollarsToPrice(InpSlDollars, InpReferenceLot);
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
//| ClaudeSMC_Trader always set one). Mirrors ClaudeSMC_Trader/python/ |
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
string DailyRiskBudgetReason(double newLots)
{
   if(InpMaxDailyLossPct <= 0.0 || g_dayStartEquity <= 0.0)
      return("");
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double slDist    = DollarsToPrice(InpSlDollars, InpReferenceLot);
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
//| filled right now. Matches ../../MQL5/Experts/TelegramSMC_Copier.mq5|
//| own CountActiveSlots()'s reasoning for counting both together.     |
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
//| the pause file python/main.py reads - see WriteClaudePauseFile().   |
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
//| shared Common\Files folder - the only place python/main.py can read |
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
   // Atomic (temp file + rename): python/main.py must never catch it empty.
   if(!CommonFileWriteAtomic(InpClaudePauseFilename, g_claudePaused ? "paused" : "running"))
      PrintFormat("UnifiedTrader_EA: WARNING - could not write the Claude pause file %s (error %d) - "
                  "python/main.py will not see the current pause state.",
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
//| python/main.py writes the latest Claude verdict to (see            |
//| mt5_gateway.write_common_file(), config.py's last_verdict_filename |
//| - MUST match InpLastVerdictFilename). MQL5's file sandbox           |
//| otherwise only sees THIS EA's own MQL5/Files directory, never       |
//| Python's decisions.csv directly - the shared Common\Files folder is |
//| the one place both processes can read/write, which is why this      |
//| cross-process hand-off exists at all rather than parsing the CSV.   |
//| Never raises: FileOpen() failing (file not written yet, filename    |
//| mismatch, ClaudeSMC_Trader not running) just means no verdict text, |
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
      SendControlReply("ResumeClaudeHab done - new Claude entries re-enabled from python/main.py's "
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
                  "until ResumeClaudeHab or ResumeHab (python/main.py skips its evaluation while "
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
         SendControlReply("No Claude verdict on file yet - either ClaudeSMC_Trader's python/main.py "
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
//| Places a Telegram-sourced order: LIMIT if price hasn't reached    |
//| the zone, MARKET if already inside it, skipped if already through |
//| it (identical zone logic to TelegramSMC_Copier.mq5's). SL is the  |
//| FIXED InpSlDollars distance, never the message's own SL. No       |
//| broker TP (tp=0.0, sl_to_tp1 exit design - see file header).      |
//+------------------------------------------------------------------+
bool PlaceCopiedOrder(bool isBuy, double lowerBound, double upperBound,
                       string &outOrderType, double &outOrderPrice, long &outTicket, int &outRetcode,
                       double &outLots)
{
   outOrderType  = "";
   outOrderPrice = 0.0;
   outTicket     = 0;
   outRetcode    = 0;
   outLots       = PositionSizeLots();

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
   string comment   = UNIFIED_TELEGRAM_SOURCE;

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
   double slDist = DollarsToPrice(InpSlDollars, InpReferenceLot);
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
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", outLots, orderPrice, sl,
                  InpTp1Dollars);
      return(true);
   }

   trade.SetExpertMagicNumber(InpTelegramMagicNumber);
   bool ok;
   if(isPending)
      ok = isBuy ? trade.BuyLimit(outLots, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
                 : trade.SellLimit(outLots, orderPrice, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else
      ok = isBuy ? trade.Buy(outLots, _Symbol, orderPrice, sl, tp, comment)
                 : trade.Sell(outLots, _Symbol, orderPrice, sl, tp, comment);

   outRetcode = (int)trade.ResultRetcode();
   outTicket  = (long)trade.ResultOrder();

   if(ok)
   {
      g_tradesToday++;
      SaveDayState();
      PrintFormat("UnifiedTrader_EA: %s %s placed - %.2f lots @ %.2f sl=%.2f ticket=%I64u",
                  isBuy ? "BUY" : "SELL", isPending ? "LIMIT" : "MARKET", outLots, orderPrice, sl,
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
//| file header's "SHARED EXIT DESIGN". The one difference from that   |
//| file: EXIT_BREAKEVEN_R_DECAY's extra breakeven step only ever      |
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
//| ASPX dashboard reads either EA's output without changes, but the   |
//| "source" column is UNIFIED_TELEGRAM_SOURCE, not that EA's          |
//| "Telegram_Sig", so rows from the two remain distinguishable if      |
//| both ever log to the same file (see that constant's own comment).  |
//| SMC columns are always "not applicable" here - this EA never runs  |
//| an SMC check (see file header).                                    |
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
                 TsmcCsvField(UNIFIED_TELEGRAM_SOURCE);

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
   string budgetReason = DailyRiskBudgetReason(PositionSizeLots());
   if(budgetReason != "")
   {
      PrintFormat("UnifiedTrader_EA: %s - skipping signal.", budgetReason);
      LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, msg.entryA, msg.entryB, tpList,
                   true, budgetReason, false, "", 0, 0, InpDryRun, 0, 0, rawText);
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
   double outLots       = 0.0;
   bool   placed = PlaceCopiedOrder(isBuy, lowerBound, upperBound,
                                     outOrderType, outOrderPrice, outTicket, outRetcode, outLots);

   LogSignalRow(chatId, "OPEN", dirStr, msg.symbolOk, lowerBound, upperBound, tpList,
                true, "", placed, outOrderType, outOrderPrice, outLots, InpDryRun,
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
                     InpXtrExportM1);
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
                     InpXtrExportM1);
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
