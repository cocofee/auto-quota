# Accuracy Baseline and Candidate Lifecycle Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only evaluation framework that compares the production matcher and GoalSearcher with one dataset contract, one candidate-lifecycle contract, and one set of recall, ranking, flip, slice, and provider-comparison metrics.

**Architecture:** Add a new offline-only package under `eval/accuracy_baseline/`. Providers adapt existing production and Goal APIs into immutable contracts; lifecycle normalization and metric computation remain provider-independent; a CLI writes deterministic JSON/JSONL/CSV artifacts. Production matching behavior, configuration, databases, and online task execution remain unchanged.

**Tech Stack:** Python 3.11+, standard library dataclasses/enum/json/csv/hashlib/subprocess, existing `tools.run_real_eval`, existing `src.goal_search.GoalSearcher`, pytest.

**Design Reference:** `docs/superpowers/specs/2026-07-12-accuracy-baseline-lifecycle-evaluation-design.md`

**Repository Constraints:** Do not commit unless the user explicitly authorizes it. Do not write ExperienceDB, AccuracyTracker, production configuration, or online task state. Do not add dependencies.

---

## File Structure

Create the following focused modules:

- `eval/accuracy_baseline/__init__.py`: public exports only.
- `eval/accuracy_baseline/contracts.py`: immutable evaluation, candidate, lifecycle, decision, provider, and dataset result contracts.
- `eval/accuracy_baseline/datasets.py`: JSONL loading, field normalization, dataset-kind validation, rejection accounting, and content hashing.
- `eval/accuracy_baseline/lifecycle.py`: production-detail and Goal-hit normalization into the common lifecycle contract.
- `eval/accuracy_baseline/metrics.py`: per-case metrics, aggregate metrics, stage flips, slices, and provider union comparison.
- `eval/accuracy_baseline/providers.py`: `CandidateProvider` protocol, production adapter, Goal Shadow adapter, dependency injection, and read-only failure isolation.
- `eval/accuracy_baseline/reporting.py`: deterministic JSON, JSONL, and CSV writers.
- `eval/accuracy_baseline/runner.py`: dataset/provider orchestration and runtime metadata.
- `tools/run_accuracy_baseline.py`: thin CLI.

Modify one existing read-only evaluation helper:

- `tools/run_real_eval.py`: preserve full recall IDs and rank-stage fields already emitted by the matcher.

Add focused tests:

- `tests/test_accuracy_baseline_contracts.py`
- `tests/test_accuracy_baseline_datasets.py`
- `tests/test_accuracy_baseline_lifecycle.py`
- `tests/test_accuracy_baseline_metrics.py`
- `tests/test_accuracy_baseline_providers.py`
- `tests/test_accuracy_baseline_reporting.py`
- `tests/test_accuracy_baseline_runner.py`

---

### Task 1: Preserve Production Evaluation Trace Fields

**Files:**
- Modify: `tools/run_real_eval.py:273`
- Test: `tests/test_real_eval_tools.py`

- [ ] **Step 1: Write the failing trace-preservation test**

Append this test to `tests/test_real_eval_tools.py`:

```python
def test_detail_from_result_preserves_full_recall_and_rank_stage_fields():
    record = {
        "sample_id": "trace-1",
        "province": "demo-province",
        "source": "user_correction",
        "project_name": "demo-project",
        "bill_name": "demo bill",
        "bill_text": "demo bill DN50",
        "specialty": "C10",
        "oracle_quota_ids": ["Q-30"],
        "oracle_quota_names": ["correct quota"],
    }
    recall_ids = [f"Q-{index}" for index in range(1, 81)]
    result = {
        "quotas": [
            {"quota_id": "Q-1", "name": "selected"},
            {"quota_id": "Q-2", "name": "second"},
            {"quota_id": "Q-3", "name": "third"},
        ],
        "all_candidate_ids": recall_ids[:20],
        "recall_topk_ids": recall_ids,
        "pre_ltr_top1_id": "Q-1",
        "post_ltr_top1_id": "Q-2",
        "post_ltr_structural_top1_id": "Q-3",
        "post_cgr_top1_id": "Q-4",
        "post_arbiter_top1_id": "Q-5",
        "post_explicit_top1_id": "Q-6",
        "post_anchor_top1_id": "Q-7",
        "post_final_top1_id": "Q-8",
        "rank_stage_trace": [
            {"name": "ltr", "top1_id": "Q-2"},
            {"name": "post_ltr_structural_ranker", "top1_id": "Q-3"},
        ],
        "match_source": "search",
        "confidence": 70,
    }

    detail = _detail_from_result(record, result)

    assert detail["recall_topk_ids"] == recall_ids
    assert detail["final_quota_ids"] == ["Q-1", "Q-2", "Q-3"]
    assert detail["post_ltr_structural_top1_id"] == "Q-3"
    assert detail["rank_stage_trace"] == result["rank_stage_trace"]
```

- [ ] **Step 2: Run the test and verify the missing fields fail**

Run:

```powershell
pytest tests/test_real_eval_tools.py::test_detail_from_result_preserves_full_recall_and_rank_stage_fields -q
```

Expected: FAIL with `KeyError: 'recall_topk_ids'`.

- [ ] **Step 3: Preserve the existing result fields without changing matching**

In `_detail_from_result`, add these keys to the returned dictionary:

```python
        "recall_topk_ids": [
            str(value).strip()
            for value in (result.get("recall_topk_ids") or result.get("all_candidate_ids") or [])
            if str(value).strip()
        ],
        "final_quota_ids": [
            str(quota.get("quota_id") or "").strip()
            for quota in quotas
            if str(quota.get("quota_id") or "").strip()
        ],
        "post_ltr_structural_top1_id": str(
            result.get("post_ltr_structural_top1_id", result.get("post_ltr_top1_id", "")) or ""
        ),
        "rank_stage_trace": list(result.get("rank_stage_trace") or []),
```

Do not change `all_candidate_ids`, existing diagnosis, stage selection, or runtime profiles.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
pytest tests/test_real_eval_tools.py -q
```

Expected: all tests in the file PASS.

---

### Task 2: Define Immutable Evaluation Contracts

**Files:**
- Create: `eval/accuracy_baseline/__init__.py`
- Create: `eval/accuracy_baseline/contracts.py`
- Test: `tests/test_accuracy_baseline_contracts.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/test_accuracy_baseline_contracts.py`:

```python
from dataclasses import FrozenInstanceError

import pytest

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    DatasetKind,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)


def test_eval_case_normalizes_oracles_and_exposes_bill_text():
    case = EvalCase(
        case_id="case-1",
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Valve",
        bill_text="DN50 threaded",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-1", "Q-2"),
        source_family="user_correction",
        project_id="project-a",
    )

    assert case.query_text == "Valve DN50 threaded"
    assert case.oracle_set == {"Q-1", "Q-2"}
    with pytest.raises(FrozenInstanceError):
        case.province = "changed"


def test_provider_result_uses_fixed_lifecycle_and_decision_contracts():
    candidate = CandidateSnapshot(
        quota_id="Q-1",
        name="Quota",
        unit="set",
        province="demo",
        provider="production",
        source="hybrid",
        stage=LifecycleStage.RETRIEVED,
        rank=1,
    )
    stage = StageSnapshot(
        stage=LifecycleStage.RETRIEVED,
        emitted=True,
        candidates=(candidate,),
        top1_id="Q-1",
    )
    result = ProviderResult(
        case_id="case-1",
        provider_name="production",
        status=ProviderStatus.OK,
        final_quota_ids=("Q-1",),
        confidence=0.9,
        lifecycle=(stage,),
        decisions=(DecisionSnapshot(name="final", top1_id="Q-1"),),
    )

    assert result.retrieved_ids == ("Q-1",)
    assert result.final_top1_id == "Q-1"
```

- [ ] **Step 2: Run the tests and verify imports fail**

Run:

```powershell
pytest tests/test_accuracy_baseline_contracts.py -q
```

Expected: FAIL with `ModuleNotFoundError: eval.accuracy_baseline`.

- [ ] **Step 3: Implement the complete contract module**

Create `eval/accuracy_baseline/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class DatasetKind(StrEnum):
    PRIMARY = "primary"
    OSS_DIAGNOSTIC = "oss_diagnostic"
    HISTORICAL_STRESS = "historical_stress"


class LifecycleStage(StrEnum):
    RETRIEVED = "retrieved"
    ROUTE_FILTERED = "route_filtered"
    RERANKED = "reranked"
    VALIDATED = "validated"
    SELECTED = "selected"
    POSTPROCESSED = "postprocessed"


