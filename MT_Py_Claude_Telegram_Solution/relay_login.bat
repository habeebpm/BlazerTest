@echo off
cd /d "%~dp0"
rem One-time Telegram login for the relay bridge (asks for phone number + code).
python solution.py relay-login
pause
