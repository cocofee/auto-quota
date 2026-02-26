@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title AutoQuota System Health
cd /d "%~dp0.."

set "MODE=%~1"
set "REVIEW_FALLBACK_URL=http://47.243.74.21:8080/"

if /i "%MODE%"=="quick" goto RUN_QUICK
if /i "%MODE%"=="full" goto RUN_FULL
if /i "%MODE%"=="ci" goto RUN_CI
if /i "%MODE%"=="review" goto RUN_REVIEW
if /i "%MODE%"=="all" goto RUN_ALL

:MENU
echo ============================================================
echo   AutoQuota System Health
echo ============================================================
echo.
echo   [1] quick   (syntax + import + stability + regression)
echo   [2] full    (quick + full pytest + db schema + exp health)
echo   [3] ci      (strict gate)
echo   [4] review  (codex review --uncommitted)
echo   [5] all     (full + review)
echo   [q] quit
echo.
set "CHOICE="
set /p "CHOICE=Select: "

if /i "%CHOICE%"=="1" set "MODE=quick" & goto RUN_QUICK
if /i "%CHOICE%"=="2" set "MODE=full" & goto RUN_FULL
if /i "%CHOICE%"=="3" set "MODE=ci" & goto RUN_CI
if /i "%CHOICE%"=="4" set "MODE=review" & goto RUN_REVIEW
if /i "%CHOICE%"=="5" set "MODE=all" & goto RUN_ALL
if /i "%CHOICE%"=="q" goto EXIT
if /i "%CHOICE%"=="quit" goto EXIT
echo Invalid choice
echo.
goto MENU

:RUN_QUICK
echo.
echo [RUN] system health (quick)
python tools\system_health_check.py --mode quick
set "RC=%ERRORLEVEL%"
goto FINISH

:RUN_FULL
echo.
echo [RUN] system health (full)
python tools\system_health_check.py --mode full
set "RC=%ERRORLEVEL%"
goto FINISH

:RUN_CI
echo.
echo [RUN] system health (ci)
python tools\system_health_check.py --mode ci
set "RC=%ERRORLEVEL%"
goto FINISH

:RUN_REVIEW
echo.
echo [RUN] code review (codex review --uncommitted)
call :PRECHECK_REVIEW
if not "!CAN_REVIEW!"=="1" (
  echo [WARN] review skipped: !REVIEW_REASON!
  set "RC=2"
  goto FINISH
)
call codex review --uncommitted
set "RC=%ERRORLEVEL%"
goto FINISH

:RUN_ALL
echo.
echo [RUN] system health (full) + code review
python tools\system_health_check.py --mode full
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto FINISH
call :PRECHECK_REVIEW
if not "!CAN_REVIEW!"=="1" (
  echo [WARN] review skipped: !REVIEW_REASON!
  set "RC=2"
  goto FINISH
)
call codex review --uncommitted
set "RC=%ERRORLEVEL%"
goto FINISH

:PRECHECK_REVIEW
set "CAN_REVIEW=1"
set "REVIEW_REASON="
where codex >nul 2>&1
if errorlevel 1 (
  set "CAN_REVIEW=0"
  set "REVIEW_REASON=codex_not_found"
  goto :eof
)
curl.exe -I --max-time 5 https://api.openai.com >nul 2>&1
if errorlevel 1 (
  curl.exe -I --max-time 5 %REVIEW_FALLBACK_URL% >nul 2>&1
  if errorlevel 1 (
    set "CAN_REVIEW=0"
    set "REVIEW_REASON=network_unreachable"
    goto :eof
  )
)
goto :eof

:FINISH
echo.
if "%RC%"=="0" goto FINISH_PASS
if "%RC%"=="2" goto FINISH_REVIEW_SKIP
goto FINISH_FAIL

:FINISH_PASS
echo ============================================================
echo   Checks passed
echo ============================================================
goto FINISH_END

:FINISH_REVIEW_SKIP
echo ============================================================
echo   Checks passed (review skipped: %REVIEW_REASON%)
echo ============================================================
set "RC=0"
goto FINISH_END

:FINISH_FAIL
echo ============================================================
echo   Checks failed, exit code %RC%
echo ============================================================
goto FINISH_END

:FINISH_END
echo.
if /i not "%~2"=="--no-pause" pause
exit /b %RC%

:EXIT
echo.
exit /b 0
