#!/usr/bin/env python3
"""
Sets the web dashboard's password (dashboard/Login.aspx).

    python goldtrader.py dashboard-password

Only a salted PBKDF2-SHA256 hash is stored, in dashboard/App_Data/
password.txt - IIS never serves App_Data, and git never uploads it. Run it
again at any time to change the password; phones signed in with the old one
stay signed in until they sign out (their cookie is still valid).
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import sys

import paths

ITERATIONS = 200_000
MIN_LENGTH = 10
PASSWORD_FILE = os.path.join(paths.PACKAGE_ROOT, "dashboard", "App_Data", "password.txt")


def hash_password(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """"pbkdf2-sha256$<iterations>$<salt b64>$<hash b64>" - the format
    Login.aspx verifies."""
    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 32)
    return "pbkdf2-sha256${}${}${}".format(iterations, base64.b64encode(salt).decode("ascii"),
                                           base64.b64encode(digest).decode("ascii"))


def verify(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.strip().split("$")
        if scheme != "pbkdf2-sha256":
            return False
        want = base64.b64decode(digest)
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt),
                                  int(iterations), len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def save(password: str, path: str | None = None) -> str:
    path = path or PASSWORD_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(hash_password(password) + "\n")
    return path


def main(ask=getpass.getpass, out=print) -> int:
    out("Web dashboard password (at least %d characters; it protects your account figures)." % MIN_LENGTH)
    first = ask("  New password: ")
    if len(first) < MIN_LENGTH:
        out("  Too short - nothing changed.")
        return 1
    if ask("  Same password again: ") != first:
        out("  The two entries differ - nothing changed.")
        return 1
    out("  Saved: " + save(first))
    return 0


if __name__ == "__main__":
    sys.exit(main())
