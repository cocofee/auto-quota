@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 对比分析 - 人工定额 vs 贾维斯定额
cd /d "%~dp0.."

echo ============================================================
echo   对比分析工具 - 人工定额 vs 贾维斯定额
echo ============================================================
echo.
echo   用途: 用人工套好定额的Excel评测贾维斯准确率
echo   流程: 贾维斯跑清单 - 和人工定额对比 - 生成差异报告
echo.
echo   注意: 不要先导入参考数据，否则贾维斯直接从经验库匹配
echo         就失去了评测的意义！
echo.
echo ============================================================
echo.

:: ============================================================
:: 第1步：选择省份定额库
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

:: ============================================================
:: 第2步：拖入带定额的Excel文件
:: ============================================================
:WAIT_FILE
echo ============================================================
echo   定额库: !PROVINCE!
echo ============================================================
echo.
echo   请将带定额的预算Excel拖拽到此窗口，然后按回车:
echo     (这个文件应该是人工已经套好定额的广联达导出文件)
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
    echo   [错误] 文件不存在: !INPUT_FILE!
    echo.
    goto WAIT_FILE
)

echo.
echo   文件: !INPUT_FILE!
echo.

:: ============================================================
:: 第3步：贾维斯跑清单
:: ============================================================
echo ############################################################
echo   第1步: 贾维斯开始匹配清单...
echo   (贾维斯会自动跳过定额行，只读清单行)
echo ############################################################
echo.

python main.py "!INPUT_FILE!" --mode agent --province "!PROVINCE!"

if errorlevel 1 (
    echo.
    echo   [错误] 贾维斯匹配失败，请检查错误信息
    echo.
    pause
    goto WAIT_FILE
)

:: 找到最新的输出文件
set "JARVIS_OUTPUT="
for /f "delims=" %%F in ('dir /b /od "output\匹配结果_*.xlsx" 2^>nul') do (
    set "JARVIS_OUTPUT=output\%%F"
)

if not defined JARVIS_OUTPUT (
    echo.
    echo   [错误] 未找到贾维斯输出文件
    echo.
    pause
    goto WAIT_FILE
)

echo.
echo   贾维斯输出: !JARVIS_OUTPUT!
echo.

:: ============================================================
:: 第4步：对比分析
:: ============================================================
echo ############################################################
echo   第2步: 开始对比分析...
echo ############################################################
echo.

python tools\eval_vs_human.py "!INPUT_FILE!" "!JARVIS_OUTPUT!" --province "!PROVINCE!"

if errorlevel 1 (
    echo.
    echo   [警告] 对比分析出现问题，请检查错误信息
    echo.
)

:: ============================================================
:: 完成后菜单
:: ============================================================
:POST_DONE
echo.
echo ============================================================
echo   对比分析完成！
echo ============================================================
echo.
echo   [1] 打开对比报告Excel
echo   [2] 打开output目录
echo   [3] 将人工定额导入经验库（确认无误后）
echo   [4] 换一个文件继续测试
echo   [q] 退出
echo.
set "ACTION="
set /p "ACTION=请选择: "

if /i "!ACTION!"=="1" goto OPEN_REPORT
if /i "!ACTION!"=="2" goto OPEN_DIR
if /i "!ACTION!"=="3" goto IMPORT_REF
if /i "!ACTION!"=="4" goto WAIT_FILE
if /i "!ACTION!"=="q" goto EXIT
if /i "!ACTION!"=="quit" goto EXIT

echo   无效选择
goto POST_DONE

:OPEN_REPORT
if exist "output\对比报告.xlsx" (
    start "" "output\对比报告.xlsx"
) else (
    echo   未找到对比报告文件
)
goto POST_DONE

:OPEN_DIR
start "" "output"
goto POST_DONE

:IMPORT_REF
echo.
echo ============================================================
echo   导入人工定额到经验库
echo ============================================================
echo.
echo   注意：这会把人工预算中的定额对应关系导入经验库，
echo   以后贾维斯遇到类似清单时可以直接匹配。
echo.
echo   确认导入吗？(y/n)
set "CONFIRM="
set /p "CONFIRM="
if /i "!CONFIRM!"=="y" (
    python tools\import_reference.py "!INPUT_FILE!" --province "!PROVINCE!"
    if errorlevel 1 (
        echo   [错误] 导入失败
    ) else (
        echo   [成功] 已导入经验库
    )
) else (
    echo   已取消
)
goto POST_DONE

:EXIT
del /q .tmp_selected_province.txt 2>nul
del /q .tmp_selected_aux_provinces.txt 2>nul
echo.
echo   再见!
echo.
pause
