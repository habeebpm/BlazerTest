"""
All tunables for the Claude-advised XAUUSD trader in one place.

Deliberately a plain dataclass with CLI overrides in main.py (see --help) -
no env-var magic, no config file format to learn. The defaults below are
the trading rules requested: fixed 0.01 lot, max 5 concurrent same-direction
positions, $6 stop-loss, SL moved to lock in $6 profit once TP1 is reached,
trailing $3 behind price from there (exit_style="sl_to_tp1" - see below).

exit_style has two values, both implemented in executor.py/backtest.py so
they can be measured against each other (see backtest.py --compare):
  - "sl_to_tp1" (default, and what ClaudeSMC_TradeManager.mq5 implements
    live): no broker-side take-profit is ever placed. Once floating profit
    reaches tp1_dollars, the stop-loss is moved to lock in exactly that
    much profit, then trails trail_dollars behind new highs/lows from
    there. Chosen over the older design below because it has no broker
    order sitting at the same price as the arm threshold - nothing for
    the broker to auto-fill ahead of the stop being moved.
  - "fixed_tp" (the original design, kept only so backtest.py --compare
    has something concrete to measure the change against): a real broker
    take-profit is placed at entry + tp1_dollars - which is the SAME
    price the trail would arm at, so in practice the standing TP order
    almost always wins that race and the trail rarely gets a chance to
    engage. Not implemented in the live MQL5 EA.
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
    tp1_dollars: float = 6.0         # profit level that locks in (sl_to_tp1) or the fixed TP (fixed_tp)
    trail_dollars: float = 3.0       # trailing distance once armed, either exit_style
    exit_style: str = "sl_to_tp1"    # "sl_to_tp1" (recommended, live default) or "fixed_tp" (comparison only)
    max_trades_per_day: int = 0      # 0 = unlimited

    # Other magic numbers to fold into max_open_positions_per_direction's own
    # count - empty by default (unchanged behavior: the cap only ever counts
    # this system's own `magic`). Set this when a single unified EA (see
    # ../../UnifiedTrader/) is also opening same-direction XAUUSD positions
    # under a different magic number (e.g. Telegram-sourced trades) and the
    # two sources are meant to share ONE combined 5-per-direction ceiling
    # rather than 5 each. See mt5_gateway.count_same_direction().
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
