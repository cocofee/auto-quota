@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 经验库体检
cd /d "%~dp0.."

echo ============================================================
echo        经验库体检工具
echo ============================================================
echo.
echo  用最新的审核规则回扫经验库，找出历史错误数据。
echo  发现的问题条目会被降级（不删除），不再直通匹配。
echo.

echo ============================================================
echo  选择体检模式:
echo ============================================================
echo.
echo   [1] 只看报告（不修改数据，先看看有多少问题）
echo   [2] 自动修复（发现问题的自动降级为候选层）
echo   [q] 退出
echo.
set "MODE="
set /p "MODE=请选择: "

if /i "!MODE!"=="q" goto EXIT
if /i "!MODE!"=="quit" goto EXIT

if "!MODE!"=="1" (
    echo.
    echo  正在扫描经验库权威层数据...
    echo.
    python tools\experience_health.py
    goto DONE
)

if "!MODE!"=="2" (
    echo.
    echo  ============================================
    echo   注意：这会降级发现问题的权威层数据！
    echo   降级后这些条目不再直通匹配，需要重新确认。
    echo   数据不会被删除，只是降级为候选层。
    echo  ============================================
    echo.
    set "CONFIRM="
    set /p "CONFIRM=确认执行自动修复？(y/n): "
    if /i "!CONFIRM!"=="y" (
        echo.
        echo  正在扫描并修复...
        echo.
        python tools\experience_health.py --fix
    ) else (
        echo  已取消。
    )
    goto DONE
)

echo  无效选择
goto EXIT

:DONE
echo.
echo ============================================================
echo  体检完成
echo ============================================================
echo.
echo  建议定期运行此工具（比如每月一次），
echo  确保经验库数据质量不退化。
echo.

:EXIT
pause