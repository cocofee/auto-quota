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
DEFAULT_1053_SUMMARY = AGENT_STATE / "goal_10x_oss_only_evidence_expansion_inventory_summary.json"
DEFAULT_CANDIDATES = AGENT_STATE / "goal_10x_oss_only_evidence_expansion_inventory_candidate_source_review_queue.csv"
DEFAULT_SOURCE_INVENTORY = AGENT_STATE / "goal_10x_oss_only_evidence_expansion_inventory_source_inventory.csv"
DEFAULT_S2_EFFECT = AGENT_STATE / "goal_10x_oss_only_evidence_expansion_inventory_s2_non_global_effect_inventory.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate"


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


def _decision_for_candidate(row: dict[str, str]) -> tuple[str, str, str]:
    source_file = row["source_file"]
    has_oss_name = source_file.startswith("v36_oss")
    net = _int(row.get("hit1_flip_net"))
    if has_oss_name:
        return (
            "ACCEPTABLE_IF_NOT_ALREADY_ACCEPTED",
            "filename uses v36_oss prefix and may match existing OSS provenance convention",
            "owner assertion plus provenance hash, producer, collection method, and generated-source exclusion check",
        )
    if net > 0:
        return (
            "HOLD_FOR_OWNER_PROVENANCE_EVIDENCE",
            "source is a v36 diagnostic trace with positive effect, but not accepted as OSS provenance",
            "owner assertion that rows are human quantity-surveyor OSS outputs plus effect re-audit after acceptance",
        )
    return (
        "DO_NOT_ACCEPT_NOW",
        "source is a v36 diagnostic trace, not v36_oss, and current effect evidence is non-positive",
        "explicit owner/source provenance package; even then S1/S2 effect gate must be re-run before re-entry",
    )


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
    decisions: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.54 OSS Candidate Source Provenance Acceptance Gate",
        "",
        "Read-only acceptance gate for the four additional v36 trace source files found in 10.53.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_source_file_count", metrics["candidate_source_file_count"]],
                ["accepted_now_count", metrics["accepted_now_count"]],
                ["do_not_accept_now_count", metrics["do_not_accept_now_count"]],
                ["owner_provenance_required_count", metrics["owner_provenance_required_count"]],
                ["effect_gate_pass_count", metrics["effect_gate_pass_count"]],
                ["reentry_ready_now", metrics["reentry_ready_now"]],
            ]
        ),
        "",
        "## Acceptance Decisions",
        "",
        _md_table(
            [["source_file", "decision", "reason", "effect_net"]]
            + [[row["source_file"], row["acceptance_decision"], row["decision_reason"], row["hit1_flip_net"]] for row in decisions]
        ),
        "",
        "## Required Provenance Package",
        "",
        _md_table(
            [["requirement", "required_content", "acceptance_check"]]
            + [[row["requirement"], row["required_content"], row["acceptance_check"]] for row in requirements]
        ),
        "",
        "## Gate Checks",
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
    parser = argparse.ArgumentParser(description="Read-only provenance acceptance gate for additional v36 source candidates")
    parser.add_argument("--summary-1053", default=str(DEFAULT_1053_SUMMARY))
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--source-inventory", default=str(DEFAULT_SOURCE_INVENTORY))
    parser.add_argument("--s2-effect", default=str(DEFAULT_S2_EFFECT))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1053 = _read_json(Path(args.summary_1053))
    candidates = _read_csv(Path(args.candidates))
    source_inventory = _read_csv(Path(args.source_inventory))
    s2_effect = _read_csv(Path(args.s2_effect))

    source_by_file = {row["source_file"]: row for row in source_inventory}
    decisions: list[dict[str, Any]] = []
    for row in candidates:
        decision, reason, needed = _decision_for_candidate(row)
        source = source_by_file.get(row["source_file"], {})
        decisions.append(
            {
                "source_file": row["source_file"],
                "source_family_guess": row["source_family_guess"],
                "raw_oss_rows": row["raw_oss_rows"],
                "expanded_oss_rows": row["expanded_oss_rows"],
                "label_anchor_rows": source.get("label_anchor_rows", "0"),
                "high_conf_accepted_rows": source.get("high_conf_accepted_rows", "0"),
                "high_conf_rejected_rows": source.get("high_conf_rejected_rows", "0"),
                "recall_review_rows": row["recall_review_rows"],
                "hit1_flip_gain": row["hit1_flip_gain"],
                "hit1_flip_loss": row["hit1_flip_loss"],
                "hit1_flip_net": row["hit1_flip_net"],
                "acceptance_decision": decision,
                "decision_reason": reason,
                "needed_to_accept": needed,
                "learning_disposition": "provenance_review_only_not_learning_reentry",
                "effect_gate_status": "fail_non_positive_net" if _int(row["hit1_flip_net"]) <= 0 else "needs_full_effect_gate",
            }
        )

    accepted_now = [row for row in decisions if row["acceptance_decision"].startswith("ACCEPT")]
    do_not_accept_now = [row for row in decisions if row["acceptance_decision"] == "DO_NOT_ACCEPT_NOW"]
    owner_required = [row for row in decisions if row["acceptance_decision"] != "ACCEPT_NOW"]
    effect_gate_pass = [row for row in decisions if _int(row["hit1_flip_net"]) > 0]
    s2_positive_candidates = [row for row in s2_effect if _int(row.get("non_global_net")) > 0]

    requirements = [
        {
            "requirement": "producer_identity",
            "required_content": "Who produced the rows: human quantity surveyor, upstream OSS export, diagnostic script, generated pipeline, or mixed.",
            "acceptance_check": "Must explicitly support human quantity-surveyor OSS provenance; diagnostic trace alone is insufficient.",
        },
        {
            "requirement": "collection_method",
            "required_content": "How rows were collected and transformed into the v36 trace source file.",
            "acceptance_check": "Must separate original OSS output from later shadow/comparator/guardrail diagnostic artifacts.",
        },
        {
            "requirement": "generated_exclusion",
            "required_content": "Statement that rows are not generated, synthetic, auto-expanded, or copied from global_repair_decision_table.csv.",
            "acceptance_check": "Generated or mixed-source rows cannot become accepted OSS learning evidence.",
        },
        {
            "requirement": "provenance_hash",
            "required_content": "Stable hash or manifest for the source file and row set under review.",
            "acceptance_check": "Future re-entry must bind evidence to the same source rows.",
        },
        {
            "requirement": "effect_reaudit_boundary",
            "required_content": "After any provenance acceptance, re-run dev/OOF gain/loss/net and loss audit for accepted sources only.",
            "acceptance_check": "Provenance acceptance alone never authorizes training, tuning, or implementation.",
        },
    ]

    gate_checks = [
        {
            "check_id": "CANDIDATES_PRESENT",
            "status": "pass" if candidates else "fail",
            "evidence": f"candidate_source_file_count={len(candidates)}",
            "decision": "Four candidate v36 trace files are available for provenance review.",
        },
        {
            "check_id": "OSS_NAMING_OR_ACCEPTED_REGISTRY",
            "status": "fail" if do_not_accept_now else "pass",
            "evidence": f"do_not_accept_now_count={len(do_not_accept_now)}; accepted_now_count={len(accepted_now)}",
            "decision": "None of the four additional files carries accepted v36_oss provenance in the current registry.",
        },
        {
            "check_id": "OWNER_PROVENANCE_PACKAGE",
            "status": "fail",
            "evidence": f"owner_provenance_required_count={len(owner_required)}",
            "decision": "Owner/source provenance package is required before any candidate can be accepted as human OSS provenance.",
        },
        {
            "check_id": "EFFECT_GATE",
            "status": "fail",
            "evidence": f"candidate_positive_source_count={len(effect_gate_pass)}; s2_positive_candidate_count={len(s2_positive_candidates)}",
            "decision": "Current candidate effect evidence is non-positive, so S1/S2 re-entry stays blocked even if provenance is later accepted.",
        },
    ]

    next_options = [
        {
            "option": "10.55 OSS provenance gap closure / pause",
            "status": "recommended",
            "rationale": "No candidate can be accepted now and effect gate remains failed; close this OSS expansion lane unless owner provenance package appears.",
        },
        {
            "option": "provide_owner_provenance_package",
            "status": "allowed_input",
            "rationale": "User/owner may provide producer, collection, generated-exclusion, hash, and row lineage for these four files.",
        },
        {
            "option": "train_or_implement",
            "status": "blocked",
            "rationale": "No provenance acceptance and no positive effect gate.",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "accept_candidate_sources_as_human_oss_now",
            "reason": "The four files are diagnostic v36 traces without accepted provenance registry rows or owner/source package.",
            "allowed_after": "producer/collection/generated-exclusion/hash package is supplied and passes a future acceptance review",
        },
        {
            "blocked_action": "reenter_s1_or_s2_from_candidate_sources",
            "reason": "Effect gate is non-positive and provenance is not accepted.",
            "allowed_after": "future provenance acceptance plus dev/OOF effect re-audit passes",
        },
        {
            "blocked_action": "train_tune_or_change_goal_searcher",
            "reason": "10.54 is read-only provenance gate and no execution authorization exists.",
            "allowed_after": "separate future execution/implementation go after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard remain validation-only and are not used in this gate.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_decisions.csv")),
        "required_provenance_package_csv": str(output_prefix.with_name(output_prefix.name + "_required_provenance_package.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1053["stage"],
        "candidate_source_file_count": len(candidates),
        "accepted_now_count": len(accepted_now),
        "do_not_accept_now_count": len(do_not_accept_now),
        "owner_provenance_required_count": len(owner_required),
        "effect_gate_pass_count": len(effect_gate_pass),
        "s2_non_global_positive_candidate_count": len(s2_positive_candidates),
        "reentry_ready_now": False,
        "selected_next_route": "10.55 OSS provenance gap closure / pause",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.54 OSS candidate source provenance acceptance gate",
        "read_only": True,
        "provenance_acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Do not accept the four additional v36 trace files as human OSS provenance now. They are diagnostic trace sources, not accepted v36_oss registry rows, and no owner/source provenance package is present. "
            "Even if a future owner package accepts them, current dev/OOF effect evidence is non-positive, so S1/S2 re-entry remains blocked."
        ),
        "anti_drift_conclusion": (
            "10.54 only reviews provenance acceptance requirements for candidate source files. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, auto-accept diagnostic traces, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.55 OSS provenance gap closure / pause",
            "goal": "Read-only close the OSS expansion lane unless an owner provenance package is supplied; keep S1/S2 blocked.",
            "default": "pause or wait for owner provenance package",
        },
    }

    _write_csv(
        Path(artifacts["acceptance_decisions_csv"]),
        decisions,
        [
            "source_file",
            "source_family_guess",
            "raw_oss_rows",
            "expanded_oss_rows",
            "label_anchor_rows",
            "high_conf_accepted_rows",
            "high_conf_rejected_rows",
            "recall_review_rows",
            "hit1_flip_gain",
            "hit1_flip_loss",
            "hit1_flip_net",
            "acceptance_decision",
            "decision_reason",
            "needed_to_accept",
            "learning_disposition",
            "effect_gate_status",
        ],
    )
    _write_csv(
        Path(artifacts["required_provenance_package_csv"]),
        requirements,
        ["requirement", "required_content", "acceptance_check"],
    )
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, decisions, requirements, gate_checks)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
