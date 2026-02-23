# 批次 P2-2 Findings（system_health.bat 收尾分支可维护性）

## 问题清单

### [P2] FINISH 分支使用 if-else if 链，可维护性差

- **位置**: `scripts/system_health.bat:114-127`
- **严重程度**: P2（可维护性）
- **原因**:
  cmd.exe 的 `if-else if-else` 链语法脆弱，括号嵌套容易出错。
  三路分支（pass / review skipped / fail）混在同一个括号块中，
  后续修改（如新增退出码、调整提示文案）容易引入"提示与退出码不一致"的 bug。

- **影响范围**: 仅影响脚本的用户提示输出和退出码映射
- **复现**: 直接阅读代码即可观察到问题
