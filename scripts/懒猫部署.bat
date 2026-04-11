@echo off

if /i not "%LAZYCAT_DEPLOY_WINDOW%"=="1" (
    echo %CMDCMDLINE% | findstr /i /c:" /c " >nul
    if not errorlevel 1 (
        set "LAZYCAT_DEPLOY_WINDOW=1"
        start "LazyCat Deploy" cmd /k ""%~f0" %*"
        exit /b
    )
)

chcp 65001 >nul

setlocal enabledelayedexpansion

title LazyCat Deploy



set "PROJECT_DIR=%~dp0.."

set "ACR_REGISTRY=crpi-w9u53ghdxy8m3wgg.cn-hangzhou.personal.cr.aliyuncs.com"

set "ACR_NAMESPACE=cocofee2026"

set "ACR_USER=nick1293622534"

set "FRONTEND_IMAGE_REPO=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-frontend"

set "BACKEND_IMAGE_REPO=%ACR_REGISTRY%/%ACR_NAMESPACE%/auto-quota-app"

set "FRONTEND_IMAGE_LATEST=%FRONTEND_IMAGE_REPO%:latest"

set "BACKEND_IMAGE_LATEST=%BACKEND_IMAGE_REPO%:latest"

set "SSH_CMD=ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q -p 22222 box@fc03:1136:3825:2790:9282:59f7:ba36:403"

set "CONTAINER=cloudlazycatappautoquota-backend-1"

set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"

set "LZC_CLI=node "%APPDATA%\npm\node_modules\@lazycatcloud\lzc-cli\scripts\cli.js""



cd /d "%PROJECT_DIR%"



echo.

echo  ========================================

echo       LazyCat Deploy Tool

echo  ========================================

echo.

echo  [1] Quick deploy - pack + install only ^(no image rebuild^)

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

echo [RESTART] Restarting celery worker to pick up new data...

%SSH_CMD% "lzc-docker restart cloudlazycatappautoquota-celery-worker-1"

echo [RESTART] Restarting backend...

%SSH_CMD% "lzc-docker restart cloudlazycatappautoquota-backend-1"

echo.

echo  ========================================

echo   Sync done! Services restarted.

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



rem ============================================================
rem Full build flow
rem Previous multilingual comment removed for cmd safety

rem ============================================================



:DO_ALL

call :BUMP_VERSION

if !errorlevel! neq 0 goto END

echo.

echo [1/4] Build frontend...

docker build --build-arg BACKEND_UPSTREAM=backend.cloud.lazycat.app.autoquota.lzcapp:8000 -t !FRONTEND_IMAGE_VERSIONED! web/frontend/

if !errorlevel! neq 0 (

    echo [FAIL] Frontend build error

    goto END

)

echo [2/4] Build backend...

docker build -f web/backend/Dockerfile -t !BACKEND_IMAGE_VERSIONED! .

if !errorlevel! neq 0 (

    echo [FAIL] Backend build error

    goto END

)

echo [3/4] Push frontend...

docker push !FRONTEND_IMAGE_VERSIONED!

if !errorlevel! neq 0 (

    echo [FAIL] Frontend push failed for !FRONTEND_IMAGE_VERSIONED!
    echo [FAIL] Run option 5 to login, then rerun full build.
    goto END

)

docker tag !FRONTEND_IMAGE_VERSIONED! %FRONTEND_IMAGE_LATEST%
docker push %FRONTEND_IMAGE_LATEST%

if !errorlevel! neq 0 (

    echo [FAIL] Frontend latest tag push failed for %FRONTEND_IMAGE_LATEST%
    echo [FAIL] Run option 5 to login, then rerun full build.
    goto END

)

echo [4/4] Push backend...

docker push !BACKEND_IMAGE_VERSIONED!

if !errorlevel! neq 0 (

    echo [FAIL] Backend push failed for !BACKEND_IMAGE_VERSIONED!
    echo [FAIL] Run option 5 to login, then rerun full build.
    goto END

)

docker tag !BACKEND_IMAGE_VERSIONED! %BACKEND_IMAGE_LATEST%
docker push %BACKEND_IMAGE_LATEST%

if !errorlevel! neq 0 (

    echo [FAIL] Backend latest tag push failed for %BACKEND_IMAGE_LATEST%
    echo [FAIL] Run option 5 to login, then rerun full build.
    goto END

)

goto DO_PACK



:DO_FRONTEND_ONLY

call :BUMP_VERSION

if !errorlevel! neq 0 goto END

echo.

echo [1/2] Build frontend...

