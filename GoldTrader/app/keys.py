#!/usr/bin/env python3
"""
keys.txt - the one place every key and id lives (GoldTrader folder).

Plain `NAME=value` lines, editable in Notepad. Read by the trading program,
the relay, the scorecard and the backtest when they start; written by the
first-run questions (check.bat / settings.bat). A value in keys.txt wins
over the same name set anywhere else; an empty value leaves it unset.

Created on install (setup.bat) and on the first start, pre-filled with any
key already saved on this PC. Never uploaded: .gitignore excludes it.

    python keys.py            create keys.txt if missing, print its path
"""
from __future__ import annotations

import os
import re

import paths

KEYS_FILE = paths.KEYS_FILE


def keys_path() -> str:
    """keys.txt - or GOLDTRADER_KEYS_FILE (the self-tests use a throwaway file,
    so a test can never read or write your real keys)."""
    return os.environ.get("GOLDTRADER_KEYS_FILE") or KEYS_FILE

# name, comment line(s) shown above it in the file
FIELDS = [
    ("ANTHROPIC_API_KEY", "Claude: console.anthropic.com -> API keys (starts with sk-ant-)"),
    ("TELEGRAM_ALERT_BOT_TOKEN", "Telegram bot token from @BotFather (also goes in the EA input InpBotToken)"),
    ("TELEGRAM_ALERT_CHAT_ID", "Your own chat id - getUpdates -> \"chat\":{\"id\":... (EA input InpControlChatId)"),
    ("TELEGRAM_API_ID", "Relay: my.telegram.org -> API development tools -> api_id"),
    ("TELEGRAM_API_HASH", "Relay: same page -> api_hash"),
    ("TELEGRAM_SOURCE_CHANNELS", "Relay: signal channel(s), @name or id, comma-separated, max 3"),
    ("TELEGRAM_RELAY_GROUP", "Relay: your private relay group id, e.g. -1001234567890 (EA input InpChannelId1)"),
    ("MT5_PASSWORD", "Optional: only if MT5 is not already logged in"),
]
NAMES = [n for n, _ in FIELDS]

HEADER = """\
# GoldTrader keys - one line per key: NAME=value (no quotes needed).
# Keep this file PRIVATE: do not share it, e-mail it or put the GoldTrader
# folder in Google Drive/Dropbox/OneDrive. It is never uploaded to git.
# Change a value here (or with settings.bat), then restart start.bat.
"""

_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def template(prefill: dict | None = None) -> str:
    prefill = prefill or {}
    parts = [HEADER]
    for name, comment in FIELDS:
        parts.append(f"\n# {comment}\n{name}={(prefill.get(name) or '').strip()}\n")
    return "".join(parts)


def read(path: str | None = None) -> dict:
    """{NAME: value} from the file (empty values included); {} if missing."""
    path = path or keys_path()
    out = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if line.lstrip().startswith("#"):
                    continue
                m = _LINE.match(line)
                if m:
                    value = m.group(2)
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    out[m.group(1)] = value.strip()
    except OSError:
        pass
    return out


def ensure_file(path: str | None = None, env=None) -> bool:
    """Creates keys.txt from the template if it is missing, pre-filled with
    any key already in `env` (e.g. saved on this PC before keys.txt existed).
    True if it was created now."""
    path = path or keys_path()
    if os.path.exists(path):
        return False
    env = os.environ if env is None else env
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(template({n: env.get(n, "") for n in NAMES}))
    return True


def load(path: str | None = None, env=None) -> list:
    """Puts every non-empty value of keys.txt into `env` (os.environ by
    default - child programs such as the relay inherit it). Creates the file
    first if missing. Returns the names loaded."""
    env = os.environ if env is None else env
    path = path or keys_path()
    try:
        ensure_file(path, env)
    except OSError:
        pass
    loaded = []
    for name, value in read(path).items():
        if value:
            env[name] = value
            loaded.append(name)
    return loaded


def save(name: str, value: str, path: str | None = None) -> bool:
    """Sets NAME=value in keys.txt, keeping every comment and other line as
    it is (appends the line if the name is not there yet). True on success."""
    path = path or keys_path()
    try:
        ensure_file(path, {})
        with open(path, encoding="utf-8-sig", newline="") as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        return False
    # keep the file's own line endings (Notepad writes CRLF)
    nl = "\r\n" if any(x.endswith("\r\n") for x in lines) else "\n"
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) == name:
            lines[i] = f"{name}={value}{nl}"
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += nl
        lines.append(f"{name}={value}{nl}")
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    created = ensure_file()
    print(("Created " if created else "Keys file: ") + keys_path())
