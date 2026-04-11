@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================================
echo   AutoQuota 本地懒猫同构模式
echo ============================================================
echo 这会启动：
echo   frontend(静态构建/nginx)
echo   backend
echo   celery-worker
echo   match-service:9300
echo   postgres
echo   redis
echo.

docker compose -f docker-compose.lazycat-local.yml up -d --build

echo.
echo 已启动本地懒猫同构模式。
echo 前端:  http://127.0.0.1:3210
echo 匹配:  http://127.0.0.1:9300/health
echo 日志:  本地任务日志.bat all
echo.

endlocal
