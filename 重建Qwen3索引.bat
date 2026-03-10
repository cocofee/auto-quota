@echo off
chcp 65001 >nul 2>&1
echo ============================================================
echo   Qwen3 向量索引重建（Phase 5）
echo   蓝绿策略：旧BGE索引不动，新索引写入 db/chroma/qwen3/
echo ============================================================
echo.
echo 预计耗时：50-60分钟（149万定额+10万经验+4万知识库）
echo 请确保GPU可用（RTX 4070）
echo.
pause

cd /d "%~dp0"
set VECTOR_MODEL_KEY=qwen3
python tools/rebuild_index_qwen3.py %*

echo.
echo 重建完成！
echo 切换到Qwen3：在 .env 中设置 VECTOR_MODEL_KEY=qwen3
echo 回退到BGE：在 .env 中设置 VECTOR_MODEL_KEY=bge
pause