class ProviderStatus(StrEnum):
    OK = "ok"
    MISSING_ORACLE = "missing_oracle"
    PROVINCE_UNAVAILABLE = "province_unavailable"
    ORACLE_NOT_IN_LOCAL_DB = "oracle_not_in_local_db"
    TRACE_INCOMPLETE = "trace_incomplete"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class EvalCase:
    case_id: str
    dataset_kind: DatasetKind
    province: str
    bill_name: str
    bill_text: str
    unit: str
    specialty: str
    oracle_quota_ids: tuple[str, ...]
    source_family: str
    project_id: str
    source: str = ""
    split: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def query_text(self) -> str:
        return " ".join(value for value in (self.bill_name, self.bill_text) if value).strip()

    @property
    def oracle_set(self) -> set[str]:
        return set(self.oracle_quota_ids)

    def to_record(self) -> dict[str, Any]:
        return {
            "sample_id": self.case_id,
            "province": self.province,
            "bill_name": self.bill_name,
            "bill_text": self.bill_text,
            "unit": self.unit,
            "specialty": self.specialty,
            "oracle_quota_ids": list(self.oracle_quota_ids),
            "source": self.source,
            "source_family": self.source_family,
            "project_name": self.project_id,
            **dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    quota_id: str
    name: str
    unit: str
    province: str
    provider: str
    source: str
    stage: LifecycleStage
    rank: int | None
    scores: Mapping[str, float] = field(default_factory=dict)
    family: str = ""
    book: str = ""
    param_match: bool | None = None
    hard_conflicts: tuple[str, ...] = ()
    drop_reason: str = ""
    raw_stage: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageSnapshot:
    stage: LifecycleStage
    emitted: bool
    candidates: tuple[CandidateSnapshot, ...] = ()
    top1_id: str = ""
    raw_stage_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    name: str
    top1_id: str
    emitted: bool = True
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProviderError:
    code: str
    message: str
    province: str = ""


@dataclass(frozen=True, slots=True)
class ProviderResult:
    case_id: str
    provider_name: str
    status: ProviderStatus
    final_quota_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    lifecycle: tuple[StageSnapshot, ...] = ()
    decisions: tuple[DecisionSnapshot, ...] = ()
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[ProviderError, ...] = ()
    raw_trace: Mapping[str, Any] = field(default_factory=dict)

    @property
    def final_top1_id(self) -> str:
        return self.final_quota_ids[0] if self.final_quota_ids else ""

    @property
    def retrieved_ids(self) -> tuple[str, ...]:
        for snapshot in self.lifecycle:
            if snapshot.stage == LifecycleStage.RETRIEVED and snapshot.emitted:
                return tuple(candidate.quota_id for candidate in snapshot.candidates)
        return ()
```

Create `eval/accuracy_baseline/__init__.py`:

```python
from .contracts import (
    CandidateSnapshot,
    DatasetKind,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)

__all__ = [
    "CandidateSnapshot",
    "DatasetKind",
    "DecisionSnapshot",
    "EvalCase",
    "LifecycleStage",
    "ProviderResult",
    "ProviderStatus",
    "StageSnapshot",
]
```

- [ ] **Step 4: Run the contract tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_contracts.py -q
```

Expected: 2 tests PASS.

---

### Task 3: Load and Validate the Three Dataset Kinds

**Files:**
- Create: `eval/accuracy_baseline/datasets.py`
- Test: `tests/test_accuracy_baseline_datasets.py`

- [ ] **Step 1: Write failing loader tests**

Create `tests/test_accuracy_baseline_datasets.py`:

```python
import json

from eval.accuracy_baseline.contracts import DatasetKind
from eval.accuracy_baseline.datasets import load_dataset


def test_load_dataset_normalizes_fields_and_reports_rejections(tmp_path):
    path = tmp_path / "cases.jsonl"
    rows = [
        {
            "sample_id": "1",
            "province": "demo",
            "bill_name": "Valve",
            "bill_text": "DN50",
            "unit": "set",
            "specialty": "C10",
            "oracle_quota_ids": ["Q-1", "Q-1", "Q-2"],
            "source": "user_correction",
            "source_family": "human",
            "project_name": "project-a",
        },
        {"sample_id": "2", "province": "demo", "bill_name": "No oracle"},
        {"sample_id": "3", "oracle_quota_ids": ["Q-3"]},
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    loaded = load_dataset(path, DatasetKind.PRIMARY)

    assert len(loaded.cases) == 1
    assert loaded.cases[0].oracle_quota_ids == ("Q-1", "Q-2")
    assert loaded.rejection_counts == {"missing_oracle": 1, "missing_province": 1}
    assert loaded.total_rows == 3
    assert len(loaded.content_sha256) == 64


def test_oss_dataset_requires_source_family_and_project_provenance(tmp_path):
    path = tmp_path / "oss.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "oss-1",
                "province": "demo",
                "bill_name": "Pipe",
                "oracle_quota_ids": ["Q-1"],
                "source_family": "",
                "project_name": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_dataset(path, DatasetKind.OSS_DIAGNOSTIC)

    assert loaded.cases == ()
    assert loaded.rejection_counts == {"missing_provenance": 1}
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run:

```powershell
pytest tests/test_accuracy_baseline_datasets.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the deterministic loader**

Create `eval/accuracy_baseline/datasets.py` with:

```python
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import DatasetKind, EvalCase


@dataclass(frozen=True, slots=True)
class DatasetLoadResult:
    path: Path
    dataset_kind: DatasetKind
    cases: tuple[EvalCase, ...]
    total_rows: int
    rejection_counts: dict[str, int]
    content_sha256: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = value.split("|")
        value = parsed
    values: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        text = _clean(item)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _case_from_row(row: dict[str, Any], kind: DatasetKind, index: int) -> tuple[EvalCase | None, str]:
    province = _clean(row.get("province") or row.get("quota_province"))
    if not province:
        return None, "missing_province"
    oracles = _string_tuple(
        row.get("oracle_quota_ids")
        or row.get("expected_quota_ids")
        or row.get("expected_ids")
        or row.get("quota_ids")
    )
    if not oracles:
        return None, "missing_oracle"
    source = _clean(row.get("source") or row.get("source_file"))
    source_family = _clean(row.get("source_family") or source)
    project_id = _clean(row.get("project_id") or row.get("project_name") or row.get("source_file"))
    if kind == DatasetKind.OSS_DIAGNOSTIC and (not source_family or not project_id):
        return None, "missing_provenance"
    case_id = _clean(row.get("case_id") or row.get("sample_id") or row.get("bill_id") or index)
    known = {
        "case_id", "sample_id", "bill_id", "province", "quota_province", "bill_name",
        "name", "bill_text", "description", "unit", "specialty", "oracle_quota_ids",
        "expected_quota_ids", "expected_ids", "quota_ids", "source", "source_file",
        "source_family", "project_id", "project_name", "split",
    }
    return EvalCase(
        case_id=case_id,
        dataset_kind=kind,
        province=province,
        bill_name=_clean(row.get("bill_name") or row.get("name")),
        bill_text=_clean(row.get("bill_text") or row.get("description")),
        unit=_clean(row.get("unit")),
        specialty=_clean(row.get("specialty")),
        oracle_quota_ids=oracles,
        source_family=source_family,
        project_id=project_id,
        source=source,
        split=_clean(row.get("split")),
        metadata={key: value for key, value in row.items() if key not in known},
    ), ""


def load_dataset(path: str | Path, dataset_kind: DatasetKind) -> DatasetLoadResult:
    resolved = Path(path)
    raw = resolved.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number} is not a JSON object: {resolved}")
        rows.append(payload)

    cases: list[EvalCase] = []
    rejections: Counter[str] = Counter()
    for index, row in enumerate(rows, start=1):
        case, reason = _case_from_row(row, dataset_kind, index)
        if case is None:
            rejections[reason] += 1
        else:
            cases.append(case)
    return DatasetLoadResult(
        path=resolved,
        dataset_kind=dataset_kind,
        cases=tuple(cases),
        total_rows=len(rows),
        rejection_counts=dict(sorted(rejections.items())),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
```

- [ ] **Step 4: Run loader tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_datasets.py -q
```

Expected: 2 tests PASS.

---

### Task 4: Normalize Production and Goal Lifecycles

**Files:**
- Create: `eval/accuracy_baseline/lifecycle.py`
- Test: `tests/test_accuracy_baseline_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `tests/test_accuracy_baseline_lifecycle.py` covering both adapters:

```python
from types import SimpleNamespace

from eval.accuracy_baseline.contracts import DatasetKind, EvalCase, LifecycleStage
from eval.accuracy_baseline.lifecycle import normalize_goal_hits, normalize_production_detail


def _case() -> EvalCase:
    return EvalCase(
        case_id="case-1",
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Valve",
        bill_text="DN50",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-2",),
        source_family="human",
        project_id="project-a",
    )


def test_normalize_production_detail_keeps_oracle_drop_and_decision_path():
    detail = {
        "recall_topk_ids": ["Q-1", "Q-2", "Q-3"],
        "candidate_snapshots": [
            {"quota_id": "Q-1", "name": "wrong", "rerank_score": 0.9},
            {"quota_id": "Q-2", "name": "correct", "family_gate_hard_conflict": True},
            {"quota_id": "Q-3", "name": "other"},
        ],
        "candidate_lifecycle_trace": [
            {"quota_id": "Q-1", "filter_state": "param_matched", "rank_position": 1},
            {
                "quota_id": "Q-2",
                "filter_state": "filtered_or_gated",
                "lost_reason": "family_gate_hard_conflict",
            },
        ],
        "router": {
            "classification": {
                "route_scope_filter": {
                    "applied": True,
                    "dropped_quota_ids": ["Q-2"],
                    "reason": "strict_route_scope",
                }
            }
        },
        "pre_ltr_top1_id": "Q-1",
        "post_ltr_top1_id": "Q-2",
        "post_ltr_structural_top1_id": "Q-1",
        "post_final_top1_id": "Q-1",
        "algo_id": "Q-1",
        "confidence": 70,
    }

    result = normalize_production_detail(_case(), detail)

    assert result.retrieved_ids == ("Q-1", "Q-2", "Q-3")
    route_stage = next(stage for stage in result.lifecycle if stage.stage == LifecycleStage.ROUTE_FILTERED)
    assert route_stage.emitted is True
    assert [candidate.quota_id for candidate in route_stage.candidates] == ["Q-1", "Q-3"]
    assert [decision.name for decision in result.decisions] == [
        "pre_ltr_seed",
        "ltr",
        "post_ltr_structural_ranker",
        "final",
    ]
    oracle = next(
        candidate
        for stage in result.lifecycle
        for candidate in stage.candidates
        if candidate.quota_id == "Q-2" and candidate.drop_reason
    )
    assert oracle.hard_conflicts == ("family_gate_hard_conflict",)


def test_normalize_goal_hits_emits_retrieved_reranked_selected_and_postprocessed():
    hits = [
        SimpleNamespace(
            quota_id="Q-2",
            name="correct",
            unit="set",
            score=1.2,
            confidence=70.0,
            reasons=["bm25:1.00"],
            source_scores={"bm25": 1.0},
        ),
        SimpleNamespace(
            quota_id="Q-1",
            name="wrong",
            unit="set",
            score=1.0,
            confidence=63.0,
            reasons=["bm25:0.80"],
            source_scores={"bm25": 0.8},
        ),
    ]

    result = normalize_goal_hits(_case(), hits)

    emitted = [stage.stage for stage in result.lifecycle if stage.emitted]
    assert emitted == [
        LifecycleStage.RETRIEVED,
        LifecycleStage.RERANKED,
        LifecycleStage.SELECTED,
        LifecycleStage.POSTPROCESSED,
    ]
    assert result.final_top1_id == "Q-2"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
pytest tests/test_accuracy_baseline_lifecycle.py -q
```

Expected: FAIL because `lifecycle.py` does not exist.

- [ ] **Step 3: Implement fixed stage construction and production normalization**

Create `eval/accuracy_baseline/lifecycle.py`. Define these constants and helpers exactly:

```python
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import (
    CandidateSnapshot,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)


STAGE_ORDER = tuple(LifecycleStage)
DECISION_FIELDS = (
    ("pre_ltr_seed", "pre_ltr_top1_id"),
    ("ltr", "post_ltr_top1_id"),
    ("post_ltr_structural_ranker", "post_ltr_structural_top1_id"),
    ("cgr_ranker", "post_cgr_top1_id"),
    ("candidate_arbiter", "post_arbiter_top1_id"),
    ("explicit_picker", "post_explicit_top1_id"),
    ("anchor", "post_anchor_top1_id"),
    ("final", "post_final_top1_id"),
)
HARD_CONFLICT_FIELDS = (
    "family_gate_hard_conflict",
    "feature_alignment_hard_conflict",
    "logic_hard_conflict",
    "context_alignment_hard_conflict",
    "param_hard_fail",
)


def _candidate_map(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("quota_id") or row.get("id") or "").strip(): row
        for row in rows
        if str(row.get("quota_id") or row.get("id") or "").strip()
    }


def _snapshot(
    *,
    case: EvalCase,
    provider: str,
    stage: LifecycleStage,
    quota_id: str,
    rank: int | None,
    row: dict[str, Any] | None = None,
    lifecycle_row: dict[str, Any] | None = None,
) -> CandidateSnapshot:
    row = dict(row or {})
    lifecycle_row = dict(lifecycle_row or {})
    hard_conflicts = tuple(field for field in HARD_CONFLICT_FIELDS if bool(row.get(field)))
    lost_reason = str(lifecycle_row.get("lost_reason") or "")
    if lost_reason in HARD_CONFLICT_FIELDS and lost_reason not in hard_conflicts:
        hard_conflicts += (lost_reason,)
    scores = {
        key: float(row[key])
        for key in (
            "hybrid_score", "rerank_score", "manual_structured_score", "ltr_score",
            "param_score", "feature_alignment_score", "family_gate_score",
        )
        if row.get(key) is not None
    }
    return CandidateSnapshot(
        quota_id=quota_id,
        name=str(row.get("name") or row.get("quota_name") or ""),
        unit=str(row.get("unit") or ""),
        province=str(row.get("_source_province") or case.province),
        provider=provider,
        source=str(lifecycle_row.get("source") or row.get("match_source") or ""),
        stage=stage,
        rank=rank,
        scores=scores,
        family=str((row.get("candidate_canonical_features") or {}).get("family") or ""),
        book=str(row.get("book") or row.get("quota_book") or ""),
        param_match=(None if row.get("param_match") is None else bool(row.get("param_match"))),
        hard_conflicts=hard_conflicts,
        drop_reason=lost_reason,
        raw_stage=str(lifecycle_row.get("first_seen_stage") or row.get("rank_stage") or ""),
        raw={**row, **lifecycle_row},
    )


def _empty_stage(stage: LifecycleStage) -> StageSnapshot:
    return StageSnapshot(stage=stage, emitted=False)
```

Then implement `normalize_production_detail`:

```python
def normalize_production_detail(case: EvalCase, detail: dict[str, Any]) -> ProviderResult:
    candidate_rows = _candidate_map(detail.get("candidate_snapshots") or [])
    lifecycle_rows = _candidate_map(detail.get("candidate_lifecycle_trace") or [])
    recall_ids = tuple(
        str(value).strip()
        for value in (detail.get("recall_topk_ids") or detail.get("all_candidate_ids") or [])
        if str(value).strip()
    )
    retrieved = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.RETRIEVED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(recall_ids, start=1)
    )
    router = dict(detail.get("router") or {})
    classification = dict(router.get("classification") or router)
    route_filter = dict(classification.get("route_scope_filter") or {})
    route_dropped_ids = {
        str(value).strip()
        for value in route_filter.get("dropped_quota_ids") or []
        if str(value).strip()
    }
    route_remaining_ids = [quota_id for quota_id in recall_ids if quota_id not in route_dropped_ids]
    route_filtered = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.ROUTE_FILTERED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(route_remaining_ids, start=1)
    )
    validated_ids = [
        quota_id
        for quota_id in recall_ids
        if str((lifecycle_rows.get(quota_id) or {}).get("filter_state") or "")
        not in {"filtered_hard_param_fail", "filtered_or_gated"}
    ]
    validated = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.VALIDATED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(validated_ids, start=1)
    )
    final_ids = tuple(
        str(value).strip()
        for value in (detail.get("final_quota_ids") or [])
        if str(value).strip()
    )
    final_id = str(detail.get("post_final_top1_id") or detail.get("algo_id") or "")
    if not final_ids and final_id:
        final_ids = (final_id,)
    selected_candidate = tuple(candidate for candidate in validated if candidate.quota_id == final_id)
    stages = {
        LifecycleStage.RETRIEVED: StageSnapshot(
            stage=LifecycleStage.RETRIEVED,
            emitted=bool(recall_ids),
            candidates=retrieved,
            top1_id=recall_ids[0] if recall_ids else "",
            raw_stage_names=("recall_topk_ids",),
        ),
        LifecycleStage.ROUTE_FILTERED: StageSnapshot(
            stage=LifecycleStage.ROUTE_FILTERED,
            emitted=bool(route_filter.get("applied")),
            candidates=route_filtered,
            top1_id=route_remaining_ids[0] if route_remaining_ids else "",
            raw_stage_names=("route_scope_filter",),
        ),
        LifecycleStage.RERANKED: StageSnapshot(
            stage=LifecycleStage.RERANKED,
            emitted=bool(candidate_rows),
            candidates=tuple(
                _snapshot(
                    case=case,
                    provider="production",
                    stage=LifecycleStage.RERANKED,
                    quota_id=quota_id,
                    rank=rank,
                    row=row,
                    lifecycle_row=lifecycle_rows.get(quota_id),
                )
                for rank, (quota_id, row) in enumerate(candidate_rows.items(), start=1)
            ),
            top1_id=str(detail.get("pre_ltr_top1_id") or ""),
            raw_stage_names=("candidate_snapshots",),
        ),
        LifecycleStage.VALIDATED: StageSnapshot(
            stage=LifecycleStage.VALIDATED,
            emitted=bool(lifecycle_rows),
            candidates=validated,
            top1_id=validated_ids[0] if validated_ids else "",
            raw_stage_names=("candidate_lifecycle_trace",),
        ),
        LifecycleStage.SELECTED: StageSnapshot(
            stage=LifecycleStage.SELECTED,
            emitted=bool(final_id),
            candidates=selected_candidate,
            top1_id=final_id,
            raw_stage_names=("selected_top1_id",),
        ),
        LifecycleStage.POSTPROCESSED: StageSnapshot(
            stage=LifecycleStage.POSTPROCESSED,
            emitted=bool(final_id),
            candidates=selected_candidate,
            top1_id=final_id,
            raw_stage_names=("post_final_top1_id",),
        ),
    }
    decisions = tuple(
        DecisionSnapshot(name=name, top1_id=str(detail.get(field) or ""))
        for name, field in DECISION_FIELDS
        if str(detail.get(field) or "")
    )
    status = ProviderStatus.OK
    if not case.oracle_quota_ids:
        status = ProviderStatus.MISSING_ORACLE
    elif str(detail.get("oracle_status") or "ok") != "ok":
        status = ProviderStatus.ORACLE_NOT_IN_LOCAL_DB
    elif not recall_ids or not lifecycle_rows:
        status = ProviderStatus.TRACE_INCOMPLETE
    return ProviderResult(
        case_id=case.case_id,
        provider_name="production",
        status=status,
        final_quota_ids=final_ids,
        confidence=float(detail.get("confidence") or 0.0),
        lifecycle=tuple(stages[stage] for stage in STAGE_ORDER),
        decisions=decisions,
        raw_trace=detail,
    )
```

- [ ] **Step 4: Implement Goal normalization without fabricated stages**

Add:

```python
def normalize_goal_hits(case: EvalCase, hits: Iterable[Any]) -> ProviderResult:
    rows = list(hits)
    candidates = tuple(
        CandidateSnapshot(
            quota_id=str(hit.quota_id),
            name=str(hit.name),
            unit=str(hit.unit),
            province=case.province,
            provider="goal_shadow",
            source="goal_search",
            stage=LifecycleStage.RETRIEVED,
            rank=rank,
            scores={"goal_score": float(hit.score), **dict(hit.source_scores or {})},
            raw_stage="goal_search",
            raw={"reasons": list(hit.reasons or [])},
        )
        for rank, hit in enumerate(rows, start=1)
    )
    final_id = candidates[0].quota_id if candidates else ""
    final_ids = tuple(candidate.quota_id for candidate in candidates[:3])
    selected = candidates[:1]

    def restage(stage: LifecycleStage, values: tuple[CandidateSnapshot, ...]) -> tuple[CandidateSnapshot, ...]:
        return tuple(
            CandidateSnapshot(
                quota_id=value.quota_id,
                name=value.name,
                unit=value.unit,
                province=value.province,
                provider=value.provider,
                source=value.source,
                stage=stage,
                rank=value.rank,
                scores=value.scores,
                raw_stage=value.raw_stage,
                raw=value.raw,
            )
            for value in values
        )

    stages = {
        LifecycleStage.RETRIEVED: StageSnapshot(
            stage=LifecycleStage.RETRIEVED,
            emitted=True,
            candidates=candidates,
            top1_id=final_id,
            raw_stage_names=("goal_candidate_generation",),
        ),
        LifecycleStage.ROUTE_FILTERED: _empty_stage(LifecycleStage.ROUTE_FILTERED),
        LifecycleStage.RERANKED: StageSnapshot(
            stage=LifecycleStage.RERANKED,
            emitted=True,
            candidates=restage(LifecycleStage.RERANKED, candidates),
            top1_id=final_id,
            raw_stage_names=("goal_score_sort",),
        ),
        LifecycleStage.VALIDATED: _empty_stage(LifecycleStage.VALIDATED),
        LifecycleStage.SELECTED: StageSnapshot(
            stage=LifecycleStage.SELECTED,
            emitted=bool(selected),
            candidates=restage(LifecycleStage.SELECTED, selected),
            top1_id=final_id,
            raw_stage_names=("goal_top1",),
        ),
        LifecycleStage.POSTPROCESSED: StageSnapshot(
            stage=LifecycleStage.POSTPROCESSED,
            emitted=bool(selected),
            candidates=restage(LifecycleStage.POSTPROCESSED, selected),
            top1_id=final_id,
            raw_stage_names=("goal_top1",),
        ),
    }
    return ProviderResult(
        case_id=case.case_id,
        provider_name="goal_shadow",
        status=ProviderStatus.OK,
        final_quota_ids=final_ids,
        confidence=(float(rows[0].confidence) if rows else 0.0),
        lifecycle=tuple(stages[stage] for stage in STAGE_ORDER),
        decisions=((DecisionSnapshot(name="goal_score", top1_id=final_id),) if final_id else ()),
    )
```

- [ ] **Step 5: Run lifecycle tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_lifecycle.py -q
```

Expected: 2 tests PASS.

---

### Task 5: Compute Recall, Conditional Ranking, Flips, Slices, and Provider Union

**Files:**
- Create: `eval/accuracy_baseline/metrics.py`
- Test: `tests/test_accuracy_baseline_metrics.py`

- [ ] **Step 1: Write failing metric tests**

Create `tests/test_accuracy_baseline_metrics.py` using small contract fixtures. Cover these assertions:

```python
from dataclasses import replace

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    DatasetKind,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)
from eval.accuracy_baseline.metrics import aggregate_provider_metrics, compare_providers


