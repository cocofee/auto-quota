# Knowledge Staging P0 实施顺序说明

## 结论
P0 先走 `B -> A`：

1. 先把 `knowledge_staging.db` 接成初始化脚本和中间层模块
2. 再在这个底座上补 `audit_errors + promotion_queue + RuleKnowledge` 的最短闭环

不建议跳过中间层模块直接写业务适配器。否则 SQL 和状态流容易散落到多个入口，后面会很难收口。

## P0-1 中间层模块化
目标：先让 staging 层可被程序稳定使用。

范围：

- 初始化入口
- `src/knowledge_staging.py`
- 建库
- 连接
- 基础插入
- 基础查询
- 状态更新
- 视图查询

建议交付：

- `docs/knowledge_staging_schema_v1.sql`
- `src/knowledge_staging.py`
- 一个最小初始化入口

验收标准：

- 可自动初始化 `db/common/knowledge_staging.db`
- 可以对 `audit_errors`、`promotion_queue` 做基础读写
- 可以查询 `v_pending_promotions`、`v_active_audit_errors`

## P0-2 审核错因录入
目标：审核一条错因后，能稳定写入 staging。

范围：

- `audit_errors` 写入逻辑
- `promotion_queue` 入队逻辑
- 基础审核状态流转

建议交付：

- 错因录入方法
- 候选晋升入队方法
- 最小查询接口

验收标准：

- 一条审核错因可写入 `audit_errors`
- 一条可晋升规则候选可写入 `promotion_queue`
- 可以按状态看到待审核候选

## P0-3 RuleKnowledge 适配器
目标：先跑通“错因 -> 规则”的最短真实闭环。

范围：

- 从 `promotion_queue` 读取已审核通过候选
- 写入 `RuleKnowledge`
- 回写 `promoted_target_id`
- 回写 `promotion_trace`
- 同步源记录状态

建议交付：

- `RuleKnowledge` 适配器骨架
- 晋升执行方法
- 回写 trace 逻辑

验收标准：

- 一条真实错因样例可以完成：
- 写入 `audit_errors`
- 入队 `promotion_queue`
- 审核通过
- 晋升到 `RuleKnowledge`
- 回写目标 ID 和 trace

## 开发约束

- OpenClaw 只写 staging，不直写正式层
- staging 是新增知识的唯一程序入口
- 正式层写入必须经过审核和适配器
- 先只打通 `RuleKnowledge`，不要同时铺开 `UniversalKB`、`MethodCards`、`ExperienceDB`

## 下一步
按这个顺序实现：

1. `src/knowledge_staging.py`
2. staging 初始化入口
3. `audit_errors` / `promotion_queue` 基础读写
4. `RuleKnowledge` 适配器
