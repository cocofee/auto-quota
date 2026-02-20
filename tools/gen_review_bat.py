# -*- coding: utf-8 -*-
"""生成 贾维斯审核.bat（GBK编码）

全流程一键操作：拖入清单Excel → 匹配定额 → 自动审核 → 自动纠正 → 打开结果
"""

bat_content = r"""@echo off
setlocal enabledelayedexpansion
title 贾维斯一键审核
cd /d "%~dp0"

echo ============================================================
echo        贾维斯一键审核 - 匹配+审核+纠正 全自动
echo ============================================================
echo.
echo  全流程: 拖入Excel → 匹配定额 → 自动审核 → 纠正错误 → 出结果
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
:: 等待拖入文件
:: ============================================================
:WAIT_FILE
echo ============================================================
echo  省份: !PROVINCE!
echo ============================================================
echo.
echo  请将清单Excel文件拖拽到此窗口，然后按回车:
echo    q=退出
echo.
set "INPUT_FILE="
set /p "INPUT_FILE="
set "INPUT_FILE=!INPUT_FILE:"=!"

if /i "!INPUT_FILE!"=="q" goto EXIT
if /i "!INPUT_FILE!"=="quit" goto EXIT
if /i "!INPUT_FILE!"=="exit" goto EXIT

if not exist "!INPUT_FILE!" (
    echo.
    echo  [错误] 文件不存在: !INPUT_FILE!
    echo.
    goto WAIT_FILE
)

set "CURRENT_FILE=!INPUT_FILE!"

:: ============================================================
:: 全自动流水线：匹配 → 审核 → 纠正
:: ============================================================
:RUN_ALL
echo.
echo ############################################################
echo  开始全自动流水线
echo  文件: !CURRENT_FILE!
echo  省份: !PROVINCE!
echo ############################################################
echo.

python tools\jarvis_pipeline.py "!CURRENT_FILE!" --province "!PROVINCE!"

:: 找到最新的输出文件
set "LAST_OUTPUT="
for /f "delims=" %%F in ('dir /b /od "output\匹配结果_*.xlsx" 2^>nul') do (
    set "LAST_OUTPUT=output\%%F"
)
set "CORRECTED_OUTPUT="
for /f "delims=" %%F in ('dir /b /od "output\*_已审核.xlsx" 2^>nul') do (
    set "CORRECTED_OUTPUT=output\%%F"
)

echo.
echo ############################################################
echo  全流程完成!
echo ############################################################
echo.

if defined CORRECTED_OUTPUT (
    echo  已审核结果: !CORRECTED_OUTPUT!
    echo  （已自动纠正错误，可直接导入广联达）
) else if defined LAST_OUTPUT (
    echo  匹配结果: !LAST_OUTPUT!
    echo  （审核通过，无需纠正）
) else (
    echo  未生成输出文件，请检查清单格式
)

:: ============================================================
:: 操作菜单
:: ============================================================
:POST_DONE
echo.
echo ============================================================
echo  接下来做什么?
echo ============================================================
echo.
if defined CORRECTED_OUTPUT (
    echo   [1] 打开已审核Excel - 直接导入广联达
) else if defined LAST_OUTPUT (
    echo   [1] 打开结果Excel - 导入广联达
)
echo   [2] 打开output目录
echo   [3] 重新处理当前文件
echo   [4] 处理新文件
echo   [q] 退出
echo.
set "ACTION="
set /p "ACTION=请选择: "

if /i "!ACTION!"=="1" goto OPEN_RESULT
if /i "!ACTION!"=="2" goto OPEN_DIR
if /i "!ACTION!"=="3" goto RUN_ALL
if /i "!ACTION!"=="4" goto WAIT_FILE
if /i "!ACTION!"=="q" goto EXIT
if /i "!ACTION!"=="quit" goto EXIT

echo  无效选择
goto POST_DONE

:OPEN_RESULT
if defined CORRECTED_OUTPUT (
    start "" "!CORRECTED_OUTPUT!"
) else if defined LAST_OUTPUT (
    start "" "!LAST_OUTPUT!"
) else (
    echo  没有结果文件
)
goto POST_DONE

:OPEN_DIR
start "" "output"
goto POST_DONE

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
