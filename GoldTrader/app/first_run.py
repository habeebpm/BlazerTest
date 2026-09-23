"""
First-run setup wizard for main.py: asks for every setting in one go and
saves each one permanently (Windows user environment - exactly what setx
does, so it survives a restart), then main.py carries on in the same run.

    - runs by itself on the first start of main.py (and again whenever
      ANTHROPIC_API_KEY is missing), only when someone is at the keyboard -
      a scheduled/background start never waits for input, it just logs;
    - `python main.py --setup` (settings.bat) runs it again at any time;
    - Enter keeps the current value (secrets shown masked), "-" skips;
    - a value that does not look right (key prefix, numeric id, ...) is
      questioned once - never silently accepted, never refused outright;
    - relay bridge settings only if you say you use the bridge, which also
      switches it on in settings.ini;
    - optional test message over Telegram at the end.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess

import paths

MARKER = paths.SETUP_MARKER

# name, secret, prompt, group, pattern a sensible value matches
SETTINGS = [
    ("ANTHROPIC_API_KEY", True, "Anthropic API key (console.anthropic.com -> API keys)", "core",
     r"^sk-ant-\S{10,}$"),
    ("TELEGRAM_ALERT_BOT_TOKEN", True, "Telegram bot token (from @BotFather)", "core",
     r"^\d{5,}:[\w-]{20,}$"),
    ("TELEGRAM_ALERT_CHAT_ID", False, "Your Telegram chat id (getUpdates -> \"chat\":{\"id\":...)", "core",
     r"^-?\d{3,}$"),
    ("TELEGRAM_API_ID", False, "Relay: api_id (my.telegram.org -> API development tools)", "relay",
     r"^\d{3,}$"),
    ("TELEGRAM_API_HASH", True, "Relay: api_hash (same page)", "relay", r"^[0-9a-fA-F]{32}$"),
    ("TELEGRAM_SOURCE_CHANNELS", False, "Relay: signal channel(s), @name or id, comma-separated (max 3)",
     "relay", r"^[^,\s]+(,[^,\s]+){0,2}$"),
    ("TELEGRAM_RELAY_GROUP", False, "Relay: your relay group id (e.g. -1001234567890)", "relay",
     r"^(-?\d{5,}|@\w{4,})$"),
]
REQUIRED = ("ANTHROPIC_API_KEY",)


def mask(value: str, secret: bool) -> str:
    if not value:
        return "not set"
    if secret:
        return "set, ends ..." + value[-4:] if len(value) > 4 else "set"
    return value


def save_permanent(name: str, value: str) -> bool:
    """This process (os.environ) + permanently for the Windows user (setx)."""
    os.environ[name] = value
    if os.name != "nt":
        print(f"    (not Windows - add  export {name}=...  to your shell profile to keep it)")
        return False
    return subprocess.call(["setx", name, value], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def set_preset_enabled(path: str, section: str, enabled: bool) -> bool:
    """Flips `enabled = ...` inside [section] of settings.ini, keeping
    every comment and other line exactly as it is."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        return False
    current, changed = None, False
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            current = m.group(1).strip()
            continue
        if current == section and re.match(r"\s*enabled\s*=", line):
            nl = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"enabled = {'true' if enabled else 'false'}{nl}"
            changed = True
            break
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return changed


def needed(env=None) -> list:
    env = os.environ if env is None else env
    return [n for n in REQUIRED if not (env.get(n) or "").strip()]


def should_run(env=None, marker: str = MARKER, interactive: bool | None = None) -> bool:
    import sys
    interactive = sys.stdin.isatty() if interactive is None else interactive
    return interactive and (not os.path.exists(marker) or bool(needed(env)))


def _ask_one(name, secret, prompt, pattern, env, ask, ask_secret, out):
    current = (env.get(name) or "").strip()
    while True:
        value = (ask_secret if secret else ask)(f"  {prompt}\n  {name} [{mask(current, secret)}]: ").strip()
        if not value or value == "-":
            return None
        if pattern and not re.match(pattern, value):
            yes = ask("    That doesn't look like the usual format - use it anyway? [y/N]: ").strip().lower()
            if yes not in ("y", "yes"):
                continue
        return value


def run_wizard(preset_path: str, env=None, ask=input, ask_secret=getpass.getpass, out=print,
               saver=save_permanent, send_test=None, marker: str = MARKER) -> int:
    """Returns 0; `send_test(token, chat_id) -> bool` sends the test message."""
    env = os.environ if env is None else env
    out("\n=== Settings (asked once - saved permanently, survive a PC restart) ===")
    out("Enter = keep the current value, '-' = skip.\n")
    saved = []

    def take(group):
        for name, secret, prompt, grp, pattern in SETTINGS:
            if grp != group:
                continue
            value = _ask_one(name, secret, prompt, pattern, env, ask, ask_secret, out)
            if value is not None:
                saver(name, value)
                env[name] = value
                saved.append(name)

    take("core")
    relay_on = False
    try:
        import services
        relay_on = services.load_preset(preset_path).relay_bridge.enabled
    except Exception:
        pass
    reply = ask(f"\n  Do you use the relay bridge (a signal channel you are NOT admin of)? "
                f"[{'Y/n' if relay_on else 'y/N'}]: ").strip().lower()
    use_relay = relay_on if not reply else reply in ("y", "yes")
    if use_relay:
        take("relay")
        if not relay_on and set_preset_enabled(preset_path, "relay_bridge", True):
            out("  Relay bridge switched on in settings.ini.")
        out("  One-time Telegram login for it: double-click relay_login.bat.")

    token, chat = (env.get("TELEGRAM_ALERT_BOT_TOKEN") or "").strip(), (env.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if token and chat and send_test is not None:
        if ask("\n  Send a test message to your Telegram now? [Y/n]: ").strip().lower() in ("", "y", "yes"):
            out("  Test message sent - check Telegram." if send_test(token, chat)
                else "  Test message FAILED - check the bot token and chat id (and that you messaged the bot once).")

    missing = needed(env)
    out(f"\n{len(saved)} setting(s) saved." + (f" Still missing: {', '.join(missing)} - main.py cannot "
                                               "call Claude without it." if missing else ""))
    try:
        os.makedirs(os.path.dirname(marker) or ".", exist_ok=True)
        with open(marker, "w") as f:
            f.write("setup wizard completed\n")
    except OSError:
        pass
    return 0
