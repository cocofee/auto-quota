@echo off
setlocal enabledelayedexpansion
title 一键导入定额数据
cd /d "%~dp0.."

echo ============================================================
echo           一键导入定额数据
echo ============================================================
echo.
echo  功能: 扫描Excel 自动识别专业 导入数据库 生成规则 建索引
echo.

:: 用Python完成省份选择（避免bat变量嵌套问题）
python tools/_select_province.py --allow-new
if errorlevel 1 (
    pause
    exit /b 1
)

:: 读取Python写入的省份名
set /p PROVINCE=<.tmp_selected_province.txt
del /q .tmp_selected_province.txt 2>nul

if not defined PROVINCE (
    echo [错误] 未选择省份
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  省份: !PROVINCE!
echo  操作: 导入定额 + 生成规则 + 重建索引
echo ============================================================
echo.
echo  注意: 相同专业的旧数据会被替换，不同专业互不影响
echo.
set /p "CONFIRM=确认开始导入? [Y/n]: "
if /i "!CONFIRM!"=="n" goto EXIT

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
echo  现在可以双击运行匹配.bat进行清单匹配
echo.

:EXIT
del /q .tmp_selected_province.txt 2>nul
pause