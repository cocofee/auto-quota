@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title AutoQuota Regression Runner
cd /d "%~dp0"

if /i "%~1"=="--help" goto USAGE
if /i "%~1"=="-h" goto USAGE
if /i "%~1"=="help" goto USAGE

if /i "%~1"=="init" goto CMD_INIT
if /i "%~1"=="smoke" goto CMD_SMOKE
if /i "%~1"=="dev" goto CMD_DEV
if /i "%~1"=="full" goto CMD_FULL
if /i "%~1"=="dev-memory" goto CMD_DEV_MEMORY

goto MENU

:MENU
cls
echo ============================================================
echo   AutoQuota Regression Runner
echo ============================================================
echo.
echo Recommended flow:
echo   1. Build golden set once
echo   2. Run regression after each ranking or rule change
echo.
echo Choose one option:
echo   [1] Build golden set only
echo   [2] Quick regression ^(smoke^)
echo   [3] Standard regression ^(dev, recommended^)
echo   [4] Full regression ^(full, slower^)
echo   [5] Regression with ExperienceDB ^(dev + with_experience^)
echo   [0] Exit
echo.
set /p CHOICE=Enter number and press Enter: 

if "%CHOICE%"=="1" goto RUN_INIT
if "%CHOICE%"=="2" goto ASK_TAG_SMOKE
if "%CHOICE%"=="3" goto ASK_TAG_DEV
if "%CHOICE%"=="4" goto ASK_TAG_FULL
if "%CHOICE%"=="5" goto ASK_TAG_DEV_MEMORY
if "%CHOICE%"=="0" exit /b 0

echo.
echo [ERROR] Invalid input.
pause
goto MENU

:ASK_TAG_SMOKE
set "PROFILE=smoke"
set "WITH_EXPERIENCE=0"
goto ASK_TAG

:ASK_TAG_DEV
set "PROFILE=dev"
set "WITH_EXPERIENCE=0"
goto ASK_TAG

:ASK_TAG_FULL
set "PROFILE=full"
set "WITH_EXPERIENCE=0"
goto ASK_TAG

:ASK_TAG_DEV_MEMORY
set "PROFILE=dev"
set "WITH_EXPERIENCE=1"
goto ASK_TAG

:ASK_TAG
echo.
echo Enter a pipeline version tag.
echo Examples:
echo   fix-fastpath-threshold
echo   fix-ltr-gap
echo   fix-book-routing
echo.
set "PIPELINE_VERSION="
set /p PIPELINE_VERSION=Pipeline version ^(press Enter to auto-generate^): 
if not defined PIPELINE_VERSION (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format ''yyyyMMdd-HHmmss''"') do set "PIPELINE_VERSION=manual-%%i"
)
goto RUN_REGRESSION

:RUN_INIT
cls
echo ============================================================
echo   Build Golden Set
echo ============================================================
echo.
echo Output:
echo   eval\golden_set.jsonl
echo.
python eval\run_regression.py --build-golden-set-only
set "RC=%ERRORLEVEL%"
goto FINISH

:RUN_REGRESSION
cls
echo ============================================================
echo   Run Golden Regression
echo ============================================================
echo   profile          : %PROFILE%
echo   pipeline version : %PIPELINE_VERSION%
if "%WITH_EXPERIENCE%"=="1" (
echo   mode             : with_experience
) else (
echo   mode             : closed_book
)
echo ============================================================
echo.

set "EXTRA_ARGS="
if "%WITH_EXPERIENCE%"=="1" set "EXTRA_ARGS=--with-experience"

python eval\run_regression.py --pipeline-version "%PIPELINE_VERSION%" --profile %PROFILE% %EXTRA_ARGS%
set "RC=%ERRORLEVEL%"
goto FINISH

:CMD_INIT
python eval\run_regression.py --build-golden-set-only
exit /b %ERRORLEVEL%

:CMD_SMOKE
set "PIPELINE_VERSION=%~2"
if not defined PIPELINE_VERSION (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format ''yyyyMMdd-HHmmss''"') do set "PIPELINE_VERSION=smoke-%%i"
)
python eval\run_regression.py --pipeline-version "%PIPELINE_VERSION%" --profile smoke
exit /b %ERRORLEVEL%

:CMD_DEV
set "PIPELINE_VERSION=%~2"
if not defined PIPELINE_VERSION (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format ''yyyyMMdd-HHmmss''"') do set "PIPELINE_VERSION=dev-%%i"
)
python eval\run_regression.py --pipeline-version "%PIPELINE_VERSION%" --profile dev
exit /b %ERRORLEVEL%

:CMD_FULL
set "PIPELINE_VERSION=%~2"
if not defined PIPELINE_VERSION (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format ''yyyyMMdd-HHmmss''"') do set "PIPELINE_VERSION=full-%%i"
)
python eval\run_regression.py --pipeline-version "%PIPELINE_VERSION%" --profile full
exit /b %ERRORLEVEL%

:CMD_DEV_MEMORY
set "PIPELINE_VERSION=%~2"
if not defined PIPELINE_VERSION (
    for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format ''yyyyMMdd-HHmmss''"') do set "PIPELINE_VERSION=dev-memory-%%i"
)
python eval\run_regression.py --pipeline-version "%PIPELINE_VERSION%" --profile dev --with-experience
exit /b %ERRORLEVEL%

:FINISH
echo.
if "%RC%"=="0" (
    echo ============================================================
    echo   Regression Completed
    echo ============================================================
    echo Summary file:
    echo   output\regression\latest_regression_summary.json
    echo.
    echo Main metrics:
    echo   top1_accuracy                 higher is better
    echo   top3_accuracy                 higher is better
    echo   fastpath_precision            higher is better
    echo   confidence_calibration_ece    lower is better
) else (
    echo ============================================================
    echo   Regression Failed ^(exit %RC%^)
    echo ============================================================
    echo If this is your first run, build the golden set first:
    echo   auto regression test.bat init
)
echo.
pause
exit /b %RC%

:USAGE
echo Usage:
echo   ^<this-bat-file^>
echo   ^<this-bat-file^> init
echo   ^<this-bat-file^> smoke [pipeline_version]
echo   ^<this-bat-file^> dev [pipeline_version]
echo   ^<this-bat-file^> full [pipeline_version]
echo   ^<this-bat-file^> dev-memory [pipeline_version]
echo.
echo Examples:
echo   ^<this-bat-file^> init
echo   ^<this-bat-file^> dev fix-fastpath-threshold
echo   ^<this-bat-file^> smoke fix-book-routing
exit /b 0


