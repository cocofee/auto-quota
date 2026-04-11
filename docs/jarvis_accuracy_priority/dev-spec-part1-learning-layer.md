# 开发说明 Part 1：学习分层模块

模块位置：`src/experience_db.py`  
依赖：无上游依赖，最先实施  
下游：索引层、检索层、反馈层

## 1. 改动概述

将现有二层体系改为三层体系：

- `authority`
- `verified`
- `candidate`

重点改造：

- `_source_to_layer()`
- 写入时 `verified` 准入判定
- `verified -> authority` 自动晋升
- `materials_signature` / `quota_fingerprint` 衍生字段

## 2. 三层定义

| 层级 | 含义 | 来源 | 检索直通权限 |
|------|------|------|-------------|
| `authority` | 多重验证的强经验 | `user_correction` / `user_confirmed` / `openclaw_approved` / `multi_project_promoted` | 可触发 green |
| `verified` | 单项目可信但未交叉验证 | `completed_project` / `reviewed_import` 且满足准入条件 | 最高 yellow |
| `candidate` | 未验证原始样本 | `batch_import` / `xml_backfill` / `historical_parse` / `auto_extract` | 仅候选 |

## 3. 来源映射

> 现表字段名用 `source`，不是 `source_type`。

```text
user_correction        -> authority
user_confirmed         -> authority
openclaw_approved      -> authority
multi_project_promoted -> authority

completed_project      -> verified   (需满足准入条件)
reviewed_import        -> verified   (需满足准入条件)

project_import         -> candidate
xml_backfill           -> candidate
batch_import           -> candidate
auto_extract           -> candidate
historical_parse       -> candidate
unknown                -> candidate
```

关键改动：

- `project_import` 不再直接进入 `authority`
- `completed_project` 只有满足准入条件才进 `verified`

## 4. verified 准入条件

`completed_project` 或 `reviewed_import` 进入 `verified`，必须同时满足：

| 条件 | 规则 |
|------|------|
| `bill_text` 非空 | depth 0 主记录标准化后可生成主文本 |
| `bill_unit` 非空 | 单位不能为空 |
| `specialty` 非空 | 专业不能为空 |
| `quota_ids` 非空 | 至少 1 个定额编号 |
| `quota_names` 非空 | 与 `quota_ids` 对齐 |
| `parse_status != error` | 来自入口解析阶段 |
| `confidence >= 0.5` | 来自 schema v0.1 |

不满足任一条件，降级为 `candidate`。

## 5. 晋升路径

```text
candidate -> verified -> authority
```

不允许 `candidate` 直接跳到 `authority`。

### 5.1 candidate -> verified

触发时机：写入时同步判定  
条件：

- `evidence_level in (completed_project, reviewed_import)`
- 满足 verified 准入条件
- `confidence >= 0.5`

### 5.2 verified -> authority

触发时机：

- 每日定时扫描
- 批量导入后增量扫描

全部条件必须满足：

| 条件 | 阈值 |
|------|------|
| `specialty` 一致 | 100% |
| `unit` 一致 | 100% |
| `normalized_text` 一致 | 100% |
| `quota_version` 一致 | 必须 |
| 不同项目数 | >= 3 |
| `quota` 一致率 | >= 80% |
| 无已存在 authority 冲突 | 必须 |

分组键：

```text
group_key = hash(normalized_text + specialty + unit + quota_version)
```

## 6. experiences 表新增字段

```sql
ALTER TABLE experiences ADD COLUMN feature_text TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN materials_signature TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN install_method TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN quota_fingerprint TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN quota_codes_sorted TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN promoted_at TEXT DEFAULT NULL;
ALTER TABLE experiences ADD COLUMN promoted_from TEXT DEFAULT NULL;
```

新增晋升日志表：

```sql
CREATE TABLE IF NOT EXISTS promotion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id INTEGER NOT NULL,
    from_layer TEXT NOT NULL,
    to_layer TEXT NOT NULL,
    group_key TEXT NOT NULL,
    matching_project_count INTEGER NOT NULL,
    quota_consistency_rate REAL NOT NULL,
    promoted_at REAL NOT NULL
);
```

## 7. `materials_signature` 生成规则

生成步骤：

1. 从 depth 2 材料明细或 `materials` 列表取材料行
2. 标准化为材料大类编码
3. 先按费用贡献排序；无费用时按 `unit_price * qty`；再不行按出现顺序
4. 取前 3 个不同大类
5. 按字典序排序
6. 用 `|` 拼接

### 7.1 初始材料大类编码

| 编码 | 含义 |
|------|------|
| `steel_pipe` | 钢管 |
| `copper_pipe` | 铜管 |
| `plastic_pipe` | 塑料管 |
| `valve` | 阀门 |
| `insulation` | 保温 |
| `fan_coil` | 风机盘管 |
| `ahu` | 空调机组 |
| `chiller` | 冷水机组 |
| `pump` | 水泵 |
| `duct` | 风管 |
| `cable` | 电缆线 |
| `bridge` | 桥架 |
| `sprinkler` | 喷淋头 |
| `fire_hydrant` | 消火栓 |
| `fitting` | 管件 |
| `other` | 其他 |

特殊情况：

- 没有材料明细：`materials_signature = ""`
- 只有 1~2 种：按实际种类数写
- 无法映射：记为 `other`

## 8. `quota_fingerprint` 生成规则

1. 收集 depth 1 子记录全部 `quota_code`
2. 去空、去重
3. 按字典序排序，写入 `quota_codes_sorted`
4. 用 `|` 拼接
5. 取 MD5 前 8 位，写入 `quota_fingerprint`

示例：

```text
原始：["9-234", "9-233", "9-233"]
排序去重：["9-233", "9-234"]
拼接：9-233|9-234
fingerprint：a3b2c1d4
```

模糊一致性用 `quota_codes_sorted` 计算 Jaccard：

- `>= 0.8`：基本相同
- `>= 0.5`：相关但有差异
- `< 0.5`：不同组价方式

## 9. 需要修改的函数

| 函数 | 改动 |
|------|------|
| `_source_to_layer()` | 三层映射 |
| `add_experience()` | 接收现表字段，写入新增列，做 verified 判定 |
| `_check_promotion()` | 新增，做 `verified -> authority` 晋升判断 |
| `run_promotion_scan()` | 新增，定时/增量晋升 |
| `_compute_material_signature()` | 新增 |
| `_compute_quota_fingerprint()` | 新增 |

## 10. 验收标准

正例：

- `user_correction` 写入后 `layer = authority`
- `completed_project` 且字段齐全时 `layer = verified`
- 3 个不同项目、同 `specialty/unit/normalized_text/quota_fingerprint` 的 verified 组可晋升 authority

反例：

- `completed_project` 缺 `quota_ids`，只能进 `candidate`
- 2 个项目一致记录，不晋升
- `specialty` 不同的同名记录，不晋升
- `project_import` 不得直接落 `authority`
