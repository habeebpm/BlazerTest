"""
Relay bridge self-test - no Telegram session, no MT5, no network.

Covers what relay/telegram_relay_bridge.py decides on its own: the signal
parser (--filter-signals), the trade-only message filter (the same rules as
UnifiedTrader_EA) and relay_decision() - which messages are forwarded.
"""
from __future__ import annotations

import sys

import message_filter as mf
import telegram_relay_bridge as bridge
from signal_parser import parse_signal


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if (detail and not ok) else ""))
    return ok


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
    print("\n=== 2. trade-only message filter + relay_decision ===")
    ok = True
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
        mf.check_channel_limit(["a", "b", "c", "d"])
        four_refused = False
    except ValueError:
        four_refused = True
    ok &= check("up to 3 source channels are accepted; a 4th is refused", three_ok and four_refused)
    return ok


def main() -> int:
    print("Relay bridge self-test")
    results = [test_parser(), test_message_filter()]
    print("\nALL PASS" if all(results) else "\nSOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
