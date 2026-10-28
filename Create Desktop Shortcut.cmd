@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create Desktop Shortcut.ps1"
if errorlevel 1 pause
