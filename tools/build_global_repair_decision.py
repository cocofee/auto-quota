import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "global_repair_decision.v2"
BASE_CSV_FIELDS = [
    "sample_id",
    "province",
    "error_stage",
    "attribution_category",
    "expected_ids",
    "selected_id",
    "recall_rank",
    "pre_ltr_top1_id",
    "post_ltr_top1_id",
    "post_final_top1_id",
]
CLUSTER_CSV_FIELDS = [
    "bill_name",
    "bill_text",
    "expected_names",
    "selected_name",
    "specialty",
    "match_source",
    "expected_prefixes",
    "selected_prefix",
    "common_issue_key",
]
CSV_FIELDS = BASE_CSV_FIELDS + CLUSTER_CSV_FIELDS
KEY_DIAGNOSTIC_FIELDS = BASE_CSV_FIELDS[2:]
ACTION_BY_BUCKET = {
    "R1": "fix_r1_recall",
    "R2": "fix_r2_ltr",
    "R3": "fix_r3_cgr",
    "R4": "fix_r4_picker",
    "R5": "fix_r5_validator",
    "R6": "review_data",
}
BUCKET_TERMS = {
    "R1": ("r1", "recall", "召回", "candidate", "retrieval", "missing_candidate", "recall_miss", "retriever", "oracle_not_in_candidates"),
    "R2": ("r2", "ltr", "rank", "rerank", "选错", "pre_ltr", "post_ltr", "post_rank"),
    "R3": ("r3", "cgr", "guard", "constraint", "reasoning_guard"),
    "R4": ("r4", "picker", "category_safe", "family_picker", "final_pick", "explicit_picker"),
    "R5": ("r5", "validator", "final_validator", "experience"),
    "R6": ("r6", "data", "label", "expected", "ambiguous", "unknown", "unclassified", "other"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_latest_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
        return records

    obj = _load_json(path)
    if isinstance(obj, list):
        return [row for row in obj if isinstance(row, dict)]
    if not isinstance(obj, dict):
        return records
    if isinstance(obj.get("details"), list):
        return [row for row in obj["details"] if isinstance(row, dict)]
    if isinstance(obj.get("records"), list):
        return [row for row in obj["records"] if isinstance(row, dict)]
    if isinstance(obj.get("results"), list):
        results = obj["results"]
        if all(isinstance(row, dict) and "details" in row for row in results):
            for province_result in results:
                province = province_result.get("province", "")
                for detail in province_result.get("details") or []:
                    if isinstance(detail, dict):
                        detail = dict(detail)
                        detail.setdefault("province", province)
                        records.append(detail)
            return records
        return [row for row in results if isinstance(row, dict)]
    return records


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [str(item) for item in value if item not in (None, "")]
    if value == "":
        return []
    return [str(value)]


def _first_present(record: dict[str, Any], names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return default


def _sample_id(record: dict[str, Any], index: int) -> str:
    explicit = _first_present(record, ("sample_id", "id", "bill_id"))
    if explicit:
        return str(explicit)
    province = str(record.get("province") or "unknown")
    bill = str(record.get("bill_name") or record.get("bill_text") or "sample")
    return f"{province}:{bill}:{index}"


def _expected_ids(record: dict[str, Any]) -> list[str]:
    return _as_list(
        _first_present(
            record,
            ("expected_ids", "expected_quota_ids", "stored_ids", "correct_quota_ids", "correct_quota_id"),
        )
    )


def _selected_id(record: dict[str, Any]) -> str:
    return str(
        _first_present(
            record,
            ("selected_id", "predicted_quota_id", "algo_id", "post_final_top1_id", "quota_id"),
        )
        or ""
    )


def _expected_names(record: dict[str, Any]) -> list[str]:
    return _as_list(
        _first_present(
            record,
            ("expected_names", "expected_quota_names", "stored_names", "correct_quota_names", "correct_quota_name"),
        )
    )


def _selected_name(record: dict[str, Any]) -> str:
    return str(
        _first_present(
            record,
            ("selected_name", "predicted_quota_name", "algo_name", "post_final_top1_name", "quota_name"),
        )
        or ""
    )


def _id_prefix(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"([A-Za-z]*\d+(?:-\d+)?)", text)
    if match:
        return match.group(1).upper()
    match = re.match(r"([A-Za-z]+\d*)", text)
    return match.group(1).upper() if match else ""


def _expected_prefixes(expected_ids: list[str]) -> list[str]:
    prefixes = sorted({_id_prefix(expected_id) for expected_id in expected_ids if _id_prefix(expected_id)})
    return prefixes


def _normalize_key_part(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text)
    return text.strip("_") or "unknown"


def _common_issue_key(row: dict[str, str]) -> str:
    bucket = _bucket_for(row.get("error_stage", ""), row.get("attribution_category", ""))
    category = _normalize_key_part(row.get("attribution_category") or row.get("error_stage"))
    specialty = _normalize_key_part(row.get("specialty"))
    match_source = _normalize_key_part(row.get("match_source"))
    selected_prefix = row.get("selected_prefix") or _id_prefix(row.get("selected_id", ""))
    expected_prefix = row.get("expected_prefixes") or "|".join(_expected_prefixes(_as_list(row.get("expected_ids"))))
    transition = f"{selected_prefix or 'unknown'}->{expected_prefix or 'unknown'}"
    return "::".join([bucket, category, specialty, match_source, transition])


def _is_wrong(record: dict[str, Any], expected_ids: list[str], selected_id: str) -> bool:
    for name in ("passed", "is_correct", "correct", "is_match"):
        if name in record and isinstance(record[name], bool):
            return not record[name]
    status = str(record.get("status") or "").lower()
    if status in {"wrong", "failed", "fail", "false", "incorrect"}:
        return True
    if status in {"passed", "pass", "correct", "ok", "true"}:
        return False
    if expected_ids and selected_id:
        return selected_id not in set(expected_ids)
    return False


def _recall_rank(record: dict[str, Any], expected_ids: list[str]) -> Any:
    explicit = _first_present(record, ("recall_rank", "oracle_rank"), None)
    if explicit is not None:
        return explicit
    candidates = _as_list(record.get("all_candidate_ids") or record.get("recall_topk_ids"))
    if not candidates or not expected_ids:
        return ""
    expected = set(expected_ids)
    for idx, candidate_id in enumerate(candidates, start=1):
        if candidate_id in expected:
            return idx
    return -1


def _bucket_for(error_stage: str, attribution_category: str) -> str:
    texts = [
        str(attribution_category or "").lower(),
        str(error_stage or "").lower(),
    ]
    if not any(texts):
        return "R6"
    for text in texts:
        if not text:
            continue
        for bucket, terms in BUCKET_TERMS.items():
            if any(term in text for term in terms):
                return bucket
    return "R6"


def build_rows(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        expected_ids = _expected_ids(record)
        selected_id = _selected_id(record)
        if not _is_wrong(record, expected_ids, selected_id):
            continue
        expected_prefixes = _expected_prefixes(expected_ids)
        attribution_category = str(
            _first_present(record, ("attribution_category", "miss_category", "cause", "error_type"), "")
        )
        row = {
            "sample_id": _sample_id(record, index),
            "province": str(record.get("province") or ""),
            "error_stage": str(record.get("error_stage") or record.get("miss_stage") or ""),
            "attribution_category": attribution_category,
            "expected_ids": "|".join(expected_ids),
            "selected_id": selected_id,
            "recall_rank": str(_recall_rank(record, expected_ids)),
            "pre_ltr_top1_id": str(record.get("pre_ltr_top1_id") or ""),
            "post_ltr_top1_id": str(record.get("post_ltr_top1_id") or ""),
            "post_final_top1_id": str(record.get("post_final_top1_id") or selected_id),
            "bill_name": str(_first_present(record, ("bill_name", "name", "item_name", "project_name"), "")),
            "bill_text": str(_first_present(record, ("bill_text", "query", "description", "raw_text"), "")),
            "expected_names": "|".join(_expected_names(record)),
            "selected_name": _selected_name(record),
            "specialty": str(_first_present(record, ("specialty", "major", "book", "quota_book"), "")),
            "match_source": str(_first_present(record, ("match_source", "source", "candidate_source", "retrieval_source"), "")),
            "expected_prefixes": "|".join(expected_prefixes),
            "selected_prefix": _id_prefix(selected_id),
        }
        row["common_issue_key"] = _common_issue_key(row)
        rows.append(row)
    return rows


def _missing_field_rate(rows: list[dict[str, str]]) -> float:
    if not rows:
        return 0.0
    missing = 0
    for row in rows:
        if any(not str(row.get(field, "")).strip() for field in KEY_DIAGNOSTIC_FIELDS):
            missing += 1
    return round(missing / len(rows), 4)


def _top_values(rows: list[dict[str, str]], field: str, limit: int = 5) -> list[str]:
    counter = Counter(str(row.get(field, "")).strip() for row in rows if str(row.get(field, "")).strip())
    return [value for value, _ in counter.most_common(limit)]


def _id_examples(rows: list[dict[str, str]], field: str, limit: int = 5) -> list[str]:
    examples: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for value in _as_list(row.get(field)):
            if value and value not in seen:
                seen.add(value)
                examples.append(value)
                if len(examples) >= limit:
                    return examples
    return examples


def build_common_issue_clusters(rows: list[dict[str, str]], max_clusters: int = 10) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = row.get("common_issue_key") or _common_issue_key(row)
        groups[key].append(row)

    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            -len(item[1]),
            _bucket_for(item[1][0].get("error_stage", ""), item[1][0].get("attribution_category", "")),
            item[0],
        ),
    )
    clusters: list[dict[str, Any]] = []
    wrong_total = len(rows) or 1
    for index, (issue_key, cluster_rows) in enumerate(ordered_groups[:max_clusters], start=1):
        bucket = _bucket_for(cluster_rows[0].get("error_stage", ""), cluster_rows[0].get("attribution_category", ""))
        cluster_id = f"{bucket}-{index:02d}"
        sample_ids = [row["sample_id"] for row in cluster_rows]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "bucket": bucket,
                "issue_key": issue_key,
                "sample_count": len(cluster_rows),
                "sample_ratio": round(len(cluster_rows) / wrong_total, 4),
                "commonality": "shared" if len(cluster_rows) >= 2 else "singleton_only",
                "representative_sample_ids": sample_ids[:10],
                "expected_id_examples": _id_examples(cluster_rows, "expected_ids"),
                "selected_id_examples": _id_examples(cluster_rows, "selected_id"),
                "recommended_action": ACTION_BY_BUCKET.get(bucket, ACTION_BY_BUCKET["R6"]),
                "shared_signals": {
                    "error_stages": _top_values(cluster_rows, "error_stage"),
                    "attribution_categories": _top_values(cluster_rows, "attribution_category"),
                    "provinces": _top_values(cluster_rows, "province"),
                    "specialties": _top_values(cluster_rows, "specialty"),
                    "selected_prefixes": _top_values(cluster_rows, "selected_prefix"),
                    "expected_prefixes": _top_values(cluster_rows, "expected_prefixes"),
                    "match_sources": _top_values(cluster_rows, "match_source"),
                },
            }
        )
    return clusters


