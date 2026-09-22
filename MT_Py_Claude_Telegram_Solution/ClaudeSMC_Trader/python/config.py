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

    # --- Execution rules (requested, fixed) ---
    fixed_lot: float = 0.01
    max_open_positions_per_direction: int = 5
    sl_dollars: float = 6.0
    tp1_dollars: float = 6.0         # profit level that locks in (sl_to_tp1/breakeven_r_decay) or the fixed TP (fixed_tp)
    trail_dollars: float = 3.0       # trailing distance once armed, any exit_style
    exit_style: str = "sl_to_tp1"    # "sl_to_tp1" (live default), "breakeven_r_decay" (live, opt-in), or "fixed_tp" (comparison only)
    max_trades_per_day: int = 0      # 0 = unlimited

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
