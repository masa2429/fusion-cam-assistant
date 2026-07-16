@echo off
rem Fusion CAM Assistant アンインストーラ（ダブルクリックで実行可）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
