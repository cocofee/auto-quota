from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_WRONG_RANK = PROJECT_ROOT / "reports" / "agent_state" / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_FIRE_DAMPER_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_duct_fire_damper_evidence_9x_review_rows.csv"
DEFAULT_VALVE_DUCT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_duct_size_tier_transferability_9x_review_rows.csv"
DEFAULT_VALVE_SAME_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_same_family_unknown_9x_audit_rows.csv"
DEFAULT_LAMP_SAME_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_lamp_same_family_unknown_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_lamp_same_family_9x"

BLOCKED_REASONS = {"query_family_empty"}
EXHAUSTED_FAMILY_REASON = {
    ("electrical_box", "near_miss_rank_2_5"): "electrical_box_near_miss_exhausted_in_9_7_9_8",
}
EXHAUSTED_FAMILIES = {
    "pipe": "pipe_direction_already_audited_in_9_1_to_9_5",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return _clean(row.get("query_family")) or "<empty>", _clean(row.get("reason")) or "<empty>"


def _load_group_ids(path: Path, status: str | None = None) -> set[str]:
    if not path.exists():
        return set()
    rows = _read_csv(path)
    if status:
        rows = [row for row in rows if _clean(row.get("transferability_status")) == status]
    return {_clean(row.get("group_id")) for row in rows if _clean(row.get("group_id"))}


def _eligibility(family: str, reason: str, count: int, provinces: set[str], sources: set[str]) -> tuple[str, str]:
    if reason in BLOCKED_REASONS or family in {"", "<empty>"}:
        return "blocked", "query_family_empty_is_too_broad_for_one_bucket"
    if (family, reason) in EXHAUSTED_FAMILY_REASON:
        return "excluded", EXHAUSTED_FAMILY_REASON[(family, reason)]
    if family in EXHAUSTED_FAMILIES:
        return "excluded", EXHAUSTED_FAMILIES[family]
    if count < 20:
        return "deprioritized", "support_below_20_after_exclusions"
    if len(provinces) < 3:
        return "deprioritized", "province_diversity_below_3"
    if len(sources) < 2:
        return "deprioritized", "source_diversity_below_2"
    return "eligible", "passes_dev_selection_gate"


def _score(reason: str, count: int, provinces: set[str], sources: set[str], status: str) -> float:
    if status != "eligible":
        return -1.0
    reason_bonus = {
        "near_miss_rank_2_5": 30.0,
        "same_family_or_unknown_wrong_rank": 18.0,
        "top1_wrong_book": 12.0,
    }.get(reason, 0.0)
    return count + reason_bonus + min(len(provinces), 8) * 2.0 + min(len(sources), 5) * 3.0


def _summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_key(row), []).append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for (family, reason), items in grouped.items():
        provinces = {_clean(row.get("province")) for row in items if _clean(row.get("province"))}
        sources = {_clean(row.get("source_file")) for row in items if _clean(row.get("source_file"))}
        rank_buckets = Counter(_clean(row.get("rank_bucket")) or "<empty>" for row in items)
        expected_books = Counter(_clean(row.get("expected_books")) or "<empty>" for row in items)
        positive_ranks = [_to_int(row.get("positive_rank_min")) for row in items if _to_int(row.get("positive_rank_min"))]
        status, status_reason = _eligibility(family, reason, len(items), provinces, sources)
        out.append(
            {
                "query_family": family,
                "reason": reason,
                "count": len(items),
                "rate_within_dev_wrong_rank_after_exclusions": _rate(len(items), total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "top_rank_bucket": rank_buckets.most_common(1)[0][0] if rank_buckets else "",
                "top_rank_bucket_count": rank_buckets.most_common(1)[0][1] if rank_buckets else 0,
                "top_expected_book": expected_books.most_common(1)[0][0] if expected_books else "",
                "top_expected_book_count": expected_books.most_common(1)[0][1] if expected_books else 0,
                "min_positive_rank": min(positive_ranks) if positive_ranks else "",
                "median_positive_rank_hint": sorted(positive_ranks)[len(positive_ranks) // 2] if positive_ranks else "",
                "eligibility": status,
                "eligibility_reason": status_reason,
                "selection_score": round(_score(reason, len(items), provinces, sources, status), 3),
                "provinces": " | ".join(sorted(provinces)),
                "source_files": " | ".join(sorted(sources)),
            }
        )
    out.sort(key=lambda row: (float(row["selection_score"]), int(row["count"])), reverse=True)
    return out


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


def _write_markdown(path: Path, report: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    selected = report["selected_candidate"]
    lines = [
        "# Stage 9.19 Ranked Gap Reselection After Lamp Same-family Audit",
        "",
        "Dev-only reselection from wrong-rank buckets after removing exhausted, blocked, and label-heavy lamp directions. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["dev_wrong_rank_rows", report["metrics"]["dev_wrong_rank_rows"]],
                ["excluded_fire_damper_rows", report["metrics"]["excluded_fire_damper_rows"]],
                ["excluded_valve_duct_block_rows", report["metrics"]["excluded_valve_duct_block_rows"]],
                ["excluded_valve_same_family_rows", report["metrics"]["excluded_valve_same_family_rows"]],
                ["excluded_lamp_same_family_rows", report["metrics"]["excluded_lamp_same_family_rows"]],
                ["remaining_dev_wrong_rank_rows", report["metrics"]["remaining_dev_wrong_rank_rows"]],
                ["candidate_groups", report["metrics"]["candidate_groups"]],
                ["eligible_candidates", report["metrics"]["eligible_candidates"]],
                ["selected_family", selected["query_family"]],
                ["selected_reason", selected["reason"]],
                ["selected_support", selected["count"]],
                ["selected_province_count", selected["province_count"]],
                ["selected_source_count", selected["source_count"]],
                ["next_stage", report["next_stage"]["stage"]],
            ]
        ),
        "",
        "## Top Candidate Buckets",
        "",
        _md_table(
            [["family", "reason", "count", "province_count", "source_count", "eligibility", "score"]]
            + [
                [row["query_family"], row["reason"], row["count"], row["province_count"], row["source_count"], row["eligibility"], row["selection_score"]]
                for row in candidates[:14]
            ]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.19 ranked gap reselection after lamp same-family audit")
    parser.add_argument("--wrong-rank", default=str(DEFAULT_WRONG_RANK))
    parser.add_argument("--fire-damper-rows", default=str(DEFAULT_FIRE_DAMPER_ROWS))
    parser.add_argument("--valve-duct-rows", default=str(DEFAULT_VALVE_DUCT_ROWS))
    parser.add_argument("--valve-same-rows", default=str(DEFAULT_VALVE_SAME_ROWS))
    parser.add_argument("--lamp-same-rows", default=str(DEFAULT_LAMP_SAME_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    fire_damper_group_ids = _load_group_ids(Path(args.fire_damper_rows))
    valve_duct_group_ids = _load_group_ids(Path(args.valve_duct_rows), status="blocked_single_source")
    valve_same_group_ids = _load_group_ids(Path(args.valve_same_rows))
    lamp_same_group_ids = _load_group_ids(Path(args.lamp_same_rows))
    blocked_group_ids = fire_damper_group_ids | valve_duct_group_ids | valve_same_group_ids | lamp_same_group_ids

    all_wrong_rank = [
        row
        for row in _read_csv(Path(args.wrong_rank))
        if _clean(row.get("split")) == "dev" and _clean(row.get("status")) == "top80_present_but_wrong_rank"
    ]
    all_group_ids = {_clean(row.get("group_id")) for row in all_wrong_rank if _clean(row.get("group_id"))}
    remaining = [row for row in all_wrong_rank if _clean(row.get("group_id")) not in blocked_group_ids]
    candidates = _summarize(remaining)
    eligible = [row for row in candidates if row["eligibility"] == "eligible"]
    selected = eligible[0] if eligible else candidates[0]
    selected_rows = [
        row
        for row in remaining
        if (_clean(row.get("query_family")) or "<empty>") == selected["query_family"]
        and (_clean(row.get("reason")) or "<empty>") == selected["reason"]
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidates_csv": str(output_prefix.with_name(output_prefix.name + "_candidates.csv")),
        "selected_rows_csv": str(output_prefix.with_name(output_prefix.name + "_selected_rows.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.19 ranked gap reselection after lamp same-family audit",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifact": str(Path(args.wrong_rank)),
        "exhausted_fire_damper_artifact": str(Path(args.fire_damper_rows)),
        "blocked_valve_duct_artifact": str(Path(args.valve_duct_rows)),
        "blocked_valve_same_artifact": str(Path(args.valve_same_rows)),
        "blocked_lamp_same_artifact": str(Path(args.lamp_same_rows)),
        "metrics": {
            "dev_wrong_rank_rows": len(all_wrong_rank),
            "excluded_fire_damper_rows": len(fire_damper_group_ids & all_group_ids),
            "excluded_valve_duct_block_rows": len(valve_duct_group_ids & all_group_ids),
            "excluded_valve_same_family_rows": len(valve_same_group_ids & all_group_ids),
            "excluded_lamp_same_family_rows": len(lamp_same_group_ids & all_group_ids),
            "excluded_group_ids_total": len(blocked_group_ids & all_group_ids),
            "remaining_dev_wrong_rank_rows": len(remaining),
            "candidate_groups": len(candidates),
            "eligible_candidates": len(eligible),
            "excluded_candidates": sum(1 for row in candidates if row["eligibility"] == "excluded"),
            "blocked_or_deprioritized_candidates": sum(1 for row in candidates if row["eligibility"] in {"blocked", "deprioritized"}),
        },
        "selected_candidate": selected,
        "decision": (
            f"Select {selected['query_family']} + {selected['reason']} after excluding exhausted pipe/electrical_box directions, "
            "duct fire-damper review rows, blocked valve directions, and the label-heavy lamp same-family bucket. "
            f"It has support={selected['count']}, province_count={selected['province_count']}, source_count={selected['source_count']}. "
            "The next step is read-only audit of this selected bucket, not a rule change."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.19 only reselects the next dev audit bucket. It subtracts the blocked lamp same-family rows and does not use heldout, train, tune, write rules, or change GoalSearcher.",
        "next_stage": {
            "stage": f"9.20 {selected['query_family']} {selected['reason']} audit",
            "goal": f"audit the {selected['count']} dev rows in {selected['query_family']} + {selected['reason']} and split them into transferable evidence, label issues, same-source artifacts, and ranking/parameter buckets",
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
            ],
        },
    }

    candidate_fields = [
        "query_family",
        "reason",
        "count",
        "rate_within_dev_wrong_rank_after_exclusions",
        "province_count",
        "source_count",
        "top_rank_bucket",
        "top_rank_bucket_count",
        "top_expected_book",
        "top_expected_book_count",
        "min_positive_rank",
        "median_positive_rank_hint",
        "eligibility",
        "eligibility_reason",
        "selection_score",
        "provinces",
        "source_files",
    ]
    selected_fields = [
        "split",
        "status",
        "reason",
        "rank_bucket",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_books",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_ranks",
        "positive_rank_min",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
        "top1_score",
        "top1_reasons",
        "top80_rows",
    ]
    _write_csv(Path(artifacts["candidates_csv"]), candidates, candidate_fields)
    _write_csv(Path(artifacts["selected_rows_csv"]), selected_rows, selected_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, candidates)

    print(json.dumps({"summary": artifacts["summary_json"], "selected_candidate": selected, "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
