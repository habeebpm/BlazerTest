@echo off
setlocal
cd /d "%~dp0"
rem Web dashboard: the IIS site "GoldTrader" on port 8080 showing your reports
rem (logs\status.json, written by start.bat). Read-only - it cannot trade.
rem Right-click -> Run as administrator. Safe to run again.
set SITE=GoldTrader
set PORT=8080
set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
net session >nul 2>&1 || (echo Right-click dashboard_setup.bat and choose "Run as administrator". & pause & exit /b 1)
python --version >nul 2>&1 || (echo Python was not found. Run setup.bat first. & pause & exit /b 1)

echo.
echo [1/5] Turning on IIS with ASP.NET 4.8 - can take a few minutes the first time...
dism /online /norestart /quiet /enable-feature /all /featurename:IIS-WebServerRole /featurename:IIS-WebServer /featurename:IIS-DefaultDocument /featurename:IIS-StaticContent /featurename:IIS-HttpErrors /featurename:IIS-RequestFiltering /featurename:IIS-ASPNET45 /featurename:IIS-ManagementConsole
set RC=%errorlevel%
if "%RC%"=="3010" echo       Done - Windows asks for a restart; do it after this script.
if not "%RC%"=="0" if not "%RC%"=="3010" (echo IIS could not be turned on ^(code %RC%^) - see the message above. & pause & exit /b 1)
set APPCMD=%windir%\system32\inetsrv\appcmd.exe
if not exist "%APPCMD%" (echo IIS is not ready yet - restart Windows, then run this again. & pause & exit /b 1)

echo [2/5] Web site "%SITE%" on port %PORT% ...
"%APPCMD%" list apppool "%SITE%" >nul 2>&1 || "%APPCMD%" add apppool /name:"%SITE%" /managedRuntimeVersion:v4.0 /managedPipelineMode:Integrated >nul
"%APPCMD%" list site "%SITE%" >nul 2>&1 || "%APPCMD%" add site /name:"%SITE%" /physicalPath:"%ROOT%\dashboard" /bindings:http/*:%PORT%: >nul
"%APPCMD%" set vdir "%SITE%/" /physicalPath:"%ROOT%\dashboard" >nul
"%APPCMD%" set app "%SITE%/" /applicationPool:"%SITE%" >nul
rem Pages are read as the site's own account (IIS AppPool\GoldTrader), not IUSR - that account is
rem the one given read access below, wherever the GoldTrader folder lives.
"%APPCMD%" set config "%SITE%" -section:system.webServer/security/authentication/anonymousAuthentication /userName:"" /commit:apphost >nul
"%APPCMD%" start site "%SITE%" >nul 2>&1

echo [3/5] Read access for the site: the dashboard and logs folders only ^(never keys.txt^)...
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
icacls "%ROOT%\dashboard" /grant "IIS AppPool\%SITE%:(OI)(CI)RX" >nul || (echo Could not set folder access. & pause & exit /b 1)
icacls "%ROOT%\logs" /grant "IIS AppPool\%SITE%:(OI)(CI)R" >nul || (echo Could not set folder access. & pause & exit /b 1)

echo [4/5] Firewall: port %PORT% open to your home network and Tailscale only...
netsh advfirewall firewall delete rule name="GoldTrader dashboard" >nul 2>&1
netsh advfirewall firewall add rule name="GoldTrader dashboard" dir=in action=allow protocol=TCP localport=%PORT% remoteip=LocalSubnet,100.64.0.0/10 profile=any >nul

echo [5/5] Dashboard password...
if exist "dashboard\App_Data\password.txt" (
  echo       Already set - change it any time with: python goldtrader.py dashboard-password
) else (
  python goldtrader.py dashboard-password
)

echo.
echo Done.
echo   On this PC:            http://localhost:%PORT%
echo   Phone on the same Wi-Fi: http://%COMPUTERNAME%:%PORT%   ^(or this PC's IP address^)
echo   Phone anywhere:        install Tailscale on this PC and the phone, then http://%COMPUTERNAME%:%PORT%
echo The figures update once a minute while start.bat runs.
pause
