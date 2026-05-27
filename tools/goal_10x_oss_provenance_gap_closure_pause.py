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
DEFAULT_1054_SUMMARY = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate_summary.json"
DEFAULT_DECISIONS = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate_acceptance_decisions.csv"
DEFAULT_REQUIREMENTS = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate_required_provenance_package.csv"
DEFAULT_NEXT_OPTIONS = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate_next_options.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_oss_candidate_source_provenance_acceptance_gate_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_oss_provenance_gap_closure_pause"


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
    closure: list[dict[str, Any]],
    pause_conditions: list[dict[str, Any]],
    resume_requirements: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.55 OSS Provenance Gap Closure / Pause",
        "",
        "Read-only closure of the OSS expansion lane after the 10.54 provenance gate.",
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
                ["pause_oss_expansion_lane_now", metrics["pause_oss_expansion_lane_now"]],
            ]
        ),
        "",
        "## Closure Decisions",
        "",
        _md_table(
            [["decision_item", "decision", "rationale"]]
            + [[row["decision_item"], row["decision"], row["rationale"]] for row in closure]
        ),
        "",
        "## Pause Conditions",
        "",
        _md_table(
            [["condition", "current_status", "action"]]
            + [[row["condition"], row["current_status"], row["action"]] for row in pause_conditions]
        ),
        "",
        "## Resume Requirements",
        "",
        _md_table(
            [["requirement", "required_content", "acceptance_check"]]
            + [[row["requirement"], row["required_content"], row["acceptance_check"]] for row in resume_requirements]
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
    parser = argparse.ArgumentParser(description="Close/pause OSS provenance gap lane after 10.54")
    parser.add_argument("--summary-1054", default=str(DEFAULT_1054_SUMMARY))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--requirements", default=str(DEFAULT_REQUIREMENTS))
    parser.add_argument("--next-options", default=str(DEFAULT_NEXT_OPTIONS))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1054 = _read_json(Path(args.summary_1054))
    decisions = _read_csv(Path(args.decisions))
    requirements = _read_csv(Path(args.requirements))
    next_options = _read_csv(Path(args.next_options))
    blocked_actions_input = _read_csv(Path(args.blocked_actions))
    m1054 = summary_1054["metrics"]

    do_not_accept = [row for row in decisions if row.get("acceptance_decision") == "DO_NOT_ACCEPT_NOW"]
    effect_fail = [row for row in decisions if row.get("effect_gate_status") == "fail_non_positive_net"]
    worst_net = min((_int(row.get("hit1_flip_net")) for row in decisions), default=0)
    best_net = max((_int(row.get("hit1_flip_net")) for row in decisions), default=0)

    closure_decisions = [
        {
            "decision_item": "oss_expansion_lane_status",
            "decision": "PAUSE_LANE",
            "rationale": "10.54 accepted_now_count=0 and owner_provenance_required_count=4; no local evidence can advance this lane by itself.",
        },
        {
            "decision_item": "candidate_source_acceptance",
            "decision": "KEEP_ALL_CANDIDATES_UNACCEPTED",
            "rationale": "All four additional v36 files are diagnostic traces without owner/source provenance packages.",
        },
        {
            "decision_item": "effect_gate",
            "decision": "KEEP_EFFECT_GATE_FAILED",
            "rationale": f"All candidate source hit1 nets are non-positive; best_net={best_net}, worst_net={worst_net}.",
        },
        {
            "decision_item": "learning_reentry",
            "decision": "DO_NOT_REENTER_S1_OR_S2",
            "rationale": "No provenance acceptance and no positive dev/OOF effect gate exist.",
        },
        {
            "decision_item": "algorithm_change",
            "decision": "DO_NOT_EXECUTE",
            "rationale": "Training, tuning, rules, GoalSearcher changes, and heldout/hard selection remain blocked.",
        },
    ]

    pause_conditions = [
        {
            "condition": "owner_source_provenance_package_absent",
            "current_status": "absent",
            "action": "pause OSS expansion lane",
        },
        {
            "condition": "candidate_sources_accepted_now",
            "current_status": f"accepted_now_count={m1054['accepted_now_count']}",
            "action": "do not use candidate sources as accepted OSS evidence",
        },
        {
            "condition": "effect_gate_pass",
            "current_status": f"effect_gate_pass_count={m1054['effect_gate_pass_count']}",
            "action": "do not open S1/S2 re-entry",
        },
        {
            "condition": "explicit_training_or_implementation_go",
            "current_status": "not provided and not sufficient without re-entry gates",
            "action": "do not train or implement",
        },
    ]

    resume_requirements = [
        {
            "requirement": row["requirement"],
            "required_content": row["required_content"],
            "acceptance_check": row["acceptance_check"],
        }
        for row in requirements
    ] + [
        {
            "requirement": "future_effect_reaudit_pass",
            "required_content": "After provenance acceptance, dev/OOF gain/loss/net and loss audit using accepted sources only.",
            "acceptance_check": "positive net, visible losses, no generated/source dominance, and at least two independent accepted source families before re-entry.",
        },
        {
            "requirement": "explicit_reentry_review",
            "required_content": "A new read-only re-entry review referencing the accepted provenance package and effect audit.",
            "acceptance_check": "Re-entry review must pass before any training, tuning, implementation, or candidate freeze.",
        },
    ]

    gap_summary = [
        {
            "gap": "source_provenance",
            "current_evidence": f"do_not_accept_now_count={len(do_not_accept)}",
            "why_it_blocks": "Diagnostic trace files cannot be treated as human OSS without owner/source provenance.",
            "needed_to_close": "producer, collection_method, generated_exclusion, provenance_hash, row lineage",
        },
        {
            "gap": "effect_evidence",
            "current_evidence": f"effect_fail_count={len(effect_fail)}; best_hit1_net={best_net}",
            "why_it_blocks": "Even accepted provenance would not justify S1/S2 re-entry without positive dev/OOF effect.",
            "needed_to_close": "accepted-source-only dev/OOF gain/loss/net and loss audit passing gate",
        },
        {
            "gap": "learning_authorization",
            "current_evidence": "training_allowed=false; implementation_allowed=false",
            "why_it_blocks": "This closure is read-only and does not authorize algorithm changes.",
            "needed_to_close": "future explicit go after re-entry gates pass",
        },
    ]

    next_options_out = [
        {
            "option": "pause_oss_expansion_lane",
            "status": "selected_default",
            "rationale": "No owner/source provenance package and no effect gate pass are available.",
        },
        {
            "option": "future_owner_provenance_intake",
            "status": "allowed_if_input_arrives",
            "rationale": "If owner supplies the required provenance package, open a future read-only intake review.",
        },
        {
            "option": "return_to_broader_strategy_review",
            "status": "allowed_user_redirect",
            "rationale": "User may choose a new strategy lane that does not depend on these OSS provenance gaps.",
        },
        {
            "option": "train_or_implement_now",
            "status": "blocked",
            "rationale": "No re-entry gate passed.",
        },
    ]

    blocked_actions = blocked_actions_input + [
        {
            "blocked_action": "continue_oss_expansion_without_new_package",
            "reason": "10.55 pauses the lane; further automatic stages would only restate the same missing provenance/effect evidence.",
            "allowed_after": "owner/source provenance package arrives or user explicitly redirects to broader strategy review",
        },
        {
            "blocked_action": "treat_pause_as_accuracy_progress",
            "reason": "Pause is governance closure, not model improvement.",
            "allowed_after": "never; accuracy requires future validated evidence and approved execution",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_closure_decisions.csv")),
        "pause_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_pause_conditions.csv")),
        "resume_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_resume_requirements.csv")),
        "gap_summary_csv": str(output_prefix.with_name(output_prefix.name + "_gap_summary.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1054["stage"],
        "candidate_source_file_count": m1054["candidate_source_file_count"],
        "accepted_now_count": m1054["accepted_now_count"],
        "do_not_accept_now_count": m1054["do_not_accept_now_count"],
        "owner_provenance_required_count": m1054["owner_provenance_required_count"],
        "effect_gate_pass_count": m1054["effect_gate_pass_count"],
        "best_candidate_hit1_net": best_net,
        "worst_candidate_hit1_net": worst_net,
        "pause_oss_expansion_lane_now": True,
        "future_reentry_ready_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "selected_next_route": "pause awaiting owner provenance package or broader strategy redirect",
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.55 OSS provenance gap closure / pause",
        "read_only": True,
        "closure_pause_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pause the OSS expansion lane. The four additional v36 diagnostic trace sources remain unaccepted as human OSS provenance, and the effect gate remains failed. "
            "Resume this lane only if an owner/source provenance package arrives; even then, run a separate accepted-source-only dev/OOF effect re-audit before any S1/S2 re-entry."
        ),
        "anti_drift_conclusion": (
            "10.55 only closes and pauses the OSS provenance gap lane. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, auto-accept diagnostic traces, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "paused awaiting owner provenance package or broader strategy redirect",
            "goal": "Do not continue OSS expansion automatically. Resume only with owner/source provenance package or explicit user redirect to broader strategy review.",
            "default": "pause",
        },
    }

    _write_csv(Path(artifacts["closure_decisions_csv"]), closure_decisions, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["pause_conditions_csv"]), pause_conditions, ["condition", "current_status", "action"])
    _write_csv(Path(artifacts["resume_requirements_csv"]), resume_requirements, ["requirement", "required_content", "acceptance_check"])
    _write_csv(Path(artifacts["gap_summary_csv"]), gap_summary, ["gap", "current_evidence", "why_it_blocks", "needed_to_close"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options_out, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_decisions, pause_conditions, resume_requirements)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
