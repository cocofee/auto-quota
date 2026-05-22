# 2026-03-29 knowledge_not_used 复核结论

## 本轮先做了什么

1. 给 `retriever` trace 补了 `kb_hit`
   - 文件：
     - `src/candidate_scoring.py`
     - `src/match_pipeline.py`
2. 用 `with_memory` 单省聚焦评测验证“资产接回主链”后的真实收益

## 关键结论

之前在 `closed_book` 里看到的大量 `knowledge_not_used`，
本质上不是“主链明明挂了资产却没生效”，
而是评测模式本身没有接入 `experience_db`。

也就是说：

- `closed_book`
  - 用来测纯搜索主链
- `with_memory`
  - 才能测经验库/知识库接回主链后的真实生产能力

## 单省 with_memory 结果

### 黑龙江省建筑与装饰工程消耗量定额(2019)

- closed_book: `30.0%`
- with_memory: `100.0%`

文件：

- `output/real_eval/heilongjiang_with_memory.summary.json`
- `output/real_eval/heilongjiang_with_memory.details.jsonl`

### 宁夏房屋建筑装饰工程计价定额(2019)

- closed_book: `20.0%`
- with_memory: `95.0%`

文件：

- `output/real_eval/ningxia_with_memory.summary.json`
- `output/real_eval/ningxia_with_memory.details.jsonl`

剩余 1 条不是经验链问题，而是：

- `match_source = skip_measure`
- 样本：`exp:7235`
- 属于输入门禁/措施项识别问题

### 上海市安装工程预算定额(2016)

- closed_book: `10.0%`
- with_memory: `100.0%`

文件：

- `output/real_eval/shanghai_install_with_memory.summary.json`
- `output/real_eval/shanghai_install_with_memory.details.jsonl`

## 结果说明

这批样本的主要收益来源已经非常明确：

- 不是 rerank
- 不是 router
- 而是经验资产接回主召回链

从结果明细看，主要命中来源是：

- `experience_similar_confirmed`
- 少量 `experience_exact_confirmed`

并且 retriever trace 里已经能看到：

- `authority_hit = True`
- 很多样本 `kb_hit = True`

## 对主架构的影响

主架构下一段应该正式拆成两条评测线：

1. `closed_book`
   - 只衡量纯搜索主链
   - 继续用于看 `router / synonym / tier`
2. `with_memory`
   - 衡量资产接回主链后的真实生产能力
   - 这是经验省份的正式标尺

## 下一步不要跑偏

不要回去继续折腾 `wrong_book`。

下一步只做两件事：

1. 把 `with_memory` 评测纳入正式对照
   - 至少按“有经验省份 / 无经验省份”分开统计
2. 单独清理 `review_check` 对经验直通的误伤
   - 当前日志已经出现 `category_mismatch / material_mismatch` 误拦
   - 但这一步应该只针对“经验直通已命中”的场景做收窄
