"""
Telegram copier + verifier self-test - runs anywhere, no MT5 terminal, no
MetaTrader5 package and no Telegram/Telethon session needed.

Exercises:
  * signal_parser against the message shapes actually seen in gold-signal
    channels (single price, entry zones, multiple TPs, pending orders,
    close/cancel/breakeven commands, unparseable noise);
  * SignalVerifier's rejection reasons one at a time (stale, duplicate,
    wrong symbol, wrong-side SL, too tight/too wide SL, missing SL, weak
    risk:reward, position/trade caps, chat allow-list, price too far from
    the signal);
  * copier_engine.evaluate_signal end-to-end, including risk-percent lot
    sizing.
"""
from __future__ import annotations

import mt5_client as mc
import copier_engine as ce
from copier_config import CopierConfig
from signal_parser import parse_signal
from verifier import SignalVerifier


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def make_spec(**overrides) -> mc.SymbolSpec:
    base = dict(
        name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
        volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0,
        tick_size=0.01, filling_mode=0,
    )
    base.update(overrides)
    return mc.SymbolSpec(**base)


def test_parser() -> bool:
    print("\n=== 1. signal_parser ===")
    ok = True

    sig = parse_signal("BUY XAUUSD @ 2350.00\nSL: 2340.00\nTP1: 2360.00\nTP2: 2370.00")
    ok &= check("single-price BUY with two TPs", sig.action == "open" and sig.direction == "buy"
                and sig.symbol == "XAUUSD" and sig.entry == 2350.0 and sig.sl == 2340.0
                and sig.tps == [2360.0, 2370.0], sig)

    sig = parse_signal("\U0001F534 GOLD SELL NOW\nEntry 2355-2358\nStop Loss 2365\nTake Profit 2345")
    ok &= check("entry-zone SELL, midpoint used", sig.direction == "sell" and sig.symbol == "XAUUSD"
                and sig.entry == 2355.0 and sig.entry_high == 2358.0
                and sig.entry_mid == 2356.5 and sig.sl == 2365.0 and sig.tps == [2345.0], sig)

    sig = parse_signal("Buy Limit Gold 2340 sl 2330 tp 2360")
    ok &= check("pending BUY LIMIT", sig.direction == "buy" and sig.order_type == "limit"
                and sig.sl == 2330.0 and sig.tps == [2360.0], sig)

    sig = parse_signal("CLOSE ALL XAUUSD NOW")
    ok &= check("close command", sig.action == "close", sig)

    sig = parse_signal("Move SL to breakeven on GOLD buy")
    ok &= check("breakeven command", sig.action == "modify_sl" and sig.direction == "buy", sig)

    sig = parse_signal("CANCEL the gold sell order")
    ok &= check("cancel command", sig.action == "cancel" and sig.direction == "sell", sig)

    sig = parse_signal("good morning traders, have a great day")
    ok &= check("unrelated chatter parses as unknown", sig.action == "unknown"
                and bool(sig.errors), sig)

    sig = parse_signal("BUY GOLD NOW")
    ok &= check("direction with no SL/TP flags both as missing", sig.action == "open"
                and sig.sl is None and not sig.tps and len(sig.errors) == 2, sig)

    sig = parse_signal("BUY GOLD 2350 SL 2340 TP 2360, move SL to breakeven after TP1")
    ok &= check("a breakeven MENTION inside a real open signal must not eat the signal",
                sig.action == "open" and sig.direction == "buy" and sig.entry == 2350.0
                and sig.sl == 2340.0 and sig.tps == [2360.0], sig)

    sig = parse_signal("BUY XAUUSD @ 2350 Stop-Loss 2340 TP 2360")
    ok &= check("hyphenated Stop-Loss is recognised", sig.sl == 2340.0, sig)

    return ok


