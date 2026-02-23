# Jarvis 重构草案（可直接交给 Claude 执行）

更新时间：2026-02-22

## 1. 目标

把当前贾维斯从“可运行”提升到“可持续演进”：

- 准确率优化可插拔（检索/重排/规则/经验互不耦合）
- 性能优化可量化（每层耗时可观测）
- 规则和经验库变更可回归（门禁自动化）
- CLI 与批处理入口稳定（不因单模块改动连锁失效）

## 2. 目标分层与职责

分 7 层，保持单向依赖（上层只依赖下层抽象）：

1. `orchestration` 编排层  
职责：流程控制、模式分发、并发调度、重试与降级。

2. `matching` 匹配决策层  
职责：融合召回结果、规则结果、经验结果，输出最终匹配。

3. `retrieval` 检索层  
职责：query 构建、召回、重排、候选融合。

4. `validation` 校验层  
职责：参数一致性、规则族档位、审核规则判错。

5. `learning` 学习层  
职责：经验写入、候选晋升、反馈闭环、方法卡片更新。

6. `repository` 数据访问层  
职责：QuotaDB/ExperienceDB 的统一读写接口与事务边界。

7. `interfaces` 接口层（CLI/Batch/API）  
职责：参数解析、输出格式、用户交互，不放业务决策。

## 3. 目录重排（目标结构）

```text
auto-quota/
  src/
    orchestration/
      pipeline_runner.py
      mode_router.py
    matching/
      decision_engine.py
      fallback_policy.py
      trace_model.py
    retrieval/
      query_builder.py
      candidate_retriever.py
      rerank_service.py
      fusion.py
    validation/
      param_validator_service.py
      rule_family_validator.py
      review_gate.py
    learning/
      experience_service.py
      promote_service.py
      method_card_service.py
    repository/
      quota_repo.py
      experience_repo.py
      run_report_repo.py
    interfaces/
      cli_main.py
      batch_entry.py
  tools/
    system_health_check.py
    jarvis_pipeline.py
```

说明：`src/match_engine.py`、`src/match_pipeline.py`、`src/match_core.py` 的职责最终拆分到 `orchestration + matching + retrieval + validation`。

## 4. 核心接口契约（先定义再迁移）

先建协议，不先大改逻辑。

### 4.1 MatchRequest / MatchResult

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class MatchRequest:
    bill_item: dict
    province: str
    mode: str  # search | agent
    use_experience: bool = True
    context: dict[str, Any] = field(default_factory=dict)

@dataclass
class MatchResult:
    bill_item: dict
    quotas: list[dict]
    confidence: int
    match_source: str
    explanation: str = ""
    alternatives: list[dict] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
```

### 4.2 检索服务接口

```python
class RetrieverService:
    def retrieve(self, req: MatchRequest) -> list[dict]:
        ...

class RerankService:
    def rerank(self, req: MatchRequest, candidates: list[dict]) -> list[dict]:
        ...
```

### 4.3 校验服务接口

```python
class ValidationService:
    def validate_candidates(self, req: MatchRequest, candidates: list[dict]) -> list[dict]:
        ...
    def validate_experience(self, req: MatchRequest, exp_result: MatchResult) -> MatchResult | None:
        ...
```

### 4.4 决策引擎接口

```python
class DecisionEngine:
    def decide(
        self,
        req: MatchRequest,
        candidates: list[dict],
        exp_result: MatchResult | None,
        rule_result: MatchResult | None,
    ) -> MatchResult:
        ...
```

## 5. 迁移顺序（低风险）

按“包裹式重构”执行，保持每步可回滚：

1. 增加新包与接口定义，不改现有调用路径。
2. 把旧逻辑先“搬函数不改语义”到新模块，旧入口继续调用。
3. 对外入口只留 `main.run()` 与 `tools/jarvis_pipeline.py`，内部改走新服务。
4. 删除旧重复路径前，先通过全量体检与回归集对比。

## 6. 每阶段验收门禁

每阶段必须满足：

- `python tools/system_health_check.py --mode full` 通过
- `tests/test_regression_fixes.py` 通过
- 新增架构回归测试通过（见第 7 节）
- 结果一致性检查通过（同一测试集 Top1 结果差异在阈值内）

## 7. 必加测试（防回归）

至少新增以下测试文件：

- `tests/test_decision_engine_contract.py`
- `tests/test_experience_validation_guard.py`
- `tests/test_query_builder_lamp_rules.py`
- `tests/test_pipeline_compatibility.py`

最小验收场景：

- 经验精确命中 + 规则族存在但提参失败，不得无校验放行。
- `应急疏散指示灯` 走标志/诱导灯族，不得落入荧光灯通道。
- `all` 模式在网络不可达时不崩溃，且给出明确 skip reason。
- 新旧路径在固定样本集上输出结构一致（字段齐全，排序稳定）。

## 8. Claude 执行清单（可直接复制）

```text
请按 docs/jarvis_refactor_blueprint.md 执行重构，要求：
1) 先补接口与测试，再迁移实现；
2) 每个阶段完成后运行 full health check 与回归测试；
3) 每次提交只改一个主题（接口、迁移、清理）；
4) 输出每阶段变更文件、风险点、回滚方式。
```

## 9. 非目标（本轮不做）

- 不更换模型供应商
- 不改 Excel 输出格式契约
- 不做数据库引擎迁移（仍用 SQLite）
- 不引入分布式服务拆分

## 10. 里程碑建议（两周）

第 1 周：

- 完成接口定义与新目录脚手架
- 迁移检索层与校验层
- 补齐关键回归测试

第 2 周：

- 迁移决策层与编排层
- 清理旧路径并保留兼容包装
- 跑全量评测并冻结 v1 架构
