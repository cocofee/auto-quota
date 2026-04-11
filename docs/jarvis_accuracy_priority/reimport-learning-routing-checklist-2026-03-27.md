# 价格与学习重导执行清单

日期: 2026-03-27
状态: 可执行
目标: 把"重新导入是否会增强 JARVIS 学习"这件事切成可落地的分流规则和批次顺序

## 一句话结论

重新导入不等于自动学习。

- 走 `ingest_intent=learning` 或 `dual_use` 的带定额历史清单, 才会增强 `experience_db`, 从而增强清单配定额与经验检索。
- 走 `ingest_intent=price_reference` 的纯价格样本, 只会增强价格参考库, 不会增强定额匹配。
- 当前正在执行的 `price_date_iso` 回补, 属于价格库治理, 不属于重新学习。

## 当前基线

基于 2026-03-27 实库统计:

| 指标 | 数值 |
|------|------|
| `historical_boq_items` 总量 | `1,959,982` |
| 其中带 `composite_unit_price` | `1,218,381` |
| 其中带 `quota_code` | `1,895,177` |
| 同时带价格 + 定额 | `1,153,582` |
| 有价格但无定额 | `64,799` |
| 已补 `price_date_iso` | `6,069` |
| `price_documents` 总量 | `12,280` |
| 其中 `priced_bill_file` 文档 | `1,233` |
| 文件名/项目名显式带日期 token 的 `priced_bill_file` 文档 | `620` |
| `historical_quote_items` 总量 | `0` |
| `experiences` 总量 | `446,109` |
| `experiences.layer=authority` | `371,657` |
| `experiences.layer=verified` | `11,823` |
| `experiences.layer=candidate` | `46,283` |
| `experiences.source=completed_project` | `0` |

结论:

- 那批 "100 多万条带综合单价 + 带定额" 的数据, 现在大部分已经进了价格库, 但还没有系统性回流成 `completed_project -> verified` 的学习样本。
- 所以它们当前更强的是"查价", 还没有充分转化成"更准的清单配定额"。

## 分流规则

### A 类: 必须重导到学习 + 价格

适用对象:

- 带 `quota_code`
- 带 `composite_unit_price`
- 能定位到原始项目文件或原始文档
- 来源可信, 可视为已完工或已复核样本

系统入口:

- `ingest_intent=dual_use` 或先 `learning`, 再补 `price_reference`
- `evidence_level=completed_project`

预期收益:

- 进入 `experience_db`
- 满足准入条件时进入 `verified`
- 后续可参与晋升到 `authority`
- 同时保留在价格库, 参与 `layered_result`

当前规模:

- 优先目标: `1,153,582` 行

执行优先级:

- 最高

### B 类: 只重导到学习

适用对象:

- 带 `quota_code`
- 无价格或价格明显不可信
- 但原始清单 + 定额组合可信

系统入口:

- `ingest_intent=learning`
- `evidence_level=completed_project` 或 `reviewed_import`

预期收益:

- 增强清单到定额的召回与重排
- 不污染价格统计

说明:

- 如果这批数据本来就来自 `experience_db` 回填种子, 不要再从 `historical_boq_items` 反向重导一遍, 否则会重复学习。

### C 类: 只重导到价格

适用对象:

- 有价格
- 无定额
- 或纯报价单 / 设备材料价

系统入口:

- `ingest_intent=price_reference`
- `evidence_level=raw_import` 或 `reviewed_import`

预期收益:

- 增强查价和价格回填
- 不增强定额匹配

当前规模:

- `64,799` 行 "有价格但无定额" 的综合单价行
- 未来新增的纯报价单文档

### D 类: 不要重导到学习

适用对象:

- `seed_source=experience_db` 的价格库种子
- 由学习库反推过来的 BOQ 参考种子
- 缺原始文件, 只能看到扁平结果行, 无法还原真实项目上下文

原因:

- 这类数据已经是学习库的派生物
- 再反灌回学习库会形成自我循环
- 会放大学习噪声, 不会增加新知识

