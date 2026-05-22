# 2026-03-29 synonym_gap 工作集导出

## 本步目标

不改匹配算法，只把主架构下一步 `closed_book -> synonym_gap -> query rewrite offline eval` 的数据通路正式接起来。

问题点：

- `tools/keyword_miss_query_rewrite_eval.py` 已经存在
- 但它默认读取的 `output/real_eval/keyword_miss_export.jsonl` 没有从当前 `run_real_eval.py` 正式导出
- 导致主评测和离线 rewrite 评测脱节

## 本步改动

文件：

- `tools/run_real_eval.py`
- `tests/test_real_eval_tools.py`

新增能力：

1. `run_real_eval.py` 新增 `_build_keyword_miss_export_rows(...)`
   - 只导出：
     - `cause = synonym_gap`
     - `miss_stage = recall_miss`
   - 即：正确答案没进候选池、且归因为术语/词面缺口的工作集

2. CLI 新增：
   - `--keyword-miss-export-out`

3. 导出字段收紧为离线 rewrite 真正需要的最小集合：
   - `sample_id / province / specialty`
   - `bill_name / bill_text`
   - `oracle_quota_ids / oracle_quota_names`
   - `search_query`
   - 精简后的 `router / retriever / ranker`
   - `cause / miss_stage / error_stage / error_type`
   - `algo_id / algo_name`

说明：

- 不再导出整份 `candidate_snapshots`
- 这样工作集可以直接给 `tools/keyword_miss_query_rewrite_eval.py` 用，不会过重

## 验证

单测：

- `tests/test_real_eval_tools.py -k "keyword_miss_export_rows or build_mode_comparison or detail_from_result_keeps_experience_review_rejection_trace"`
- 结果：`3 passed`

实跑样例：

- 数据集：`output/real_eval/real_eval_smoke_install_only.jsonl`
- 省份：
  - `上海市安装工程预算定额(2016)`
  - `福建省通用安装工程预算定额(2017)`
- profile：`smoke`

命令：

```powershell
python tools/run_real_eval.py `
  --dataset output/real_eval/real_eval_smoke_install_only.jsonl `
  --profile smoke `
  --province "上海市安装工程预算定额(2016)" `
  --province "福建省通用安装工程预算定额(2017)" `
  --summary-out output/real_eval/synonym_gap_export_smoke.summary.json `
  --details-out output/real_eval/synonym_gap_export_smoke.details.jsonl `
  --keyword-miss-export-out output/real_eval/keyword_miss_export.jsonl
