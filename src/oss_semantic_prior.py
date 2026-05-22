from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import config
from src.candidate_canonicalizer import build_candidate_canonical_features_no_store


_BAD_TEXT_VALUES = {"", "unknown", "??", "??/??"}


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _candidate_score(raw_score: object) -> float:
    try:
        score = float(raw_score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 0:
        return 0.36
    # Keep OSS semantic prior as candidate-retention evidence, not a final-answer override.
    return max(0.36, min(0.58, score / 16.0))


def build_no_store_canonical_features_for_text(text: str, *, specialty: str = "") -> dict[str, Any]:
    candidate = {"name": _clean_text(text), "specialty": _clean_text(specialty)}
    return build_candidate_canonical_features_no_store(candidate, specialty=specialty)


def _feature_key(features: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("canonical_name", "entity", "family", "material", "connection", "install_method"):
        value = _clean_text((features or {}).get(key))
        if value and value not in _BAD_TEXT_VALUES:
            parts.append(value)
    numeric = features.get("numeric_params") if isinstance(features, dict) else {}
    if isinstance(numeric, dict):
        for key in ("dn", "cable_section", "half_perimeter", "perimeter", "kw", "ampere"):
            value = numeric.get(key)
            if value not in (None, "", []):
                parts.append(f"{key}:{value}")
    return "|".join(parts)


class OssSemanticPriorSource:
    """Read-only OSS semantic prior source.

    The source consumes a precomputed shadow JSONL and emits local quota candidates
    as weak prior evidence. It never uses source OSS quota IDs as answers and never
    writes candidate feature caches.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or getattr(config, "OSS_SEMANTIC_PRIOR_SHADOW_PATH", ""))
        self._loaded = False
        self._by_province_text: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._by_province_feature: dict[tuple[str, str], list[dict[str, Any]]] = {}

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
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                province = _clean_text(record.get("province"))
                candidates = [
                    self._materialize_candidate(raw, record)
                    for raw in list(record.get("top_candidates") or [])[:50]
                ]
                candidates = [candidate for candidate in candidates if candidate]
                if not province or not candidates:
                    continue

                keys = {
                    _normalize_text(record.get("bill_name")),
                    _normalize_text(record.get("bill_core")),
                }
                for key in keys:
                    if key:
                        self._by_province_text[(province, key)] = candidates

                feature_key = _feature_key(record.get("target_feature_snapshot") or {})
                if feature_key:
                    self._by_province_feature[(province, feature_key)] = candidates

    def _materialize_candidate(self, raw: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
        quota_id = _clean_text(raw.get("quota_id"))
        name = _clean_text(raw.get("name"))
        if not quota_id or not name:
            return None
        candidate: dict[str, Any] = {
            "quota_id": quota_id,
            "name": name,
            "unit": _clean_text(raw.get("unit")),
            "match_source": "oss_semantic_prior_shadow",
            "candidate_source": "oss_semantic_prior_shadow",
            "knowledge_prior_sources": ["oss_semantic_prior_shadow"],
            "knowledge_prior_score": _candidate_score(raw.get("score")),
            "oss_semantic_prior_shadow": True,
            "oss_semantic_prior_decision_authority": False,
            "oss_semantic_prior_reason": list(raw.get("why") or []),
            "oss_semantic_prior_bill_name": _clean_text(record.get("bill_name")),
            "oss_semantic_prior_bucket": _clean_text(record.get("bucket")),
        }
        features = build_candidate_canonical_features_no_store(candidate)
        if features:
            candidate["canonical_features"] = dict(features)
        return candidate

    def collect(
        self,
        *,
        province: str,
        query_text: str = "",
        full_query: str = "",
        item: dict[str, Any] | None = None,
        top_k: int = 6,
    ) -> list[dict[str, Any]]:
        self._load()
        province = _clean_text(province)
        if not province:
            return []

        texts = [
            query_text,
            full_query,
            (item or {}).get("name") if isinstance(item, dict) else "",
            (item or {}).get("bill_name") if isinstance(item, dict) else "",
        ]
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()

        for text in texts:
            key = _normalize_text(text)
            if not key:
                continue
            for candidate in self._by_province_text.get((province, key), []):
                quota_id = _clean_text(candidate.get("quota_id"))
                if quota_id and quota_id not in seen:
                    matches.append(dict(candidate))
                    seen.add(quota_id)
                    if len(matches) >= top_k:
                        return matches

        item_features = (item or {}).get("canonical_features") if isinstance(item, dict) else {}
        if isinstance(item_features, dict):
            feature_key = _feature_key(item_features)
            for candidate in self._by_province_feature.get((province, feature_key), []):
                quota_id = _clean_text(candidate.get("quota_id"))
                if quota_id and quota_id not in seen:
                    matches.append(dict(candidate))
                    seen.add(quota_id)
                    if len(matches) >= top_k:
                        return matches
        return matches[:top_k]


_SOURCE: OssSemanticPriorSource | None = None


def get_oss_semantic_prior_source() -> OssSemanticPriorSource:
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = OssSemanticPriorSource()
    return _SOURCE


def collect_oss_semantic_prior_candidates(
    *,
    province: str,
    query_text: str = "",
    full_query: str = "",
    item: dict[str, Any] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    if not bool(getattr(config, "OSS_SEMANTIC_PRIOR_ENABLED", False)):
        return []
    resolved_top_k = int(top_k or getattr(config, "OSS_SEMANTIC_PRIOR_TOP_K", 6) or 0)
    if resolved_top_k <= 0:
        return []
    return get_oss_semantic_prior_source().collect(
        province=province,
        query_text=query_text,
        full_query=full_query,
        item=item,
        top_k=resolved_top_k,
    )
