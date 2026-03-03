@echo off
chcp 65001 >nul
title Jarvis 自动提取

echo ============================================================
echo   Jarvis 自动提取（增量提取 + 增量扫描）
echo   %date% %time%
echo ============================================================

cd /d C:\Users\Administrator\Documents\trae_projects\auto-quota

echo.
echo [第1步] 增量提取：从微信/企业微信提取新文件...
python tools/collect_wechat_files.py
if errorlevel 1 (
    echo [错误] 增量提取失败！
    goto :end
)

echo.
echo [第2步] 增量扫描：将新文件登记到数据库...
python tools/batch_scanner.py "F:/jarvis"
if errorlevel 1 (
    echo [错误] 增量扫描失败！
    goto :end
)

echo.
echo ============================================================
echo   全部完成！
echo ============================================================

:end
echo.
echo 按任意键关闭...
pause >nul
