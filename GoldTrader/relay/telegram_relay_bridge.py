#!/usr/bin/env python3
"""
Telegram relay bridge - for a signal channel you are NOT admin of.

UnifiedTrader_EA reads Telegram through a bot, and Telegram only shows a
channel's posts to a bot that is admin there. This script logs in as YOUR
Telegram account (a normal member sees everything) and forwards the trade
messages into a private group you own, where your bot IS admin. Point the
EA's InpChannelId1 at that relay group.

Only trade messages are forwarded (message_filter.py - the same rules as the
EA): greetings, mood posts, long commentary, videos, audio, stickers and
documents are dropped. --filter-signals: only messages that parse as a
signal; --relay-everything: no filtering. At most 3 source channels.

Normally you never run this file yourself: GoldTrader's start.bat runs it in
the background when settings.ini [relay_bridge] is on (the first-run
questions switch it on). One-time login: relay_login.bat.

    python telegram_relay_bridge.py --check   # log in, show chat ids, exit
    python telegram_relay_bridge.py           # relay (Ctrl+C to stop)

Settings come from the environment (saved by the first-run questions):
TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SOURCE_CHANNELS,
TELEGRAM_RELAY_GROUP.
"""
from __future__ import annotations

import argparse
import logging
import os

import message_filter
from signal_parser import parse_signal

log = logging.getLogger("telegram_relay_bridge")


# Exit codes the supervisor (app/relay_supervisor.py) acts
# on: restart after anything else, stop for good after these two.
EXIT_NOT_LOGGED_IN = 2   # --no-login and no saved session: run --check once
EXIT_CONFIG = 3          # missing credentials / channels / telethon


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
    The EA only ever sees chats through the Bot API, never
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
    in relay_selftest.py with fake message objects."""
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

    if args.no_login:
        # Background mode: never prompt for a phone number/code (nobody is
        # there to answer, and a blocked input() would hang forever).
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Relay bridge is not logged in to Telegram yet - run relay_login.bat once "
                      "(or: python telegram_relay_bridge.py --check)")
            return EXIT_NOT_LOGGED_IN
    else:
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
        return EXIT_CONFIG
    if not dest:
        log.error("No relay group configured - set TELEGRAM_RELAY_GROUP or pass --dest.")
        return EXIT_CONFIG

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
    parser.add_argument("--no-login", action="store_true", dest="no_login",
                        help="never prompt for a login (background use): exit with code 2 if no "
                             "saved session exists yet")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    sources = _split_chats(args.sources or os.environ.get("TELEGRAM_SOURCE_CHANNELS"))
    dest = args.dest or os.environ.get("TELEGRAM_RELAY_GROUP")
    try:
        message_filter.check_channel_limit(sources, "source channels")
    except ValueError as exc:
        log.error("%s - the EAs read at most 3 channels (InpChannelId1..3).", exc)
        return EXIT_CONFIG

    try:
        from telethon import TelegramClient
    except ImportError:
        log.error("The 'telethon' package is required for this bridge: pip install telethon")
        return EXIT_CONFIG

    api_id = _env_int("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session = os.environ.get("TELEGRAM_RELAY_SESSION", "tg_relay_bridge")

    if not api_id or not api_hash:
        log.error("TELEGRAM_API_ID and TELEGRAM_API_HASH are required - get them from "
                  "https://my.telegram.org (API development tools). This is your personal API "
                  "app credential, not a bot token.")
        return EXIT_CONFIG


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
