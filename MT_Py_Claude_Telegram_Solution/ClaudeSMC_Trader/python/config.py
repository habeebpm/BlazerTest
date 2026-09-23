"""
All tunables for the Claude-advised XAUUSD trader in one place.

Deliberately a plain dataclass with CLI overrides in main.py (see --help) -
no env-var magic, no config file format to learn. The defaults below are
the trading rules requested: fixed 0.01 lot, max 5 concurrent same-direction
positions, $6 stop-loss, SL moved to lock in $6 profit once TP1 is reached,
trailing $3 behind price from there (exit_style="sl_to_tp1" - see below).

exit_style has three values. All three agree on one thing: no broker-side
take-profit is ever placed except under "fixed_tp" - the stop-loss is the
only thing that closes a position early.
  - "sl_to_tp1" (default, and what ClaudeSMC_TradeManager.mq5/
    UnifiedTrader_EA.mq5 implement live when InpExitStyle is left at its
    own default): once floating profit reaches tp1_dollars, the SL moves
    to lock in exactly that much profit, then trails trail_dollars behind
    new highs/lows from there. Chosen over "fixed_tp" below because it has
    no broker order sitting at the same price as the arm threshold -
    nothing for the broker to auto-fill ahead of the stop being moved.
  - "breakeven_r_decay" (also implemented live, selectable per-EA via
    InpExitStyle - see ClaudeSMC_TradeManager.mq5/UnifiedTrader_EA.mq5):
    adds an earlier protective step before the sl_to_tp1 lock described
    above. Once floating profit reaches breakeven_atr_mult x the position's
    own M5 ATR, OR decay_window_minutes have passed since entry (whichever
    happens first), the SL moves to exactly breakeven (entry price) - never
    earlier than that, and never if the trade hasn't actually reached
    breakeven-or-better yet. From there, the existing tp1_dollars lock/
    trail_dollars trail behavior applies exactly as in "sl_to_tp1" (a fast
    move can still jump straight past breakeven to the full TP1 lock in
    one step). This is the MQL5 EAs' own logic (they own the position's
    open time and can watch ATR tick-by-tick) - nothing here in Python
    computes or enforces it; these three fields exist so the EA's inputs
    have one documented source of truth to be kept in sync with by hand.
  - "fixed_tp" (the original design, kept only so backtest.py --compare
    has something concrete to measure sl_to_tp1 against): a real broker
    take-profit is placed at entry + tp1_dollars - which is the SAME
    price the trail would arm at, so in practice the standing TP order
    almost always wins that race and the trail rarely gets a chance to
    engage. Not implemented in either live MQL5 EA.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AdvisorConfig:
    # --- Instrument ---
    symbol: str = "XAUUSD"

    # --- DXY correlation context (optional; off by default) - XAUUSD is
    #     usually (not always) inversely correlated with US dollar strength,
    #     so a fresh DXY move that price hasn't caught up with yet is useful
    #     corroborating/contradicting context for Claude - see market_intel.
    #     dxy_context() and claude_advisor.SYSTEM_PROMPT. There is no fixed
    #     broker symbol for the dollar index - set this to whatever your
    #     broker calls it (e.g. "USDX", "DXY", "USDollar") by checking Market
    #     Watch; leave blank (the default) to disable this section entirely.
    #     Sourced from MT5 like everything else here, not a new dependency -
    #     if the symbol isn't available, dxy_context() no-ops rather than
    #     failing the whole snapshot.
    dxy_symbol: str = ""

    # --- News/calendar blackout windows (optional; off by default) - this
    #     solution has no economic-calendar data source of its own, so these
    #     are maintained by hand: a list of (start_iso, end_iso) UTC pairs,
    #     e.g. [("2026-10-03T12:25:00Z", "2026-10-03T12:40:00Z")] to skip a
    #     15-minute window around an NFP release. executor.gate() rejects
    #     any new entry whose evaluation time falls inside one of these -
    #     see executor.in_news_blackout(). Empty list (the default) never
    #     blocks anything.
    news_blackout_windows: list = field(default_factory=list)

    # --- Economic calendar (on by default) - MT5's own built-in calendar,
    #     exported every few minutes by UnifiedTrader_EA.mq5 /
    #     ClaudeSMC_TradeManager.mq5 (EconCalendar.mqh) to this file in the
    #     shared Common\Files folder; see econ_calendar.py. MUST match the
    #     EA's InpCalendarExportFile. Two uses:
    #       - automatic blackout: executor.gate() refuses a new entry from
    #         news_block_before_minutes before to news_block_after_minutes
    #         after any news_min_importance+ event for news_currencies
    #         (news_auto_blackout; the manual windows above still apply too);
    #       - context: upcoming events and recent releases (actual vs
    #         forecast, and what the surprise usually means for gold) are
    #         added to the snapshot Claude reads.
    #     No file yet (EA not running, calendar not synced, or a backtest)
    #     simply means no calendar - nothing is blocked.
    econ_calendar_filename: str = "econ_calendar.csv"
    news_auto_blackout: bool = True
    news_currencies: list = field(default_factory=lambda: ["USD"])
    news_min_importance: str = "high"      # "low", "moderate" or "high"
    news_block_before_minutes: int = 15
    news_block_after_minutes: int = 15

    # --- Execution rules (requested, fixed) ---
    fixed_lot: float = 0.01
    max_open_positions_per_direction: int = 5

    # --- Equity-scaled lot sizing (ports ../../python/mt5_client.py's
    #     position_size()/TradeConfig.use_risk_percent pattern) - when
    #     use_risk_percent is set, executor.execute() sizes each trade from
    #     current equity instead of always using fixed_lot. fixed_lot then
    #     acts as the REFERENCE lot: sl_dollars/tp1_dollars/trail_dollars are
    #     dollar amounts at fixed_lot, i.e. fixed PRICE distances, and the
    #     traded lot is solved so the SL distance risks exactly risk_percent%
    #     of equity, then clamped to [volume_min, volume_max, max_lot_size]
    #     and rounded down to the broker's volume_step. Because TP1/trail
    #     are the same fixed price distances (ClaudeSMC_TradeManager.mq5's
    #     InpReferenceLot MUST equal fixed_lot), a bigger lot risks AND locks
    #     proportionally more money with an unchanged SL : TP1 : trail shape.
    # On by default at 2.0% (= max_daily_loss_pct 10% / 5): the conventional
    # 1-2%-per-trade band, and ~5 full losses before the daily breaker
    # halts - see README's "Risk parameters". executor.execute()'s open-risk
    # budget check keeps several concurrent positions from jointly risking
    # more than what's left of the daily cap.
    use_risk_percent: bool = True
    risk_percent: float = 2.0       # 2.0% = this file's max_daily_loss_pct (10.0%) / 5
    max_lot_size: float = 5.0       # hard cap on a risk-sized lot, regardless of how large equity grows

    # --- Daily loss circuit breaker (mirrors ../../python/trader.py's
    #     Bot.roll_day()/entry_blocked() daily-loss pattern) - main.py's
    #     DayRoll tracks account equity from the first cycle of each UTC day
    #     and withholds NEW entries once the day's move breaches these
    #     limits. Existing open positions are left alone -
    #     ClaudeSMC_TradeManager.mq5 already owns exit management, so this
    #     never closes anything itself, only executor.gate() refusing new
    #     signals. Set to 0 to disable the check entirely.
    max_daily_loss_pct: float = 10.0       # stop new entries once the account is down this many pct on the UTC day
    use_daily_target: bool = False         # also stop new entries once daily_target_pct is reached
    daily_target_pct: float = 2.0

    sl_dollars: float = 6.0
    tp1_dollars: float = 6.0         # profit level that locks in (sl_to_tp1/breakeven_r_decay) or the fixed TP (fixed_tp)
    trail_dollars: float = 3.0       # trailing distance once armed, any exit_style
    exit_style: str = "sl_to_tp1"    # "sl_to_tp1" (live default), "breakeven_r_decay" (live, opt-in), or "fixed_tp" (comparison only)
    max_trades_per_day: int = 0      # 0 = unlimited

    # --- ATR-adaptive initial stop-loss (opt-in; entry SL ONLY) - when
    #     sl_mode="atr", executor.execute() derives the initial stop
    #     distance from the symbol's own recent volatility (ATR) instead of
    #     the fixed sl_dollars, clamped to [sl_dollars_min, sl_dollars_max]
    #     (still USD, at fixed_lot) as a safety rail against a wild ATR
    #     reading. This does NOT touch tp1_dollars/trail_dollars, which stay
    #     fixed dollar amounts either way: ClaudeSMC_TradeManager.mq5's exit
    #     logic only ever reads those two INPUT values, not anything
    #     computed here at entry time - there is no live channel to hand it
    #     a per-trade ATR-derived lock/trail distance, only the entry SL is
    #     Python's own decision to make. Off by default: sl_mode="fixed"
    #     keeps the old behavior (sl_dollars, always) unchanged.
    sl_mode: str = "fixed"           # "fixed" (default) or "atr"
    sl_atr_mult: float = 1.5         # sl_mode="atr" only: stop = ATR x this multiplier
    sl_atr_period: int = 14
    sl_atr_timeframe: str = "M15"    # sl_mode="atr" only: usually primary_timeframe
    sl_dollars_min: float = 3.0      # sl_mode="atr" only: floor, in USD at fixed_lot
    sl_dollars_max: float = 15.0     # sl_mode="atr" only: ceiling, in USD at fixed_lot

    # --- breakeven_r_decay only - see the module docstring; enforced by the
    #     MQL5 EAs, not this script, so these three exist purely as the one
    #     documented source of truth to mirror into InpBreakevenAtrMult/
    #     InpAtrTimeframe+InpAtrPeriod/InpDecayWindowMinutes by hand ---
    breakeven_atr_mult: float = 0.5       # move SL to breakeven once profit reaches this x M5 ATR
    breakeven_atr_period: int = 14
    decay_window_minutes: float = 15.0    # force breakeven after this long even short of the ATR trigger

    # Other magic numbers to fold into max_open_positions_per_direction's own
    # count - empty by default (unchanged behavior: the cap only ever counts
    # this system's own `magic`). Set this when a single unified EA (see
    # ../../UnifiedTrader/) is also opening same-direction XAUUSD positions
    # under a different magic number (e.g. Telegram-sourced trades) and the
    # two sources are meant to share ONE combined ceiling rather than
    # max_open_positions_per_direction each. This wires up WHICH positions
    # get counted together on the Python side - it does NOT keep the two
    # ceilings themselves in sync: UnifiedTrader_EA.mq5's own
    # InpMaxPositionsPerDirection is a separate number in a separate file,
    # and must be set to the SAME value as max_open_positions_per_direction
    # by hand, or the "shared" cap silently becomes asymmetric (whichever
    # side has the lower number stops first, the other keeps opening past
    # it) even though the magic-number wiring here is correct. See
    # mt5_gateway.count_same_direction() and main.py's startup warning.
    shared_cap_magic_numbers: list = field(default_factory=list)

    # --- Consensus-aware context (optional, purely informational; empty by
    #     default) - magic numbers of OTHER trading systems on this same
    #     account/symbol (e.g. UnifiedTrader_EA.mq5's own
    #     InpTelegramMagicNumber) whose open positions market_intel.
    #     consensus_context() summarizes into the snapshot Claude sees, so
    #     it can weigh whether the other system already agrees or disagrees
    #     with the setup. Read-only: unlike shared_cap_magic_numbers above,
    #     this never affects executor.gate() or any position cap - it only
    #     ever changes what Claude reads, never what's allowed to execute.
    #     Can safely be set to the SAME numbers as shared_cap_magic_numbers
    #     if both behaviors are wanted from the same other system(s).
    consensus_magic_numbers: list = field(default_factory=list)

    # --- Telegram alert (optional, send-only - see telegram_alert.py) ---
    # Fires on EVERY "full" conviction verdict from Claude, whether or not it
    # actually executes (executor.gate() can still reject it - position cap,
    # daily trade limit, confluence floor - the alert message says so either
    # way). Completely independent of the Telegram signal-copying stack
    # elsewhere in this repo (../../python/, ../../MQL5/, ../UnifiedTrader/'s
    # own Telegram side) - a dedicated bot is recommended so this alert
    # traffic never mixes with that stack's chat. Off by default: leave
    # either field blank and send_alert() is a safe no-op.
    telegram_alert_bot_token: str = ""   # from @BotFather
    telegram_alert_chat_id: str = ""     # your own chat id - DM the bot, then GET
                                         # https://api.telegram.org/bot<token>/getUpdates to find it

    # Daily (every UTC day roll) + weekly (every UTC Sunday->Monday roll)
    # performance digest, reusing the same telegram_alert_bot_token/
    # telegram_alert_chat_id above - see main.py's send_performance_digests()
    # and telegram_alert.format_performance_digest(). Takes effect only when
    # those credentials are actually set; this flag alone changes nothing.
    send_performance_digest: bool = True

    # --- "Why" button (optional; on by default, harmless if the EA side
    #     isn't set up for it) - main.py writes the latest Claude verdict's
    #     reasoning to this filename in MT5's shared Common\Files folder
    #     (see mt5_gateway.write_common_file()) after every evaluation
    #     cycle, so UnifiedTrader_EA.mq5's "Why" Telegram command/button can
    #     echo it back on demand. MUST match that EA's own
    #     InpLastVerdictFilename input, or the button will just report "no
    #     verdict on file yet" forever. Set to "" to disable writing the
    #     file at all.
    last_verdict_filename: str = "claudesmc_last_verdict.txt"

    # --- PauseClaudeHab / ResumeClaudeHab (UnifiedTrader_EA.mq5 Telegram
    #     buttons) - the EA writes "paused" or "running" to this file in
    #     MT5's shared Common\Files folder; main.py reads it at the start of
    #     every cycle and, while it says "paused", skips the whole evaluation
    #     (no Claude call, no order). A missing file means running, so this is
    #     harmless without UnifiedTrader_EA. MUST match the EA's
    #     InpClaudePauseFilename. "" disables the check.
    claude_pause_filename: str = "claudesmc_pause.txt"

    # --- Heartbeat / stale-cycle alert (reuses telegram_alert_bot_token/
    #     telegram_alert_chat_id above; see main.py's Heartbeat class) ---
    # Periodic "still alive" ping, independent of any trading activity -
    # useful on a quiet day with no signals, to confirm the process itself
    # hasn't silently died. Off by default: 0 disables it (a genuine
    # opt-in, unlike stale_cycle_alert_minutes below, since a recurring
    # ping is more a nice-to-have than a safety feature).
    heartbeat_interval_hours: float = 0.0
    # A ONE-TIME (latched until the next successful pass) warning if too
    # long passes without the poll loop completing a pass without an
    # unexpected exception - catches a bot that's technically still
    # running but stuck in a repeating-error loop (dropped MT5 connection,
    # etc.) rather than genuinely evaluating cycles. On by default (once
    # telegram creds are set) since a live trading bot silently going
    # stale is a real risk this exists specifically to catch. A
    # ClaudeUnavailableError does NOT count as "stuck" here - that already
    # has its own distinct backoff/logging (see main.py) - only the
    # generic except-Exception path does.
    stale_cycle_alert_minutes: float = 60.0

    # --- Order plumbing ---
    magic: int = 20260921
    comment: str = "Claude_Sig"
    deviation_points: int = 30

    # --- Confluence gate (mirrors the existing 3-confluence framework:
    #     Trend / Momentum / Strength - see python/market_intel.py) ---
    min_confluence_count: int = 2        # at least 2 of 3 must agree
    require_full_conviction: bool = True  # AND Claude's own conviction must be "full"

    # --- Timeframes the feature snapshot is built from ---
    primary_timeframe: str = "M15"    # the "new bar" gate and most SMC structure
    trend_timeframe: str = "H4"       # macro trend bias (EMA200)
    bars_per_timeframe: int = 300

    # --- SMC structure (liquidity sweep / premium-discount) ---
    sweep_recent_bars: int = 20
    sweep_ref_bars: int = 30
    sweep_min_pierce_pips: float = 3.0

    # --- SMC structure (market structure / order blocks / fair value gaps) ---
    structure_swing_order: int = 3               # bars confirmed each side of a swing point (fractal)
    order_block_lookback_bars: int = 50
    order_block_displacement_atr_mult: float = 1.5
    fvg_lookback_bars: int = 50

    # --- Claude ---
    claude_model: str = "claude-opus-5"
    claude_max_tokens: int = 2000

    # --- Loop ---
    poll_seconds: int = 30            # how often to check for a new closed bar
    dry_run: bool = True

    # --- Logging ---
    log_dir: str = "logs"
