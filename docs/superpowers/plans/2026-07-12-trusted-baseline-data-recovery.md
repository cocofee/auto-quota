# Trusted Baseline Data Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first trustworthy accuracy datasets from human corrections and OSS XML provenance, then restore an evaluation-only production candidate source without presenting reconstructed data as the original province database.

**Architecture:** Keep the existing read-only evaluator unchanged as the metric layer. Add deterministic dataset-export and asset-audit tools around `experience.db`, the OSS XML mother directory, and `national_index.sqlite`; any reconstructed province database lives under `output/accuracy_baseline/` and is activated only for the evaluation process. Primary, OSS diagnostic, and reconstructed-production results remain explicitly separated.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, `pathlib`, `hashlib`, `json`, existing XML parser, existing accuracy-baseline package, pytest.

**Repository Constraints:** Do not write `experience.db`, `national_index.sqlite`, production configuration, `db/provinces`, models, or online task state. Do not add dependencies. Do not commit unless the user explicitly authorizes it.

---

## Evidence Checkpoint: 2026-07-12

> 2026-08-21 口径修正：下述 261 条 `user_correction` 全部来自安徽安装、单一来源族，只是高可信人工切片，不是完整系统基线。历史实验数字仅用于该切片的离线诊断，不得外推为跨省、跨来源族或全系统准确率。

- `db/provinces` contains only a 4 KB 安徽 `quota.db`; it has no usable production quota corpus.
- No `quota.db` exists anywhere on `D:\`; `D:\广联达临时文件\2026` contains only one unrelated `Thumbs.db`.
- `data/goal_search/national_index.sqlite` contains 1,481,806 quota rows, including 18,096 rows for 安徽安装, but preserves only a subset of the production `quotas` schema.
- `db/common/experience.db` contains 261 `user_correction` authority rows, all for 安徽安装.
- The same database contains 103,651 `oss_import` candidate rows across 福建 and 浙江 quota systems.
- The OSS mother directory contains 2,476 XML paths, 1,250 unique `(file_name, size)` identities, and 1,226 canonical files under `by_province`.
- All 103,651 OSS experience rows map back to 289 original XML identifiers after removing the `oss_YYYYMMDD_HHMM_` import prefix.
- All 827 OSS `(project_name, province)` groups agree with their `by_province` directory; this is sufficient for an auditable project-level split.

## Dataset Policy

| Dataset | Source | Role | Allowed headline metrics |
| --- | --- | --- | --- |
| `primary_v0` | 261 `user_correction` authority rows | 安徽人工纠正切片 | 仅报告切片 Top1/Top3/Recall；不得作为系统总体准确率或生产上线依据 |
| `oss_diagnostic_v1` | OSS XML or traceable `oss_import` rows | Recall, conditional ranking, taxonomy and parameter diagnostics | Recall@25/80, conditional Top1, MRR, slice metrics |
| `historical_stress_v0` | Existing failure-oriented samples | Regression pressure test | Repair count and new regression count only |

The reconstructed province source is never called the original production database. Its results must carry `asset_mode=reconstructed_from_national_index` in runtime metadata and reports.

### Task 1: Export the Human Primary Dataset

**Files:**
- Create: `eval/accuracy_baseline/data_audit.py`
- Create: `tools/build_accuracy_datasets.py`
- Test: `tests/test_accuracy_baseline_data_audit.py`

- [ ] **Step 1: Write failing tests for authority-only export**

Create a temporary SQLite fixture containing `user_correction`, `oss_import`, authority, and candidate rows. Assert that `export_primary_cases()` accepts only non-disputed authority rows with province and quota IDs, preserves `project_name`, and reports every rejection reason.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `pytest tests/test_accuracy_baseline_data_audit.py::test_export_primary_cases_uses_only_human_authority_rows -q`

Expected: FAIL because `eval.accuracy_baseline.data_audit` does not exist.

- [ ] **Step 3: Implement a read-only exporter**

Add:

```python
def export_primary_cases(
    experience_db: Path,
    output_path: Path,
) -> DatasetExportReport:
    ...
```

Open SQLite with URI `mode=ro`. Select only `source='user_correction'`, `layer='authority'`, and non-disputed rows. Normalize `quota_ids` into `oracle_quota_ids`; use a stable hash of the normalized row as `sample_id`; set `source_family='human_user_correction'`; never update the source database.

- [ ] **Step 4: Expose the primary export CLI**

Add `--experience-db`, `--primary-output`, and `--summary-output` to `tools/build_accuracy_datasets.py`. JSON output must include accepted count, rejection counts, province counts, project counts, and SHA-256.

- [ ] **Step 5: Run focused tests and the real read-only export**

Run:

```powershell
pytest tests/test_accuracy_baseline_data_audit.py -q
python tools/build_accuracy_datasets.py --experience-db db/common/experience.db --primary-output output/accuracy_baseline/datasets/primary_v0.jsonl --summary-output output/accuracy_baseline/datasets/data_manifest.json
```

Expected: 261 accepted authority cases unless a row is rejected with an explicit reason.

### Task 2: Export an Auditable OSS Diagnostic Split

**Files:**
- Modify: `eval/accuracy_baseline/data_audit.py`
- Modify: `tools/build_accuracy_datasets.py`
- Test: `tests/test_accuracy_baseline_data_audit.py`

- [ ] **Step 1: Write failing provenance and split tests**

Assert that `oss_20260528_1847_<uuid>.XML` resolves to `<uuid>.XML`, that duplicate file paths collapse to one canonical project identity, and that one `project_id` cannot appear in more than one split.

- [ ] **Step 2: Implement deterministic OSS provenance resolution**

Add:

```python
def resolve_oss_project(project_name: str, xml_root: Path) -> OssProjectProvenance:
    ...

