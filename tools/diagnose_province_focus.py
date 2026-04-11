from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.classify_retriever_miss import quota_id_to_book
from tools.diagnose_recall_gap import analyze_recall_gaps, load_records


DEFAULT_INPUT = PROJECT_ROOT / "output" / "benchmark_assets"
DEFAULT_PRIORITY_REPORT = PROJECT_ROOT / "output" / "diagnostics" / "province_gap_report.json"
DEFAULT_PROVINCE = "江西省通用安装工程消耗量定额及统一基价表(2017)"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "diagnostics" / "province_focus_jx_install.json"


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_asset_dirs(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    if (path / "all_errors.jsonl").exists():
        return [path]
    return sorted(child for child in path.iterdir() if child.is_dir() and (child / "all_errors.jsonl").exists())


def _count_matching_rows(
    asset_path: Path,
    *,
    province: str = "",
    contains_terms: list[str] | None = None,
    specialty: str = "",
) -> int:
    contains_terms = [term for term in (contains_terms or []) if term]
    target_path = asset_path / "all_errors.jsonl" if asset_path.is_dir() else asset_path
    count = 0
    with target_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            province_name = _normalize_text(row.get("province", ""))
            if province and province_name != province:
                continue
            if contains_terms and not all(term in province_name for term in contains_terms):
                continue
            if specialty and _normalize_text(row.get("specialty", "")) != specialty:
                continue
            count += 1
    return count


def resolve_input_source(
    input_path: Path,
    *,
    province: str = "",
    contains_terms: list[str] | None = None,
    specialty: str = "",
) -> tuple[Path, dict]:
    asset_candidates = _iter_asset_dirs(input_path)
    if not asset_candidates:
        return input_path, {"input_source": str(input_path), "selection_mode": "direct"}
    if len(asset_candidates) == 1:
        selected = asset_candidates[0]
        return selected, {
            "input_source": str(selected),
            "selection_mode": "direct",
        }

    ranked: list[tuple[int, str, Path]] = []
    for candidate in asset_candidates:
        count = _count_matching_rows(
            candidate,
            province=province,
            contains_terms=contains_terms,
            specialty=specialty,
        )
        ranked.append((count, candidate.name, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected_count, _, selected_path = ranked[0]
    return selected_path, {
        "input_source": str(selected_path),
        "selection_mode": "max_matching_asset",
        "matched_rows_in_selected_asset": selected_count,
        "asset_ranking": [
            {"asset": path.name, "matched_rows": count}
            for count, _, path in ranked[:10]
        ],
    }


def resolve_target_province(
    records: list[dict],
    *,
    province: str = "",
    contains_terms: list[str] | None = None,
    fallback: str = DEFAULT_PROVINCE,
) -> str:
    province_counts = Counter(_normalize_text(row.get("province", "")) for row in records if _normalize_text(row.get("province", "")))
    if province and province in province_counts:
        return province

    contains_terms = [term for term in (contains_terms or []) if term]
    if contains_terms:
        matches = [
            name
            for name, _ in province_counts.most_common()
            if all(term in name for term in contains_terms)
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            if fallback in matches:
                return fallback
            return matches[0]

    if fallback in province_counts:
        return fallback
    if province_counts:
        return province_counts.most_common(1)[0][0]
    return province or fallback


def _candidate_map(record: dict) -> dict[str, dict]:
    snapshots = list(record.get("candidate_snapshots") or [])
    return {
        str(snapshot.get("quota_id", "")).strip(): snapshot
        for snapshot in snapshots
        if str(snapshot.get("quota_id", "")).strip()
    }


def _candidate_entity(snapshot: dict) -> str:
    features = dict(snapshot.get("candidate_canonical_features") or {})
    return _normalize_text(
        features.get("entity")
        or features.get("canonical_name")
        or snapshot.get("name")
        or ""
    )


def _fallback_subject(name: object) -> str:
    text = _normalize_text(name)
    if not text:
        return ""
    text = text.replace("\x7f", "").strip()
    text = re.split(r"\s|[(（≤<0-9]", text, maxsplit=1)[0]
    return text[:18]


def _top_rows(counter: Counter[str], examples: dict[str, list[dict]], limit: int) -> list[dict]:
    rows: list[dict] = []
    for key, count in counter.most_common(limit):
        rows.append(
            {
                "key": key,
                "count": count,
                "examples": examples.get(key, [])[:3],
            }
        )
    return rows


def _build_rank_case(record: dict) -> dict:
    expected_id = str((record.get("expected_quota_ids") or [""])[0] or "").strip()
    expected_name = _normalize_text((record.get("expected_quota_names") or [""])[0] or "")
    predicted_id = _normalize_text(record.get("predicted_quota_id", ""))
    predicted_name = _normalize_text(record.get("predicted_quota_name", ""))
    snap_map = _candidate_map(record)
    expected_snapshot = dict(snap_map.get(expected_id) or {})
    predicted_snapshot = dict(snap_map.get(predicted_id) or {})
    expected_entity = _candidate_entity(expected_snapshot) or _fallback_subject(expected_name)
    predicted_entity = _candidate_entity(predicted_snapshot) or _fallback_subject(predicted_name)
    expected_param = _safe_float(expected_snapshot.get("param_score"))
    predicted_param = _safe_float(predicted_snapshot.get("param_score"))
    expected_logic = _safe_float(expected_snapshot.get("logic_score"))
    predicted_logic = _safe_float(predicted_snapshot.get("logic_score"))
    expected_rank = _safe_float(expected_snapshot.get("ltr_score") or expected_snapshot.get("manual_structured_score"))
    predicted_rank = _safe_float(predicted_snapshot.get("ltr_score") or predicted_snapshot.get("manual_structured_score"))
    entity_confusion = bool(expected_entity and predicted_entity and expected_entity != predicted_entity)
    return {
        "bill_name": _normalize_text(record.get("bill_name", "")),
        "expected_quota_id": expected_id,
        "expected_quota_name": expected_name,
        "predicted_quota_id": predicted_id,
        "predicted_quota_name": predicted_name,
        "expected_entity": expected_entity,
        "predicted_entity": predicted_entity,
        "entity_confusion": entity_confusion,
        "expected_param_score": expected_param,
        "predicted_param_score": predicted_param,
        "expected_logic_score": expected_logic,
        "predicted_logic_score": predicted_logic,
        "expected_rank_score": expected_rank,
        "predicted_rank_score": predicted_rank,
        "rank_gap": round(predicted_rank - expected_rank, 4),
        "expected_param_better": expected_param > predicted_param,
        "expected_logic_better": expected_logic > predicted_logic,
    }


def analyze_ranking_gaps(records: list[dict], *, top_n: int = 10) -> dict:
    rank_rows = [row for row in records if bool(row.get("oracle_in_candidates"))]
    confusion_counter: Counter[str] = Counter()
    pair_counter: Counter[str] = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)
    pair_examples: dict[str, list[dict]] = defaultdict(list)
    rank_cases: list[dict] = []
    entity_confusion_total = 0
    same_entity_wrong_tier_total = 0
    expected_param_better_but_lost = 0
    expected_logic_better_but_lost = 0
    both_better_but_lost = 0

    for row in rank_rows:
        case = _build_rank_case(row)
        rank_cases.append(case)
        confusion_key = f"{case['expected_entity']} -> {case['predicted_entity']}".strip()
        pair_key = f"{case['expected_quota_name']} -> {case['predicted_quota_name']}".strip()
        confusion_counter[confusion_key] += 1
        pair_counter[pair_key] += 1
        examples[confusion_key].append(case)
        pair_examples[pair_key].append(case)
        if case["entity_confusion"]:
            entity_confusion_total += 1
        else:
            same_entity_wrong_tier_total += 1
        if case["expected_param_better"]:
            expected_param_better_but_lost += 1
        if case["expected_logic_better"]:
            expected_logic_better_but_lost += 1
        if case["expected_param_better"] and case["expected_logic_better"]:
            both_better_but_lost += 1

    rank_cases.sort(
        key=lambda row: (
            -abs(_safe_float(row.get("rank_gap"))),
            -_safe_float(row.get("predicted_rank_score")),
            row.get("bill_name", ""),
        )
    )
    return {
        "total": len(rank_rows),
        "entity_confusion_total": entity_confusion_total,
        "same_entity_wrong_tier_total": same_entity_wrong_tier_total,
        "expected_param_better_but_lost": expected_param_better_but_lost,
        "expected_logic_better_but_lost": expected_logic_better_but_lost,
        "both_param_and_logic_better_but_lost": both_better_but_lost,
        "top_entity_confusions": _top_rows(confusion_counter, examples, top_n),
        "top_name_pairs": _top_rows(pair_counter, pair_examples, top_n),
        "top_rank_cases": rank_cases[:top_n],
    }


def _is_wrong_book_record(record: dict) -> bool:
    if _normalize_text(record.get("cause", "")) == "wrong_book":
        return True
    expected_books = {
        quota_id_to_book(value)
        for value in record.get("expected_quota_ids", []) or []
        if quota_id_to_book(value)
    }
    predicted_book = quota_id_to_book(record.get("predicted_quota_id", ""))
    return bool(predicted_book and expected_books and predicted_book not in expected_books)


def analyze_wrong_book(records: list[dict], *, top_n: int = 10) -> dict:
    wrong_book_rows = [row for row in records if _is_wrong_book_record(row)]
    transition_counter: Counter[str] = Counter()
    examples: dict[str, list[dict]] = defaultdict(list)

    for row in wrong_book_rows:
        expected_books = sorted(
            {
                quota_id_to_book(value)
                for value in row.get("expected_quota_ids", []) or []
                if quota_id_to_book(value)
            }
        )
        predicted_book = quota_id_to_book(row.get("predicted_quota_id", ""))
        key = f"{predicted_book or '?'} -> {'/'.join(expected_books) or '?'}"
        detail = {
            "bill_name": _normalize_text(row.get("bill_name", "")),
            "expected_books": expected_books,
            "predicted_book": predicted_book,
            "expected_quota_name": _normalize_text((row.get("expected_quota_names") or [""])[0] or ""),
            "predicted_quota_name": _normalize_text(row.get("predicted_quota_name", "")),
        }
        transition_counter[key] += 1
        examples[key].append(detail)

    return {
        "total": len(wrong_book_rows),
        "top_transitions": _top_rows(transition_counter, examples, top_n),
        "sample_cases": [examples[key][0] for key, _ in transition_counter.most_common(min(top_n, len(transition_counter)))],
    }


def _load_priority_context(priority_report_path: Path, province: str) -> dict:
    payload = _load_json(priority_report_path)
    for row in payload.get("all_provinces", []) or []:
        if _normalize_text(row.get("province", "")) == province:
            return dict(row)
    return {}


def _derived_miss_category(record: dict) -> str:
    raw = _normalize_text(record.get("miss_category", ""))
    if raw:
        return raw
    if bool(record.get("oracle_in_candidates")):
        return "rank_miss"
    return "recall_miss"


def _derived_error_stage(record: dict) -> str:
    raw = _normalize_text(record.get("error_stage", ""))
    if raw:
        return raw
    if bool(record.get("oracle_in_candidates")):
        return "ranker"
    return "retriever"


def _build_priority_actions(report: dict) -> list[dict]:
    actions: list[dict] = []
    recall = dict(report.get("recall") or {})
    ranking = dict(report.get("ranking") or {})
    wrong_book = dict(report.get("wrong_book") or {})
    recall_counts = dict(recall.get("category_counts") or {})

    if _safe_int(recall_counts.get("query_dilution")) >= 10:
        actions.append(
            {
                "priority": 1,
                "category": "query_dilution",
                "count": _safe_int(recall_counts.get("query_dilution")),
                "suggested_fix": "trim_query_builder_append_terms",
            }
        )
    if _safe_int(recall_counts.get("synonym_gap")) >= 10:
        actions.append(
            {
                "priority": 2,
                "category": "synonym_gap",
                "count": _safe_int(recall_counts.get("synonym_gap")),
                "suggested_fix": "patch_province_synonyms_top_cases",
            }
        )
    if _safe_int(ranking.get("entity_confusion_total")) >= 5:
        actions.append(
            {
                "priority": 3,
                "category": "family_discrimination",
                "count": _safe_int(ranking.get("entity_confusion_total")),
                "suggested_fix": "strengthen_two_stage_family_gate",
            }
        )
    if _safe_int(ranking.get("expected_param_better_but_lost")) >= 10:
        actions.append(
            {
                "priority": 4,
                "category": "tier_ranking",
                "count": _safe_int(ranking.get("expected_param_better_but_lost")),
                "suggested_fix": "reduce_within_family_semantic_bias",
            }
        )
    if _safe_int(wrong_book.get("total")) >= 3:
        actions.append(
            {
                "priority": 5,
                "category": "book_routing",
                "count": _safe_int(wrong_book.get("total")),
                "suggested_fix": "add_route_guard_for_wrong_book_pairs",
            }
        )
    if not actions:
        actions.append(
            {
                "priority": 9,
                "category": "monitor_only",
                "count": _safe_int(report.get("summary", {}).get("error_total")),
                "suggested_fix": "collect_more_cases",
            }
        )
    return actions


def build_province_focus_report(
    records: list[dict],
    *,
    province: str,
    priority_context: dict | None = None,
    specialty: str = "",
    top_n: int = 10,
) -> dict:
    province_rows = [
        row
        for row in records
        if _normalize_text(row.get("province", "")) == province
        and (not specialty or _normalize_text(row.get("specialty", "")) == specialty)
    ]
    error_rows = [
        row
        for row in province_rows
        if not bool(row.get("oracle_in_candidates"))
        or _normalize_text(row.get("miss_category", ""))
        or _normalize_text(row.get("cause", ""))
        or _normalize_text(row.get("error_stage", ""))
    ]
    error_row_ids = {id(row) for row in error_rows}
    recall = analyze_recall_gaps(error_rows, top_synonyms=top_n, top_terms=top_n, top_rules=top_n)
    ranking = analyze_ranking_gaps([row for row in error_rows if bool(row.get("oracle_in_candidates"))], top_n=top_n)
    wrong_book = analyze_wrong_book(error_rows, top_n=top_n)
    cause_counts = Counter(_normalize_text(row.get("cause", "")) or "correct" for row in province_rows)
    miss_counts = Counter(
        _derived_miss_category(row) if id(row) in error_row_ids else (_normalize_text(row.get("miss_category", "")) or "none")
        for row in province_rows
    )
    stage_counts = Counter(
        _derived_error_stage(row) if id(row) in error_row_ids else (_normalize_text(row.get("error_stage", "")) or "correct")
        for row in province_rows
    )

    report = {
        "meta": {
            "province": province,
            "specialty": specialty,
            "top_n": top_n,
            "sample_total": len(province_rows),
            "error_total": len(error_rows),
        },
        "priority_context": dict(priority_context or {}),
        "summary": {
            "sample_total": len(province_rows),
            "error_total": len(error_rows),
            "recall_miss_total": _safe_int(recall.get("recall_miss_total")),
            "rank_miss_total": _safe_int(ranking.get("total")),
            "wrong_book_total": _safe_int(wrong_book.get("total")),
            "cause_counts": dict(cause_counts),
            "miss_category_counts": dict(miss_counts),
            "error_stage_counts": dict(stage_counts),
        },
        "recall": recall,
        "ranking": ranking,
        "wrong_book": wrong_book,
    }
    report["priority_actions"] = _build_priority_actions(report)
    return report


def _print_report(report: dict, selection_meta: dict) -> None:
    meta = dict(report.get("meta") or {})
    summary = dict(report.get("summary") or {})
    print(f"province: {meta.get('province', '')}")
    if selection_meta:
        print(f"input_source: {selection_meta.get('input_source', '')}")
        if selection_meta.get("selection_mode") == "max_matching_asset":
            print(f"selection_mode: {selection_meta['selection_mode']} matched_rows={selection_meta.get('matched_rows_in_selected_asset', 0)}")
    print(
        "summary: "
        f"samples={summary.get('sample_total', 0)} "
        f"errors={summary.get('error_total', 0)} "
        f"recall={summary.get('recall_miss_total', 0)} "
        f"rank={summary.get('rank_miss_total', 0)} "
        f"wrong_book={summary.get('wrong_book_total', 0)}"
    )
    print("priority_actions:")
    for row in report.get("priority_actions", []) or []:
        print(f"  p{row['priority']} {row['category']} x{row['count']} -> {row['suggested_fix']}")

    top_confusion = next(iter(report.get("ranking", {}).get("top_entity_confusions", []) or []), None)
    if top_confusion:
        print(f"top_entity_confusion: {top_confusion['key']} x{top_confusion['count']}")
    top_recall_term = next(iter(report.get("recall", {}).get("top_query_dilution_terms", []) or []), None)
    if top_recall_term:
        print(f"top_query_dilution: {top_recall_term['key']} x{top_recall_term['count']}")
    top_wrong_book = next(iter(report.get("wrong_book", {}).get("top_transitions", []) or []), None)
    if top_wrong_book:
        print(f"top_wrong_book: {top_wrong_book['key']} x{top_wrong_book['count']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a province-focused diagnostic report from benchmark outputs or benchmark assets.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input benchmark file, asset dir, or benchmark_assets root dir.")
    parser.add_argument("--province", default=DEFAULT_PROVINCE, help="Exact province name. Defaults to Jiangxi installation.")
    parser.add_argument("--contains", action="append", default=[], help="Optional province substring filters.")
    parser.add_argument("--specialty", default="", help="Optional specialty filter, for example C4.")
    parser.add_argument("--priority-report", default=str(DEFAULT_PRIORITY_REPORT), help="Province gap report JSON path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    parser.add_argument("--top", type=int, default=10, help="Top rows kept in each bucket.")
    parser.add_argument("--preview", action="store_true", help="Print report summary without writing file.")
    args = parser.parse_args()

    input_path = Path(args.input)
    selection_input, selection_meta = resolve_input_source(
        input_path,
        province=args.province,
        contains_terms=list(args.contains or []),
        specialty=args.specialty,
    )
    records = load_records(selection_input)
    province = resolve_target_province(
        records,
        province=args.province,
        contains_terms=list(args.contains or []),
    )
    priority_context = _load_priority_context(Path(args.priority_report), province)
    report = build_province_focus_report(
        records,
        province=province,
        priority_context=priority_context,
        specialty=args.specialty,
        top_n=max(int(args.top or 10), 1),
    )
    report["meta"]["input_source"] = selection_meta.get("input_source", str(selection_input))
    report["meta"]["selection"] = selection_meta
    _print_report(report, selection_meta)

    if not args.preview:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report_written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
