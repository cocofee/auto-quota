@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Step 1: call lzc-cli project build ===
call lzc-cli project build .
echo === Step 2: errorlevel after build = !errorlevel! ===

set "LPK_FILE=cloud.lazycat.app.autoquota-v0.1.31.lpk"
echo === Step 3: checking !LPK_FILE! ===
if not exist "!LPK_FILE!" (
    echo [FAIL] not found
    goto END
)
echo === Step 4: file found, about to install ===

echo === Step 5: calling install ===
call lzc-cli app install "!LPK_FILE!"
echo === Step 6: errorlevel after install = !errorlevel! ===

:END
echo === DONE ===
pause
