# 阶段A Findings：架构与依赖

更新时间：2026-02-22  
适用项目：`auto-quota`

## 0. 本轮结论摘要

- 当前 `src/` 依赖图无显式循环依赖：`modules=57, edges=62, scc_gt1=0, self_cycles=0`。
- 但存在“新分层代码已落地、主入口仍走旧链路”的双轨状态，且有至少 1 个已知契约不一致点，后续接入时会触发运行时错误。
- 本阶段优先级建议：先修契约与入口一致性，再做功能性重构。

---

## 1. Findings（按严重级别）

### [P1] 新分层未接入生产主入口，形成双轨实现风险

- 证据：
  - `main.py:48` 直接从 `src.match_engine` 导入并使用旧核心 API。
  - `main.py:238` 直接调用 `init_search_components(...)`，`main.py:245` 直接调用 `match_by_mode(...)`。
  - 新分层模块存在但未被主入口使用：`src/orchestration/pipeline_runner.py:16`、`src/orchestration/mode_router.py:12`、`src/interfaces/cli_main.py:9`、`src/interfaces/batch_entry.py:9`。
  - `tools/system_health_check.py:160-164` 仅做 import smoke，不验证这些新层在真实运行路径中的行为。
- 影响：
  - 维护成本增大：修改逻辑时需要同时关注两套入口语义是否漂移。
  - 测试覆盖“看似存在”，但生产路径可能完全没走到新层。
- 建议修复：
  - 先定义“单一事实入口”（建议 `main.run`），新层只做兼容包裹，不并行演进业务语义。
  - 用 feature flag 将 `main.run` 切到 `PipelineRunner`，先灰度，再移除旧直连路径。
  - 增加 1 条集成测试，强制覆盖新入口真实调用链。

### [P1（潜在P0）] `PipelineRunner` 与 `init_search_components` 返回契约不一致

- 证据：
  - `src/orchestration/pipeline_runner.py:37-40` 假设 `init_search_components` 返回字典并按键取值：`result["searcher"]`、`result["validator"]`。
  - `src/match_engine.py:282` 实际返回 tuple：`return searcher, validator`。
- 影响：
  - 一旦主入口切换到 `PipelineRunner.init`，会在运行时因 tuple 按键访问而失败，直接中断流程。
- 建议修复：
  - 统一契约，二选一：
    - 保持旧 API：`PipelineRunner` 解包 tuple；
    - 或升级 `init_search_components` 返回 dict 并同步全链路调用方。
  - 补最小回归测试：验证 `PipelineRunner.init(...).is_ready is True`。

### [P1（潜在数据丢失）] 数据契约字段名与当前核心结果字段不一致

- 证据：
  - `src/contracts.py:45`、`src/contracts.py:67-69` 使用 `quota_name`。
  - 当前核心结果普遍使用 `name`：
    - `src/match_pipeline.py:193`、`src/match_pipeline.py:201`
    - `src/match_core.py:185`、`src/match_core.py:293`
    - `src/output_writer.py:915`
- 影响：
  - 若后续推广 `MatchResult.to_legacy_dict()`，可能出现定额名称被写空或映射错误（静默数据质量问题）。
- 建议修复：
  - 在契约层增加统一规范化（`name <-> quota_name`）并强制双向兼容。
  - round-trip 测试同时覆盖两种字段输入，避免静默退化。

### [P2] `scripts/system_health.bat` 收尾分支可读性/稳定性风险

- 证据：
  - `scripts/system_health.bat:118` 使用 `) else if "%RC%"=="2" (` 写法。
  - 同段含三路输出：`scripts/system_health.bat:116`、`scripts/system_health.bat:120`、`scripts/system_health.bat:125`。
- 影响：
  - 在 `cmd` 下可维护性差，后续修改容易引入“提示文案与退出码不一致”。
- 建议修复：
  - 改成显式嵌套 `if` 或标签分支（`goto`），保证分支语义单一且可测试。

### [P2] 存在跨层隐式耦合与全局状态依赖

- 证据：
  - `main.py:94` 直接写全局运行态：`config.set_current_province(...)`。
  - `main.py:135-136` 直接读取 `src.match_pipeline` 模块级计数器（`get_and_reset_review_rejection_count`）。
  - `src/match_pipeline.py:44`、`src/match_pipeline.py:47-51`、`src/match_pipeline.py:507-508` 依赖模块级全局计数。
- 影响：
  - 不利于并发/多任务场景；重复调用与嵌套调用下统计口径易混淆。
- 建议修复：
  - 通过 `RunContext/RunStats` 显式传递与汇总，逐步替代模块级全局可变状态。

---

## 2. 阶段A中已确认的“非问题/已改善点”

- 导入历史“仅按文件名唯一”问题在当前代码已迁移为按路径唯一：
  - `src/quota_db.py:106` 使用 `UNIQUE(file_path)`。
  - `src/quota_db.py:171-206` 包含从旧 `file_name` 唯一约束迁移逻辑。

---

## 3. 建议修复顺序（给 Claude 的执行批次）

1. 修复 `PipelineRunner` 契约不一致（先加最小复现测试，再改实现）。
2. 统一 `contracts` 与核心字段名映射（补 round-trip 回归）。
3. 将 `main.run` 接入新编排层（先开关灰度，再默认切换）。
4. 清理 `system_health.bat` 分支语义并补脚本级验收命令。
5. 收口全局状态：将审核拦截计数并入显式运行上下文。

---

## 4. 可直接发给 Claude 的指令

```text
请按 docs/阶段A_架构与依赖findings.md 执行修复，严格按批次进行：
1) 每个批次先写最小复现测试，再改实现；
2) 每批次只解决一个主题，不跨主题混改；
3) 每批次结束提供 validation（命令、结果、未覆盖风险）；
4) 保持 Excel 输出契约不变，不改用户可见字段语义。
```

