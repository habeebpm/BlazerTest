@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.12 ^(64-bit^) from python.org and tick "Add python.exe to PATH", then try again. & pause & exit /b 1)
rem BTC backtest on YOUR broker's BTCUSD prices from MT5 (last 12 months), free (mechanical legs,
rem no Claude calls). Results + price history go to Google Drive (BTC_backtest_*, BTC_prices_*)
rem so Claude can analyse and tune the BTC parameters. MT5 must be open. Nothing is traded.
rem Broker symbol with a suffix? add e.g. --symbol BTCUSDm
python goldtrader.py backtest --profile btc --from-mt5 --months 12 --mechanical --yes --to-drive %*
pause
