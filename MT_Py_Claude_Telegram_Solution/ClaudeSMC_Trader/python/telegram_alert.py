"""
Sends a one-way Telegram notification when Claude issues a "full" conviction
verdict - see main.py's run_once(). This is send-only, not a signal source:
it never reads Telegram, never places an order, and is completely
independent of the Telegram signal-copying stack elsewhere in this repo
(../../python/telegram_copier.py, ../../MQL5/Experts/TelegramSMC_Copier.mq5,
UnifiedTrader_EA.mq5's own Telegram side) - a bad bot token or a Telegram
outage here can never affect trading, and vice versa.

Fires on EVERY "full" conviction verdict, whether or not the trade actually
executes - executor.gate() can still reject it (position cap, daily trade
limit, confluence floor), and the alert message says so either way. Off by
default: config.py's telegram_alert_bot_token/telegram_alert_chat_id are
both "" until you set them (see README.md), and send_alert() is a safe
no-op with either blank.

Uses only the standard library (urllib) - no new dependency beyond what's
already in requirements.txt.

send_alert() takes the HTTP-posting function as a parameter (dependency
injection, same philosophy as executor.py's `gateway` and claude_advisor.py's
`client` parameters) so selftest.py exercises it with a fake poster that
records the call instead of ever touching the network.
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> None:
    """The real HTTP POST - swapped out in selftest.py for a fake poster."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def format_full_conviction_message(symbol: str, verdict, executed: bool,
                                    reject_reason: str = "") -> str:
    """verdict is a claude_advisor.ConfluenceVerdict. `executed`/
    `reject_reason` come straight from the executor.Decision this verdict
    produced - see main.py's run_once()."""
    status = "EXECUTED" if executed else ("NOT executed - " + reject_reason if reject_reason
                                          else "NOT executed")
    return (
        f"Claude full conviction: {verdict.direction.upper()} {symbol} "
        f"(confluence {verdict.confluence_count}/3)\n"
        f"Status: {status}\n"
        f"Reasoning: {verdict.reasoning}"
    )


def send_alert(bot_token: str, chat_id: str, text: str, poster=_post_json) -> bool:
    """Never raises - a Telegram outage (bad token, network down, rate
    limited) must never interrupt the trading loop this is a side-effect of.
    Returns True on an apparent success, False otherwise (and logs why,
    except for the blank-credentials no-op case, which is the normal "alerts
    just aren't configured" state, not a failure worth logging every cycle).
    """
    if not bot_token or not chat_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    try:
        poster(url, {"chat_id": chat_id, "text": text})
        return True
    except Exception as exc:
        log.warning("Telegram full-conviction alert failed (bad bot token/chat id, network "
                    "down, or rate limited) - trading continues unaffected: %s", exc)
        return False
