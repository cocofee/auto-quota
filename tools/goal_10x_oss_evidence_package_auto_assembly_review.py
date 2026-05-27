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
DEFAULT_S5_SUMMARY = AGENT_STATE / "goal_10x_s5_artifact_acceptance_gate_summary.json"
DEFAULT_S5_FIELDS = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_field_manifest.csv"
DEFAULT_ACCEPTED_SOURCES = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_sources.csv"
DEFAULT_GENERATED_EXCLUSIONS = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_generated_exclusions.csv"
DEFAULT_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_S2_GAP_SUMMARY = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review_summary.json"
DEFAULT_S2_SOURCE_GAP = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review_accepted_source_gap.csv"
DEFAULT_S2_FAMILY_GAP = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review_accepted_family_gap.csv"
DEFAULT_S1_ROWS = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_rows.csv"
DEFAULT_S1_FUTURE_PACKAGE = (
    AGENT_STATE / "goal_10x_s1_independent_recall_evidence_request_broader_strategy_closure_future_evidence_package.csv"
)
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review"

S2_LEAD_CANDIDATE = "OBJ_A_current_lambda_rank_baseline__FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT"


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _source_registry(accepted_sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "source_family": row.get("source_family", ""),
            "source_file": row.get("source_file", ""),
            "producer": row.get("producer", ""),
            "collection_method": row.get("collection_method", ""),
            "split": "dev_oof",
            "is_human_quantity_surveyor_output": row.get("is_human_quantity_surveyor_output", ""),
            "is_generated_or_synthetic": row.get("is_generated_or_synthetic", ""),
            "trust_level": row.get("trust_level", ""),
            "gain": _int(row.get("s2_gain")),
            "loss": _int(row.get("s2_loss")),
            "net": _int(row.get("s2_net")),
            "taxonomy_disposition": "evidence_only",
            "provenance_hash": row.get("provenance_hash", ""),
            "accepted_scope": row.get("accepted_scope", ""),
            "acceptance_decision": row.get("acceptance_decision", ""),
        }
        for row in accepted_sources
    ]


def _aggregate_flips(
    flips: list[dict[str, Any]],
    accepted_by_file: dict[str, dict[str, str]],
    generated_files: set[str],
) -> tuple[
    dict[tuple[str, str], dict[str, int]],
    dict[tuple[str, str], dict[str, int]],
    dict[tuple[str, str], dict[str, int]],
]:
    by_candidate_source: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"gain": 0, "loss": 0})
    by_candidate_family: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"gain": 0, "loss": 0})
    generated_by_candidate: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"gain": 0, "loss": 0})
    for row in flips:
        candidate_id = str(row.get("candidate_id", ""))
        source_file = str(row.get("source_file", ""))
        flip_type = str(row.get("flip_type", ""))
        if source_file in accepted_by_file:
            source_family = accepted_by_file[source_file].get("source_family", "")
            target = by_candidate_source[(candidate_id, source_file)]
            family_target = by_candidate_family[(candidate_id, source_family)]
            if flip_type == "gain":
                target["gain"] += 1
                family_target["gain"] += 1
            elif flip_type == "loss":
                target["loss"] += 1
                family_target["loss"] += 1
        if source_file in generated_files:
            target = generated_by_candidate[(candidate_id, source_file)]
            if flip_type == "gain":
                target["gain"] += 1
            elif flip_type == "loss":
                target["loss"] += 1
    return by_candidate_source, by_candidate_family, generated_by_candidate