```

结果：

- `summary`: `40` 条，命中率 `25.0%`
- `diagnosis`:
  - `wrong_book = 17`
  - `synonym_gap = 11`
  - `wrong_tier = 2`
- 导出工作集：
  - `output/real_eval/keyword_miss_export.jsonl`
  - 共 `6` 条

说明：

- 这 6 条就是当前两省 smoke 样本里可直接进入 rewrite 评测的 `synonym_gap + recall_miss` 工作集
- 数据通路已经打通

## 当前状态

主架构层面：

- 收口已完成
- 双口径评测已正式化
- `experience_review_rejected` 可观测性已补齐
- `synonym_gap` 工作集导出已接回主评测链

所以当前下一步不再是改架构，而是：

1. 用这批工作集跑 `tools/keyword_miss_query_rewrite_eval.py`
2. 先看 query rewrite 对 `recall@k` 的净提升
3. 再决定只打哪一类 `synonym_gap` 子问题
   - 固定术语别名
   - 主体被描述噪声劫持
   - 参数格式差异

## 下一步

只做一件事：

- 跑 `keyword_miss_query_rewrite_eval.py`
- 对当前导出的 `keyword_miss_export.jsonl` 做一次离线 rewrite 验证
- 看 `old_recall@k -> new_recall@k` 的真实增益

## 2026-03-29 追加：synonym_gap 子类分析与第一刀落地

### 子类分析

基于旧基线样本 `compare_baseline.details.jsonl` 中：

- `cause = synonym_gap`
- `miss_stage = recall_miss`

共抽出 `78` 条做快速归类，结果：

- `subject_noise_or_field_loss`: `60`
- `fixed_alias_or_term_gap`: `15`
- `other_long_tail`: `3`

结论：

- 当前 `synonym_gap` 的主矛盾不是先补大规模术语表
- 而是主体词在主 query 里拿不到主导权，尤其：
  - 空名称场景
  - 字段化描述场景
  - 管道分支提前 return，导致 `decisive_terms` 根本没进最终 query

### 第一刀改动

文件：

- `src/query_builder.py`
- `tests/test_query_builder_primary_guard.py`

改动原则：

- 不重写 query builder
- 只把 `primary_profile` 里已经存在的主体信号前移到真实会生效的位置

具体做法：

1. 新增主体种子词收口：
   - `_build_query_subject_seed_terms(...)`

2. 管道分支前置主体种子词：
   - 修复 `fields` 场景下 `decisive_terms` 因提前 return 失效的问题

3. 通用分支前置主体种子词，但加安全约束：
   - `front_segment` 先走 `_truncate_subject_phrase(...)`
   - 只保留单主体 seed，不重复叠加 `primary_subject`
   - 如果标准化后的主题不在 front seed 里，则不让 front seed 覆盖标准主题

### 定向测试

新增/覆盖：

- `塑料管 + 字段描述`
  - 断言 query 保留 `PE100 / DN100 / 电热熔连接`
- `空名称 + 风口长描述`
  - 断言 query 保留 `送风口 / 检修口`
- `空名称 + 墙面喷刷涂料`
  - 断言不再把 `PT-202` 这类长描述型号重新压回 query

结果：

- `tests/test_query_builder_primary_guard.py`
  - 定向 `5 passed`

### 两省 smoke 回测

数据集：

- `output/real_eval/real_eval_smoke_install_only.jsonl`

省份：

- `上海市安装工程预算定额(2016)`
- `福建省通用安装工程预算定额(2017)`

基线：

- `hit_rate = 25.0%`

第一版主体前置：

- `27.5%`
- 但有 `+2 / -1`
- 说明前置方向对，但 front seed 过重

收稳后版本：

- `hit_rate = 27.5%`
- 稳定成 `+1 / -0`

稳定收益样本：

- `exp:447783`
  - `金属踢脚线 木龙骨 9mm厚B1级阻燃多层板 50mm高古铜色不锈钢`
  - 由错配改为命中

说明：

- 这一步已经证明“主体种子词前置”可以安全进主链
- 但当前收益主要落在 `front_segment` 的空名称/弱主题场景
- 下一步不再继续加大主体前置力度，而应转入第二子类：
  - `fixed_alias_or_term_gap`
  - 也就是固定术语别名

### 当前状态更新

主架构推进到这里：

1. 架构收口完成
2. 双口径评测完成
3. review rejection 可观测完成
4. synonym_gap 工作集导出完成
5. synonym_gap 离线 rewrite 验证完成
6. 主体种子词前置已在主链做了第一刀，并完成小范围稳定回测

所以下一步不再回头改这部分，直接进入：

- `fixed_alias_or_term_gap`
- 做最小别名映射切入

## 2026-03-29 追加：fixed_alias_or_term_gap 第一刀验证

### 本步目标

只做最小 alias 补丁，不改 query builder 结构，不扩散到排序/后处理：

- 验证固定术语别名是否能在主链安全落地
- 先挑两条已在基线错例里出现、且语义稳定的映射

### 本步改动

文件：

- `data/engineering_synonyms.json`
- `tests/test_query_builder_fixed_aliases.py`

补丁内容：

1. 新增：
   - `大便槽冲洗管 -> 大便冲洗管`

2. 修正既有映射方向：
   - `不间断电源系统调试`
   - 原先首选扩展词是 `UPS不停电装置调试`
   - 现改为首选扩展词 `保安电源系统调试`
   - 原词保留为第二扩展值，仅作兼容记录

说明：

- 这一步没有改 `_apply_synonyms(...)` 机制
- 仍然保持“命中一个 key 只追加一次”的安全策略

### 定向测试

新增测试覆盖：

- `_apply_synonyms("大便槽冲洗管 DN32")`
  - 断言保留原词，同时追加 `大便冲洗管`
- `build_quota_query(...)` 对短卫浴条目
  - 断言最终 query 含 `大便冲洗管`
- `build_quota_query(...)` 对 UPS 系统调试
  - 断言最终 query 含 `保安电源系统调试`

结果：

- `tests/test_query_builder_fixed_aliases.py`
  - `3 passed`
- `tests/test_query_builder_primary_guard.py`
  - `14 passed`

### 两省 smoke 回测

回测口径：

- `closed_book`
- 数据集：`output/real_eval/real_eval_smoke_install_only.jsonl`
- 省份：
  - `上海市安装工程预算定额(2016)`
  - `福建省通用安装工程预算定额(2017)`

结果：

- `hit_rate = 27.5%`
- 与 `synonym_gap_subject_seed_v3` 持平
- `change_count = 0`

### 结论

这一步的结论很明确：

1. alias 补丁是安全的
   - 没有引入回归
   - 定向查询已正确扩展

2. 但它不是当前两省 smoke 的主收益来源
   - 例如 `exp:122871`
   - query 已从：
     - `DN32 大便槽冲洗管 DN32`
   - 扩成：
     - `DN32 大便槽冲洗管 DN32 大便冲洗管`
   - 候选数从 `12 -> 14`
   - 但 `oracle` 仍未进候选池

3. 说明当前阻塞点已不是“有没有 alias”
   - 而是短词条目在 `DN32` 这类强规格干扰下，仍会被错误 family / tier 候选淹没
   - 这属于更靠近 `subject/tier` 的召回问题，不是继续堆 alias 能解决的

### 下一步

不再继续扩 alias 表，转入：

- `短主体 + 强规格(DN/De) + 卫浴/器具语义` 的 tier 保护
- 目标不是加更多别名
- 而是让这类短条目先稳住主体 family，再放规格参与召回
