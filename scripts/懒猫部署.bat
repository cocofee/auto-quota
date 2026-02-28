@echo off
setlocal enabledelayedexpansion
title LazyCat Deploy

set "PROJECT_DIR=%~dp0.."
set "ACR_REGISTRY=crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com"
set "ACR_NAMESPACE=cocofee2026"
set "ACR_USER=nick1293622534"
set "FRONTEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-frontend:latest"
set "BACKEND_IMAGE=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-app:latest"
set "SSH_CMD=ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q -p 22222 box@fc03:1136:3825:2790:9282:59f7:ba36:403"
set "CONTAINER=cloudlazycatappautoquota-backend-1"
set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"

cd /d "%PROJECT_DIR%"

echo.
echo  ========================================
echo       LazyCat Deploy Tool
echo  ========================================
echo.
echo  [1] Quick deploy - pack + install only
echo  [2] Full build - docker build + push + pack + install
echo  [3] Sync data - quota/experience/knowledge
echo  [4] View logs
echo  [5] Docker login
echo.
set /p choice="Select (1-5): "

if "%choice%"=="1" goto DO_PACK
if "%choice%"=="2" goto FULL_BUILD
if "%choice%"=="3" goto SYNC_DATA
if "%choice%"=="4" goto LOGS
if "%choice%"=="5" goto LOGIN
echo Invalid option
goto END

:LOGIN
echo.
echo Username: COCOfee2012
echo.
docker login --username=%ACR_USER% %ACR_REGISTRY%
goto END

:SYNC_DATA
echo.
echo  Sync what to LazyCat?
echo.
echo  [1] All - quota+experience+knowledge ~1.5GB
echo  [2] Quota db - data/ + db/provinces/
echo  [3] Experience db - db/common/
echo  [4] Knowledge rules - knowledge/
echo.
set /p syncchoice="Select (1-4): "

if "%syncchoice%"=="1" (
    call :DO_SYNC data "quota-raw"
    call :DO_SYNC db "quota-db+experience"
    call :DO_SYNC knowledge "knowledge-rules"
    goto SYNC_DONE
)
if "%syncchoice%"=="2" (
    call :DO_SYNC data "quota-raw"
    call :DO_SYNC db\provinces "quota-db"
    goto SYNC_DONE
)
if "%syncchoice%"=="3" (
    call :DO_SYNC db\common "experience-db"
    goto SYNC_DONE
)
if "%syncchoice%"=="4" (
    call :DO_SYNC knowledge "knowledge-rules"
    goto SYNC_DONE
)
echo Invalid option
goto END

:DO_SYNC
echo.
echo [SYNC] %~2 - %~1/ ...
cd /d "%PROJECT_DIR%"
set "SYNCDIR=%~1"
set "SYNCDIR=!SYNCDIR:\=/!"
>"%TEMP%\lzc_sync.sh" echo tar cf - '!SYNCDIR!/' ^| !SSH_CMD! lzc-docker cp - !CONTAINER!:/app/
"%GIT_BASH%" "%TEMP%\lzc_sync.sh"
if !errorlevel! neq 0 (
    echo [FAIL] %~2 sync failed
    exit /b 1
)
echo [OK] %~2 synced
exit /b 0

:SYNC_DONE
echo.
echo  ========================================
echo   Sync done! Refresh browser to see data
echo  ========================================
goto END

:LOGS
echo.
echo [LOG] Ctrl+C to quit
echo.
%SSH_CMD% "lzc-docker logs -f --tail 50 cloudlazycatappautoquota-celery-worker-1"
goto END

:FULL_BUILD
echo.
echo [BUILD] What to build?
echo.
echo  [1] Frontend + Backend
echo  [2] Frontend only
echo  [3] Backend only
echo.
set /p buildchoice="Select (1-3): "

if "%buildchoice%"=="1" goto DO_ALL
if "%buildchoice%"=="2" goto DO_FRONTEND_ONLY
if "%buildchoice%"=="3" goto DO_BACKEND_ONLY
echo Invalid option
goto END

:DO_ALL
echo.
echo [1/4] Build frontend...
docker build -t %FRONTEND_IMAGE% web/frontend/
if !errorlevel! neq 0 (
    echo [FAIL] Frontend build error
    goto END
)
echo [2/4] Build backend...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if !errorlevel! neq 0 (
    echo [FAIL] Backend build error
    goto END
)
echo [3/4] Push frontend...
docker push %FRONTEND_IMAGE%
if !errorlevel! neq 0 (
    echo [FAIL] Push failed, try option 5 to login
    goto END
)
echo [4/4] Push backend...
docker push %BACKEND_IMAGE%
if !errorlevel! neq 0 (
    echo [FAIL] Push failed, try option 5 to login
    goto END
)
goto DO_PACK

:DO_FRONTEND_ONLY
echo.
echo [1/2] Build frontend...
docker build -t %FRONTEND_IMAGE% web/frontend/
if !errorlevel! neq 0 (
    echo [FAIL] Frontend build error
    goto END
)
echo [2/2] Push frontend...
docker push %FRONTEND_IMAGE%
if !errorlevel! neq 0 (
    echo [FAIL] Push failed, try option 5 to login
    goto END
)
goto DO_PACK

:DO_BACKEND_ONLY
echo.
echo [1/2] Build backend...
docker build -f web/backend/Dockerfile -t %BACKEND_IMAGE% .
if !errorlevel! neq 0 (
    echo [FAIL] Backend build error
    goto END
)
echo [2/2] Push backend...
docker push %BACKEND_IMAGE%
if !errorlevel! neq 0 (
    echo [FAIL] Push failed, try option 5 to login
    goto END
)
goto DO_PACK

:DO_PACK
echo.
echo [PACK] Reading current version...
for /f "tokens=2 delims=: " %%a in ('findstr /r "^version:" lzc-manifest.yml') do set "VER=%%a"
set "VER=%VER:"=%"
echo        Current: %VER%

for /f "tokens=1,2,3 delims=." %%a in ("%VER%") do (
    set /a "P=%%c+1"
    set "NEW=%%a.%%b.!P!"
)
echo        New:     !NEW!

echo [PACK] Updating manifest version...
powershell -Command "$c=[System.IO.File]::ReadAllText('lzc-manifest.yml'); $c=$c -replace 'version: %VER%','version: !NEW!'; [System.IO.File]::WriteAllText('lzc-manifest.yml',$c)"

echo [PACK] Building LPK...
lzc-cli project build .

set "LPK_FILE=cloud.lazycat.app.autoquota-v!NEW!.lpk"
if not exist "!LPK_FILE!" (
    echo [FAIL] LPK not found: !LPK_FILE!
    goto END
)
echo [OK] LPK ready: !LPK_FILE!

echo [INSTALL] Deploying to LazyCat...
lzc-cli app install "!LPK_FILE!"
if !errorlevel! neq 0 (
    echo [FAIL] Install failed
    echo [TIP] Manual: lzc-cli app install !LPK_FILE!
    goto END
)

echo.
echo  ========================================
echo   Done! v!NEW! deployed
echo   https://autoquota.microfeicat2025.heiyu.space
echo  ========================================

echo.
echo [GIT] Auto commit...
git add -A
git commit -m "deploy: v!NEW!"
if !errorlevel! equ 0 (
    echo [OK] Committed deploy: v!NEW!
) else (
    echo [INFO] Nothing to commit
)

:END
echo.
pause
