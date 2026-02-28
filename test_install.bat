@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Test install ===
echo LPK: cloud.lazycat.app.autoquota-v0.1.36.lpk
echo SSH: %USERPROFILE%\.ssh\id_ed25519
echo.

echo [Method 1] cmd /c ...
cmd /c lzc-cli app install "cloud.lazycat.app.autoquota-v0.1.36.lpk" --ssh-key "%USERPROFILE%\.ssh\id_ed25519"
echo Exit code: !errorlevel!
echo.

echo === Done ===
pause
