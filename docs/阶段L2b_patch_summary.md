# 阶段L2-b Patch Summary — 参数提取率提升

## 日期
2026-02-23

## 改动清单

### 新增文件

| 文件 | 行数 | 用途 |
|------|------|------|
| `tests/test_text_parser.py` | 237行 | 42个参数提取回归测试（DN/截面/回路/材质/连接） |

### 修改文件

| 文件 | 改动范围 | 说明 |
|------|---------|------|
| `src/text_parser.py` | `_extract_dn()`, 新增 `_extract_conduit_dn()`, `_extract_cable_section()`, `parse()` | 新增管材代号管径提取(conduit_dn)、导线型号截面提取、"直径"关键词、"X路"回路匹配 |
| `src/param_validator.py` | `_check_params()` | "定额无参数"评分从0.5调高到0.9（合理化：通用定额不应被误罚） |

## 改动行数统计
- 新增代码：约280行（测试237行 + 实现约43行）
- 修改代码：约15行（param_validator 5处 0.5→0.9）

## 关键设计决策

### conduit_dn 与 dn 分离
管材代号（SC20、PC25）的管径存为 `conduit_dn`，而非 `dn`：
- **dn**: 给排水管径，定额按此分档（DN100≠DN150），参数验证器会校验
- **conduit_dn**: 电气配管管径，定额不按此分档（SC20和SC25用同一个定额），参数验证器不校验

如果混用，B2华佑电气绿率会从84.8%暴跌到68.2%（-16.6pp），因为电气定额没有DN参数导致验证器误罚。分离后B2绿率反而提升到85.9%（+1.1pp）。

## 影响范围
- `src/text_parser.py`: 参数提取增强，影响清单数据清洗和定额名称解析
- `src/param_validator.py`: 评分微调，影响置信度计算
- 全部162个测试通过（原120个 + 新42个）

## 兼容性
- 旧数据/旧索引/旧库结构完全兼容
- 新增的 `conduit_dn` 字段对不识别它的下游代码无影响（dict里多一个key而已）

## 回滚点
```bash
# 如需回滚，恢复以下文件：
git checkout HEAD -- src/text_parser.py src/param_validator.py
rm tests/test_text_parser.py
# 回到原来的 120 个测试状态
```
