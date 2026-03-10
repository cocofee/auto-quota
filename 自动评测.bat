@echo off
chcp 65001 >nul
title Qwen3-v2 训练后自动评测

echo ============================================
echo   等待v2训练完成后自动运行评测
echo ============================================
echo.

cd /d C:\Users\Administrator\Documents\trae_projects\auto-quota

:wait_loop
if exist "models\qwen3-embedding-quota-v2\config.json" (
    echo [%time%] 检测到v2模型已保存，开始评测...
    goto run_eval
)
echo [%time%] 训练中，等待60秒后再检查...
timeout /t 60 /nobreak >nul
goto wait_loop

:run_eval
echo.
echo ============================================
echo   开始评测 BGE vs Qwen3-v2
echo ============================================
echo.

python -u tools/qwen3_eval.py --qwen3-model models/qwen3-embedding-quota-v2 > output\temp\qwen3_v2_eval_result.txt 2>&1

echo.
echo ============================================
echo   评测完成！结果已保存到:
echo   output\temp\qwen3_v2_eval_result.txt
echo ============================================
echo.
type output\temp\qwen3_v2_eval_result.txt
echo.
pause
