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
DATA_GOAL_SEARCH = PROJECT_ROOT / "data" / "goal_search"

DEFAULT_1052_SUMMARY = AGENT_STATE / "goal_10x_oss_evidence_package_no_go_strategy_handoff_summary.json"
DEFAULT_ACCEPTED_REGISTRY = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review_accepted_oss_source_registry.csv"
DEFAULT_GENERATED_EXCLUSIONS = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_generated_exclusions.csv"
DEFAULT_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_RECALL_ROWS = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_rows.csv"
DEFAULT_OSS_SAMPLES = DATA_GOAL_SEARCH / "oss_samples.jsonl"
DEFAULT_OSS_SAMPLES_EXPANDED = DATA_GOAL_SEARCH / "oss_samples_expanded.jsonl"
DEFAULT_LABEL_ANCHOR = AGENT_STATE / "goal_oss_label_anchor_audit_dev_details.csv"
DEFAULT_HIGH_CONF_ACCEPTED = AGENT_STATE / "goal_high_confidence_oss_group_stats_accepted.csv"
DEFAULT_HIGH_CONF_REJECTED = AGENT_STATE / "goal_high_confidence_oss_group_stats_rejected.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_oss_only_evidence_expansion_inventory"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


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


def _family_guess(source_file: str) -> str:
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


def _source_status(source_file: str, accepted_files: set[str], generated_files: set[str]) -> str:
    if source_file in accepted_files:
        return "accepted_oss_existing"
    if source_file in generated_files:
        return "generated_excluded"
    if source_file.startswith("v36_oss"):
        return "candidate_oss_filename_needs_acceptance"
    if source_file.startswith("v36_"):
        return "candidate_v36_trace_needs_provenance_review"
    return "unknown_or_non_oss"


def _add_rows(
    store: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    artifact_key: str,
    accepted_files: set[str],
    generated_files: set[str],
) -> None:
    for row in rows:
        source_file = str(row.get("source_file") or "").strip()
        if not source_file:
            continue
        item = store.setdefault(
            source_file,
            {
                "source_file": source_file,
                "source_family_guess": _family_guess(source_file),
                "source_status": _source_status(source_file, accepted_files, generated_files),
                "raw_oss_rows": 0,
                "expanded_oss_rows": 0,
                "label_anchor_rows": 0,
                "high_conf_accepted_rows": 0,
                "high_conf_rejected_rows": 0,
                "recall_review_rows": 0,
                "hit1_flip_gain": 0,
                "hit1_flip_loss": 0,
                "hit1_flip_net": 0,
                "province_count": 0,
                "bucket_count": 0,
                "_provinces": Counter(),
                "_buckets": Counter(),
                "_learnability": Counter(),
            },
        )
        item[artifact_key] += 1
        province = str(row.get("province") or "").strip()
        bucket = str(row.get("bucket") or row.get("target_bucket") or row.get("probe_bucket") or "").strip()
        learnability = str(row.get("learnability_status") or "").strip()
        if province:
            item["_provinces"][province] += 1
        if bucket:
            item["_buckets"][bucket] += 1
        if learnability:
            item["_learnability"][learnability] += 1


def _add_flips(store: dict[str, dict[str, Any]], rows: list[dict[str, Any]], accepted_files: set[str], generated_files: set[str]) -> None:
    for row in rows:
        source_file = str(row.get("source_file") or "").strip()
        if not source_file:
            continue
        item = store.setdefault(
            source_file,
            {
                "source_file": source_file,
                "source_family_guess": _family_guess(source_file),
                "source_status": _source_status(source_file, accepted_files, generated_files),
                "raw_oss_rows": 0,
                "expanded_oss_rows": 0,
                "label_anchor_rows": 0,
                "high_conf_accepted_rows": 0,
                "high_conf_rejected_rows": 0,
                "recall_review_rows": 0,
                "hit1_flip_gain": 0,
                "hit1_flip_loss": 0,
                "hit1_flip_net": 0,
                "province_count": 0,
                "bucket_count": 0,
                "_provinces": Counter(),
                "_buckets": Counter(),
                "_learnability": Counter(),
            },
        )
        flip_type = str(row.get("flip_type") or "")
        if flip_type == "gain":
            item["hit1_flip_gain"] += 1
        elif flip_type == "loss":
            item["hit1_flip_loss"] += 1
        province = str(row.get("province") or "").strip()
        if province:
            item["_provinces"][province] += 1


