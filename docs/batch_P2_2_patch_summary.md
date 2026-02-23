# 批次 P2-2 Patch Summary（system_health.bat 收尾分支重构）

## 改动点

| 文件 | 行号 | 改动 | 影响面 |
|------|------|------|--------|
| `scripts/system_health.bat` | L112-130 | if-else if 链 → goto 标签分支 | 仅脚本输出提示 |

## 具体改动

### scripts/system_health.bat L112-130

```batch
# 修复前（if-else if 链）：
:FINISH
if "%RC%"=="0" (
  echo   Checks passed
) else if "%RC%"=="2" (
  echo   Checks passed (review skipped: codex unavailable)
  set "RC=0"
) else (
  echo   Checks failed, exit code %RC%
)

# 修复后（goto 标签分支，每个分支独立）：
:FINISH
if "%RC%"=="0" goto FINISH_PASS
if "%RC%"=="2" goto FINISH_REVIEW_SKIP
goto FINISH_FAIL

:FINISH_PASS
echo   Checks passed
goto FINISH_END

:FINISH_REVIEW_SKIP
echo   Checks passed (review skipped: codex unavailable)
set "RC=0"
goto FINISH_END

:FINISH_FAIL
echo   Checks failed, exit code %RC%
goto FINISH_END

:FINISH_END
```

**优势**：
- 每个退出码对应一个独立标签，添加新退出码只需加一个 if + 一个标签
- 不依赖 cmd.exe 的括号嵌套语法，避免意外截断
- 分支语义清晰：PASS / REVIEW_SKIP / FAIL / END

## 回滚方式

单文件回滚：`git checkout scripts/system_health.bat`

## 未改动项

- 功能逻辑不变：quick/full/ci/review/all 五种模式的执行流程不变
- 退出码语义不变：0=通过，2=review跳过（降级为0），其他=失败
- PRECHECK_REVIEW 子程序不变