def _case(case_id: str, oracle: str = "Q-2") -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Bill",
        bill_text="Spec",
        unit="set",
        specialty="C10",
        oracle_quota_ids=(oracle,),
        source_family="human",
        project_id="project-a",
    )


def _result(case_id: str, ids: list[str], decisions: list[tuple[str, str]], final: str) -> ProviderResult:
    candidates = tuple(
        CandidateSnapshot(
            quota_id=quota_id,
            name=quota_id,
            unit="set",
            province="demo",
            provider="production",
            source="hybrid",
            stage=LifecycleStage.RETRIEVED,
            rank=rank,
        )
        for rank, quota_id in enumerate(ids, start=1)
    )
    return ProviderResult(
        case_id=case_id,
        provider_name="production",
        status=ProviderStatus.OK,
        final_quota_ids=(final, *tuple(quota_id for quota_id in ids if quota_id != final)[:2]),
        lifecycle=(
            StageSnapshot(
                stage=LifecycleStage.RETRIEVED,
                emitted=True,
                candidates=candidates,
                top1_id=ids[0],
            ),
        ),
        decisions=tuple(DecisionSnapshot(name=name, top1_id=top1) for name, top1 in decisions),
    )


def test_aggregate_metrics_separates_recall_conditional_top1_and_flips():
    case = _case("case-1")
    result = _result(
        "case-1",
        ["Q-1", "Q-2", "Q-3"],
        [("manual", "Q-1"), ("ltr", "Q-2"), ("final", "Q-1")],
        "Q-1",
    )

    report = aggregate_provider_metrics([case], [result], min_slice_size=1)

    assert report["recall_at"] == {"5": 1.0, "10": 1.0, "25": 1.0, "80": 1.0}
    assert report["conditional_top1"] == 0.0
    assert report["final_top1"] == 0.0
    assert report["final_top3"] == 1.0
    assert report["refusal_rate"] == 0.0
    assert report["mrr"] == 0.5
    assert report["stage_flips"]["ltr"] == {"good_flip": 1, "bad_flip": 0, "net_gain": 1}
    assert report["stage_flips"]["final"] == {"good_flip": 0, "bad_flip": 1, "net_gain": -1}


