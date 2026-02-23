# 阶段L2-a Validation — 建立固定Benchmark + 采集基线

## 日期
2026-02-23

## 验收命令与结果

### 1. 全量回归测试
```bash
python -m pytest tests/ -v
```
**结果**: 120 passed, 1 warning in 0.47s

### 2. benchmark 回归测试
```bash
python -m pytest tests/test_benchmark_regression.py -v
```
**结果**: 13 passed in 0.19s

### 3. benchmark 采集（search模式）
```bash
python tools/run_benchmark.py --mode search --save
```
**结果**: 4个数据集全部成功运行，基线已保存

| 数据集 | 条数 | 绿率 | 黄率 | 红率 | 经验命中 | 降级率 | 均耗 |
|--------|------|------|------|------|---------|-------|------|
| B1 公厕给排水 | 20 | 80.0% | 10.0% | 10.0% | 10.0% | 0.0% | 1.18s |
| B2 华佑电气 | 283 | 84.8% | 3.9% | 11.3% | 20.1% | 0.0% | 0.15s |
| B3 配套楼混合 | 95 | 94.7% | 4.2% | 1.1% | 30.5% | 0.0% | 0.12s |
| B4 脏数据 | 19 | 89.5% | 0.0% | 10.5% | 0.0% | 0.0% | 0.16s |

### 4. benchmark 对比
```bash
python tools/run_benchmark.py --mode search --compare
```
**结果**: [OK] 无退化，所有指标在允许范围内。

### 5. 基线文件格式验证
```bash
python -c "import json; d=json.load(open('tests/benchmark_baseline.json','r',encoding='utf-8')); print(f'版本:{d[\"version\"]}, 数据集:{len(d[\"datasets\"])}个')"
```
**结果**: 版本:L2-a_baseline, 数据集:4个

## 未覆盖风险

1. **Agent模式基线未采集**: 当前基线仅包含search模式数据。Agent模式需要API Key，等用户手动运行 `python tools/run_benchmark.py --mode agent --save` 补充。
2. **B4脏数据样本是人工构造的**: 可能不完全覆盖真实场景中的脏数据模式，后续可根据实际遇到的问题补充。
3. **基线文件不在版本控制中**: `benchmark_baseline.json` 应该纳入git跟踪，确保团队共享同一基线。

## 残余风险
- 无 P0/P1 残余风险
- search模式和agent模式的基线可能差异较大（agent有LLM精选，绿率通常更高），后续agent基线采集后需要注意区分

## 验收结论
L2-a 阶段完成。固定benchmark体系已建立，4组数据集基线已采集，13个回归测试已覆盖。
后续 L2-b（参数提取率提升）和 L2-c（低准确率类型专项）的改动都可以通过 `python tools/run_benchmark.py --compare` 量化验证。
