@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" -m mkr.gui
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw -m mkr.gui
    exit /b 0
)

echo 未找到 .venv\Scripts\pythonw.exe 或 pythonw。
echo 请先在项目目录运行: .venv\Scripts\pip install -e .
pause
exit /b 1