def build_summary(rows: list[dict[str, str]], latest_path: Path, attribution_path: Path) -> dict[str, Any]:
    bucket_counts: Counter[str] = Counter()
    for row in rows:
        bucket_counts[_bucket_for(row["error_stage"], row["attribution_category"])] += 1
    largest_bucket = ""
    if bucket_counts:
        largest_bucket = sorted(bucket_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    common_issue_clusters = build_common_issue_clusters(rows)
    shared_clusters = [cluster for cluster in common_issue_clusters if int(cluster["sample_count"]) >= 2]
    if shared_clusters:
        target_common_issue = shared_clusters[0]
    else:
        target_common_issue = next(
            (cluster for cluster in common_issue_clusters if cluster["bucket"] == largest_bucket),
            common_issue_clusters[0] if common_issue_clusters else {},
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "input_latest_path": str(latest_path),
        "input_attribution_path": str(attribution_path),
        "wrong_total": len(rows),
        "stage_counts": dict(sorted(bucket_counts.items())),
        "missing_field_rate": _missing_field_rate(rows),
        "largest_bucket": largest_bucket,
        "common_issue_clusters": common_issue_clusters,
        "target_common_issue": target_common_issue,
        "cluster_selection_reason": "largest common_issue_cluster by sample_count, then bucket/key",
    }


def build_next_action(summary: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, Any]:
    largest_bucket = summary.get("largest_bucket") or "R6"
    target_common_issue = summary.get("target_common_issue") or {}
    target_bucket = target_common_issue.get("bucket") or largest_bucket
    if float(summary.get("missing_field_rate") or 0.0) > 0.1:
        action = "improve_diagnostics"
        reason = "missing_field_rate > 10%"
    else:
        target_bucket = target_bucket if target_bucket in ACTION_BY_BUCKET else "R6"
        action = ACTION_BY_BUCKET[target_bucket]
        if target_common_issue:
            reason = (
                f"target_common_issue={target_common_issue.get('cluster_id')}; "
                f"bucket={target_bucket}; samples={target_common_issue.get('sample_count')}; "
                f"commonality={target_common_issue.get('commonality')}"
            )
        else:
            reason = f"largest_bucket={target_bucket}"
    representative_ids = list(target_common_issue.get("representative_sample_ids") or [])
    if not representative_ids:
        representative_rows = [
            row for row in rows if _bucket_for(row["error_stage"], row["attribution_category"]) == target_bucket
        ]
        representative_ids = [row["sample_id"] for row in representative_rows[:10]]
    cluster_sample_count = int(target_common_issue.get("sample_count") or len(representative_ids))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "action": action,
        "reason": reason,
        "largest_bucket": largest_bucket,
        "sample_count": cluster_sample_count,
        "target_common_issue": target_common_issue,
        "cluster_sample_ids": representative_ids,
        "representative_sample_ids": representative_ids,
        "suggested_validation_scope": {
            "latest_path": summary["input_latest_path"],
            "attribution_path": summary["input_attribution_path"],
            "filter_bucket": target_bucket,
            "filter_cluster_id": target_common_issue.get("cluster_id", ""),
            "filter_common_issue_key": target_common_issue.get("issue_key", ""),
            "sample_limit": min(50, cluster_sample_count or summary.get("wrong_total", 0)),
        },
        "input_latest_path": summary["input_latest_path"],
        "input_attribution_path": summary["input_attribution_path"],
        "full_validation_status": "pending",
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", required=True, type=Path)
    parser.add_argument("--attribution", required=True, type=Path)
    parser.add_argument("--decision-table", required=True, type=Path)
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/attribution/global_repair_decision_summary.json"),
    )
    parser.add_argument(
        "--next-action",
        type=Path,
        default=Path("reports/attribution/global_repair_next_action.json"),
    )
    args = parser.parse_args()

    if not args.latest.exists():
        raise SystemExit(f"latest not found: {args.latest}")
    if not args.attribution.exists():
        raise SystemExit(f"attribution not found: {args.attribution}")

    records = _iter_latest_records(args.latest)
    rows = build_rows(records)
    if not rows:
        raise SystemExit("no wrong samples found in latest input")

    summary = build_summary(rows, args.latest, args.attribution)
    if not summary["largest_bucket"]:
        raise SystemExit("largest_bucket is empty")
    next_action = build_next_action(summary, rows)

    _write_csv(args.decision_table, rows)
    _write_json(args.summary, summary)
    _write_json(args.next_action, next_action)

    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "decision_table": str(args.decision_table),
                "summary": str(args.summary),
                "next_action": str(args.next_action),
                "wrong_total": len(rows),
                "largest_bucket": summary["largest_bucket"],
                "missing_field_rate": summary["missing_field_rate"],
                "action": next_action["action"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
