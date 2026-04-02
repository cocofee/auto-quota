"""
Unified retrieval layer across experience, knowledge, price, and quota sources.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any

from loguru import logger

from src.experience_db import ExperienceDB
from src.price_reference_db import PriceReferenceDB
from src.quota_db import QuotaDB
from src.universal_kb import UniversalKB
from src.vector_engine import VectorEngine


class UnifiedDataLayer:
    """统一数据融合层。"""

    _ALL_SOURCES = ("experience", "universal_kb", "price", "quota")

    def __init__(
        self,
        *,
        province: str | None = None,
        experience_db: Any | None = None,
        universal_kb: Any | None = None,
        price_db: Any | None = None,
        quota_db: Any | None = None,
        vector_engine: Any | None = None,
    ):
        self.province = province
        self.experience_db = experience_db or ExperienceDB(province=province)
        self.universal_kb = universal_kb or UniversalKB()
        self.price_db = price_db or PriceReferenceDB()
        self.quota_db = quota_db or QuotaDB(province=province)
        self._vector_engine = vector_engine

    @property
    def vector_engine(self):
        if self._vector_engine is None:
            self._vector_engine = VectorEngine(self.province)
        return self._vector_engine

    def search(
        self,
        query: str | dict[str, Any],
        *,
        sources: list[str] | tuple[str, ...] | None = None,
        strategy: str = "auto",
        top_k: int = 10,
        timeout: float = 5.0,
        authority_only: bool = True,
    ) -> dict[str, Any]:
        """统一搜索接口。"""
        payload = self._build_query_payload(query)
        requested_sources = self._normalize_sources(sources)
        grouped_results: dict[str, list[dict[str, Any]]] = {
            "experience": [],
            "universal_kb": [],
            "price": [],
            "quota": [],
        }
        failures: dict[str, str] = {}
        raw_results: dict[str, Any] = {}

        if not requested_sources:
            return {
                "query": payload,
                "strategy": strategy,
                "items": [],
                "grouped": grouped_results,
                "meta": {
                    "requested_sources": [],
                    "completed_sources": [],
                    "failed_sources": [],
                    "total_items": 0,
                },
                "raw_results": raw_results,
            }

        with ThreadPoolExecutor(max_workers=min(len(requested_sources), 4)) as executor:
            future_map = {
                executor.submit(self._search_source, source, payload, top_k, authority_only): source
                for source in requested_sources
            }
            pending = set(future_map)

            try:
                for future in as_completed(future_map, timeout=timeout):
                    source = future_map[future]
                    pending.discard(future)
                    try:
                        raw_result, normalized_items = future.result()
                        raw_results[source] = raw_result
                        grouped_results[source] = normalized_items
                    except Exception as exc:  # pragma: no cover - defensive logging
                        failures[source] = str(exc)
                        logger.warning(f"{source} search failed: {exc}")
            except FuturesTimeoutError:
                for future in list(pending):
                    source = future_map[future]
                    future.cancel()
                    failures[source] = f"search timeout after {timeout:.1f}s"
                    logger.warning(f"{source} search timed out after {timeout:.1f}s")

        merged_items = self._merge_results(grouped_results, strategy=strategy, top_k=top_k)
        completed_sources = [source for source in requested_sources if source not in failures]

        return {
            "query": payload,
            "strategy": strategy,
            "items": merged_items,
            "grouped": grouped_results,
            "meta": {
                "requested_sources": requested_sources,
                "completed_sources": completed_sources,
                "failed_sources": sorted(failures),
                "failures": failures,
                "total_items": len(merged_items),
                "group_counts": {source: len(items) for source, items in grouped_results.items()},
            },
            "raw_results": raw_results,
        }

    def _search_source(
        self,
        source: str,
        payload: dict[str, Any],
        top_k: int,
        authority_only: bool,
    ) -> tuple[Any, list[dict[str, Any]]]:
        if source == "experience":
            raw = self.experience_db.search_experience(
                payload["text"],
                top_k=max(top_k, 3),
                min_confidence=payload["min_confidence"],
                province=payload["province"],
                specialty=payload["specialty"],
                unit=payload["unit"],
                materials_signature=payload["materials_signature"],
                install_method=payload["install_method"],
                quota_version=payload["quota_version"],
            )
            return raw, self._normalize_experience_results(raw)

        if source == "universal_kb":
            raw = self.universal_kb.search_hints(
                payload["text"],
                top_k=max(min(top_k, 5), 1),
                authority_only=authority_only,
            )
            return raw, self._normalize_universal_kb_results(raw)

        if source == "price":
            raw = self.price_db.search_composite_prices(
                query=payload["text"],
                specialty=payload["specialty"],
                quota_code=payload["quota_code"],
                region=payload["region"],
                page=1,
                size=max(top_k, 5),
            )
            return raw, self._normalize_price_results(raw)

        if source == "quota":
            raw = self._search_quota_candidates(payload, top_k=top_k)
            return raw, self._normalize_quota_results(raw)

        raise ValueError(f"unsupported source: {source}")

    def _search_quota_candidates(self, payload: dict[str, Any], *, top_k: int) -> dict[str, Any]:
        keyword_rows = self.quota_db.search_by_keywords(
            payload["text"],
            chapter=payload["chapter"] or None,
            book=payload["book"] or None,
            limit=max(top_k * 2, top_k),
        )

        vector_rows: list[dict[str, Any]] = []
        try:
            vector_rows = self.vector_engine.search(
                payload["text"],
                top_k=max(top_k * 2, top_k),
                books=[payload["book"]] if payload["book"] else None,
                specialty=payload["specialty"] or None,
            )
        except Exception as exc:
            logger.debug(f"quota vector search degraded: {exc}")

        return {
            "keyword": keyword_rows,
            "vector": vector_rows,
        }

    def _normalize_experience_results(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for index, record in enumerate(records or []):
            quota_pairs = []
            quota_ids = record.get("quota_ids") or []
            quota_names = record.get("quota_names") or []
            for offset, quota_id in enumerate(quota_ids):
                quota_name = quota_names[offset] if offset < len(quota_names) else ""
                quota_pairs.append(f"{quota_id} {quota_name}".strip())

            score = float(record.get("total_score") or record.get("similarity") or 0.0)
            gate = str(record.get("gate") or "")
            layer = str(record.get("layer") or "")
            gate_bonus = {"green": 0.20, "yellow": 0.05, "red": -0.20}.get(gate, 0.0)
            layer_bonus = {"authority": 0.15, "verified": 0.08, "candidate": 0.0}.get(layer, 0.0)

            items.append(
                {
                    "source": "experience",
                    "type": "case",
                    "id": record.get("id"),
                    "title": str(record.get("bill_text") or "").strip(),
                    "content": " | ".join(quota_pairs),
                    "score": score,
                    "merge_score": score + gate_bonus + layer_bonus,
                    "rank": index,
                    "gate": gate,
                    "layer": layer,
                    "confidence": int(record.get("confidence") or 0),
                    "match_type": str(record.get("match_type") or ""),
                    "raw": record,
                }
            )
        return items

    def _normalize_universal_kb_results(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for index, record in enumerate(records or []):
            quota_patterns = [str(item).strip() for item in (record.get("quota_patterns") or []) if str(item).strip()]
            associated_patterns = [
                str(item).strip() for item in (record.get("associated_patterns") or []) if str(item).strip()
            ]
            similarity = float(record.get("similarity") or 0.0)
            confidence = int(record.get("confidence") or 0)
            score = similarity * max(min(confidence / 100.0, 1.0), 0.0)
            content_parts = []
            if quota_patterns:
                content_parts.append("quota: " + " / ".join(quota_patterns[:3]))
            if associated_patterns:
                content_parts.append("associated: " + " / ".join(associated_patterns[:2]))
            items.append(
                {
                    "source": "universal_kb",
                    "type": "hint",
                    "id": record.get("id"),
                    "title": str(record.get("bill_pattern") or "").strip(),
                    "content": " | ".join(content_parts),
                    "score": score,
                    "merge_score": score,
                    "rank": index,
                    "layer": str(record.get("layer") or ""),
                    "similarity": similarity,
                    "confidence": confidence,
                    "raw": record,
                }
            )
        return items

    def _normalize_price_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for index, row in enumerate((result or {}).get("items", []) or []):
            price_value = self._coerce_float(row.get("price_value"))
            composite_value = self._coerce_float(row.get("composite_unit_price"))
            base_score = 0.55 if not row.get("price_outlier") else 0.30
            if composite_value is not None:
                base_score += 0.15
            elif price_value is not None:
                base_score += 0.08
            items.append(
                {
                    "source": "price",
                    "type": "price_reference",
                    "id": row.get("id"),
                    "title": str(row.get("boq_name_raw") or row.get("boq_name_normalized") or "").strip(),
                    "content": self._format_price_content(row),
                    "score": base_score,
                    "merge_score": base_score,
                    "rank": index,
                    "price_outlier": bool(row.get("price_outlier")),
                    "raw": row,
                }
            )
        return items

    def _normalize_quota_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}

        for index, row in enumerate(result.get("keyword") or []):
            quota_id = str(row.get("quota_id") or "").strip()
            if not quota_id:
                continue
            score = self._keyword_match_score(index)
            merged[quota_id] = {
                "source": "quota",
                "type": "quota_candidate",
                "id": quota_id,
                "title": str(row.get("name") or "").strip(),
                "content": self._format_quota_content(row),
                "score": score,
                "merge_score": score,
                "rank": index,
                "match_channel": ["keyword"],
                "raw": dict(row),
            }

        for index, row in enumerate(result.get("vector") or []):
            quota_id = str(row.get("quota_id") or "").strip()
            if not quota_id:
                continue
            score = float(row.get("vector_score") or 0.0)
            existing = merged.get(quota_id)
            if existing:
                existing["score"] = max(existing["score"], score)
                existing["merge_score"] = max(existing["merge_score"], score)
                existing["rank"] = min(existing["rank"], index)
                existing["raw"].update({k: v for k, v in row.items() if v not in (None, "", [])})
                if "vector" not in existing["match_channel"]:
                    existing["match_channel"].append("vector")
            else:
                merged[quota_id] = {
                    "source": "quota",
                    "type": "quota_candidate",
                    "id": quota_id,
                    "title": str(row.get("name") or "").strip(),
                    "content": self._format_quota_content(row),
                    "score": score,
                    "merge_score": score,
                    "rank": index,
                    "match_channel": ["vector"],
                    "raw": dict(row),
                }

        items = list(merged.values())
        items.sort(key=lambda item: (-float(item.get("merge_score", 0.0) or 0.0), item.get("title", ""), item["id"]))
        return items

    def _merge_results(
        self,
        grouped_results: dict[str, list[dict[str, Any]]],
        *,
        strategy: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if strategy == "by_source":
            items: list[dict[str, Any]] = []
            for source in self._ALL_SOURCES:
                items.extend(grouped_results.get(source, [])[:top_k])
            return items

        if strategy not in {"auto", "score"}:
            raise ValueError(f"unsupported merge strategy: {strategy}")

        experience_items = grouped_results.get("experience", [])
        universal_items = grouped_results.get("universal_kb", [])
        price_items = grouped_results.get("price", [])
        quota_items = grouped_results.get("quota", [])

        merged: list[dict[str, Any]] = []
        used_keys: set[tuple[str, str]] = set()

        top_experience = experience_items[0] if experience_items else None
        if (
            strategy == "auto"
            and top_experience
            and top_experience.get("gate") == "green"
            and top_experience.get("layer") in {"authority", "verified"}
            and float(top_experience.get("score", 0.0) or 0.0) >= 0.85
        ):
            recommended = dict(top_experience)
            recommended["recommended"] = True
            self._push_unique(merged, used_keys, recommended)
            self._extend_unique(merged, used_keys, universal_items[:2])
            self._extend_unique(merged, used_keys, price_items[:2])
            self._extend_unique(merged, used_keys, quota_items[:3])
            return merged[:top_k]

        blended = []
        for source in self._ALL_SOURCES:
            blended.extend(grouped_results.get(source, []))
        blended.sort(
            key=lambda item: (
                -float(item.get("merge_score", 0.0) or 0.0),
                self._source_priority(item.get("source", "")),
                item.get("rank", 0),
            )
        )
        self._extend_unique(merged, used_keys, blended)
        return merged[:top_k]

    def _build_query_payload(self, query: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(query, str):
            data = {"text": query}
        elif isinstance(query, dict):
            data = dict(query)
        else:
            raise TypeError("query must be a string or dict")

        text = str(
            data.get("text")
            or data.get("query")
            or data.get("query_text")
            or data.get("bill_text")
            or ""
        ).strip()
        if not text:
            raise ValueError("query text is required")

        return {
            "text": text,
            "province": str(data.get("province") or self.province or "").strip() or None,
            "specialty": str(data.get("specialty") or "").strip(),
            "unit": str(data.get("unit") or "").strip(),
            "materials_signature": str(data.get("materials_signature") or "").strip(),
            "install_method": str(data.get("install_method") or "").strip(),
            "quota_version": str(data.get("quota_version") or "").strip(),
            "chapter": str(data.get("chapter") or "").strip(),
            "book": str(data.get("book") or "").strip(),
            "region": str(data.get("region") or "").strip(),
            "quota_code": str(data.get("quota_code") or "").strip(),
            "min_confidence": self._coerce_int(data.get("min_confidence"), default=60),
        }

    def _normalize_sources(self, sources: list[str] | tuple[str, ...] | None) -> list[str]:
        if not sources:
            return list(self._ALL_SOURCES)

        normalized = []
        for source in sources:
            value = str(source or "").strip().lower()
            if not value:
                continue
            if value == "all":
                return list(self._ALL_SOURCES)
            if value not in self._ALL_SOURCES:
                raise ValueError(f"unsupported source: {value}")
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _keyword_match_score(index: int) -> float:
        return max(0.35, 0.78 - index * 0.06)

    @staticmethod
    def _source_priority(source: str) -> int:
        return {
            "experience": 0,
            "universal_kb": 1,
            "quota": 2,
            "price": 3,
        }.get(str(source or ""), 9)

    @staticmethod
    def _item_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("source") or ""),
            str(item.get("id") or item.get("title") or ""),
        )

    def _push_unique(
        self,
        merged: list[dict[str, Any]],
        used_keys: set[tuple[str, str]],
        item: dict[str, Any],
    ) -> None:
        key = self._item_key(item)
        if key in used_keys:
            return
        used_keys.add(key)
        merged.append(item)

    def _extend_unique(
        self,
        merged: list[dict[str, Any]],
        used_keys: set[tuple[str, str]],
        items: list[dict[str, Any]],
    ) -> None:
        for item in items or []:
            self._push_unique(merged, used_keys, item)

    @staticmethod
    def _format_price_content(row: dict[str, Any]) -> str:
        parts = []
        quota_name = str(row.get("quota_name") or "").strip()
        if quota_name:
            parts.append(quota_name)
        unit = str(row.get("unit") or "").strip()
        price_value = row.get("composite_unit_price")
        if price_value in (None, ""):
            price_value = row.get("price_value")
        if price_value not in (None, ""):
            price_text = f"price={price_value}"
            if unit:
                price_text += f"/{unit}"
            parts.append(price_text)
        region = str(row.get("region") or "").strip()
        if region:
            parts.append(region)
        return " | ".join(parts)

    @staticmethod
    def _format_quota_content(row: dict[str, Any]) -> str:
        parts = []
        quota_id = str(row.get("quota_id") or "").strip()
        unit = str(row.get("unit") or "").strip()
        if quota_id:
            parts.append(quota_id)
        if unit:
            parts.append(f"unit={unit}")
        chapter = str(row.get("chapter") or "").strip()
        if chapter:
            parts.append(chapter)
        return " | ".join(parts)

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
