@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title 候选层审核
cd /d "%~dp0.."

echo ============================================================
echo        候选层审核工具
echo ============================================================
echo.
echo  候选层存放了系统自动纠正的数据（未经人工确认）。
echo  审核确认后，数据会晋升为权威层，参与下次匹配直通。
echo.

echo ============================================================
echo  选择操作:
echo ============================================================
echo.
echo   [1] 查看候选层数据（只看不改）
echo   [2] 逐条审核（推荐，每条可选择 确认/跳过/删除）
echo   [3] 全部晋升（谨慎！不审核直接全部晋升）
echo   [q] 退出
echo.
set "MODE="
set /p "MODE=请选择: "

if /i "!MODE!"=="q" goto EXIT
if /i "!MODE!"=="quit" goto EXIT

if "!MODE!"=="1" (
    echo.
    python tools\experience_promote.py --list
    goto DONE
)

if "!MODE!"=="2" (
    echo.
    python tools\experience_promote.py
    goto DONE
)

if "!MODE!"=="3" (
    echo.
    echo  ============================================
    echo   警告：这会把所有候选层数据直接晋升为权威层！
    echo   建议先选 [1] 查看，或选 [2] 逐条审核。
    echo  ============================================
    echo.
    set "CONFIRM="
    set /p "CONFIRM=确认全部晋升？(y/n): "
    if /i "!CONFIRM!"=="y" (
        echo.
        python tools\experience_promote.py --all --limit 0
    ) else (
        echo  已取消。
    )
    goto DONE
)

echo  无效选择
goto EXIT

:DONE
echo.
echo ============================================================
echo  操作完成
echo ============================================================
echo.

:EXIT
pause
