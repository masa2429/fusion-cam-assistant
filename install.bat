@echo off
rem QUHP CAM Assistant インストーラ（ダブルクリックで実行可）
rem PowerShell の実行ポリシーに関係なく install.ps1 を実行する
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