docker build --build-arg BACKEND_UPSTREAM=backend.cloud.lazycat.app.autoquota.lzcapp:8000 -t !FRONTEND_IMAGE_VERSIONED! web/frontend/

if !errorlevel! neq 0 (

    echo [FAIL] Frontend build error

    goto END

)

echo [2/2] Push frontend...

docker push !FRONTEND_IMAGE_VERSIONED!

if !errorlevel! neq 0 (

    echo [FAIL] Frontend push failed for !FRONTEND_IMAGE_VERSIONED!
    echo [FAIL] Run option 5 to login, then rerun frontend build.
    goto END

)

docker tag !FRONTEND_IMAGE_VERSIONED! %FRONTEND_IMAGE_LATEST%
docker push %FRONTEND_IMAGE_LATEST%

if !errorlevel! neq 0 (

    echo [FAIL] Frontend latest tag push failed for %FRONTEND_IMAGE_LATEST%
    echo [FAIL] Run option 5 to login, then rerun frontend build.
    goto END

)

goto DO_PACK



:DO_BACKEND_ONLY

call :BUMP_VERSION

if !errorlevel! neq 0 goto END

echo.

echo [1/2] Build backend...

docker build -f web/backend/Dockerfile -t !BACKEND_IMAGE_VERSIONED! .

if !errorlevel! neq 0 (

    echo [FAIL] Backend build error

    goto END

)

echo [2/2] Push backend...

docker push !BACKEND_IMAGE_VERSIONED!

if !errorlevel! neq 0 (

    echo [FAIL] Backend push failed for !BACKEND_IMAGE_VERSIONED!
    echo [FAIL] Run option 5 to login, then rerun backend build.
    goto END

)

docker tag !BACKEND_IMAGE_VERSIONED! %BACKEND_IMAGE_LATEST%
docker push %BACKEND_IMAGE_LATEST%

if !errorlevel! neq 0 (

    echo [FAIL] Backend latest tag push failed for %BACKEND_IMAGE_LATEST%
    echo [FAIL] Run option 5 to login, then rerun backend build.
    goto END

)

goto DO_PACK



rem ============================================================
rem Prepare release version metadata
rem Previous multilingual comment removed for cmd safety
rem Full build can retry the current version or bump to the next one
rem Previous multilingual comment removed for cmd safety
rem Manifest and changelog are written only after remote images are available
rem ============================================================

rem ============================================================



:BUMP_VERSION

echo.

echo [VER] Reading current version...

call :READ_CURRENT_VERSION

echo       Current: %VER%

echo.
echo  [1] Retry current version tag ^(recommended if previous push/install failed^)
echo  [2] Bump to next version
echo.
set /p releasemode="Release mode (1-2): "

if "%releasemode%"=="1" (
    set "NEW=%VER%"
    echo       Reuse:   !NEW!
    goto VERSION_READY
)

if "%releasemode%"=="2" (
    for /f "delims=" %%a in ('python tools\release_sync.py next') do set "NEW=%%a"
    echo       New:     !NEW!
    goto VERSION_READY
)

echo [FAIL] Invalid release mode
exit /b 1

:VERSION_READY
set "VERSION_APPLIED="
call :SET_IMAGE_TAGS !NEW!

exit /b 0



rem Build LPK, install to LazyCat, then optionally commit release files
rem ============================================================

:DO_PACK

rem Quick deploy reaches this block without creating NEW first

if not defined NEW (

	call :READ_CURRENT_VERSION

	call :SET_IMAGE_TAGS !VER!

	call :ENSURE_RELEASE_FILES_MATCH_VERSION !VER!

	if !errorlevel! neq 0 goto END

    call :ENSURE_QUICK_PACK_SAFE

    if !errorlevel! neq 0 goto END

    echo [PACK] Quick deploy will reuse current manifest version/image tags: !VER!

)

if defined NEW (

    call :VERIFY_REMOTE_IMAGES_READY

    if !errorlevel! neq 0 goto END

    call :APPLY_VERSION_FILES

    if !errorlevel! neq 0 goto END

)

set "PACK_VERSION=!NEW!"

if not defined PACK_VERSION set "PACK_VERSION=!VER!"

call :ENSURE_RELEASE_FILES_MATCH_VERSION !PACK_VERSION!

if !errorlevel! neq 0 goto END

call :VERIFY_REMOTE_IMAGES_READY

if !errorlevel! neq 0 goto END

echo.

echo [PACK] Building LPK...

%LZC_CLI% project build .



set "LPK_FILE=cloud.lazycat.app.autoquota-v!PACK_VERSION!.lpk"

if not exist "!LPK_FILE!" (

    echo [FAIL] LPK not found: !LPK_FILE!

    goto END

)