def test_provider_comparison_reports_unique_and_union_recall():
    case = _case("case-1")
    production = _result("case-1", ["Q-1"], [("final", "Q-1")], "Q-1")
    goal_candidate = CandidateSnapshot(
        quota_id="Q-2",
        name="Q-2",
        unit="set",
        province="demo",
        provider="goal_shadow",
        source="goal_search",
        stage=LifecycleStage.RETRIEVED,
        rank=1,
    )
    goal = replace(
        production,
        provider_name="goal_shadow",
        final_quota_ids=("Q-2",),
        lifecycle=(
            StageSnapshot(
                stage=LifecycleStage.RETRIEVED,
                emitted=True,
                candidates=(goal_candidate,),
                top1_id="Q-2",
            ),
        ),
    )

    comparison = compare_providers([case], {"production": [production], "goal_shadow": [goal]})

    assert comparison["production_recall"] == 0.0
    assert comparison["goal_shadow_recall"] == 1.0
    assert comparison["union_recall"] == 1.0
    assert comparison["goal_shadow_unique_recall_gain"] == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
pytest tests/test_accuracy_baseline_metrics.py -q
```

Expected: FAIL because `metrics.py` does not exist.

- [ ] **Step 3: Implement per-case helpers and aggregate metrics**

Create `eval/accuracy_baseline/metrics.py` with these functions and rules:

```python
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from .contracts import EvalCase, LifecycleStage, ProviderResult, ProviderStatus


