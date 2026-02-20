# -*- coding: utf-8 -*-
"""生成 贾维斯审核.bat（GBK编码）

单独对已有的匹配结果运行Jarvis自动审核+纠正。
适用场景：之前跑过匹配，想单独重新审核（比如规则更新后）。
"""

bat_content = r"""@echo off
setlocal enabledelayedexpansion
title 贾维斯自动审核
cd /d "%~dp0"

echo ============================================================
echo           贾维斯自动审核 - 检测错误并自动纠正
echo ============================================================
echo.
echo  功能: 对已有的匹配结果运行自动审核
echo        检测类别错误/管材错配/配对错误等，自动纠正
echo.

:: ============================================================
:: 选择省份
:: ============================================================
python tools\_select_province.py
if errorlevel 1 (
    pause
    exit /b 1
)
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul
echo.

:: ============================================================
:: 查找最近的匹配结果JSON
:: ============================================================
:SELECT_FILE
echo ============================================================
echo  省份: !PROVINCE!
echo ============================================================
echo.
echo  选择要审核的匹配结果:
echo.

set file_count=0

:: 列出output\temp中的pipeline JSON（由运行匹配.bat生成）
for /f "delims=" %%F in ('dir /b /od "output\temp\pipeline_*.json" 2^>nul') do (
    set /a file_count+=1
    set "file_!file_count!=output\temp\%%F"
    echo   [!file_count!] %%~nF
)

if !file_count!==0 (
    echo   没有找到匹配结果!
    echo   请先双击"运行匹配.bat"生成匹配结果。
    echo.
    pause
    exit /b 1
)

echo.
echo   按回车选择最新的，或输入编号选择其他
echo   q=退出
echo.

set "CHOICE="
set /p "CHOICE=请选择: "

if /i "!CHOICE!"=="q" goto EXIT
if /i "!CHOICE!"=="quit" goto EXIT

:: 默认选最新的
if "!CHOICE!"=="" set "CHOICE=!file_count!"

set "JSON_FILE=!file_%CHOICE%!"

if not defined JSON_FILE (
    echo  无效选择
    goto SELECT_FILE
)

if not exist "!JSON_FILE!" (
    echo  文件不存在: !JSON_FILE!
    goto SELECT_FILE
)

echo.
echo  已选择: !JSON_FILE!

:: ============================================================
:: 运行审核
:: ============================================================
:RUN_REVIEW
echo.
echo ============================================================
echo  正在审核...
echo ============================================================
echo.

python tools\jarvis_auto_review.py "!JSON_FILE!" --province "!PROVINCE!"

:: jarvis_auto_review 有纠正时返回1，无纠正返回0
:: 检查是否生成了纠正JSON
set "CORRECTION_FILE="
for /f "delims=" %%F in ('dir /b /od "output\temp\auto_corrections_*.json" 2^>nul') do (
    set "CORRECTION_FILE=output\temp\%%F"
)

if not defined CORRECTION_FILE (
    echo.
    echo  审核完成，未发现需要纠正的错误!
    goto POST_REVIEW
)

:: ============================================================
:: 纠正Excel
:: ============================================================
echo.
echo ============================================================
echo  发现纠正项，正在查找对应的匹配结果Excel...
echo ============================================================

:: 查找最新的匹配结果Excel
set "MATCH_EXCEL="
for /f "delims=" %%F in ('dir /b /od "output\匹配结果_*.xlsx" 2^>nul') do (
    set "MATCH_EXCEL=output\%%F"
)

if not defined MATCH_EXCEL (
    echo.
    echo  未找到匹配结果Excel文件。
    echo  纠正JSON已保存: !CORRECTION_FILE!
    echo  你可以手动运行: python tools\jarvis_correct.py "Excel路径" "!CORRECTION_FILE!"
    goto POST_REVIEW
)

echo.
echo  匹配结果: !MATCH_EXCEL!
echo  纠正文件: !CORRECTION_FILE!
echo.
echo  是否将纠正写入Excel? [Y/n]
set "DO_CORRECT="
set /p "DO_CORRECT=  "
if /i "!DO_CORRECT!"=="n" goto POST_REVIEW

echo.
python tools\jarvis_correct.py "!MATCH_EXCEL!" "!CORRECTION_FILE!"

:: 找到已审核版
set "CORRECTED_EXCEL="
for /f "delims=" %%F in ('dir /b /od "output\*_已审核.xlsx" 2^>nul') do (
    set "CORRECTED_EXCEL=output\%%F"
)
if defined CORRECTED_EXCEL (
    echo.
    echo  已审核Excel已生成: !CORRECTED_EXCEL!
)

:: ============================================================
:: 操作菜单
:: ============================================================
:POST_REVIEW
echo.
echo ============================================================
echo  接下来做什么?
echo ============================================================
echo.
echo   [1] 打开output目录 - 查看结果
echo   [2] 审核其他文件
echo   [3] 重新审核当前文件
echo   [q] 退出
echo.
set "ACTION="
set /p "ACTION=请选择: "

if /i "!ACTION!"=="1" goto OPEN_OUTPUT
if /i "!ACTION!"=="2" goto SELECT_FILE
if /i "!ACTION!"=="3" goto RUN_REVIEW
if /i "!ACTION!"=="q" goto EXIT
if /i "!ACTION!"=="quit" goto EXIT

echo  无效选择
goto POST_REVIEW

:OPEN_OUTPUT
start "" "output"
goto POST_REVIEW

:EXIT
del /q .tmp_selected_province.txt 2>nul
echo.
echo  再见!
echo.
pause
"""

with open("贾维斯审核.bat", "w", encoding="gbk") as f:
    f.write(bat_content)

print("OK: 贾维斯审核.bat 已用GBK编码生成")
