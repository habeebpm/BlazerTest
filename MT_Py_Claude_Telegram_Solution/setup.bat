@echo off
cd /d "%~dp0"
rem Installs every Python package, runs every self-test, then installs + compiles the EAs in MT5.
python solution.py setup && python solution.py install-mt5
pause
