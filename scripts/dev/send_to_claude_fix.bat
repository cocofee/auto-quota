@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

where claude >nul 2>&1
if errorlevel 1 (
  echo [ERROR] claude command not found.
  exit /b 2
)

if "%CLAUDE_CODE_GIT_BASH_PATH%"=="" (
  if exist "C:\Program Files\Git\bin\bash.exe" set "CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe"
  if "%CLAUDE_CODE_GIT_BASH_PATH%"=="" if exist "C:\Progra~1\Git\bin\bash.exe" set "CLAUDE_CODE_GIT_BASH_PATH=C:\Progra~1\Git\bin\bash.exe"
)

if "%CLAUDE_CODE_GIT_BASH_PATH%"=="" (
  echo [ERROR] git-bash path not found. Install Git for Windows first.
  exit /b 4
)

"%CLAUDE_CODE_GIT_BASH_PATH%" --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] git-bash not executable in current session: %CLAUDE_CODE_GIT_BASH_PATH%
  echo [HINT] try running in your own local terminal with normal user permissions.
  exit /b 5
)

set "TASK_FILE="
for /f "delims=" %%f in ('dir /b /o-d "output\health_reports\claude_fix_tasks_*.md" 2^>nul') do (
  set "TASK_FILE=output\health_reports\%%f"
  goto FOUND
)

echo [ERROR] no claude_fix_tasks_*.md found under output\health_reports.
exit /b 3

:FOUND
echo [INFO] handoff file: %TASK_FILE%
echo.
echo [RUN] launching Claude...
call claude "Please read %TASK_FILE% and fix issues in priority order P0->P1->P2. First output a short plan, then patch, run tests, and summarize changed files and residual risks."
exit /b %ERRORLEVEL%
