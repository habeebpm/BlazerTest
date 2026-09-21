#!/usr/bin/env python3
"""
Telegram signal copier + verifier for MetaTrader 5.

Listens to configured Telegram chats/channels, parses each message as a
trade signal (signal_parser.py), runs it through SignalVerifier before it
touches the broker (verifier.py - stale/duplicate/off-symbol/bad-SL/weak-R:R
signals are rejected with a logged reason), and copies whatever survives to
MT5 with the signal's own SL/TP rather than a fixed distance. After sending
an order it re-checks the fill against what was verified (slippage, and that
the broker actually applied the requested SL) and logs a warning on a
mismatch - the "verifier" half of the job doesn't stop at the parse.

It also understands a few management messages that aren't new entries:
CLOSE / EXIT closes every open position on the configured symbol, and a
"move SL to breakeven" message does exactly that (skipped per-position if a
trade isn't yet far enough in profit to clear the broker's minimum stop
distance). CANCEL is logged and otherwise ignored - this copier only manages
positions, not resting pending orders.

    python telegram_copier.py --selftest              # parser/verifier tests, no MT5/Telegram
    python telegram_copier.py --replay signals.txt     # dry-run parse+verify a batch of messages
    python telegram_copier.py --check                  # connect to MT5, print spec, exit
    python telegram_copier.py                           # listen live, DRY-RUN (no orders sent)
    python telegram_copier.py --live                    # listen live and actually copy trades

Requires Windows + MetaTrader 5 terminal for anything that touches the
market (see mt5_client.py), and the `telethon` package plus a Telegram API
id/hash for anything that touches Telegram - both are imported lazily, so
--selftest and --replay need neither.

Credentials come from the environment, never from this file:
    set MT5_LOGIN=12345678
    set MT5_PASSWORD=...
    set MT5_SERVER=YourBroker-Demo
    set TELEGRAM_API_ID=...
    set TELEGRAM_API_HASH=...
    set TELEGRAM_CHANNELS=@some_signal_channel,-1001234567890
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import copier_engine as ce
import mt5_client as mc
from copier_config import CopierConfig
from signal_parser import parse_signal
from verifier import SignalVerifier

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
SIGNAL_LOG = LOG_DIR / "copier_signals.csv"

log = logging.getLogger("telegram_copier")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "telegram_copier.log", encoding="utf-8"),
        ],
    )


SIGNAL_LOG_FIELDS = [
    "time", "chat_id", "direction", "entry_reference", "sl", "tp", "lots",
    "fill_price", "mode", "retcode", "ticket", "source",
]


_warned_stale_signal_log_header = False


def record_signal(row: dict) -> None:
    """Append a row to logs/copier_signals.csv under a fixed column set.

    Fieldnames are pinned rather than derived from `row` so a per-call change
    to the row dict's own key set can't misalign columns. That still leaves
    one case this can't fix retroactively: if SIGNAL_LOG_FIELDS itself gains
    a column across a version upgrade (as "source" just did), an already-
    existing log file keeps its OLD header forever - this function never
    rewrites it - while new rows are written under the NEW, longer field
    list. That's exactly why "source" was appended at the END of
    SIGNAL_LOG_FIELDS rather than inserted in the middle: every
    already-existing column stays at its original position for any reader
    keyed by column name (csv.DictReader and friends), and only the new
    trailing column is unreadable-by-name until the file is rotated - a
    missing feature, not silently corrupted data.
    """
    global _warned_stale_signal_log_header
    new_file = not SIGNAL_LOG.exists()
    with SIGNAL_LOG.open("a", newline="", encoding="utf-8") as fh:
        if not new_file and not _warned_stale_signal_log_header:
            with SIGNAL_LOG.open("r", encoding="utf-8") as existing:
                first_line = existing.readline().rstrip("\r\n")
            expected = ",".join(SIGNAL_LOG_FIELDS)
            if first_line and first_line != expected:
                log.warning(
                    "%s has an older header than this build writes - new columns (e.g. "
                    "\"source\") won't be readable by name for this file until you rename/"
                    "delete it and let a fresh one be created. Existing data is unaffected.",
                    SIGNAL_LOG,
                )
            _warned_stale_signal_log_header = True

        writer = csv.DictWriter(fh, fieldnames=SIGNAL_LOG_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


class Copier:
    """Live MT5-connected wrapper around the pure evaluate_signal engine."""

    def __init__(self, cfg: CopierConfig, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.verifier = SignalVerifier(cfg)
        self.spec: mc.SymbolSpec | None = None
        self.trades_today = 0
        self.day = None

    def start(self) -> None:
        mc.connect(self.cfg)
        self.spec = mc.get_symbol_spec(self.cfg)
        self.day = datetime.now(timezone.utc).date()
        log.info("Copier ready: symbol=%s allowed_chats=%s mode=%s", self.cfg.symbol,
                 self.cfg.allowed_chats or "ALL", "DRY-RUN" if self.dry_run else "LIVE")

    def roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day = today
            self.trades_today = 0
            log.info("New day - trade counter reset")

    # ---------------- management messages ----------------
    def handle_close(self, chat_id) -> None:
        if self.cfg.allowed_chats and str(chat_id) not in self.cfg.allowed_chats:
            log.info("Ignoring CLOSE from unauthorized chat %s", chat_id)
            return
        positions = mc.get_positions(self.cfg)
        if not positions:
            log.info("CLOSE received but no open %s positions to close", self.cfg.symbol)
            return
        for pos in positions:
            log.info("CLOSE signal: closing ticket %s", pos.ticket)
            mc.close_position(self.cfg, self.spec, pos, self.dry_run)

    def handle_breakeven(self, chat_id) -> None:
        if self.cfg.allowed_chats and str(chat_id) not in self.cfg.allowed_chats:
            log.info("Ignoring breakeven request from unauthorized chat %s", chat_id)
            return
        positions = mc.get_positions(self.cfg)
        if not positions:
            log.info("Breakeven signal received but no open %s positions", self.cfg.symbol)
            return
        tick = mc.get_tick(self.cfg.symbol)
        m = mc.mt5()
        min_stop = self.spec.stops_level_points * self.spec.point
        for pos in positions:
            is_buy = pos.type == m.POSITION_TYPE_BUY
            current = tick.bid if is_buy else tick.ask
            distance = (current - pos.price_open) if is_buy else (pos.price_open - current)
            if distance < min_stop:
                log.info("Breakeven skipped for ticket %s: only %.2f in profit "
                         "(need >= %.2f to clear the broker's minimum stop distance)",
                         pos.ticket, distance, min_stop)
                continue
            mc.modify_stop(self.cfg, pos, pos.price_open, self.spec.digits, self.dry_run)

    # ---------------- post-trade verification ----------------
    def verify_execution(self, ev: ce.Evaluation, result) -> None:
        if result is None:
            return
        tolerance = self.cfg.post_trade_tolerance_units * self.cfg.unit_size(self.spec.point)
        fill_price = getattr(result, "price", None)
        if fill_price:
            slip = abs(fill_price - ev.verdict.entry_reference)
            if slip > tolerance:
                log.warning("VERIFY: fill %.2f slipped %.2f from the verified reference "
                            "price %.2f (tolerance %.2f %ss)", fill_price, slip,
                            ev.verdict.entry_reference, tolerance, self.cfg.distance_unit)
        ticket = getattr(result, "order", None)
        if not ticket:
            return
        for pos in mc.get_positions(self.cfg):
            if pos.ticket == ticket:
                if ev.verdict.sl and abs(pos.sl - ev.verdict.sl) > self.spec.point * 2:
                    log.warning("VERIFY: broker SL %.*f does not match the requested "
                                "%.*f on ticket %s", self.spec.digits, pos.sl,
                                self.spec.digits, ev.verdict.sl, ticket)
                return
        log.warning("VERIFY: could not find ticket %s among open positions to confirm SL/TP",
                     ticket)

    # ---------------- main entry point ----------------
    def on_message(self, text: str, chat_id, message_time: float) -> ce.Evaluation | None:
        self.roll_day()
        quick = parse_signal(text, symbol_aliases=self.cfg.symbol_aliases)

        if quick.action == "close":
            self.handle_close(chat_id)
            return None
        if quick.action == "modify_sl":
            self.handle_breakeven(chat_id)
            return None
        if quick.action == "cancel":
            log.info("CANCEL signal received from %s (pending-order tracking is out of "
                      "scope for this copier - ignoring)", chat_id)
            return None
        if quick.action != "open":
            log.debug("Message from %s did not parse as an actionable signal: %s",
                      chat_id, quick.errors)
            return None

        tick = mc.get_tick(self.cfg.symbol)
        mid_price = (tick.ask + tick.bid) / 2.0
        positions = mc.get_positions(self.cfg)
        equity = mc.account_equity()

        ev = ce.evaluate_signal(
            self.cfg, self.verifier, text, chat_id, message_time, self.spec,
            current_price=mid_price, open_positions=len(positions),
            trades_today=self.trades_today, equity=equity, parsed=quick,
        )
        self._log_and_record(ev, chat_id)
        if not ev.verdict.accepted:
            return ev

        result = mc.open_signal_position(
            self.cfg, self.spec, ev.verdict.direction, ev.lots,
            ev.verdict.sl, ev.verdict.tps[0] if ev.verdict.tps else 0.0, self.dry_run,
        )
        # Dry-run (result is None) and a broker rejection (bad retcode) must
        # not count against max_trades_per_day - neither one actually opened
        # a position, and counting them would let a string of rejections
        # lock out real signals for the rest of the day.
        if result is not None and result.retcode == mc.mt5().TRADE_RETCODE_DONE:
            self.trades_today += 1
        self.verify_execution(ev, result)
        record_signal({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "Telegram_Sig",
            "chat_id": chat_id,
            "direction": ev.verdict.direction,
            "entry_reference": ev.verdict.entry_reference,
            "sl": ev.verdict.sl,
            "tp": ev.verdict.tps[0] if ev.verdict.tps else "",
            "lots": ev.lots,
            "fill_price": getattr(result, "price", ""),
            "mode": "dry-run" if self.dry_run else "live",
            "retcode": getattr(result, "retcode", ""),
            "ticket": getattr(result, "order", ""),
        })
        return ev

    def _log_and_record(self, ev: ce.Evaluation, chat_id) -> None:
        if ev.verdict.accepted:
            log.info("ACCEPTED %s from %s: entry~%.2f sl=%.2f tp=%s lots=%.2f rr=%s",
                      ev.verdict.direction.upper(), chat_id, ev.verdict.entry_reference,
                      ev.verdict.sl, ev.verdict.tps[0] if ev.verdict.tps else "n/a", ev.lots,
                      f"{ev.verdict.risk_reward:.2f}" if ev.verdict.risk_reward else "n/a")
            for note in ev.verdict.reasons:
                log.info("    note: %s", note)
        else:
            log.info("REJECTED signal from %s: %s", chat_id, "; ".join(ev.verdict.reasons))


# --------------------------------------------------------------------------- #
# replay mode - no MT5, no Telegram
# --------------------------------------------------------------------------- #
def _guess_price(msg: str, aliases: dict) -> float | None:
    return parse_signal(msg, symbol_aliases=aliases).entry_mid


def run_replay(cfg: CopierConfig, args) -> int:
    text = Path(args.replay).read_text(encoding="utf-8")
    messages = [m.strip() for m in re.split(r"\n-{3,}\n", text) if m.strip()]
    if not messages:
        log.error("No messages found in %s (separate messages with a line of ---)", args.replay)
        return 1

    spec = mc.SymbolSpec(
        name=cfg.symbol, point=args.point, digits=args.digits,
        stops_level_points=args.stops_level_points, spread_points=args.spread_points,
        volume_min=0.01, volume_max=cfg.max_lot_size, volume_step=0.01,
        tick_value=1.0, tick_size=args.point, filling_mode=0,
    )
    verifier = SignalVerifier(cfg)
    open_positions, trades_today = 0, 0
    now = time.time()

    for i, msg in enumerate(messages, 1):
        price = args.price if args.price is not None else (
            _guess_price(msg, cfg.symbol_aliases) or 2350.0)
        ev = ce.evaluate_signal(
            cfg, verifier, msg, chat_id="replay", message_time=now, spec=spec,
            current_price=price, open_positions=open_positions, trades_today=trades_today,
            equity=args.equity, now=now,
        )
        first_line = msg.splitlines()[0][:80]
        print(f"\n--- message {i}: {first_line} ---")
        print(f"  parsed:  action={ev.signal.action} direction={ev.signal.direction} "
              f"symbol={ev.signal.symbol} entry={ev.signal.entry} sl={ev.signal.sl} "
              f"tps={ev.signal.tps}")
        print(f"  verdict: {'ACCEPTED' if ev.verdict.accepted else 'REJECTED'}")
        for reason in ev.verdict.reasons:
            print(f"    - {reason}")
        if ev.verdict.accepted:
            print(f"  would send: {ev.request}")
            open_positions += 1
            trades_today += 1
    return 0


# --------------------------------------------------------------------------- #
# live mode - MT5 + Telethon
# --------------------------------------------------------------------------- #
def run_check(cfg: CopierConfig) -> int:
    mc.connect(cfg)
    spec = mc.get_symbol_spec(cfg)
    print(f"Symbol: {spec.name}  point={spec.point}  digits={spec.digits}  "
          f"spread={spec.spread_points} pts  stops_level={spec.stops_level_points} pts")
    print(f"Sizing: {'risk ' + str(cfg.risk_percent) + '%/trade' if cfg.use_risk_percent else str(cfg.lots) + ' fixed lots'}")
    print(f"Verifier: SL {cfg.min_sl_units:g}-{cfg.max_sl_units:g} {cfg.distance_unit}s, "
          f"max age {cfg.max_signal_age_seconds:g}s, max deviation "
          f"{cfg.max_price_deviation_units:g} {cfg.distance_unit}s")
    print(f"Allowed chats: {cfg.allowed_chats or 'ALL (no allow-list configured)'}")
    mc.disconnect()
    return 0


def run_live(cfg: CopierConfig, args) -> int:
    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        log.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set - see python/README.md.")
        return 1
    try:
        from telethon import TelegramClient, events
    except ImportError:
        log.error("The 'telethon' package is required for live listening: pip install telethon")
        return 1

    copier = Copier(cfg, dry_run=not args.live)
    copier.start()
    chats = cfg.allowed_chats or None

    client = TelegramClient(cfg.telegram_session, cfg.telegram_api_id, cfg.telegram_api_hash)

    @client.on(events.NewMessage(chats=chats))
    async def handler(event):
        text = event.raw_text or ""
        if not text.strip():
            return
        try:
            copier.on_message(text, event.chat_id, event.message.date.timestamp())
        except Exception:
            log.exception("Failed to process message from chat %s", event.chat_id)

    async def runner():
        await client.start()
        me = await client.get_me()
        log.info("Connected to Telegram as %s. Watching %s. Ctrl+C to stop.",
                  getattr(me, "username", None) or me.id, chats or "ALL chats (no allow-list set)")
        await client.run_until_disconnected()

    try:
        client.loop.run_until_complete(runner())
    finally:
        mc.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="actually copy trades (default is dry-run)")
    parser.add_argument("--check", action="store_true",
                        help="connect to MT5, print symbol spec and verifier settings, exit")
    parser.add_argument("--selftest", action="store_true",
                        help="run the parser/verifier/engine self-test (no MT5/Telegram needed)")
    parser.add_argument("--replay", metavar="FILE",
                        help="parse+verify sample messages from a text file (one message per "
                             "block, separated by a line of ---); no MT5/Telegram needed")
    parser.add_argument("--price", type=float,
                        help="synthetic current price for --replay (default: guess from each "
                             "message's own entry, else 2350.0)")
    parser.add_argument("--equity", type=float, default=10000.0,
                        help="synthetic equity for --replay risk-percent sizing")
    parser.add_argument("--spread-points", type=float, default=25.0, dest="spread_points")
    parser.add_argument("--stops-level-points", type=float, default=0.0, dest="stops_level_points")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--digits", type=int, default=2)
    parser.add_argument("--symbol", help="override the traded/verified symbol")
    parser.add_argument("--channels", help="comma-separated allow-list of chat ids/@usernames "
                                            "(overrides TELEGRAM_CHANNELS)")
    parser.add_argument("--lots", type=float, help="override the fixed lot size")
    parser.add_argument("--risk-percent", type=float, dest="risk_percent",
                        help="size from equity instead of a fixed lot")
    parser.add_argument("--min-risk-reward", type=float, dest="min_risk_reward")
    parser.add_argument("--min-sl-units", type=float, dest="min_sl_units")
    parser.add_argument("--max-sl-units", type=float, dest="max_sl_units")
    parser.add_argument("--max-signal-age", type=float, dest="max_signal_age")
    parser.add_argument("--allow-missing-sl", action="store_true", dest="allow_missing_sl")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        import copier_selftest
        return copier_selftest.run()

    overrides = {}
    if args.symbol:
        overrides["symbol"] = args.symbol.upper()
    if args.channels:
        overrides["allowed_chats"] = [c.strip() for c in args.channels.split(",") if c.strip()]
    if args.lots is not None:
        overrides["lots"] = args.lots
    if args.risk_percent is not None:
        overrides["risk_percent"] = args.risk_percent
        overrides["use_risk_percent"] = args.risk_percent > 0
    if args.min_risk_reward is not None:
        overrides["min_risk_reward"] = args.min_risk_reward
    if args.min_sl_units is not None:
        overrides["min_sl_units"] = args.min_sl_units
    if args.max_sl_units is not None:
        overrides["max_sl_units"] = args.max_sl_units
    if args.max_signal_age is not None:
        overrides["max_signal_age_seconds"] = args.max_signal_age
    if args.allow_missing_sl:
        overrides["allow_missing_sl_fallback"] = True
    cfg = CopierConfig.from_env(**overrides)

    if args.replay:
        return run_replay(cfg, args)

    try:
        if args.check:
            return run_check(cfg)
        return run_live(cfg, args)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
