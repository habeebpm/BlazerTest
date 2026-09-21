"""
All tunables for the Claude-advised XAUUSD trader in one place.

Deliberately a plain dataclass with CLI overrides in main.py (see --help) -
no env-var magic, no config file format to learn. The defaults below are
exactly the trading rules requested: fixed 0.01 lot, max 5 concurrent
same-direction positions, $6 stop-loss, $6 take-profit that becomes a $3
trail once armed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdvisorConfig:
    # --- Instrument ---
    symbol: str = "XAUUSD"

    # --- Execution rules (requested, fixed) ---
    fixed_lot: float = 0.01
    max_open_positions_per_direction: int = 5
    sl_dollars: float = 6.0
    tp_arm_dollars: float = 6.0      # initial broker TP; also the profit level that arms trailing
    trail_dollars: float = 3.0       # trailing distance once armed
    max_trades_per_day: int = 0      # 0 = unlimited

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

    # --- Claude ---
    claude_model: str = "claude-opus-5"
    claude_max_tokens: int = 2000

    # --- Loop ---
    poll_seconds: int = 30            # how often to check for a new closed bar
    dry_run: bool = True

    # --- Logging ---
    log_dir: str = "logs"
