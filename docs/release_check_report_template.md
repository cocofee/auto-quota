# 发布检查报告模板

- 检查日期:
- 检查人:
- 分支:
- 目标版本:

## 1. 一键体检命令

```bat
scripts\release_check.bat ci search all --no-pause
```

## 2. 结果摘要

- 结论: `PASS / FAIL`
- 系统体检模式: `ci / full`
- 基准模式: `search / agent`
- 数据集: `all / 指定名称`

## 3. 关键产物路径

- Health JSON:
- Health Markdown:
- Benchmark 对比输出摘要:

## 4. 指标门槛核对

- 绿率未低于基线容忍阈值
- 红率未高于基线容忍阈值
- 可运行数据集无执行失败
- Jarvis 入口冒烟检查通过

## 5. 异常与处理

- 异常1:
- 原因:
- 处理建议:

## 6. 给 Claude 的修复指令（可直接粘贴）

```text
请根据本次发布检查失败项进行修复，要求：
1) 先修复导致失败的必过项，再修复性能/质量退化项；
2) 每个修复项提供: 问题定位(文件:行号)、修复说明、回归测试结果；
3) 修复后重新执行:
   scripts\release_check.bat ci search all --no-pause
4) 输出最终结论: PASS / FAIL。
```
