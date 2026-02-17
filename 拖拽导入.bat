@echo off

echo ============================================================
echo   自动套定额系统 - 拖拽导入工具
echo ============================================================
echo.
echo   把Excel文件直接拖到本文件上即可导入
echo.

cd /d "%~dp0"

REM 检查是否有拖入的文件
if "%~1"=="" (
    echo [错误] 请把Excel文件拖拽到本文件上
    echo.
    echo 使用方法：
    echo   1. 从造价Home导出Excel文件
    echo   2. 把Excel文件拖到"拖拽导入.bat"图标上
    echo   3. 输入省份名称
    echo   4. 自动导入
    echo.
    pause
    exit /b 1
)

echo 文件: %~nx1
echo.

REM 让用户输入省份
set /p province="请输入省份名称（如：四川、江苏、山东）: "

if "%province%"=="" (
    echo [错误] 省份名称不能为空
    pause
    exit /b 1
)

echo.
echo 开始导入...
echo   文件: %~nx1
echo   省份: %province%
echo.

python tools\import_reference.py "%~1" --province "%province%" --project "%~n1"

echo.
if %errorlevel%==0 (
    echo [成功] 导入完成，数据已进入候选层
) else (
    echo [失败] 导入失败，请检查文件格式
)

echo.
pause