## 现有链路状态

### 已经对齐到统一入口的链路

- [bill_price_documents.py](/C:/Users/Administrator/Documents/trae_projects/auto-quota/web/backend/app/api/bill_price_documents.py)
  解析带定额清单后会调用 `ingest(..., ingest_intent="learning", evidence_level="completed_project")`
- [feedback.py](/C:/Users/Administrator/Documents/trae_projects/auto-quota/web/backend/app/api/feedback.py)
  用户纠错与管理员导入已走统一入口
- [file_intake.py](/C:/Users/Administrator/Documents/trae_projects/auto-quota/web/backend/app/api/file_intake.py)
  已负责 learning / price_reference / dual_use 路由

### 本轮补齐的链路

- [backfill_priced_bills_to_price_reference.py](/C:/Users/Administrator/Documents/trae_projects/auto-quota/tools/backfill_priced_bills_to_price_reference.py)
  之前 `--sync-learning` 直写 `ExperienceDB.add_experience()`。
  现在已改为走统一 `ingest()` 并写成 `completed_project` 学习样本, 不再绕过入口层。

## 推荐执行顺序

### P0: 继续价格日期回补

目标:

- 先把 `latest_price` 的时间可信度拉起来

范围:

- 先扫文件名/项目名显式带日期 token 的 `620` 份 `priced_bill_file`

备注:

- 这一步增强的是价格可信度, 不是学习层

### P1: 重导原始带定额综合单价文档到学习层

目标:

- 把历史已完工综合单价文档系统性写成 `completed_project`

入口:

- 优先走原始文档重解析
- 不建议直接拿扁平 `historical_boq_items` 行反灌

理由:

- 原始文档才能保留项目名, 层级, 材料, 特征文本, 定额组合
- 扁平行容易把同一清单项拆碎, 降低学习质量

预期结果:

- `experiences.source=completed_project` 从 `0` 开始增长
- `verified` 数量显著提升

### P2: 重建学习索引

在 P1 批量写入后执行:

- FTS 已逐条更新
- 向量检索仍需统一执行 `rebuild_vector_index()`

否则:

- 文本召回会变强
- 语义召回不会完全同步

### P3: 跑学习增强回补

目标:

- 回补 `materials_signature`
- 回补 `quota_fingerprint / quota_codes_sorted`
- 跑一次 `run_promotion_scan()`

预期结果:

- 更多 `verified -> authority`
- 检索重排和 green/yellow/red 判定更稳定

### P4: 再做价格层细化治理

目标:

- 继续回补 `materials_signature`
- 跑异常值复扫
- 提高 `layered_result` 聚类质量

## 具体执行口径

### 可以直接开始重导学习的范围

- 原始 XML / Excel 带定额综合单价文件
- 已能被 `parse_priced_bill_document()` 正常解析的历史文档
- 业务上可认定为已完工项目的文件

### 暂时不要动的范围

- 纯价格但无定额的 `64,799` 行
- `seed_source=experience_db` 的 `741,595` 行种子样本
- 无法定位原始文件的派生结果

## 成功标准

满足下面 4 条, 才算"重新导入已经真正增强学习":

1. `experiences.source=completed_project` 明显增长, 不再是 `0`
2. `experiences.layer=verified` 显著增长
3. 批量导入后执行过 `rebuild_vector_index()`
4. 线上匹配结果里, `knowledge_evidence` 和经验候选可明显看到新回流项目

## 对业务的直接解释

这批数据重新导入后, JARVIS 会增强, 但前提是导入方式正确:

- 重导到 `learning/dual_use`: 会增强
- 只进 `price_reference`: 只增强查价, 不增强定额匹配
- 只做日期/异常值回补: 属于治理, 不属于学习

所以后面的主线不是"把 100 多万行再导一次", 而是:

- 把其中最有价值的原始带定额项目文档按 `completed_project` 重导进学习层
- 把纯价格样本留在价格层
- 把派生种子禁止反灌回学习层
