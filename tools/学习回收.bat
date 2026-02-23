@echo off
chcp 65001 >nul

echo ============================================================
echo   Jarvis 学习回收
echo   用法：把 原始输出.xlsx 和 修正版.xlsx 拖到本文件上
echo ============================================================
echo.

if "%~1"=="" (
    echo 请拖拽两个Excel文件到此bat文件上：
    echo   第1个：Jarvis输出的原始Excel
    echo   第2个：在广联达修正后导出的Excel
    echo.
    pause
    exit /b 1
)

if "%~2"=="" (
    echo 需要两个文件！
    echo   第1个：Jarvis输出的原始Excel
    echo   第2个：在广联达修正后导出的Excel
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0.."
python tools/jarvis_learn.py "%~1" "%~2"

echo.
pause
