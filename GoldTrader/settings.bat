@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Enter / change every setting in one go (API key, Telegram ids, relay) - saved in keys.txt.
python goldtrader.py settings
pause
