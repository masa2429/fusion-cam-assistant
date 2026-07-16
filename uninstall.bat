@echo off
rem Fusion CAM Assistant uninstaller (double-click to run)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
