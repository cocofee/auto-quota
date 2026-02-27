@echo off
setlocal enabledelayedexpansion
title 定额安装

:MENU
cls
echo.
echo  ========================================
echo    定额安装（省份初始化）
echo  ========================================
echo.
echo    首次使用新省份时，需要导入定额数据。
echo    安装完成后日常使用不需要再运行此工具。
echo.
echo  ----------------------------------------
echo    [1] 导入定额库（必选）
echo        ^| 自动筛选未导入的省份
echo.
echo    [2] 导入定额规则（可选）
echo        ^| 导入定额说明文本，提升匹配准确率
echo.
echo    [q] 退出
echo  ----------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  请选择: "

if /i "!CHOICE!"=="1" goto IMPORT_QUOTA
if /i "!CHOICE!"=="2" goto IMPORT_RULES
if /i "!CHOICE!"=="q" goto EXIT
if /i "!CHOICE!"=="quit" goto EXIT

echo.
echo  [错误] 请输入 1-2 或 q
timeout /t 2 >nul
goto MENU

:IMPORT_QUOTA
cd /d "%~dp0.."

echo.
echo ============================================================
echo           导入定额数据库
echo ============================================================
echo.
echo  流程: 扫描Excel 自动识别专业 导入数据库 重建索引
echo.

:: 只显示未导入的省份，已导入的自动跳过
python tools/_select_province.py --only-new
if errorlevel 1 (
    pause
    goto MENU
)

:: 读取Python写的省份名
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul

if not defined PROVINCE (
    echo [错误] 未选择省份
    pause
    goto MENU
)

echo.
echo ============================================================
echo  省份: !PROVINCE!
echo  操作: 导入定额 + 重建索引
echo ============================================================
echo.
echo  注意: 相同专业的旧数据会被替换，不同专业互不影响
echo.
set /p "CONFIRM=确认开始导入? [Y/n]: "
if /i "!CONFIRM!"=="n" goto MENU

echo.
echo ============================================================
echo  开始导入...
echo ============================================================
echo.

python tools/import_all.py --province "!PROVINCE!"

echo.
echo ============================================================
echo  导入完成!
echo ============================================================
echo.

del /q .tmp_selected_province.txt 2>nul
pause
goto MENU

:IMPORT_RULES
cd /d "%~dp0.."

echo.
echo ============================================================
echo   导入定额规则
echo ============================================================
echo.
echo   使用说明：
echo   1. 在 knowledge\规则库\ 下按省份建文件夹
echo   2. 把定额说明文本文件(.txt)放到对应省份文件夹下
echo   3. 运行此功能自动解析并导入规则
echo.
echo   文件夹结构示例：
echo     knowledge\规则库\北京2024\安装定额说明.txt
echo     knowledge\规则库\北京2024\给排水补充说明.txt
echo     knowledge\规则库\山东2024\安装定额说明.txt
echo.
echo ============================================================
echo.

REM 检查目录
if not exist "knowledge\规则库\" (
    echo [提示] knowledge\规则库\ 目录不存在，正在创建...
    mkdir "knowledge\规则库"
    echo.
    echo 已创建目录，请按以下步骤操作：
    echo   1. 在 knowledge\规则库\ 下新建省份文件夹
    echo   2. 把定额说明文本放进去（.txt格式）
    echo   3. 再次运行此功能
    echo.
    pause
    goto MENU
)

python srcule_knowledge.py import

echo.
python srcule_knowledge.py stats

echo.
pause
goto MENU

:EXIT
echo.
echo  再见!
echo.