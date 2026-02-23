@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

echo ============================================================
echo   AI Pair Guard (Claude Fix + Codex Gate)
echo ============================================================
echo.

echo [STEP1] Send fix tasks to Claude
call scripts\send_to_claude_fix.bat
set "RC1=%ERRORLEVEL%"
if not "%RC1%"=="0" (
  echo [WARN] Claude step returned %RC1%. Continue with local gates...
  echo.
 )

echo [STEP2] Run full system health gate
call scripts\system_health.bat full --no-pause
set "RC2=%ERRORLEVEL%"
if not "%RC2%"=="0" (
  echo [ERROR] Full health gate failed: %RC2%
  exit /b %RC2%
 )

echo [STEP3] Run codex review gate (auto-skip when network unavailable)
call scripts\system_health.bat review --no-pause
set "RC3=%ERRORLEVEL%"

echo [STEP4] Workspace summary
git status --short

echo.
echo ============================================================
echo   Done: Claude=%RC1% Health=%RC2% Review=%RC3%
echo ============================================================
exit /b 0
