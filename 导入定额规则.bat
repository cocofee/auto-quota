@echo off

echo ============================================================
echo   自动套定额系统 - 定额规则导入工具
echo ============================================================
echo.
echo   使用说明：
echo   1. 在 knowledge\规则库\ 下按省份建文件夹
echo   2. 把定额说明文本文件(.txt)放到对应省份文件夹中
echo   3. 双击本文件即可自动导入所有规则
echo.
echo   文件夹结构示例：
echo     knowledge\规则库\北京2024\安装工程说明.txt
echo     knowledge\规则库\北京2024\给排水章节说明.txt
echo     knowledge\规则库\山东2024\安装工程说明.txt
echo.
echo ============================================================
echo.

cd /d "%~dp0"

REM 检查目录
if not exist "knowledge\规则库\" (
    echo [提示] knowledge\规则库\ 目录不存在，正在创建...
    mkdir "knowledge\规则库"
    echo.
    echo 已创建目录，请按以下步骤操作：
    echo   1. 在 knowledge\规则库\ 下新建省份文件夹（如：北京2024）
    echo   2. 把定额说明文本放进去（.txt格式）
    echo   3. 重新双击本文件
    echo.
    pause
    exit /b 0
)

python src\rule_knowledge.py import

echo.
python src\rule_knowledge.py stats

echo.
pause
