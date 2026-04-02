"""
Unified retrieval layer skeleton for the ranking refactor.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def _as_item_dict(query_item: Any) -> dict[str, Any]:
    if isinstance(query_item, dict):
        return dict(query_item)
    if query_item is None:
        return {}
    data = getattr(query_item, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    return {"value": query_item}


class UnifiedRetrieval:
    """Unified retrieval facade with source-level fault isolation."""

    def __init__(
        self,
        *,
        hybrid_searcher: Any | None = None,
        cascade_search_fn: Any | None = None,
        bm25_searcher: Any | None = None,
        vector_searcher: Any | None = None,
        experience_db: Any | None = None,
        universal_kb: Any | None = None,
        rule_matcher: Any | None = None,
    ):
        self.hybrid_searcher = hybrid_searcher
        self.cascade_search_fn = cascade_search_fn
        self.bm25_searcher = bm25_searcher
        self.vector_searcher = vector_searcher
        self.experience_db = experience_db
        self.universal_kb = universal_kb
        self.rule_matcher = rule_matcher

    def retrieve(
        self,
        query_item: Any,
        *,
        top_k: int = 100,
        include_prior_candidates: bool = True,
    ) -> dict[str, Any]:
        payload = self._build_payload(query_item)
        item = payload["item"]
        limit = max(int(top_k or 0), 1)
        candidates: dict[str, dict[str, Any]] = {}
        sources: list[str] = []
        errors: dict[str, str] = {}

        if self.hybrid_searcher is not None:
            try:
                hybrid_rows = self._run_hybrid_search(self.hybrid_searcher, payload, top_k=limit)
                if hybrid_rows:
                    sources.append("hybrid")
                    self._merge_candidates(candidates, hybrid_rows, source_name="hybrid")
            except Exception as exc:  # pragma: no cover - defensive logging
                errors["hybrid"] = str(exc)
                logger.warning(f"unified retrieval source failed: hybrid {exc}")

            if include_prior_candidates and hasattr(self.hybrid_searcher, "collect_prior_candidates"):
                try:
                    prior_rows = self.hybrid_searcher.collect_prior_candidates(
                        payload["search_query"],
                        full_query=payload["full_query"],
                        books=payload["books"],
                        item=item,
                        top_k=min(limit, 8),
                    )
                    if prior_rows:
                        sources.append("prior")
                        self._merge_candidates(candidates, self._normalize_candidate_rows(prior_rows), source_name="prior")
                except Exception as exc:  # pragma: no cover - defensive logging
                    errors["prior"] = str(exc)
                    logger.warning(f"unified retrieval source failed: prior {exc}")

        for source_name, source_obj in (
            ("bm25", self.bm25_searcher),
            ("vector", self.vector_searcher),
            ("experience", self.experience_db),
        ):
            if source_obj is None:
                continue
            try:
                normalized = self._run_candidate_source(source_name, source_obj, payload, top_k=limit)
            except Exception as exc:  # pragma: no cover - defensive logging
                errors[source_name] = str(exc)
                logger.warning(f"unified retrieval source failed: {source_name} {exc}")
                continue
            if not normalized:
                continue
            sources.append(source_name)
            self._merge_candidates(candidates, normalized, source_name=source_name)

        rule_matches: list[dict[str, Any]] = []
        if self.rule_matcher is not None:
            try:
                rule_matches = self._run_rule_source(self.rule_matcher, payload)
                if rule_matches:
                    sources.append("rule")
                    self._merge_candidates(candidates, rule_matches, source_name="rule")
            except Exception as exc:  # pragma: no cover - defensive logging
                errors["rule"] = str(exc)
                logger.warning(f"unified retrieval source failed: rule {exc}")

        kb_hints: list[dict[str, Any]] = []
        if self.universal_kb is not None:
            try:
                kb_hints = self._run_kb_source(self.universal_kb, payload, top_k=limit)
                if kb_hints:
                    sources.append("universal_kb")
            except Exception as exc:  # pragma: no cover - defensive logging
                errors["universal_kb"] = str(exc)
                logger.warning(f"unified retrieval kb source failed: {exc}")

        merged_candidates = list(candidates.values())
        merged_candidates.sort(
            key=lambda candidate: (
                float(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0)) or 0.0),
                float(candidate.get("bm25_score", 0.0) or 0.0),
            ),
            reverse=True,
        )

        return {
            "query_item": item,
            "candidates": merged_candidates[:limit],
            "sources": sources,
            "kb_hints": kb_hints[: min(limit, 10)],
            "total_retrieved": len(merged_candidates),
            "errors": errors,
            "meta": {
                "requested_top_k": limit,
                "source_count": len(sources),
                "error_count": len(errors),
            },
        }

    def _build_payload(self, query_item: Any) -> dict[str, Any]:
        item = _as_item_dict(query_item)
        canonical_query = dict(item.get("canonical_query") or {})
        classification = dict(item.get("classification") or {})
        context_prior = dict(item.get("context_prior") or {})
        search_query = str(
            canonical_query.get("search_query")
            or item.get("search_query")
            or item.get("name")
            or ""
        ).strip()
        full_query = " ".join(
            part
            for part in (
                item.get("name", ""),
                item.get("description", ""),
                canonical_query.get("validation_query", ""),
            )
            if str(part or "").strip()
        ).strip() or search_query
        books = [
            str(book).strip()
            for book in (
                classification.get("search_books")
                or classification.get("candidate_books")
                or []
            )
            if str(book).strip()
        ]
        return {
            "item": item,
            "search_query": search_query,
            "full_query": full_query,
            "classification": classification,
            "context_prior": context_prior,
            "books": books or None,
        }

    def _run_candidate_source(
        self,
        source_name: str,
        source_obj: Any,
        payload: dict[str, Any],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if hasattr(source_obj, "search"):
            try:
                raw = source_obj.search(payload["item"], top_k=top_k)
            except TypeError:
                raw = source_obj.search(payload["search_query"], top_k=top_k)
        elif callable(source_obj):
            raw = source_obj(payload["item"], top_k=top_k)
        else:
            raise TypeError(f"{source_name} source does not expose search()")
        return self._normalize_candidate_rows(raw)

    def _run_hybrid_search(
        self,
        searcher: Any,
        payload: dict[str, Any],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if self.cascade_search_fn is not None:
            raw = self.cascade_search_fn(
                searcher,
                payload["search_query"],
                payload["classification"],
                top_k=top_k,
                item=payload["item"],
                context_prior=payload["context_prior"],
            )
        else:
            try:
                raw = searcher.search(
                    payload["search_query"],
                    top_k=top_k,
                    books=payload["books"],
                    item=payload["item"],
                    context_prior=payload["context_prior"],
                )
            except TypeError:
                raw = searcher.search(payload["search_query"], top_k=top_k)
        return self._normalize_candidate_rows(raw)

    def _run_rule_source(self, rule_source: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
        item = payload["item"]
        if hasattr(rule_source, "match"):
            raw = rule_source.match(item)
        elif callable(rule_source):
            raw = rule_source(item)
        else:
            raise TypeError("rule source does not expose match()")
        return self._normalize_candidate_rows(raw)

    def _run_kb_source(self, kb_source: Any, payload: dict[str, Any], *, top_k: int) -> list[dict[str, Any]]:
        query_text = payload["search_query"]
        if hasattr(kb_source, "get_hints"):
            raw = kb_source.get_hints(payload["item"])
        elif hasattr(kb_source, "get_search_keywords"):
            raw = kb_source.get_search_keywords(query_text)
        elif hasattr(kb_source, "search_hints"):
            raw = kb_source.search_hints(query_text, top_k=top_k)
        elif callable(kb_source):
            raw = kb_source(payload["item"], top_k=top_k)
        else:
            raise TypeError("universal_kb source does not expose get_hints()")

        if raw is None:
            return []
        if isinstance(raw, list):
            normalized = []
            for row in raw:
                if isinstance(row, dict):
                    normalized.append(dict(row))
                else:
                    normalized.append({"hint": row, "keyword": str(row).strip()})
            return normalized
        return [dict(raw)] if isinstance(raw, dict) else [{"hint": raw, "keyword": str(raw).strip()}]

    def _normalize_candidate_rows(self, raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, dict):
            if all(isinstance(value, dict) for value in raw.values()):
                rows = [dict(value) for value in raw.values()]
            else:
                rows = [dict(raw)]
        elif isinstance(raw, (list, tuple)):
            rows = [dict(row) for row in raw if isinstance(row, dict)]
        else:
            rows = []
        return rows

    def _merge_candidates(
        self,
        merged: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
        *,
        source_name: str,
    ) -> None:
        for index, row in enumerate(rows):
            candidate = dict(row)
            quota_id = str(
                candidate.get("quota_id")
                or candidate.get("id")
                or candidate.get("name")
                or f"{source_name}:{index}"
            ).strip()
            existing = merged.get(quota_id)
            sources = list(candidate.get("sources") or [])
            for inferred_source in self._infer_candidate_sources(candidate, source_name=source_name):
                if inferred_source not in sources:
                    sources.append(inferred_source)
            candidate["sources"] = sources
            if source_name == "experience" or "experience" in sources:
                candidate["from_experience"] = True
            if source_name == "rule" or "rule" in sources:
                candidate["from_rule"] = True
            if existing is None:
                merged[quota_id] = candidate
                continue
            merged_candidate = dict(existing)
            merged_candidate.update(candidate)
            merged_sources = list(existing.get("sources") or [])
            for value in sources:
                if value not in merged_sources:
                    merged_sources.append(value)
            merged_candidate["sources"] = merged_sources
            merged[quota_id] = merged_candidate

    def _infer_candidate_sources(self, candidate: dict[str, Any], *, source_name: str) -> list[str]:
        sources: list[str] = []
        if source_name == "hybrid":
            sources.append("hybrid")
            if candidate.get("bm25_rank") is not None or candidate.get("bm25_score") is not None:
                sources.append("bm25")
            if candidate.get("vector_rank") is not None or candidate.get("vector_score") is not None:
                sources.append("vector")
            return sources

        if source_name == "prior":
            sources.append("prior")
            for value in list(candidate.get("knowledge_prior_sources") or []):
                normalized = str(value or "").strip().lower()
                if normalized == "universal_kb":
                    normalized = "universal_kb"
                if normalized and normalized not in sources:
                    sources.append(normalized)
            return sources

        sources.append(source_name)
        return sources
