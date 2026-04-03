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

rem  Full Build: 先递增版本号，再构建镜像，这样镜像里的代码版本是新的

rem ============================================================



:DO_ALL

call :BUMP_VERSION

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

    echo [WARN] Frontend push failed, try option 5 to login

    echo [WARN] Continue to pack anyway...

)

docker tag !FRONTEND_IMAGE_VERSIONED! %FRONTEND_IMAGE_LATEST%
docker push %FRONTEND_IMAGE_LATEST%

echo [4/4] Push backend...

docker push !BACKEND_IMAGE_VERSIONED!

if !errorlevel! neq 0 (

    echo [WARN] Backend push failed, try option 5 to login

    echo [WARN] Continue to pack anyway...

)

docker tag !BACKEND_IMAGE_VERSIONED! %BACKEND_IMAGE_LATEST%
docker push %BACKEND_IMAGE_LATEST%

goto DO_PACK



:DO_FRONTEND_ONLY

call :BUMP_VERSION

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

    echo [WARN] Push failed, try option 5 to login

    echo [WARN] Continue to pack anyway...

)

docker tag !FRONTEND_IMAGE_VERSIONED! %FRONTEND_IMAGE_LATEST%
docker push %FRONTEND_IMAGE_LATEST%

goto DO_PACK



:DO_BACKEND_ONLY

call :BUMP_VERSION

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

    echo [WARN] Push failed, try option 5 to login

    echo [WARN] Continue to pack anyway...

)

docker tag !BACKEND_IMAGE_VERSIONED! %BACKEND_IMAGE_LATEST%
docker push %BACKEND_IMAGE_LATEST%

goto DO_PACK



rem ============================================================

rem  BUMP_VERSION: 递增版本号 + 更新 changelog（子程序）

rem  Quick deploy 在 DO_PACK 里调用，Full build 在构建前调用

rem ============================================================



:BUMP_VERSION

echo.

echo [VER] Reading current version...

call :READ_CURRENT_VERSION

echo       Current: %VER%



for /f "tokens=1,2,3 delims=." %%a in ("%VER%") do (

    set /a "P=%%c+1"

    set "NEW=%%a.%%b.!P!"

)

echo       New:     !NEW!

call :SET_IMAGE_TAGS !NEW!



echo [VER] Updating manifest...

powershell -Command "$c=[System.IO.File]::ReadAllText('lzc-manifest.yml'); $c=$c -replace 'version: %VER%','version: !NEW!'; $c=[regex]::Replace($c,'(auto-quota-frontend:)[^""\r\n]+','$1!NEW!'); $c=[regex]::Replace($c,'(auto-quota-app:)[^""\r\n]+','$1!NEW!'); [System.IO.File]::WriteAllText('lzc-manifest.yml',$c)"



echo [VER] Updating changelog...

python tools\bump_changelog.py !NEW!

exit /b 0



rem ============================================================

rem  DO_PACK: 打包 LPK + 安装到懒猫 + git commit

rem ============================================================



:DO_PACK

rem Quick deploy 走这里时还没改版本，先改

if not defined NEW (

    call :READ_CURRENT_VERSION

    call :SET_IMAGE_TAGS !VER!

    call :ENSURE_MANIFEST_IMAGE_TAGS_MATCH_VERSION

    if !errorlevel! neq 0 goto END

    call :ENSURE_QUICK_PACK_SAFE

    if !errorlevel! neq 0 goto END

    echo [PACK] Quick deploy will reuse current manifest version/image tags: !VER!

)

set "PACK_VERSION=!NEW!"

if not defined PACK_VERSION set "PACK_VERSION=!VER!"

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

echo.
echo [LOCAL] Syncing local Docker...
rem 把刚构建的ACR镜像标记为本地docker-compose用的名字
docker tag !BACKEND_IMAGE_VERSIONED! auto-quota-app:latest 2>nul
rem 重建前端 + 重启所有容器
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

    git add lzc-manifest.yml web/frontend/src/constants/changelog.ts scripts/

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



:ENSURE_MANIFEST_IMAGE_TAGS_MATCH_VERSION

set "FRONTEND_IMAGE_TAG="
set "BACKEND_IMAGE_TAG="

for /f "tokens=2 delims=:" %%a in ('findstr /c:"auto-quota-frontend:" lzc-manifest.yml') do (
    set "FRONTEND_IMAGE_TAG=%%a"
)

for /f "tokens=2 delims=:" %%a in ('findstr /c:"auto-quota-app:" lzc-manifest.yml') do (
    if not defined BACKEND_IMAGE_TAG set "BACKEND_IMAGE_TAG=%%a"
)

set "FRONTEND_IMAGE_TAG=!FRONTEND_IMAGE_TAG: =!"
set "BACKEND_IMAGE_TAG=!BACKEND_IMAGE_TAG: =!"

if /i not "!FRONTEND_IMAGE_TAG!"=="!VER!" (
    echo.
    echo [BLOCK] Manifest version is !VER!, but frontend image tag is !FRONTEND_IMAGE_TAG!.
    echo         Rebuild and push the frontend image first, or align lzc-manifest.yml before packing.
    exit /b 1
)

if /i not "!BACKEND_IMAGE_TAG!"=="!VER!" (
    echo.
    echo [BLOCK] Manifest version is !VER!, but backend image tag is !BACKEND_IMAGE_TAG!.
    echo         Rebuild and push the backend image first, or align lzc-manifest.yml before packing.
    exit /b 1
)

echo [CHECK] Manifest image tags match current version: !VER!

exit /b 0



:END

echo.

pause

