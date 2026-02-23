@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call ..\release_check.bat %*
exit /b %ERRORLEVEL%
