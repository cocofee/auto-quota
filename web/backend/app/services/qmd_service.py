"""
QMD knowledge search service.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.schemas.qmd import QMDSearchRequest, QMDSearchResponse
from src.qmd_index import QMDIndex


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _trim_text(value: Any, limit: int) -> str:
    text = _clean_str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _compact_filters(
    *,
    category: str = "",
    page_type: str = "",
    province: str = "",
    specialty: str = "",
    source_kind: str = "",
    status: str = "",
) -> dict[str, str]:
    payload = {
        "category": _clean_str(category),
        "page_type": _clean_str(page_type),
        "province": _clean_str(province),
        "specialty": _clean_str(specialty),
        "source_kind": _clean_str(source_kind),
        "status": _clean_str(status),
    }
    return {key: value for key, value in payload.items() if value}


class QMDService:
    def __init__(self, index: QMDIndex | None = None) -> None:
        self.index = index or QMDIndex()

    def search(self, request: QMDSearchRequest) -> QMDSearchResponse:
        query = _clean_str(request.query)
        if not query:
            raise ValueError("query 不能为空")

        filters = _compact_filters(
            category=request.category,
            page_type=request.page_type,
            province=request.province,
            specialty=request.specialty,
            source_kind=request.source_kind,
            status=request.status,
        )
        hits = self.index.search(
            query,
            top_k=request.top_k,
            category=request.category or None,
            page_type=request.page_type or None,
            province=request.province or None,
            specialty=request.specialty or None,
            source_kind=request.source_kind or None,
            status=request.status or None,
        )
        normalized_hits = [self._normalize_hit(item) for item in hits]
        return QMDSearchResponse(
            query=query,
            count=len(normalized_hits),
            filters=filters,
            hits=normalized_hits,
        )

    def recall_for_review_context(
        self,
        task: Any,
        match_result: Any,
        *,
        top_k: int = 3,
    ) -> dict[str, Any]:
        query = self.build_review_query(task, match_result)
        if not query:
            return {
                "query": "",
                "count": 0,
                "filters": {},
                "hits": [],
            }
        try:
            response = self.search(QMDSearchRequest(query=query, top_k=top_k))
            return response.model_dump()
        except Exception as exc:
            return {
                "query": query,
                "count": 0,
                "filters": {},
                "hits": [],
                "error": _trim_text(str(exc), 240),
            }

    @staticmethod
    def build_review_query(task: Any, match_result: Any) -> str:
        parts = [
            _clean_str(getattr(match_result, "bill_name", "")),
            _clean_str(getattr(match_result, "bill_description", "")),
            _clean_str(getattr(match_result, "specialty", "")),
            _clean_str(getattr(task, "province", "")),
        ]
        seen: set[str] = set()
        ordered: list[str] = []
        for part in parts:
            if not part or part in seen:
                continue
            seen.add(part)
            ordered.append(part)
        return " ".join(ordered)

    @staticmethod
    def _normalize_hit(item: dict[str, Any]) -> dict[str, Any]:
        document = _trim_text(item.get("document"), 1200)
        preview = _clean_str(item.get("preview")) or _trim_text(document, 220)
        return {
            "chunk_id": _clean_str(item.get("chunk_id")),
            "score": float(item.get("score") or 0.0),
            "title": _clean_str(item.get("title")),
            "heading": _clean_str(item.get("heading")),
            "category": _clean_str(item.get("category")),
            "page_type": _clean_str(item.get("type")),
            "path": _clean_str(item.get("path")),
            "province": _clean_str(item.get("province")),
            "specialty": _clean_str(item.get("specialty")),
            "status": _clean_str(item.get("status")),
            "source_kind": _clean_str(item.get("source_kind")),
            "source_refs_text": _clean_str(item.get("source_refs_text")),
            "preview": preview,
            "document": document,
        }


@lru_cache(maxsize=1)
def get_default_qmd_service() -> QMDService:
    return QMDService()
