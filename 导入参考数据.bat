@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo   自动套定额系统 - 参考数据导入工具
echo ============================================================
echo.
echo   使用说明：
echo   1. 把造价Home导出的Excel文件放到 data\reference\ 下的省份文件夹中
echo   2. 文件夹名就是省份名（如：四川、江苏、山东）
echo   3. 双击本文件即可自动导入所有数据
echo.
echo   文件夹结构示例：
echo     data\reference\四川\导出单位工程1.xlsx
echo     data\reference\四川\导出单位工程2.xlsx
echo     data\reference\江苏\某项目.xlsx
echo     data\reference\山东\某项目.xlsx
echo.
echo ============================================================
echo.

cd /d "%~dp0"

REM 检查 data\reference 目录是否存在
if not exist "data\reference\" (
    echo [错误] data\reference\ 目录不存在
    echo 请先创建目录并放入Excel文件
    pause
    exit /b 1
)

REM 检查是否有省份文件夹
set found=0
for /d %%P in ("data\reference\*") do (
    set found=1
)

if %found%==0 (
    echo [提示] data\reference\ 下没有省份文件夹
    echo.
    echo 请按以下步骤操作：
    echo   1. 在 data\reference\ 下新建文件夹，用省份名命名
    echo      例如：data\reference\四川\
    echo   2. 把造价Home导出的Excel文件放进去
    echo   3. 重新双击本文件
    echo.
    pause
    exit /b 1
)

REM 遍历每个省份文件夹
set total_files=0
set total_success=0

for /d %%P in ("data\reference\*") do (
    set "province=%%~nxP"
    echo.
    echo ------ 省份：!province! ------

    REM 遍历该省份下的所有xlsx文件
    set province_files=0
    for %%F in ("%%P\*.xlsx") do (
        set /a total_files+=1
        set /a province_files+=1

        echo   导入: %%~nxF
        python tools\import_reference.py "%%F" --province "!province!" --project "%%~nF"

        if !errorlevel!==0 (
            set /a total_success+=1
            echo   [成功]
        ) else (
            echo   [失败] 请检查文件格式
        )
    )

    if !province_files!==0 (
        echo   [提示] 该文件夹下没有.xlsx文件
    )
)

echo.
echo ============================================================
echo   导入完成
echo   总文件数: %total_files%
echo   成功: %total_success%
echo   失败: %total_files% - %total_success%
echo.
echo   数据已进入候选层（不影响匹配）
echo   使用系统匹配并确认后，数据会晋升到权威层
echo ============================================================
echo.
pause