def test_verifier() -> bool:
    print("\n=== 2. SignalVerifier ===")
    ok = True
    spec = make_spec()
    common = dict(current_price=2350.0, point=spec.point,
                  spread_price=spec.spread_points * spec.point,
                  min_stop_price=spec.stops_level_points * spec.point,
                  open_positions=0, trades_today=0)

    cfg = CopierConfig()
    v = SignalVerifier(cfg)
    good = parse_signal("BUY XAUUSD @ 2350 SL 2340 TP 2365")
    verdict = v.verify(good, chat_id="chan1", **common)
    ok &= check("a clean signal is accepted", verdict.accepted, verdict.reasons)
    ok &= check("risk:reward computed from the nearest TP",
                verdict.risk_reward is not None and abs(verdict.risk_reward - 1.5) < 1e-6,
                verdict.risk_reward)

    dup = v.verify(good, chat_id="chan1", **common)
    ok &= check("an identical signal moments later is deduped", not dup.accepted, dup.reasons)

    v1b = SignalVerifier(cfg)
    capped = dict(common)
    capped["open_positions"] = cfg.max_open_positions
    blocked = v1b.verify(good, chat_id="chan1", **capped)
    ok &= check("a signal rejected for an unrelated reason (position cap) is not accepted",
                not blocked.accepted, blocked.reasons)
    retry = v1b.verify(good, chat_id="chan1", **common)
    ok &= check("the same signal retried once the cap clears is accepted, not falsely deduped",
                retry.accepted, retry.reasons)

    v2 = SignalVerifier(cfg)
    stale = v2.verify(good, chat_id="chan1", message_age_seconds=999, **common)
    ok &= check("a stale signal is rejected", not stale.accepted, stale.reasons)

    v3 = SignalVerifier(cfg)
    wrong_symbol = v3.verify(parse_signal("BUY EURUSD @ 1.10 SL 1.09 TP 1.12"),
                              chat_id="chan1", **common)
    ok &= check("an off-symbol signal is rejected", not wrong_symbol.accepted, wrong_symbol.reasons)

    v4 = SignalVerifier(cfg)
    wrong_side = v4.verify(parse_signal("BUY XAUUSD @ 2350 SL 2360 TP 2370"),
                            chat_id="chan1", **common)
    ok &= check("SL on the wrong side of a BUY is rejected", not wrong_side.accepted,
                wrong_side.reasons)

    v5 = SignalVerifier(cfg)
    too_tight = v5.verify(parse_signal("BUY XAUUSD @ 2350 SL 2349.9 TP 2360"),
                           chat_id="chan1", **common)
    ok &= check("an SL inside the spread is rejected", not too_tight.accepted, too_tight.reasons)

    v6 = SignalVerifier(cfg)
    too_wide = v6.verify(parse_signal("BUY XAUUSD @ 2350 SL 2000 TP 2500"),
                          chat_id="chan1", **common)
    ok &= check("a fat-finger SL far past max_sl_units is rejected", not too_wide.accepted,
                too_wide.reasons)

    v7 = SignalVerifier(cfg)
    no_sl = v7.verify(parse_signal("BUY XAUUSD NOW"), chat_id="chan1", **common)
    ok &= check("no SL and no fallback configured is rejected", not no_sl.accepted, no_sl.reasons)

    v8 = SignalVerifier(CopierConfig(allow_missing_sl_fallback=True, default_sl_units=60))
    fallback = v8.verify(parse_signal("BUY XAUUSD NOW"), chat_id="chan1", **common)
    ok &= check("no SL but fallback enabled is accepted with a computed SL",
                fallback.accepted and fallback.sl == 2350.0 - 60 * cfg.unit_size(spec.point),
                fallback.sl)

    v9 = SignalVerifier(CopierConfig(min_risk_reward=3.0))
    weak_rr = v9.verify(parse_signal("BUY XAUUSD @ 2350 SL 2340 TP 2355"),
                         chat_id="chan1", **common)
    ok &= check("risk:reward below the configured minimum is rejected", not weak_rr.accepted,
                weak_rr.reasons)

    v9b = SignalVerifier(CopierConfig(min_risk_reward=3.0))
    no_tp_rr = v9b.verify(parse_signal("BUY XAUUSD @ 2350 SL 2340"), chat_id="chan1", **common)
    ok &= check("a minimum risk:reward with no usable TP fails closed, not silently through",
                not no_tp_rr.accepted, no_tp_rr.reasons)

    v10 = SignalVerifier(cfg)
    capped = v10.verify(good, chat_id="chan2", current_price=2350.0, point=spec.point,
                         spread_price=common["spread_price"], min_stop_price=common["min_stop_price"],
                         open_positions=cfg.max_open_positions, trades_today=0)
    ok &= check("max open positions blocks a new entry", not capped.accepted, capped.reasons)

    v11 = SignalVerifier(CopierConfig(allowed_chats=["trusted_chan"]))
    unauthorized = v11.verify(good, chat_id="random_chan", **common)
    ok &= check("a chat off the allow-list is rejected", not unauthorized.accepted,
                unauthorized.reasons)

    v12 = SignalVerifier(cfg)
    far_price = v12.verify(good, chat_id="chan3", current_price=2500.0, point=spec.point,
                            spread_price=common["spread_price"],
                            min_stop_price=common["min_stop_price"],
                            open_positions=0, trades_today=0)
    ok &= check("price too far from the signaled entry is rejected", not far_price.accepted,
                far_price.reasons)

    return ok


def test_engine() -> bool:
    print("\n=== 3. copier_engine.evaluate_signal ===")
    ok = True
    spec = make_spec()
    cfg = CopierConfig(use_risk_percent=True, risk_percent=0.2, max_lot_size=5.0)
    verifier = SignalVerifier(cfg)

    ev = ce.evaluate_signal(
        cfg, verifier, "BUY XAUUSD @ 2350 SL 2340 TP 2370", chat_id="chan1",
        message_time=1000.0, spec=spec, current_price=2350.0, open_positions=0,
        trades_today=0, equity=10_000.0, now=1000.0,
    )
    ok &= check("accepted signal produces a request", ev.verdict.accepted and ev.request is not None,
                ev.request)
    ok &= check("risk-percent sizing scales with equity and clears the broker minimum",
                ev.lots >= spec.volume_min and ev.lots > 0, ev.lots)
    ok &= check("request carries the verified SL/TP", ev.request["sl"] == 2340.0
                and ev.request["tp"] == 2370.0, ev.request)

    rejected = ce.evaluate_signal(
        cfg, verifier, "just chatting, no signal here", chat_id="chan1",
        message_time=1000.0, spec=spec, current_price=2350.0, open_positions=0,
        trades_today=0, equity=10_000.0, now=1000.0,
    )
    ok &= check("a non-signal message produces no request", not rejected.verdict.accepted
                and rejected.request is None, rejected.verdict.reasons)

    return ok


def run() -> int:
    print("Telegram copier + verifier self-test")
    results = [test_parser(), test_verifier(), test_engine()]
    passed = all(results)
    print(f"\n{'ALL PASS' if passed else 'SOME FAILED'} ({sum(results)}/{len(results)} suites)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(run())
