from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from src.goal_search.national_index import QuotaSignal, clean_text, extract_signal, tokenize
from src.goal_search.oss_alias_prior import normalize_alias_text

GENERIC_TERMS = {
    "concrete",
    "pipe",
    "pump",
    "rebar",
    "support",
    "install",
    "make",
    "made",
    "work",
    "project",
}


def recall_terms(value: object, *, limit: int = 24) -> list[str]:
    text = clean_text(value)
    signal = extract_signal(text)
    raw_terms = list(signal.tokens or tokenize(text))
    raw_terms.extend(
        clean_text(item)
        for item in (
            signal.family,
            signal.action,
            signal.material,
            signal.connection,
            signal.install_method,
            f"dn:{signal.dn:g}" if signal.dn is not None else "",
            f"cable_section:{signal.cable_section:g}" if signal.cable_section is not None else "",
            f"cable_cores:{signal.cable_cores}" if signal.cable_cores is not None else "",
            f"circuits:{signal.circuits}" if signal.circuits is not None else "",
            f"concrete_grade:{signal.concrete_grade}" if signal.concrete_grade is not None else "",
            f"thickness:{signal.thickness:g}" if signal.thickness is not None else "",
        )
    )
    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        term = normalize_alias_text(term)
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class OssRecallCandidate:
    quota_id: str
    province: str
    query_family: str
    bill_name_key: str
    bill_name_keys: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    bill_terms: set[str] = field(default_factory=set)
    quota_terms: set[str] = field(default_factory=set)
    quota_names: list[str] = field(default_factory=list)
    support_count: int = 0
    source_families: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    signal: dict[str, Any] = field(default_factory=dict)

    @property
    def source_family_count(self) -> int:
        return len({item for item in self.source_families if item})


