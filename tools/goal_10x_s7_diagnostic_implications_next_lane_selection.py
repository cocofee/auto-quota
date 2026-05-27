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
DEFAULT_1059_SUMMARY = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_acceptance_gate_summary.json"
DEFAULT_1059_IMPLICATIONS = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_acceptance_gate_diagnostic_implications.csv"
DEFAULT_1059_SCOPE = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_acceptance_gate_strategy_support_scope.csv"
DEFAULT_1059_NEXT_OPTIONS = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_acceptance_gate_next_options.csv"
DEFAULT_POOL_BOUNDARY = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_candidate_pool_boundary.csv"
DEFAULT_RANK_POSITION = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_rank_position_distribution.csv"
DEFAULT_LOSS_MAP = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_loss_concentration_map.csv"
DEFAULT_CANDIDATE_LANES = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_candidate_lanes.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s7_diagnostic_implications_next_lane_selection"


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


def _first_row(rows: list[dict[str, str]], **filters: str) -> dict[str, str]:
    for row in rows:
        if all(row.get(key) == value for key, value in filters.items()):
            return row
    return {}


def _sum_groups(rows: list[dict[str, str]], **filters: str) -> int:
    total = 0
    for row in rows:
        if all(row.get(key) == value for key, value in filters.items()):
            total += _int(row.get("groups"))
    return total


