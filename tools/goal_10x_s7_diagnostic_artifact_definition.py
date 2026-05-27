from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1057_SUMMARY = AGENT_STATE / "goal_10x_s7_rank_position_candidate_pool_design_gate_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_RECALL_BOUNDARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_recall_boundary_report.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_family_guess(source_file: str) -> str:
    if source_file == "global_repair_decision_table.csv":
        return "generated_repair_decision_table"
    if source_file.startswith("v36_oss_r2"):
        return "oss_v36_canonicalizer_alignment"
    if source_file.startswith("v36_oss_r3"):
        return "oss_v36_speed_chain"
    if "global_rank_miss_shadow" in source_file:
        return "candidate_v36_global_rank_miss_shadow"
    if "data_fuel_r2_shadow" in source_file:
        return "candidate_v36_data_fuel_shadow"
    if "hard_param_guardrail" in source_file:
        return "candidate_v36_hard_param_guardrail"
    if "primary_param_consumption_guarded_speed" in source_file:
        return "candidate_v36_primary_param_guarded_speed"
    if source_file.startswith("v36_"):
        return "candidate_v36_unknown_trace"
    return "other_or_unknown"


def _taxonomy_disposition(row: dict[str, str]) -> str:
    source_file = row.get("source_file", "")
    reason = row.get("reason", "")
    query_family = row.get("query_family", "")
    top1_family = row.get("top1_family", "")
    if source_file == "global_repair_decision_table.csv":
        return "exclude"
    if reason in {"query_family_empty", "top1_family_empty"} or not query_family or not top1_family:
        return "taxonomy_cleanup"
    return "unknown"


def _book_relation(row: dict[str, str]) -> str:
    expected_books = {item.strip() for item in (row.get("expected_books") or "").split(",") if item.strip()}
    top1_book = (row.get("top1_book") or "").strip()
    if not expected_books and not top1_book:
        return "both_books_empty"
    if not expected_books:
        return "expected_book_empty"
    if not top1_book:
        return "top1_book_empty"
    if top1_book in expected_books:
        return "same_book"
    return "wrong_book"


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 6)


def _rank_position_distribution(wrong_rank: list[dict[str, str]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str, str, str, str, str, str]] = Counter()
    split_totals: Counter[str] = Counter()
    for row in wrong_rank:
        split = row.get("split", "")
        split_totals[split] += 1
        key = (
            split,
            row.get("rank_bucket", ""),
            row.get("reason", ""),
            row.get("query_family") or "<empty>",
            row.get("top1_family") or "<empty>",
            row.get("source_file", ""),
            _source_family_guess(row.get("source_file", "")),
            _book_relation(row),
        )
        counts[key] += 1
    rows: list[dict[str, Any]] = []
    for key, count in counts.items():
        split, rank_bucket, reason, query_family, top1_family, source_file, source_family, book_relation = key
        rows.append(
            {
                "split": split,
                "rank_bucket": rank_bucket,
                "reason": reason,
                "query_family": query_family,
                "top1_family": top1_family,
                "source_file": source_file,
                "source_family": source_family,
                "book_relation": book_relation,
                "count": count,
                "rate_within_split_wrong_rank": _rate(count, split_totals[split]),
                "taxonomy_disposition": "taxonomy_cleanup" if query_family == "<empty>" or top1_family == "<empty>" else "unknown",
                "diagnostic_use_only": True,
            }
        )
    return sorted(rows, key=lambda row: (-_int(row["count"]), row["split"], row["rank_bucket"], row["reason"]))


