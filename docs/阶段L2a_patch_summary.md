# 阶段L2-a Patch Summary — 建立固定Benchmark + 采集基线

## 日期
2026-02-23

## 改动清单

### 新增文件

| 文件 | 行数 | 用途 |
|------|------|------|
| `tests/benchmark_config.json` | 35行 | 4组固定benchmark数据集定义（B1-B4） |
| `tools/run_benchmark.py` | 364行 | benchmark运行与基线管理脚本 |
| `tests/benchmark_baseline.json` | 43行 | search模式基线数据（由脚本生成） |
| `tests/test_benchmark_regression.py` | 133行 | 13个pytest回归测试 |
| `tests/fixtures/dirty_data_sample.xlsx` | 6KB | B4脏数据样本（20条） |
| `tests/fixtures/gen_dirty_data.py` | 108行 | 脏数据生成脚本 |

### 修改文件
无（L2-a 纯新增，不修改现有代码）

## 改动行数统计
- 新增代码：约640行
- 修改代码：0行
- **超200行说明**：因为是从零建立benchmark体系，包含运行脚本（364行）、回归测试（133行）、脏数据生成（108行）等，每个文件功能独立，没有过度设计。

## 影响范围
- **无**：L2-a 不修改任何现有文件，只新增文件
- 全部120个测试通过（原105个 + 新13个 + 2个脏数据验证）

## 兼容性
- 旧数据/旧索引/旧库结构完全兼容（不碰现有代码）
- benchmark 数据文件路径不存在时自动跳过，不阻塞其他数据集

## 回滚点
```bash
# 如需回滚，删除以下文件即可：
rm tests/benchmark_config.json
rm tests/benchmark_baseline.json
rm tests/test_benchmark_regression.py
rm tests/fixtures/dirty_data_sample.xlsx
rm tests/fixtures/gen_dirty_data.py
rm tools/run_benchmark.py
# 回到原来的 105 个测试状态
```
