@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion
title 懒猫微服部署

set "PROJECT_DIR=%~dp0.."
set "ACR_REGISTRY=crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com"
set "ACR_NAMESPACE=cocofee2026"
set "ACR_USER=nick1293622534"
set "FRONTEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-frontend:latest"
set "BACKEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-app:latest"

cd /d "%PROJECT_DIR%"

echo.
echo  ========================================
echo       懒猫微服 部署工具
echo  ========================================
echo.
echo  [1] 一键更新 (自动判断改了什么)
echo  [2] 查看日志
echo  [3] 登录仓库 (提示登录失败时用)
echo.
set /p choice="请输入编号(1-3): "

if "%choice%"=="1" goto UPDATE
if "%choice%"=="2" goto LOGS
if "%choice%"=="3" goto LOGIN
echo 无效选择
goto END

:LOGIN
echo.
echo 提示: 密码是 COCOfee2012
echo.
docker login --username=%ACR_USER% %ACR_REGISTRY%
goto END

:LOGS
echo.
echo [日志] Ctrl+C 退出
echo.
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 22222 box@fc03:1136:3825:2790:9282:59f7:ba36:403 "lzc-docker logs -f --tail 50 cloudlazycatappautoquota-celery-worker-1"
goto END

:UPDATE
echo.
echo [检测] 正在分析哪些文件有改动...

:: 用git检测改动的文件
set "NEED_FRONTEND=0"
set "NEED_BACKEND=0"

:: 检查有没有改动（暂存+未暂存+未跟踪的新文件）
for /f "delims=" %%f in ('git diff --name-only 2^>nul') do (
    set "F=%%f"
    echo     %%f
    :: web/frontend/ 开头的是界面
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    :: 其他代码文件是引擎 (排除scripts/ .bat .md .json .lpk等非代码文件)
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
    :: lzc-manifest.yml 改了只需要重新打包
)
for /f "delims=" %%f in ('git diff --cached --name-only 2^>nul') do (
    echo     %%f
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
)

:: 也检查未跟踪的新文件
for /f "delims=" %%f in ('git ls-files --others --exclude-standard 2^>nul') do (
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
)

echo.
if "!NEED_FRONTEND!"=="1" if "!NEED_BACKEND!"=="1" (
    echo [结果] 界面和引擎都有改动, 全部重建
    goto DO_ALL
)
if "!NEED_FRONTEND!"=="1" (
    echo [结果] 只有界面改动, 跳过引擎构建 (快)
    goto DO_FRONTEND_ONLY
)
if "!NEED_BACKEND!"=="1" (
    echo [结果] 只有引擎改动, 跳过界面构建
    goto DO_BACKEND_ONLY
)

echo [结果] 没检测到代码改动, 直接重新打包安装
goto DO_PACK

:: ============================================================
:DO_ALL
echo.
echo [1/5] 构建界面...
docker build -t %FRONTEND_IMAGE% web/frontend/
if %errorlevel% neq 0 (
    echo [失败] 界面构建出错
    goto END
)
echo [2/5] 构建引擎...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if %errorlevel% neq 0 (
    echo [失败] 引擎构建出错
    goto END
)
echo.
echo [3/5] 上传界面...
docker push %FRONTEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选3登录仓库
    goto END
)
echo [4/5] 上传引擎...
docker push %BACKEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选3登录仓库
    goto END
)
goto DO_PACK

:: ============================================================
:DO_FRONTEND_ONLY
echo.
echo [1/3] 构建界面...
docker build -t %FRONTEND_IMAGE% web/frontend/
if %errorlevel% neq 0 (
    echo [失败] 界面构建出错
    goto END
)
echo [2/3] 上传界面...
docker push %FRONTEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选3登录仓库
    goto END
)
goto DO_PACK

:: ============================================================
:DO_BACKEND_ONLY
echo.
echo [1/3] 构建引擎...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if %errorlevel% neq 0 (
    echo [失败] 引擎构建出错
    goto END
)
echo [2/3] 上传引擎...
docker push %BACKEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选3登录仓库
    goto END
)
goto DO_PACK

:: ============================================================
:DO_PACK
echo.
echo [打包] 版本号+1...
for /f "tokens=2 delims=: " %%a in ('findstr /r "^version:" lzc-manifest.yml') do set "VER=%%a"
set "VER=%VER:"=%"
for /f "tokens=1,2,3 delims=." %%a in ("%VER%") do (
    set /a "P=%%c+1"
    set "NEW=%%a.%%b.!P!"
)
echo       %VER% -- !NEW!
powershell -Command "(Get-Content 'lzc-manifest.yml') -replace 'version: %VER%', 'version: !NEW!' | Set-Content 'lzc-manifest.yml'"

echo [打包] 生成lpk...
lzc-cli project build .
if %errorlevel% neq 0 (
    echo [失败] 打包出错
    goto END
)

echo [安装] 部署到懒猫微服...
lzc-cli app install cloud.lazycat.app.autoquota-v!NEW!.lpk
if %errorlevel% neq 0 (
    echo [失败] 安装出错
    goto END
)

echo.
echo  ========================================
echo   搞定! v!NEW! 已部署
echo   https://autoquota.microfeicat2025.heiyu.space
echo  ========================================

:END
echo.
pause
