from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config  # noqa: E402
from src.goal_search import GoalSearcher  # noqa: E402
from src.goal_search.national_index import clean_text  # noqa: E402
from tools.goal_build_ltr_features import (  # noqa: E402
    DIAG_COLUMNS,
    FEATURE_COLUMNS,
    _build_feature_row,
    _feature_text,
    _query_signal,
    _query_text,
)
from tools.goal_eval import _expected_ids, _load_rows, _row_id, _row_province, _with_leakage_controls  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run_summary.md"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_LOCAL_ASSETS_DB_DIR = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522" / "db"

DEFAULT_SPLIT_INPUTS = {
    "dev": PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "dev.jsonl",
    "heldout": PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "heldout_validation.jsonl",
    "hard": PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "hard_validation.jsonl",
}

FORBIDDEN_TRAINING_FEATURES = {
    "anchor_group_id",
    "anchor_reason",
    "anchor_status",
    "bill_name",
    "bill_text",
    "candidate_id",
    "candidate_name",
    "candidate_rank",
    "correct_quota_id",
    "expected_id",
    "expected_ids",
    "expected_quota_id",
    "expected_quota_ids",
    "group_id",
    "name",
    "positive_id",
    "project_name",
    "province",
    "query",
    "query_text",
    "quota_book",
    "quota_chapter",
    "quota_id",
    "quota_name",
    "quota_unit",
    "raw_query",
    "reasons",
    "row_index",
    "sample_id",
    "source_file",
    "split",
    "stored_ids",
}


def _read_feature_whitelist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} missing training_features")
    result = [clean_text(feature) for feature in features if clean_text(feature)]
    forbidden = sorted(set(result) & FORBIDDEN_TRAINING_FEATURES)
    if forbidden:
        raise ValueError(f"forbidden fields leaked into training_features: {forbidden}")
    return result


def _clean_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result


def _format_number(value: Any) -> str:
    number = _clean_float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10g}"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_matrix_csv(path: Path, rows: list[dict[str, Any]], training_features: list[str]) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    invalid_numeric: Counter[str] = Counter()
    missing_features: Counter[str] = Counter()
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["label", *training_features], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            matrix_row: dict[str, Any] = {"label": int(row.get("label") or 0)}
            for feature in training_features:
                if feature not in row:
                    missing_features[feature] += 1
                value = row.get(feature, 0)
                try:
                    float(value)
                except (TypeError, ValueError):
                    invalid_numeric[feature] += 1
                matrix_row[feature] = _format_number(value)
            writer.writerow(matrix_row)
    issues: list[dict[str, Any]] = []
    issues.extend({"type": "missing_feature", "feature": key, "count": value} for key, value in missing_features.most_common())
    issues.extend({"type": "invalid_numeric", "feature": key, "count": value} for key, value in invalid_numeric.most_common())
    return issues


