"""
SignalVerifier - decides whether a parsed Telegram signal is safe to copy.

Deliberately pure: every market fact it needs (current price, spread, the
broker's minimum stop distance, how many positions/trades already happened
today) is passed in rather than fetched, so it can be unit-tested without an
MT5 connection or a Telegram session (see copier_selftest.py) and reused
identically by the live copier and by `telegram_copier.py --replay`.

Checks, in order (first failure wins - reasons stop accumulating once
rejected, so the message tells you the one thing that actually mattered):
    1. it must be an "open" signal (close/cancel/modify_sl are handled
       separately by the copier, not by this verifier)
    2. a clear BUY/SELL direction
    3. a symbol this copier is configured to trade
    4. the source chat is on the allow-list (if one is configured)
    5. not stale (older than max_signal_age_seconds)
    6. not a duplicate of a signal seen in the last dedupe_window_seconds
    7. room under max_open_positions / max_trades_per_day
    8. the current price hasn't moved too far from the signaled entry
    9. a stop-loss exists (or the fallback is enabled) and sits on the
       correct side of price
   10. the stop distance clears the spread, the broker's minimum stop
       distance, and configured min/max sanity bounds
   11. take-profits are on the profit side of price, and the nearest one
       clears min_risk_reward if that's configured

A signal that survives all of this is not a guarantee of a good trade - it
only means the message parsed cleanly and the numbers in it aren't obviously
broken or stale. It says nothing about whether the call itself is any good.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from copier_config import CopierConfig
from signal_parser import ParsedSignal


@dataclass
class Verdict:
    accepted: bool
    reasons: list = field(default_factory=list)
    direction: str | None = None
    entry_reference: float | None = None
    sl: float | None = None
    tps: list = field(default_factory=list)
    risk_reward: float | None = None


class SignalVerifier:
    def __init__(self, cfg: CopierConfig):
        self.cfg = cfg
        self._seen: dict = {}   # dedupe key -> last-seen timestamp

    def resolve_symbol(self, sig: ParsedSignal) -> str | None:
        if sig.symbol is None:
            return None
        return self.cfg.symbol_aliases.get(sig.symbol.upper(), sig.symbol.upper())

    def _dedupe_key(self, sig: ParsedSignal, chat_id) -> str:
        return f"{chat_id}:{sig.direction}:{sig.symbol}:{sig.sl}:{tuple(sig.tps)}"

    def is_duplicate(self, sig: ParsedSignal, chat_id, now: float) -> bool:
        key = self._dedupe_key(sig, chat_id)
        last = self._seen.get(key)
        self._seen[key] = now
        return last is not None and (now - last) < self.cfg.dedupe_window_seconds

    def verify(self, sig: ParsedSignal, *, chat_id, current_price: float, point: float,
               spread_price: float, min_stop_price: float, open_positions: int,
               trades_today: int, message_age_seconds: float = 0.0,
               now: float | None = None) -> Verdict:
        cfg = self.cfg
        now = now if now is not None else time.time()

        if sig.action != "open":
            return Verdict(False, [f"not an open signal (action={sig.action})"])

        if sig.direction not in ("buy", "sell"):
            return Verdict(False, ["no clear BUY/SELL direction found"])

        symbol = self.resolve_symbol(sig)
        if symbol is None:
            return Verdict(False, ["no recognisable symbol in the message"])
        if symbol != cfg.symbol:
            return Verdict(False, [f"symbol {symbol} is not the configured {cfg.symbol}"])

        if cfg.allowed_chats and str(chat_id) not in cfg.allowed_chats:
            return Verdict(False, [f"chat {chat_id} is not on the allow-list"])

        if message_age_seconds > cfg.max_signal_age_seconds:
            return Verdict(False, [
                f"signal is {message_age_seconds:.0f}s old "
                f"(> {cfg.max_signal_age_seconds:.0f}s) - too stale to copy"
            ])

        if self.is_duplicate(sig, chat_id, now):
            return Verdict(False, [
                f"duplicate of a signal seen in the last {cfg.dedupe_window_seconds:.0f}s"
            ])

        if open_positions >= cfg.max_open_positions:
            return Verdict(False, [f"max open positions reached ({cfg.max_open_positions})"])
        if cfg.max_trades_per_day and trades_today >= cfg.max_trades_per_day:
            return Verdict(False, [f"max trades/day reached ({cfg.max_trades_per_day})"])

        entry_ref = sig.entry_mid
        if entry_ref is not None:
            unit = cfg.unit_size(point)
            deviation = abs(current_price - entry_ref) / unit if unit else 0.0
            if deviation > cfg.max_price_deviation_units:
                return Verdict(False, [
                    f"current price {current_price:.2f} is {deviation:.1f} "
                    f"{cfg.distance_unit}s from the signaled entry {entry_ref:.2f} "
                    f"(max {cfg.max_price_deviation_units:g})"
                ])
        else:
            entry_ref = current_price

        notes: list = []
        sl = sig.sl
        if sl is None:
            if not cfg.allow_missing_sl_fallback:
                return Verdict(False, ["signal has no stop-loss and allow_missing_sl_fallback is off"])
            fallback_dist = cfg.default_sl_units * cfg.unit_size(point)
            sl = entry_ref - fallback_dist if sig.direction == "buy" else entry_ref + fallback_dist
            notes.append(f"no SL in the signal - using the {cfg.default_sl_units:g} "
                         f"{cfg.distance_unit} fallback")

        if sig.direction == "buy" and sl >= entry_ref:
            return Verdict(False, [f"SL {sl:.2f} is not below the entry/price {entry_ref:.2f} for a BUY"])
        if sig.direction == "sell" and sl <= entry_ref:
            return Verdict(False, [f"SL {sl:.2f} is not above the entry/price {entry_ref:.2f} for a SELL"])

        sl_dist = abs(entry_ref - sl)
        sl_units = sl_dist / cfg.unit_size(point)
        if sl_units < cfg.min_sl_units:
            return Verdict(False, [
                f"SL distance {sl_units:.1f} {cfg.distance_unit}s is below the "
                f"minimum {cfg.min_sl_units:g}"
            ])
        if sl_units > cfg.max_sl_units:
            return Verdict(False, [
                f"SL distance {sl_units:.1f} {cfg.distance_unit}s exceeds the "
                f"maximum {cfg.max_sl_units:g} - looks like a fat-finger"
            ])
        if sl_dist <= spread_price:
            return Verdict(False, [
                f"SL distance {sl_dist:.2f} is inside the current spread "
                f"{spread_price:.2f} - would stop out the instant it opens"
            ])
        if min_stop_price and sl_dist < min_stop_price:
            return Verdict(False, [
                f"SL distance {sl_dist:.2f} is inside the broker's minimum stop "
                f"distance {min_stop_price:.2f}"
            ])

        tps = []
        for tp in sig.tps:
            on_profit_side = (tp > entry_ref) if sig.direction == "buy" else (tp < entry_ref)
            if on_profit_side:
                tps.append(tp)
            else:
                notes.append(f"TP {tp:.2f} is on the wrong side of the entry for a "
                             f"{sig.direction.upper()} - dropped")

        rr = None
        if tps:
            nearest_dist = min(abs(tp - entry_ref) for tp in tps)
            rr = nearest_dist / sl_dist if sl_dist > 0 else None
            if cfg.min_risk_reward and rr is not None and rr < cfg.min_risk_reward:
                return Verdict(False, [
                    f"risk:reward {rr:.2f} is below the minimum {cfg.min_risk_reward:g}"
                ])

        return Verdict(True, notes, direction=sig.direction, entry_reference=entry_ref,
                        sl=sl, tps=tps, risk_reward=rr)
