@echo off
chcp 65001 >nul 2>&1
title Codex 5.3 代码审查
echo ============================================================
echo   Codex 5.3 代码审查
echo ============================================================
echo.

cd /d "%~dp0"

echo 正在运行 codex review --uncommitted ...
echo.

call codex review --uncommitted

echo.
echo ============================================================
echo   审查完成
echo ============================================================
echo.
echo 按任意键关闭...
pause >nul
