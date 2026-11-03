@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 找不到 .venv\Scripts\python.exe，请先创建虚拟环境并运行 pip install -e .
    pause
    exit /b 1
)

echo 正在构建 onedir 版本（仅 QtCore/QtGui/QtWidgets，禁用 UPX）...
if exist "mkr.spec" del /q "mkr.spec"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name mkr --icon "assets\ico.ico" --version-file "version_info.txt" ^
  --collect-all mkr --collect-submodules pynput ^
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia ^
  --noupx ^
  entry.py

if errorlevel 1 (
    echo.
    echo 构建失败。
    pause
    exit /b 1
)

echo.
echo 构建完成: dist\mkr\mkr.exe
echo 注意：未签名的 exe 仍可能触发 SmartScreen/Defender；日常启动建议用 mkr.lnk
echo 或 run_gui.cmd（直接调用 pythonw，不经过 exe）。
echo 分发前请对 exe 做代码签名（OV/EV/Azure Trusted Signing）。
pause
