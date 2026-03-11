@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo 开始扫描 F:\jarvis ...
python tools\batch_scanner.py "F:/jarvis"
echo.
pause
