from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.province_plugins.loader import normalize_plugin_term


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "output" / "benchmark_assets"
DEFAULT_OUT_PATH = PROJECT_ROOT / "data" / "province_plugins" / "generated" / "knowledge.json"
DEFAULT_DIGEST_OUT_PATH = PROJECT_ROOT / "data" / "province_plugins" / "generated" / "knowledge_digest.json"
DEFAULT_DIGEST_MD_OUT_PATH = PROJECT_ROOT / "data" / "province_plugins" / "generated" / "knowledge_digest.md"


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _top_values(counter: Counter[str], limit: int = 5) -> list[str]:
    return [value for value, _ in counter.most_common(limit) if value]


def _supported_in_provinces(province_map: dict[str, dict[str, dict]], category: str, term: str) -> int:
    support = 0
    for province_payload in province_map.values():
        if ((province_payload.get(category) or {}).get(term) or {}):
            support += 1
    return support


def _bucket(block: dict, province: str, category: str, term: str) -> dict:
    provinces = block.setdefault("provinces", {})
    national = block.setdefault("national", {})
    province_bucket = provinces.setdefault(province, {}).setdefault(category, {}).setdefault(term, {})
    national_bucket = national.setdefault(category, {}).setdefault(term, {})
    return {
        "province": province_bucket,
        "national": national_bucket,
    }


def build_knowledge_from_asset_root(asset_root: str | Path) -> dict:
    asset_root = Path(asset_root)
    source_dirs = sorted({str(path.parent) for path in asset_root.rglob("manifest.json")})

    synonym_aliases_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    route_books_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    route_specialties_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    tier_preferred_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    tier_avoided_by_province: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

    stats = Counter()

    for path in asset_root.rglob("synonym_gaps.jsonl"):
        for record in _read_jsonl(path):
            province = str(record.get("province") or "").strip()
            term = normalize_plugin_term(record.get("bill_name", ""))
            if not province or not term:
                continue
            stats["synonym_records"] += 1
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
            stats["route_records"] += 1
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
            stats["tier_records"] += 1
            for quota_name in record.get("expected_quota_names", []) or []:
                quota_name = str(quota_name or "").strip()
                if quota_name:
                    tier_preferred_by_province[province][term][quota_name] += 1
            avoided = str(record.get("predicted_quota_name") or "").strip()
            if avoided:
                tier_avoided_by_province[province][term][avoided] += 1

    knowledge: dict = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_asset_root": str(asset_root),
        "source_dirs": source_dirs,
        "record_counts": dict(stats),
        "national": {
            "synonyms": {},
            "route_biases": {},
            "tier_hints": {},
        },
        "provinces": {},
    }

    def _ensure_province(province: str) -> dict:
        return knowledge["provinces"].setdefault(
            province,
            {
                "synonyms": {},
                "route_biases": {},
                "tier_hints": {},
            },
        )

    for province, mapping in synonym_aliases_by_province.items():
        province_block = _ensure_province(province)["synonyms"]
        for term, counter in mapping.items():
            province_block[term] = {
                "aliases": _top_values(counter, limit=5),
                "count": int(sum(counter.values())),
            }

    for province, mapping in route_books_by_province.items():
        province_block = _ensure_province(province)["route_biases"]
        for term, counter in mapping.items():
            province_block.setdefault(term, {})
            province_block[term]["preferred_books"] = _top_values(counter, limit=3)
            province_block[term]["count"] = int(sum(counter.values()))

    for province, mapping in route_specialties_by_province.items():
        province_block = _ensure_province(province)["route_biases"]
        for term, counter in mapping.items():
            province_block.setdefault(term, {})
            province_block[term]["preferred_specialties"] = _top_values(counter, limit=3)
            province_block[term]["count"] = province_block[term].get("count", 0) + int(sum(counter.values()))

    for province, mapping in tier_preferred_by_province.items():
        province_block = _ensure_province(province)["tier_hints"]
        for term, counter in mapping.items():
            province_block.setdefault(term, {})
            province_block[term]["preferred_quota_names"] = _top_values(counter, limit=3)
            province_block[term]["count"] = int(sum(counter.values()))

    for province, mapping in tier_avoided_by_province.items():
        province_block = _ensure_province(province)["tier_hints"]
        for term, counter in mapping.items():
            province_block.setdefault(term, {})
            province_block[term]["avoided_quota_names"] = _top_values(counter, limit=3)
            province_block[term]["count"] = province_block[term].get("count", 0) + int(sum(counter.values()))

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


