@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Stops GoldTrader on purpose: the autostart will not reopen it until you double-click start.bat.
rem Then close the GoldTrader window (or press Ctrl+C in it). Open trades stay managed by the EAs.
python goldtrader.py autostart pause
pause
