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
