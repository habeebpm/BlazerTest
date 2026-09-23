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
    sizing;
  * telegram_copier.record_signal() tagging every logged row with
    source=Telegram_Sig, so it's distinguishable from the Claude-SMC
    Trader's own logs (see ../ClaudeSMC_Trader/python/executor.py).
"""
from __future__ import annotations

import csv

import mt5_client as mc
import copier_engine as ce
import telegram_copier as tc
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


def test_signal_logging() -> bool:
    print("\n=== 4. signal logging ===")
    ok = True
    ok &= check("SIGNAL_LOG_FIELDS declares a source column", "source" in tc.SIGNAL_LOG_FIELDS,
                tc.SIGNAL_LOG_FIELDS)
    ok &= check("source is the LAST column, not inserted mid-row (backward compat - see "
                "record_signal's docstring)", tc.SIGNAL_LOG_FIELDS[-1] == "source", tc.SIGNAL_LOG_FIELDS)

    if tc.SIGNAL_LOG.exists():
        tc.SIGNAL_LOG.unlink()
    tc._warned_stale_signal_log_header = False

    tc.record_signal({
        "time": "2026-09-21T00:00:00+00:00", "chat_id": "test_chan",
        "direction": "buy", "entry_reference": 2350.0, "sl": 2340.0, "tp": 2360.0, "lots": 0.05,
        "fill_price": 2350.0, "mode": "dry-run", "retcode": "", "ticket": "", "source": "Telegram_Sig",
    })
    with tc.SIGNAL_LOG.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ok &= check("record_signal() writes source=Telegram_Sig to copier_signals.csv",
                rows and rows[-1]["source"] == "Telegram_Sig", rows[-1] if rows else None)

    # Simulate a log file that predates the "source" column (an already
    # running deployment upgrading to this build) and confirm appending a
    # new row under the new, longer field list does NOT corrupt the old
    # row's pre-existing columns - the whole point of appending "source" at
    # the end instead of inserting it after "time".
    tc.SIGNAL_LOG.write_text(
        "time,chat_id,direction,entry_reference,sl,tp,lots,fill_price,mode,retcode,ticket\r\n"
        "2026-09-20T00:00:00+00:00,old_chan,sell,2350.0,2360.0,2340.0,0.05,2350.0,dry-run,,\r\n",
        encoding="utf-8",
    )
    tc._warned_stale_signal_log_header = False
    tc.record_signal({
        "time": "2026-09-21T00:00:00+00:00", "chat_id": "new_chan", "direction": "buy",
        "entry_reference": 2350.0, "sl": 2340.0, "tp": 2360.0, "lots": 0.05,
        "fill_price": 2350.0, "mode": "dry-run", "retcode": "", "ticket": "", "source": "Telegram_Sig",
    })
    with tc.SIGNAL_LOG.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    old_row = rows[0]
    ok &= check("appending under a stale (pre-source) header does not corrupt the OLD row's "
                "pre-existing columns", old_row.get("chat_id") == "old_chan"
                and old_row.get("direction") == "sell", old_row)

    return ok



# Shared with the MQL5 EAs' TsmcClassifyMessage() - keep both tables in sync.
FILTER_CASES = [
    # (message, expected kind)
    ("Good morning traders ☀️ have a blessed day", "skip"),
    ("Good morning! Close your charts and relax \U0001F60C", "skip"),
    ("Close all your worries \U0001F60A", "skip"),
    ("Don't cancel your plans for the weekend", "skip"),
    ("We closed +200 pips yesterday, congrats team \U0001F389", "skip"),
    ("Gold will fly, buy the dip mindset \U0001F4AA", "skip"),
    ("3 reasons to buy gold this week", "skip"),
    ("Buy the dip 2650", "skip"),
    ("Market update: gold consolidating near 2650, waiting for NFP", "skip"),
    ("\U0001F680\U0001F680\U0001F680", "skip"),
    ("", "skip"),
    ("XAUUSD BUY NOW 2650-2647 SL 2640 TP1 2655 TP2 2660", "signal"),
    ("\U0001F525 GOLD SELL 2655/2658 \U0001F525\nSL: 2665\nTP: 2645", "signal"),
    ("BUY 2650-2645 SL 2640 TP 2660", "signal"),
    ("XAUUSD SELL 2655", "signal"),
    ("Close all gold trades now", "command"),
    ("CLOSE ALL", "command"),
    ("Exit now guys", "command"),
    ("Cancel the limit order", "command"),
    ("SL to BE ✅", "command"),
    ("Move SL to entry", "command"),
    ("Close half and move SL to breakeven", "command"),
]


class _FakeMsg:
    def __init__(self, **attrs):
        self.action = None
        self.message = attrs.pop("message", "")
        for k, v in attrs.items():
            setattr(self, k, v)


def test_message_filter() -> bool:
    print("\n=== 5. trade-only message filter (greetings/mood/long/media omitted) ===")
    import message_filter as mf
    import telegram_relay_bridge as bridge
    ok = True
    wrong = [(t, want, mf.classify_message(t)) for t, want in FILTER_CASES
             if mf.classify_message(t)[0] != want]
    ok &= check(f"all {len(FILTER_CASES)} sample messages classified as expected (signal / command / skip)",
                not wrong, wrong)
    long_signal = "XAUUSD BUY 2650 SL 2640 TP 2660 " + "analysis " * 60
    kind, why = mf.classify_message(long_signal)
    ok &= check("a long message is omitted even if it contains a signal", kind == "skip" and "long" in why, why)
    ok &= check("the length limits are configurable",
                mf.classify_message(long_signal, max_message_chars=0)[0] == "signal"
                and mf.classify_message("Close all gold trades now", max_command_chars=10)[0] == "skip")
    ok &= check("a copier configured for another symbol recognises it through its aliases",
                mf.classify_message("EURUSD BUY 1.0850", symbol_words={"EURUSD"})[0] == "signal"
                and mf.classify_message("EURUSD BUY 1.0850")[0] == "skip")

    ok &= check("media: video / voice / audio / sticker / document / poll are omitted, a service "
                "message (pin) too; a text post with a link preview is NOT media",
                mf.message_media_kind(_FakeMsg(video=object())) == "video"
                and mf.message_media_kind(_FakeMsg(voice=object())) == "voice"
                and mf.message_media_kind(_FakeMsg(audio=object(), document=object())) == "audio"
                and mf.message_media_kind(_FakeMsg(sticker=object())) == "sticker"
                and mf.message_media_kind(_FakeMsg(document=object())) == "document"
                and mf.message_media_kind(_FakeMsg(poll=object())) == "poll"
                and mf.message_media_kind(_FakeMsg(action=object())) is not None
                and mf.message_media_kind(_FakeMsg(web_preview=object(), message="BUY GOLD 2650")) is None)
    photo = _FakeMsg(photo=object(), message="XAUUSD BUY 2650 SL 2640")
    ok &= check("a photo is omitted unless accept_photo_captions is on (then its caption is read)",
                mf.message_media_kind(photo) == "photo"
                and mf.message_media_kind(photo, accept_photo_captions=True) is None
                and mf.message_media_kind(_FakeMsg(photo=object()), accept_photo_captions=True) is not None)

    ok &= check("relay bridge: trade messages relayed, greetings and videos dropped by default",
                bridge.relay_decision(_FakeMsg(), "XAUUSD BUY 2650 SL 2640", False, False, False) == ""
                and bridge.relay_decision(_FakeMsg(), "Good morning fam", False, False, False) != ""
                and bridge.relay_decision(_FakeMsg(video=object()), "BUY GOLD 2650 SL 2640", False, False,
                                          False) == "video")
    ok &= check("relay bridge: --relay-everything relays any text; --filter-signals needs a parseable signal",
                bridge.relay_decision(_FakeMsg(), "Good morning fam", True, False, False) == ""
                and bridge.relay_decision(_FakeMsg(), "CLOSE ALL", False, False, False) == ""
                and bridge.relay_decision(_FakeMsg(), "Move SL to entry", False, True, False) != "")

    try:
        mf.check_channel_limit(["a", "b", "c"])
        three_ok = True
    except ValueError:
        three_ok = False
    try:
        CopierConfig(allowed_chats=["a", "b", "c", "d"])
        four_refused = False
    except ValueError:
        four_refused = True
    ok &= check("up to 3 channels are accepted; a 4th is refused at config time",
                three_ok and four_refused)

    copier = tc.Copier(CopierConfig(), dry_run=True)
    ok &= check("Copier.on_message() omits a greeting before parsing (no evaluation, nothing logged)",
                copier.on_message("Good morning! Close your charts and relax", "chat", 0.0) is None)
    return ok

def run() -> int:
    print("Telegram copier + verifier self-test")
    results = [test_parser(), test_verifier(), test_engine(), test_signal_logging(),
               test_message_filter()]
    passed = all(results)
    print(f"\n{'ALL PASS' if passed else 'SOME FAILED'} ({sum(results)}/{len(results)} suites)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(run())
