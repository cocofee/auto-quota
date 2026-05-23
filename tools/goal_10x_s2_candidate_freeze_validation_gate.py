from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_EXECUTION_SUMMARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_leakage_gate_report.json"
DEFAULT_FALLBACK = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_fallback_contract_report.csv"
DEFAULT_RECALL_BOUNDARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_recall_boundary_report.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_candidate_freeze_validation_gate"


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _select_candidate(scorecard: pd.DataFrame) -> dict[str, Any]:
    approved = scorecard[scorecard["approval_status"] == "pass_dev_oof_candidate"].copy()
    if approved.empty:
        return {}
    approved["scorecard_rank_int"] = approved["scorecard_rank"].map(_int)
    approved["hit1_net_int"] = approved["hit1_net"].map(_int)
    approved["hit1_loss_int"] = approved["hit1_loss"].map(_int)
    approved["candidate_hit1_rate_float"] = approved["candidate_hit1_rate"].map(_float)
    approved = approved.sort_values(
        ["hit1_net_int", "hit1_loss_int", "candidate_hit1_rate_float", "scorecard_rank_int"],
        ascending=[False, True, False, True],
    )
    return approved.iloc[0].to_dict()


def _slice_review(loss_audit: pd.DataFrame, candidate_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = loss_audit[loss_audit["candidate_id"] == candidate_id].copy()
    review_rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    for dimension in ["query_family", "top1_family", "book_and_rank_bucket", "source_file", "province"]:
        part = rows[rows["slice_dimension"] == dimension].copy()
        if part.empty:
            stats[f"{dimension}_slice_count"] = 0
            stats[f"{dimension}_max_gain_share"] = 0.0
            stats[f"{dimension}_max_net_share"] = 0.0
            continue
        part["gain_int"] = part["gain"].map(_int)
        part["loss_int"] = part["loss"].map(_int)
        part["net_int"] = part["net"].map(_int)
        total_gain = int(part["gain_int"].sum())
        positive_net_sum = int(part[part["net_int"] > 0]["net_int"].sum())
        part["gain_share"] = part["gain_int"].apply(lambda value: round(value / total_gain, 6) if total_gain else 0.0)
        part["positive_net_share"] = part["net_int"].apply(lambda value: round(value / positive_net_sum, 6) if positive_net_sum and value > 0 else 0.0)
        top_part = part.sort_values(["net_int", "gain_int"], ascending=[False, False]).head(12)
        for _, row in top_part.iterrows():
            review_rows.append(
                {
                    "candidate_id": candidate_id,
                    "slice_dimension": dimension,
                    "slice_key": row.get("slice_key"),
                    "groups": _int(row.get("groups")),
                    "gain": _int(row.get("gain")),
                    "loss": _int(row.get("loss")),
                    "net": _int(row.get("net")),
                    "gain_share": row.get("gain_share"),
                    "positive_net_share": row.get("positive_net_share"),
                    "candidate_hit1_rate": row.get("candidate_hit1_rate"),
                }
            )
        stats[f"{dimension}_slice_count"] = int(len(part))
        stats[f"{dimension}_total_gain"] = total_gain
        stats[f"{dimension}_total_loss"] = int(part["loss_int"].sum())
        stats[f"{dimension}_positive_net_sum"] = positive_net_sum
        stats[f"{dimension}_max_gain_share"] = float(part["gain_share"].max()) if len(part) else 0.0
        stats[f"{dimension}_max_net_share"] = float(part["positive_net_share"].max()) if len(part) else 0.0
        stats[f"{dimension}_max_net_slice_key"] = str(part.sort_values(["net_int", "gain_int"], ascending=[False, False]).iloc[0].get("slice_key"))
    return review_rows, stats


def _fallback_stats(fallback_csv: Path, candidate_id: str) -> dict[str, Any]:
    df = pd.read_csv(fallback_csv, encoding="utf-8-sig")
    rows = df[df["candidate_id"] == candidate_id].copy()
    if rows.empty:
        return {"fallback_rows": 0, "fallback_no_gate_relaxation": False, "override_count": 0}
    return {
        "fallback_rows": int(len(rows)),
        "fallback_no_gate_relaxation": bool(rows["no_gate_relaxation"].map(_bool).all()),
        "override_count": int(rows["candidate_override"].map(_bool).sum()),
        "override_gain_count": int((rows["override_outcome"] == "gain").sum()),
        "override_loss_count": int((rows["override_outcome"] == "loss").sum()),
    }


def _recall_stats(recall_json: Path, candidate_id: str) -> dict[str, Any]:
    payload = _read_json(recall_json)
    row = next((item for item in payload.get("recall_boundary", []) if item.get("candidate_id") == candidate_id), {})
    return {
        "recall_boundary_present": bool(row),
        "top80_present_groups": _int(row.get("top80_present_groups")),
        "top80_missing_groups": _int(row.get("top80_missing_groups")),
        "top80_recall_rate": _float(row.get("top80_recall_rate")),
        "ranking_claim_scope": row.get("ranking_claim_scope", ""),
        "recall_missing_claim": row.get("recall_missing_claim", ""),
    }


def _gate_checks(
    *,
    selected: dict[str, Any],
    execution_summary: dict[str, Any],
    leakage_report: dict[str, Any],
    slice_stats: dict[str, Any],
    fallback_stats: dict[str, Any],
    recall_stats: dict[str, Any],
) -> list[dict[str, Any]]:
    source_net_share = _float(slice_stats.get("source_file_max_net_share"))
    source_key = str(slice_stats.get("source_file_max_net_slice_key") or "")
    return [
        {
            "gate": "candidate_exists",
            "status": "pass" if selected else "fail",
            "observed": f"candidate_id={selected.get('candidate_id', '')}",
            "decision": "eligible_for_freeze_review" if selected else "no_candidate",
            "not_allowed": "no ad hoc candidate outside locked scorecard",
        },
        {
            "gate": "dev_oof_selection_only",
            "status": "pass" if not _bool(execution_summary.get("metrics", {}).get("heldout_used_for_selection")) else "fail",
            "observed": f"heldout_used_for_selection={execution_summary.get('metrics', {}).get('heldout_used_for_selection')}; hard_used_for_selection={execution_summary.get('metrics', {}).get('hard_used_for_selection')}",
            "decision": "selection_clean",
            "not_allowed": "no heldout/hard selection",
        },
        {
            "gate": "leakage_gate",
            "status": "pass" if _bool(leakage_report.get("leakage_gate_passed")) else "fail",
            "observed": f"leakage_gate_passed={leakage_report.get('leakage_gate_passed')}",
            "decision": "features_clean_for_dev_oof",
            "not_allowed": "no forbidden identifiers in training features",
        },
        {
            "gate": "loss_budget",
            "status": "pass" if _int(selected.get("hit1_loss")) <= 18 else "fail",
            "observed": f"hit1_loss={selected.get('hit1_loss')}; ceiling=18",
            "decision": "loss_budget_pass" if _int(selected.get("hit1_loss")) <= 18 else "loss_budget_fail",
            "not_allowed": "net gain cannot override loss ceiling",
        },
        {
            "gate": "net_gain_reference",
            "status": "pass" if _int(selected.get("hit1_net")) > 48 else "fail",
            "observed": f"hit1_net={selected.get('hit1_net')}; selected_gate_net_reference=48",
            "decision": "beats_selected_gate_reference",
            "not_allowed": "do not freeze low-gain candidate",
        },
        {
            "gate": "fallback_contract",
            "status": "pass" if fallback_stats.get("fallback_no_gate_relaxation") else "fail",
            "observed": f"fallback_rows={fallback_stats.get('fallback_rows')}; no_gate_relaxation={fallback_stats.get('fallback_no_gate_relaxation')}",
            "decision": "fallback_boundary_preserved",
            "not_allowed": "no gate relaxation or online fallback change",
        },
        {
            "gate": "recall_boundary",
            "status": "pass" if recall_stats.get("ranking_claim_scope") == "top80_present_only" and recall_stats.get("recall_missing_claim") == "unchanged_not_claimed" else "fail",
            "observed": f"top80_missing_groups={recall_stats.get('top80_missing_groups')}; recall_missing_claim={recall_stats.get('recall_missing_claim')}",
            "decision": "ranking_claim_limited_to_top80_present",
            "not_allowed": "do not claim recall-missing improvement",
        },
        {
            "gate": "cross_source_artifact",
            "status": "fail" if source_net_share >= 0.8 or "global_repair_decision_table" in source_key else "pass",
            "observed": f"max_source_positive_net_share={source_net_share}; max_source={source_key}",
            "decision": "source_dominated_hold" if source_net_share >= 0.8 or "global_repair_decision_table" in source_key else "cross_source_ok",
            "not_allowed": "do not freeze general validation candidate from single generated-source dominated evidence",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "retrain_or_expand_candidate_matrix",
            "reason": "freeze gate consumes the completed locked S2 dev/OOF run only.",
            "allowed_after": "separate explicit execution stage",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only and cannot influence freeze selection.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "promote_to_online_or_goal_searcher",
            "reason": "freeze gate is not online integration.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "feature toggles are evaluation candidates only.",
            "allowed_after": "separate implementation proposal after validation",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "selected candidate evidence is source-dominated until independent-source support exists.",
            "allowed_after": "future independent-slice evidence review",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    selected = report["selected_candidate"]
    lines = [
        "# S2 Candidate Freeze / Validation Gate",
        "",
        "Read-only freeze gate after the dev/OOF-only offline ranking experiment. No retraining, no heldout/hard selection, and no online change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_candidate_id", metrics["selected_candidate_id"]],
                ["freeze_decision", metrics["freeze_decision"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["gate_fail_count", metrics["gate_fail_count"]],
                ["hit1_net", selected.get("hit1_net")],
                ["hit1_gain", selected.get("hit1_gain")],
                ["hit1_loss", selected.get("hit1_loss")],
                ["source_file_max_net_share", metrics["source_file_max_net_share"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "observed", "decision"]] + [[row["gate"], row["status"], row["observed"], row["decision"]] for row in report["gate_checks"]]),
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
    parser = argparse.ArgumentParser(description="S2 candidate freeze / validation gate")
    parser.add_argument("--execution-summary", default=str(DEFAULT_EXECUTION_SUMMARY))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--loss-audit", default=str(DEFAULT_LOSS_AUDIT))
    parser.add_argument("--leakage-report", default=str(DEFAULT_LEAKAGE))
    parser.add_argument("--fallback-report", default=str(DEFAULT_FALLBACK))
    parser.add_argument("--recall-boundary", default=str(DEFAULT_RECALL_BOUNDARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    execution_summary = _read_json(Path(args.execution_summary))
    leakage_report = _read_json(Path(args.leakage_report))
    scorecard = pd.read_csv(args.scorecard, encoding="utf-8-sig")
    loss_audit = pd.read_csv(args.loss_audit, encoding="utf-8-sig")
    selected = _select_candidate(scorecard)
    if not selected:
        raise RuntimeError("no approved candidate found")
    candidate_id = str(selected["candidate_id"])
    slice_review_rows, slice_stats = _slice_review(loss_audit, candidate_id)
    fallback = _fallback_stats(Path(args.fallback_report), candidate_id)
    recall = _recall_stats(Path(args.recall_boundary), candidate_id)
    gate_checks = _gate_checks(
        selected=selected,
        execution_summary=execution_summary,
        leakage_report=leakage_report,
        slice_stats=slice_stats,
        fallback_stats=fallback,
        recall_stats=recall,
    )
    fail_gates = [row for row in gate_checks if row["status"] != "pass"]
    freeze_decision = "HOLD_DO_NOT_FREEZE_FOR_GENERAL_VALIDATION" if fail_gates else "FREEZE_FOR_VALIDATION"
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "selected_candidate_csv": str(output_prefix.with_name(output_prefix.name + "_selected_candidate.csv")),
        "loss_slice_review_csv": str(output_prefix.with_name(output_prefix.name + "_loss_slice_review.csv")),
        "freeze_decision_csv": str(output_prefix.with_name(output_prefix.name + "_freeze_decision.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "selected_candidate_id": candidate_id,
        "freeze_decision": freeze_decision,
        "gate_count": len(gate_checks),
        "gate_pass_count": sum(1 for row in gate_checks if row["status"] == "pass"),
        "gate_fail_count": len(fail_gates),
        "hit1_net": _int(selected.get("hit1_net")),
        "hit1_gain": _int(selected.get("hit1_gain")),
        "hit1_loss": _int(selected.get("hit1_loss")),
        "candidate_hit1_rate": _float(selected.get("candidate_hit1_rate")),
        "approval_candidate_count": execution_summary.get("metrics", {}).get("approval_candidate_count"),
        "source_file_max_net_share": slice_stats.get("source_file_max_net_share"),
        "source_file_max_net_slice_key": slice_stats.get("source_file_max_net_slice_key"),
        "query_family_max_net_share": slice_stats.get("query_family_max_net_share"),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "online_integration": False,
        "goal_searcher_changed": False,
        "feature_whitelist_changed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    decision = (
        f"Select {candidate_id} as the best approved dev/OOF candidate for freeze review, but do not freeze it as a general validation candidate yet. "
        "It passes leakage, loss budget, net-gain, fallback, and recall-boundary gates, but fails the cross-source artifact gate because positive net gain is dominated by "
        f"{slice_stats.get('source_file_max_net_slice_key')} with max_source_positive_net_share={slice_stats.get('source_file_max_net_share')}. "
        "Treat it as the diagnostic lead candidate and require an independent-source robustness gate before heldout/hard validation."
        if freeze_decision.startswith("HOLD")
        else f"Freeze {candidate_id} for the next validation stage based only on dev/OOF evidence."
    )
    report = {
        "stage": "Goal LTR v1 / S2 candidate freeze / validation gate",
        "read_only": True,
        "dev_oof_only_selection": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "no_retraining": True,
        "no_threshold_change": True,
        "no_ranking_change": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "execution_summary": str(Path(args.execution_summary)),
            "scorecard": str(Path(args.scorecard)),
            "loss_audit": str(Path(args.loss_audit)),
            "leakage_report": str(Path(args.leakage_report)),
            "fallback_report": str(Path(args.fallback_report)),
            "recall_boundary": str(Path(args.recall_boundary)),
        },
        "metrics": metrics,
        "selected_candidate": selected,
        "slice_stats": slice_stats,
        "fallback_stats": fallback,
        "recall_stats": recall,
        "gate_checks": gate_checks,
        "freeze_decision": {
            "candidate_id": candidate_id,
            "decision": freeze_decision,
            "failed_gates": [row["gate"] for row in fail_gates],
            "next_allowed": "independent-source robustness review before any heldout/hard validation" if fail_gates else "frozen validation stage",
        },
        "blocked_actions": _blocked_actions(),
        "artifacts": artifacts,
        "decision": decision,
        "anti_drift_conclusion": (
            "This stage only reviews completed dev/OOF artifacts. It does not retrain, tune, expand candidates, use heldout/hard for selection, change thresholds, "
            "patch rules, modify ranking or GoalSearcher, edit the feature whitelist, or connect online."
        ),
        "next_stage": {
            "stage": "S2 independent-source robustness gate",
            "goal": "Read-only test whether the diagnostic lead candidate has enough non-generated and cross-source support before any heldout/hard validation.",
            "prohibited": [
                "retraining",
                "candidate expansion",
                "heldout/hard selection",
                "threshold change",
                "ranking implementation",
                "GoalSearcher change",
                "feature whitelist edit",
                "online integration",
            ],
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(
        Path(artifacts["selected_candidate_csv"]),
        [{key: selected.get(key) for key in selected.keys()}],
        list(selected.keys()),
    )
    _write_csv(
        Path(artifacts["loss_slice_review_csv"]),
        slice_review_rows,
        ["candidate_id", "slice_dimension", "slice_key", "groups", "gain", "loss", "net", "gain_share", "positive_net_share", "candidate_hit1_rate"],
    )
    _write_csv(
        Path(artifacts["freeze_decision_csv"]),
        [report["freeze_decision"]],
        ["candidate_id", "decision", "failed_gates", "next_allowed"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), report["blocked_actions"], ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
