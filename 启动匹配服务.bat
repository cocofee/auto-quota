@echo off
chcp 65001 >nul
echo ============================================================
echo   本地匹配API服务
echo   关闭此窗口将停止服务
echo ============================================================
echo.
cd /d "%~dp0"
python local_match_server.py
pause