echo [OK] LPK ready: !LPK_FILE!



echo.

echo [INSTALL] Deploying to LazyCat...

echo unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy> "%TEMP%\lzc_install.sh"

echo lzc-cli app install '!LPK_FILE!' --ssh-key "$HOME/.ssh/id_ed25519">> "%TEMP%\lzc_install.sh"

"%GIT_BASH%" --login "%TEMP%\lzc_install.sh"

echo [INSTALL] exit code: !errorlevel!

rem Re-tag the pushed backend image for local docker-compose use
echo [LOCAL] Syncing local Docker...
rem Rebuild frontend and restart local containers
docker tag !BACKEND_IMAGE_VERSIONED! auto-quota-app:latest 2>nul
rem Local container restart step
docker-compose up -d --build frontend 2>nul
docker-compose up -d 2>nul
echo [OK] Local Docker synced



echo.

echo  ========================================

echo   Done! v!PACK_VERSION! deployed

echo   https://autoquota.microfeicat2025.heiyu.space

echo  ========================================



echo.

if defined NEW (

    echo [GIT] Auto commit...

    git add lzc-manifest.yml web/frontend/src/constants/changelog.ts

    git commit -m "deploy: v!NEW!"

    if !errorlevel! equ 0 (

        echo [OK] Committed deploy: v!NEW!

    ) else (

        echo [INFO] Nothing to commit

    )

) else (

    echo [GIT] Skip auto commit for pack-only deploy

)



:READ_CURRENT_VERSION

for /f "tokens=2 delims=: " %%a in ('findstr /r "^version:" lzc-manifest.yml') do set "VER=%%a"

set "VER=%VER:"=%"

exit /b 0



:SET_IMAGE_TAGS

set "FRONTEND_IMAGE_VERSIONED=%FRONTEND_IMAGE_REPO%:%~1"

set "BACKEND_IMAGE_VERSIONED=%BACKEND_IMAGE_REPO%:%~1"

exit /b 0



:VERIFY_REMOTE_IMAGES_READY

call :VERIFY_REMOTE_IMAGE "!FRONTEND_IMAGE_VERSIONED!" "frontend"

if !errorlevel! neq 0 exit /b 1

call :VERIFY_REMOTE_IMAGE "!BACKEND_IMAGE_VERSIONED!" "backend"

if !errorlevel! neq 0 exit /b 1

exit /b 0



:VERIFY_REMOTE_IMAGE

set "VERIFY_IMAGE=%~1"
set "VERIFY_NAME=%~2"

echo [CHECK] Verifying remote !VERIFY_NAME! image: !VERIFY_IMAGE!

for /l %%n in (1,1,8) do (
    docker manifest inspect !VERIFY_IMAGE! >nul 2>nul
    if !errorlevel! equ 0 (
        echo [CHECK] Remote !VERIFY_NAME! image is available.
        exit /b 0
    )
    echo [WAIT] Remote !VERIFY_NAME! image not visible yet ^(attempt %%n/8^). Retrying...
    timeout /t 2 >nul
)

echo [FAIL] Remote !VERIFY_NAME! image not found: !VERIFY_IMAGE!
echo [FAIL] Do not install this LPK yet. Push the image successfully first.

exit /b 1



:APPLY_VERSION_FILES

if defined VERSION_APPLIED exit /b 0

echo [VER] Syncing release files to version !NEW!...

python tools\release_sync.py apply !NEW!

if !errorlevel! neq 0 (
    echo [FAIL] Failed to sync release files
    exit /b 1
)

set "VER=!NEW!"
set "VERSION_APPLIED=1"

exit /b 0



:ENSURE_QUICK_PACK_SAFE

set "HAS_CODE_CHANGES="

for /f "delims=" %%i in ('git status --porcelain -- web/frontend web/backend') do set "HAS_CODE_CHANGES=1"

if defined HAS_CODE_CHANGES (

    echo.

    echo [BLOCK] Quick deploy no longer allows frontend/backend code changes without rebuilding images.

    echo         Please use option [2] Full build so the deployed containers match the code.

    exit /b 1

)

echo [CHECK] No frontend/backend code changes detected. Safe to pack current manifest only.

exit /b 0



:ENSURE_RELEASE_FILES_MATCH_VERSION

python tools\release_sync.py validate %~1

if !errorlevel! neq 0 (
    echo [BLOCK] Release files are out of sync for version %~1.
    echo         Align lzc-manifest.yml and changelog.ts before packing.
    exit /b 1
)

echo [CHECK] Release files match version: %~1

exit /b 0



:END

echo.

pause

