@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title Jarvis 学习管理

:MENU
cls
echo.
echo  ========================================
echo    Jarvis 学习管理
echo  ========================================
echo.
echo    让系统从你的经验中学习，匹配越来越准。
echo.
echo  ----------------------------------------
echo    日常学习（喂数据给系统）
echo  ----------------------------------------
echo    [1] 导入带定额清单（最常用）
echo        ^| 把已套好定额的Excel喂给系统学习
echo.
echo    [2] 导入修正结果
echo        ^| 把修正后的Excel和原始Excel对比学习
echo.
echo    [3] 快捷笔记
echo        ^| 记录零散的造价知识点
echo.
echo  ----------------------------------------
echo    定期维护（保证数据质量）
echo  ----------------------------------------
echo    [4] 候选层审核
echo        ^| 审核系统自动纠正的数据，确认后晋升
echo.
echo    [5] 经验库体检
echo        ^| 用最新规则回扫历史数据，清理错误
echo.
echo    [6] 维护方法卡片
echo        ^| 根据经验+规则自动生成/更新方法论卡片
echo.
echo  ----------------------------------------
echo    [q] 退出
echo  ----------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  请选择: "

if /i "!CHOICE!"=="1" (
    call "%~dp0导入带定额清单.bat"
    goto MENU
)
if /i "!CHOICE!"=="2" (
    call "%~dp0导入修正.bat"
    goto MENU
)
if /i "!CHOICE!"=="3" (
    call "%~dp0快捷笔记.bat"
    goto MENU
)
if /i "!CHOICE!"=="4" (
    call "%~dp0候选层审核.bat"
    goto MENU
)
if /i "!CHOICE!"=="5" (
    call "%~dp0经验库体检.bat"
    goto MENU
)
if /i "!CHOICE!"=="6" (
    call "%~dp0维护方法卡片.bat"
    goto MENU
)
if /i "!CHOICE!"=="q" goto EXIT
if /i "!CHOICE!"=="quit" goto EXIT

echo.
echo  [错误] 请输入 1-6 或 q
timeout /t 2 >nul
goto MENU

:EXIT
echo.
echo  再见!
echo.
