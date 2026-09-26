@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Downloads the latest GoldTrader from GitHub and installs it: keeps keys.txt, the relay login,
rem logs and your edited start.bat / settings.ini, runs every self-test (puts the
rem old version back if one fails), refreshes the Google Drive copy, then recompiles the EAs in MT5.
rem Close the start.bat window first.
python goldtrader.py update && python goldtrader.py install-mt5
rem The autostart (if on) reopens start.bat within 5 minutes after this.
python goldtrader.py autostart resume
pause