def _sum_counts(rows: list[dict[str, str]], **filters: str) -> int:
    total = 0
    for row in rows:
        if all(row.get(key) == value for key, value in filters.items()):
            total += _int(row.get("count"))
    return total


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
    implication_review: list[dict[str, Any]],
    candidate_lanes: list[dict[str, Any]],
    selected_lane: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.60 S7 Diagnostic Implications And Next-Lane Selection",
        "",
        "Read-only interpretation of accepted S7 diagnostics and selection of the next non-execution strategy lane.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s7_artifacts_accepted_for_strategy_support", metrics["s7_artifacts_accepted_for_strategy_support"]],
                ["satisfies_learning_reentry", metrics["satisfies_learning_reentry"]],
                ["dev_top80_missing_top1_family_empty", metrics["dev_top80_missing_top1_family_empty"]],
                ["dev_wrong_rank_query_family_empty", metrics["dev_wrong_rank_query_family_empty"]],
                ["selected_next_lane", metrics["selected_next_lane"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Implication Review",
        "",
        _md_table(
            [["finding", "evidence", "implication", "lane_signal"]]
            + [[row["finding"], row["evidence"], row["implication"], row["lane_signal"]] for row in implication_review]
        ),
        "",
        "## Candidate Lanes",
        "",
        _md_table(
            [["lane_id", "decision", "score", "reason"]]
            + [[row["lane_id"], row["decision"], row["score"], row["selection_reason"]] for row in candidate_lanes]
        ),
        "",
        "## Selected Lane",
        "",
        _md_table(
            [["selected_next_lane", "next_stage", "scope", "not_allowed"]]
            + [[row["selected_next_lane"], row["next_stage"], row["scope"], row["not_allowed"]] for row in selected_lane]
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
    parser = argparse.ArgumentParser(description="Select the next non-execution strategy lane from S7 diagnostics")
    parser.add_argument("--summary-1059", default=str(DEFAULT_1059_SUMMARY))
    parser.add_argument("--implications-1059", default=str(DEFAULT_1059_IMPLICATIONS))
    parser.add_argument("--scope-1059", default=str(DEFAULT_1059_SCOPE))
    parser.add_argument("--next-options-1059", default=str(DEFAULT_1059_NEXT_OPTIONS))
    parser.add_argument("--pool-boundary", default=str(DEFAULT_POOL_BOUNDARY))
    parser.add_argument("--rank-position", default=str(DEFAULT_RANK_POSITION))
    parser.add_argument("--loss-map", default=str(DEFAULT_LOSS_MAP))
    parser.add_argument("--candidate-lanes", default=str(DEFAULT_CANDIDATE_LANES))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1059 = _read_json(Path(args.summary_1059))
    implications_1059 = _read_csv(Path(args.implications_1059))
    scope_1059 = _read_csv(Path(args.scope_1059))
    next_options_1059 = _read_csv(Path(args.next_options_1059))
    pool_boundary = _read_csv(Path(args.pool_boundary))
    rank_position = _read_csv(Path(args.rank_position))
    loss_map = _read_csv(Path(args.loss_map))
    candidate_lane_input = _read_csv(Path(args.candidate_lanes))
    m1059 = summary_1059["metrics"]

    dev_recall = _first_row(
        pool_boundary,
        source="dev_oof_recall_boundary",
        split="dev",
        boundary_class="top80_recall_boundary",
    )
    dev_top80_present = _int(dev_recall.get("top80_present_groups"))
    dev_top80_missing = _int(dev_recall.get("top80_missing_groups"))
    dev_top80_recall_rate = _float(dev_recall.get("top80_recall_rate"))
    dev_missing_top1_empty = _sum_groups(
        pool_boundary,
        source="9x_gap_rows",
        split="dev",
        boundary_class="top80_missing",
        reason="top1_family_empty",
    )
    dev_missing_query_empty = _sum_groups(
        pool_boundary,
        source="9x_gap_rows",
        split="dev",
        boundary_class="top80_missing",
        reason="query_family_empty",
    )
    dev_wrong_rank_query_empty = _sum_groups(
        pool_boundary,
        source="9x_gap_rows",
        split="dev",
        boundary_class="top80_present_wrong_rank",
        reason="query_family_empty",
    )
    dev_wrong_rank_same_family = _sum_groups(
        pool_boundary,
        source="9x_gap_rows",
        split="dev",
        boundary_class="top80_present_wrong_rank",
        reason="same_family_or_unknown_wrong_rank",
    )
    top_rank_query_empty = max(
        (
            _int(row.get("count"))
            for row in rank_position
            if row.get("split") == "dev"
            and row.get("rank_bucket") == "rank_2_5"
            and row.get("reason") == "query_family_empty"
        ),
        default=0,
    )
    top_loss = loss_map[0] if loss_map else {}
    top_loss_source = top_loss.get("slice_key", "")
    top_loss_loss = _int(top_loss.get("loss"))

    implication_review = [
        {
            "finding": "candidate_pool_ceiling_visible",
            "evidence": f"dev_top80_present={dev_top80_present}; dev_top80_missing={dev_top80_missing}; dev_top80_recall_rate={dev_top80_recall_rate:.6f}",
            "implication": "Ranking work cannot claim wins on missing candidates; recall/pool ceiling must stay separated.",
            "lane_signal": "do_not_reopen_ranking_execution",
        },
        {
            "finding": "taxonomy_empty_dominates_missing_boundary",
            "evidence": f"dev_top80_missing_top1_family_empty={dev_missing_top1_empty}; dev_top80_missing_query_family_empty={dev_missing_query_empty}",
            "implication": "The largest missing-candidate evidence points to taxonomy/parser coverage gaps, not a clean recall learner.",
            "lane_signal": "prefer_parser_query_normalization_inventory",
        },
        {
            "finding": "query_family_empty_dominates_wrong_rank",
            "evidence": f"dev_wrong_rank_query_family_empty={dev_wrong_rank_query_empty}; top_dev_rank_2_5_query_family_empty={top_rank_query_empty}",
            "implication": "Top visible wrong-rank mass is diagnosable through query-family emptiness before any objective or feature change.",
            "lane_signal": "prefer_parser_query_normalization_inventory",
        },
        {
            "finding": "same_family_wrong_rank_still_large",
            "evidence": f"dev_wrong_rank_same_family_or_unknown={dev_wrong_rank_same_family}",
            "implication": "Same-family ranking remains important, but S2 is parked until independent accepted-OSS positive effect exists.",
            "lane_signal": "keep_s2_parked",
        },
        {
            "finding": "loss_concentration_source_risk",
            "evidence": f"top_loss_slice={top_loss_source}; top_loss={top_loss_loss}",
            "implication": "Loss evidence must remain source/taxonomy separated and cannot freeze a candidate.",
            "lane_signal": "diagnostic_support_only",
        },
    ]

    lane_scores: dict[str, int] = {}
    lane_reasons: dict[str, list[str]] = {}
    for row in candidate_lane_input:
        lane_id = row.get("lane_id", "")
        lane_scores[lane_id] = 0
        lane_reasons[lane_id] = []
        if lane_id == "S6_parser_query_normalization_inventory":
            lane_scores[lane_id] += 4
            lane_reasons[lane_id].append("S7's biggest dev signals are query_family_empty/top1_family_empty.")
            lane_reasons[lane_id].append("Can stay inventory-only and use existing artifacts without owner row mappings.")
        elif lane_id == "S7_rank_position_distribution_diagnostics":
            lane_scores[lane_id] += 1
            lane_reasons[lane_id].append("S7 diagnostics are now accepted, so continuing S7 would duplicate the same support lane.")
        elif lane_id == "S5_measurement_integrity_slice_telemetry":
            lane_scores[lane_id] += 1
            lane_reasons[lane_id].append("S5 support contract is already accepted; useful but not the next largest new signal.")
        elif lane_id == "S8_source_family_independence_registry_design":
            lane_scores[lane_id] += 1
            lane_reasons[lane_id].append("Source independence remains important, but OSS/provenance expansion is paused without owner package.")
        else:
            lane_reasons[lane_id].append("No stronger signal than S6 from current S7 implications.")

    candidate_lanes: list[dict[str, Any]] = []
    for row in candidate_lane_input:
        lane_id = row.get("lane_id", "")
        selected = lane_id == "S6_parser_query_normalization_inventory"
        candidate_lanes.append(
            {
                "lane_id": lane_id,
                "lane_name": row.get("lane_name", ""),
                "decision": "selected_next_non_execution_lane" if selected else "defer",
                "score": lane_scores.get(lane_id, 0),
                "selection_reason": " ".join(lane_reasons.get(lane_id, [])),
                "evidence_requirement": row.get("evidence_requirement", ""),
                "not_allowed": row.get("not_allowed", ""),
            }
        )
    candidate_lanes = sorted(candidate_lanes, key=lambda row: (-_int(row["score"]), row["lane_id"]))

    selected_next_lane = "S6_parser_query_normalization_inventory"
    selected_lane = [
        {
            "selected_next_lane": selected_next_lane,
            "next_stage": "10.61 S6 parser/query normalization inventory design gate",
            "scope": "Read-only decide whether S6 is concrete enough to inventory query_family_empty, top1_family_empty, parser-normalization, and taxonomy-empty failure modes from existing artifacts only.",
            "required_inputs": "existing query text/parser outputs if present, 9.x gap rows, 10.58 S7 rank-position and pool-boundary artifacts, taxonomy disposition labels",
            "not_allowed": "no parser edits, no taxonomy edits, no rule writing, no training, no candidate-matrix expansion, no GoalSearcher change, no heldout/hard selection, no Top1 gain claim",
            "reason": "S7 points to taxonomy/parser empty coverage as the next best non-execution lane and keeps S1/S2/S3/DQ implementation parked.",
        }
    ]

    blocked_actions = [
        {
            "blocked_action": "reopen_s2_execution_from_s7",
            "reason": "S7 diagnostics do not provide independent accepted-OSS positive effect evidence.",
            "allowed_after": "future S2 re-entry review with accepted-OSS non-generated positive net and independent source support",
        },
        {
            "blocked_action": "change_candidate_pool_or_retrieval",
            "reason": "Candidate-pool boundary is explanatory only and does not define a retrieval implementation.",
            "allowed_after": "future implementation plan with explicit go",
        },
        {
            "blocked_action": "edit_parser_or_taxonomy_in_s6",
            "reason": "The selected next lane is inventory/design-gate only.",
            "allowed_after": "future DQ or parser implementation plan with explicit go and accepted mappings",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "10.60 selects the next read-only lane from dev/OOF diagnostics and prior lane boundaries only.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "implication_review_csv": str(output_prefix.with_name(output_prefix.name + "_implication_review.csv")),
        "candidate_next_lanes_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_next_lanes.csv")),
        "selected_next_lane_csv": str(output_prefix.with_name(output_prefix.name + "_selected_next_lane.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1059["stage"],
        "s7_artifacts_accepted_for_strategy_support": bool(m1059.get("s7_artifacts_accepted_for_strategy_support")),
        "satisfies_learning_reentry": False,
        "source_implication_count": len(implications_1059),
        "strategy_support_scope_count": len(scope_1059),
        "source_next_option_count": len(next_options_1059),
        "candidate_lane_count": len(candidate_lanes),
        "dev_top80_present_groups": dev_top80_present,
        "dev_top80_missing_groups": dev_top80_missing,
        "dev_top80_recall_rate": round(dev_top80_recall_rate, 6),
        "dev_top80_missing_top1_family_empty": dev_missing_top1_empty,
        "dev_top80_missing_query_family_empty": dev_missing_query_empty,
        "dev_wrong_rank_query_family_empty": dev_wrong_rank_query_empty,
        "dev_wrong_rank_same_family_or_unknown": dev_wrong_rank_same_family,
        "top_rank_position_query_family_empty": top_rank_query_empty,
        "top_loss_slice": top_loss_source,
        "top_loss": top_loss_loss,
        "selected_next_lane": selected_next_lane,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.60 S7 diagnostic implications and next-lane selection",
        "read_only": True,
        "strategy_lane_selection_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "S7 diagnostics are interpreted as strategy support only. The dominant actionable signals are taxonomy/parser-empty coverage gaps "
            "in dev top80_missing and dev wrong-rank buckets, so the next lane is S6_parser_query_normalization_inventory. "
            "S2 ranking execution remains parked, and S7 does not authorize training, retrieval changes, candidate selection, heldout/hard validation, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.60 only selects the next non-execution strategy lane from existing diagnostic artifacts. It does not train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit feature whitelists, implement DQ/parser fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.61 S6 parser/query normalization inventory design gate",
            "goal": "Read-only decide whether S6 can inventory query_family_empty/top1_family_empty/parser-normalization/taxonomy-empty failure modes from existing artifacts only.",
            "default": "inventory design gate only; no parser edit, taxonomy edit, training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(
        Path(artifacts["implication_review_csv"]),
        implication_review,
        ["finding", "evidence", "implication", "lane_signal"],
    )
    _write_csv(
        Path(artifacts["candidate_next_lanes_csv"]),
        candidate_lanes,
        ["lane_id", "lane_name", "decision", "score", "selection_reason", "evidence_requirement", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["selected_next_lane_csv"]),
        selected_lane,
        ["selected_next_lane", "next_stage", "scope", "required_inputs", "not_allowed", "reason"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, implication_review, candidate_lanes, selected_lane)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
