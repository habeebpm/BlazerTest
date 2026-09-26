"""
Split entries: one trade sent as several positions ("legs") that share the
lot, the stop and the 2% risk.

  - Claude (gold, config.claude_split): three legs. Leg 1 carries a broker
    take-profit at +tp1_dollars; legs 2 and 3 have none - once price reaches
    +tp1_dollars their stop goes to break-even (the entry) and trails
    trail_dollars behind price, tightening only.
  - Telegram (UnifiedTrader_EA InpTelegramSplit): two legs the same way
    (+$4 take-profit / break-even and $3 trail).

Legs 2+ carry LEG_MARK in their order comment ("Claude_Sig|L2"). That is how
the same-direction cap counts a split entry ONCE (5 per direction = 5 trades,
each risking 2%, however many legs) and how UnifiedTrader_EA knows which
Claude positions get the break-even-then-trail exit. If a broker ever
dropped the comment, a leg would simply be counted as its own trade and
managed like a single position (lock at +tp1, then trail) - the safe side.
Must match UnifiedTrader_EA.mq5's LEG_MARK.
"""
from __future__ import annotations

LEG_MARK = "|L"
COMMENT_MAX = 31          # MT5's order comment limit


def comment_for(base: str, leg: int) -> str:
    """Order comment of leg `leg` (1-based): the plain comment for leg 1."""
    if leg <= 1:
        return base
    tag = f"{LEG_MARK}{leg}"
    return base[:COMMENT_MAX - len(tag)] + tag


def is_follower(comment) -> bool:
    """True for legs 2+ of a split entry (never counted as their own trade)."""
    return LEG_MARK in str(comment or "")


def leg_number(comment) -> int:
    """2, 3, ... for a follower leg; 1 otherwise (leg 1 or an unsplit trade)."""
    text = str(comment or "")
    i = text.rfind(LEG_MARK)
    if i < 0:
        return 1
    digits = ""
    for ch in text[i + len(LEG_MARK):]:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else 1


def split_entry(cfg) -> bool:
    """Whether Claude's entries go out as split legs (config.claude_split):
    gold's dollar lock only - the BTC R-based lock and the backtest-only
    fixed_tp style always send one position."""
    return (bool(getattr(cfg, "claude_split", False)) and getattr(cfg, "lock_mode", "dollars") != "r"
            and getattr(cfg, "exit_style", "") in ("sl_to_tp1", "breakeven_r_decay"))


def split_lots(total: float, volume_min: float, volume_step: float, legs: int) -> list:
    """`total` (already a valid lot) shared out over up to `legs` positions,
    each a multiple of volume_step and at least volume_min; the sum is
    exactly `total`. Fewer legs when the lot is too small to split that far
    (0.02 lot -> two legs, 0.01 -> one)."""
    step = volume_step if volume_step and volume_step > 0 else 0.01
    units = int(round(total / step))
    min_units = max(1, int(round((volume_min or step) / step)))
    if units < min_units:
        return [round(total, 6)]
    n = max(1, min(int(legs or 1), units // min_units))
    base, extra = divmod(units, n)
    return [round((base + (1 if i < extra else 0)) * step, 6) for i in range(n)]
