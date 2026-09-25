@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Replays your signal provider's past Telegram messages (last 3 months) through the EA's rules
rem on real MT5 prices: fixed $6 stop vs the signal's stop. MT5 must be open. Nothing is traded.
rem Other periods: python goldtrader.py replay-signals --months 6   (--help lists every option)
python goldtrader.py replay-signals %*
pause
