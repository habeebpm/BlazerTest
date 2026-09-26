@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Downloads the latest GoldTrader from GitHub and installs it: keeps keys.txt, the relay login,
rem logs and your edited start.bat / start_btc.bat / settings.ini, runs every self-test (puts the
rem old version back if one fails), refreshes the Google Drive copy, then recompiles the EAs in MT5.
rem Close start.bat and start_btc.bat first.
python goldtrader.py update && python goldtrader.py install-mt5
pause
