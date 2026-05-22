@echo off
setlocal
cd /d "%~dp0.."

if "%~1"=="" (
  echo Usage:
  echo   tools\生成GCCP辅助组价源.bat "清单.xlsx" "辅助组价源.GBQ7" "正式工程.GBQ7" "省份或定额库"
  exit /b 1
)

set "BILL=%~1"
set "AUX=%~2"
set "FORMAL=%~3"
set "PROVINCE=%~4"

python tools\gccp_aux_workflow.py build --bill "%BILL%" --aux-gbq7 "%AUX%" --formal-gbq7 "%FORMAL%" --province "%PROVINCE%"
