@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem The trading program + every companion switched on in settings.ini. Leave this window open.
rem Remove --live for a dry-run (no real orders).
set GT_ARGS=--live --xtr-gate require_alignment --shared-cap-magic 20260922
python goldtrader.py start %GT_ARGS%
pause
