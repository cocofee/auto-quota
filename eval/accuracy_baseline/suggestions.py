from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Self

from src.goal_search.national_index import QuotaSignal, extract_signal, tokenize

SUGGESTION_VERSION = "accuracy_review_suggestions.v1"
SUGGESTION_SOURCE = "national_index_structured_search"

_SELECT_COLUMNS = (
    "province",
    "quota_id",
    "name",
    "unit",
    "family",
    "action",
    "material",
    "connection",
    "install_method",
    "dn",
    "cable_section",
    "cable_cores",
    "circuits",
    "concrete_grade",
    "thickness",
    "param_type",
    "cluster_key",
    "tokens",
    "normalized_text",
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _candidate_book_names(value: Any) -> list[str]:
    books = value
    if isinstance(value, str):
        try:
            books = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            books = []
    if not isinstance(books, Sequence) or isinstance(books, (str, bytes)):
        return []
    result: list[str] = []
    for item in books:
        name = _clean(item.get("name")) if isinstance(item, Mapping) else _clean(item)
        if name and name not in result:
            result.append(name)
    return result


def _parse_tokens(value: Any) -> set[str]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes)):
        return set()
    return {_clean(token).casefold() for token in parsed if _clean(token)}


def _distinctive_tokens(values: Sequence[str], *, limit: int = 6) -> list[str]:
    unique = {_clean(value).casefold() for value in values if len(_clean(value)) >= 2}
    return sorted(unique, key=lambda value: (-len(value), value))[:limit]


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_signal_score(
    signal: QuotaSignal,
    candidate: Mapping[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for field_name, weight in (
        ("dn", 0.10),
        ("cable_section", 0.09),
        ("cable_cores", 0.08),
        ("circuits", 0.07),
        ("concrete_grade", 0.08),
        ("thickness", 0.07),
    ):
        expected = _number(getattr(signal, field_name))
        observed = _number(candidate.get(field_name))
        if expected is None or observed is None:
            continue
        if math.isclose(expected, observed, rel_tol=0.0, abs_tol=0.01):
            score += weight
            reasons.append(f"{field_name}_exact")
            continue
        if observed >= expected:
            gap = (observed - expected) / max(expected, 1.0)
            if gap <= 1.0:
                score += weight * max(0.2, 1.0 - gap)
                reasons.append(f"{field_name}_tier_up")
                continue
        score -= weight * 0.75
        reasons.append(f"{field_name}_conflict")
    return score, reasons


def _score_candidate(
    *,
    query_text: str,
    query_tokens: set[str],
    signal: QuotaSignal,
    unit: str,
    candidate: Mapping[str, Any],
) -> tuple[float, list[str]]:
    candidate_tokens = _parse_tokens(candidate.get("tokens"))
    normalized_text = _clean(candidate.get("normalized_text")).casefold()
    if not candidate_tokens and normalized_text:
        candidate_tokens = set(tokenize(normalized_text))
    overlap = query_tokens & candidate_tokens
    coverage = len(overlap) / max(len(query_tokens), 1)
    precision = len(overlap) / max(len(candidate_tokens), 1)
    score = 0.48 * coverage + 0.08 * min(1.0, precision * 4.0)
    reasons = [f"token_coverage:{coverage:.2f}"] if overlap else []

    compact_query = "".join(query_text.casefold().split())
    compact_name = "".join(_clean(candidate.get("name")).casefold().split())
    if compact_name and compact_name in compact_query:
        score += 0.12
        reasons.append("name_in_query")

    candidate_family = _clean(candidate.get("family"))
    if signal.family and candidate_family == signal.family:
        score += 0.18
        reasons.append(f"family:{signal.family}")
    for field_name, weight in (
        ("action", 0.04),
        ("material", 0.07),
        ("connection", 0.05),
        ("install_method", 0.04),
    ):
        expected = _clean(getattr(signal, field_name))
        observed = _clean(candidate.get(field_name))
        if expected and observed == expected:
            score += weight
            reasons.append(f"{field_name}:{expected}")

    if signal.cluster_key(unit) == _clean(candidate.get("cluster_key")):
        score += 0.12
        reasons.append("cluster_exact")

    query_unit = _clean(unit).casefold()
    candidate_unit = _clean(candidate.get("unit")).casefold()
    if query_unit and candidate_unit:
        if query_unit == candidate_unit:
            score += 0.04
            reasons.append("unit_exact")
        elif query_unit not in candidate_unit and candidate_unit not in query_unit:
            score -= 0.03
            reasons.append("unit_conflict")

    numeric_score, numeric_reasons = _numeric_signal_score(signal, candidate)
    score += numeric_score
    reasons.extend(numeric_reasons)
    return max(0.0, min(1.0, score)), reasons


class NationalIndexSuggestionProvider:
    def __init__(
        self,
        path: str | Path,
        *,
        top_k: int = 5,
        minimum_score: float = 20.0,
        candidate_pool_limit: int = 600,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if candidate_pool_limit < top_k:
            raise ValueError("candidate_pool_limit must be at least top_k")
        if not 0.0 <= minimum_score <= 100.0:
            raise ValueError("minimum_score must be between 0 and 100")
        self.path = Path(path).resolve()
        self.top_k = top_k
        self.minimum_score = float(minimum_score)
        self.candidate_pool_limit = candidate_pool_limit
        self.connection = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro",
            uri=True,
        )
        self.connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(national_quotas)"
            ).fetchall()
        }
        missing = sorted(set(_SELECT_COLUMNS) - columns)
        if missing:
            self.connection.close()
            raise ValueError(
                "national_quotas missing suggestion columns: " + ", ".join(missing)
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def suggest(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        books = _candidate_book_names(row.get("candidate_quota_books"))
        if not books:
            return []
        bill_name = _clean(row.get("bill_name"))
        query_text = _clean(
            " ".join(
                value
                for value in (
                    bill_name,
                    _clean(row.get("bill_text")),
                    _clean(row.get("description")),
                    _clean(row.get("specialty")),
                )
                if value
            )
        )
        signal = extract_signal(query_text)
        query_tokens = set(tokenize(bill_name)) or set(tokenize(query_text))
        search_tokens = _distinctive_tokens(list(query_tokens), limit=4)
        for token in _distinctive_tokens(tokenize(query_text), limit=8):
            if token not in search_tokens:
                search_tokens.append(token)
            if len(search_tokens) >= 8:
                break
        candidates = self._candidate_rows(
            books=books,
            signal=signal,
            unit=_clean(row.get("unit")),
            query_tokens=search_tokens,
        )
        scored: list[tuple[float, str, str, dict[str, Any]]] = []
        for candidate in candidates:
            score, reasons = _score_candidate(
                query_text=bill_name or query_text,
                query_tokens=query_tokens,
                signal=signal,
                unit=_clean(row.get("unit")),
                candidate=candidate,
            )
            payload = {
                "quota_book": _clean(candidate.get("province")),
                "quota_id": _clean(candidate.get("quota_id")),
                "name": _clean(candidate.get("name")),
                "unit": _clean(candidate.get("unit")),
                "score": round(score * 100.0, 2),
                "reasons": reasons[:8],
            }
            if (
                payload["quota_id"]
                and payload["name"]
                and payload["score"] >= self.minimum_score
            ):
                scored.append(
                    (
                        score,
                        payload["quota_id"],
                        payload["quota_book"],
                        payload,
                    )
                )
        scored.sort(key=lambda value: (-value[0], value[1], value[2]))
        unique: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for _score, quota_id, _book, payload in scored:
            if quota_id in seen_ids:
                continue
            seen_ids.add(quota_id)
            unique.append(payload)
            if len(unique) >= self.top_k:
                break
        return unique

    def _candidate_rows(
        self,
        *,
        books: Sequence[str],
        signal: QuotaSignal,
        unit: str,
        query_tokens: Sequence[str],
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in books)
        select_columns = ", ".join(_SELECT_COLUMNS)
        indexed: dict[tuple[str, str], dict[str, Any]] = {}

        def add(rows: Sequence[sqlite3.Row]) -> None:
            for candidate_row in rows:
                payload = dict(candidate_row)
                key = (_clean(payload.get("province")), _clean(payload.get("quota_id")))
                if key[0] and key[1]:
                    indexed.setdefault(key, payload)

        def lexical_rows(family: str = "") -> list[sqlite3.Row]:
            filters = [f"province IN ({placeholders})"]
            parameters: list[Any] = list(books)
            order_parameters: list[Any] = []
            order_sql = "quota_id, province"
            if family:
                filters.append("family = ?")
                parameters.append(family)
            if query_tokens:
                token_filters: list[str] = []
                token_order: list[str] = []
                for token in query_tokens:
                    token_filters.append("(normalized_text LIKE ? OR name LIKE ?)")
                    token_order.append(
                        "CASE WHEN normalized_text LIKE ? OR name LIKE ? "
                        "THEN 1 ELSE 0 END"
                    )
                    pattern = f"%{token}%"
                    parameters.extend((pattern, pattern))
                    order_parameters.extend((pattern, pattern))
                filters.append("(" + " OR ".join(token_filters) + ")")
                order_sql = (
                    "(" + " + ".join(token_order) + ") DESC, quota_id, province"
                )
            return self.connection.execute(
                f"""
                SELECT {select_columns}
                FROM national_quotas
                WHERE {' AND '.join(filters)}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                [*parameters, *order_parameters, self.candidate_pool_limit],
            ).fetchall()

        cluster_key = signal.cluster_key(unit)
        if signal.family and cluster_key:
            add(
                self.connection.execute(
                    f"""
                    SELECT {select_columns}
                    FROM national_quotas
                    WHERE province IN ({placeholders}) AND cluster_key = ?
                    ORDER BY quota_id, province
                    LIMIT ?
                    """,
                    [*books, cluster_key, self.candidate_pool_limit],
                ).fetchall()
            )

        add(lexical_rows(signal.family))

        if signal.family and query_tokens:
            add(lexical_rows())

        if len(indexed) < self.top_k and signal.family:
            add(
                self.connection.execute(
                    f"""
                    SELECT {select_columns}
                    FROM national_quotas
                    WHERE province IN ({placeholders}) AND family = ?
                    ORDER BY quota_id, province
                    LIMIT ?
                    """,
                    [*books, signal.family, self.candidate_pool_limit],
                ).fetchall()
            )
        return list(indexed.values())


def suggestion_columns(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(candidate) for candidate in candidates]
    return {
        "suggested_quota_ids": [_clean(candidate.get("quota_id")) for candidate in rows],
        "suggested_quota_names": [_clean(candidate.get("name")) for candidate in rows],
        "suggested_quota_books": [
            _clean(candidate.get("quota_book")) for candidate in rows
        ],
        "suggested_scores": [candidate.get("score") for candidate in rows],
        "suggested_reasons": [list(candidate.get("reasons") or []) for candidate in rows],
        "suggested_source": SUGGESTION_SOURCE,
        "suggested_version": SUGGESTION_VERSION,
    }


__all__ = [
    "SUGGESTION_SOURCE",
    "SUGGESTION_VERSION",
    "NationalIndexSuggestionProvider",
    "suggestion_columns",
]
