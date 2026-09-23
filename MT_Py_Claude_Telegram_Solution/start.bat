@echo off
cd /d "%~dp0"
rem The Claude program + every companion switched on in ClaudeSMC_Trader\python\main_preset.ini.
rem Edit the options below if needed (remove --live for dry-run).
set MAIN_ARGS=--live --xtr-gate require_alignment --shared-cap-magic 20260922
python solution.py start %MAIN_ARGS%
pause
