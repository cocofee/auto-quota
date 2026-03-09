@echo off
chcp 65001 >nul
title Qwen3-Embedding 第二轮训练（分层采样）
echo ============================================
echo   Qwen3-Embedding 第二轮微调训练
echo   15万分层采样 + 降低学习率
echo   预计 4-5 小时
echo ============================================
echo.

cd /d C:\Users\Administrator\Documents\trae_projects\auto-quota

python -u tools/qwen3_finetune.py --epochs 1 --batch-size 16 --grad-accum 2 --lr 1e-4 --max-samples 150000 --output models/qwen3-embedding-quota-v2

echo.
echo ============================================
echo   训练完成！按任意键关闭窗口
echo ============================================
pause