VALID_STATUSES = {ProviderStatus.OK, ProviderStatus.TRACE_INCOMPLETE}
RECALL_K_VALUES = (5, 10, 25, 80)


def _first_oracle_rank(case: EvalCase, result: ProviderResult) -> int | None:
    for rank, quota_id in enumerate(result.retrieved_ids, start=1):
        if quota_id in case.oracle_set:
            return rank
    return None


def _is_correct(case: EvalCase, quota_id: str) -> bool:
    return bool(quota_id and quota_id in case.oracle_set)


def _ranked_top_ids(result: ProviderResult, limit: int = 3) -> tuple[str, ...]:
    for target_stage in (
        LifecycleStage.VALIDATED,
        LifecycleStage.RERANKED,
        LifecycleStage.RETRIEVED,
    ):
        stage = next(
            (
                value
                for value in result.lifecycle
                if value.stage == target_stage and value.emitted and value.candidates
            ),
            None,
        )
        if stage is not None:
            ordered = sorted(
                stage.candidates,
                key=lambda candidate: (candidate.rank is None, candidate.rank or 10**9, candidate.quota_id),
            )
            return tuple(candidate.quota_id for candidate in ordered[:limit])
    return result.final_quota_ids[:limit]


def _stage_flips(case: EvalCase, result: ProviderResult) -> dict[str, tuple[int, int]]:
    flips: dict[str, tuple[int, int]] = {}
    previous_correct: bool | None = None
    for decision in result.decisions:
        current_correct = _is_correct(case, decision.top1_id)
        good = int(previous_correct is False and current_correct is True)
        bad = int(previous_correct is True and current_correct is False)
        flips[decision.name] = (good, bad)
        previous_correct = current_correct
    return flips


def aggregate_provider_metrics(
    cases: Sequence[EvalCase],
    results: Sequence[ProviderResult],
    *,
    min_slice_size: int = 20,
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    valid: list[tuple[EvalCase, ProviderResult]] = []
    exclusions: Counter[str] = Counter()
    for result in results:
        case = case_by_id.get(result.case_id)
        if case is None:
            exclusions["unknown_case"] += 1
        elif result.status not in VALID_STATUSES:
            exclusions[result.status.value] += 1
        else:
            valid.append((case, result))

    ranks = [_first_oracle_rank(case, result) for case, result in valid]
    recalled = [rank for rank in ranks if rank is not None]
    final_correct = [
        _is_correct(case, result.final_top1_id)
        for case, result in valid
        if _first_oracle_rank(case, result) is not None
    ]
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for case, result in valid:
        for stage_name, (good, bad) in _stage_flips(case, result).items():
            stage_counts[stage_name]["good_flip"] += good
            stage_counts[stage_name]["bad_flip"] += bad

    slices: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[tuple[EvalCase, ProviderResult]]] = defaultdict(list)
    for case, result in valid:
        grouped[f"province={case.province}"].append((case, result))
        grouped[f"source_family={case.source_family or '<empty>'}"].append((case, result))
        grouped[f"project={case.project_id or '<empty>'}"].append((case, result))
    for key, rows in sorted(grouped.items()):
        correct = sum(_is_correct(case, result.final_top1_id) for case, result in rows)
        slices[key] = {
            "count": len(rows),
            "correct": correct,
            "top1": (round(correct / len(rows), 6) if len(rows) >= min_slice_size else None),
        }

    taxonomy_false_veto_keys: set[tuple[str, str]] = set()
    param_false_hard_fail_keys: set[tuple[str, str]] = set()
    route_loss_count = 0
    route_evaluable_count = 0
    required_candidate_fields = (
        "quota_id", "name", "unit", "province", "provider", "source", "stage", "rank",
        "scores", "family", "book", "param_match", "hard_conflicts", "drop_reason", "raw_stage",
    )
    candidate_field_present: Counter[str] = Counter()
    candidate_total = 0
    for case, result in valid:
        retrieved_stage = next(
            (stage for stage in result.lifecycle if stage.stage == LifecycleStage.RETRIEVED and stage.emitted),
            None,
        )
        route_stage = next(
            (stage for stage in result.lifecycle if stage.stage == LifecycleStage.ROUTE_FILTERED and stage.emitted),
            None,
        )
        if retrieved_stage and route_stage:
            retrieved_ids = {candidate.quota_id for candidate in retrieved_stage.candidates}
            route_ids = {candidate.quota_id for candidate in route_stage.candidates}
            if retrieved_ids & case.oracle_set:
                route_evaluable_count += 1
                route_loss_count += not bool(route_ids & case.oracle_set)
        for stage in result.lifecycle:
            for candidate in stage.candidates:
                candidate_total += 1
                for field_name in required_candidate_fields:
                    value = getattr(candidate, field_name)
                    if value not in (None, "", (), {}):
                        candidate_field_present[field_name] += 1
                if candidate.quota_id not in case.oracle_set:
                    continue
                if "family_gate_hard_conflict" in candidate.hard_conflicts:
                    taxonomy_false_veto_keys.add((case.case_id, candidate.quota_id))
                if "param_hard_fail" in candidate.hard_conflicts or candidate.drop_reason == "hard_param_fail":
                    param_false_hard_fail_keys.add((case.case_id, candidate.quota_id))

    denominator = len(valid)
    final_top1_hits = sum(_is_correct(case, result.final_top1_id) for case, result in valid)
    final_top3_hits = sum(
        bool(set(_ranked_top_ids(result, 3)) & case.oracle_set)
        for case, result in valid
    )
    refusal_count = sum(not result.final_top1_id for _, result in valid)
    calibration_rows = [
        (
            max(0.0, min(1.0, result.confidence / 100.0 if result.confidence > 1.0 else result.confidence)),
            float(_is_correct(case, result.final_top1_id)),
        )
        for case, result in valid
        if result.final_top1_id
    ]
    ece = 0.0
    for bin_index in range(10):
        lower = bin_index / 10.0
        upper = (bin_index + 1) / 10.0
        rows = [row for row in calibration_rows if lower <= row[0] < upper or (bin_index == 9 and row[0] == 1.0)]
        if rows:
            average_confidence = sum(row[0] for row in rows) / len(rows)
            average_accuracy = sum(row[1] for row in rows) / len(rows)
            ece += len(rows) / len(calibration_rows) * abs(average_confidence - average_accuracy)
    return {
        "total_cases": len(cases),
        "valid_cases": denominator,
        "exclusions": dict(sorted(exclusions.items())),
        "recall_at": {
            str(k): round(sum(rank is not None and rank <= k for rank in ranks) / denominator, 6)
            if denominator else 0.0
            for k in RECALL_K_VALUES
        },
        "conditional_top1": round(sum(final_correct) / len(final_correct), 6) if final_correct else 0.0,
        "final_top1": round(final_top1_hits / denominator, 6) if denominator else 0.0,
        "final_top3": round(final_top3_hits / denominator, 6) if denominator else 0.0,
        "refusal_rate": round(refusal_count / denominator, 6) if denominator else 0.0,
        "confidence_ece": round(ece, 6),
        "mrr": round(sum(1.0 / rank for rank in recalled) / denominator, 6) if denominator else 0.0,
        "stage_flips": {
            name: {
                "good_flip": counts["good_flip"],
                "bad_flip": counts["bad_flip"],
                "net_gain": counts["good_flip"] - counts["bad_flip"],
            }
            for name, counts in sorted(stage_counts.items())
        },
        "taxonomy_false_veto_count": len(taxonomy_false_veto_keys),
        "param_false_hard_fail_count": len(param_false_hard_fail_keys),
        "route_filter_oracle_loss_count": route_loss_count,
        "route_filter_oracle_loss_rate": (
            round(route_loss_count / route_evaluable_count, 6) if route_evaluable_count else None
        ),
        "candidate_contract_coverage": {
            field_name: round(candidate_field_present[field_name] / candidate_total, 6)
            if candidate_total else 0.0
            for field_name in required_candidate_fields
        },
        "trace_complete_rate": round(
            sum(result.status == ProviderStatus.OK for _, result in valid) / denominator,
            6,
        ) if denominator else 0.0,
        "slices": slices,
    }
