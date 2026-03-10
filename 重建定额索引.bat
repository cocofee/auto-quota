@echo off
chcp 65001 >nul 2>&1
cd /d C:\Users\Administrator\Documents\trae_projects\auto-quota
set VECTOR_MODEL_KEY=qwen3
echo ============================================================
echo   正在重建定额索引（149万条，约2小时）
echo   索引输出: db\chroma\qwen3\
echo ============================================================
echo.
python tools/rebuild_index_qwen3.py --quota-only
echo.
echo 完成！按任意键关闭...
pause >nul
