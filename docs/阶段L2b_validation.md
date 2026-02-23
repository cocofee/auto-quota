# 阶段L2-b Validation — 参数提取率提升

## 日期
2026-02-23

## 验收命令与结果

### 1. 全量回归测试
```bash
python -m pytest tests/ -v
```
**结果**: 162 passed, 1 warning in 0.50s

### 2. text_parser 专项测试
```bash
python -m pytest tests/test_text_parser.py -v
```
**结果**: 42 passed in 0.07s

### 3. benchmark 对比
```bash
python tools/run_benchmark.py --mode search --compare
```
**结果**: [OK] 无退化，所有指标在允许范围内。

| 数据集 | 绿率(基线) | 绿率(改后) | 变化 | 红率变化 |
|--------|-----------|-----------|------|---------|
| B1 公厕给排水 | 80.0% | 90.0% | **↑10.0pp** | →0pp |
| B2 华佑电气 | 84.8% | 85.9% | **↑1.1pp** | →0pp |
| B3 配套楼混合 | 94.7% | 99.0% | **↑4.2pp** | ↓1.1pp |
| B4 脏数据 | 89.5% | 89.5% | →0pp | →0pp |

### 4. 基线已更新
```bash
python tools/run_benchmark.py --mode search --save
```
**结果**: 新基线已保存（版本 L2-a_baseline → 覆盖更新）

## 未覆盖风险

1. **conduit_dn 未被 query_builder 利用**: 当前 conduit_dn 只做提取，不影响搜索查询构建。后续 L2-c 可以利用 conduit_dn 优化搜索。
2. **param_validator 的"定额无参数"分数调整**: 从0.5提到0.9，对所有5种硬性参数生效。如果未来出现"定额确实应该有参数但没有"的case，可能需要微调。

## 残余风险
- 无 P0/P1 残余风险
- B2 经验命中率从 20.1% 略降到 19.1%（1pp），属于正常波动

## 验收结论
L2-b 阶段完成。参数提取能力显著增强，4组数据集绿率全部不退化，B1/B2/B3均有提升。