def write_knowledge(knowledge: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(knowledge, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _top_terms(block: dict[str, dict], key: str, limit: int = 10) -> list[dict]:
    rows: list[dict] = []
    for term, payload in (block or {}).items():
        rows.append({
            "term": term,
            "count": int(payload.get("count", 0) or 0),
            key: list(payload.get(key, []) or []),
        })
    rows.sort(key=lambda item: (-item["count"], item["term"]))
    return rows[:limit]


def build_knowledge_digest(knowledge: dict) -> dict:
    provinces = knowledge.get("provinces", {}) or {}
    national = knowledge.get("national", {}) or {}

    province_summaries: list[dict] = []
    for province, payload in provinces.items():
        route_count = sum(int((entry or {}).get("count", 0) or 0) for entry in (payload.get("route_biases") or {}).values())
        tier_count = sum(int((entry or {}).get("count", 0) or 0) for entry in (payload.get("tier_hints") or {}).values())
        synonym_count = sum(int((entry or {}).get("count", 0) or 0) for entry in (payload.get("synonyms") or {}).values())
        province_summaries.append({
            "province": province,
            "synonym_terms": len(payload.get("synonyms", {}) or {}),
            "route_terms": len(payload.get("route_biases", {}) or {}),
            "tier_terms": len(payload.get("tier_hints", {}) or {}),
            "synonym_count": synonym_count,
            "route_count": route_count,
            "tier_count": tier_count,
        })
    province_summaries.sort(key=lambda item: (-(item["synonym_count"] + item["route_count"] + item["tier_count"]), item["province"]))

    return {
        "generated_at": knowledge.get("generated_at", ""),
        "source_asset_root": knowledge.get("source_asset_root", ""),
        "record_counts": dict(knowledge.get("record_counts", {}) or {}),
        "top_national_synonyms": _top_terms(national.get("synonyms", {}) or {}, "aliases"),
        "top_national_route_biases": _top_terms(national.get("route_biases", {}) or {}, "preferred_books"),
        "top_national_tier_hints": _top_terms(national.get("tier_hints", {}) or {}, "preferred_quota_names"),
        "province_summaries": province_summaries[:20],
    }


def write_digest(digest: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def render_digest_markdown(digest: dict) -> str:
    lines = [
        "# Benchmark Knowledge Digest",
        "",
        f"- Generated At: {digest.get('generated_at', '')}",
        f"- Source Asset Root: {digest.get('source_asset_root', '')}",
        f"- Record Counts: {digest.get('record_counts', {})}",
        "",
        "## Top National Synonyms",
    ]
    for row in digest.get("top_national_synonyms", []) or []:
        lines.append(f"- {row.get('term', '')}: {', '.join(row.get('aliases', []) or [])}")

    lines.extend(["", "## Top National Route Biases"])
    for row in digest.get("top_national_route_biases", []) or []:
        lines.append(f"- {row.get('term', '')}: {', '.join(row.get('preferred_books', []) or [])}")

    lines.extend(["", "## Top National Tier Hints"])
    for row in digest.get("top_national_tier_hints", []) or []:
        lines.append(f"- {row.get('term', '')}: {', '.join(row.get('preferred_quota_names', []) or [])}")

    lines.extend(["", "## Province Summaries"])
    for row in digest.get("province_summaries", []) or []:
        lines.append(
            f"- {row.get('province', '')}: synonym_terms={row.get('synonym_terms', 0)}, "
            f"route_terms={row.get('route_terms', 0)}, tier_terms={row.get('tier_terms', 0)}"
        )

    return "\n".join(lines).strip() + "\n"


def write_digest_markdown(digest: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_digest_markdown(digest), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build province plugin knowledge from benchmark assets")
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT), help="benchmark asset root directory")
    parser.add_argument("--out", default=str(DEFAULT_OUT_PATH), help="output knowledge json path")
    parser.add_argument("--digest-out", default=str(DEFAULT_DIGEST_OUT_PATH), help="output digest json path")
    parser.add_argument("--digest-md-out", default=str(DEFAULT_DIGEST_MD_OUT_PATH), help="output digest markdown path")
    args = parser.parse_args()

    knowledge = build_knowledge_from_asset_root(args.asset_root)
    output_path = write_knowledge(knowledge, args.out)
    digest = build_knowledge_digest(knowledge)
    digest_path = write_digest(digest, args.digest_out)
    digest_md_path = write_digest_markdown(digest, args.digest_md_out)
    print(f"[OK] wrote benchmark knowledge: {output_path}")
    print(f"[OK] wrote benchmark digest: {digest_path}")
    print(f"[OK] wrote benchmark digest markdown: {digest_md_path}")
    print(f"  source dirs: {len(knowledge.get('source_dirs', []))}")
    print(f"  provinces: {len(knowledge.get('provinces', {}))}")
    print(f"  record counts: {knowledge.get('record_counts', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
