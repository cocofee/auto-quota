# 准确率恢复执行计划

更新时间：2026-03-27

## 目标

把当前链路收敛成固定顺序：

1. `parser`
2. `router`
3. `retriever`
4. `ranker`
5. `final_veto`

约束：

- 只有 `ranker` 可以决定 `top1`
- `candidate_arbiter`、`final_validator` 先冻结，不再作为当前主攻点
- 本轮不再扩散改 `query_builder`、`arbiter`、`final`

## 当前诊断结论

安装真实清单 smoke 集：

- 总样本：385
- 命中率：39.7%
- 错误：232

错误阶段分布：

- `retriever`: 149
- `ranker`: 73
- `ltr_ranker`: 10
- `candidate_arbiter`: 0
- `final_validator`: 0

`retriever miss` 已拆分：

- `keyword_miss`: 98
- `routing_miss`: 47
- `empty_result`: 4

## 本周执行顺序

### Step 1. 上 LTR Guard

目的：

- 只拦截 `LTR` 想推翻强结构化锚点 `top1` 的情况
- 不重写 LTR，不动其他层

规则：

- 实体精确匹配：`+4`
- 材质匹配：`+2`
- 连接方式匹配：`+2`
- authority 经验命中：`+3`
- 规格精确匹配：`+1`
- 初始阈值：`6.0`

产出：

- `src/ltr_ranker.py` 增加 guard
- trace 中保留 `ltr_guard`

### Step 2. 复跑 smoke eval

只看三件事：

- `ltr_ranker` 错误数是否下降
- 总命中率是否回升
- `LTR` 改对样本有没有被明显误拦

### Step 3. 处理 `keyword_miss`

前提：

- Step 2 稳住后再做

方法：

- 只处理 `keyword_miss=98`
- 先细分词面差异类型
- 再定向改 `query_builder`

### Step 4. 最后才碰 retriever 其他问题

- `routing_miss`
- `empty_result`

## 明确不做

- 不再继续散改排序算法
- 不同时改多个模块
- 不把 LLM 前移到主决策链
- 不重启一轮“规则系统重构”
