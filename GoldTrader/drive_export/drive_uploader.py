"""
Pushes the CSVs/manifest exporter.py writes locally up to a Google Drive
folder, via a service account - for a headless MT5 box (a VPS with no
desktop, where Google Drive for Desktop isn't an option). If your MT5
machine DOES have a desktop, skip this file entirely and just point
Google Drive for Desktop at exporter.py's --out-dir instead - zero code,
zero credentials to manage (the EA's InpXtrExportCopyTo).

WHY A SERVICE ACCOUNT, NOT THE INTERACTIVE OAUTH FLOW: this runs
unattended in a poll loop - there's no human at a browser to click
"Allow" every time a token expires. A service account's key file
authenticates on its own, indefinitely, with no browser step, which is
what an unattended background process needs. See docs/REFERENCE.md
for the one-time Google Cloud setup (create the service account, enable
the Drive API, share the target folder with the service account's own
email address - it has no storage of its own, so without that share step
every upload fails with a quota error).

Every upload UPDATES the same Drive file (by a cached file id) rather
than creating a new copy each cycle - otherwise the folder would fill
with thousands of near-duplicate files and whatever link was shared with
XTR would silently stop being the freshest one. The cache is keyed by
(folder id, filename), not filename alone, so pointing --drive-folder-id
at a different folder later (e.g. moving from a test folder to the real
one) starts a fresh file there instead of continuing to silently update
the old folder's file by its now-stale cached id. It's also saved to disk
after EVERY file, not once at the end of a cycle - if the process dies
mid-cycle, only the files it never got to are re-created next run; a
file it already created keeps its id and gets updated in place instead of
duplicated.

UNCHANGED FILES ARE SKIPPED: the cache also remembers the sha256 of the
content last uploaded for each file, and a file whose content hasn't
changed since is not re-sent. With the M1 trigger the manifest (its
exported_at_utc is a heartbeat) goes up every minute, but XAUUSD_M5.csv
only when an M5 bar actually closed, M15 every 15 minutes, H1 hourly -
instead of re-uploading identical bytes 4 times a minute.

Lazy-imports the google-api-python-client/google-auth packages, same
convention as MetaTrader5 elsewhere in this repo, so exporter.py and
xtr_export.py --once (without --upload-drive) never need them installed.
"""
from __future__ import annotations

import hashlib
import json
import os

_MIMETYPES = {".csv": "text/csv", ".json": "application/json"}


def _cache_key(folder_id: str, filename: str) -> str:
    return f"{folder_id}::{filename}"


def _hash_key(folder_id: str, filename: str) -> str:
    return f"sha256::{folder_id}::{filename}"


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _mimetype(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _MIMETYPES.get(ext, "application/octet-stream")


def build_service(credentials_path: str):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive upload needs google-api-python-client and google-auth - "
            "pip install -r requirements.txt, or drop --upload-drive and use Google "
            "Drive for Desktop instead (the EA's InpXtrExportCopyTo)."
        ) from exc
    creds = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=["https://www.googleapis.com/auth/drive.file"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _load_cache(cache_path: str) -> dict:
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path) as f:
        return json.load(f)


def _save_cache(cache_path: str, cache: dict) -> None:
    """Writes via a temp file + atomic rename (os.replace), not a direct
    write to cache_path - a process killed mid-write can never leave a
    half-written/corrupt cache file behind (os.replace either lands the
    whole new file or doesn't happen at all), which would otherwise
    permanently break every future sync until a human fixed it by hand.
    """
    tmp_path = f"{cache_path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp_path, cache_path)


def _build_media(local_path: str):
    from googleapiclient.http import MediaFileUpload
    return MediaFileUpload(local_path, mimetype=_mimetype(local_path), resumable=False)


def upload_or_update(service, folder_id: str, local_path: str, cache: dict, cache_path: str,
                     media_factory=_build_media):
    """Creates the file on Drive the first time, updates its content (same
    file id, same shareable link) on every later call whose content
    differs from the last upload, and does nothing when it doesn't. Mutates
    `cache` and persists it to `cache_path` immediately after a successful
    create - not batched until the whole sync_paths() cycle finishes - so
    a crash right after this call still keeps the new file's id instead of
    creating a duplicate on the next run.

    `media_factory` defaults to the real googleapiclient MediaFileUpload
    (lazy-imported, same convention as MetaTrader5 elsewhere in this repo)
    but is a parameter so selftest.py can exercise the create-vs-update/
    cache logic above with a fake, without needing google-api-python-client
    installed just to run the offline test suite.
    """
    name = os.path.basename(local_path)
    key = _cache_key(folder_id, name)
    hkey = _hash_key(folder_id, name)
    digest = _file_sha256(local_path)
    file_id = cache.get(key)
    if file_id and cache.get(hkey) == digest:
        return file_id  # same bytes already on Drive - nothing to send
    media = media_factory(local_path)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        created = service.files().create(
            body={"name": name, "parents": [folder_id]}, media_body=media, fields="id").execute()
        cache[key] = created["id"]
    cache[hkey] = digest
    _save_cache(cache_path, cache)
    return cache[key]


def sync_paths(service, folder_id: str, paths: dict, cache_path: str, media_factory=_build_media) -> dict:
    """paths: {label: local_file_path} (exporter.export_once()'s own return
    shape works directly). Returns {label: drive_file_id}."""
    cache = _load_cache(cache_path)
    result = {}
    for label, local_path in paths.items():
        result[label] = upload_or_update(service, folder_id, local_path, cache, cache_path, media_factory)
    return result