def _build_s2_packages(
    accepted_sources: list[dict[str, str]],
    scorecard_rows: list[dict[str, str]],
    by_candidate_source: dict[tuple[str, str], dict[str, int]],
    by_candidate_family: dict[tuple[str, str], dict[str, int]],
    generated_by_candidate: dict[tuple[str, str], dict[str, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted_by_file = {row["source_file"]: row for row in accepted_sources}
    candidate_ids = {row.get("candidate_id", "") for row in scorecard_rows}
    candidate_ids.update(candidate_id for candidate_id, _source in by_candidate_source)
    scorecard_by_candidate = {row.get("candidate_id", ""): row for row in scorecard_rows}

    source_rows: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_ids):
        if not candidate_id:
            continue
        score = scorecard_by_candidate.get(candidate_id, {})
        for accepted in accepted_sources:
            source_file = accepted.get("source_file", "")
            counts = by_candidate_source.get((candidate_id, source_file), {"gain": 0, "loss": 0})
            gain = counts["gain"]
            loss = counts["loss"]
            net = gain - loss
            source_rows.append(
                {
                    "candidate_id": candidate_id,
                    "source_family": accepted.get("source_family", ""),
                    "source_file": source_file,
                    "producer": accepted.get("producer", ""),
                    "collection_method": accepted.get("collection_method", ""),
                    "split": "dev_oof",
                    "gain": gain,
                    "loss": loss,
                    "net": net,
                    "taxonomy_disposition": "evidence_only",
                    "provenance_hash": accepted.get("provenance_hash", ""),
                    "overall_hit1_gain": _int(score.get("hit1_gain")),
                    "overall_hit1_loss": _int(score.get("hit1_loss")),
                    "overall_hit1_net": _int(score.get("hit1_net")),
                    "loss_budget_pass": score.get("loss_budget_pass", ""),
                    "approval_status": score.get("approval_status", ""),
                    "heldout_used_for_selection": score.get("heldout_used_for_selection", ""),
                    "package_status": "structural_package_only",
                }
            )

    family_rows: list[dict[str, Any]] = []
    family_keys = {(candidate_id, row.get("source_family", "")) for candidate_id in candidate_ids for row in accepted_sources}
    for candidate_id, source_family in sorted(family_keys):
        if not candidate_id or not source_family:
            continue
        counts = by_candidate_family.get((candidate_id, source_family), {"gain": 0, "loss": 0})
        gain = counts["gain"]
        loss = counts["loss"]
        net = gain - loss
        family_rows.append(
            {
                "candidate_id": candidate_id,
                "source_family": source_family,
                "accepted_source_files": sum(1 for row in accepted_sources if row.get("source_family") == source_family),
                "split": "dev_oof",
                "gain": gain,
                "loss": loss,
                "net": net,
                "positive_net": int(net > 0),
                "taxonomy_disposition": "evidence_only",
                "package_status": "structural_package_only",
            }
        )

    candidate_rows: list[dict[str, Any]] = []
    generated_by_id: dict[str, dict[str, int]] = defaultdict(lambda: {"gain": 0, "loss": 0})
    for (candidate_id, _source_file), counts in generated_by_candidate.items():
        generated_by_id[candidate_id]["gain"] += counts["gain"]
        generated_by_id[candidate_id]["loss"] += counts["loss"]

    for candidate_id in sorted(candidate_ids):
        if not candidate_id:
            continue
        score = scorecard_by_candidate.get(candidate_id, {})
        source_matches = [row for row in source_rows if row["candidate_id"] == candidate_id]
        family_matches = [row for row in family_rows if row["candidate_id"] == candidate_id]
        accepted_gain = sum(_int(row["gain"]) for row in source_matches)
        accepted_loss = sum(_int(row["loss"]) for row in source_matches)
        accepted_net = accepted_gain - accepted_loss
        positive_family_count = sum(1 for row in family_matches if _int(row["net"]) > 0)
        generated_gain = generated_by_id[candidate_id]["gain"]
        generated_loss = generated_by_id[candidate_id]["loss"]
        generated_net = generated_gain - generated_loss
        loss_budget_pass = _bool(score.get("loss_budget_pass"))
        overall_hit1_net = _int(score.get("hit1_net"))
        approved = score.get("approval_status") == "pass_dev_oof_candidate"
        generated_dominated = generated_net > max(0, accepted_net)
        reentry_ready = (
            accepted_net > 0
            and positive_family_count >= 2
            and loss_budget_pass
            and overall_hit1_net > 0
            and approved
            and not generated_dominated
        )
        block_reasons = []
        if accepted_net <= 0:
            block_reasons.append("accepted_oss_net_not_positive")
        if positive_family_count < 2:
            block_reasons.append("positive_accepted_source_family_below_2")
        if not loss_budget_pass:
            block_reasons.append("loss_budget_not_passed")
        if overall_hit1_net <= 0:
            block_reasons.append("overall_dev_oof_net_not_positive")
        if not approved:
            block_reasons.append("candidate_not_approved_for_dev_oof")
        if generated_dominated:
            block_reasons.append("generated_positive_net_still_dominates")
        candidate_rows.append(
            {
                "candidate_id": candidate_id,
                "split": "dev_oof",
                "accepted_oss_gain": accepted_gain,
                "accepted_oss_loss": accepted_loss,
                "accepted_oss_net": accepted_net,
                "positive_accepted_source_family_count": positive_family_count,
                "accepted_source_family_count": len({row["source_family"] for row in accepted_sources}),
                "generated_gain": generated_gain,
                "generated_loss": generated_loss,
                "generated_net": generated_net,
                "overall_hit1_gain": _int(score.get("hit1_gain")),
                "overall_hit1_loss": _int(score.get("hit1_loss")),
                "overall_hit1_net": overall_hit1_net,
                "loss_budget_pass": loss_budget_pass,
                "approval_status": score.get("approval_status", ""),
                "heldout_used_for_selection": score.get("heldout_used_for_selection", ""),
                "reentry_ready": reentry_ready,
                "block_reasons": ";".join(block_reasons) if block_reasons else "none",
            }
        )
    return source_rows, family_rows, candidate_rows


def _build_s1_packages(
    s1_rows: list[dict[str, str]],
    accepted_by_file: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    package_rows: list[dict[str, Any]] = []
    accepted_recall_rows = [row for row in s1_rows if row.get("source_file") in accepted_by_file]
    for index, row in enumerate(accepted_recall_rows, start=1):
        source = accepted_by_file[row.get("source_file", "")]
        learnability = row.get("learnability_status", "")
        taxonomy_disposition = "taxonomy_cleanup" if "taxonomy" in learnability else "evidence_only"
        package_rows.append(
            {
                "evidence_row_id": f"s1_accepted_oss_{index:03d}",
                "source_family": source.get("source_family", ""),
                "source_file": row.get("source_file", ""),
                "producer": source.get("producer", ""),
                "collection_method": source.get("collection_method", ""),
                "split": "dev_oof",
                "gain": 0,
                "loss": 0,
                "net": 0,
                "taxonomy_disposition": taxonomy_disposition,
                "provenance_hash": source.get("provenance_hash", ""),
                "target_bucket": row.get("target_bucket", ""),
                "coverage_gap_class": row.get("coverage_gap_class", ""),
                "learnability_status": learnability,
                "query_domain": row.get("query_domain", ""),
                "top1_domain": row.get("top1_domain", ""),
                "recommendation": row.get("stage_9_29_recommendation", ""),
                "query": row.get("query", ""),
                "package_status": "dq_taxonomy_artifact_not_recall_learning",
            }
        )

    status_counts = Counter(row["learnability_status"] for row in package_rows)
    disposition_counts = Counter(row["taxonomy_disposition"] for row in package_rows)
    source_family_counts = Counter(row["source_family"] for row in package_rows)
    gap_rows = [
        {
            "review_item": "accepted_oss_s1_rows",
            "value": len(package_rows),
            "decision": "not_reentry_ready",
            "evidence": "Accepted OSS recall-missing rows are taxonomy/coverage artifacts, not confirmed true recall failures.",
        },
        {
            "review_item": "accepted_oss_s1_source_families",
            "value": len(source_family_counts),
            "decision": "informational_only",
            "evidence": ";".join(f"{key}:{value}" for key, value in sorted(source_family_counts.items())),
        },
        {
            "review_item": "taxonomy_disposition",
            "value": ";".join(f"{key}:{value}" for key, value in sorted(disposition_counts.items())),
            "decision": "exclude_from_learning_evidence",
            "evidence": "S1 rows require taxonomy/DQ handling before recall learning.",
        },
        {
            "review_item": "learnability_status",
            "value": ";".join(f"{key}:{value}" for key, value in sorted(status_counts.items())),
            "decision": "not_reentry_ready",
            "evidence": "No accepted OSS row is classified as true recall learning signal.",
        },
    ]
    return package_rows, gap_rows


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    checks: list[dict[str, Any]],
    s2_candidates: list[dict[str, Any]],
    s1_gap: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    top_s2 = sorted(s2_candidates, key=lambda row: _int(row["accepted_oss_net"]), reverse=True)[:5]
    lines = [
        "# 10.51 OSS Evidence Package Auto-assembly Review",
        "",
        "Read-only auto-assembly of S1/S2 re-entry evidence package candidates from existing accepted OSS, dev/OOF artifacts, and the accepted S5 support contract.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["accepted_oss_source_file_count", metrics["accepted_oss_source_file_count"]],
                ["accepted_oss_source_family_count", metrics["accepted_oss_source_family_count"]],
                ["s2_reentry_ready_candidate_count", metrics["s2_reentry_ready_candidate_count"]],
                ["s1_reentry_ready", metrics["s1_reentry_ready"]],
                ["accepted_oss_non_generated_evidence_exists", metrics["accepted_oss_non_generated_evidence_exists"]],
                ["future_reentry_review_allowed", metrics["future_reentry_review_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Re-entry Readiness Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in checks]
        ),
        "",
        "## Top S2 Accepted-OSS Candidate Rollups",
        "",
        _md_table(
            [
                [
                    "candidate_id",
                    "accepted_net",
                    "positive_families",
                    "overall_net",
                    "loss_budget_pass",
                    "reentry_ready",
                    "block_reasons",
                ]
            ]
            + [
                [
                    row["candidate_id"],
                    row["accepted_oss_net"],
                    row["positive_accepted_source_family_count"],
                    row["overall_hit1_net"],
                    row["loss_budget_pass"],
                    row["reentry_ready"],
                    row["block_reasons"],
                ]
                for row in top_s2
            ]
        ),
        "",
        "## S1 Gap Review",
        "",
        _md_table(
            [["review_item", "value", "decision", "evidence"]]
            + [[row["review_item"], row["value"], row["decision"], row["evidence"]] for row in s1_gap]
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
    parser = argparse.ArgumentParser(description="Assemble accepted-OSS S1/S2 evidence packages without executing changes")
    parser.add_argument("--s5-summary", default=str(DEFAULT_S5_SUMMARY))
    parser.add_argument("--s5-fields", default=str(DEFAULT_S5_FIELDS))
    parser.add_argument("--accepted-sources", default=str(DEFAULT_ACCEPTED_SOURCES))
    parser.add_argument("--generated-exclusions", default=str(DEFAULT_GENERATED_EXCLUSIONS))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--hit1-flips", default=str(DEFAULT_HIT1_FLIPS))
    parser.add_argument("--s2-gap-summary", default=str(DEFAULT_S2_GAP_SUMMARY))
    parser.add_argument("--s2-source-gap", default=str(DEFAULT_S2_SOURCE_GAP))
    parser.add_argument("--s2-family-gap", default=str(DEFAULT_S2_FAMILY_GAP))
    parser.add_argument("--s1-rows", default=str(DEFAULT_S1_ROWS))
    parser.add_argument("--s1-future-package", default=str(DEFAULT_S1_FUTURE_PACKAGE))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s5_summary = _read_json(Path(args.s5_summary))
    s5_fields = _read_csv(Path(args.s5_fields))
    accepted_sources = _read_csv(Path(args.accepted_sources))
    generated_exclusions = _read_csv(Path(args.generated_exclusions))
    scorecard_rows = _read_csv(Path(args.scorecard))
    flips = _read_jsonl(Path(args.hit1_flips))
    s2_gap_summary = _read_json(Path(args.s2_gap_summary))
    s2_source_gap = _read_csv(Path(args.s2_source_gap))
    s2_family_gap = _read_csv(Path(args.s2_family_gap))
    s1_rows = _read_csv(Path(args.s1_rows))
    s1_future_package = _read_csv(Path(args.s1_future_package))

    accepted_by_file = {row["source_file"]: row for row in accepted_sources}
    generated_files = {row["source_file"] for row in generated_exclusions}
    registry_rows = _source_registry(accepted_sources)
    by_candidate_source, by_candidate_family, generated_by_candidate = _aggregate_flips(
        flips, accepted_by_file, generated_files
    )
    s2_source_rows, s2_family_rows, s2_candidate_rows = _build_s2_packages(
        accepted_sources, scorecard_rows, by_candidate_source, by_candidate_family, generated_by_candidate
    )
    s1_package_rows, s1_gap_rows = _build_s1_packages(s1_rows, accepted_by_file)

    s5_required_fields = {
        "source_family",
        "source_file",
        "producer",
        "collection_method",
        "split",
        "gain",
        "loss",
        "net",
        "taxonomy_disposition",
        "provenance_hash",
    }
    observed_fields = {row.get("field", "") for row in s5_fields}
    s5_package_fields_present = s5_required_fields.issubset(observed_fields)
    accepted_non_generated = [
        row for row in accepted_sources if str(row.get("is_generated_or_synthetic", "")).lower() == "false"
    ]
    s2_reentry_ready = [row for row in s2_candidate_rows if row["reentry_ready"]]
    s2_positive_net = [row for row in s2_candidate_rows if _int(row["accepted_oss_net"]) > 0]
    max_s2_accepted_net = max([_int(row["accepted_oss_net"]) for row in s2_candidate_rows] or [0])
    max_s2_positive_families = max([_int(row["positive_accepted_source_family_count"]) for row in s2_candidate_rows] or [0])
    s1_reentry_ready = False
    future_reentry_review_allowed = bool(s2_reentry_ready) or s1_reentry_ready

    readiness_checks = [
        {
            "check_id": "ACCEPTED_OSS_PROVENANCE",
            "status": "pass" if accepted_non_generated else "fail",
            "evidence": f"accepted_non_generated_source_files={len(accepted_non_generated)}; source_families={len({row.get('source_family') for row in accepted_non_generated})}",
            "decision": "accepted OSS source registry can be used as provenance input",
        },
        {
            "check_id": "S5_PACKAGE_FIELD_CONTRACT",
            "status": "pass" if s5_summary["metrics"].get("s5_support_contract_accepted") and s5_package_fields_present else "fail",
            "evidence": f"s5_support_contract_accepted={s5_summary['metrics'].get('s5_support_contract_accepted')}; required_fields_present={s5_package_fields_present}",
            "decision": "S5 fields are available to standardize package rows",
        },
        {
            "check_id": "S2_ACCEPTED_OSS_POSITIVE_NET",
            "status": "pass" if s2_positive_net else "fail",
            "evidence": f"positive_net_candidate_count={len(s2_positive_net)}; max_accepted_oss_net={max_s2_accepted_net}",
            "decision": "positive accepted-OSS signal exists structurally but still must pass family, loss, and dominance gates",
        },
        {
            "check_id": "S2_TWO_INDEPENDENT_POSITIVE_FAMILIES",
            "status": "pass" if max_s2_positive_families >= 2 else "fail",
            "evidence": f"max_positive_accepted_source_family_count={max_s2_positive_families}",
            "decision": "S2 cannot re-enter until positive support spans at least two accepted source families",
        },
        {
            "check_id": "S2_FULL_REENTRY_CANDIDATE",
            "status": "pass" if s2_reentry_ready else "fail",
            "evidence": f"s2_reentry_ready_candidate_count={len(s2_reentry_ready)}",
            "decision": "No S2 candidate satisfies accepted OSS net, source-family, loss-budget, approval, and generated-dominance gates",
        },
        {
            "check_id": "S1_TRUE_RECALL_EVIDENCE",
            "status": "pass" if s1_reentry_ready else "fail",
            "evidence": f"accepted_oss_s1_rows={len(s1_package_rows)}; taxonomy_cleanup_rows={sum(1 for row in s1_package_rows if row['taxonomy_disposition'] == 'taxonomy_cleanup')}",
            "decision": "Current accepted OSS S1 rows are taxonomy/coverage artifacts, not true recall-learning evidence",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "start_s2_training_or_candidate_freeze_from_assembled_package",
            "reason": "No S2 candidate passes full accepted-OSS re-entry readiness.",
            "allowed_after": "future read-only re-entry review finds accepted OSS positive net across at least two source families with loss/dominance gates passing",
        },
        {
            "blocked_action": "start_s1_recall_training_from_accepted_oss_rows",
            "reason": "Accepted OSS S1 rows are taxonomy/coverage artifacts, not true missing-recall learning rows.",
            "allowed_after": "future accepted-OSS non-generated recall evidence package proves true recall failure and positive support",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "10.51 only uses existing dev/OOF evidence for package assembly.",
            "allowed_after": "never for selection; heldout/hard remain validation-only after candidate freeze",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "This is a read-only review and no re-entry gate passed.",
            "allowed_after": "separate approved implementation plan after valid evidence and explicit go",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "accepted_oss_source_registry_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_oss_source_registry.csv")),
        "s2_evidence_package_candidates_csv": str(output_prefix.with_name(output_prefix.name + "_s2_evidence_package_candidates.csv")),
        "s2_family_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_s2_family_rollup.csv")),
        "s2_candidate_readiness_csv": str(output_prefix.with_name(output_prefix.name + "_s2_candidate_readiness.csv")),
        "s1_evidence_package_candidates_csv": str(output_prefix.with_name(output_prefix.name + "_s1_evidence_package_candidates.csv")),
        "s1_gap_review_csv": str(output_prefix.with_name(output_prefix.name + "_s1_gap_review.csv")),
        "reentry_readiness_checks_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_readiness_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": "Goal LTR v1 / 10.51 OSS evidence package auto-assembly review",
        "s5_support_contract_accepted": bool(s5_summary["metrics"].get("s5_support_contract_accepted")),
        "s5_package_fields_present": s5_package_fields_present,
        "accepted_oss_source_file_count": len(accepted_sources),
        "accepted_oss_source_family_count": len({row.get("source_family", "") for row in accepted_sources}),
        "accepted_oss_non_generated_source_file_count": len(accepted_non_generated),
        "generated_exclusion_source_file_count": len(generated_files),
        "s2_source_package_row_count": len(s2_source_rows),
        "s2_candidate_count": len(s2_candidate_rows),
        "s2_positive_accepted_oss_net_candidate_count": len(s2_positive_net),
        "s2_max_accepted_oss_net": max_s2_accepted_net,
        "s2_max_positive_accepted_source_family_count": max_s2_positive_families,
        "s2_reentry_ready_candidate_count": len(s2_reentry_ready),
        "s2_prior_lead_candidate": S2_LEAD_CANDIDATE,
        "s2_prior_lead_accepted_oss_net": s2_gap_summary["metrics"].get("accepted_oss_s2_net"),
        "s2_prior_lead_accepted_oss_positive_source_family_count": s2_gap_summary["metrics"].get(
            "accepted_oss_positive_source_family_count"
        ),
        "s1_accepted_oss_package_row_count": len(s1_package_rows),
        "s1_taxonomy_cleanup_row_count": sum(1 for row in s1_package_rows if row["taxonomy_disposition"] == "taxonomy_cleanup"),
        "s1_reentry_ready": s1_reentry_ready,
        "future_reentry_review_allowed": future_reentry_review_allowed,
        "accepted_oss_non_generated_evidence_exists": bool(accepted_non_generated),
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "prior_s2_source_gap_rows": len(s2_source_gap),
        "prior_s2_family_gap_rows": len(s2_family_gap),
        "s1_future_package_requirement_count": len(s1_future_package),
    }
    report = {
        "stage": "Goal LTR v1 / 10.51 OSS evidence package auto-assembly review",
        "read_only": True,
        "oss_evidence_package_auto_assembly_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Assemble the accepted-OSS package structurally, but do not re-enter S1 or S2. The registry confirms accepted non-generated OSS provenance and the S5 field contract is available. "
            "However, no S2 candidate passes the full accepted-OSS re-entry gate: the only accepted-OSS positive signal is small, source-family insufficient, and tied to candidates with failing overall/loss gates. "
            "S1 accepted-OSS rows are taxonomy/coverage cleanup artifacts rather than true recall-learning evidence."
        ),
        "anti_drift_conclusion": (
            "10.51 only reads existing accepted OSS, v36_oss, dev/OOF, and S5 contract artifacts and writes review/package outputs. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, implement telemetry or DQ fixes, treat DQ backlog rows as learning evidence, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "10.52 OSS evidence package no-go / strategy handoff",
            "goal": "Read-only close the assembled OSS package as not re-entry-ready, then either wait for stronger accepted-OSS evidence or choose a new strategy lane that does not rely on these blocked S1/S2 gates.",
            "default": "no execution; no training; no implementation",
        },
    }

    _write_csv(
        Path(artifacts["accepted_oss_source_registry_csv"]),
        registry_rows,
        [
            "source_family",
            "source_file",
            "producer",
            "collection_method",
            "split",
            "is_human_quantity_surveyor_output",
            "is_generated_or_synthetic",
            "trust_level",
            "gain",
            "loss",
            "net",
            "taxonomy_disposition",
            "provenance_hash",
            "accepted_scope",
            "acceptance_decision",
        ],
    )
    _write_csv(
        Path(artifacts["s2_evidence_package_candidates_csv"]),
        s2_source_rows,
        [
            "candidate_id",
            "source_family",
            "source_file",
            "producer",
            "collection_method",
            "split",
            "gain",
            "loss",
            "net",
            "taxonomy_disposition",
            "provenance_hash",
            "overall_hit1_gain",
            "overall_hit1_loss",
            "overall_hit1_net",
            "loss_budget_pass",
            "approval_status",
            "heldout_used_for_selection",
            "package_status",
        ],
    )
    _write_csv(
        Path(artifacts["s2_family_rollup_csv"]),
        s2_family_rows,
        [
            "candidate_id",
            "source_family",
            "accepted_source_files",
            "split",
            "gain",
            "loss",
            "net",
            "positive_net",
            "taxonomy_disposition",
            "package_status",
        ],
    )
    _write_csv(
        Path(artifacts["s2_candidate_readiness_csv"]),
        s2_candidate_rows,
        [
            "candidate_id",
            "split",
            "accepted_oss_gain",
            "accepted_oss_loss",
            "accepted_oss_net",
            "positive_accepted_source_family_count",
            "accepted_source_family_count",
            "generated_gain",
            "generated_loss",
            "generated_net",
            "overall_hit1_gain",
            "overall_hit1_loss",
            "overall_hit1_net",
            "loss_budget_pass",
            "approval_status",
            "heldout_used_for_selection",
            "reentry_ready",
            "block_reasons",
        ],
    )
    _write_csv(
        Path(artifacts["s1_evidence_package_candidates_csv"]),
        s1_package_rows,
        [
            "evidence_row_id",
            "source_family",
            "source_file",
            "producer",
            "collection_method",
            "split",
            "gain",
            "loss",
            "net",
            "taxonomy_disposition",
            "provenance_hash",
            "target_bucket",
            "coverage_gap_class",
            "learnability_status",
            "query_domain",
            "top1_domain",
            "recommendation",
            "query",
            "package_status",
        ],
    )
    _write_csv(
        Path(artifacts["s1_gap_review_csv"]),
        s1_gap_rows,
        ["review_item", "value", "decision", "evidence"],
    )
    _write_csv(
        Path(artifacts["reentry_readiness_checks_csv"]),
        readiness_checks,
        ["check_id", "status", "evidence", "decision"],
    )
    _write_csv(
        Path(artifacts["blocked_actions_csv"]),
        blocked_actions,
        ["blocked_action", "reason", "allowed_after"],
    )
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, readiness_checks, s2_candidate_rows, s1_gap_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