def export_oss_diagnostic_cases(
    experience_db: Path,
    xml_root: Path,
    output_dir: Path,
    split_seed: str,
) -> DatasetExportReport:
    ...
```

Use the original XML identifier as `project_id`, the canonical XML path as `source`, and `oss_xml/<province_code>/<xml_format>` as `source_family`. Split by stable project hash, never by individual row. Reject missing, ambiguous, province-inconsistent, or unparseable provenance instead of guessing.

- [ ] **Step 3: Add leakage assertions**

The manifest must show zero project overlap across train/dev/eval and counts by province, source-family, XML format, specialty, and quota family. Fail the command if overlap is nonzero.

- [ ] **Step 4: Run tests and build the OSS diagnostic manifest**

Run:

```powershell
pytest tests/test_accuracy_baseline_data_audit.py -q
python tools/build_accuracy_datasets.py --experience-db db/common/experience.db --oss-xml-root 'D:\广联达临时文件\oss_samples' --oss-output-dir output/accuracy_baseline/datasets/oss_v1 --summary-output output/accuracy_baseline/datasets/data_manifest.json
```

Expected: every accepted row carries non-empty `province`, `source`, `source_family`, and `project_id`; project overlap is zero.

### Task 3: Build an Evaluation-Only Province Source

**Files:**
- Create: `eval/accuracy_baseline/reconstructed_assets.py`
- Create: `tools/materialize_eval_province_db.py`
- Modify: `tools/run_accuracy_baseline.py`
- Modify: `eval/accuracy_baseline/runner.py`
- Test: `tests/test_accuracy_baseline_reconstructed_assets.py`
- Test: `tests/test_accuracy_baseline_runner.py`

- [x] **Step 1: Write failing schema and isolation tests**

Assert that materialization reads `national_index.sqlite` with `mode=ro`, writes only below the requested output root, creates the production-compatible `quotas` columns, and refuses a destination inside `db/provinces`.

- [x] **Step 2: Implement the minimal deterministic mapping**

Map `quota_id`, `name`, `unit`, `specialty`, `chapter`, `dn`, `cable_section`, `material`, and `connection` directly. Use `normalized_text` as `search_text`; derive `book` with the existing quota-ID convention; leave unavailable production-only fields null. Write an asset manifest containing source hash, source row count, target row count, null-field counts, and `asset_mode=reconstructed_from_national_index`.

- [x] **Step 3: Add oracle-coverage and schema gates**

Before evaluation, require:

- target row count equals the selected national-index province row count;
- every `primary_v0` oracle exists in the reconstructed `quotas` table;
- duplicate `(quota_id, chapter)` count is zero;
- `search_text`, `book`, and `specialty` non-empty rates are reported;
- unavailable reconstructed fields are listed in the report.

Failure of any required gate blocks reconstructed-production metrics but does not block Goal Shadow evaluation.

- [x] **Step 4: Add evaluation-only path injection**

Add CLI option `--provinces-db-dir`. Set `config.PROVINCES_DB_DIR` only inside the CLI process before providers run. Record the resolved directory and asset mode in runtime metadata; do not edit `config.py` or write `db/provinces`.

- [x] **Step 5: Run focused tests and materialize 安徽安装**

Run:

```powershell
pytest tests/test_accuracy_baseline_reconstructed_assets.py tests/test_accuracy_baseline_runner.py -q
python tools/materialize_eval_province_db.py --national-index data/goal_search/national_index.sqlite --province '安徽省安装工程计价定额(2018)' --output-root output/accuracy_baseline/reconstructed_assets --primary output/accuracy_baseline/datasets/primary_v0.jsonl
```

Expected: gates pass or the tool exits nonzero with exact failed-gate evidence. No repository database changes.

### Task 4: Establish the First Accuracy Numbers

> **2026-08-21 失效说明：** 本节 2026-07-13 的数值产生于旧评测契约，当时多定额未区分 `any/all`、最终候选排序与最终输出集合混用、Provider/结果缺失可能被排除在分母外。因此这些数值只能保留为历史诊断证据，不能再作为当前算法门槛、回归基线或系统准确率。重新导出带 `oracle_semantics` 的数据并通过覆盖合同前，不得重跑或发布“261 条完整基线”。

**Files:**
- Output only: `output/accuracy_baseline/baseline_v0/`

- [x] **Step 1: Run Goal Shadow on the human primary set**

Run:

```powershell
python tools/run_accuracy_baseline.py --primary output/accuracy_baseline/datasets/primary_v0.jsonl --providers goal_shadow --provinces-db-dir output/accuracy_baseline/reconstructed_assets/provinces --output-dir output/accuracy_baseline/baseline_v0/goal_primary
```

Expected: all evaluable cases use human authority labels; report exclusions separately.

- [x] **Step 2: Run reconstructed production only if Task 3 gates pass**

Run:

```powershell
python tools/run_accuracy_baseline.py --primary output/accuracy_baseline/datasets/primary_v0.jsonl --providers search_core,goal_shadow --provinces-db-dir output/accuracy_baseline/reconstructed_assets/provinces --output-dir output/accuracy_baseline/baseline_v0/reconstructed_comparison
```

Expected: runtime metadata explicitly identifies reconstructed assets. These numbers are diagnostic, not canonical production baseline numbers.

- [ ] **Step 3: Run OSS diagnostic evaluation split only**

Use only the held-out OSS eval JSONL. Keep train and dev projects unavailable to metric aggregation. Report Recall@25/80, conditional Top1, MRR, route-filter oracle loss, taxonomy false veto, parameter hard-fail, and slices.

Blocked at the asset gate on 2026-07-13. 福建安装、浙江安装和浙江市政 pass oracle coverage; 福建房建、福建市政、福建园林和浙江园林 fail with 25, 53, 82 and 97 missing unique oracle IDs respectively. Do not run or report a combined OSS accuracy number until the version mismatch is resolved or the report explicitly excludes failed provinces.

- [x] **Step 4: Define the first algorithm experiment from measured loss**

Select exactly one P1 experiment based on the largest measured error source:

- retrieval miss: production ∪ Goal Shadow candidates, then rerank;
- route-filter loss: preserve oracle-compatible candidate families;
- rerank loss: train/evaluate a source-aware reranker using train/dev projects only;
- parameter hard-fail: change hard veto to calibrated penalty in shadow mode.

Do not select an experiment before the baseline report identifies the dominant loss stage.

Measured decision on 2026-07-13: select `production_goal_candidate_union_shadow_v1`. Production recalls 102/261 cases, Goal Shadow recalls 176/261, and the deduplicated union recalls 199/261; Goal contributes 97 cases not recalled by production. Merge candidates by `quota_id`, preserve production features, add Goal source/score metadata, and send the merged set through the existing production rerank and decision stages. Goal Shadow must not replace production Top1 directly. Acceptance requires merged Recall@80 to match the measured 199/261 union ceiling, production Top1 not to regress from 63/261, and final-stage bad flips not to exceed the current 12.

Shadow result on 2026-07-13: reject `production_goal_candidate_union_shadow_v1` for production use. The frozen standalone Production pool recalls 102/261 cases, Goal Top80 recalls 176/261, the raw union recalls 199/261, and Goal contributes 97 unique recalled cases with zero local materialization gaps. After the unchanged production filtering and ranking chain, rankable recall falls to 103/261, final Top1 is 61/261, final Top3 is 89/261, and final-stage bad flips remain 12. The candidate-union contract passes, but the final Top1 safety gate fails because 61 is below the 63-case Production baseline. Keep the implementation offline-only; the next algorithm investigation should isolate route/filter and reranker losses without changing production configuration.

40/10 budget shadow result on 2026-07-13: accept `production_40_goal_10` as the next offline algorithm baseline, not as a production enablement decision. Production recall remains 102/261, Goal Top80 recall remains 176/261, raw union recall remains 199/261, and local Goal materialization gaps remain zero. Source-balanced cascade budgeting raises rankable recall from 103/261 to 133/261, final Top1 from 61/261 to 71/261, and final Top3 from 89/261 to 101/261 while reducing final-stage bad flips from 12 to 6. The policy executes for 260 searchable cases; the remaining case exits through `input_gate_abstain` before search. This passes the predefined safety gates of final Top1 >= 63 and final-stage bad flips <= 12. Keep the policy offline-only until a separate production-readiness review authorizes any runtime change.

### Task 5: Verification Checkpoint

**Files:**
- Test: `tests/test_accuracy_baseline_*.py`
- Verify: generated manifests and reports

- [ ] **Step 1: Run all accuracy-baseline tests**

Run: `pytest tests/test_accuracy_baseline_*.py tests/test_real_eval_tools.py -q`

Expected: PASS; unrelated failures are reported without unrelated fixes.

- [ ] **Step 2: Verify read-only and working-tree boundaries**

Run:

```powershell
git diff --check
git status --short
```

Confirm that no file under `db/provinces`, no source database, no model, and no production configuration changed.

- [ ] **Step 3: Stop for checkpoint review**

Report primary and OSS accepted/rejected counts, provenance leakage checks, reconstructed-asset gate results, Goal Shadow metrics, reconstructed-production diagnostics if allowed, and the single recommended P1 algorithm experiment.
