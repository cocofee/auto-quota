@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title 定额安装

:MENU
cls
echo.
echo  ========================================
echo    定额安装（新省份初始化）
echo  ========================================
echo.
echo    首次使用新省份时，需要导入定额数据。
echo    安装完成后日常使用不需要再运行此工具。
echo.
echo  ----------------------------------------
echo    [1] 导入定额库（必选）
echo        ^| 导入定额Excel到数据库，生成索引
echo.
echo    [2] 导入定额规则（可选）
echo        ^| 导入定额说明文本，提升匹配准确率
echo.
echo    [q] 退出
echo  ----------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  请选择: "

if /i "!CHOICE!"=="1" (
    call "%~dp0一键导入定额.bat"
    goto MENU
)
if /i "!CHOICE!"=="2" (
    call "%~dp0导入定额规则.bat"
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
