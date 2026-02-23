@echo off
setlocal
chcp 65001 >nul 2>&1
if exist ".\scripts\system_health.bat" (
  call ".\scripts\system_health.bat" %*
) else (
  echo [ERROR] Please run this script from project root.
  exit /b 2
)
exit /b %ERRORLEVEL%
