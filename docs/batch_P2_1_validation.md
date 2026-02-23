# 批次 P2-1 Validation（灯具硬映射偏移修复）

## 执行命令与结果

```
python tools/system_health_check.py --mode quick
→ 3/3 PASS (syntax, import smoke, regression)

python -m pytest tests/test_query_builder_lamp_rules.py -v
→ 28 passed（原 16 + 新增 12）

python -m pytest tests/ -v
→ 67 passed（全量回归无退化）
```

## 新增测试覆盖

| 测试类 | 测试数 | 覆盖场景 |
|--------|--------|---------|
| TestTubeLampMapping | 4 | 直管灯/灯管 不再映射到 LED灯带；线槽灯保持映射 |
| TestLongTailLamps | 8 | 筒灯、地脚灯、庭院灯、洗墙灯、轨道灯、防爆灯、井道灯 |

## 关键验证点

修复前 3 个测试失败：
- `直管灯` → `"LED灯带 灯管式"` (错误)
- `灯管` → `"LED灯带 灯管式"` (错误)
- `LED直管灯` → `"LED灯带 灯管式"` (错误)

修复后全部正确：
- `直管灯` → `"荧光灯具安装 单管"` (正确)
- `灯管` → `"荧光灯具安装 单管"` (正确)
- `LED直管灯` → `"荧光灯具安装 单管"` (正确)
- `线槽灯` → `"LED灯带 灯管式"` (保持不变)

## 未覆盖风险

- 其他特定灯具子类型（如"格栅灯"、"平板灯"）未添加专门映射，走通用兜底路径
- 实际搜索效果需在真实定额库中验证
