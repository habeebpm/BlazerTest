"""
Pure glue between a raw message, SignalVerifier and an MT5 order shape.

`evaluate_signal` touches no MT5 connection and no Telegram client - every
market fact (current price, spec, equity, how many positions/trades already
happened) is passed in. That makes it usable from three places with identical
logic: the live Copier in telegram_copier.py, `telegram_copier.py --replay`
(synthetic market numbers, no MT5/Telegram needed) and copier_selftest.py.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import mt5_client as mc
from copier_config import CopierConfig
from signal_parser import ParsedSignal, parse_signal
from verifier import SignalVerifier, Verdict


@dataclass
class Evaluation:
    signal: ParsedSignal
    verdict: Verdict
    lots: float = 0.0
    request: dict | None = None    # the order shape that WOULD be sent, or None if rejected


def build_request(cfg: CopierConfig, spec: mc.SymbolSpec, verdict: Verdict, lots: float) -> dict:
    tp = verdict.tps[0] if verdict.tps else 0.0
    return {
        "symbol": cfg.symbol,
        "direction": verdict.direction,
        "volume": round(lots, 2),
        "reference_price": round(verdict.entry_reference, spec.digits),
        "sl": round(verdict.sl, spec.digits) if verdict.sl is not None else 0.0,
        "tp": round(tp, spec.digits) if tp else 0.0,
        "deviation": cfg.deviation_points,
        "magic": cfg.magic,
        "comment": cfg.comment,
        "further_targets": [round(t, spec.digits) for t in verdict.tps[1:]],
    }


def evaluate_signal(cfg: CopierConfig, verifier: SignalVerifier, text: str, chat_id,
                     message_time: float, spec: mc.SymbolSpec, *, current_price: float,
                     open_positions: int, trades_today: int, equity: float = 0.0,
                     now: float | None = None, parsed: ParsedSignal | None = None) -> Evaluation:
    # A caller that already parsed `text` for its own routing (e.g. Copier.on_message
    # deciding open/close/cancel/modify_sl) can pass that result in as `parsed` to
    # avoid running the same regex scan over the same text twice.
    sig = parsed if parsed is not None else parse_signal(text, symbol_aliases=cfg.symbol_aliases)
    now = now if now is not None else time.time()
    age = max(0.0, now - message_time)

    verdict = verifier.verify(
        sig, chat_id=chat_id, current_price=current_price, point=spec.point,
        spread_price=spec.spread_points * spec.point,
        min_stop_price=spec.stops_level_points * spec.point,
        open_positions=open_positions, trades_today=trades_today,
        message_age_seconds=age, now=now,
    )

    if not verdict.accepted:
        return Evaluation(signal=sig, verdict=verdict)

    sl_distance = abs(verdict.entry_reference - verdict.sl)
    lots = mc.position_size_for(
        spec, sl_distance, lots=cfg.lots, use_risk_percent=cfg.use_risk_percent,
        risk_percent=cfg.risk_percent, max_lot_size=cfg.max_lot_size, equity=equity,
    )
    request = build_request(cfg, spec, verdict, lots)
    return Evaluation(signal=sig, verdict=verdict, lots=lots, request=request)
