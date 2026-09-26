@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem Bitcoin (BTCUSD): Claude-decided entries with BTC's own rules (app\profiles.py) - leave this window open.
rem Needs: MT5 open, BTCTrader_EA on a BTCUSD chart (Algo Trading on). Runs next to start.bat (gold).
rem Remove --live for a dry-run. Broker symbol with a suffix? add e.g. --symbol BTCUSDm
rem Other options: --max-daily-loss 5  --risk-percent 2  --max-positions 3   (see docs\BTC.md)
set GT_ARGS=--profile btc --live
python goldtrader.py start %GT_ARGS%
pause
