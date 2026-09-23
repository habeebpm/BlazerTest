"""
Every location in the GoldTrader package, in one place (nothing else in the
code guesses folders). Layout:

    GoldTrader/
        settings.ini      companion programs on/off
        logs/             every log, CSV, model and state file
        app/              this program (main.py and its modules)
        relay/            Telegram relay bridge
        drive_export/     Python price export to Google Drive (VPS)
        mt5/              EAs, include files, presets (installed into MT5)
"""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(APP_DIR)
LOG_DIR = os.path.join(PACKAGE_ROOT, "logs")
SETTINGS_INI = os.path.join(PACKAGE_ROOT, "settings.ini")
RELAY_DIR = os.path.join(PACKAGE_ROOT, "relay")
DRIVE_EXPORT_DIR = os.path.join(PACKAGE_ROOT, "drive_export")
MT5_DIR = os.path.join(PACKAGE_ROOT, "mt5")
SETUP_MARKER = os.path.join(LOG_DIR, ".setup_done")
