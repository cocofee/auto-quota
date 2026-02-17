@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title 自动套定额系统

echo ============================================================
echo           自动套定额系统 - 一键运行
echo ============================================================
echo.

cd /d "%~dp0"

:: ============================================================
:: 第1步：检测API Key（支持Kimi/DeepSeek/Qwen）
:: ============================================================
set HAS_API_KEY=0
if exist .env (
    findstr /C:"KIMI_API_KEY=sk" .env >nul 2>&1
    if not errorlevel 1 set HAS_API_KEY=1
    findstr /C:"DEEPSEEK_API_KEY=sk" .env >nul 2>&1
    if not errorlevel 1 set HAS_API_KEY=1
    findstr /C:"QWEN_API_KEY=sk" .env >nul 2>&1
    if not errorlevel 1 set HAS_API_KEY=1
)

:: ============================================================
:: 第2步：选择省份（自动扫描已安装省份）
:: ============================================================
echo [第1步] 选择省份/定额版本:
echo.

set province_count=0
for /d %%P in ("db\provinces\*") do (
    set /a province_count+=1
    set "province_!province_count!=%%~nxP"
    echo   [!province_count!] %%~nxP
)

if !province_count!==0 (
    echo [错误] db\provinces\ 中没有已安装的省份定额库
    echo 请先导入定额数据
    pause
    exit /b 1
)

if !province_count!==1 (
    set "PROVINCE=!province_1!"
    echo.
    echo   只有1个省份，自动选择: !PROVINCE!
) else (
    echo.
    set /p "PROVINCE_CHOICE=请输入编号: "
    set "PROVINCE=!province_%PROVINCE_CHOICE%!"
    if not defined PROVINCE (
        echo [错误] 无效选择
        pause
        exit /b 1
    )
)
echo.

:: ============================================================
:: 第3步：选择匹配模式
:: ============================================================
echo [第2步] 选择匹配模式:
if %HAS_API_KEY%==1 (
    echo   [1] Agent模式（推荐）像造价员一样思考，自动学习进化，准确率最高
    echo   [2] 完整模式        搜索+大模型纠偏，准确率较高
    echo   [3] 纯搜索模式      不调大模型，免费但准确率较低
    echo.
    set /p "MODE_CHOICE=请输入 1/2/3 [默认1]: "
    if "!MODE_CHOICE!"=="2" (
        set "MODE=full"
        echo   已选择: 完整模式
    ) else if "!MODE_CHOICE!"=="3" (
        set "MODE=search"
        echo   已选择: 纯搜索模式
    ) else (
        set "MODE=agent"
        echo   已选择: Agent模式（造价员贾维斯）
    )
) else (
    echo   [提示] 未检测到API Key，使用纯搜索模式
    echo   想提高准确率？在.env文件中配置KIMI_API_KEY
    set "MODE=search"
)
echo.

:: ============================================================
:: 第4步：选择清单文件
:: ============================================================
echo [第3步] 请将清单Excel文件拖拽到此窗口，然后按回车:
set /p "INPUT_FILE="

:: 去掉可能的引号
set INPUT_FILE=%INPUT_FILE:"=%

if not exist "%INPUT_FILE%" (
    echo [错误] 文件不存在: %INPUT_FILE%
    pause
    exit /b 1
)

:: ============================================================
:: 第5步：选择过滤范围
:: ============================================================
echo.
echo [第4步] 选择清单范围:
echo   [1] 仅安装工程（编码03开头）
echo   [2] 全部清单
echo.
set /p "SCOPE_CHOICE=请输入 1 或 2 [默认1]: "
if "!SCOPE_CHOICE!"=="2" (
    set "FILTER="
    echo   已选择: 全部清单
) else (
    set "FILTER=--filter-code 03"
    echo   已选择: 仅安装工程
)

echo.
echo ============================================================
echo  开始匹配...
echo  清单文件: %INPUT_FILE%
echo  省份: !PROVINCE!
echo  匹配模式: !MODE!
echo ============================================================
echo.

:: 运行匹配
python main.py "%INPUT_FILE%" --mode !MODE! --province "!PROVINCE!" !FILTER!

echo.
echo ============================================================
echo  匹配完成！
echo.
echo  下一步操作：
echo  1. 在弹出的文件夹中找到输出的Excel
echo  2. 查看"匹配结果"Sheet，重点看黄色和红色标记的清单
echo  3. 修正后保存，然后双击"导入修正.bat"让系统学习
echo ============================================================
echo.

:: 打开输出文件夹
explorer output

pause
