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

    # --- Execution rules (requested, fixed) ---
    fixed_lot: float = 0.01
    max_open_positions_per_direction: int = 5

    # --- Equity-scaled lot sizing (opt-in; ports ../../python/mt5_client.py's
    #     position_size()/TradeConfig.use_risk_percent pattern) - when
    #     use_risk_percent is set, executor.execute() sizes each trade from
    #     current equity instead of always using fixed_lot: the SAME price
    #     distance sl_dollars/fixed_lot implies (see sl_dollars' own comment)
    #     is held fixed, and the lot is solved for so that price distance
    #     times that lot risks exactly risk_percent% of equity, then clamped
    #     to [volume_min, volume_max, max_lot_size] and rounded down to the
    #     broker's volume_step. ClaudeSMC_TradeManager.mq5 needs no change
    #     either way - it already recomputes InpTp1Dollars/InpTrailDollars'
    #     price distance from each position's own live volume (see its
    #     DollarsToPrice()), so a bigger lot still locks/trails at the same
    #     dollar amounts. Off by default: fixed_lot keeps its old meaning
    #     (the ONLY lot ever traded) until this is turned on.
    use_risk_percent: bool = False
    risk_percent: float = 0.2       # 0.2% = a 1.0% max_daily_loss_pct / 5 - see config.py's daily-loss fields
    max_lot_size: float = 5.0       # hard cap on a risk-sized lot, regardless of how large equity grows

    # --- Daily loss circuit breaker (mirrors ../../python/trader.py's
    #     Bot.roll_day()/entry_blocked() daily-loss pattern) - main.py's
    #     DayRoll tracks account equity from the first cycle of each UTC day
    #     and withholds NEW entries once the day's move breaches these
    #     limits. Existing open positions are left alone -
    #     ClaudeSMC_TradeManager.mq5 already owns exit management, so this
    #     never closes anything itself, only executor.gate() refusing new
    #     signals. Off by default: max_daily_loss_pct=0 disables the check.
    max_daily_loss_pct: float = 0.0        # 0 = disabled; e.g. 3.0 = stop new entries after -3% on the day
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
