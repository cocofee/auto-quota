# 2026-03-29 wrong_book 复核结论

## 本轮动作

围绕 `wrong_book` 做了两件事：

1. 给检索主链补上 `resolved_books` 实证 trace
2. 收掉一类明确越权的路由收窄
   - 非标准数字册省份里
   - 当路由只给出宽泛大类 `A/D/E`
   - 不再在 `match_core` 里二次猜测成若干数字册
   - 改为 `open_search`

## 5 省聚焦评测结果

评测文件：

- `output/real_eval/routing_focus_after_broad_group_guard.summary.json`
- `output/real_eval/routing_focus_after_broad_group_guard.details.jsonl`

结果：

- 总体：`94 -> 31.9%`
- 电力技改：`85.0%`，未被打坏
- 黑龙江：`30.0%`，命中率没有拉升
- 宁夏：`20.0% -> 15.0%`

结论：

- 这次改动没有带来直接分数收益
- 但把 retriever 病因从“伪 routing_miss”里剥出来了

## 用新诊断复核后的真实分布

诊断文件：

- `output/real_eval/diag/routing_focus_after_broad_group_guard_retriever_diag_v2.summary.json`
- `output/real_eval/diag/routing_focus_after_broad_group_guard_retriever_diag_v2.details.jsonl`

新的 retriever miss 分布：

- `knowledge_not_used`: `30`
- `routing_miss`: `1`
- `index_miss`: `1`

分省：

- 黑龙江：`10` 条全部转成 `knowledge_not_used`
- 宁夏：`13` 条是 `knowledge_not_used`
- 上海安装：`5` 条 `knowledge_not_used`，`1` 条 `routing_miss`
- 上海园林：`2` 条 `knowledge_not_used`，`1` 条 `index_miss`

## 这说明什么

之前把很多样本看成 `wrong_book`，是因为诊断还在读旧的 router 意图。

现在改成读检索实证 trace 之后，能看到：

- 黑龙江这批样本其实已经不是“搜错册”
- 它们是 `open_search` 了
- 但 oracle 还是没进候选池
- 同时 `experience_exact_hit = true`

所以当前真正暴露出来的是：

- 资产明明有
- 但主召回链没有把它们吃进去

也就是 `knowledge_not_used`，不是 `wrong_book`

## 对主架构的影响

主架构顺序需要更新为：

1. `wrong_book`
   - 已基本打穿，只剩极少量残留
2. `knowledge_not_used`
   - 现在成为 retriever 主病灶
3. `synonym_gap`
4. `wrong_tier`

## 下一步

不要再继续扩路由算法。

下一步应该只做一件事：

- 把 `experience / universal_kb` 的 exact overlap 前移到主召回链
- 先做 retriever 级直通或强召回
- 不让这类样本在 open search 里空跑