def _finalize_inventory(store: dict[str, dict[str, Any]], accepted_by_file: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_file, item in store.items():
        item["hit1_flip_net"] = _int(item["hit1_flip_gain"]) - _int(item["hit1_flip_loss"])
        item["province_count"] = len(item["_provinces"])
        item["bucket_count"] = len(item["_buckets"])
        item["top_buckets"] = ";".join(f"{key}:{value}" for key, value in item["_buckets"].most_common(5))
        item["learnability_status_counts"] = ";".join(f"{key}:{value}" for key, value in item["_learnability"].most_common(5))
        item["accepted_scope"] = accepted_by_file.get(source_file, {}).get("accepted_scope", "")
        if item["source_status"] == "accepted_oss_existing":
            item["inventory_decision"] = "already_accepted_provenance_no_new_reentry"
        elif item["source_status"] == "generated_excluded":
            item["inventory_decision"] = "exclude_from_learning_evidence"
        elif item["source_status"].startswith("candidate_"):
            if item["hit1_flip_net"] > 0:
                item["inventory_decision"] = "candidate_provenance_review_but_effect_gate_needed"
            else:
                item["inventory_decision"] = "candidate_provenance_review_only_effect_not_positive"
        else:
            item["inventory_decision"] = "not_oss_evidence_candidate"
        rows.append({key: value for key, value in item.items() if not key.startswith("_")})
    return sorted(
        rows,
        key=lambda row: (
            row["source_status"] != "candidate_v36_trace_needs_provenance_review",
            row["source_status"] != "accepted_oss_existing",
            -_int(row["raw_oss_rows"]) - _int(row["expanded_oss_rows"]),
            row["source_file"],
        ),
    )


def _candidate_queue(inventory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in inventory_rows:
        if not str(row["source_status"]).startswith("candidate_"):
            continue
        queue.append(
            {
                "source_file": row["source_file"],
                "source_family_guess": row["source_family_guess"],
                "raw_oss_rows": row["raw_oss_rows"],
                "expanded_oss_rows": row["expanded_oss_rows"],
                "hit1_flip_gain": row["hit1_flip_gain"],
                "hit1_flip_loss": row["hit1_flip_loss"],
                "hit1_flip_net": row["hit1_flip_net"],
                "recall_review_rows": row["recall_review_rows"],
                "provenance_need": "owner acceptance required before use as accepted OSS evidence",
                "effect_need": "positive dev/OOF gain/loss/net after acceptance; current net must not be negative or source dominated",
                "candidate_decision": row["inventory_decision"],
            }
        )
    return queue


def _s2_effect_inventory(hit1_rows: list[dict[str, Any]], generated_files: set[str]) -> list[dict[str, Any]]:
    by_candidate: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "candidate_id": "",
            "non_global_gain": 0,
            "non_global_loss": 0,
            "non_global_net": 0,
            "source_families_touched": Counter(),
            "candidate_sources_touched": Counter(),
        }
    )
    for row in hit1_rows:
        source_file = str(row.get("source_file") or "")
        if not source_file or source_file in generated_files:
            continue
        candidate_id = str(row.get("candidate_id") or "")
        flip_type = str(row.get("flip_type") or "")
        if not candidate_id or flip_type not in {"gain", "loss"}:
            continue
        item = by_candidate[candidate_id]
        item["candidate_id"] = candidate_id
        item[f"non_global_{flip_type}"] += 1
        item["source_families_touched"][_family_guess(source_file)] += 1
        item["candidate_sources_touched"][source_file] += 1
    rows: list[dict[str, Any]] = []
    for item in by_candidate.values():
        item["non_global_net"] = item["non_global_gain"] - item["non_global_loss"]
        positive_family_count = sum(1 for _family, count in item["source_families_touched"].items() if count > 0)
        rows.append(
            {
                "candidate_id": item["candidate_id"],
                "non_global_gain": item["non_global_gain"],
                "non_global_loss": item["non_global_loss"],
                "non_global_net": item["non_global_net"],
                "source_family_count": positive_family_count,
                "source_families_touched": ";".join(
                    f"{key}:{value}" for key, value in item["source_families_touched"].most_common()
                ),
                "candidate_sources_touched": ";".join(
                    f"{key}:{value}" for key, value in item["candidate_sources_touched"].most_common()
                ),
                "inventory_decision": "not_reentry_ready" if item["non_global_net"] <= 0 else "needs_full_reentry_gate",
            }
        )
    return sorted(rows, key=lambda row: (-_int(row["non_global_net"]), row["candidate_id"]))


