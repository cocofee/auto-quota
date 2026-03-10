@echo off
chcp 65001 >nul
title Qwen3-Embedding v3 训练中...
cd /d "%~dp0"

echo ============================================
echo   Qwen3-Embedding v3 训练
echo   数据: 417,633条 (采样20万)
echo   预计时间: 4-5小时 (RTX 4070)
echo ============================================
echo.

python tools/qwen3_finetune.py ^
  --input data/qwen3_training_triplets_v3.jsonl ^
  --output models/qwen3-embedding-quota-v3 ^
  --epochs 1 ^
  --lr 1e-4 ^
  --max-samples 200000 ^
  --batch-size 16 ^
  --grad-accum 2

echo.
echo ============================================
if %ERRORLEVEL% EQU 0 (
    echo   训练完成！模型在 models/qwen3-embedding-quota-v3
) else (
    echo   训练出错，错误码: %ERRORLEVEL%
)
echo ============================================
echo.
pause
