@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title 评测工具

:MENU
cls
echo.
echo  ========================================
echo    评测工具
echo  ========================================
echo.
echo    检验系统匹配质量，看系统是越来越准还是在退化。
echo.
echo  ----------------------------------------
echo    [1] 对比分析
echo        ^| 人工预算 vs Jarvis，生成差异报告
echo.
echo    [2] 准确率趋势
echo        ^| 查看历次运行的准确率变化曲线
echo.
echo    [q] 退出
echo  ----------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  请选择: "

if /i "!CHOICE!"=="1" (
    call "%~dp0对比分析.bat"
    goto MENU
)
if /i "!CHOICE!"=="2" (
    call "%~dp0准确率趋势.bat"
    goto MENU
)
if /i "!CHOICE!"=="q" goto EXIT
if /i "!CHOICE!"=="quit" goto EXIT

echo.
echo  [错误] 请输入 1-2 或 q
timeout /t 2 >nul
goto MENU

:EXIT
echo.
echo  再见!
echo.
