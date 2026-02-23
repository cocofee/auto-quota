@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call ..\system_health.bat review %*
exit /b %ERRORLEVEL%
