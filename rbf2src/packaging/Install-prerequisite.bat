@echo off
rem Installs the Microsoft Visual C++ 2015-2022 x64 runtime that Mistery needs (once per PC).
echo Installing Microsoft Visual C++ runtime (x64) ...
"%~dp0prerequisites\vc_redist.x64.exe" /install /passive /norestart
set RC=%ERRORLEVEL%
if "%RC%"=="0" echo Done. You can now run Mistery.exe.
if "%RC%"=="1638" echo A newer or equal version is already installed. You can now run Mistery.exe.
if "%RC%"=="3010" echo Installed. Windows asks for a restart before first use.
if not "%RC%"=="0" if not "%RC%"=="1638" if not "%RC%"=="3010" echo Installer returned error %RC%.
pause
