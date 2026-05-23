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
DEFAULT_STRATEGY = AGENT_STATE / "goal_10x_accuracy_strategy_definition_summary.json"
DEFAULT_DECOMPOSITION = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_SAFETY_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_COMPAT = AGENT_STATE / "goal_family_compatibility_whatif_summary.json"
DEFAULT_BACKLOG = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory"


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


def _split_metrics(decomp: dict[str, Any], split: str = "dev") -> dict[str, Any]:
    for row in decomp.get("splits", []):
        if row.get("split") == split:
            return row
    return {}


def _compat_oof(compat: dict[str, Any]) -> dict[str, Any]:
    for row in compat.get("split_metrics", []):
        if row.get("split") == "dev_oof":
            return row
    return {}


def _selected_oof(safety: dict[str, Any]) -> dict[str, Any]:
    return safety.get("selection", {}).get("selected_metrics", {})


def _raw_oof(safety: dict[str, Any]) -> dict[str, Any]:
    return safety.get("selection", {}).get("raw_oof_metrics", {})


def _candidate_by_id(strategy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["strategy_id"]: row for row in strategy.get("strategy_candidates", [])}


def _evidence_inventory(
    strategy: dict[str, Any],
    decomp: dict[str, Any],
    safety: dict[str, Any],
    compat: dict[str, Any],
    backlog: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = _candidate_by_id(strategy)
    dev = _split_metrics(decomp, "dev")
    selected = _selected_oof(safety)
    raw = _raw_oof(safety)
    compat_dev = _compat_oof(compat)
    backlog_metrics = backlog.get("metrics", {})
    rows = [
        {
            "strategy_id": "S1_recall_route_evidence_inventory",
            "candidate_lever": candidates["S1_recall_route_evidence_inventory"]["candidate_lever"],
            "dev_signal": f"dev_top80_missing={dev.get('top80_missing', 0)}; dev_top80_recall_rate={dev.get('top80_recall_rate', 0)}",
            "oof_signal": "not_yet_independent_recall_inventory",
            "positive_evidence": "recall gap exists in dev and is large enough to inventory",
            "blocking_evidence": (
                f"9.32 source_provenance_rows={backlog_metrics.get('source_provenance_rows', 0)}; "
                f"query_family_empty_rows={backlog_metrics.get('query_family_empty_rows', 0)}; "
                "global_repair_decision_table cannot be direct learning evidence"
            ),
            "evidence_strength": "medium",
            "blocker_risk": "high",
            "readiness": "inventory_only",
        },
        {
            "strategy_id": "S2_ranking_objective_and_feature_strategy",
            "candidate_lever": candidates["S2_ranking_objective_and_feature_strategy"]["candidate_lever"],
            "dev_signal": (
                f"dev_wrong_rank={dev.get('top80_present_but_wrong_rank', 0)}; "
                f"wrong_rank_share_of_non_hit={dev.get('wrong_rank_share_of_non_hit', 0)}"
            ),
            "oof_signal": (
                f"raw_oof_net={raw.get('raw_ltr_hit1_net', 0)}; raw_oof_loss={raw.get('raw_ltr_hit1_loss', 0)}; "
                f"selected_gate_net={selected.get('gated_hit1_net', 0)}; selected_gate_loss={selected.get('gated_hit1_loss', 0)}"
            ),
            "positive_evidence": "largest dev gap lane plus measurable OOF ranking gain/loss evidence already exists",
            "blocking_evidence": "requires feature leakage audit, objective framing, fallback interaction, and loss budget before any training",
            "evidence_strength": "high",
            "blocker_risk": "medium",
            "readiness": "next_analysis_lane",
        },
        {
            "strategy_id": "S3_safety_gate_calibration_v2_plan",
            "candidate_lever": candidates["S3_safety_gate_calibration_v2_plan"]["candidate_lever"],
            "dev_signal": (
                f"selected_gate_blocked_gain={selected.get('blocked_raw_hit1_gain', 0)}; "
                f"prevented_raw_loss={selected.get('prevented_raw_hit1_loss', 0)}"
            ),
            "oof_signal": (
                f"compat_rescued_blocked_gain={compat_dev.get('rescued_blocked_gain', 0)}; "
                f"compat_new_residual_loss={compat_dev.get('new_residual_loss', 0)}"
            ),
            "positive_evidence": "OOF safety gate and compatibility have measurable rescue/loss signals",
            "blocking_evidence": "depends on explicit loss budget and should follow ranking/objective evidence framing",
            "evidence_strength": "medium",
            "blocker_risk": "medium",
            "readiness": "defer_after_S2",
        },
        {
            "strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "candidate_lever": candidates["S4_taxonomy_data_quality_prerequisite_track"]["candidate_lever"],
            "dev_signal": (
                f"backlog_handoff_items={backlog_metrics.get('handoff_item_count', 0)}; "
                f"priority_backlog_rows={backlog_metrics.get('total_priority_backlog_rows', 0)}"
            ),
            "oof_signal": "not_a_learning_split",
            "positive_evidence": "clear taxonomy/data-quality prerequisites are documented",
            "blocking_evidence": "not an accuracy learning lane; cannot count backlog rows as Top1 evidence",
            "evidence_strength": "high",
            "blocker_risk": "high_for_learning",
            "readiness": "parallel_prerequisite_not_selected_lane",
        },
    ]
    return rows


def _score(row: dict[str, Any]) -> tuple[int, str, str]:
    strength_points = {"high": 3, "medium": 2, "low": 1}.get(row["evidence_strength"], 0)
    risk_penalty = {"low": 0, "medium": 1, "high": 2, "high_for_learning": 2}.get(row["blocker_risk"], 1)
    readiness_bonus = {
        "next_analysis_lane": 2,
        "inventory_only": 1,
        "defer_after_S2": 0,
        "parallel_prerequisite_not_selected_lane": 0,
    }.get(row["readiness"], 0)
    score = strength_points * 2 + readiness_bonus - risk_penalty
    if row["readiness"] == "next_analysis_lane":
        decision = "select_next_analysis_lane"
        next_stage = "10.2 ranking objective and feature evidence review"
    elif row["readiness"] == "parallel_prerequisite_not_selected_lane":
        decision = "keep_parallel_prerequisite"
        next_stage = "parallel taxonomy/data-quality backlog, not 10.2"
    elif row["readiness"] == "defer_after_S2":
        decision = "defer"
        next_stage = "after ranking/objective evidence framing"
    else:
        decision = "inventory_before_selection"
        next_stage = "revisit after independent recall evidence inventory"
    return score, decision, next_stage


def _scoring_matrix(evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in evidence_rows:
        score, decision, next_stage = _score(row)
        rows.append(
            {
                "strategy_id": row["strategy_id"],
                "candidate_lever": row["candidate_lever"],
                "evidence_strength": row["evidence_strength"],
                "blocker_risk": row["blocker_risk"],
                "readiness": row["readiness"],
                "score": score,
                "decision": decision,
                "next_stage_if_selected": next_stage,
            }
        )
    rows.sort(key=lambda item: (item["decision"] != "select_next_analysis_lane", -int(item["score"])))
    return rows


def _selected_lane(scoring_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [row for row in scoring_rows if row["decision"] == "select_next_analysis_lane"]
    if not selected:
        return []
    top = selected[0]
    evidence = next(row for row in evidence_rows if row["strategy_id"] == top["strategy_id"])
    return [
        {
            "selected_strategy_id": top["strategy_id"],
            "selected_candidate_lever": top["candidate_lever"],
            "selected_next_stage": top["next_stage_if_selected"],
            "score": top["score"],
            "selection_basis": "dev/OOF only; heldout/hard not used for selection",
            "why_selected": evidence["positive_evidence"],
            "required_boundary": evidence["blocking_evidence"],
            "implementation_allowed": "no",
        }
    ]


def _deferred_rows(scoring_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in scoring_rows if row["decision"] != "select_next_analysis_lane"]


def _metrics(evidence_rows: list[dict[str, Any]], scoring_rows: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_count": len(evidence_rows),
        "scored_count": len(scoring_rows),
        "selected_next_lane_count": len(selected),
        "selected_strategy_id": selected[0]["selected_strategy_id"] if selected else "",
        "heldout_used_for_selection": False,
        "implementation_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.1 Accuracy Strategy Evidence Inventory",
        "",
        "Read-only inventory and scoring of the four candidate levers defined in 10.0. Selection uses dev/OOF evidence only and chooses at most one next analysis lane.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", metrics.get("candidate_count")],
                ["scored_count", metrics.get("scored_count")],
                ["selected_next_lane_count", metrics.get("selected_next_lane_count")],
                ["selected_strategy_id", metrics.get("selected_strategy_id")],
                ["heldout_used_for_selection", metrics.get("heldout_used_for_selection")],
                ["implementation_allowed", metrics.get("implementation_allowed")],
            ]
        ),
        "",
        "## Scoring Matrix",
        "",
        _md_table(
            [["strategy_id", "lever", "strength", "risk", "readiness", "score", "decision"]]
            + [
                [
                    row["strategy_id"],
                    row["candidate_lever"],
                    row["evidence_strength"],
                    row["blocker_risk"],
                    row["readiness"],
                    row["score"],
                    row["decision"],
                ]
                for row in report["scoring_matrix"]
            ]
        ),
        "",
        "## Selected Lane",
        "",
        _md_table(
            [["strategy_id", "lever", "next_stage", "basis", "implementation_allowed"]]
            + [
                [
                    row["selected_strategy_id"],
                    row["selected_candidate_lever"],
                    row["selected_next_stage"],
                    row["selection_basis"],
                    row["implementation_allowed"],
                ]
                for row in report["selected_next_lane"]
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
    parser = argparse.ArgumentParser(description="Stage 10.1 accuracy strategy evidence inventory")
    parser.add_argument("--strategy-10-0", default=str(DEFAULT_STRATEGY))
    parser.add_argument("--decomposition-9x", default=str(DEFAULT_DECOMPOSITION))
    parser.add_argument("--safety-oof", default=str(DEFAULT_SAFETY_OOF))
    parser.add_argument("--compat-whatif", default=str(DEFAULT_COMPAT))
    parser.add_argument("--backlog-handoff", default=str(DEFAULT_BACKLOG))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    strategy = _read_json(Path(args.strategy_10_0))
    decomp = _read_json(Path(args.decomposition_9x))
    safety = _read_json(Path(args.safety_oof))
    compat = _read_json(Path(args.compat_whatif))
    backlog = _read_json(Path(args.backlog_handoff))
    evidence_rows = _evidence_inventory(strategy, decomp, safety, compat, backlog)
    scoring_rows = _scoring_matrix(evidence_rows)
    selected = _selected_lane(scoring_rows, evidence_rows)
    deferred = _deferred_rows(scoring_rows)
    metrics = _metrics(evidence_rows, scoring_rows, selected)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "evidence_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_inventory.csv")),
        "scoring_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_scoring_matrix.csv")),
        "selected_next_lane_csv": str(output_prefix.with_name(output_prefix.name + "_selected_next_lane.csv")),
        "deferred_candidates_csv": str(output_prefix.with_name(output_prefix.name + "_deferred_candidates.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.1 accuracy strategy evidence inventory",
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
            "stage_10_0_strategy": str(Path(args.strategy_10_0)),
            "stage_9_0_decomposition": str(Path(args.decomposition_9x)),
            "stage_7_1_safety_oof": str(Path(args.safety_oof)),
            "stage_7_5_compatibility_whatif": str(Path(args.compat_whatif)),
            "stage_9_32_backlog_handoff": str(Path(args.backlog_handoff)),
        },
        "metrics": metrics,
        "evidence_inventory": evidence_rows,
        "scoring_matrix": scoring_rows,
        "selected_next_lane": selected,
        "deferred_candidates": deferred,
        "decision": (
            "Select S2 ranking_objective_and_feature_strategy as the next read-only analysis lane. It has the largest dev gap evidence "
            "and existing OOF ranking gain/loss signals, while S1 is blocked by provenance/taxonomy evidence, S3 should follow a loss-budget framing, "
            "and S4 remains a parallel data-quality prerequisite. No implementation is allowed in 10.1."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.1 only inventories and scores strategy evidence using dev/OOF. It does not train, tune, patch rules, change ranking, "
            "modify GoalSearcher, use heldout for selection, connect online, relax gates, or convert taxonomy/data-quality backlog into learning evidence."
        ),
        "next_stage": {
            "stage": "10.2 ranking objective and feature evidence review",
            "goal": (
                "Read-only review the selected S2 lane: define candidate feature/objective families, leakage checks, fallback interaction, "
                "and loss-audit slices before any LTR training or ranking change."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
            ],
        },
    }

    _write_csv(
        Path(artifacts["evidence_inventory_csv"]),
        evidence_rows,
        [
            "strategy_id",
            "candidate_lever",
            "dev_signal",
            "oof_signal",
            "positive_evidence",
            "blocking_evidence",
            "evidence_strength",
            "blocker_risk",
            "readiness",
        ],
    )
    _write_csv(
        Path(artifacts["scoring_matrix_csv"]),
        scoring_rows,
        ["strategy_id", "candidate_lever", "evidence_strength", "blocker_risk", "readiness", "score", "decision", "next_stage_if_selected"],
    )
    _write_csv(
        Path(artifacts["selected_next_lane_csv"]),
        selected,
        ["selected_strategy_id", "selected_candidate_lever", "selected_next_stage", "score", "selection_basis", "why_selected", "required_boundary", "implementation_allowed"],
    )
    _write_csv(
        Path(artifacts["deferred_candidates_csv"]),
        deferred,
        ["strategy_id", "candidate_lever", "evidence_strength", "blocker_risk", "readiness", "score", "decision", "next_stage_if_selected"],
    )
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
