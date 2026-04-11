@echo off
setlocal

cd /d "%~dp0"

if /I "%~1"=="dry" goto dry_run
if /I "%~1"=="full" goto full_run

echo ==========================================
echo RAW Batch Ingest
echo Repo: %CD%
echo RAW Root: E:\Jarvis-Raw
echo ==========================================
echo 1. Dry run ^(preview only^)
echo 2. Import + compile wiki + build qmd
echo.
choice /C 12 /N /M "Choose [1/2] and press Enter: "
if errorlevel 2 goto full_run
if errorlevel 1 goto dry_run

:dry_run
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --dry-run --limit 50
goto end

:full_run
python tools\ingest_raw_batch.py --raw-root "E:\Jarvis-Raw" --compile-wiki --build-qmd
goto end

:end
echo.
echo Completed.
pause
exit /b %ERRORLEVEL%
