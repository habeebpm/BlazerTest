@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Installs every Python package, runs every self-test, then copies + compiles the EAs in MT5.
python goldtrader.py setup && python goldtrader.py install-mt5
pause
