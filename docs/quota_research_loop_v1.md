# Quota Research Loop V1

## 目标

把当前 `benchmark -> 错题分析 -> 小步修改 -> keep/discard` 的做法，抽象成一个可持续运行、可恢复、可回滚的研究闭环。

这个闭环不是“无限自动写回系统”，而是“持续自动提出候选优化，并用实验验证后保留有效修改”。

## 核心原则

### 1. 做受控闭环，不做失控闭环

- 自动化的内容：
  - 错题/低置信题收集
  - 方向队列热启动
  - 小样本快筛
  - 全量复核
  - keep / discard / crash 记录
- 保守处理的内容：
  - 经验库写回
  - 权威层晋升
  - 大模型裁决结果入主库

### 2. 优化闭环快，数据闭环慢

- 优化闭环：日常持续跑
- 数据晋升：按批次、带 health/conflict/confidence 护栏再做

### 3. 先快筛，再全量

- 所有新想法先跑 `fast-screen`
- 只有快筛变好，才有资格跑 `full benchmark`
- 快筛目标：`1~3 分钟`
- 全量目标：`<= 10 分钟`

### 4. Git 只保留当前最好状态

- 每轮实验从当前稳定点起步
- 变好：保留并前进
- 变差：回滚到本轮开始前
- crash：最多做 1~2 次低级错误修复，否则直接放弃

### 5. Git 护栏要显式开启

- `start --require-clean-git`：要求起步前工作区干净，适合准备进入自动 keep/discard 时使用
- `execute --experiment-id <id>`：基于已经 `start` 的实验继续跑，不重新生成 base 元数据
- `execute --git-reset-on-discard`：仅当该实验 `base_dirty=false` 时，discard/crash 自动回到 `base_commit`
- `execute --git-commit-on-keep`：仅在 keep/bootstrap 且工作区有变更时，按模板白名单自动提交
- 自动提交后，会把新 commit 写入 `quota_research_loop_state.json` 的 `best_commit`

## 适用范围

### 适合长期闭环的部分

- `src/query_builder.py` 的对象模板 / 搜索词模板
- `src/match_pipeline.py` 的排序守卫
- `src/hybrid_searcher.py` 的召回变体与融合策略
- `tools/run_benchmark.py` 的快筛与评测能力

### 不适合自动闭环写回的部分

- 经验库权威层自动晋升
- `health --fix` 一类批量修库操作
- 真实文件上大模型直接写回经验库

## 评估分层

### L1 快筛

用于方向试错，命令形式例如：

```bash
python tools/run_benchmark.py --json-only --install-only --item-keyword 配电箱 --item-keyword 配电柜 --max-items-per-province 20
```

### L2 全量安装卷

用于确认安装主线是否真的前进：

```bash
python tools/run_benchmark.py --json-only --install-only
```

### L3 真实文件

微信群/实际清单文件不作为夜间快迭代主评测，而是作为业务对照验证：

- 主流程：纯搜索
- 疑难题：大模型兜底
- 低置信结果：人工池

## keep / discard / crash 规则

### keep

- 快筛上涨，且
- 全量不下降，且
- 关键主省份无明显回撤

### discard

- 快筛下降
- 或全量下降
- 或脏数据/鲁棒性明显变差

### crash

- 超时
- OOM
- 明显 bug 且 1~2 次简单修复后仍失败

## 方向模板

V1 先只支持模板化方向，不做全仓自由改写：

1. `distribution_box`
2. `conduit`
3. `cable_split`
4. `lamp_install`
5. `valve_family`（保守，需分省/分册）

每个模板定义：

- 允许修改的文件白名单
- 快筛命令
- 全量命令
- 判定说明

## 状态文件

### 1. 轮次状态

- `output/temp/autoresearch_state.json`
- 负责：方向队列 + round keep/discard + 边际收益检测

### 2. loop 状态

- `output/temp/quota_research_loop_state.json`
- 负责：最佳 commit、当前模板、最近实验、watchdog 状态

### 3. 实验日志

- `output/temp/quota_research_experiments.jsonl`
- 每轮一条 append-only 记录

## V1 执行时序

1. 从 `autoresearch_manager` 读取 `active direction`
2. 解析成模板
3. 生成本轮实验计划
4. 修改白名单文件
5. 跑快筛
6. 若快筛通过，跑全量
7. 记录 `keep / discard / crash`
8. 更新 priority queue，进入下一轮

## 和真实文件联动

真实微信群文件进入闭环时，建议遵循：

- `benchmark` 决定“能不能留在主干”
- `真实文件` 决定“下一轮应该优化什么”

即：

- 用真实文件找方向
- 用 benchmark 决定 keep/discard

这样不会被单个文件分布带偏。