```

- [ ] **Step 4: Implement provider union comparison**

Add:

```python
def compare_providers(
    cases: Sequence[EvalCase],
    provider_results: dict[str, Sequence[ProviderResult]],
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    result_maps = {
        provider: {result.case_id: result for result in results}
        for provider, results in provider_results.items()
    }
    production_hits = 0
    goal_hits = 0
    union_hits = 0
    goal_unique = 0
    excluded = 0
    rows: list[dict[str, Any]] = []
    for case_id, case in sorted(case_by_id.items()):
        production = result_maps.get("production", {}).get(case_id)
        goal = result_maps.get("goal_shadow", {}).get(case_id)
        if (
            production is None
            or goal is None
            or production.status not in VALID_STATUSES
            or goal.status not in VALID_STATUSES
        ):
            excluded += 1
            continue
        production_ids = set(production.retrieved_ids if production else ())
        goal_ids = set(goal.retrieved_ids if goal else ())
        production_hit = bool(production_ids & case.oracle_set)
        goal_hit = bool(goal_ids & case.oracle_set)
        union_hit = bool((production_ids | goal_ids) & case.oracle_set)
        production_hits += production_hit
        goal_hits += goal_hit
        union_hits += union_hit
        goal_unique += goal_hit and not production_hit
        rows.append({
            "case_id": case_id,
            "production_recalled": production_hit,
            "goal_shadow_recalled": goal_hit,
            "union_recalled": union_hit,
            "goal_shadow_unique": goal_hit and not production_hit,
        })
    total = len(rows)
    return {
        "total_cases": len(cases),
        "comparable_cases": total,
        "excluded_cases": excluded,
        "production_recall": round(production_hits / total, 6) if total else 0.0,
        "goal_shadow_recall": round(goal_hits / total, 6) if total else 0.0,
        "union_recall": round(union_hits / total, 6) if total else 0.0,
        "goal_shadow_unique_recall_gain": goal_unique,
        "cases": rows,
    }
```

- [ ] **Step 5: Run metric tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_metrics.py -q
```

Expected: all tests PASS.

---

### Task 6: Implement Read-Only Production and Goal Providers

**Files:**
- Create: `eval/accuracy_baseline/providers.py`
- Test: `tests/test_accuracy_baseline_providers.py`

- [ ] **Step 1: Write provider contract and failure-isolation tests**

Create `tests/test_accuracy_baseline_providers.py` with injected fakes:

```python
from types import SimpleNamespace

from eval.accuracy_baseline.contracts import DatasetKind, EvalCase, ProviderStatus
from eval.accuracy_baseline.providers import GoalShadowProvider, ProductionProvider


def _case(case_id: str, province: str) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province=province,
        bill_name="Valve",
        bill_text="DN50",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-1",),
        source_family="human",
        project_id="project-a",
    )


def test_production_provider_isolates_unavailable_province():
    def executor(province, records, with_experience=False):
        if province == "missing":
            raise RuntimeError("index unavailable")
        return {
            "details": [
                {
                    "sample_id": records[0]["sample_id"],
                    "recall_topk_ids": ["Q-1"],
                    "candidate_snapshots": [{"quota_id": "Q-1", "name": "correct"}],
                    "candidate_lifecycle_trace": [
                        {"quota_id": "Q-1", "filter_state": "param_matched", "rank_position": 1}
                    ],
                    "post_final_top1_id": "Q-1",
                    "algo_id": "Q-1",
                    "oracle_status": "ok",
                }
            ]
        }

    results = ProductionProvider(executor=executor).run([
        _case("ok-1", "available"),
        _case("bad-1", "missing"),
    ])

    assert {result.case_id: result.status for result in results} == {
        "bad-1": ProviderStatus.PROVINCE_UNAVAILABLE,
        "ok-1": ProviderStatus.OK,
    }


def test_goal_provider_forces_leakage_safe_priors_and_top80():
    calls = []

    class FakeSearcher:
        index = SimpleNamespace(by_quota_id={"Q-1": object()})

        def search(self, item, top_k):
            calls.append((item, top_k))
            return [
                SimpleNamespace(
                    quota_id="Q-1",
                    name="correct",
                    unit="set",
                    score=1.0,
                    confidence=63.0,
                    reasons=[],
                    source_scores={"bm25": 1.0},
                )
            ]

    results = GoalShadowProvider(searcher_factory=lambda province: FakeSearcher()).run([
        _case("goal-1", "available")
    ])

    assert results[0].status == ProviderStatus.OK
    assert calls[0][1] == 80
    assert calls[0][0]["goal_no_answer_priors"] is True
    assert calls[0][0]["goal_excluded_sources"]["sample_id"] == {"goal-1"}
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest tests/test_accuracy_baseline_providers.py -q
```

Expected: FAIL because providers do not exist.

- [ ] **Step 3: Implement the provider protocol and production adapter**

Create `eval/accuracy_baseline/providers.py`:

```python
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .contracts import EvalCase, ProviderError, ProviderResult, ProviderStatus
from .lifecycle import normalize_goal_hits, normalize_production_detail


class CandidateProvider(Protocol):
    name: str

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]: ...


def _error_result(case: EvalCase, provider: str, status: ProviderStatus, exc: Exception) -> ProviderResult:
    return ProviderResult(
        case_id=case.case_id,
        provider_name=provider,
        status=status,
        errors=(ProviderError(code=status.value, message=str(exc), province=case.province),),
    )


class ProductionProvider:
    name = "production"

    def __init__(
        self,
        *,
        executor: Callable[..., dict[str, Any]] | None = None,
        with_experience: bool = False,
    ) -> None:
        if executor is None:
            from tools.run_real_eval import evaluate_province_records

            executor = evaluate_province_records
        self._executor = executor
        self._with_experience = with_experience

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]:
        grouped: dict[str, list[EvalCase]] = defaultdict(list)
        for case in cases:
            grouped[case.province].append(case)

        results: list[ProviderResult] = []
        for province in sorted(grouped):
            province_cases = grouped[province]
            try:
                payload = self._executor(
                    province,
                    [case.to_record() for case in province_cases],
                    with_experience=self._with_experience,
                )
            except Exception as exc:
                results.extend(
                    _error_result(case, self.name, ProviderStatus.PROVINCE_UNAVAILABLE, exc)
                    for case in province_cases
                )
                continue
            details = {
                str(detail.get("sample_id") or ""): detail
                for detail in payload.get("details") or []
            }
            for case in province_cases:
                detail = details.get(case.case_id)
                if detail is None:
                    results.append(
                        _error_result(
                            case,
                            self.name,
                            ProviderStatus.PROVIDER_ERROR,
                            RuntimeError("production result missing case detail"),
                        )
                    )
                else:
                    results.append(normalize_production_detail(case, detail))
        return sorted(results, key=lambda result: result.case_id)
```

- [ ] **Step 4: Implement the leakage-safe Goal Shadow adapter**

Add:

```python
class GoalShadowProvider:
    name = "goal_shadow"

    def __init__(
        self,
        *,
        searcher_factory: Callable[[str], Any] | None = None,
        top_k: int = 80,
    ) -> None:
        if searcher_factory is None:
            from src.goal_search import GoalSearcher

            searcher_factory = GoalSearcher
        self._searcher_factory = searcher_factory
        self._top_k = top_k

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]:
        searchers: dict[str, Any] = {}
        results: list[ProviderResult] = []
        for case in sorted(cases, key=lambda value: (value.province, value.case_id)):
            try:
                searcher = searchers.setdefault(case.province, self._searcher_factory(case.province))
                local_ids = set(getattr(searcher.index, "by_quota_id", {}))
                if case.oracle_set and not (case.oracle_set & local_ids):
                    results.append(
                        ProviderResult(
                            case_id=case.case_id,
                            provider_name=self.name,
                            status=ProviderStatus.ORACLE_NOT_IN_LOCAL_DB,
                        )
                    )
                    continue
                item = case.to_record()
                item["goal_no_answer_priors"] = True
                item["goal_excluded_sources"] = {
                    "sample_id": {case.case_id},
                    "source_file": ({case.source} if case.source else set()),
                    "project_name": ({case.project_id} if case.project_id else set()),
                }
                hits = searcher.search(item, top_k=self._top_k)
                results.append(normalize_goal_hits(case, hits))
            except Exception as exc:
                results.append(_error_result(case, self.name, ProviderStatus.PROVIDER_ERROR, exc))
        return sorted(results, key=lambda result: result.case_id)
```

Avoid `dict.setdefault` with a factory call because its default argument is evaluated eagerly. The final implementation must instead use:

```python
                searcher = searchers.get(case.province)
                if searcher is None:
                    searcher = self._searcher_factory(case.province)
                    searchers[case.province] = searcher
```

- [ ] **Step 5: Run provider tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_providers.py -q
```

Expected: all tests PASS.

---

### Task 7: Write Deterministic Reports and Runtime Metadata

**Files:**
- Create: `eval/accuracy_baseline/reporting.py`
- Test: `tests/test_accuracy_baseline_reporting.py`

- [ ] **Step 1: Write failing report tests**

Create `tests/test_accuracy_baseline_reporting.py`:

```python
import csv
import json

from eval.accuracy_baseline.reporting import write_reports


def test_write_reports_is_deterministic_and_writes_all_artifacts(tmp_path):
    payload = {
        "summary": {"valid_cases": 1, "recall_at": {"25": 1.0}},
        "cases": [{"case_id": "b"}, {"case_id": "a"}],
        "stage_attribution": [
            {"provider": "production", "stage": "ltr", "good_flip": 1, "bad_flip": 0, "net_gain": 1}
        ],
        "slice_metrics": [
            {"provider": "production", "slice": "province=demo", "count": 1, "correct": 1, "top1": None}
        ],
        "provider_comparison": [
            {"case_id": "a", "production_recalled": False, "goal_shadow_recalled": True}
        ],
    }

    paths = write_reports(tmp_path, payload)

    assert set(paths) == {
        "summary_json",
        "cases_jsonl",
        "stage_attribution_csv",
        "slice_metrics_csv",
        "provider_comparison_csv",
    }
    case_lines = [json.loads(line) for line in paths["cases_jsonl"].read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in case_lines] == ["a", "b"]
    with paths["stage_attribution_csv"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["stage"] == "ltr"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
pytest tests/test_accuracy_baseline_reporting.py -q
```

Expected: FAIL because reporting does not exist.

- [ ] **Step 3: Implement deterministic serializers**

Create `eval/accuracy_baseline/reporting.py`:

```python
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_reports(output_dir: str | Path, payload: dict[str, Any]) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": root / "summary.json",
        "cases_jsonl": root / "cases.jsonl",
        "stage_attribution_csv": root / "stage_attribution.csv",
        "slice_metrics_csv": root / "slice_metrics.csv",
        "provider_comparison_csv": root / "provider_comparison.csv",
    }
    paths["summary_json"].write_text(
        json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    case_rows = sorted(
        payload.get("cases") or [],
        key=lambda row: (
            str(row.get("dataset") or ""),
            str(row.get("case_id") or (row.get("case") or {}).get("case_id") or ""),
            str((row.get("provider_result") or {}).get("provider_name") or ""),
        ),
    )
    paths["cases_jsonl"].write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in case_rows),
        encoding="utf-8",
    )
    _write_csv(paths["stage_attribution_csv"], list(payload.get("stage_attribution") or []))
    _write_csv(paths["slice_metrics_csv"], list(payload.get("slice_metrics") or []))
    _write_csv(paths["provider_comparison_csv"], list(payload.get("provider_comparison") or []))
    return paths
