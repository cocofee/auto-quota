from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLUGIN_COMMON_PATH = PROJECT_ROOT / "data" / "province_plugins" / "common.json"
DEFAULT_PLUGIN_KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "province_plugins" / "generated" / "knowledge.json"
DEFAULT_BENCHMARK_ASSET_ROOT = PROJECT_ROOT / "output" / "benchmark_assets"


def normalize_plugin_term(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[：:;；,，。!！?？()（）\[\]{}]+", "", text)
    return text[:80]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _dedupe_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _term_family_variants(text: str) -> list[str]:
    normalized = normalize_plugin_term(text)
    if not normalized:
        return []

    variants = [normalized]

    stripped_code = re.sub(r"[A-Za-z]+[\d\-_/~A-Za-z]*$", "", normalized).strip()
    if stripped_code and stripped_code != normalized:
        variants.append(stripped_code[:80])

    chinese_only = "".join(re.findall(r"[\u4e00-\u9fff]+", normalized))
    if chinese_only and chinese_only != normalized:
        variants.append(chinese_only[:80])

    compact = re.sub(r"[\dA-Za-z\-_/~]+", "", normalized).strip()
    if compact and compact != normalized:
        variants.append(compact[:80])

    return _dedupe_keep_order(variants)


def _merge_plugin_blocks(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_plugin_blocks(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            merged[key] = _dedupe_keep_order(list(existing) + list(value))
        else:
            merged[key] = value
    return merged


def _supported_in_provinces(province_map: dict[str, dict[str, Any]], category: str, term: str) -> int:
    support = 0
    for province_payload in province_map.values():
        if ((province_payload.get(category) or {}).get(term) or {}):
            support += 1
    return support


def _top_values(counter: Counter[str], limit: int = 5) -> list[str]:
    return [value for value, _ in counter.most_common(limit) if value]


def _is_effectively_empty(block: dict[str, Any]) -> bool:
    provinces = block.get("provinces", {}) or {}
    national = block.get("national", {}) or {}
    if provinces:
        return False
    return not any(bool(value) for value in national.values())


def _build_runtime_knowledge_from_assets(asset_root: Path) -> dict[str, Any]:
    asset_root = Path(asset_root)
    if not asset_root.exists():
        return {"provinces": {}, "national": {}}

    synonym_aliases_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    route_books_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    route_specialties_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    tier_preferred_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    tier_avoided_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

    for path in asset_root.rglob("synonym_gaps.jsonl"):
        for record in _read_jsonl(path):
            province = str(record.get("province") or "").strip()
            term = normalize_plugin_term(record.get("bill_name", ""))
            if not province or not term:
                continue
            for alias in record.get("expected_quota_names", []) or []:
                alias = str(alias or "").strip()
                if alias:
                    synonym_aliases_by_province[province][term][alias] += 1

    for path in asset_root.rglob("route_errors.jsonl"):
        for record in _read_jsonl(path):
            province = str(record.get("province") or "").strip()
            term = normalize_plugin_term(record.get("bill_name", ""))
            if not province or not term:
                continue
            expected_book = str(record.get("expected_book") or "").strip()
            specialty = str(record.get("specialty") or "").strip()
            if expected_book:
                route_books_by_province[province][term][expected_book] += 1
            if specialty:
                route_specialties_by_province[province][term][specialty] += 1

    for path in asset_root.rglob("tier_errors.jsonl"):
        for record in _read_jsonl(path):
            province = str(record.get("province") or "").strip()
            term = normalize_plugin_term(record.get("bill_name", ""))
            if not province or not term:
                continue
            for quota_name in record.get("expected_quota_names", []) or []:
                quota_name = str(quota_name or "").strip()
                if quota_name:
                    tier_preferred_by_province[province][term][quota_name] += 1
            avoided = str(record.get("predicted_quota_name") or "").strip()
            if avoided:
                tier_avoided_by_province[province][term][avoided] += 1

    knowledge: dict[str, Any] = {
        "provinces": {},
        "national": {"synonyms": {}, "route_biases": {}, "tier_hints": {}},
    }

    def _ensure_province(province: str) -> dict[str, Any]:
        return knowledge["provinces"].setdefault(
            province,
            {"synonyms": {}, "route_biases": {}, "tier_hints": {}},
        )

    for province, mapping in synonym_aliases_by_province.items():
        block = _ensure_province(province)["synonyms"]
        for term, counter in mapping.items():
            block[term] = {"aliases": _top_values(counter, limit=5), "count": int(sum(counter.values()))}

    for province, mapping in route_books_by_province.items():
        block = _ensure_province(province)["route_biases"]
        for term, counter in mapping.items():
            block.setdefault(term, {})
            block[term]["preferred_books"] = _top_values(counter, limit=3)
            block[term]["count"] = int(sum(counter.values()))

    for province, mapping in route_specialties_by_province.items():
        block = _ensure_province(province)["route_biases"]
        for term, counter in mapping.items():
            block.setdefault(term, {})
            block[term]["preferred_specialties"] = _top_values(counter, limit=3)
            block[term]["count"] = max(int(block[term].get("count", 0) or 0), int(sum(counter.values())))

    for province, mapping in tier_preferred_by_province.items():
        block = _ensure_province(province)["tier_hints"]
        for term, counter in mapping.items():
            block.setdefault(term, {})
            block[term]["preferred_quota_names"] = _top_values(counter, limit=3)
            block[term]["count"] = int(sum(counter.values()))

    for province, mapping in tier_avoided_by_province.items():
        block = _ensure_province(province)["tier_hints"]
        for term, counter in mapping.items():
            block.setdefault(term, {})
            block[term]["avoided_quota_names"] = _top_values(counter, limit=3)
            block[term]["count"] = max(int(block[term].get("count", 0) or 0), int(sum(counter.values())))

    national_synonyms: dict[str, Counter[str]] = defaultdict(Counter)
    national_route_books: dict[str, Counter[str]] = defaultdict(Counter)
    national_route_specialties: dict[str, Counter[str]] = defaultdict(Counter)
    national_tier_preferred: dict[str, Counter[str]] = defaultdict(Counter)
    national_tier_avoided: dict[str, Counter[str]] = defaultdict(Counter)

    for province_block in knowledge["provinces"].values():
        for term, payload in (province_block.get("synonyms") or {}).items():
            for alias in payload.get("aliases", []) or []:
                national_synonyms[term][alias] += 1
        for term, payload in (province_block.get("route_biases") or {}).items():
            for book in payload.get("preferred_books", []) or []:
                national_route_books[term][book] += 1
            for specialty in payload.get("preferred_specialties", []) or []:
                national_route_specialties[term][specialty] += 1
        for term, payload in (province_block.get("tier_hints") or {}).items():
            for quota_name in payload.get("preferred_quota_names", []) or []:
                national_tier_preferred[term][quota_name] += 1
            for quota_name in payload.get("avoided_quota_names", []) or []:
                national_tier_avoided[term][quota_name] += 1

    for term, counter in national_synonyms.items():
        knowledge["national"]["synonyms"][term] = {
            "aliases": _top_values(counter, limit=5),
            "count": int(sum(counter.values())),
        }
    for term, counter in national_route_books.items():
        if _supported_in_provinces(knowledge["provinces"], "route_biases", term) >= 2:
            knowledge["national"]["route_biases"].setdefault(term, {})
            knowledge["national"]["route_biases"][term]["preferred_books"] = _top_values(counter, limit=3)
            knowledge["national"]["route_biases"][term]["count"] = int(sum(counter.values()))
    for term, counter in national_route_specialties.items():
        if _supported_in_provinces(knowledge["provinces"], "route_biases", term) >= 2:
            knowledge["national"]["route_biases"].setdefault(term, {})
            knowledge["national"]["route_biases"][term]["preferred_specialties"] = _top_values(counter, limit=3)
            knowledge["national"]["route_biases"][term]["count"] = max(
                int(knowledge["national"]["route_biases"][term].get("count", 0) or 0),
                int(sum(counter.values())),
            )
    for term, counter in national_tier_preferred.items():
        if _supported_in_provinces(knowledge["provinces"], "tier_hints", term) >= 2:
            knowledge["national"]["tier_hints"].setdefault(term, {})
            knowledge["national"]["tier_hints"][term]["preferred_quota_names"] = _top_values(counter, limit=3)
            knowledge["national"]["tier_hints"][term]["count"] = int(sum(counter.values()))
    for term, counter in national_tier_avoided.items():
        if _supported_in_provinces(knowledge["provinces"], "tier_hints", term) >= 2:
            knowledge["national"]["tier_hints"].setdefault(term, {})
            knowledge["national"]["tier_hints"][term]["avoided_quota_names"] = _top_values(counter, limit=3)
            knowledge["national"]["tier_hints"][term]["count"] = max(
                int(knowledge["national"]["tier_hints"][term].get("count", 0) or 0),
                int(sum(counter.values())),
            )

    return knowledge


class ProvincePluginRegistry:
    def __init__(self,
                 knowledge_path: str | Path | None = None,
                 extra_paths: list[str | Path] | None = None,
                 asset_root: str | Path | None = None):
        self.knowledge_path = Path(knowledge_path) if knowledge_path else DEFAULT_PLUGIN_KNOWLEDGE_PATH
        resolved_extra_paths = extra_paths if extra_paths is not None else [DEFAULT_PLUGIN_COMMON_PATH]
        self.extra_paths = [Path(path) for path in resolved_extra_paths]
        self.asset_root = Path(asset_root) if asset_root else DEFAULT_BENCHMARK_ASSET_ROOT

    @lru_cache(maxsize=1)
    def _load(self) -> dict[str, Any]:
        merged: dict[str, Any] = {"provinces": {}, "national": {}}
        for path in [*self.extra_paths, self.knowledge_path]:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            merged = _merge_plugin_blocks(merged, data)
        if _is_effectively_empty(merged):
            merged = _merge_plugin_blocks(merged, _build_runtime_knowledge_from_assets(self.asset_root))
        return merged

    def _resolve_province_key(self, province: str) -> str:
        province = str(province or "").strip()
        if not province:
            return ""
        provinces = self._load().get("provinces", {})
        if province in provinces:
            return province
        for key in provinces:
            if key and (key in province or province in key):
                return key
        return ""

    def _lookup_term_block(self, mapping: dict[str, Any], term_candidates: list[str]) -> dict[str, Any]:
        for term in term_candidates:
            if term and isinstance(mapping.get(term), dict):
                return mapping[term]
        for term in term_candidates:
            if not term:
                continue
            for key, value in mapping.items():
                if not isinstance(value, dict):
                    continue
                if key and (key in term or term in key):
                    return value
        return {}

    def resolve_hints(
        self,
        *,
        province: str = "",
        item: dict[str, Any] | None = None,
        canonical_features: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = dict(item or {})
        canonical_features = dict(canonical_features or {})
        knowledge = self._load()

        term_candidates = _dedupe_keep_order(
            _term_family_variants(item.get("name", "")) +
            _term_family_variants(item.get("original_name", "")) +
            _term_family_variants(canonical_features.get("canonical_name", "")) +
            _term_family_variants(canonical_features.get("entity", ""))
        )
        if not term_candidates:
            return {}

        province_key = self._resolve_province_key(province)
        province_block = (knowledge.get("provinces", {}) or {}).get(province_key, {}) if province_key else {}
        national_block = knowledge.get("national", {}) or {}

        province_synonyms = self._lookup_term_block(province_block.get("synonyms", {}) or {}, term_candidates)
        national_synonyms = self._lookup_term_block(national_block.get("synonyms", {}) or {}, term_candidates)
        province_route = self._lookup_term_block(province_block.get("route_biases", {}) or {}, term_candidates)
        national_route = self._lookup_term_block(national_block.get("route_biases", {}) or {}, term_candidates)
        province_tier = self._lookup_term_block(province_block.get("tier_hints", {}) or {}, term_candidates)
        national_tier = self._lookup_term_block(national_block.get("tier_hints", {}) or {}, term_candidates)

        aliases = _dedupe_keep_order(
            list(province_synonyms.get("aliases", []) or []) +
            list(national_synonyms.get("aliases", []) or [])
        )[:3]
        preferred_books = _dedupe_keep_order(
            list(province_route.get("preferred_books", []) or []) +
            list(national_route.get("preferred_books", []) or [])
        )[:3]
        preferred_specialties = _dedupe_keep_order(
            list(province_route.get("preferred_specialties", []) or []) +
            list(national_route.get("preferred_specialties", []) or [])
        )[:3]
        preferred_quota_names = _dedupe_keep_order(
            list(province_tier.get("preferred_quota_names", []) or []) +
            list(national_tier.get("preferred_quota_names", []) or [])
        )[:3]
        avoided_quota_names = _dedupe_keep_order(
            list(province_tier.get("avoided_quota_names", []) or []) +
            list(national_tier.get("avoided_quota_names", []) or [])
        )[:3]

        if not any((aliases, preferred_books, preferred_specialties, preferred_quota_names, avoided_quota_names)):
            return {}

        return {
            "province_key": province_key,
            "matched_terms": term_candidates[:3],
            "synonym_aliases": aliases,
            "preferred_books": preferred_books,
            "preferred_specialties": preferred_specialties,
            "preferred_quota_names": preferred_quota_names,
            "avoided_quota_names": avoided_quota_names,
            "source": "generated_benchmark_knowledge",
        }


@lru_cache(maxsize=1)
def _default_registry() -> ProvincePluginRegistry:
    return ProvincePluginRegistry()


def resolve_plugin_hints(
    *,
    province: str = "",
    item: dict[str, Any] | None = None,
    canonical_features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _default_registry().resolve_hints(
        province=province,
        item=item,
        canonical_features=canonical_features,
    )
