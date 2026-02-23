@echo off
setlocal enabledelayedexpansion
title 准确率趋势 - 系统体检报告
cd /d "%~dp0.."

echo ============================================================
echo        系统准确率趋势报告
echo ============================================================
echo.
echo  每次运行Jarvis后，系统会自动记录关键指标。
echo  这个工具帮你查看：系统是越来越准，还是在退化。
echo.

python tools\accuracy_trend.py

echo.
pause