```

If a CSV has zero rows, `_write_csv` must still create an empty UTF-8-SIG file without calling `DictWriter` with an empty field list. Add this guard before creating the writer:

```python
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
```

- [ ] **Step 4: Run report tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_reporting.py -q
```

Expected: PASS.

---

### Task 8: Orchestrate Providers and Expose the CLI

**Files:**
- Create: `eval/accuracy_baseline/runner.py`
- Create: `tools/run_accuracy_baseline.py`
- Test: `tests/test_accuracy_baseline_runner.py`

- [ ] **Step 1: Write a failing end-to-end runner test with fake providers**

Create `tests/test_accuracy_baseline_runner.py`:

```python
import json
from pathlib import Path

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)
from eval.accuracy_baseline.runner import run_accuracy_baseline


class FakeProvider:
    def __init__(self, name: str, quota_id: str):
        self.name = name
        self.quota_id = quota_id

    def run(self, cases):
        results = []
        for case in cases:
            candidate = CandidateSnapshot(
                quota_id=self.quota_id,
                name=self.quota_id,
                unit=case.unit,
                province=case.province,
                provider=self.name,
                source="fake",
                stage=LifecycleStage.RETRIEVED,
                rank=1,
            )
            results.append(
                ProviderResult(
                    case_id=case.case_id,
                    provider_name=self.name,
                    status=ProviderStatus.OK,
                    final_quota_ids=(self.quota_id,),
                    lifecycle=(
                        StageSnapshot(
                            stage=LifecycleStage.RETRIEVED,
                            emitted=True,
                            candidates=(candidate,),
                            top1_id=self.quota_id,
                        ),
                    ),
                )
            )
        return results


def test_run_accuracy_baseline_writes_isolated_dataset_metrics(tmp_path):
    dataset = tmp_path / "primary.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "sample_id": "1",
                "province": "demo",
                "bill_name": "Valve",
                "bill_text": "DN50",
                "unit": "set",
                "specialty": "C10",
                "oracle_quota_ids": ["Q-2"],
                "source": "user_correction",
                "source_family": "human",
                "project_name": "project-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_accuracy_baseline(
        datasets={"primary": dataset},
        output_dir=tmp_path / "reports",
        providers=[FakeProvider("production", "Q-1"), FakeProvider("goal_shadow", "Q-2")],
        min_slice_size=20,
    )

    assert result["summary"]["datasets"]["primary"]["providers"]["production"]["recall_at"]["25"] == 0.0
    assert result["summary"]["datasets"]["primary"]["providers"]["goal_shadow"]["recall_at"]["25"] == 1.0
    assert result["summary"]["datasets"]["primary"]["provider_comparison"]["union_recall"] == 1.0
    assert (tmp_path / "reports" / "summary.json").exists()
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
pytest tests/test_accuracy_baseline_runner.py -q
```

Expected: FAIL because runner does not exist.

- [ ] **Step 3: Implement runtime metadata and orchestration**

Create `eval/accuracy_baseline/runner.py`. Use `dataclasses.asdict` for contract serialization and do not serialize raw model objects.

