@echo off
setlocal enabledelayedexpansion
title 懒猫微服部署

set "PROJECT_DIR=%~dp0.."
set "ACR_REGISTRY=crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com"
set "ACR_NAMESPACE=cocofee2026"
set "ACR_USER=nick1293622534"
set "FRONTEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-frontend:latest"
set "BACKEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-app:latest"
set "SSH_CMD=ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q -p 22222 box@fc03:1136:3825:2790:9282:59f7:ba36:403"
set "CONTAINER=cloudlazycatappautoquota-backend-1"
set "GIT_TAR=C:\Program Files\Git\usr\bin\tar.exe"
set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"

cd /d "%PROJECT_DIR%"

echo.
echo  ========================================
echo       懒猫微服 部署工具
echo  ========================================
echo.
echo  [1] 一键部署 (自动判断更新什么)
echo  [2] 同步数据 (定额库/经验库/知识库到懒猫)
echo  [3] 查看日志
echo  [4] 登录仓库 (提示登录失败时用)
echo.
set /p choice="请输入选项(1-4): "

if "%choice%"=="1" goto UPDATE
if "%choice%"=="2" goto SYNC_DATA
if "%choice%"=="3" goto LOGS
if "%choice%"=="4" goto LOGIN
echo 无效选项
goto END

:LOGIN
echo.
echo 提示: 密码是 COCOfee2012
echo.
docker login --username=%ACR_USER% %ACR_REGISTRY%
goto END

:SYNC_DATA
echo.
echo  同步什么数据到懒猫微服?
echo.
echo  [1] 全部同步 (定额库+经验库+知识库, 约1.5GB)
echo  [2] 只同步定额库 (data/原始数据 + db/provinces/编译库)
echo  [3] 只同步经验库 (db/common/)
echo  [4] 只同步知识库规则 (knowledge/)
echo.
set /p syncchoice="请输入选项(1-4): "

if "%syncchoice%"=="1" (
    call :DO_SYNC data "定额原始数据"
    call :DO_SYNC db "定额数据库+经验库"
    call :DO_SYNC knowledge "知识库规则"
    goto SYNC_DONE
)
if "%syncchoice%"=="2" (
    call :DO_SYNC data "定额原始数据"
    call :DO_SYNC db\provinces "定额数据库"
    goto SYNC_DONE
)
if "%syncchoice%"=="3" (
    call :DO_SYNC db\common "经验库"
    goto SYNC_DONE
)
if "%syncchoice%"=="4" (
    call :DO_SYNC knowledge "知识库规则"
    goto SYNC_DONE
)
echo 无效选项
goto END

:DO_SYNC
echo.
echo [同步] %~2 (%~1/) ...
cd /d "%PROJECT_DIR%"
set "SYNCDIR=%~1"
set "SYNCDIR=!SYNCDIR:\=/!"
>"%TEMP%\lzc_sync.sh" echo tar cf - '!SYNCDIR!/' ^| !SSH_CMD! lzc-docker cp - !CONTAINER!:/app/
"%GIT_BASH%" "%TEMP%\lzc_sync.sh"
if !errorlevel! neq 0 (
    echo [失败] %~2同步出错，请检查网络连接
    exit /b 1
)
echo [成功] %~2同步完成
exit /b 0

:SYNC_DONE
echo.
echo  ========================================
echo   数据同步完成! 刷新网页即可看到最新数据
echo  ========================================
goto END

:LOGS
echo.
echo [日志] Ctrl+C 退出
echo.
%SSH_CMD% "lzc-docker logs -f --tail 50 cloudlazycatappautoquota-celery-worker-1"
goto END

:UPDATE
echo.
echo [检测] 正在分析哪些文件有改动...

set "NEED_FRONTEND=0"
set "NEED_BACKEND=0"

