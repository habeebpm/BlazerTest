@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem GoldTrader: gold AND Bitcoin in this one window (each line starts with GOLD or BTC) + every
rem companion switched on in settings.ini. Leave this window open; Ctrl+C or closing it stops both.
rem
rem GOLD (XAUUSD) - remove --live for a dry-run (no real orders).
rem Telegram signals 06:00-23:00 Oman, Mon-Fri (EA). Claude 08:00-16:45 + 18:15-20:00 New York. Options:
rem   --trade-hours any   --friday-cutoff off   --max-spread 40   --min-adx 25
rem   --journal-folder off  (trade journal CSVs only in logs\journal, not Google Drive)
rem   --xtr-gate require_alignment  (fewer trades, smaller swings, no edge in the backtest)   see docs\REFERENCE.md
set GT_ARGS=--live --shared-cap-magic 20260922
rem
rem BITCOIN (BTCUSD) - needs BTCTrader_EA on a BTCUSD chart (Algo Trading on). Remove --live for a
rem dry-run; broker symbol with a suffix? add e.g. --symbol BTCUSDm. Other options: --max-daily-loss 5
rem --risk-percent 2  --max-positions 3  (see docs\BTC.md). Gold only: set BTC_ARGS=off
set BTC_ARGS=--live
python goldtrader.py start-all %GT_ARGS% --btc %BTC_ARGS%
rem Opened by the autostart (autostart.bat): close the window when the program ends.
if /i "%~1"=="auto" exit /b
pause