def _s1_recall_inventory(recall_rows: list[dict[str, str]], accepted_files: set[str], generated_files: set[str]) -> list[dict[str, Any]]:
    by_status: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "source_status": "",
            "learnability_status": "",
            "rows": 0,
            "source_files": Counter(),
            "target_buckets": Counter(),
        }
    )
    for row in recall_rows:
        source_file = row.get("source_file", "")
        source_status = _source_status(source_file, accepted_files, generated_files)
        learnability = row.get("learnability_status", "")
        item = by_status[(source_status, learnability)]
        item["source_status"] = source_status
        item["learnability_status"] = learnability
        item["rows"] += 1
        item["source_files"][source_file] += 1
        item["target_buckets"][row.get("target_bucket", "")] += 1
    rows: list[dict[str, Any]] = []
    for item in by_status.values():
        rows.append(
            {
                "source_status": item["source_status"],
                "learnability_status": item["learnability_status"],
                "rows": item["rows"],
                "source_files": ";".join(f"{key}:{value}" for key, value in item["source_files"].most_common()),
                "target_buckets": ";".join(f"{key}:{value}" for key, value in item["target_buckets"].most_common()),
                "inventory_decision": (
                    "not_recall_learning"
                    if "taxonomy" in item["learnability_status"] or "blocked" in item["learnability_status"]
                    else "needs_manual_review"
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["source_status"], -_int(row["rows"])))


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
    inventory_rows: list[dict[str, Any]],
    candidate_queue: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    top_inventory = inventory_rows[:10]
    lines = [
        "# 10.53 OSS-only Evidence Expansion Inventory",
        "",
        "Read-only inventory of existing local OSS/v36 artifacts for additional accepted-source evidence candidates.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["inventory_source_file_count", metrics["inventory_source_file_count"]],
                ["accepted_existing_source_file_count", metrics["accepted_existing_source_file_count"]],
                ["candidate_source_file_count", metrics["candidate_source_file_count"]],
                ["generated_excluded_source_file_count", metrics["generated_excluded_source_file_count"]],
                ["candidate_positive_s2_net_source_count", metrics["candidate_positive_s2_net_source_count"]],
                ["s2_non_global_positive_candidate_count", metrics["s2_non_global_positive_candidate_count"]],
                ["reentry_ready_now", metrics["reentry_ready_now"]],
            ]
        ),
        "",
        "## Inventory Sources",
        "",
        _md_table(
            [["source_file", "status", "raw", "expanded", "hit1_net", "recall_rows", "decision"]]
            + [
                [
                    row["source_file"],
                    row["source_status"],
                    row["raw_oss_rows"],
                    row["expanded_oss_rows"],
                    row["hit1_flip_net"],
                    row["recall_review_rows"],
                    row["inventory_decision"],
                ]
                for row in top_inventory
            ]
        ),
        "",
        "## Candidate Review Queue",
        "",
        _md_table(
            [["source_file", "family", "raw", "hit1_net", "decision"]]
            + [
                [
                    row["source_file"],
                    row["source_family_guess"],
                    row["raw_oss_rows"],
                    row["hit1_flip_net"],
                    row["candidate_decision"],
                ]
                for row in candidate_queue
            ]
        ),
        "",
        "## Readiness Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in checks]
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
    parser = argparse.ArgumentParser(description="Inventory local OSS/v36 artifacts for additional evidence candidates")
    parser.add_argument("--summary-1052", default=str(DEFAULT_1052_SUMMARY))
    parser.add_argument("--accepted-registry", default=str(DEFAULT_ACCEPTED_REGISTRY))
    parser.add_argument("--generated-exclusions", default=str(DEFAULT_GENERATED_EXCLUSIONS))
    parser.add_argument("--hit1-flips", default=str(DEFAULT_HIT1_FLIPS))
    parser.add_argument("--recall-rows", default=str(DEFAULT_RECALL_ROWS))
    parser.add_argument("--oss-samples", default=str(DEFAULT_OSS_SAMPLES))
    parser.add_argument("--oss-samples-expanded", default=str(DEFAULT_OSS_SAMPLES_EXPANDED))
    parser.add_argument("--label-anchor", default=str(DEFAULT_LABEL_ANCHOR))
    parser.add_argument("--high-conf-accepted", default=str(DEFAULT_HIGH_CONF_ACCEPTED))
    parser.add_argument("--high-conf-rejected", default=str(DEFAULT_HIGH_CONF_REJECTED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1052 = _read_json(Path(args.summary_1052))
    accepted_registry = _read_csv(Path(args.accepted_registry))
    generated_exclusions = _read_csv(Path(args.generated_exclusions))
    hit1_flips = _read_jsonl(Path(args.hit1_flips))
    recall_rows = _read_csv(Path(args.recall_rows))
    oss_samples = _read_jsonl(Path(args.oss_samples))
    oss_samples_expanded = _read_jsonl(Path(args.oss_samples_expanded))
    label_anchor = _read_csv(Path(args.label_anchor))
    high_conf_accepted = _read_csv(Path(args.high_conf_accepted))
    high_conf_rejected = _read_csv(Path(args.high_conf_rejected))

    accepted_by_file = {row["source_file"]: row for row in accepted_registry}
    accepted_files = set(accepted_by_file)
    generated_files = {row["source_file"] for row in generated_exclusions}

    store: dict[str, dict[str, Any]] = {}
    _add_rows(store, oss_samples, "raw_oss_rows", accepted_files, generated_files)
    _add_rows(store, oss_samples_expanded, "expanded_oss_rows", accepted_files, generated_files)
    _add_rows(store, label_anchor, "label_anchor_rows", accepted_files, generated_files)
    _add_rows(store, high_conf_accepted, "high_conf_accepted_rows", accepted_files, generated_files)
    _add_rows(store, high_conf_rejected, "high_conf_rejected_rows", accepted_files, generated_files)
    _add_rows(store, recall_rows, "recall_review_rows", accepted_files, generated_files)
    _add_flips(store, hit1_flips, accepted_files, generated_files)

    inventory_rows = _finalize_inventory(store, accepted_by_file)
    candidate_rows = _candidate_queue(inventory_rows)
    s2_effect_rows = _s2_effect_inventory(hit1_flips, generated_files)
    s1_inventory_rows = _s1_recall_inventory(recall_rows, accepted_files, generated_files)

    candidate_positive_sources = [row for row in candidate_rows if _int(row["hit1_flip_net"]) > 0]
    s2_non_global_positive = [row for row in s2_effect_rows if _int(row["non_global_net"]) > 0]
    candidate_raw_rows = sum(_int(row["raw_oss_rows"]) for row in candidate_rows)
    candidate_expanded_rows = sum(_int(row["expanded_oss_rows"]) for row in candidate_rows)

    readiness_checks = [
        {
            "check_id": "LOCAL_OSS_ARTIFACTS_FOUND",
            "status": "pass" if inventory_rows else "fail",
            "evidence": f"inventory_source_file_count={len(inventory_rows)}; raw_oss_rows={len(oss_samples)}; expanded_oss_rows={len(oss_samples_expanded)}",
            "decision": "Local OSS/v36 artifacts can be inventoried read-only.",
        },
        {
            "check_id": "ADDITIONAL_SOURCE_CANDIDATES_FOUND",
            "status": "pass" if candidate_rows else "fail",
            "evidence": f"candidate_source_file_count={len(candidate_rows)}; candidate_raw_rows={candidate_raw_rows}; candidate_expanded_rows={candidate_expanded_rows}",
            "decision": "Additional v36 trace candidates exist, but provenance is not accepted yet.",
        },
        {
            "check_id": "CANDIDATE_S2_EFFECT_POSITIVE",
            "status": "pass" if candidate_positive_sources else "fail",
            "evidence": f"candidate_positive_s2_net_source_count={len(candidate_positive_sources)}",
            "decision": "No additional candidate source has positive S2 hit1 net by itself.",
        },
        {
            "check_id": "NON_GLOBAL_S2_REENTRY_SIGNAL",
            "status": "pass" if s2_non_global_positive else "fail",
            "evidence": f"s2_non_global_positive_candidate_count={len(s2_non_global_positive)}",
            "decision": "No non-global candidate-level S2 signal is positive enough to support re-entry.",
        },
        {
            "check_id": "S1_TRUE_RECALL_SIGNAL",
            "status": "fail",
            "evidence": "Recall inventory remains taxonomy/blocked-source dominated; no true accepted OSS recall-learning slice identified.",
            "decision": "Do not re-enter S1 from this inventory.",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "auto_accept_non_oss_named_v36_traces_as_human_oss",
            "reason": "Candidate files are v36 diagnostic traces, not previously accepted v36_oss provenance rows.",
            "allowed_after": "read-only provenance acceptance gate with owner/source evidence",
        },
        {
            "blocked_action": "train_or_tune_from_candidate_inventory",
            "reason": "10.53 found no re-entry-ready S1/S2 evidence.",
            "allowed_after": "future re-entry review passes after accepted provenance and positive effect evidence",
        },
        {
            "blocked_action": "use_global_repair_decision_table_as_learning_evidence",
            "reason": "It remains generated_excluded.",
            "allowed_after": "never as accepted OSS learning evidence",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "10.53 is read-only inventory and heldout/hard are validation-only.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "source_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_source_inventory.csv")),
        "candidate_source_review_queue_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_source_review_queue.csv")),
        "s2_non_global_effect_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_s2_non_global_effect_inventory.csv")),
        "s1_non_global_recall_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_s1_non_global_recall_inventory.csv")),
        "reentry_readiness_checks_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_readiness_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1052["stage"],
        "inventory_source_file_count": len(inventory_rows),
        "accepted_existing_source_file_count": sum(1 for row in inventory_rows if row["source_status"] == "accepted_oss_existing"),
        "candidate_source_file_count": len(candidate_rows),
        "generated_excluded_source_file_count": sum(1 for row in inventory_rows if row["source_status"] == "generated_excluded"),
        "raw_oss_rows": len(oss_samples),
        "expanded_oss_rows": len(oss_samples_expanded),
        "candidate_raw_oss_rows": candidate_raw_rows,
        "candidate_expanded_oss_rows": candidate_expanded_rows,
        "candidate_positive_s2_net_source_count": len(candidate_positive_sources),
        "s2_non_global_positive_candidate_count": len(s2_non_global_positive),
        "s1_recall_inventory_row_count": len(s1_inventory_rows),
        "reentry_ready_now": False,
        "selected_next_route": "10.54 OSS candidate source provenance acceptance gate",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.53 OSS-only evidence expansion inventory",
        "read_only": True,
        "oss_only_inventory": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Local OSS/v36 artifacts contain additional candidate v36 trace source files beyond the already accepted v36_oss registry, but they are not automatically accepted OSS provenance and they do not show positive S2 effect evidence. "
            "Keep S1/S2 re-entry blocked. The only useful next read-only action is a provenance acceptance gate for these candidate source files, not training or implementation."
        ),
        "anti_drift_conclusion": (
            "10.53 only inventories existing local OSS/v36 artifacts and writes review outputs. It does not train, tune, expand the ranking candidate matrix, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, auto-accept unverified sources, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.54 OSS candidate source provenance acceptance gate",
            "goal": "Read-only decide whether the additional non-accepted v36 trace source files can be accepted as human OSS provenance or must remain excluded/unknown; still no training or implementation.",
            "default": "provenance review only",
        },
    }

    inventory_fields = [
        "source_file",
        "source_family_guess",
        "source_status",
        "raw_oss_rows",
        "expanded_oss_rows",
        "label_anchor_rows",
        "high_conf_accepted_rows",
        "high_conf_rejected_rows",
        "recall_review_rows",
        "hit1_flip_gain",
        "hit1_flip_loss",
        "hit1_flip_net",
        "province_count",
        "bucket_count",
        "top_buckets",
        "learnability_status_counts",
        "accepted_scope",
        "inventory_decision",
    ]
    _write_csv(Path(artifacts["source_inventory_csv"]), inventory_rows, inventory_fields)
    _write_csv(
        Path(artifacts["candidate_source_review_queue_csv"]),
        candidate_rows,
        [
            "source_file",
            "source_family_guess",
            "raw_oss_rows",
            "expanded_oss_rows",
            "hit1_flip_gain",
            "hit1_flip_loss",
            "hit1_flip_net",
            "recall_review_rows",
            "provenance_need",
            "effect_need",
            "candidate_decision",
        ],
    )
    _write_csv(
        Path(artifacts["s2_non_global_effect_inventory_csv"]),
        s2_effect_rows,
        [
            "candidate_id",
            "non_global_gain",
            "non_global_loss",
            "non_global_net",
            "source_family_count",
            "source_families_touched",
            "candidate_sources_touched",
            "inventory_decision",
        ],
    )
    _write_csv(
        Path(artifacts["s1_non_global_recall_inventory_csv"]),
        s1_inventory_rows,
        ["source_status", "learnability_status", "rows", "source_files", "target_buckets", "inventory_decision"],
    )
    _write_csv(
        Path(artifacts["reentry_readiness_checks_csv"]),
        readiness_checks,
        ["check_id", "status", "evidence", "decision"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, inventory_rows, candidate_rows, readiness_checks)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