for /f "delims=" %%f in ('git diff --name-only 2^>nul') do (
    echo     %%f
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
)
for /f "delims=" %%f in ('git diff --cached --name-only 2^>nul') do (
    echo     %%f
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
)
for /f "delims=" %%f in ('git ls-files --others --exclude-standard 2^>nul') do (
    echo "%%f" | findstr /i "web/frontend/" >nul && set "NEED_FRONTEND=1"
    echo "%%f" | findstr /i "\.py \.txt web/backend/" >nul && set "NEED_BACKEND=1"
)

echo.
if "!NEED_FRONTEND!"=="1" if "!NEED_BACKEND!"=="1" (
    echo [检测] 前端和后端都有改动, 全部重建
    goto DO_ALL
)
if "!NEED_FRONTEND!"=="1" (
    echo [检测] 只有前端改动, 只重建前端 (快)
    goto DO_FRONTEND_ONLY
)
if "!NEED_BACKEND!"=="1" (
    echo [检测] 只有后端改动, 只重建后端
    goto DO_BACKEND_ONLY
)

echo [检测] 没有检测到代码改动, 直接更新版本安装
goto DO_PACK

:DO_ALL
echo.
echo [1/5] 构建前端...
docker build -t %FRONTEND_IMAGE% web/frontend/
if %errorlevel% neq 0 (
    echo [失败] 界面构建出错
    goto END
)
echo [2/5] 构建后端...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if %errorlevel% neq 0 (
    echo [失败] 后端构建出错
    goto END
)
echo.
echo [3/5] 上传前端...
docker push %FRONTEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选4登录仓库
    goto END
)
echo [4/5] 上传后端...
docker push %BACKEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选4登录仓库
    goto END
)
goto DO_PACK

:DO_FRONTEND_ONLY
echo.
echo [1/3] 构建前端...
docker build -t %FRONTEND_IMAGE% web/frontend/
if %errorlevel% neq 0 (
    echo [失败] 界面构建出错
    goto END
)
echo [2/3] 上传前端...
docker push %FRONTEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选4登录仓库
    goto END
)
goto DO_PACK

:DO_BACKEND_ONLY
echo.
echo [1/3] 构建后端...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if %errorlevel% neq 0 (
    echo [失败] 后端构建出错
    goto END
)
echo [2/3] 上传后端...
docker push %BACKEND_IMAGE%
if %errorlevel% neq 0 (
    echo [失败] 上传失败,请先选4登录仓库
    goto END
)
goto DO_PACK

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
powershell -Command "$c=[System.IO.File]::ReadAllText('lzc-manifest.yml'); $c=$c -replace 'version: %VER%','version: !NEW!'; [System.IO.File]::WriteAllText('lzc-manifest.yml',$c)"

echo [打包] 生成lpk...
lzc-cli project build .
if !errorlevel! neq 0 (
    echo [失败] 打包出错，正在回滚版本号...
    powershell -Command "$c=[System.IO.File]::ReadAllText('lzc-manifest.yml'); $c=$c -replace 'version: !NEW!','version: %VER%'; [System.IO.File]::WriteAllText('lzc-manifest.yml',$c)"
    goto END
)

echo [安装] 部署到懒猫微服...
lzc-cli app install cloud.lazycat.app.autoquota-v!NEW!.lpk
if !errorlevel! neq 0 (
    echo [失败] 安装出错 (lpk已生成: cloud.lazycat.app.autoquota-v!NEW!.lpk)
    echo [提示] 可手动执行: lzc-cli app install cloud.lazycat.app.autoquota-v!NEW!.lpk
    goto END
)

echo.
echo  ========================================
echo   搞定! v!NEW! 已部署
echo   https://autoquota.microfeicat2025.heiyu.space
echo  ========================================

echo.
echo [提交] 自动提交代码变更...
git add -A
git commit -m "deploy: v!NEW!"
if !errorlevel! equ 0 (
    echo [成功] 代码已提交 deploy: v!NEW!
) else (
    echo [跳过] 没有需要提交的变更
)

:END
echo.
pause
