@echo off
setlocal
chcp 65001 >nul 2>&1
title AutoQuota Release Check
cd /d "%~dp0.."

if /i "%~1"=="--help" goto USAGE
if /i "%~1"=="-h" goto USAGE

set "HEALTH_MODE=ci"
if /i "%~1"=="full" set "HEALTH_MODE=full"
if /i "%~1"=="ci" set "HEALTH_MODE=ci"

set "BENCH_MODE=search"
if /i "%~2"=="agent" set "BENCH_MODE=agent"
if /i "%~2"=="search" set "BENCH_MODE=search"

set "DATASET=all"
if not "%~3"=="" set "DATASET=%~3"

set "NO_PAUSE=0"
if /i "%~4"=="--no-pause" set "NO_PAUSE=1"

if /i not "%HEALTH_MODE%"=="ci" if /i not "%HEALTH_MODE%"=="full" (
    echo [ERROR] arg1 must be ci or full
    exit /b 2
)
if /i not "%BENCH_MODE%"=="search" if /i not "%BENCH_MODE%"=="agent" (
    echo [ERROR] arg2 must be search or agent
    exit /b 2
)

if not exist "tests\benchmark_baseline.json" (
    echo [ERROR] missing baseline: tests\benchmark_baseline.json
    echo [HINT] run: python tools\run_benchmark.py --profile full --mode search --save
    set "RC=1"
    goto FINISH
)

echo ============================================================
echo   AutoQuota Release Gate
echo ============================================================
echo   health mode : %HEALTH_MODE%
echo   bench mode  : %BENCH_MODE%
echo   dataset     : %DATASET%
echo ============================================================
echo.

echo [1/3] Run system health (%HEALTH_MODE%)
python tools\system_health_check.py --mode %HEALTH_MODE%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto FINISH

echo.
echo [2/3] Run benchmark compare
set "BENCHMARK_LOG_LEVEL=ERROR"
if /i "%DATASET%"=="all" goto RUN_BENCH_ALL
python tools\run_benchmark.py --profile full --mode %BENCH_MODE% --dataset "%DATASET%" --compare
set "RC=%ERRORLEVEL%"
goto BENCH_DONE

:RUN_BENCH_ALL
python tools\run_benchmark.py --profile full --mode %BENCH_MODE% --compare
set "RC=%ERRORLEVEL%"

:BENCH_DONE
if not "%RC%"=="0" goto FINISH

echo.
echo [3/3] Run Jarvis CLI smoke
python tools\jarvis_pipeline.py --help >nul 2>&1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [ERROR] jarvis_pipeline.py --help failed
    goto FINISH
)

:FINISH
echo.
if "%RC%"=="0" (
    echo ============================================================
    echo   Release Check PASSED
    echo ============================================================
    echo Template: docs\release_check_report_template.md
) else (
    echo ============================================================
    echo   Release Check FAILED (exit %RC%^)
    echo ============================================================
)

if "%NO_PAUSE%"=="0" pause
exit /b %RC%

:USAGE
echo Usage:
echo   scripts\release_check.bat [ci^|full] [search^|agent] [dataset_name] [--no-pause]
echo.
echo Examples:
echo   scripts\release_check.bat ci search all --no-pause
echo   scripts\release_check.bat full search B2_huayou --no-pause
exit /b 0

