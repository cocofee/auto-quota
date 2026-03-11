@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo  批量匹配工具
echo  ============
echo.
echo  [1] 白天模式 - 只跑安装类标准清单（最有价值的先跑）
echo  [2] 晚上模式 - 全部都跑（放着过夜）
echo.
set /p choice=选择模式 (1/2):

if "%choice%"=="2" goto night
goto day

:day
echo.
echo 白天模式：只跑安装类标准清单
python tools\batch_loop.py --mode day
goto end

:night
echo.
echo 晚上模式：全部都跑，放着过夜
python tools\batch_loop.py --mode night
goto end

:end
echo.
pause
