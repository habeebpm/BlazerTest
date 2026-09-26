@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Once: start.bat opens by itself at every Windows sign-in (MT5 too), and is reopened within
rem 5 minutes if its window closes unexpectedly. Ctrl+C in the window or stop.bat stops it for good
rem (until you double-click start.bat). Undo: python goldtrader.py autostart off
python goldtrader.py autostart on
pause
