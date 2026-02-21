@echo off
setlocal enabledelayedexpansion
title 快捷笔记
cd /d "%~dp0.."

echo ============================================================
echo   快捷笔记 - 记录零散的造价知识
echo ============================================================
echo.
echo   论坛答疑、微信群讨论、AI对话等随手记下来
echo   维护方法卡片时会自动融入
echo.
echo   请选择:
echo     [1] 直接记笔记（交互模式，一条一条输入）
echo     [2] 导入笔记文件（knowledge\笔记\ 下的txt）
echo     [3] 查看已有笔记
echo.
set "MODE="
set /p "MODE=  请选择(1/2/3): "

if "!MODE!"=="1" (
    echo.
    python tools/add_note.py
) else if "!MODE!"=="2" (
    echo.
    echo   正在导入 knowledge\笔记\ 目录...
    echo.
    python tools/add_note.py --import-all
) else if "!MODE!"=="3" (
    echo.
    python tools/add_note.py --list
) else (
    echo.
    echo   [错误] 请输入 1、2 或 3
)

echo.
pause