def _write_group_files(group_path: Path, group_meta_path: Path, group_sizes: list[int], group_meta: list[dict[str, Any]]) -> None:
    group_path.parent.mkdir(parents=True, exist_ok=True)
    group_path.write_text("\n".join(str(size) for size in group_sizes) + ("\n" if group_sizes else ""), encoding="utf-8")
    with group_meta_path.open("w", encoding="utf-8") as handle:
        for row in group_meta:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _copy_whitelist(path: Path, training_features: list[str], source_whitelist: Path) -> None:
    payload = {
        "stage": "Goal LTR v1 / stage 6.5 query anchored matrix dry run",
        "training_features": training_features,
        "label_column": "label",
        "group_column": "group_id",
        "excluded_diagnostic_columns": sorted(set(DIAG_COLUMNS) | FORBIDDEN_TRAINING_FEATURES),
        "source_whitelist": str(source_whitelist),
        "notes": [
            "Eval-only dry run; no model training.",
            "Matrix CSV includes only label plus whitelisted numeric features.",
            "Identifiers and answer fields remain only in ltr_features_<split>.jsonl diagnostics.",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _input_path(split: str, args: argparse.Namespace) -> Path:
    override = getattr(args, f"{split}_input", "")
    if override:
        return Path(override)
    return DEFAULT_SPLIT_INPUTS[split]


def _has_any_quota_db(provinces_dir: Path) -> bool:
    if not provinces_dir.exists():
        return False
    return any(provinces_dir.glob("*/quota.db"))


def _configure_db_dirs(db_dir_arg: str) -> dict[str, Any]:
    configured_db_dir = Path(db_dir_arg) if db_dir_arg else Path(config.DB_DIR)
    candidates = [configured_db_dir]
    if DEFAULT_LOCAL_ASSETS_DB_DIR not in candidates:
        candidates.append(DEFAULT_LOCAL_ASSETS_DB_DIR)

    selected_db_dir = configured_db_dir
    selected_reason = "configured"
    for candidate in candidates:
        provinces_dir = candidate / "provinces"
        if _has_any_quota_db(provinces_dir):
            selected_db_dir = candidate
            selected_reason = "configured" if candidate == configured_db_dir else "local_assets_fallback"
            break

    config.DB_DIR = selected_db_dir
    config.COMMON_DB_DIR = selected_db_dir / "common"
    config.PROVINCES_DB_DIR = selected_db_dir / "provinces"
    return {
        "db_dir": str(config.DB_DIR),
        "common_db_dir": str(config.COMMON_DB_DIR),
        "provinces_db_dir": str(config.PROVINCES_DB_DIR),
        "reason": selected_reason,
        "has_quota_db": _has_any_quota_db(config.PROVINCES_DB_DIR),
    }


def _group_id(split: str, row: dict[str, Any], row_index: int) -> str:
    return clean_text(row.get("anchor_group_id")) or f"{split}:{row_index}:{_row_id(row, row_index)}"


def _row_reject_reason(row: dict[str, Any], expected: set[str], searcher: GoalSearcher | None = None) -> str:
    if not _row_province(row):
        return "missing_province"
    if not (_query_text(row) or _feature_text(row)):
        return "missing_query_text"
    if not expected:
        return "missing_expected_ids"
    if searcher is not None:
        missing = sorted(qid for qid in expected if qid not in searcher.index.by_quota_id)
        if missing:
            return "expected_id_not_in_local_db"
    return ""


def _top_snapshot(hits: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "quota_id": hit.quota_id,
            "name": hit.name,
            "score": hit.score,
            "confidence": hit.confidence,
            "reasons": list(hit.reasons or []),
        }
        for rank, hit in enumerate(hits[:limit], 1)
    ]


def _feature_coverage(rows: list[dict[str, Any]], training_features: list[str]) -> list[dict[str, Any]]:
    total = len(rows)
    coverage: list[dict[str, Any]] = []
    for feature in training_features:
        present = 0
        nonzero = 0
        for row in rows:
            value = row.get(feature)
            if value not in ("", None):
                present += 1
            if _clean_float(value) != 0.0:
                nonzero += 1
        coverage.append(
            {
                "feature": feature,
                "present": present,
                "present_rate": round(present / total, 6) if total else 0.0,
                "nonzero": nonzero,
                "nonzero_rate": round(nonzero / total, 6) if total else 0.0,
            }
        )
    return coverage


def _top_counter(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _build_split(
    *,
    split: str,
    input_path: Path,
    output_dir: Path,
    training_features: list[str],
    args: argparse.Namespace,
    searchers: dict[str, GoalSearcher],
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = _load_rows(input_path)
    if args.limit > 0:
        rows = rows[: args.limit]

    feature_rows: list[dict[str, Any]] = []
    group_sizes: list[int] = []
    group_meta: list[dict[str, Any]] = []
    recall_gap_rows: list[dict[str, Any]] = []
    anchor_excluded_rows: list[dict[str, Any]] = []

    reject_counts: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    recall_gap_family_counts: Counter[str] = Counter()
    accepted_family_counts: Counter[str] = Counter()
    positive_rank_counts: Counter[str] = Counter()
    candidate_count_counts: Counter[str] = Counter()

    eligible_rows = 0
    searched_rows = 0
    accepted_groups = 0
    positive_rows = 0
    baseline_hit1 = 0
    baseline_hit5 = 0

    for row_index, row in enumerate(rows, 1):
        province = _row_province(row)
        expected = _expected_ids(row)
        gid = _group_id(split, row, row_index)
        pre_reason = _row_reject_reason(row, expected)
        if pre_reason:
            reject_counts[pre_reason] += 1
            anchor_excluded_rows.append(
                {
                    "split": split,
                    "group_id": gid,
                    "row_index": row_index,
                    "sample_id": _row_id(row, row_index),
                    "source_file": clean_text(row.get("source_file")),
                    "project_name": clean_text(row.get("project_name")),
                    "province": province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "reject_reason": pre_reason,
                }
            )
            continue

        if province not in searchers:
            searchers[province] = GoalSearcher(province)
        searcher = searchers[province]
        local_reason = _row_reject_reason(row, expected, searcher)
        if local_reason:
            reject_counts[local_reason] += 1
            anchor_excluded_rows.append(
                {
                    "split": split,
                    "group_id": gid,
                    "row_index": row_index,
                    "sample_id": _row_id(row, row_index),
                    "source_file": clean_text(row.get("source_file")),
                    "project_name": clean_text(row.get("project_name")),
                    "province": province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "missing_expected_ids": sorted(qid for qid in expected if qid not in searcher.index.by_quota_id),
                    "reject_reason": local_reason,
                }
            )
            continue

        eligible_rows += 1
        province_counts[province] += 1
        item = _with_leakage_controls(row, args)
        hits = searcher.search(item, top_k=args.top_k)
        searched_rows += 1
        if len(hits) < 2:
            reject_counts["insufficient_candidates"] += 1
            anchor_excluded_rows.append(
                {
                    "split": split,
                    "group_id": gid,
                    "row_index": row_index,
                    "sample_id": _row_id(row, row_index),
                    "source_file": clean_text(row.get("source_file")),
                    "project_name": clean_text(row.get("project_name")),
                    "province": province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "candidate_count": len(hits),
                    "reject_reason": "insufficient_candidates",
                }
            )
            continue

        top_ids = [hit.quota_id for hit in hits]
        expected_rank = next((rank for rank, quota_id in enumerate(top_ids, 1) if quota_id in expected), None)
        query_signal = _query_signal(row)
        if expected_rank is None:
            recall_gap_family_counts[query_signal.family or "<empty>"] += 1
            recall_gap_rows.append(
                {
                    "split": split,
                    "group_id": gid,
                    "row_index": row_index,
                    "sample_id": _row_id(row, row_index),
                    "source_file": clean_text(row.get("source_file")),
                    "project_name": clean_text(row.get("project_name")),
                    "province": province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "query_family": query_signal.family,
                    "candidate_count": len(hits),
                    "top_ids": top_ids[:10],
                    "top": _top_snapshot(hits, limit=5),
                    "recall_gap_reason": "expected_id_not_in_topk",
                }
            )
            continue

        group_feature_rows: list[dict[str, Any]] = []
        for hit_rank, hit in enumerate(hits, 1):
            if hit.quota_id not in searcher.index.by_quota_id:
                continue
            feature_row = _build_feature_row(
                split=split,
                row=row,
                row_index=row_index,
                hit_rank=hit_rank,
                hit=hit,
                searcher=searcher,
                query_signal=query_signal,
                expected=expected,
            )
            feature_row["group_id"] = gid
            feature_row["anchor_status"] = clean_text(row.get("anchor_status"))
            feature_row["anchor_reason"] = clean_text(row.get("anchor_reason"))
            group_feature_rows.append(feature_row)

        group_positive_rows = sum(1 for item_row in group_feature_rows if int(item_row.get("label") or 0) == 1)
        if not group_feature_rows or group_positive_rows <= 0:
            recall_gap_rows.append(
                {
                    "split": split,
                    "group_id": gid,
                    "row_index": row_index,
                    "sample_id": _row_id(row, row_index),
                    "source_file": clean_text(row.get("source_file")),
                    "project_name": clean_text(row.get("project_name")),
                    "province": province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "query_family": query_signal.family,
                    "candidate_count": len(group_feature_rows),
                    "top_ids": top_ids[:10],
                    "recall_gap_reason": "positive_filtered_after_feature_build",
                }
            )
            continue

        accepted_groups += 1
        positive_rows += group_positive_rows
        baseline_hit1 += int(expected_rank == 1)
        baseline_hit5 += int(expected_rank <= 5)
        positive_rank_counts[str(expected_rank)] += 1
        candidate_count_counts[str(len(group_feature_rows))] += 1
        accepted_family_counts[query_signal.family or "<empty>"] += 1
        feature_rows.extend(group_feature_rows)
        group_sizes.append(len(group_feature_rows))
        group_meta.append(
            {
                "split": split,
                "group_id": gid,
                "rows": len(group_feature_rows),
                "positive_count": group_positive_rows,
                "positive_rank": expected_rank,
                "sample_id": _row_id(row, row_index),
                "source_file": clean_text(row.get("source_file")),
                "project_name": clean_text(row.get("project_name")),
                "province": province,
                "query": _query_text(row),
                "expected_ids": sorted(expected),
                "anchor_status": clean_text(row.get("anchor_status")),
                "anchor_type": "expected_id_anchor",
                "query_family": query_signal.family,
                "candidate_count": len(group_feature_rows),
            }
        )

        if args.progress_every > 0 and row_index % args.progress_every == 0:
            print(
                f"[{split}] processed {row_index}/{len(rows)} rows; "
                f"accepted={accepted_groups}; recall_gap={len(recall_gap_rows)}; excluded={len(anchor_excluded_rows)}",
                file=sys.stderr,
            )

    feature_jsonl = output_dir / f"ltr_features_{split}.jsonl"
    matrix_csv = output_dir / f"ltr_matrix_{split}.csv"
    group_txt = output_dir / f"ltr_group_{split}.txt"
    group_jsonl = output_dir / f"ltr_group_{split}.jsonl"
    recall_gap_jsonl = output_dir / f"recall_gap_{split}.jsonl"
    anchor_excluded_jsonl = output_dir / f"anchor_excluded_{split}.jsonl"

    _write_jsonl(feature_jsonl, feature_rows)
    matrix_issues = _write_matrix_csv(matrix_csv, feature_rows, training_features)
    _write_group_files(group_txt, group_jsonl, group_sizes, group_meta)
    _write_jsonl(recall_gap_jsonl, recall_gap_rows)
    _write_jsonl(anchor_excluded_jsonl, anchor_excluded_rows)

    group_sum = sum(group_sizes)
    matrix_rows_match_group = group_sum == len(feature_rows)
    elapsed = time.perf_counter() - started
    return {
        "split": split,
        "input_path": str(input_path),
        "input_rows": len(rows),
        "eligible_anchor_rows": eligible_rows,
        "searched_rows": searched_rows,
        "accepted_groups": accepted_groups,
        "matrix_rows": len(feature_rows),
        "group_sum": group_sum,
        "matrix_rows_match_group": matrix_rows_match_group,
        "positive_rows": positive_rows,
        "positive_group_rate": round(accepted_groups / eligible_rows, 6) if eligible_rows else 0.0,
        "top80_recall_rate": round(accepted_groups / eligible_rows, 6) if eligible_rows else 0.0,
        "baseline_hit1_groups": baseline_hit1,
        "baseline_hit1_rate_on_matrix_groups": round(baseline_hit1 / accepted_groups, 6) if accepted_groups else 0.0,
        "baseline_hit5_groups": baseline_hit5,
        "baseline_hit5_rate_on_matrix_groups": round(baseline_hit5 / accepted_groups, 6) if accepted_groups else 0.0,
        "recall_gap_groups": len(recall_gap_rows),
        "anchor_excluded_groups": len(anchor_excluded_rows),
        "reject_counts": dict(reject_counts),
        "province_counts": _top_counter(province_counts),
        "accepted_family_counts": _top_counter(accepted_family_counts),
        "recall_gap_family_counts": _top_counter(recall_gap_family_counts),
        "positive_rank_counts": dict(sorted(positive_rank_counts.items(), key=lambda item: int(item[0]))),
        "candidate_count_counts": dict(candidate_count_counts),
        "feature_coverage": _feature_coverage(feature_rows, training_features),
        "matrix_issue_count": len(matrix_issues),
        "matrix_issues": matrix_issues[:20],
        "outputs": {
            "feature_jsonl": str(feature_jsonl),
            "matrix_csv": str(matrix_csv),
            "group_txt": str(group_txt),
            "group_jsonl": str(group_jsonl),
            "recall_gap_jsonl": str(recall_gap_jsonl),
            "anchor_excluded_jsonl": str(anchor_excluded_jsonl),
        },
        "elapsed_sec": round(elapsed, 3),
        "rows_per_sec": round(searched_rows / elapsed, 3) if elapsed > 0 else None,
    }


def _validate_report(report: dict[str, Any], training_features: list[str]) -> list[str]:
    failures: list[str] = []
    forbidden = sorted(set(training_features) & FORBIDDEN_TRAINING_FEATURES)
    if forbidden:
        failures.append(f"forbidden_training_features={forbidden}")
    for split in report["splits"]:
        if split["matrix_issue_count"]:
            failures.append(f"{split['split']}:matrix_issues={split['matrix_issues'][:3]}")
        if not split["matrix_rows_match_group"]:
            failures.append(f"{split['split']}:group_sum_mismatch")
        if split["accepted_groups"] > 0 and split["positive_rows"] <= 0:
            failures.append(f"{split['split']}:accepted_groups_without_positive_rows")
    return failures


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Goal Query-Anchored Ranking Matrix Dry Run",
        "",
        "Stage 6.5 eval-only. It generates loader-readable matrices only for query groups where a validated expected_id appears in TopK. Recall gaps are written separately. No model training, no rerank switch, no search rule changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["top_k", report["top_k"]],
                ["training_features", len(report["training_features"])],
                ["forbidden_feature_leak_count", report["forbidden_feature_leak_count"]],
                ["validation_passed", report["validation_passed"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Splits",
        "",
        _md_table(
            [
                [
                    "split",
                    "input",
                    "eligible",
                    "accepted",
                    "matrix_rows",
                    "recall_gap",
                    "anchor_excluded",
                    "top80_recall",
                    "baseline_hit1_on_matrix",
                    "elapsed_sec",
                ],
                *[
                    [
                        split["split"],
                        split["input_rows"],
                        split["eligible_anchor_rows"],
                        split["accepted_groups"],
                        split["matrix_rows"],
                        split["recall_gap_groups"],
                        split["anchor_excluded_groups"],
                        split["top80_recall_rate"],
                        split["baseline_hit1_rate_on_matrix_groups"],
                        split["elapsed_sec"],
                    ]
                    for split in report["splits"]
                ],
            ]
        ),
        "",
        "## Outputs",
        "",
    ]
    output_rows = [["split", "matrix_csv", "group_txt", "features_jsonl", "recall_gap"]]
    for split in report["splits"]:
        outputs = split["outputs"]
        output_rows.append([split["split"], outputs["matrix_csv"], outputs["group_txt"], outputs["feature_jsonl"], outputs["recall_gap_jsonl"]])
    lines.extend([_md_table(output_rows), "", "## Validation Failures", "", json.dumps(report["validation_failures"], ensure_ascii=False, indent=2), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6.5 eval-only query anchored ranking matrix dry run")
    parser.add_argument("--splits", default="dev,heldout,hard")
    parser.add_argument("--dev-input", default="")
    parser.add_argument("--heldout-input", default="")
    parser.add_argument("--hard-input", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--db-dir", default="", help="Optional db root containing provinces/ and common/. Falls back to local assets when project db is empty.")
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--allow-answer-priors", action="store_true", help="Compatibility only; default is leakage-safe")
    parser.add_argument("--exclude-sample-id", default="")
    parser.add_argument("--exclude-source-file", default="")
    parser.add_argument("--exclude-project-name", default="")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    split_names = [part.strip() for part in args.splits.split(",") if part.strip()]
    unknown = [split for split in split_names if split not in DEFAULT_SPLIT_INPUTS]
    if unknown:
        raise ValueError(f"unsupported splits: {unknown}")

    training_features = _read_feature_whitelist(Path(args.feature_whitelist))
    db_config = _configure_db_dirs(args.db_dir)
    if not db_config["has_quota_db"]:
        raise ValueError(f"no quota.db found under {db_config['provinces_db_dir']}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_whitelist(output_dir / "ltr_feature_whitelist_query_anchored_v1.json", training_features, Path(args.feature_whitelist))

    searchers: dict[str, GoalSearcher] = {}
    split_reports: list[dict[str, Any]] = []
    for split in split_names:
        split_reports.append(
            _build_split(
                split=split,
                input_path=_input_path(split, args),
                output_dir=output_dir,
                training_features=training_features,
                args=args,
                searchers=searchers,
            )
        )

    report = {
        "stage": "Goal LTR v1 / stage 6.5 query anchored matrix dry run",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "top_k": args.top_k,
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "db_config": db_config,
        "training_features": training_features,
        "forbidden_training_features": sorted(set(training_features) & FORBIDDEN_TRAINING_FEATURES),
        "forbidden_feature_leak_count": len(set(training_features) & FORBIDDEN_TRAINING_FEATURES),
        "output_dir": str(output_dir),
        "splits": split_reports,
        "searcher_count": len(searchers),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report["validation_failures"] = _validate_report(report, training_features)
    report["validation_passed"] = not report["validation_failures"]

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "no_training": report["no_training"],
                    "top_k": report["top_k"],
                    "training_features": len(training_features),
                    "validation_passed": report["validation_passed"],
                    "validation_failures": report["validation_failures"],
                    "searcher_count": report["searcher_count"],
                    "elapsed_sec": report["elapsed_sec"],
                    "output_dir": report["output_dir"],
                },
                "splits": [
                    {
                        key: split[key]
                        for key in (
                            "split",
                            "input_rows",
                            "eligible_anchor_rows",
                            "accepted_groups",
                            "matrix_rows",
                            "positive_rows",
                            "recall_gap_groups",
                            "anchor_excluded_groups",
                            "top80_recall_rate",
                            "baseline_hit1_rate_on_matrix_groups",
                            "elapsed_sec",
                        )
                    }
                    for split in split_reports
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["validation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
