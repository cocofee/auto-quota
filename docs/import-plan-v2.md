# 导入带定额清单 — 全专业平台化方案 v2.2

> v2.2：采用方案A（新增 `batch_import` source 类型），补全 `--trust` 端到端执行路径；删除3项已证伪的重复建设项。

---

## 一、现状问题（经审核确认成立的）

### 数据丢失（P1）

| 丢失字段 | 位置 | 影响 |
|----------|------|------|
| `bill_name/bill_code/bill_unit` | `import_reference.py:411` 调用 `add_experience()` 时没传 | 经验库记录缺少清单名/编码/单位，无法追溯 |
| 介质（给水/消防/空调） | `text_parser.py:1065` 的 `normalize_bill_text()` 跳过了"介质"字段 | 不同介质的管道生成相同 `bill_pattern`，经验库互相覆盖 |

### 架构瓶颈

| 问题 | 位置 | 影响 |
|------|------|------|
| 外部大规模导入直接进 authority 层 | `experience_db.py:226` | 未验证数据可直通匹配，污染风险 |
| 同义词只对安装(C册)生效 | `query_builder.py:95` 非C册直接 return | 土建/市政/园林的同义词挖出来也用不上 |
| 同义词挖掘和导入流程断开 | `self_learn.py` 是独立脚本，手动运行 | 导入数据不能自动发现BM25盲区 |
| XML导入时 section 硬编码为空 | `import_xml.py:105` | XML记录的专业分类靠猜测（解析层已有数据，转换层没用） |

### 同义词挖掘弱点

| 问题 | 位置 | 影响 |
|------|------|------|
| 只看第一个定额 | `self_learn.py:131` | 辅助定额的同义词缺口被忽略 |
| `has_keyword_overlap()` 排除集只有3个词 | `self_learn.py:100` | 高频词导致真正的缺口被误判为"有重叠" |

## 二、设计目标

**核心原则**：要提高算法（BM25搜索），不是最后都给大模型。

**三阶段路线图**：

```
阶段一：改底座（全专业可扩展）  ← 本次实施
阶段二：专业插件化              ← 下次迭代
阶段三：全专业验收门禁          ← 再下次
```

---

## 三、阶段一改动明细

### 3.1 大规模导入默认进 candidate 层

**问题**：`_source_to_layer()` 把 `project_import` 映射到 authority。外部造价软件的9000+项目文件，一次导入几千条未验证数据直接进权威层，"导入几万条发现不对"就晚了。

**执行路径**（方案A，解决 5.3 审核指出的闭环问题）：

新增 source 类型 `batch_import`，不改现有 `project_import` 的映射逻辑。

```
_source_to_layer() 现有逻辑（不改）:
  authority_sources = ("user_correction", "user_confirmed", "project_import")
  → "batch_import" 不在列表里 → 自动进 candidate
```

调用端改动（端到端）：

```python
# tools/import_xml.py (CLI入口)
parser.add_argument("--trust", action="store_true")
source = "project_import" if args.trust else "batch_import"
exp_stats = import_to_experience(..., source=source)

# tools/import_reference.py (桥接层)
def import_to_experience(..., source="batch_import"):
    exp_db.add_experience(..., source=source)
```

更新路径补充（防止同一清单二次导入时失效）：

```python
# src/experience_db.py::_update_experience()
elif source == "batch_import":
    # 批量导入默认仅候选层：更新 quota/materials，但不涨分、不直通
    UPDATE experiences SET
        quota_ids = ?,
        quota_names = ?,
        materials = CASE WHEN ? != '[]' THEN ? ELSE materials END,
        source = 'batch_import',
        layer = 'candidate',
        updated_at = ?
    WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed')
```

| 场景 | source | 层级 | 说明 |
|------|--------|------|------|
| 大规模XML导入（默认） | `batch_import` | candidate | 安全，不参与直通匹配 |
| 用户加 `--trust` | `project_import` | authority | 用户确认过的数据 |
| 用户手动修正 | `user_correction` | authority | 不变 |
| 用户点击确认 | `user_confirmed` | authority | 不变 |

**向后兼容**：已经导入的 `project_import` 记录不受影响，仍在 authority 层。只有新的大规模导入默认走 candidate。

**防污染约束**：`batch_import` 不参与自动晋升 authority，必须走人工确认（`promote_to_authority`）或 `--trust` 路径。

**改动文件**：
- `tools/import_xml.py`：增加 `--trust` 参数，并把 source 传给 `import_to_experience`（~6行）
- `tools/import_reference.py`：`import_to_experience` 增加 `source` 参数并透传到 `add_experience`（~6行）
- `src/experience_db.py`：`_update_experience()` 增加 `batch_import` 分支；自动晋升条件排除 `batch_import`（~15行）

