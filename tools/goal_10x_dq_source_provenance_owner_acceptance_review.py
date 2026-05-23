from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REGISTRY = AGENT_STATE / "goal_10x_dq_source_provenance_bootstrap_audit_registry.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review"


def _read_csv(path: Path) -> list[dict[str, str]]:
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


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _is_accepted_oss(row: dict[str, str]) -> bool:
    return (
        row.get("source_file", "").startswith("v36_oss_")
        and row.get("producer") == "human_quantity_surveyor_oss_asserted_by_user"
        and row.get("is_generated_or_synthetic") == "false"
    )


def _is_generated_exclusion(row: dict[str, str]) -> bool:
    return row.get("learning_disposition") == "exclude_generated_source"


def _write_markdown(path: Path, report: dict[str, Any], accepted_rows: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.27 DQ source provenance owner acceptance review",
        "",
        "Read-only owner acceptance review for 10.26 source provenance bootstrap outputs.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["accepted_human_oss_source_file_count", metrics["accepted_human_oss_source_file_count"]],
                ["accepted_source_family_count", metrics["accepted_source_family_count"]],
                ["generated_exclusion_accepted_count", metrics["generated_exclusion_accepted_count"]],
                ["pending_source_documentation_count", metrics["pending_source_documentation_count"]],
                ["s2_accepted_non_generated_positive_net", metrics["s2_accepted_non_generated_positive_net"]],
                ["source_provenance_dq_acceptance", metrics["source_provenance_dq_acceptance"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Accepted OSS Sources",
        "",
        _md_table(
            [["source_file", "source_family", "row_count_total", "s2_net", "acceptance_decision"]]
            + [
                [
                    row["source_file"],
                    row["source_family"],
                    row["row_count_total"],
                    row["s2_net"],
                    row["acceptance_decision"],
                ]
                for row in accepted_rows
            ]
        ),
        "",
        "## Remaining Blockers",
        "",
        _md_table(
            [["blocker", "status", "why_it_blocks_reentry"]]
            + [[row["blocker"], row["status"], row["why_it_blocks_reentry"]] for row in blockers]
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
    parser = argparse.ArgumentParser(description="Review owner acceptance for source provenance registry")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    registry_rows = _read_csv(Path(args.registry))
    accepted_rows: list[dict[str, Any]] = []
    non_accepted_rows: list[dict[str, Any]] = []
    generated_exclusions: list[dict[str, Any]] = []

    for row in registry_rows:
        reviewed = dict(row)
        if _is_accepted_oss(row):
            reviewed.update(
                {
                    "acceptance_decision": "ACCEPT_AS_HUMAN_QUANTITY_SURVEYOR_OSS_SOURCE",
                    "accepted_by": "user_owner_assertion_in_10_27_request",
                    "accepted_scope": "source_provenance_only_not_learning_reentry",
                    "acceptance_note": "User stated OSS rows are quantity-surveyor completed outputs; source is accepted for DQ provenance boundary only.",
                }
            )
            accepted_rows.append(reviewed)
        elif _is_generated_exclusion(row):
            reviewed.update(
                {
                    "acceptance_decision": "ACCEPT_AS_GENERATED_EXCLUSION",
                    "accepted_by": "source_gate_and_10_27_review",
                    "accepted_scope": "generated_exclusion_only",
                    "acceptance_note": "Generated repair-decision table remains excluded from learning evidence.",
                }
            )
            generated_exclusions.append(reviewed)
        else:
            reviewed.update(
                {
                    "acceptance_decision": "NOT_ACCEPTED_PENDING_SOURCE_DOCUMENTATION",
                    "accepted_by": "",
                    "accepted_scope": "none",
                    "acceptance_note": "Non-OSS diagnostic trace still needs producer and collection-method documentation.",
                }
            )
            non_accepted_rows.append(reviewed)

    accepted_family_count = len({row["source_family"] for row in accepted_rows})
    s2_accepted_net = sum(int(row.get("s2_positive_net") or 0) for row in accepted_rows)
    s2_accepted_groups = sum(int(row.get("s2_groups") or 0) for row in accepted_rows)
    blockers = [
        {
            "blocker": "S2_NON_GENERATED_POSITIVE_NET",
            "status": f"blocked; accepted OSS positive net={s2_accepted_net}",
            "why_it_blocks_reentry": "S2 still has no positive net on accepted human OSS sources.",
        },
        {
            "blocker": "S2_NON_GENERATED_SOURCE_COUNT",
            "status": f"blocked; accepted source families={accepted_family_count}, positive-net families=0",
            "why_it_blocks_reentry": "Independence requires positive support, not just accepted provenance.",
        },
        {
            "blocker": "S2_GENERATED_POSITIVE_NET_SHARE",
            "status": "blocked; generated positive net remains 78 and accepted OSS positive net remains 0",
            "why_it_blocks_reentry": "The diagnostic S2 gain remains generated-source dominated.",
        },
        {
            "blocker": "DQ_QUERY_FAMILY_EMPTY_TOP1_FAMILY_LABEL_MIXTURE",
            "status": "pending",
            "why_it_blocks_reentry": "Other DQ acceptance artifacts remain outside this source provenance review.",
        },
    ]
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "accepted_sources_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_sources.csv")),
        "generated_exclusions_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_generated_exclusions.csv")),
        "non_accepted_sources_csv": str(output_prefix.with_name(output_prefix.name + "_non_accepted_sources.csv")),
        "reentry_blockers_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_blockers.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "registry_source_file_count": len(registry_rows),
        "accepted_human_oss_source_file_count": len(accepted_rows),
        "accepted_source_family_count": accepted_family_count,
        "accepted_human_oss_row_count_total": sum(int(row.get("row_count_total") or 0) for row in accepted_rows),
        "accepted_human_oss_dev_row_count": sum(int(row.get("dev_row_count") or 0) for row in accepted_rows),
        "generated_exclusion_accepted_count": len(generated_exclusions),
        "pending_source_documentation_count": len(non_accepted_rows),
        "s2_accepted_non_generated_groups": s2_accepted_groups,
        "s2_accepted_non_generated_positive_net": s2_accepted_net,
        "s2_generated_positive_net_still_blocking": sum(int(row.get("s2_positive_net") or 0) for row in generated_exclusions),
        "source_provenance_dq_acceptance": True,
        "all_dq_acceptance_completed": False,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    blocked_actions = [
        {
            "blocked_action": "open_learning_reentry_review",
            "reason": "Source provenance is accepted only for v36_oss and generated exclusion; S2 accepted OSS positive net is still 0 and other DQ artifacts remain pending.",
            "allowed_after": "independent non-generated S2 positive evidence plus remaining DQ acceptance artifacts",
        },
        {
            "blocked_action": "claim_s2_general_top1_gain",
            "reason": "Accepted OSS rows do not contain positive S2 net; generated repair-decision table still carries all positive net.",
            "allowed_after": "future independent non-generated positive-net evidence review",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "10.27 is a read-only owner acceptance review.",
            "allowed_after": "future explicit execution stage after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No source robustness pass or frozen validation candidate exists.",
            "allowed_after": "future validation gate after learning re-entry and source robustness pass",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
    ]
    report = {
        "stage": "Goal LTR v1 / 10.27 DQ source provenance owner acceptance review",
        "read_only": True,
        "owner_acceptance_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept v36_oss_* registry rows as human quantity-surveyor OSS source provenance based on the user's owner assertion, "
            "and accept global_repair_decision_table.csv as a generated-source exclusion. This satisfies the source-provenance boundary for DQ, "
            "but it does not reopen learning because accepted OSS S2 positive net remains 0 and other DQ/S2 evidence gates remain blocked."
        ),
        "anti_drift_conclusion": (
            "10.27 accepts provenance scope only. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or claim S2 general Top1 gain."
        ),
        "next_stage": {
            "stage": "10.28 S2 accepted-OSS evidence gap review",
            "goal": "Read-only decide whether existing dev/OOF artifacts can explain why accepted OSS sources have zero S2 positive net, and whether additional independent evidence is needed.",
            "blocked_until": "S2 non-generated positive net > 0 and at least two independent positive source families are shown without generated dominance.",
        },
    }

    fields = list(accepted_rows[0].keys()) if accepted_rows else [
        "source_file", "source_family", "producer", "collection_method", "acceptance_decision",
        "accepted_by", "accepted_scope", "acceptance_note",
    ]
    generated_fields = list(generated_exclusions[0].keys()) if generated_exclusions else fields
    non_accepted_fields = list(non_accepted_rows[0].keys()) if non_accepted_rows else fields
    _write_csv(Path(artifacts["accepted_sources_csv"]), accepted_rows, fields)
    _write_csv(Path(artifacts["generated_exclusions_csv"]), generated_exclusions, generated_fields)
    _write_csv(Path(artifacts["non_accepted_sources_csv"]), non_accepted_rows, non_accepted_fields)
    _write_csv(Path(artifacts["reentry_blockers_csv"]), blockers, ["blocker", "status", "why_it_blocks_reentry"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, accepted_rows, blockers)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
