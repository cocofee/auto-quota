@echo off
setlocal
cd /d "%~dp0"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=task"

set "SINCE=%~2"
if "%SINCE%"=="" set "SINCE=20m"

set "TAIL=%~3"
if "%TAIL%"=="" set "TAIL=200"

echo ============================================================
echo AutoQuota Local Logs
echo mode  = %TARGET%
echo since = %SINCE%
echo tail  = %TAIL%
echo ============================================================
echo.

if /i "%TARGET%"=="task" goto task
if /i "%TARGET%"=="all" goto all
if /i "%TARGET%"=="backend" goto backend
if /i "%TARGET%"=="worker" goto worker
if /i "%TARGET%"=="frontend" goto frontend
if /i "%TARGET%"=="help" goto help

echo [ERROR] Unsupported mode: %TARGET%
echo.
goto help

:task
docker compose logs --since %SINCE% --tail %TAIL% -f backend celery-worker
goto end

:all
docker compose logs --since %SINCE% --tail %TAIL% -f frontend backend celery-worker
goto end

:backend
docker compose logs --since %SINCE% --tail %TAIL% -f backend
goto end

:worker
docker compose logs --since %SINCE% --tail %TAIL% -f celery-worker
goto end

:frontend
docker compose logs --since %SINCE% --tail %TAIL% -f frontend
goto end

:help
echo Usage:
echo   %~n0
echo   %~n0 task
echo   %~n0 task 5m 120
echo   %~n0 backend
echo   %~n0 worker
echo   %~n0 frontend
echo   %~n0 all
echo.
pause
goto done

:end
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo [ERROR] docker compose logs failed with code %RC%
  echo.
  pause
)

:done
endlocal