### 3.2 专业路由统一（同义词不再安装专属）

**问题**：`query_builder.py:95` 对非C册直接跳过同义词替换。

**改动**：`src/query_builder.py` 第95行

```python
# 修改前
if specialty and not specialty.upper().startswith("C"):
    return query   # 非安装全跳过

# 修改后：按 _specialty_scope 过滤，不是按首字母一刀切
applicable = _filter_synonyms_by_specialty(self._synonyms, specialty)
# 然后用 applicable 做替换（替换逻辑不变）
```

同义词表 `data/engineering_synonyms.json` 增加可选的 `_specialty_scope` 字段：

```json
{
  "_specialty_scope": {
    "镀锌钢管": ["C10", "C8"],
    "风机盘管": ["C7"],
    "防火阀": ["C7", "C9"]
  },
  "镀锌钢管": ["焊接钢管 镀锌"],
  "风机盘管": ["风机盘管机组"]
}
```

规则：
- `_specialty_scope` 中有记录的 → 只对指定专业生效
- 没有记录的 → **全专业通用**（默认，向后兼容，现有190条无需打标签即可工作）

**改动文件**：`src/query_builder.py`（~15行）、`data/engineering_synonyms.json`（加 `_specialty_scope` 字典，初始为空 `{}`，后续按需补充）

### 3.3 导入时跳过逐条向量写入，导入后批量重建

**问题**：`add_experience()` 每次调用都写 ChromaDB。导入1500条就要逐条嵌入，极慢且容易超时。

**执行路径**：`rebuild_vector_index()` 已存在于 `experience_importer.py:174`（batch_size=256），不需要新建。只需在导入流程中：

1. `add_experience()` 利用已有的 `skip_vector` 参数（检查是否已存在，如不存在则新增）
2. 导入循环结束后调用 `exp_db.rebuild_vector_index()`

```python
# 导入流程：
for pair in pairs:
    exp_db.add_experience(..., skip_vector=True)   # 只写SQLite
exp_db.rebuild_vector_index()                       # 一次性重建
```

**改动文件**：
- `src/experience_db.py`：`add_experience()` 检查/新增 `skip_vector` 参数（~5行）
- `tools/import_xml.py`：导入后调用 rebuild（~3行）
- `tools/import_reference.py`：导入后调用 rebuild（~3行）

### 3.3.1 自动晋升门禁补充（配合 3.1）

为避免“批量导入重复出现同错样本后被自动升权威层”，自动晋升 SQL 增加排除：

```sql
-- 修改前（示意）
if source != "project_import_suspect":
    UPDATE ... SET layer='authority' ...

-- 修改后（示意）
if source not in ("project_import_suspect", "batch_import"):
    UPDATE ... SET layer='authority' ...
```

### 3.4 补传丢失字段

**改动**：`tools/import_reference.py` 第411行

```python
# 修改前
record_id = exp_db.add_experience(
    bill_text=bill_text,
    quota_ids=quota_ids,
    quota_names=quota_names,
    materials=materials,
    confidence=90,
    source="project_import",
    project_name=project_name,
    province=try_province,
)

# 修改后：补传 pair 中已有的3个字段
record_id = exp_db.add_experience(
    bill_text=bill_text,
    quota_ids=quota_ids,
    quota_names=quota_names,
    materials=materials,
    bill_name=pair.get("bill_name"),    # 补传
    bill_code=pair.get("bill_code"),    # 补传
    bill_unit=pair.get("bill_unit"),    # 补传
    confidence=90,
    source=source,                       # 3.1改动：由 --trust 决定
    project_name=project_name,
    province=try_province,
)
```

`add_experience()` 签名已有这3个参数（experience_db.py:558-568），不改接收端。

**改动文件**：`tools/import_reference.py`（~4行）

### 3.5 保留介质信息

**改动**：`src/text_parser.py` 第1065行

```python
# 修改前
r'^(压力试验|安装部位|安装位置|介质|施工要求|...'

# 修改后：移除"介质"
r'^(压力试验|安装部位|安装位置|施工要求|...'
```

**影响**：保留介质后 `bill_pattern` 更细（给水管道≠消防管道），去重粒度变细。需跑 benchmark 确认不退化。

**改动文件**：`src/text_parser.py`（~1行）

### 3.6 同义词自动挖掘集成到导入

**改动**：`tools/import_xml.py` 新增 `mine_synonyms_from_import()` 函数

复用 `self_learn.py` 的核心函数（`clean_bill_name`、`clean_quota_name`、`has_keyword_overlap`），改进点：
- 看**所有定额**，不只第一个（修复 self_learn.py:131 的问题）
- 扩大 `has_keyword_overlap` 排除集（加入 "管道/电缆/设备/工程/系统"）
- 输出带专业标签（配合 3.2 的 `_specialty_scope`）

