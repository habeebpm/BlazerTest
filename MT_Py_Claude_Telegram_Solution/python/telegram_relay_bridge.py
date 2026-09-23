#!/usr/bin/env python3
"""
Telegram relay bridge: reads a channel/group your own Telegram account is a
member of (no admin rights there needed) and forwards each message into a
private group you create and own yourself - where you CAN freely add a
disposable bot as admin, since it's your group.

Point TelegramSMC_Copier.mq5's InpChannelId1 / InpChannelId2 at the RELAY
GROUP's chat id (printed by --check below), not the original source
channel's. The MQL5 EA's bot never needs to see the restricted source at
all - only this relay group, which you fully control.

WHY THIS EXISTS: MQL5 cannot read Telegram itself; TelegramSMC_Copier.mq5
polls the Bot API, and every Bot API call needs a bot token, and Telegram
only delivers a channel's posts to a bot that's an admin THERE - not
something you can grant yourself on a channel you don't own or moderate.
This script logs in as your own account instead (MTProto, via Telethon,
the same mechanism telegram_copier.py uses) - a regular member sees
everything a channel posts, no admin needed - and hands it to the MQL5 EA
through a middle-man chat you *do* control.

WHY FORWARD RATHER THAN RETYPE: Telegram's native forward keeps the
message's exact text/caption intact in the copy that lands in the relay
group, so this script never re-parses or reconstructs the signal itself -
it only decides WHICH messages to relay: by default TRADE MESSAGES ONLY -
signals and short trading commands pass, while greetings, mood posts,
commentary, long messages, videos, audio, voice notes, stickers, documents
and service messages are dropped (message_filter.py, the same rules the
MQL5 EAs apply). --filter-signals is stricter (only messages that parse as
an actionable signal); --relay-everything turns filtering off.

At most 3 source channels (message_filter.MAX_CHANNELS) can be routed
through one bridge - matching the EAs' InpChannelId1..3. The actual parsing, verification and SMC gate all stay
in TelegramSMC_Copier.mq5, exactly as if it were reading the source
channel directly.

Setup:
    1. Create a NEW private Telegram group (any name) - just for this relay.
    2. Add your bot (the one InpBotToken already uses) to that group as
       ADMIN, zero permissions needed - same as the original channel setup,
       except now it's your own group so you can do this yourself.
    3. Get TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org
       (API development tools) - your personal API app credential, NOT a
       bot token; this is the same credential telegram_copier.py uses.
    4. Run with --check to log in (prompts once for your phone number and
       the login code) and resolve/print both chat ids:
           python telegram_relay_bridge.py --check
       With nothing configured yet, --check instead lists every chat this
       account can see, so you can find the source channel's identifier.
    5. Set TELEGRAM_SOURCE_CHANNELS (the real channel(s)) and
       TELEGRAM_RELAY_GROUP (the group from step 1-2) and run for real:
           python telegram_relay_bridge.py
    6. Put the id --check printed for the relay group into
       TelegramSMC_Copier.mq5's InpChannelId1.

Keep this running continuously (same machine as MT5, or anywhere with
network access) - if it stops, nothing new reaches the relay group and the
EA sees no signals, same as if the source channel went quiet.

Credentials come from the environment, never from this file:
    set TELEGRAM_API_ID=1234567
    set TELEGRAM_API_HASH=your-api-hash
    set TELEGRAM_SOURCE_CHANNELS=@some_signal_channel,-1001234567890
    set TELEGRAM_RELAY_GROUP=-1009876543210
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import message_filter
from signal_parser import parse_signal

log = logging.getLogger("telegram_relay_bridge")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _split_chats(raw: str | None) -> list:
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def bot_api_chat_id(entity) -> int:
    """Convert a Telethon entity's own MTProto id into the -100-prefixed
    (channels/supergroups) or bare-negative (basic groups) form the Bot API
    uses - the exact form InpChannelId1/InpChannelId2 expect, since
    TelegramSMC_Copier.mq5 only ever sees chats through the Bot API, never
    MTProto directly. A private one-to-one chat (User) keeps its plain
    positive id in both APIs.
    """
    type_name = entity.__class__.__name__
    if type_name == "Channel":
        return int(f"-100{entity.id}")
    if type_name == "Chat":
        return -entity.id
    return entity.id


async def resolve_and_log(client, label: str, chats: list) -> None:
    for chat in chats:
        try:
            entity = await client.get_entity(chat)
        except Exception as exc:
            log.error("%s: could not resolve %r (%s)", label, chat, exc)
            continue
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
        log.info("%s: %r -> chat id %s (this is what InpChannelId1/InpChannelId2 expect)",
                 label, title, bot_api_chat_id(entity))


def relay_decision(message, text: str, relay_everything: bool, filter_signals: bool,
                   accept_photo_captions: bool) -> str:
    """"" to relay `message`, else the reason it is dropped. Pure - tested
    in copier_selftest.py with fake message objects."""
    if relay_everything:
        return "" if text.strip() else "empty"
    media = message_filter.message_media_kind(message, accept_photo_captions)
    if media:
        return media
    kind, why = message_filter.classify_message(text)
    if kind == message_filter.SKIP:
        return why
    if filter_signals and parse_signal(text).action == "unknown":
        return "no actionable signal"
    return ""


async def amain(client, args, sources: list, dest: str | None, filter_signals: bool) -> int:
    from telethon import events

    await client.start()
    me = await client.get_me()
    log.info("Logged in to Telegram as %s (id=%s)",
             getattr(me, "username", None) or me.first_name, me.id)

    if args.check:
        if sources:
            await resolve_and_log(client, "SOURCE", sources)
        else:
            log.info("No source channel(s) configured yet (TELEGRAM_SOURCE_CHANNELS / --sources).")
        if dest:
            await resolve_and_log(client, "RELAY GROUP", [dest])
        else:
            log.info("No relay group configured yet (TELEGRAM_RELAY_GROUP / --dest).")
        if not sources or not dest:
            log.info("Every chat this account can currently see, for reference:")
            async for d in client.iter_dialogs():
                log.info("  %r -> chat id %s (%s)",
                         d.name, bot_api_chat_id(d.entity), type(d.entity).__name__)
        return 0

    if not sources:
        log.error("No source channel(s) configured - set TELEGRAM_SOURCE_CHANNELS or pass --sources.")
        return 1
    if not dest:
        log.error("No relay group configured - set TELEGRAM_RELAY_GROUP or pass --dest.")
        return 1

    dest_entity = await client.get_entity(dest)
    dest_title = getattr(dest_entity, "title", None) or dest
    log.info("Relaying %d source chat(s) -> %r (chat id %s)",
             len(sources), dest_title, bot_api_chat_id(dest_entity))
    log.info("Filtering: %s", "everything is relayed (--relay-everything)" if args.relay_everything
             else "only messages that parse as an actionable signal (--filter-signals)" if filter_signals
             else "trade messages only (default) - greetings, mood posts, long messages and media dropped")

    @client.on(events.NewMessage(chats=sources))
    async def handler(event):
        text = event.raw_text or ""
        why = relay_decision(event.message, text, args.relay_everything, filter_signals,
                             args.accept_photo_captions)
        if why:
            log.debug("Not relayed (%s): %s", why, text.strip().splitlines()[0][:60] if text.strip() else "")
            return
        try:
            await client.forward_messages(dest_entity, event.message)
            log.info("Relayed from chat %s: %s", event.chat_id, text.splitlines()[0][:80])
        except Exception:
            log.exception("Failed to relay a message from chat %s", event.chat_id)

    log.info("Bridge running - Ctrl+C to stop.")
    await client.run_until_disconnected()
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="log in, resolve the configured source(s)/relay group, exit "
                             "(lists every visible chat instead if nothing is configured yet)")
    parser.add_argument("--sources", help="comma-separated source channel(s): @username or numeric "
                                          "id (overrides TELEGRAM_SOURCE_CHANNELS)")
    parser.add_argument("--dest", help="relay group: @username or numeric id "
                                       "(overrides TELEGRAM_RELAY_GROUP)")
    parser.add_argument("--filter-signals", action="store_true", dest="filter_signals",
                        help="stricter: only relay messages that parse as an actionable signal "
                             "(default: trade messages only - signals and short trading commands)")
    parser.add_argument("--relay-everything", action="store_true", dest="relay_everything",
                        help="relay every text message, including greetings/mood/long posts "
                             "(default: trade messages only; media is always dropped unless this is set)")
    parser.add_argument("--accept-photo-captions", action="store_true", dest="accept_photo_captions",
                        help="also relay photos whose caption is a trade message")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    sources = _split_chats(args.sources or os.environ.get("TELEGRAM_SOURCE_CHANNELS"))
    dest = args.dest or os.environ.get("TELEGRAM_RELAY_GROUP")
    try:
        message_filter.check_channel_limit(sources, "source channels")
    except ValueError as exc:
        log.error("%s - the EAs read at most 3 channels (InpChannelId1..3).", exc)
        return 1

    try:
        from telethon import TelegramClient
    except ImportError:
        log.error("The 'telethon' package is required for this bridge: pip install telethon")
        return 1

    api_id = _env_int("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session = os.environ.get("TELEGRAM_RELAY_SESSION", "tg_relay_bridge")

    if not api_id or not api_hash:
        log.error("TELEGRAM_API_ID and TELEGRAM_API_HASH are required - get them from "
                  "https://my.telegram.org (API development tools). This is your personal API "
                  "app credential, not a bot token.")
        return 1


    client = TelegramClient(session, api_id, api_hash)
    try:
        return client.loop.run_until_complete(amain(client, args, sources, dest, args.filter_signals))
    except KeyboardInterrupt:
        log.info("Stopped.")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
