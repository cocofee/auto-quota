"""
Unified ranking pipeline skeleton for the ranking refactor.
"""

from __future__ import annotations

from typing import Any

from src.constraint_filter import ConstraintFilter
from src.unified_retrieval import UnifiedRetrieval
from src.unified_scoring_engine import UnifiedScoringEngine


class UnifiedRankingPipeline:
    """Three-stage ranking pipeline shell without production wiring yet."""

    def __init__(
        self,
        *,
        retrieval: UnifiedRetrieval | None = None,
        scoring: UnifiedScoringEngine | None = None,
        constraint_filter: ConstraintFilter | None = None,
    ):
        self.retrieval = retrieval or UnifiedRetrieval()
        self.scoring = scoring or UnifiedScoringEngine()
        self.constraint_filter = constraint_filter or ConstraintFilter()

    def rank(self, query_item: Any, *, top_k: int = 5, retrieval_top_k: int = 100) -> dict[str, Any]:
        retrieval_result = self.retrieval.retrieve(query_item, top_k=retrieval_top_k)
        return self.rank_candidates(
            query_item,
            retrieval_result.get("candidates") or [],
            top_k=top_k,
            sources_used=retrieval_result.get("sources") or [],
            kb_hints=retrieval_result.get("kb_hints") or [],
            retrieval_meta=retrieval_result.get("meta") or {},
            retrieval_errors=retrieval_result.get("errors") or {},
        )

    def rank_candidates(
        self,
        query_item: Any,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 5,
        sources_used: list[str] | None = None,
        kb_hints: list[dict[str, Any]] | None = None,
        retrieval_meta: dict[str, Any] | None = None,
        retrieval_errors: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared_candidates = list(candidates or [])
        scored_candidates = self.scoring.score(query_item, prepared_candidates)
        filter_result = self.constraint_filter.filter(query_item, scored_candidates, top_k=top_k)
        final_candidates = list(filter_result.get("candidates") or [])
        top_candidate = final_candidates[0] if final_candidates else None
        resolved_sources = list(sources_used or self._infer_sources(prepared_candidates))
        resolved_hints = list(kb_hints or [])
        diagnostics = self._build_diagnostics(
            retrieval_result={
                "candidates": prepared_candidates,
                "sources": resolved_sources,
                "errors": dict(retrieval_errors or {}),
                "meta": dict(retrieval_meta or {}),
            },
            scored_candidates=scored_candidates,
            filter_result=filter_result,
            top_candidate=top_candidate,
        )
        return {
            "candidates": final_candidates,
            "total_retrieved": len(prepared_candidates),
            "sources_used": resolved_sources,
            "kb_hints": resolved_hints,
            "rejected_candidates": list(filter_result.get("rejected") or []),
            "top1_score": float((top_candidate or {}).get("filtered_score", (top_candidate or {}).get("unified_score", 0.0)) or 0.0),
            "top1_confidence": float((top_candidate or {}).get("confidence", 0.0) or 0.0),
            "top1_explanation": dict((top_candidate or {}).get("explanation") or {}),
            "diagnostics": diagnostics,
            "meta": {
                "retrieval": dict(retrieval_meta or {}),
                "filter": dict(filter_result.get("meta") or {}),
                "prepared_candidates": True,
                "skeleton": True,
            },
        }

    def _infer_sources(self, candidates: list[dict[str, Any]]) -> list[str]:
        seen: list[str] = []
        for candidate in candidates or []:
            for value in list(candidate.get("sources") or []):
                normalized = str(value or "").strip()
                if normalized and normalized not in seen:
                    seen.append(normalized)
        return seen

    def _build_diagnostics(
        self,
        *,
        retrieval_result: dict[str, Any],
        scored_candidates: list[dict[str, Any]],
        filter_result: dict[str, Any],
        top_candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        final_candidates = list(filter_result.get("candidates") or [])
        rejected_candidates = list(filter_result.get("rejected") or [])
        return {
            "retrieval": {
                "candidate_count": len(retrieval_result.get("candidates") or []),
                "sources_used": list(retrieval_result.get("sources") or []),
                "errors": dict(retrieval_result.get("errors") or {}),
            },
            "scoring": {
                "candidate_count": len(scored_candidates),
                "top_unified_quota_id": str(((scored_candidates or [{}])[0]).get("quota_id", "") or "") if scored_candidates else "",
            },
            "filter": {
                "candidate_count": len(final_candidates),
                "rejected_count": len(rejected_candidates),
                "hard_violation_counts": dict((filter_result.get("meta") or {}).get("hard_violation_counts") or {}),
                "soft_violation_counts": dict((filter_result.get("meta") or {}).get("soft_violation_counts") or {}),
            },
            "selection": {
                "top_quota_id": str((top_candidate or {}).get("quota_id", "") or ""),
                "top_filtered_score": float((top_candidate or {}).get("filtered_score", 0.0) or 0.0),
                "top_driver": str(((top_candidate or {}).get("explanation") or {}).get("top_driver") or ""),
            },
        }
