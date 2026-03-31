"""
Reranker module.

The reranker keeps two views of the same query:
1. semantic view: strips numeric spec tokens so the model focuses on item type
2. spec view: keeps the original query so DN/sections/loops remain visible

Spec-heavy routes use the spec view as the active rerank score while still
preserving the semantic score for diagnostics.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from loguru import logger

import config
from src.query_router import normalize_query_route


@dataclass(frozen=True)
class _BackendKey:
    backend: str
    model_name: str
    model_type: str | None = None


class _BaseRerankerBackend:
    name = "base"

    def __init__(self, model_name: str, model_type: str | None = None):
        self.model_name = model_name
        self.model_type = model_type
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = self.load_model()
        return self._model

    def load_model(self):
        raise NotImplementedError

    def score(self, query: str, candidates: list[dict], text_getter) -> list[float]:
        raise NotImplementedError


class _CrossEncoderBackend(_BaseRerankerBackend):
    name = "cross_encoder"

    def load_model(self):
        from src.model_cache import ModelCache

        return ModelCache.get_reranker_model()

    def score(self, query: str, candidates: list[dict], text_getter) -> list[float]:
        pairs = [[query, text_getter(candidate)] for candidate in candidates]
        return [float(score) for score in self.model.predict(pairs)]


class _RerankersBackend(_BaseRerankerBackend):
    name = "rerankers"
    _cache: dict[_BackendKey, object] = {}
    _lock = threading.Lock()

    @classmethod
    def clear_cache(cls):
        with cls._lock:
            cls._cache.clear()

    def load_model(self):
        try:
            from rerankers import Reranker as ExternalReranker
        except Exception as exc:
            logger.warning(f"rerankers backend unavailable, fallback required: {exc}")
            return None

        cache_key = _BackendKey(
            backend=self.name,
            model_name=self.model_name,
            model_type=self.model_type,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        kwargs = {}
        if self.model_type:
            kwargs["model_type"] = self.model_type
        try:
            model = ExternalReranker(self.model_name, **kwargs)
        except Exception as exc:
            logger.warning(f"rerankers backend load failed: {exc}")
            return None

        with self._lock:
            return self._cache.setdefault(cache_key, model)

    def score(self, query: str, candidates: list[dict], text_getter) -> list[float]:
        docs = [text_getter(candidate) for candidate in candidates]
        doc_ids = [str(index) for index in range(len(candidates))]
        ranked = self.model.rank(query, docs=docs, doc_ids=doc_ids)
        results = getattr(ranked, "results", ranked)
        scores = [0.0 for _ in candidates]
        for result in results or []:
            score = float(getattr(result, "score", 0.0) or 0.0)
            doc_id = getattr(getattr(result, "document", None), "doc_id", None)
            if doc_id is None:
                doc_id = getattr(result, "doc_id", None)
            try:
                index = int(doc_id)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(scores):
                scores[index] = score
        return scores


class _DisabledBackend(_BaseRerankerBackend):
    name = "disabled"

    def load_model(self):
        return None

    def score(self, query: str, candidates: list[dict], text_getter) -> list[float]:
        return [0.0 for _ in candidates]


class _UnavailableBackend(_BaseRerankerBackend):
    name = "unavailable"

    def __init__(self, backend_name: str, model_name: str, model_type: str | None = None):
        super().__init__(model_name=model_name, model_type=model_type)
        self.backend_name = backend_name

    def load_model(self):
        logger.warning(f"Unknown reranker backend '{self.backend_name}', fallback required")
        return None

    def score(self, query: str, candidates: list[dict], text_getter) -> list[float]:
        return [0.0 for _ in candidates]


class Reranker:
    """Semantic/spec-aware reranker with pluggable backends."""

    _BACKENDS = {
        _CrossEncoderBackend.name: _CrossEncoderBackend,
        _DisabledBackend.name: _DisabledBackend,
        _RerankersBackend.name: _RerankersBackend,
    }

    def __init__(self, model_name: str = None, backend: str = None, model_type: str = None):
        self.model_name = model_name or config.RERANKER_MODEL_NAME
        self.backend = str(backend or config.RERANKER_BACKEND or "cross_encoder").strip().lower()
        self.model_type = model_type or config.RERANKERS_MODEL_TYPE
        self._model = None
        backend_cls = self._BACKENDS.get(self.backend)
        if backend_cls is None:
            self._backend = _UnavailableBackend(
                backend_name=self.backend,
                model_name=self.model_name,
                model_type=self.model_type,
            )
        else:
            self._backend = backend_cls(
                model_name=self.model_name,
                model_type=self.model_type,
            )

    @property
    def model(self):
        if self._model is not None:
            return self._model
        return self._backend.model

    @classmethod
    def clear_backend_caches(cls):
        _RerankersBackend.clear_cache()

    @staticmethod
    def _candidate_text(candidate: dict) -> str:
        text = str(
            candidate.get("reranker_text")
            or candidate.get("name")
            or candidate.get("quota_name")
            or ""
        ).strip()
        return text

    def _predict_scores(self, active_query: str, candidates: list[dict]) -> list[float]:
        if self._model is not None:
            if hasattr(self._model, "rank"):
                docs = [self._candidate_text(candidate) for candidate in candidates]
                doc_ids = [str(index) for index in range(len(candidates))]
                ranked = self._model.rank(active_query, docs=docs, doc_ids=doc_ids)
                results = getattr(ranked, "results", ranked)
                scores = [0.0 for _ in candidates]
                for result in results or []:
                    score = float(getattr(result, "score", 0.0) or 0.0)
                    doc_id = getattr(getattr(result, "document", None), "doc_id", None)
                    if doc_id is None:
                        doc_id = getattr(result, "doc_id", None)
                    try:
                        index = int(doc_id)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= index < len(scores):
                        scores[index] = score
                return scores
            if hasattr(self._model, "predict"):
                pairs = [[active_query, self._candidate_text(candidate)] for candidate in candidates]
                return [float(score) for score in self._model.predict(pairs)]
        return self._backend.score(active_query, candidates, self._candidate_text)

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = None,
        route_profile=None,
    ) -> list[dict]:
        if not candidates:
            return candidates

        if top_k is None:
            top_k = config.RERANKER_TOP_K

        if not getattr(config, "VECTOR_ENABLED", True):
            return candidates[:top_k] if top_k else candidates

        model = self.model
        if model is None:
            logger.warning(f"Reranker model unavailable for backend={self.backend}, skipping rerank")
            for candidate in candidates:
                candidate["reranker_failed"] = True
                candidate["reranker_backend"] = self.backend
            return candidates[:top_k] if top_k else candidates

        route = normalize_query_route(route_profile)
        use_spec_rerank = route in {"installation_spec", "spec_heavy", "ambiguous_short"}
        semantic_query = self._strip_numbers(query) or str(query or "").strip()
        spec_query = str(query or "").strip()

        try:
            semantic_scores = self._predict_scores(semantic_query, candidates)
            if use_spec_rerank and spec_query and spec_query != semantic_query:
                spec_scores = self._predict_scores(spec_query, candidates)
            else:
                spec_scores = semantic_scores
        except Exception as exc:
            logger.error(f"Reranker scoring failed for backend={self.backend}: {exc}")
            for candidate in candidates:
                candidate["reranker_failed"] = True
                candidate["reranker_backend"] = self.backend
            return candidates[:top_k] if top_k else candidates

        for idx, candidate in enumerate(candidates):
            semantic_score = float(semantic_scores[idx])
            spec_score = float(spec_scores[idx])
            active_score = spec_score if use_spec_rerank else semantic_score
            candidate["semantic_rerank_score"] = semantic_score
            candidate["spec_rerank_score"] = spec_score
            candidate["active_rerank_score"] = active_score
            candidate["rerank_score"] = active_score
            candidate["reranker_backend"] = self.backend
            candidate["reranker_model_name"] = self.model_name

        ranked = sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)
        if top_k and len(ranked) > top_k:
            ranked = ranked[:top_k]
        return ranked

    @staticmethod
    def _strip_numbers(text: str) -> str:
        text = re.sub(r"\b[\d.]+[脳/][\d.]+\b", "", text or "")
        text = re.sub(r"\b[\d.]+\b", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
