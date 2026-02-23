# 批次 P2-1 Patch Summary（灯具硬映射偏移修复）

## 改动点

| 文件 | 行号 | 改动 | 影响面 |
|------|------|------|--------|
| `src/query_builder.py` | L179-184 | 拆分正则：线槽灯单独映射，直管灯/灯管改走荧光灯 | 灯具搜索 query 构建 |
| `tests/test_query_builder_lamp_rules.py` | +85行 | 新增 TestTubeLampMapping(4个) + TestLongTailLamps(8个) | 无 |

## 具体改动

### src/query_builder.py L179-184

```python
# 修复前（一个正则覆盖三种灯具）：
if re.search(r'直管|灯管|线槽灯', cleaned):
    return "LED灯带 灯管式"

# 修复后（按安装工艺拆分）：
# 线槽灯 → LED灯带 灯管式（线槽灯安装在线槽内，安装工艺接近LED灯带）
if "线槽灯" in cleaned:
    return "LED灯带 灯管式"

# 直管灯/灯管 → 荧光灯具安装（管状灯具不论LED还是荧光，套荧光灯安装定额）
if re.search(r'直管|灯管', cleaned):
    return "荧光灯具安装 单管"
```

**逻辑**：线槽灯必须在直管灯之前判断，因为"线槽灯"也包含"灯"字但安装工艺不同。

### tests/test_query_builder_lamp_rules.py

新增两个测试类：

**TestTubeLampMapping**（4个测试）：
- `test_tube_lamp_not_led_strip` — 直管灯不映射到LED灯带
- `test_lamp_tube_not_led_strip` — 灯管不映射到LED灯带
- `test_led_tube_lamp_not_led_strip` — LED直管灯不映射到LED灯带
- `test_trough_lamp_is_led_strip` — 线槽灯正确映射到LED灯带

**TestLongTailLamps**（8个测试）：
- `test_downlight_preserved` — 筒灯保留原名
- `test_foot_light_preserved` — 地脚灯保留原名
- `test_courtyard_lamp_preserved` — 庭院灯保留原名
- `test_wall_washer_special` — 洗墙灯走特殊灯具路径
- `test_track_light_special` — 轨道灯走特殊灯具路径
- `test_explosion_proof_lamp` — 防爆灯走密闭灯安装
- `test_well_shaft_lamp` — 井道灯走密闭灯安装

## 回滚方式

单文件回滚：`git checkout src/query_builder.py`

## 未改动项

- `main.py` 主入口不变
- Excel 输出契约和用户可见字段语义不变
- 其他灯具映射规则（吸顶灯、壁灯、应急灯等）不变
