from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from src.goal_search.national_index import clean_text, extract_signal


def normalize_alias_text(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip().lower())
    return re.sub(r"[|,;:，；：、()（）\[\]【】\"']", "", text)


def _source_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:
        return []
    return [part for part in re.split(r"[;,|]", text) if clean_text(part)]


@dataclass(slots=True)
class GuardedOssAliasCandidate:
    quota_id: str
    support_count: int
    source_families: list[str] = field(default_factory=list)
    source_file_hashes: list[str] = field(default_factory=list)
    oof_folds: list[int] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    alias_key: str = ""

    @property
    def source_family_count(self) -> int:
        return len({value for value in self.source_families if value})


class GuardedOssAliasPriorSource:
    """Default-off strict OSS alias source for candidate generation.

    The source returns traceable candidate evidence only. It does not decide
    final answers and it tolerates a missing index by returning no candidates.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        min_support: int | None = None,
        core_families: set[str] | None = None,
    ):
        self.path = Path(path or getattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", ""))
        self.min_support = int(min_support or getattr(config, "OSS_GUARDED_ALIAS_MIN_SUPPORT", 2) or 2)
        configured = getattr(config, "OSS_GUARDED_ALIAS_CORE_FAMILIES", ("concrete", "rebar", "pipe", "pump", "support"))
        self.core_families = set(core_families or configured)
        self._loaded = False
        self._by_key: dict[tuple[str, str, str], list[GuardedOssAliasCandidate]] = {}

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
                query_family = clean_text(raw.get("query_family"))
                if query_family not in self.core_families:
                    continue
                support_count = int(raw.get("support_count") or 0)
                if support_count < self.min_support:
                    continue
                quota_id = clean_text(raw.get("quota_id"))
                alias_key = clean_text(raw.get("normalized_query") or raw.get("alias_key"))
                province = clean_text(raw.get("province"))
                if not quota_id or not alias_key or not province:
                    continue
                candidate = GuardedOssAliasCandidate(
                    quota_id=quota_id,
                    support_count=support_count,
                    source_families=sorted(set(_source_values(raw.get("source_families")))),
                    source_file_hashes=sorted(set(_source_values(raw.get("source_file_hashes")))),
                    oof_folds=sorted({int(value) for value in raw.get("oof_folds", []) if str(value).strip().isdigit()}),
                    evidence=[item for item in raw.get("evidence", []) if isinstance(item, dict)],
                    alias_key=alias_key,
                )
                self._by_key.setdefault((alias_key, province, query_family), []).append(candidate)
        for candidates in self._by_key.values():
            candidates.sort(key=lambda row: (-row.source_family_count, -row.support_count, row.quota_id))

    def collect(
        self,
        *,
        province: str,
        query_text: str,
        query_family: str = "",
        item: dict[str, Any] | None = None,
        top_k: int = 6,
    ) -> list[dict[str, Any]]:
        self._load()
        province = clean_text(province)
        query_family = clean_text(query_family) or extract_signal(query_text).family
        if not province or query_family not in self.core_families:
            return []
        key = (normalize_alias_text(query_text), province, query_family)
        excluded_hash = clean_text((item or {}).get("source_file_hash"))
        excluded_fold = (item or {}).get("oof_fold")
        results: list[dict[str, Any]] = []
        for candidate in self._by_key.get(key, []):
            usable_evidence = []
            for evidence in candidate.evidence or []:
                if excluded_hash and clean_text(evidence.get("source_file_hash")) == excluded_hash:
                    continue
                if excluded_fold not in (None, ""):
                    try:
                        if int(excluded_fold) == int(evidence.get("oof_fold")):
                            continue
                    except (TypeError, ValueError):
                        pass
                usable_evidence.append(evidence)
            if candidate.evidence:
                support_count = len(usable_evidence)
                source_families = sorted({clean_text(row.get("source_family")) for row in usable_evidence if clean_text(row.get("source_family"))})
            else:
                support_count = candidate.support_count
                source_families = list(candidate.source_families)
            if support_count < self.min_support:
                continue
            results.append(
                {
                    "quota_id": candidate.quota_id,
                    "oss_alias_prior": True,
                    "oss_alias_alias_key": candidate.alias_key,
                    "oss_alias_support_count": support_count,
                    "oss_alias_source_family_count": len(source_families),
                    "oss_alias_source_families": source_families,
                    "knowledge_prior_sources": ["oss_guarded_alias"],
                    "knowledge_prior_score": 0.18,
                    "reason": f"oss_guarded_alias:support{support_count}",
                }
            )
            if len(results) >= top_k:
                break
        return results


_SOURCE: GuardedOssAliasPriorSource | None = None


def get_guarded_oss_alias_prior_source() -> GuardedOssAliasPriorSource:
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = GuardedOssAliasPriorSource()
    return _SOURCE


def reset_guarded_oss_alias_prior_source() -> None:
    global _SOURCE
    _SOURCE = None


def collect_guarded_oss_alias_candidates(
    *,
    province: str,
    query_text: str,
    query_family: str = "",
    item: dict[str, Any] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    if not bool(getattr(config, "OSS_GUARDED_ALIAS_ENABLED", False)):
        return []
    resolved_top_k = int(top_k or getattr(config, "OSS_GUARDED_ALIAS_TOP_K", 6) or 0)
    if resolved_top_k <= 0:
        return []
    return get_guarded_oss_alias_prior_source().collect(
        province=province,
        query_text=query_text,
        query_family=query_family,
        item=item,
        top_k=resolved_top_k,
    )
