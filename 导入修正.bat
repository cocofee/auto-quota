@echo off
setlocal enabledelayedexpansion
title 导入修正 - 自动学习

echo ============================================================
echo           导入修正 - 自动对比学习
echo ============================================================
echo.
echo  功能说明：
echo  将你在广联达里修正后的Excel和原始输出Excel对比，
echo  系统会自动学习你的修正，下次匹配更准确。
echo.

cd /d "%~dp0"

:: ============================================================
:: 第1步：选择省份
:: ============================================================
echo [第1步] 选择省份/定额版本:
echo.

set province_count=0
for /d %%P in ("db\provinces\*") do (
    set /a province_count+=1
    set "province_!province_count!=%%~nxP"
    echo   [!province_count!] %%~nxP
)

if !province_count!==0 (
    echo [错误] 未找到已安装的省份定额库
    pause
    exit /b 1
)

if !province_count!==1 (
    set "PROVINCE=!province_1!"
    echo.
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
:: 第2步：拖入原始输出Excel
:: ============================================================
echo [第2步] 请将【原始输出Excel】拖入此窗口，然后按回车:
echo   (就是系统生成的那个匹配结果文件，在output文件夹里)
echo.
set /p "ORIGINAL_FILE="

:: 去掉可能的引号
set ORIGINAL_FILE=%ORIGINAL_FILE:"=%

if not exist "%ORIGINAL_FILE%" (
    echo [错误] 文件不存在: %ORIGINAL_FILE%
    pause
    exit /b 1
)
echo.

:: ============================================================
:: 第3步：拖入修正后Excel
:: ============================================================
echo [第3步] 请将【修正后Excel】拖入此窗口，然后按回车:
echo   (就是你在广联达里改好后保存的文件)
echo.
set /p "CORRECTED_FILE="

:: 去掉可能的引号
set CORRECTED_FILE=%CORRECTED_FILE:"=%

if not exist "%CORRECTED_FILE%" (
    echo [错误] 文件不存在: %CORRECTED_FILE%
    pause
    exit /b 1
)
echo.

:: ============================================================
:: 第4步：执行对比学习
:: ============================================================
echo ============================================================
echo  开始对比学习...
echo  原始文件: %ORIGINAL_FILE%
echo  修正文件: %CORRECTED_FILE%
echo  省份: !PROVINCE!
echo ============================================================
echo.

python -m src.diff_learner "%ORIGINAL_FILE%" "%CORRECTED_FILE%" --province "!PROVINCE!"

echo.
echo ============================================================
echo  学习完成！
echo.
echo  系统已经记住了你的修正，下次匹配同类清单会更准确。
echo  你可以继续修正其他项目，每次修正都会让系统变得更好。
echo ============================================================
echo.

pause