class OssRecallPriorSource:
    """Default-off OSS recall/index source for candidate generation."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        min_support: int | None = None,
        min_source_families: int | None = None,
        min_overlap: int | None = None,
        min_specific_overlap: int | None = None,
        intervention_mode: str | None = None,
        core_families: set[str] | None = None,
    ) -> None:
        self.path = Path(path or getattr(config, "OSS_RECALL_INDEX_PATH", ""))
        self.min_support = int(min_support or getattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 2) or 2)
        self.min_source_families = int(
            min_source_families or getattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", 2) or 2
        )
        self.min_overlap = int(min_overlap or getattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", 2) or 2)
        self.min_specific_overlap = int(
            min_specific_overlap or getattr(config, "OSS_RECALL_INDEX_MIN_SPECIFIC_OVERLAP", 1) or 1
        )
        mode = clean_text(intervention_mode or getattr(config, "OSS_RECALL_INDEX_INTERVENTION_MODE", "broad"))
        self.intervention_mode = mode if mode in {"broad", "exact_name"} else "broad"
        configured = getattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", ("concrete", "rebar", "pipe", "pump", "support"))
        self.core_families = set(core_families or configured)
        self._loaded = False
        self._by_scope: dict[tuple[str, str], list[OssRecallCandidate]] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                province = clean_text(raw.get("province"))
                query_family = clean_text(raw.get("query_family"))
                quota_id = clean_text(raw.get("quota_id"))
                support_count = _safe_int(raw.get("support_count"))
                if not province or not quota_id or query_family not in self.core_families:
                    continue
                if support_count < self.min_support:
                    continue
                source_families = [clean_text(item) for item in raw.get("source_families", []) if clean_text(item)]
                if len(set(source_families)) < self.min_source_families:
                    continue
                candidate = OssRecallCandidate(
                    quota_id=quota_id,
                    province=province,
                    query_family=query_family,
                    bill_name_key=clean_text(raw.get("bill_name_key")),
                    bill_name_keys={clean_text(term) for term in raw.get("bill_name_keys", []) if clean_text(term)},
                    terms={clean_text(term) for term in raw.get("terms", []) if clean_text(term)},
                    bill_terms={clean_text(term) for term in raw.get("bill_terms", []) if clean_text(term)},
                    quota_terms={clean_text(term) for term in raw.get("quota_terms", []) if clean_text(term)},
                    quota_names=[clean_text(item) for item in raw.get("quota_names", []) if clean_text(item)],
                    support_count=support_count,
                    source_families=source_families,
                    evidence=[item for item in raw.get("evidence", []) if isinstance(item, dict)],
                    signal=raw.get("signal") if isinstance(raw.get("signal"), dict) else {},
                )
                self._by_scope.setdefault((province, query_family), []).append(candidate)
        for candidates in self._by_scope.values():
            candidates.sort(key=lambda row: (-row.source_family_count, -row.support_count, row.quota_id))

    @staticmethod
    def _conflicts(query_signal: QuotaSignal, candidate: OssRecallCandidate) -> bool:
        signal = candidate.signal or {}
        for key in ("action", "material", "connection", "install_method"):
            query_value = clean_text(getattr(query_signal, key, ""))
            candidate_value = clean_text(signal.get(key))
            if query_value and candidate_value and query_value != candidate_value:
                return True
        checks = (
            ("dn", query_signal.dn),
            ("cable_section", query_signal.cable_section),
            ("cable_cores", query_signal.cable_cores),
            ("circuits", query_signal.circuits),
            ("concrete_grade", query_signal.concrete_grade),
            ("thickness", query_signal.thickness),
        )
        for key, query_value in checks:
            if query_value is None or signal.get(key) in (None, ""):
                continue
            try:
                candidate_value = float(signal[key])
                query_float = float(query_value)
            except (TypeError, ValueError):
                continue
            if abs(candidate_value - query_float) > 1e-6:
                return True
        return False

    @staticmethod
    def _specific_terms(terms: set[str], family: str) -> set[str]:
        generic = set(GENERIC_TERMS)
        if family:
            generic.add(family)
        return {term for term in terms if term not in generic and not re.fullmatch(r"\d+(?:\.\d+)?", term)}

    def collect(
        self,
        *,
        province: str,
        query_text: str,
        query_family: str = "",
        item: dict[str, Any] | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        self._load()
        province = clean_text(province)
        query_signal = extract_signal(query_text)
        query_family = clean_text(query_family) or query_signal.family
        if not province or query_family not in self.core_families:
            return []
        query_terms = set(recall_terms(query_text))
        if len(query_terms) < self.min_overlap:
            return []
        query_specific = self._specific_terms(query_terms, query_family)
        query_name_key = normalize_alias_text((item or {}).get("bill_name") or query_text)
        scored: list[tuple[float, OssRecallCandidate, int, int, int, int, bool]] = []
        for candidate in self._by_scope.get((province, query_family), []):
            if self._conflicts(query_signal, candidate):
                continue
            overlap = len(query_terms & candidate.terms)
            exact_name = bool(query_name_key and (query_name_key == candidate.bill_name_key or query_name_key in candidate.bill_name_keys))
            if self.intervention_mode == "exact_name" and not exact_name:
                continue
            if overlap < self.min_overlap and not exact_name:
                continue
            quota_name_overlap = len(query_terms & candidate.quota_terms)
            quota_specific_overlap = len(query_specific & self._specific_terms(candidate.quota_terms, query_family))
            specific_overlap = len(query_specific & self._specific_terms(candidate.terms, query_family))
            bill_overlap = len(query_terms & candidate.bill_terms)
            if not exact_name and quota_specific_overlap < self.min_specific_overlap:
                continue
            score = (
                overlap * 1.0
                + (2.5 if exact_name else 0.0)
                + quota_name_overlap * 0.6
                + quota_specific_overlap * 1.2
                + specific_overlap * 0.9
                + bill_overlap * 0.25
                + min(candidate.support_count, 8) * 0.15
                + candidate.source_family_count * 0.35
            )
            scored.append((score, candidate, overlap, quota_name_overlap, specific_overlap, quota_specific_overlap, exact_name))
        scored.sort(key=lambda row: (-row[0], -row[1].source_family_count, -row[1].support_count, row[1].quota_id))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for score, candidate, overlap, quota_name_overlap, specific_overlap, quota_specific_overlap, exact_name in scored:
            if candidate.quota_id in seen:
                continue
            seen.add(candidate.quota_id)
            results.append(
                {
                    "quota_id": candidate.quota_id,
                    "oss_recall_prior": True,
                    "oss_recall_support_count": candidate.support_count,
                    "oss_recall_source_family_count": candidate.source_family_count,
                    "oss_recall_overlap": overlap,
                    "oss_recall_quota_name_overlap": quota_name_overlap,
                    "oss_recall_specific_overlap": specific_overlap,
                    "oss_recall_quota_specific_overlap": quota_specific_overlap,
                    "oss_recall_exact_name": exact_name,
                    "oss_recall_intervention_mode": self.intervention_mode,
                    "oss_recall_source_families": candidate.source_families,
                    "knowledge_prior_sources": ["oss_recall_index"],
                    "knowledge_prior_score": min(
                        0.2,
                        0.06
                        + overlap * 0.01
                        + quota_name_overlap * 0.01
                        + quota_specific_overlap * 0.02
                        + specific_overlap * 0.02
                        + candidate.source_family_count * 0.01,
                    ),
                    "reason": f"oss_recall_index:overlap{overlap}/support{candidate.support_count}",
                }
            )
            if len(results) >= top_k:
                break
        return results


_SOURCE: OssRecallPriorSource | None = None


def get_oss_recall_prior_source() -> OssRecallPriorSource:
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = OssRecallPriorSource()
    return _SOURCE


def reset_oss_recall_prior_source() -> None:
    global _SOURCE
    _SOURCE = None


def collect_oss_recall_candidates(
    *,
    province: str,
    query_text: str,
    query_family: str = "",
    item: dict[str, Any] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    if not bool(getattr(config, "OSS_RECALL_INDEX_ENABLED", False)):
        return []
    resolved_top_k = int(top_k or getattr(config, "OSS_RECALL_INDEX_TOP_K", 8) or 0)
    if resolved_top_k <= 0:
        return []
    return get_oss_recall_prior_source().collect(
        province=province,
        query_text=query_text,
        query_family=query_family,
        item=item,
        top_k=resolved_top_k,
    )