```python
from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .contracts import DatasetKind, ProviderStatus
from .datasets import load_dataset
from .metrics import aggregate_provider_metrics, compare_providers
from .providers import CandidateProvider
from .reporting import write_reports


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _configured_artifacts() -> dict[str, Any]:
    import config

    paths: dict[str, Any] = {}
    for name in (
        "LTR_V2_MODEL_PATH",
        "LTR_V2_FEATURES_PATH",
        "OSS_RECALL_INDEX_PATH",
    ):
        value = getattr(config, name, "")
        path = Path(value) if value else None
        paths[name] = {
            "path": str(path or ""),
            "exists": bool(path and path.exists()),
            "size": (path.stat().st_size if path and path.exists() and path.is_file() else None),
            "modified_ns": (path.stat().st_mtime_ns if path and path.exists() else None),
        }
    national_index = Path(config.DATA_DIR) / "goal_search" / "national_index.sqlite"
    paths["NATIONAL_INDEX"] = {
        "path": str(national_index),
        "exists": national_index.exists(),
        "size": national_index.stat().st_size if national_index.exists() else None,
        "modified_ns": national_index.stat().st_mtime_ns if national_index.exists() else None,
    }
    return {
        "paths": paths,
        "flags": {
            name: getattr(config, name, None)
            for name in (
                "HYBRID_TOP_K",
                "RERANKER_TOP_K",
                "LTR_V2_ENABLED",
                "CONSTRAINED_GATED_RANKER_ENABLED",
                "UNIFIED_RANKING_ENABLED",
                "OSS_RECALL_INDEX_ENABLED",
            )
        },
    }


def _runtime_metadata() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "git_revision": _git_value("rev-parse", "HEAD"),
        "git_status": _git_value("status", "--short"),
        "configured_artifacts": _configured_artifacts(),
    }


def _flatten_stage_rows(dataset_name: str, provider: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"dataset": dataset_name, "provider": provider, "stage": stage, **values}
        for stage, values in sorted((report.get("stage_flips") or {}).items())
    ]


def _flatten_slice_rows(dataset_name: str, provider: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"dataset": dataset_name, "provider": provider, "slice": key, **values}
        for key, values in sorted((report.get("slices") or {}).items())
    ]


def _dataset_metric_view(
    dataset_kind: DatasetKind,
    report: dict[str, Any],
    cases,
    results,
) -> dict[str, Any]:
    if dataset_kind == DatasetKind.PRIMARY:
        return report
    if dataset_kind == DatasetKind.OSS_DIAGNOSTIC:
        allowed = {
            "total_cases", "valid_cases", "exclusions", "recall_at", "conditional_top1", "mrr",
            "stage_flips", "taxonomy_false_veto_count", "param_false_hard_fail_count",
            "route_filter_oracle_loss_count", "route_filter_oracle_loss_rate",
            "candidate_contract_coverage", "trace_complete_rate", "slices",
        }
        return {key: value for key, value in report.items() if key in allowed}

    case_by_id = {case.case_id: case for case in cases}
    valid = [result for result in results if result.status in {ProviderStatus.OK, ProviderStatus.TRACE_INCOMPLETE}]
    repaired = sum(
        result.final_top1_id in case_by_id[result.case_id].oracle_set
        for result in valid
    )
    regression_evaluable = [
        result
        for result in valid
        if bool(case_by_id[result.case_id].metadata.get("baseline_correct"))
    ]
    new_regressions = sum(
        result.final_top1_id not in case_by_id[result.case_id].oracle_set
        for result in regression_evaluable
    )
    return {
        "total_cases": report["total_cases"],
        "valid_cases": report["valid_cases"],
        "exclusions": report["exclusions"],
        "repair_count": repaired,
        "repair_rate": round(repaired / len(valid), 6) if valid else 0.0,
        "new_regression_count": new_regressions,
        "new_regression_evaluable_count": len(regression_evaluable),
        "stage_flips": report["stage_flips"],
        "slices": report["slices"],
    }


def run_accuracy_baseline(
    *,
    datasets: dict[str, str | Path],
    output_dir: str | Path,
    providers: Sequence[CandidateProvider],
    min_slice_size: int = 20,
) -> dict[str, Any]:
    dataset_kind_map = {
        "primary": DatasetKind.PRIMARY,
        "oss_diagnostic": DatasetKind.OSS_DIAGNOSTIC,
        "historical_stress": DatasetKind.HISTORICAL_STRESS,
    }
    summary: dict[str, Any] = {"runtime": _runtime_metadata(), "datasets": {}}
    case_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    slice_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []

    for dataset_name, path in datasets.items():
        loaded = load_dataset(path, dataset_kind_map[dataset_name])
        provider_results = {provider.name: provider.run(loaded.cases) for provider in providers}
        full_provider_metrics = {
            name: aggregate_provider_metrics(loaded.cases, results, min_slice_size=min_slice_size)
            for name, results in provider_results.items()
        }
        provider_metrics = {
            name: _dataset_metric_view(
                loaded.dataset_kind,
                full_provider_metrics[name],
                loaded.cases,
                results,
            )
            for name, results in provider_results.items()
        }
        comparison = compare_providers(loaded.cases, provider_results)
        summary["datasets"][dataset_name] = {
            "path": str(loaded.path),
            "content_sha256": loaded.content_sha256,
            "total_rows": loaded.total_rows,
            "accepted_cases": len(loaded.cases),
            "rejection_counts": loaded.rejection_counts,
            "providers": provider_metrics,
            "provider_comparison": {key: value for key, value in comparison.items() if key != "cases"},
        }
        for provider_name, results in provider_results.items():
            stage_rows.extend(_flatten_stage_rows(dataset_name, provider_name, full_provider_metrics[provider_name]))
            slice_rows.extend(_flatten_slice_rows(dataset_name, provider_name, full_provider_metrics[provider_name]))
            case_by_id = {case.case_id: case for case in loaded.cases}
            for result in results:
                case_rows.append({
                    "dataset": dataset_name,
                    "case": asdict(case_by_id[result.case_id]),
                    "provider_result": asdict(result),
                })
        comparison_rows.extend({"dataset": dataset_name, **row} for row in comparison["cases"])

    payload = {
        "summary": summary,
        "cases": case_rows,
        "stage_attribution": stage_rows,
        "slice_metrics": slice_rows,
        "provider_comparison": comparison_rows,
    }
    write_reports(output_dir, payload)
    return payload
```

Before writing reports, normalize enums and tuples for JSON serialization. Add a private recursive helper:

```python
def _jsonable(value: Any) -> Any:
    if hasattr(value, "value") and value.__class__.__module__ == "enum":
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value
```

Use `_jsonable(asdict(...))` for case and provider result rows. In the final implementation, prefer `isinstance(value, Enum)` by importing `Enum`; do not use the module-string check shown above.

- [ ] **Step 4: Implement the thin CLI**

Create `tools/run_accuracy_baseline.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.providers import GoalShadowProvider, ProductionProvider  # noqa: E402
from eval.accuracy_baseline.runner import run_accuracy_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only accuracy baseline evaluation")
    parser.add_argument("--primary")
    parser.add_argument("--oss-diagnostic")
    parser.add_argument("--historical-stress")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--providers", default="production,goal_shadow")
    parser.add_argument("--goal-top-k", type=int, default=80)
    parser.add_argument("--min-slice-size", type=int, default=20)
    parser.add_argument("--with-experience", action="store_true")
    args = parser.parse_args()

    datasets = {
        name: value
        for name, value in {
            "primary": args.primary,
            "oss_diagnostic": args.oss_diagnostic,
            "historical_stress": args.historical_stress,
        }.items()
        if value
    }
    if not datasets:
        parser.error("at least one dataset path is required")
    requested = {value.strip() for value in args.providers.split(",") if value.strip()}
    providers = []
    if "production" in requested:
        providers.append(ProductionProvider(with_experience=args.with_experience))
    if "goal_shadow" in requested:
        providers.append(GoalShadowProvider(top_k=args.goal_top_k))
    unknown = requested - {"production", "goal_shadow"}
    if unknown:
        parser.error(f"unknown providers: {','.join(sorted(unknown))}")

    payload = run_accuracy_baseline(
        datasets=datasets,
        output_dir=args.output_dir,
        providers=providers,
        min_slice_size=args.min_slice_size,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run runner and CLI tests**

Run:

```powershell
pytest tests/test_accuracy_baseline_runner.py -q
python tools/run_accuracy_baseline.py --help
```

Expected: pytest PASS; CLI prints options and exits 0.

---

### Task 9: Complete Integration and Regression Verification

**Files:**
- Test: all new `tests/test_accuracy_baseline_*.py`
- Test: `tests/test_real_eval_tools.py`
- Test: `tests/test_goal_search.py`

- [ ] **Step 1: Run the complete new evaluation test set**

Run:

```powershell
pytest tests/test_accuracy_baseline_contracts.py tests/test_accuracy_baseline_datasets.py tests/test_accuracy_baseline_lifecycle.py tests/test_accuracy_baseline_metrics.py tests/test_accuracy_baseline_providers.py tests/test_accuracy_baseline_reporting.py tests/test_accuracy_baseline_runner.py -q
```

Expected: all new tests PASS.

- [ ] **Step 2: Run adjacent existing regression tests**

Run:

```powershell
pytest tests/test_real_eval_tools.py tests/test_real_eval_context_fields.py tests/test_goal_search.py -q
```

Expected: all adjacent tests PASS. If a pre-existing unrelated test fails, record it without changing unrelated code.

- [ ] **Step 3: Run a synthetic CLI smoke test**

Use a temporary JSONL under `output/_tmp_accuracy_baseline/primary.jsonl` with one case and run only a fake-free provider when its local province DB is available. If no local DB is available, run the runner integration test instead and explicitly record that real-provider smoke validation was skipped.

Command when a usable dataset and province DB exist:

```powershell
python tools/run_accuracy_baseline.py --primary eval/golden_set.jsonl --providers production --output-dir output/accuracy_baseline/smoke --min-slice-size 20
```

Expected: process exits 0 and writes all five artifacts. A zero-case result caused by unavailable local province data is not an accuracy baseline; report it as an environment limitation.

- [ ] **Step 4: Verify formatting and working-tree scope**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only the planned evaluation files plus pre-existing user changes appear.

- [ ] **Step 5: Produce the implementation handoff summary**

Report:

- files created and the single existing file modified;
- exact tests run and outcomes;
- whether real-provider smoke validation ran;
- remaining data risks: representative primary set, OSS source-family/project provenance, and unavailable province indexes;
- confirmation that no database, production config, model, or online task state was modified.

Do not commit unless the user explicitly authorizes it.
