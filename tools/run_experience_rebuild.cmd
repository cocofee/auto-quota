@echo off
setlocal

set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "LOG=%~1"
if "%LOG%"=="" set "LOG=logs\experience_rebuild_manual.log"

echo [%date% %time%] rebuild-start>>"%LOG%"
python tools\rebuild_index_qwen3.py --exp-only >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] rebuild-end code=%RC%>>"%LOG%"
exit /b %RC%