在 `preview_import()` 末尾输出同义词缺口报告。
新增 `--apply-synonyms` 参数：正式导入时写入 `auto_synonyms.json`。

**改动文件**：`tools/import_xml.py`（~60行）

### 3.7 XML导入传递专业分类

**问题**：`import_xml.py:105` 的 `section` 硬编码为空字符串，但 `parse_zaojia_xml.py` 已经解析了 `unit_project`。

**改动**：`tools/import_xml.py` 的 `convert_xml_to_pairs()` 中使用已有的 `unit_project` 字段：

```python
# 修改前
'section': '',

# 修改后
'section': p.get('unit_project', '') or p.get('specialty', ''),
```

**改动文件**：`tools/import_xml.py`（~1行）

---

## 四、阶段二：专业插件化（记录方向，本次不做）

- 拆分参数校验器：install/civil/municipal/landscape 四套规则
- 拆分审核器：review_checkers 按专业分发
- 拆分Prompt模板：Agent按专业注入不同术语和审核重点

## 五、阶段三：全专业验收门禁（记录方向，本次不做）

- 每专业独立 Benchmark 试卷
- 每专业独立统计绿灯率/红灯率
- 候选层按专业晋升，不达标禁止自动晋升

---

## 六、改动汇总

| 文件 | 改动内容 | 行数 |
|------|----------|------|
| `src/experience_db.py` | add_experience 增加 skip_vector + batch_import更新分支 + 自动晋升排除batch_import | ~20行 |
| `src/query_builder.py` | 同义词按专业分发 | ~15行 |
| `src/text_parser.py` | 移除介质跳过 | ~1行 |
| `tools/import_reference.py` | 补字段 + --trust + skip_vector + rebuild | ~15行 |
| `tools/import_xml.py` | section + 同义词挖掘 + --trust + --apply-synonyms + rebuild | ~70行 |
| `data/engineering_synonyms.json` | 增加 _specialty_scope（初始为空） | ~3行 |
| **合计** | **6个文件** | **~125行** |

---

## 七、验证方法

```bash
# 1. 回归测试（405+条全部通过）
python -m pytest tests/ -q

# 2. 健康检查
python tools/system_health_check.py --mode quick

# 3. benchmark不退化
python tools/run_benchmark.py

# 4. 导入预览（核心验证）
python tools/import_xml.py A.xml \
  --province "重庆市通用安装工程计价定额(2018)" \
  --aux-provinces "重庆市房屋建筑与装饰工程计价定额(2018),重庆市市政工程计价定额(2018)" \
  --preview
# 期望看到：
#   编号命中率报告
#   数据将进入 candidate 层的提示
#   同义词缺口报告

# 5. 确认介质保留
python -c "
from src.text_parser import normalize_bill_text
r = normalize_bill_text('给水管道', '介质:给水\n规格:DN25')
assert '给水' in r, f'介质丢失: {r}'
print(f'OK: {r}')
"

# 6. 确认默认导入走 candidate
python tools/import_xml.py A.xml --province 重庆安装 --preview
# 应提示"数据将进入 candidate 层"

# 7. 确认 --trust 导入走 authority
python tools/import_xml.py A.xml --province 重庆安装 --trust --preview
# 应提示"数据将进入 authority 层"

# 8. 数据库断言：按项目批次验证 source/layer（示意）
python -c "
import sqlite3
from pathlib import Path
db = Path('data') / 'experience.db'
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute(\"\"\"
SELECT source, layer, COUNT(*)
FROM experiences
WHERE project_name=?
GROUP BY source, layer
ORDER BY source, layer
\"\"\", ('人民医院迁建工程项目--发热门诊楼',))
rows = cur.fetchall()
print(rows)
assert any(s=='batch_import' and l=='candidate' for s,l,_ in rows)
conn.close()
"
```

---

## 八、风险点

| 风险 | 应对 |
|------|------|
| 保留介质后 benchmark 退化 | 改完跑 benchmark，退化就回滚 |
| _specialty_scope 冷启动（190条同义词没标签） | 不打标签=全专业通用（默认行为），不影响现有效果，后续按需补充 |
| rebuild_vector_index 导入到一半崩了 | SQLite有事务保护；向量索引在导入全部完成后才重建，不会出现半成品 |
| 需要回滚已导入数据 | 后续可按 source="batch_import" + project_name 批量删除（本次不实现，记录需求） |
| batch_import 误入 authority | 自动晋升逻辑排除 batch_import；仅 `--trust` 或人工晋升可入 authority |
