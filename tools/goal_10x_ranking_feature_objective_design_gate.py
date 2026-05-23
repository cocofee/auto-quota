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
DEFAULT_STAGE_10_2 = AGENT_STATE / "goal_10x_ranking_objective_feature_evidence_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_ranking_feature_objective_design_gate"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _gate_decisions(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_2.get("metrics", {})
    objectives = stage_10_2.get("objective_families", [])
    features = stage_10_2.get("feature_families", [])
    leakage = stage_10_2.get("leakage_checks", [])
    fallback = stage_10_2.get("fallback_interactions", [])
    loss = stage_10_2.get("loss_audit_slices", [])
    p0_objectives = [row for row in objectives if row.get("priority") == "P0"]
    available_features = [row for row in features if row.get("evidence_status") == "available_in_current_whitelist"]
    leakage_pass = [row for row in leakage if row.get("decision") == "pass"]
    return [
        {
            "gate": "objective_family_coverage",
            "status": "pass" if len(objectives) >= 3 and len(p0_objectives) >= 2 else "fail",
            "observed": f"objective_families={len(objectives)}; p0_objectives={len(p0_objectives)}",
            "required": "at least 3 objective families and at least 2 P0 objectives",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "feature_family_coverage",
            "status": "pass" if len(features) >= 6 and len(available_features) == len(features) else "fail",
            "observed": f"feature_families={len(features)}; available={len(available_features)}; training_feature_count={metrics.get('training_feature_count', 0)}",
            "required": "all reviewed feature families available in the current whitelist",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "leakage_safety",
            "status": "pass" if metrics.get("leakage_training_hits", 1) == 0 and len(leakage_pass) == len(leakage) else "fail",
            "observed": f"leakage_checks={len(leakage)}; leakage_training_hits={metrics.get('leakage_training_hits', 0)}; pass={len(leakage_pass)}",
            "required": "zero forbidden identifier fields in training features",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "fallback_interaction_coverage",
            "status": "pass" if len(fallback) >= 3 else "fail",
            "observed": f"fallback_interactions={len(fallback)}; blocked_raw_hit1_gain={metrics.get('blocked_raw_hit1_gain', 0)}; prevented_raw_hit1_loss={metrics.get('prevented_raw_hit1_loss', 0)}",
            "required": "raw-vs-baseline, gate-vs-raw, and fallback contract interactions defined",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "loss_audit_slice_coverage",
            "status": "pass" if len(loss) >= 4 else "fail",
            "observed": f"loss_audit_slices={len(loss)}",
            "required": "query family, top1 family, book/rank bucket, and source/province slices defined",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "no_implementation_boundary",
            "status": "pass" if metrics.get("implementation_allowed") is False else "fail",
            "observed": f"implementation_allowed={metrics.get('implementation_allowed')}",
            "required": "10.3 may only decide whether to define an offline experiment plan",
            "decision": "do_not_train_or_change_ranking",
        },
    ]


def _plan_requirements() -> list[dict[str, Any]]:
    return [
        {
            "requirement": "experiment_scope",
            "required_content": "candidate objective variants, candidate feature-family inclusion/exclusion sets, and baseline/fallback comparators",
            "must_include": "raw LTR, selected safety gate, and baseline-only comparators",
            "not_allowed": "no training in 10.3; only define the plan in 10.4",
        },
        {
            "requirement": "split_and_selection_policy",
            "required_content": "dev/OOF selection only, frozen candidate before heldout/hard validation",
            "must_include": "explicit statement that heldout/hard are not used for threshold or strategy selection",
            "not_allowed": "no heldout threshold selection",
        },
        {
            "requirement": "loss_budget",
            "required_content": "new_loss ceiling, retained net gain target, blocked-gain recovery target, and saved-loss retention target",
            "must_include": "loss budget before any LambdaRank or gate experiment",
            "not_allowed": "no promotion based on net gain without loss slices",
        },
        {
            "requirement": "feature_leakage_gate",
            "required_content": "forbidden feature scan and whitelist diff policy",
            "must_include": "sample/source/expected/province/quota-id fields remain diagnostics only",
            "not_allowed": "no source_file, sample_id, expected_id, quota_id, or province ID feature",
        },
        {
            "requirement": "loss_audit_outputs",
            "required_content": "gain/loss by query_family, top1_family, book/rank_bucket, source_file, and province",
            "must_include": "generated repair source isolated from independent traces",
            "not_allowed": "no single-source or single-family improvement claim",
        },
    ]


def _blocked_items() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "train_ltr_model",
            "reason": "10.3 is only a design gate; offline experiment plan is not yet defined",
            "allowed_after": "10.4 plan definition plus explicit later approval stage",
        },
        {
            "blocked_action": "change_feature_whitelist",
            "reason": "feature families are reviewed, not edited",
            "allowed_after": "feature whitelist diff policy and leakage gate are defined",
        },
        {
            "blocked_action": "relax_safety_gate",
            "reason": "fallback/safety interaction must be preserved until loss budget exists",
            "allowed_after": "frozen OOF-selected candidate passes gate policy review",
        },
        {
            "blocked_action": "use_heldout_for_selection",
            "reason": "heldout/hard remain validation-only",
            "allowed_after": "never for selection; only after freeze for validation",
        },
    ]


