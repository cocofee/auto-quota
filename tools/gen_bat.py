# -*- coding: utf-8 -*-
"""生成运行匹配.bat（GBK编码）"""

bat_content = """@echo off
setlocal enabledelayedexpansion
title 自动套定额系统
cd /d "%~dp0"

echo ============================================================
echo           自动套定额系统 - 一键运行
echo ============================================================
echo.
echo  流程: 匹配 - 生成Excel+审核文件 - Claude Code审核 - 导入修正
echo.

:: ============================================================
:: 选择省份
:: ============================================================
echo [第1步] 选择省份/定额版本:
echo.

set province_count=0
for /d %%P in ("db\\provinces\\*") do (
    set /a province_count+=1
    set "province_!province_count!=%%~nxP"
    echo   [!province_count!] %%~nxP
)

if !province_count!==0 (
    echo [错误] db\\provinces\\ 中没有已安装的省份定额库
    pause
    exit /b 1
)

if !province_count!==1 (
    set "PROVINCE=!province_1!"
    echo   只有1个省份，自动选择: !PROVINCE!
) else (
    echo.
    set /p "PROVINCE_CHOICE=请输入编号: "
    set "PROVINCE=!province_%PROVINCE_CHOICE%!"
    if not defined PROVINCE (
        echo [错误] 无效选择
        pause
        exit /b 1
    )
)
echo.

:: ============================================================
:: 等待拖入文件
:: ============================================================
:WAIT_FILE
echo ============================================================
echo  省份: !PROVINCE!  模式: 搜索+经验库
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
:: 执行匹配
:: ============================================================
:RUN_MATCH
echo.
echo ============================================================
echo  开始匹配...
echo  文件: !CURRENT_FILE!
echo  省份: !PROVINCE!
echo ============================================================
echo.

python tools/jarvis_pipeline.py "!CURRENT_FILE!" --province "!PROVINCE!"

set "LAST_OUTPUT="
for /f "delims=" %%F in ('dir /b /od "output\\匹配结果_*.xlsx" 2^>nul') do (
    set "LAST_OUTPUT=output\\%%F"
)

echo.
if defined LAST_OUTPUT (
    echo  Excel输出: !LAST_OUTPUT!
)
echo  审核文件: output\\review\\ 目录
echo.
echo  下一步: 把审核文件交给 Claude Code 审核

:: ============================================================
:: 操作菜单
:: ============================================================
:POST_MATCH
echo.
echo ============================================================
echo  接下来做什么?
echo ============================================================
echo.
echo   [1] 打开结果Excel - 导入广联达
echo   [2] 打开审核文件目录 - 交给Claude Code审核
echo   [3] 导入修正 - 对比学习
echo   [4] 重新匹配当前文件
echo   [5] 匹配新文件
echo   [q] 退出
echo.
set "ACTION="
set /p "ACTION=请选择: "

if /i "!ACTION!"=="1" goto OPEN_RESULT
if /i "!ACTION!"=="2" goto OPEN_REVIEW
if /i "!ACTION!"=="3" goto IMPORT_CORRECTION
if /i "!ACTION!"=="4" goto RUN_MATCH
if /i "!ACTION!"=="5" goto WAIT_FILE
if /i "!ACTION!"=="q" goto EXIT
if /i "!ACTION!"=="quit" goto EXIT

echo  无效选择，请输入 1-5 或 q
goto POST_MATCH

:: ============================================================
:: [1] 打开结果Excel
:: ============================================================
:OPEN_RESULT
if defined LAST_OUTPUT (
    echo.
    echo  正在打开: !LAST_OUTPUT!
    start "" "!LAST_OUTPUT!"
) else (
    echo.
    echo  未找到输出文件，请先运行匹配
)
goto POST_MATCH

:: ============================================================
:: [2] 打开审核文件目录
:: ============================================================
:OPEN_REVIEW
echo.
echo  正在打开审核文件目录...
echo.
echo  使用方法:
echo    1. 把 output\\review\\ 中的审核文件交给 Claude Code
echo    2. Claude Code 会逐条审核，生成审核报告Excel
echo    3. 你确认后，Claude Code 将修正存入经验库
echo    4. 回来选 [4] 重新匹配，看改进效果
echo.
start "" "output\\review"
goto POST_MATCH

:: ============================================================
:: [3] 导入修正
:: ============================================================
:IMPORT_CORRECTION
echo.
echo ============================================================
echo  导入修正 - 对比学习
echo ============================================================
echo.

if defined LAST_OUTPUT (
    echo  检测到上次输出: !LAST_OUTPUT!
    echo  用这个作为原始文件? [Y/n]
    set /p "USE_LAST=  "
    if /i "!USE_LAST!"=="n" (
        echo.
        echo  请拖入原始输出Excel:
        set /p "ORIGINAL_FILE="
        set "ORIGINAL_FILE=!ORIGINAL_FILE:"=!"
    ) else (
        set "ORIGINAL_FILE=!LAST_OUTPUT!"
    )
) else (
    echo  请拖入原始输出Excel:
    set /p "ORIGINAL_FILE="
    set "ORIGINAL_FILE=!ORIGINAL_FILE:"=!"
)

if not exist "!ORIGINAL_FILE!" (
    echo  文件不存在: !ORIGINAL_FILE!
    goto POST_MATCH
)

echo.
echo  请拖入修正后的Excel:
set /p "CORRECTED_FILE="
set "CORRECTED_FILE=!CORRECTED_FILE:"=!"

if not exist "!CORRECTED_FILE!" (
    echo  文件不存在: !CORRECTED_FILE!
    goto POST_MATCH
)

echo.
echo  开始对比学习...
python -m src.diff_learner "!ORIGINAL_FILE!" "!CORRECTED_FILE!" --province "!PROVINCE!"

echo.
echo  学习完成! 选 [4] 重新匹配看效果。
goto POST_MATCH

:: ============================================================
:: 退出
:: ============================================================
:EXIT
echo.
echo  再见!
echo.
pause
"""

# 写入bat文件: bat文件中路径分隔符用单个反斜杠
# Python字符串中 \\ 会变成文件中的 \\，需要处理
import re

# 把Python转义的 \\\\ 还原为单个 \\（bat文件中路径用单反斜杠）
bat_final = bat_content.replace("\\\\", "\\")

with open("运行匹配.bat", "w", encoding="gbk") as f:
    f.write(bat_final)

print("OK: 运行匹配.bat 已用GBK编码生成")
