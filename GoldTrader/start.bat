@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem The trading program + every companion switched on in settings.ini. Leave this window open.
rem Remove --live for a dry-run (no real orders).
rem Claude trades 08:00-16:45 and 18:15-20:00 New York time by default. Options you can add:
rem   --trade-hours any   --friday-cutoff off   --max-spread 40   --min-adx 25
rem   --xtr-gate require_alignment  (fewer trades, smaller swings, no edge in the backtest)   see docs\REFERENCE.md
set GT_ARGS=--live --shared-cap-magic 20260922
python goldtrader.py start %GT_ARGS%
pause
