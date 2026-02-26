@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
title Git提交

cd /d "%~dp0.."

echo.
echo  ========================================
echo       代码提交工具
echo  ========================================
echo.

:: 显示有哪些改动
echo [改动文件]
git status --short
echo.

:: 统计改动数量
set "COUNT=0"
for /f %%a in ('git status --short ^| find /c /v ""') do set "COUNT=%%a"

if "%COUNT%"=="0" (
    echo 没有需要提交的改动
    goto END
)

echo 共 %COUNT% 个文件有改动
echo.
echo  请选择提交说明 (或输入自定义内容):
echo.
echo  [1] 更新知识库规则
echo  [2] 修复bug
echo  [3] 界面优化
echo  [4] 新增功能
echo  [5] 部署配置更新
echo.
set /p msgchoice="输入编号(1-5)或直接输入说明: "

if "%msgchoice%"=="1" set "MSG=更新知识库规则"
if "%msgchoice%"=="2" set "MSG=修复bug"
if "%msgchoice%"=="3" set "MSG=界面优化"
if "%msgchoice%"=="4" set "MSG=新增功能"
if "%msgchoice%"=="5" set "MSG=部署配置更新"

:: 如果不是1-5,就把输入的内容当作提交说明
if not defined MSG set "MSG=%msgchoice%"

echo.
echo [提交说明] %MSG%
echo.
set /p confirm="确认提交? (y/n): "
if /i not "%confirm%"=="y" (
    echo 已取消
    goto END
)

:: 添加所有改动并提交
git add -A
git commit -m "%MSG%"

if %errorlevel% neq 0 (
    echo [失败] 提交出错
    goto END
)

echo.
echo  ========================================
echo   提交成功!
echo  ========================================

:END
echo.
pause
