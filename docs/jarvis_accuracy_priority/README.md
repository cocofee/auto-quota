# JARVIS 准确性优先方案

版本：v1.0  
对齐补充：Alignment Addendum v1.1  
日期：2026-03-26  
状态：设计锁定，可进入开发

## 文档清单

| 编号 | 文件 | 内容 |
|------|------|------|
| Part 1 | `dev-spec-part1-learning-layer.md` | 学习分层：authority / verified / candidate 三层体系，晋升规则，`materials_signature` / `quota_fingerprint` |
| Part 2 | `dev-spec-part2-index-retrieval.md` | 索引构建，六段式检索，多通道召回，硬过滤，重排，green/yellow/red 门控 |
| Part 3 | `dev-spec-part3-intake.md` | 统一入口：`ingest_intent` / `evidence_level` / `ingest()` 契约 / schema 映射 |
| Part 4 | `dev-spec-part4-feedback.md` | 两阶段反馈采集，误排主因自动判定，权重校准 |
| Part 5 | `dev-spec-part5-price-reference.md` | 价格参考：标准化，事实化，聚类，异常值标记，分层输出 |
| Addendum | `alignment-addendum-v1.1.md` | 与现代码基线对齐：旧入口收口，现表字段映射，双表增强，API 兼容 |
| Status | `current-price-reference-status-2026-03-27.md` | 统一价格参考库最新实测存量、缺口和下一步执行顺序 |
| Ops | `learning-backfill-checkpoint-ops-2026-03-27.md` | 学习回流批量 checkpoint 续跑规则，避免 `offset` 与已处理文档相互干扰 |

## 模块依赖

```text
                file_intake (Part 3)
                       |
            +----------+----------+
            |                     |
            v                     v
   experience_db (Part 1)   price_reference_db (Part 5)
            |
            v
   索引 + 检索层 (Part 2)
            |
            v
       反馈层 (Part 4)
```

## 锁定实施顺序

| 步骤 | 内容 | 对应文档 |
|------|------|----------|
| S1 | 学习分层改造 | Part 1 |
| S1.5 | 旧入口收口，`file_intake.ingest()` 激活 | Addendum A1 |
| S2 | 轻量反馈采集 | Part 4 阶段 A |
| S3 | 索引构建层 | Part 2 索引部分 |
| S4 | 结构化重排 + 门控 + 完整反馈 | Part 2 检索部分 + Part 4 阶段 B |
| S5 | 价格双表增强 + 价格事实化 + API 兼容 + 日期标准化 | Part 5 + Addendum A3/A4/A6/A7 |
| S6 | 入口层路由改造 + schema 对齐映射 | Part 3 + Addendum A2 |
| S7 | green/yellow/red 上线 | Part 2 |
| S8 | 扩大导入 | 基于 S1~S6 完成后进行 |

## 锁定决策

| 决策 | 内容 |
|------|------|
| 三层体系 | `authority / verified / candidate` |
| 查询扩展顺序 | 本省 authority -> 全国 authority -> 本省 verified -> 全国 verified -> 本省 candidate -> 全国 candidate |
| 向量索引 | 单 collection + metadata filter |
| `materials_signature` | 主材前 3 大类编码，排序后用 `|` 拼接 |
| `materials_signature_first` | `materials_signature` 首段，作为价格 bucket 物理列 |
| `quota_fingerprint` | 定额编号去重排序后拼接，MD5 前 8 位，并保留 `quota_codes_sorted` |
| 晋升硬条件 | `specialty/unit/normalized_text` 一致 + 不同项目 >= 3 + `quota` 一致率 >= 80% |
| 重排权重 | text 0.35 / specialty 0.20 / unit 0.15 / material 0.15 / source 0.10 / consensus 0.05 |
| 门控阈值 | green >= 0.85；yellow >= 0.60；red < 0.60 或有 red flag |
| 价格异常值 | 不删除，只标记 `price_outlier` |
| 价格事实化 | 写入时拆分 price fact，不在查询时临时拆 |
| 反馈采集 | 阶段 A 轻量采集，阶段 B 采集维度得分 |

## 当前存量基线

以下存量数据已经进库，但尚未完全接入本方案的新准确性链路：

| 项目 | 当前状态 |
|------|----------|
| XML 文件回补 | 1232 份 XML 已入库 |
| XML 解析行数 | `1,217,654` 行 |
| XML 已带综合单价行数 | `1,217,654` 行 |
| 全部综合单价存量 | `1,218,381` 行 |

这些综合单价样本是后续 `verified/candidate` 学习层、价格异常值扫描、`layered_result` 分层输出的重要基线数据。  
S5 与 S6 实施时，必须把这批存量纳入迁移和补算，不允许只覆盖新增导入。

## 与统一文件入口方案的对齐

| 对齐点 | 状态 |
|--------|------|
| JSON schema v0.1 作为统一中间格式 | 已对齐 |
| `materials_signature` / `install_method` 回补到 schema | 待补 |
| `quota_fingerprint` / `quota_codes_sorted` 回补到 schema | 待补 |
| 主 skill 分流结果接 `file_intake.ingest()` | 已锁定 |
| `result-backfill` 消费价格层输出 | 已对齐，S5 补 `layered_result` |

## 文档优先级

若 Part 1~5 与 Addendum v1.1 冲突：

1. 物理表结构以 Addendum 为准。
2. 现表字段名与 schema 映射以 Addendum 为准。
3. 业务规则、阈值、权重、验收口径以 Part 1~5 为准。
