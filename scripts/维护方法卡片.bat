@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 方法卡片维护
cd /d "%~dp0.."

echo ============================================================
echo   方法卡片（批量维护）
echo ============================================================
echo.
echo   根据经验库+定额规则+笔记，生成/更新方法论卡片
echo   卡片越丰富，清单匹配定额就越准确、越快
echo.

:: ============================================================
:: 第1步：选省份
:: ============================================================
python tools\_select_province.py
if errorlevel 1 (
    pause
    exit /b 1
)
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul
del /q .tmp_selected_aux_provinces.txt 2>nul

echo.
echo   已选择: !PROVINCE!
echo.

:: ============================================================
:: 第2步：导入笔记（如果有新笔记的话）
:: ============================================================
if exist "knowledge\笔记\*.txt" (
    echo   检测到笔记文件，正在导入...
    python tools/add_note.py --import-all --province "!PROVINCE!"
    echo.
)

:: ============================================================
:: 第3步：选择维护模式
:: ============================================================
echo   请选择维护模式:
echo.
echo     [1] 先预览（看看哪些模式会生成，不消耗API）
echo     [2] 增量更新（只生成新增的卡片，省API）
echo     [3] 全量刷新（重新分析所有模式，最精准，API较多）
echo.
set "MODE="
set /p "MODE=  请选择(1/2/3): "

if "!MODE!"=="1" (
    echo.
    echo   正在分析现有数据（预览模式）...
    echo.
    python tools/gen_method_cards.py --province "!PROVINCE!" --dry-run --min-samples 3
) else if "!MODE!"=="2" (
    echo.
    echo   正在增量更新方法卡片...
    echo.
    python tools/gen_method_cards.py --province "!PROVINCE!" --incremental --min-samples 3
) else if "!MODE!"=="3" (
    echo.
    echo   正在全量刷新方法卡片（可能需要较长时间）...
    echo.
    python tools/gen_method_cards.py --province "!PROVINCE!" --refresh --min-samples 3
) else (
    echo.
    echo   [错误] 请输入 1、2 或 3
    pause
    exit /b 1
)

if errorlevel 1 (
    echo.
    echo   [错误] 方法卡片生成失败，请查看上方错误信息
) else (
    echo.
    echo   [完成] 方法卡片已更新
    echo.
    echo   卡片文件: knowledge_notes\method_cards.md
)

echo.
pause