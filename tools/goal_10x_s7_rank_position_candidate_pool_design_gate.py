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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1056_SUMMARY = AGENT_STATE / "goal_10x_broader_strategy_review_after_oss_pause_summary.json"
DEFAULT_1056_NEXT_GATE = AGENT_STATE / "goal_10x_broader_strategy_review_after_oss_pause_next_gate.csv"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_RANKED_GAP = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_ranked_gap_table.csv"
DEFAULT_RECALL_BOUNDARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_recall_boundary_report.csv"
DEFAULT_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_S5_FIELDS = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_field_manifest.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s7_rank_position_candidate_pool_design_gate"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


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
    gate_checks: list[dict[str, Any]],
    diagnostic_axes: list[dict[str, Any]],
    artifact_plan: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.57 S7 Rank-position and Candidate-pool Diagnostics Design Gate",
        "",
        "Read-only gate for deciding whether S7 diagnostics are concrete enough to define a future artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["wrong_rank_rows", metrics["wrong_rank_rows"]],
                ["top80_missing_rows", metrics["top80_missing_rows"]],
                ["scorecard_candidate_count", metrics["scorecard_candidate_count"]],
                ["loss_audit_slice_rows", metrics["loss_audit_slice_rows"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["gate_fail_count", metrics["gate_fail_count"]],
                ["s7_design_gate_decision", metrics["s7_design_gate_decision"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Diagnostic Axes",
        "",
        _md_table(
            [["axis_id", "purpose", "required_inputs", "forbidden_use"]]
            + [[row["axis_id"], row["purpose"], row["required_inputs"], row["forbidden_use"]] for row in diagnostic_axes]
        ),
        "",
        "## Artifact Plan",
        "",
        _md_table(
            [["artifact", "contents", "acceptance_check"]]
            + [[row["artifact"], row["contents"], row["acceptance_check"]] for row in artifact_plan]
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
    parser = argparse.ArgumentParser(description="S7 rank-position and candidate-pool diagnostics design gate")
    parser.add_argument("--summary-1056", default=str(DEFAULT_1056_SUMMARY))
    parser.add_argument("--next-gate-1056", default=str(DEFAULT_1056_NEXT_GATE))
    parser.add_argument("--wrong-rank", default=str(DEFAULT_WRONG_RANK))
    parser.add_argument("--top80-missing", default=str(DEFAULT_TOP80_MISSING))
    parser.add_argument("--ranked-gap", default=str(DEFAULT_RANKED_GAP))
    parser.add_argument("--recall-boundary", default=str(DEFAULT_RECALL_BOUNDARY))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--loss-audit", default=str(DEFAULT_LOSS_AUDIT))
    parser.add_argument("--hit1-flips", default=str(DEFAULT_HIT1_FLIPS))
    parser.add_argument("--s5-fields", default=str(DEFAULT_S5_FIELDS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1056 = _read_json(Path(args.summary_1056))
    next_gate_1056 = _read_csv(Path(args.next_gate_1056))
    wrong_rank = _read_csv(Path(args.wrong_rank))
    top80_missing = _read_csv(Path(args.top80_missing))
    ranked_gap = _read_csv(Path(args.ranked_gap))
    recall_boundary = _read_csv(Path(args.recall_boundary))
    scorecard = _read_csv(Path(args.scorecard))
    loss_audit = _read_csv(Path(args.loss_audit))
    hit1_flip_count = _read_jsonl_count(Path(args.hit1_flips))
    s5_fields = _read_csv(Path(args.s5_fields))

    wrong_rank_buckets = Counter(row.get("rank_bucket", "") for row in wrong_rank)
    wrong_rank_reasons = Counter(row.get("reason", "") for row in wrong_rank)
    missing_reasons = Counter(row.get("reason", "") for row in top80_missing)
    top80_rows_values = Counter(row.get("top80_rows", "") for row in wrong_rank + top80_missing)
    s5_field_names = {row.get("field", "") for row in s5_fields}
    required_s5 = {"split", "source_file", "source_family", "rank_bucket", "gain", "loss", "net", "taxonomy_disposition"}
    dev_recall_rows = [row for row in recall_boundary if row.get("split") == "dev"]
    top80_present_groups = max((_int(row.get("top80_present_groups")) for row in dev_recall_rows), default=0)
    top80_missing_groups = max((_int(row.get("top80_missing_groups")) for row in dev_recall_rows), default=len(top80_missing))
    top80_recall_rate = max((float(row.get("top80_recall_rate") or 0) for row in dev_recall_rows), default=0.0)

    input_manifest = [
        {
            "input_id": "wrong_rank_rows",
            "path": str(Path(args.wrong_rank)),
            "rows": len(wrong_rank),
            "use": "rank_bucket and positive_rank_min diagnostics for top80_present but wrong-rank rows",
            "status": "available" if wrong_rank else "missing",
        },
        {
            "input_id": "top80_missing_rows",
            "path": str(Path(args.top80_missing)),
            "rows": len(top80_missing),
            "use": "candidate-pool ceiling and recall-vs-ranking separation",
            "status": "available" if top80_missing else "missing",
        },
        {
            "input_id": "ranked_gap_table",
            "path": str(Path(args.ranked_gap)),
            "rows": len(ranked_gap),
            "use": "existing gap dimensions by split/status/dimension/key",
            "status": "available" if ranked_gap else "missing",
        },
        {
            "input_id": "recall_boundary_report",
            "path": str(Path(args.recall_boundary)),
            "rows": len(recall_boundary),
            "use": "top80_present/top80_missing split boundary for S2 outputs",
            "status": "available" if recall_boundary else "missing",
        },
        {
            "input_id": "candidate_scorecard",
            "path": str(Path(args.scorecard)),
            "rows": len(scorecard),
            "use": "candidate-level hit1/hit5 gain/loss/net context; diagnostic only",
            "status": "available" if scorecard else "missing",
        },
        {
            "input_id": "loss_audit_by_slice",
            "path": str(Path(args.loss_audit)),
            "rows": len(loss_audit),
            "use": "slice-level gain/loss/net and loss concentration context",
            "status": "available" if loss_audit else "missing",
        },
        {
            "input_id": "hit1_flips",
            "path": str(Path(args.hit1_flips)),
            "rows": hit1_flip_count,
            "use": "flip rows for candidate/source/family loss diagnostics; no new training",
            "status": "available" if hit1_flip_count else "missing",
        },
        {
            "input_id": "s5_field_contract",
            "path": str(Path(args.s5_fields)),
            "rows": len(s5_fields),
            "use": "field boundary and forbidden-use contract",
            "status": "available" if required_s5.issubset(s5_field_names) else "incomplete",
        },
    ]

    diagnostic_axes = [
        {
            "axis_id": "rank_position_distribution",
            "purpose": "Separate near-miss rank_2_5 rows from deeper rank_6_80 wrong-rank rows.",
            "required_inputs": "wrong_rank.rank_bucket, positive_rank_min, reason, query_family, source_file",
            "forbidden_use": "Do not convert buckets into thresholds or ranking rules.",
        },
        {
            "axis_id": "candidate_pool_ceiling",
            "purpose": "Separate top80_present ranking failures from top80_missing recall/pool ceiling failures.",
            "required_inputs": "wrong_rank rows, top80_missing rows, recall_boundary top80_present/top80_missing counts",
            "forbidden_use": "Do not claim ranking can fix missing positives outside the pool.",
        },
        {
            "axis_id": "loss_concentration",
            "purpose": "Identify whether candidate losses cluster by query_family/source_file/book/rank_bucket.",
            "required_inputs": "loss_audit_by_slice, hit1_flips, scorecard",
            "forbidden_use": "Do not select or freeze candidates from diagnostics alone.",
        },
        {
            "axis_id": "source_and_taxonomy_disposition",
            "purpose": "Keep generated/source-dominated and taxonomy-cleanup rows separate from learning signals.",
            "required_inputs": "source_file/source_family fields, S5 taxonomy_disposition/effect contract",
            "forbidden_use": "Do not turn DQ backlog or generated-source rows into learning evidence.",
        },
        {
            "axis_id": "candidate_pool_size_shape",
            "purpose": "Record pool size/top80_rows coverage and detect whether failures are due to pool truncation or rank ordering.",
            "required_inputs": "top80_rows, top80_missing, top80_present rows",
            "forbidden_use": "Do not expand retrieval or candidate matrix in this stage.",
        },
    ]

    artifact_plan = [
        {
            "artifact": "rank_position_distribution.csv",
            "contents": "Counts by split, rank_bucket, reason, query_family, source_family/source_file, top1/expected book relation.",
            "acceptance_check": "Every wrong-rank row is assigned to one rank-position bucket without using heldout/hard.",
        },
        {
            "artifact": "candidate_pool_boundary.csv",
            "contents": "top80_present vs top80_missing counts, top80_rows shape, top80_recall_rate, recall-vs-ranking boundary.",
            "acceptance_check": "Rows outside top80 are not counted as ranking-objective wins.",
        },
        {
            "artifact": "loss_concentration_map.csv",
            "contents": "Gain/loss/net by candidate_id and S5-compatible slice dimensions.",
            "acceptance_check": "Loss rows remain visible; no net-only claim.",
        },
        {
            "artifact": "diagnostic_readiness_checks.csv",
            "contents": "Gate checks for inputs, split boundary, S5 compatibility, and blocked actions.",
            "acceptance_check": "Any missing input or execution dependency stops at design/definition only.",
        },
    ]

    gate_checks = [
        {
            "check_id": "S7_SCOPE_DEFINED",
            "status": "pass" if summary_1056["metrics"].get("selected_next_lane") == "S7_rank_position_distribution_diagnostics" else "fail",
            "evidence": f"selected_next_lane={summary_1056['metrics'].get('selected_next_lane')}",
            "decision": "S7 is the selected broader strategy lane.",
        },
        {
            "check_id": "RANK_POSITION_INPUTS_AVAILABLE",
            "status": "pass" if wrong_rank and top80_missing and ranked_gap else "fail",
            "evidence": f"wrong_rank_rows={len(wrong_rank)}; top80_missing_rows={len(top80_missing)}; ranked_gap_rows={len(ranked_gap)}",
            "decision": "Existing 9.x gap rows can support rank-position and candidate-pool diagnostics.",
        },
        {
            "check_id": "DEV_OOF_LOSS_INPUTS_AVAILABLE",
            "status": "pass" if scorecard and loss_audit and hit1_flip_count else "fail",
            "evidence": f"scorecard_candidates={len(scorecard)}; loss_audit_rows={len(loss_audit)}; hit1_flip_rows={hit1_flip_count}",
            "decision": "Existing dev/OOF outputs can support loss-concentration diagnostics.",
        },
        {
            "check_id": "RECALL_BOUNDARY_AVAILABLE",
            "status": "pass" if top80_present_groups > 0 and top80_missing_groups > 0 else "fail",
            "evidence": f"top80_present_groups={top80_present_groups}; top80_missing_groups={top80_missing_groups}; top80_recall_rate={top80_recall_rate:.6f}",
            "decision": "Candidate-pool ceiling can be separated from ranking failures.",
        },
        {
            "check_id": "S5_FIELD_COMPATIBILITY",
            "status": "pass" if required_s5.issubset(s5_field_names) else "fail",
            "evidence": f"required_fields_present={required_s5.issubset(s5_field_names)}",
            "decision": "S7 artifacts can reuse S5 split/source/effect field boundaries.",
        },
        {
            "check_id": "NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "training_allowed=false; implementation_allowed=false; heldout_selection_allowed=false",
            "decision": "S7 may proceed only to artifact definition, not execution or algorithm change.",
        },
    ]
    gate_fail_count = sum(1 for row in gate_checks if row["status"] != "pass")

    blocked_actions = [
        {
            "blocked_action": "train_or_tune_ranking_from_s7",
            "reason": "10.57 is a diagnostic design gate only.",
            "allowed_after": "future explicit execution authorization after separate strategy gates",
        },
        {
            "blocked_action": "expand_candidate_matrix_or_retrieval_pool",
            "reason": "S7 can diagnose candidate-pool shape but cannot change the pool.",
            "allowed_after": "future implementation plan with explicit go",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "S7 is restricted to existing dev/OOF diagnostics; heldout/hard are validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain_from_diagnostics",
            "reason": "Diagnostics explain failure modes; they are not accuracy improvements.",
            "allowed_after": "future validated candidate with full loss audit and validation boundary",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "input_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_input_manifest.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "diagnostic_axes_csv": str(output_prefix.with_name(output_prefix.name + "_diagnostic_axes.csv")),
        "artifact_plan_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_plan.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1056["stage"],
        "wrong_rank_rows": len(wrong_rank),
        "top80_missing_rows": len(top80_missing),
        "ranked_gap_rows": len(ranked_gap),
        "rank_bucket_count": len(wrong_rank_buckets),
        "top_wrong_rank_bucket": wrong_rank_buckets.most_common(1)[0][0] if wrong_rank_buckets else "",
        "top_wrong_rank_bucket_count": wrong_rank_buckets.most_common(1)[0][1] if wrong_rank_buckets else 0,
        "top_wrong_rank_reason": wrong_rank_reasons.most_common(1)[0][0] if wrong_rank_reasons else "",
        "top_missing_reason": missing_reasons.most_common(1)[0][0] if missing_reasons else "",
        "top80_rows_values": ";".join(f"{key}:{value}" for key, value in top80_rows_values.most_common()),
        "top80_present_groups": top80_present_groups,
        "top80_missing_groups": top80_missing_groups,
        "top80_recall_rate": round(top80_recall_rate, 6),
        "scorecard_candidate_count": len(scorecard),
        "loss_audit_slice_rows": len(loss_audit),
        "hit1_flip_rows": hit1_flip_count,
        "diagnostic_axis_count": len(diagnostic_axes),
        "planned_artifact_count": len(artifact_plan),
        "gate_pass_count": len(gate_checks) - gate_fail_count,
        "gate_fail_count": gate_fail_count,
        "s7_design_gate_decision": "pass_to_read_only_artifact_definition" if gate_fail_count == 0 else "hold_until_inputs_complete",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.57 S7 rank-position and candidate-pool diagnostics design gate",
        "read_only": True,
        "diagnostic_design_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pass S7 to read-only artifact definition. Existing dev/OOF and 9.x gap artifacts are sufficient to define rank-position distribution, candidate-pool boundary, loss-concentration, and source/taxonomy disposition diagnostics. "
            "This does not authorize training, candidate matrix expansion, retrieval changes, heldout/hard selection, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.57 only checks whether S7 diagnostics are concrete enough for future artifact definition. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.58 S7 diagnostic artifact definition",
            "goal": "Read-only define S7 rank-position distribution, candidate-pool boundary, loss-concentration, and source/taxonomy disposition artifacts from existing dev/OOF inputs.",
            "default": "artifact definition only; no training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["input_manifest_csv"]), input_manifest, ["input_id", "path", "rows", "use", "status"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["diagnostic_axes_csv"]), diagnostic_axes, ["axis_id", "purpose", "required_inputs", "forbidden_use"])
    _write_csv(Path(artifacts["artifact_plan_csv"]), artifact_plan, ["artifact", "contents", "acceptance_check"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, diagnostic_axes, artifact_plan)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