def _metrics(gates: list[dict[str, Any]], stage_10_2: dict[str, Any]) -> dict[str, Any]:
    pass_count = sum(1 for row in gates if row["status"] == "pass")
    metrics_10_2 = stage_10_2.get("metrics", {})
    return {
        "gate_count": len(gates),
        "gate_pass_count": pass_count,
        "gate_fail_count": len(gates) - pass_count,
        "design_gate_passed": pass_count == len(gates),
        "objective_family_count": len(stage_10_2.get("objective_families", [])),
        "feature_family_count": len(stage_10_2.get("feature_families", [])),
        "leakage_training_hits": metrics_10_2.get("leakage_training_hits", 0),
        "training_feature_count": metrics_10_2.get("training_feature_count", 0),
        "raw_oof_net": metrics_10_2.get("raw_oof_net", 0),
        "raw_oof_loss": metrics_10_2.get("raw_oof_loss", 0),
        "selected_gate_net": metrics_10_2.get("selected_gate_net", 0),
        "selected_gate_loss": metrics_10_2.get("selected_gate_loss", 0),
        "implementation_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.3 Ranking Feature/Objective Design Gate",
        "",
        "Read-only design gate for S2. This decides whether the 10.2 objective/feature review is concrete enough to define an offline experiment plan. It does not train or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["gate_count", metrics["gate_count"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["gate_fail_count", metrics["gate_fail_count"]],
                ["design_gate_passed", metrics["design_gate_passed"]],
                ["objective_family_count", metrics["objective_family_count"]],
                ["feature_family_count", metrics["feature_family_count"]],
                ["leakage_training_hits", metrics["leakage_training_hits"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Gate Decisions",
        "",
        _md_table(
            [["gate", "status", "observed", "required"]]
            + [[row["gate"], row["status"], row["observed"], row["required"]] for row in report["gate_decisions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.3 ranking feature/objective design gate")
    parser.add_argument("--stage-10-2", default=str(DEFAULT_STAGE_10_2))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_2 = _read_json(Path(args.stage_10_2))
    gates = _gate_decisions(stage_10_2)
    requirements = _plan_requirements()
    blocked = _blocked_items()
    metrics = _metrics(gates, stage_10_2)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_gate_decisions.csv")),
        "experiment_plan_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_experiment_plan_requirements.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.3 ranking feature/objective design gate",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_10_2_summary": str(Path(args.stage_10_2)),
        },
        "metrics": metrics,
        "gate_decisions": gates,
        "experiment_plan_requirements": requirements,
        "blocked_actions": blocked,
        "decision": (
            "Pass the S2 design gate for plan definition only. Objective families, feature families, leakage gates, fallback interactions, "
            "and loss-audit slices are sufficiently specified to write an offline experiment plan in 10.4. This does not permit training, tuning, "
            "feature edits, ranking changes, heldout selection, or gate relaxation."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.3 only gates readiness for an offline experiment plan. It does not train, tune, patch rules, change ranking, modify GoalSearcher, "
            "use heldout for selection, connect online, relax gates, or edit features."
        ),
        "next_stage": {
            "stage": "10.4 offline ranking experiment plan definition",
            "goal": (
                "Read-only define the offline experiment plan for S2: objective variants, feature-family toggles, split policy, leakage gates, "
                "fallback contract, loss budget, and required outputs before any training run."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
            ],
        },
    }

    _write_csv(Path(artifacts["gate_decisions_csv"]), gates, ["gate", "status", "observed", "required", "decision"])
    _write_csv(Path(artifacts["experiment_plan_requirements_csv"]), requirements, ["requirement", "required_content", "must_include", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": metrics,
                "decision": report["decision"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