def _candidate_pool_boundary(
    wrong_rank: list[dict[str, str]],
    top80_missing: list[dict[str, str]],
    recall_boundary: list[dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    split_status_counts: Counter[tuple[str, str, str, str]] = Counter()
    split_totals: Counter[str] = Counter()
    for row in wrong_rank:
        split = row.get("split", "")
        split_totals[split] += 1
        split_status_counts[(split, "top80_present_wrong_rank", row.get("reason", ""), row.get("top80_rows", ""))] += 1
    for row in top80_missing:
        split = row.get("split", "")
        split_totals[split] += 1
        split_status_counts[(split, "top80_missing", row.get("reason", ""), row.get("top80_rows", ""))] += 1
    for key, count in split_status_counts.items():
        split, boundary_class, reason, top80_rows = key
        rows.append(
            {
                "source": "9x_gap_rows",
                "split": split,
                "boundary_class": boundary_class,
                "reason": reason,
                "top80_rows": top80_rows,
                "groups": count,
                "rate_within_split_gap_rows": _rate(count, split_totals[split]),
                "top80_present_groups": "",
                "top80_missing_groups": "",
                "top80_recall_rate": "",
                "diagnostic_decision": (
                    "ranking_position_problem" if boundary_class == "top80_present_wrong_rank" else "candidate_pool_or_recall_ceiling"
                ),
            }
        )
    seen_recall: set[tuple[str, str]] = set()
    for row in recall_boundary:
        split = row.get("split", "")
        candidate_id = row.get("candidate_id", "")
        key = (split, candidate_id)
        if key in seen_recall:
            continue
        seen_recall.add(key)
        rows.append(
            {
                "source": "dev_oof_recall_boundary",
                "split": split,
                "boundary_class": "top80_recall_boundary",
                "reason": "candidate_pool_boundary",
                "top80_rows": "80",
                "groups": _int(row.get("top80_present_groups")) + _int(row.get("top80_missing_groups")),
                "rate_within_split_gap_rows": "",
                "top80_present_groups": _int(row.get("top80_present_groups")),
                "top80_missing_groups": _int(row.get("top80_missing_groups")),
                "top80_recall_rate": _float(row.get("top80_recall_rate")),
                "diagnostic_decision": "ranking_claim_scope_top80_present_only",
            }
        )
    return sorted(rows, key=lambda row: (row["source"], row["split"], row["boundary_class"], -_int(row["groups"])))


def _loss_concentration_map(loss_audit: list[dict[str, str]]) -> list[dict[str, Any]]:
    candidate_loss: Counter[str] = Counter()
    candidate_gain: Counter[str] = Counter()
    for row in loss_audit:
        candidate_id = row.get("candidate_id", "")
        candidate_loss[candidate_id] += _int(row.get("loss"))
        candidate_gain[candidate_id] += _int(row.get("gain"))
    rows: list[dict[str, Any]] = []
    for row in loss_audit:
        candidate_id = row.get("candidate_id", "")
        loss = _int(row.get("loss"))
        gain = _int(row.get("gain"))
        net = _int(row.get("net"))
        total_loss = candidate_loss[candidate_id]
        total_gain = candidate_gain[candidate_id]
        rows.append(
            {
                "candidate_id": candidate_id,
                "slice_dimension": row.get("slice_dimension", ""),
                "slice_key": row.get("slice_key", ""),
                "groups": _int(row.get("groups")),
                "gain": gain,
                "loss": loss,
                "net": net,
                "loss_share_within_candidate": _rate(loss, total_loss),
                "gain_share_within_candidate": _rate(gain, total_gain),
                "candidate_total_loss": total_loss,
                "candidate_total_gain": total_gain,
                "candidate_hit1_rate": row.get("candidate_hit1_rate", ""),
                "diagnostic_flag": "loss_visible" if loss > 0 else "no_loss_on_slice",
                "forbidden_use": "do_not_select_or_freeze_candidate_from_diagnostic",
            }
        )
    return sorted(rows, key=lambda row: (-_int(row["loss"]), -abs(_int(row["net"])), row["candidate_id"], row["slice_dimension"]))


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    readiness: list[dict[str, Any]],
    boundary_summary: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.58 S7 Diagnostic Artifact Definition",
        "",
        "Read-only artifact definition for rank-position distribution, candidate-pool boundary, and loss concentration diagnostics.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rank_position_rows", metrics["rank_position_rows"]],
                ["candidate_pool_boundary_rows", metrics["candidate_pool_boundary_rows"]],
                ["loss_concentration_rows", metrics["loss_concentration_rows"]],
                ["readiness_pass_count", metrics["readiness_pass_count"]],
                ["readiness_fail_count", metrics["readiness_fail_count"]],
                ["definition_decision", metrics["definition_decision"]],
            ]
        ),
        "",
        "## Readiness Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in readiness]
        ),
        "",
        "## Boundary Summary",
        "",
        _md_table(
            [["boundary_class", "groups", "diagnostic_decision"]]
            + [[row["boundary_class"], row["groups"], row["diagnostic_decision"]] for row in boundary_summary[:10]]
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
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Define S7 diagnostic artifacts from existing dev/OOF inputs")
    parser.add_argument("--summary-1057", default=str(DEFAULT_1057_SUMMARY))
    parser.add_argument("--wrong-rank", default=str(DEFAULT_WRONG_RANK))
    parser.add_argument("--top80-missing", default=str(DEFAULT_TOP80_MISSING))
    parser.add_argument("--recall-boundary", default=str(DEFAULT_RECALL_BOUNDARY))
    parser.add_argument("--loss-audit", default=str(DEFAULT_LOSS_AUDIT))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1057 = _read_json(Path(args.summary_1057))
    wrong_rank = _read_csv(Path(args.wrong_rank))
    top80_missing = _read_csv(Path(args.top80_missing))
    recall_boundary = _read_csv(Path(args.recall_boundary))
    loss_audit = _read_csv(Path(args.loss_audit))

    rank_rows = _rank_position_distribution(wrong_rank)
    boundary_rows = _candidate_pool_boundary(wrong_rank, top80_missing, recall_boundary)
    loss_rows = _loss_concentration_map(loss_audit)

    boundary_by_class: Counter[str] = Counter()
    for row in boundary_rows:
        if row["source"] == "9x_gap_rows":
            boundary_by_class[row["boundary_class"]] += _int(row["groups"])
    boundary_summary = [
        {
            "boundary_class": boundary_class,
            "groups": groups,
            "diagnostic_decision": (
                "ranking_position_problem" if boundary_class == "top80_present_wrong_rank" else "candidate_pool_or_recall_ceiling"
            ),
        }
        for boundary_class, groups in boundary_by_class.most_common()
    ]
    dev_boundary = [row for row in recall_boundary if row.get("split") == "dev"]
    dev_top80_present = max((_int(row.get("top80_present_groups")) for row in dev_boundary), default=0)
    dev_top80_missing = max((_int(row.get("top80_missing_groups")) for row in dev_boundary), default=0)
    dev_top80_recall_rate = max((_float(row.get("top80_recall_rate")) for row in dev_boundary), default=0.0)

    readiness = [
        {
            "check_id": "RANK_POSITION_ARTIFACT_CREATED",
            "status": "pass" if rank_rows else "fail",
            "evidence": f"rank_position_rows={len(rank_rows)}; wrong_rank_input_rows={len(wrong_rank)}",
            "decision": "Every wrong-rank input row contributes to a rank-position diagnostic bucket.",
        },
        {
            "check_id": "CANDIDATE_POOL_BOUNDARY_CREATED",
            "status": "pass" if boundary_rows else "fail",
            "evidence": f"candidate_pool_boundary_rows={len(boundary_rows)}; dev_top80_recall_rate={dev_top80_recall_rate:.6f}",
            "decision": "Ranking failures and pool/recall ceiling failures are represented separately.",
        },
        {
            "check_id": "LOSS_CONCENTRATION_CREATED",
            "status": "pass" if loss_rows else "fail",
            "evidence": f"loss_concentration_rows={len(loss_rows)}; loss_audit_input_rows={len(loss_audit)}",
            "decision": "Loss rows remain visible; no net-only claim is needed.",
        },
        {
            "check_id": "DEV_OOF_BOUNDARY_PRESERVED",
            "status": "pass",
            "evidence": f"dev_top80_present_groups={dev_top80_present}; dev_top80_missing_groups={dev_top80_missing}; heldout_selection_allowed=false",
            "decision": "Artifacts are diagnostic-only and do not use heldout/hard selection.",
        },
        {
            "check_id": "NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "training_allowed=false; implementation_allowed=false; goal_searcher_change_allowed=false",
            "decision": "10.58 defines artifacts only.",
        },
    ]
    readiness_fail_count = sum(1 for row in readiness if row["status"] != "pass")

    blocked_actions = [
        {
            "blocked_action": "train_or_tune_from_s7_artifacts",
            "reason": "10.58 artifacts are diagnostic only and do not define a learning objective.",
            "allowed_after": "future explicit execution plan and go after strategy gates",
        },
        {
            "blocked_action": "change_candidate_pool_or_retrieval",
            "reason": "Candidate-pool boundary artifact can diagnose pool ceiling but cannot change retrieval.",
            "allowed_after": "future implementation plan with explicit go",
        },
        {
            "blocked_action": "select_using_heldout_or_hard",
            "reason": "Heldout/hard are not used for this diagnostic definition.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain",
            "reason": "Diagnostics are explanatory artifacts, not accuracy improvements.",
            "allowed_after": "future validated candidate with full loss audit",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rank_position_distribution_csv": str(output_prefix.with_name(output_prefix.name + "_rank_position_distribution.csv")),
        "candidate_pool_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_pool_boundary.csv")),
        "loss_concentration_map_csv": str(output_prefix.with_name(output_prefix.name + "_loss_concentration_map.csv")),
        "diagnostic_readiness_checks_csv": str(output_prefix.with_name(output_prefix.name + "_diagnostic_readiness_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1057["stage"],
        "rank_position_rows": len(rank_rows),
        "candidate_pool_boundary_rows": len(boundary_rows),
        "loss_concentration_rows": len(loss_rows),
        "wrong_rank_input_rows": len(wrong_rank),
        "top80_missing_input_rows": len(top80_missing),
        "loss_audit_input_rows": len(loss_audit),
        "dev_top80_present_groups": dev_top80_present,
        "dev_top80_missing_groups": dev_top80_missing,
        "dev_top80_recall_rate": round(dev_top80_recall_rate, 6),
        "readiness_pass_count": len(readiness) - readiness_fail_count,
        "readiness_fail_count": readiness_fail_count,
        "definition_decision": "artifact_definition_complete" if readiness_fail_count == 0 else "artifact_definition_incomplete",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.58 S7 diagnostic artifact definition",
        "read_only": True,
        "artifact_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define the S7 diagnostic artifacts. Rank-position distribution, candidate-pool boundary, loss-concentration map, and diagnostic readiness checks are now available for read-only review. "
            "These artifacts explain failure modes and preserve the top80_present/top80_missing boundary; they do not authorize training, retrieval changes, candidate-matrix expansion, heldout/hard selection, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.58 only writes diagnostic artifacts from existing dev/OOF and 9.x gap inputs. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.59 S7 diagnostic artifact acceptance gate",
            "goal": "Read-only decide whether the 10.58 S7 artifacts are acceptable for future strategy support and what they imply for the next non-execution lane.",
            "default": "acceptance gate only; no training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(
        Path(artifacts["rank_position_distribution_csv"]),
        rank_rows,
        [
            "split",
            "rank_bucket",
            "reason",
            "query_family",
            "top1_family",
            "source_file",
            "source_family",
            "book_relation",
            "count",
            "rate_within_split_wrong_rank",
            "taxonomy_disposition",
            "diagnostic_use_only",
        ],
    )
    _write_csv(
        Path(artifacts["candidate_pool_boundary_csv"]),
        boundary_rows,
        [
            "source",
            "split",
            "boundary_class",
            "reason",
            "top80_rows",
            "groups",
            "rate_within_split_gap_rows",
            "top80_present_groups",
            "top80_missing_groups",
            "top80_recall_rate",
            "diagnostic_decision",
        ],
    )
    _write_csv(
        Path(artifacts["loss_concentration_map_csv"]),
        loss_rows,
        [
            "candidate_id",
            "slice_dimension",
            "slice_key",
            "groups",
            "gain",
            "loss",
            "net",
            "loss_share_within_candidate",
            "gain_share_within_candidate",
            "candidate_total_loss",
            "candidate_total_gain",
            "candidate_hit1_rate",
            "diagnostic_flag",
            "forbidden_use",
        ],
    )
    _write_csv(
        Path(artifacts["diagnostic_readiness_checks_csv"]),
        readiness,
        ["check_id", "status", "evidence", "decision"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, readiness, boundary_summary)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
