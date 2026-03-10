@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set VECTOR_MODEL_KEY=qwen3

echo ============================================================
echo   Qwen3-v3 全流程升级（无人值守，直接去睡觉）
echo ============================================================
echo.
echo   第1步：重建向量索引（~50分钟）
echo   第2步：生成LTR训练数据（~30分钟）
echo   第3步：训练LTR排序模型（~2分钟）
echo   第4步：跑Benchmark看新基线（~10分钟）
echo.
echo   总计预估：~90分钟
echo   开始时间：%date% %time%
echo ============================================================
echo.

REM === 第1步：重建向量索引 ===
echo [1/4] 重建向量索引（149万定额+10万经验+4万知识库）...
echo 开始时间：%time%
python tools/rebuild_index_qwen3.py
if %errorlevel% neq 0 (
    echo [错误] 索引重建失败！错误码：%errorlevel%
    echo 失败时间：%time%
    goto :done
)
echo [1/4] 索引重建完成！时间：%time%
echo.

REM === 第2步：生成LTR训练数据 ===
echo [2/4] 生成LTR训练数据（2174条试卷 x 20候选 x 21维特征）...
echo 开始时间：%time%
python tools/ltr_prepare_data.py
if %errorlevel% neq 0 (
    echo [错误] LTR数据生成失败！错误码：%errorlevel%
    echo 失败时间：%time%
    goto :done
)
echo [2/4] LTR数据生成完成！时间：%time%
echo.

REM === 第3步：训练LTR排序模型 ===
echo [3/4] 训练LTR排序模型（LightGBM LambdaRank）...
echo 开始时间：%time%
python tools/ltr_train.py --no-cv
if %errorlevel% neq 0 (
    echo [错误] LTR训练失败！错误码：%errorlevel%
    echo 失败时间：%time%
    goto :done
)
echo [3/4] LTR训练完成！时间：%time%
echo.

REM === 第4步：跑Benchmark ===
echo [4/4] 跑Benchmark（11省2174条）...
echo 开始时间：%time%
python tools/run_benchmark.py
if %errorlevel% neq 0 (
    echo [错误] Benchmark失败！错误码：%errorlevel%
    echo 失败时间：%time%
    goto :done
)
echo [4/4] Benchmark完成！时间：%time%
echo.

echo ============================================================
echo   全部完成！结束时间：%date% %time%
echo ============================================================
echo.
echo   醒来后看上面的Benchmark结果就行。
echo   如果要回退：改 .env 里 VECTOR_MODEL_KEY=bge
echo.

:done
echo.
echo 按任意键关闭窗口...
pause >nul
