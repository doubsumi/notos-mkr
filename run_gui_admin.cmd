@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0.venv\Scripts\pythonw.exe' -ArgumentList '-m','notos_mkr.gui' -WorkingDirectory '%~dp0' -Verb RunAs"
if errorlevel 1 pause
