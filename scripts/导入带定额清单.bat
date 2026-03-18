@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 导入带定额清单
cd /d "%~dp0.."

echo ============================================================
echo   导入带定额清单 - 让系统学习人工经验
echo ============================================================
echo.
echo   把带定额Excel或整个文件夹拖进来，系统会自动学习清单和定额的对应关系
echo.

:: ============================================================
:: 第1步：选择省份定额库
:: ============================================================
python tools\_select_province.py --allow-new
if errorlevel 1 (
    pause
    exit /b 1
)
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul
set "AUX_PROVINCES="
if exist .tmp_selected_aux_provinces.txt (
    set /p AUX_PROVINCES=<.tmp_selected_aux_provinces.txt
    del /q .tmp_selected_aux_provinces.txt 2>nul
)
echo.

:: ============================================================
:: 第2步：拖入带定额的Excel或文件夹
:: ============================================================
:WAIT_FILE
echo ============================================================
echo   定额库: !PROVINCE!
echo ============================================================
echo.
echo   请将带定额的Excel文件或文件夹拖拽到此窗口，然后按回车:
echo     q=退出
echo.
set "INPUT_FILE="
set /p "INPUT_FILE="
set "INPUT_FILE=!INPUT_FILE:"=!"

if /i "!INPUT_FILE!"=="q" goto EXIT
if /i "!INPUT_FILE!"=="quit" goto EXIT
if /i "!INPUT_FILE!"=="exit" goto EXIT

if not exist "!INPUT_FILE!" (
    echo.
    echo   [错误] 文件或文件夹不存在: !INPUT_FILE!
    echo.
    goto WAIT_FILE
)

echo.
echo   文件: !INPUT_FILE!
echo   定额库: !PROVINCE!
echo.
echo   开始导入...
echo.

if defined AUX_PROVINCES (
    python tools\import_reference.py "!INPUT_FILE!" --province "!PROVINCE!" --aux-provinces "!AUX_PROVINCES!"
) else (
    python tools\import_reference.py "!INPUT_FILE!" --province "!PROVINCE!"
)

if errorlevel 1 (
    echo.
    echo   [错误] 导入失败，请检查错误信息
) else (
    echo.
    echo   [成功] 导入完成
)

echo.
echo ============================================================
echo   继续导入下一个文件/文件夹？
echo ============================================================
echo   [1] 继续导入（同省份）
echo   [2] 换省份
echo   [q] 退出
echo.
set "ACTION="
set /p "ACTION=请选择: "

if /i "!ACTION!"=="1" goto WAIT_FILE
if /i "!ACTION!"=="2" (
    python tools\_select_province.py --allow-new
    if errorlevel 1 (
        pause
        exit /b 1
    )
    set /p PROVINCE=<.tmp_selected_province.txt
    del /q .tmp_selected_province.txt 2>nul
    set "AUX_PROVINCES="
    if exist .tmp_selected_aux_provinces.txt (
        set /p AUX_PROVINCES=<.tmp_selected_aux_provinces.txt
        del /q .tmp_selected_aux_provinces.txt 2>nul
    )
    goto WAIT_FILE
)

:EXIT
del /q .tmp_selected_province.txt 2>nul
del /q .tmp_selected_aux_provinces.txt 2>nul
echo.
echo   再见!
echo.
pause
