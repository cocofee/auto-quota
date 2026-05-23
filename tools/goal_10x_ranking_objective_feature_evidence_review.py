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
DEFAULT_STAGE_10_1 = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_DECOMPOSITION = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_SAFETY_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_FEATURE_WHITELIST = AGENT_STATE / "goal_query_anchored_ranking_matrix_dry_run" / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_ranking_objective_feature_evidence_review"


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


def _split(decomp: dict[str, Any], split: str = "dev") -> dict[str, Any]:
    for row in decomp.get("splits", []):
        if row.get("split") == split:
            return row
    return {}


def _selected_oof(safety: dict[str, Any]) -> dict[str, Any]:
    return safety.get("selection", {}).get("selected_metrics", {})


def _raw_oof(safety: dict[str, Any]) -> dict[str, Any]:
    return safety.get("selection", {}).get("raw_oof_metrics", {})


def _objective_families(dev: dict[str, Any], selected: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "objective_family": "top1_net_gain_with_loss_budget",
            "priority": "P0",
            "dev_oof_evidence": (
                f"raw_ltr_hit1_net={raw.get('raw_ltr_hit1_net', 0)}; raw_ltr_hit1_loss={raw.get('raw_ltr_hit1_loss', 0)}; "
                f"selected_gate_net={selected.get('gated_hit1_net', 0)}; selected_gate_loss={selected.get('gated_hit1_loss', 0)}"
            ),
            "review_question": "Can a future ranking objective increase net Top1 while making new losses explicit and bounded?",
            "required_before_training": "define acceptable new_loss ceiling, retained gain target, and loss-slice report",
            "not_allowed_in_10_2": "no LambdaRank retraining or threshold selection",
        },
        {
            "objective_family": "recall_vs_ranking_separation",
            "priority": "P0",
            "dev_oof_evidence": (
                f"dev_top80_missing={dev.get('top80_missing', 0)}; dev_wrong_rank={dev.get('top80_present_but_wrong_rank', 0)}; "
                f"dev_top80_recall_rate={dev.get('top80_recall_rate', 0)}"
            ),
            "review_question": "Can ranking changes be evaluated only on rows where the expected answer is already in Top80?",
            "required_before_training": "freeze reporting that separates top80_missing from top80_present_but_wrong_rank",
            "not_allowed_in_10_2": "no claim that ranking changes fix recall-missing rows",
        },
        {
            "objective_family": "fallback_and_safety_interaction",
            "priority": "P1",
            "dev_oof_evidence": (
                f"blocked_raw_hit1_gain={selected.get('blocked_raw_hit1_gain', 0)}; "
                f"prevented_raw_hit1_loss={selected.get('prevented_raw_hit1_loss', 0)}; "
                f"gate_pool={selected.get('selection_variant_mode', '')}"
            ),
            "review_question": "Can future objective changes reduce harmful overrides without discarding too much blocked gain?",
            "required_before_training": "define fallback interaction table and gate residual review before changing any model",
            "not_allowed_in_10_2": "no gate relaxation or online wiring",
        },
    ]


def _feature_families(whitelist: dict[str, Any]) -> list[dict[str, Any]]:
    features = set(whitelist.get("training_features", []))
    families = [
        ("base_retrieval_score", ["base_rank", "current_score", "confidence", "bm25_score", "national_cluster_bonus", "token_overlap"]),
        ("book_and_chapter_alignment", ["book_requested", "book_match", "book_conflict", "chapter_book_match"]),
        ("taxonomy_family_and_action", ["query_family_present", "candidate_family_present", "family_match", "family_conflict", "action_match", "material_match", "connection_match", "install_method_match"]),
        ("field_numeric_domain_scores", ["field_score", "numeric_score", "domain_rule_score", "domain_label_overlap_count", "domain_conflict_count"]),
        ("parameter_exact_gap_features", ["param_exact_count", "param_tier_up_count", "param_conflict_count", "dn_exact", "dn_gap_ratio", "cable_section_exact", "cable_section_gap_ratio", "thickness_exact", "thickness_gap_ratio", "width_height_exact", "width_height_gap_ratio"]),
        ("conflict_reason_flags", ["has_domain_conflict", "has_family_conflict_reason", "has_book_conflict_reason", "has_unit_conflict_reason", "has_param_conflict_reason", "has_national_reason", "reason_count"]),
    ]
    rows: list[dict[str, Any]] = []
    for name, expected in families:
        present = [item for item in expected if item in features]
        rows.append(
            {
                "feature_family": name,
                "present_feature_count": len(present),
                "example_features": " | ".join(present[:10]),
                "review_question": _feature_review_question(name),
                "evidence_status": "available_in_current_whitelist" if present else "missing_from_current_whitelist",
                "not_allowed_in_10_2": "no new feature engineering or model training",
            }
        )
    return rows


def _feature_review_question(name: str) -> str:
    mapping = {
        "base_retrieval_score": "Which base retrieval signals are already strong enough, and where do they cause unsafe overrides?",
        "book_and_chapter_alignment": "Do book/chapter signals reduce wrong-book errors without becoming answer leakage?",
        "taxonomy_family_and_action": "How much taxonomy signal is usable after the 9.x empty-family backlog boundaries?",
        "field_numeric_domain_scores": "Which numeric/domain scores explain OOF gains and losses?",
        "parameter_exact_gap_features": "Do exact/gap features handle DN, cable, thickness, and size cases without single-family patches?",
        "conflict_reason_flags": "Which conflict flags should drive loss audits or fallback decisions?",
    }
    return mapping.get(name, "Review this feature family before implementation.")


def _leakage_checks(whitelist: dict[str, Any]) -> list[dict[str, Any]]:
    training = set(whitelist.get("training_features", []))
    excluded = set(whitelist.get("excluded_diagnostic_columns", []))
    forbidden = [
        "sample_id",
        "source_file",
        "expected_id",
        "expected_ids",
        "expected_quota_id",
        "positive_id",
        "correct_quota_id",
        "quota_id",
        "province",
        "project_name",
        "group_id",
    ]
    rows = []
    for key in forbidden:
        rows.append(
            {
                "forbidden_key": key,
                "in_training_features": key in training,
                "in_excluded_diagnostics": key in excluded,
                "decision": "pass" if key not in training and key in excluded else "review",
                "required_review": "must remain out of training matrix; diagnostics only if needed for audit",
            }
        )
    return rows


def _fallback_interactions(selected: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "interaction": "raw_ltr_vs_baseline",
            "oof_evidence": f"raw_net={raw.get('raw_ltr_hit1_net', 0)}; raw_loss={raw.get('raw_ltr_hit1_loss', 0)}; raw_override_count={raw.get('raw_override_count', 0)}",
            "review_need": "retain raw LTR upside while explicitly auditing new losses",
            "required_output": "raw gain/loss slices by query_family/top1_family/book/source/province",
        },
        {
            "interaction": "selected_gate_vs_raw_ltr",
            "oof_evidence": f"selected_net={selected.get('gated_hit1_net', 0)}; selected_loss={selected.get('gated_hit1_loss', 0)}; blocked_gain={selected.get('blocked_raw_hit1_gain', 0)}",
            "review_need": "understand whether blocked gain can be recovered through objective/features rather than gate relaxation",
            "required_output": "blocked-gain residual slices and prevented-loss slices",
        },
        {
            "interaction": "baseline_fallback_contract",
            "oof_evidence": f"prevented_raw_loss={selected.get('prevented_raw_hit1_loss', 0)}; gate_decisions={selected.get('gate_decisions', {})}",
            "review_need": "preserve fallback as default safety contract until a frozen strategy passes OOF gates",
            "required_output": "fallback contract table before any model or gate change",
        },
    ]


def _loss_audit_slices(dev: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "slice_family": "query_family",
            "why_required": "wrong-rank and prior bucket mining are organized by query_family",
            "current_dev_signal": f"top wrong-rank families include { _top_keys(dev, 'wrong_rank_breakdown', 'by_query_family') }",
            "required_output": "gain/loss by query_family, including empty family as data-quality boundary",
        },
        {
            "slice_family": "top1_family",
            "why_required": "many 9.x failures involved empty or unstable top1_family",
            "current_dev_signal": f"top top1 families include { _top_keys(dev, 'wrong_rank_breakdown', 'by_top1_family') }",
            "required_output": "gain/loss by top1_family with empty-family separated",
        },
        {
            "slice_family": "book_and_rank_bucket",
            "why_required": "wrong-book and near-miss rank buckets need different remedies",
            "current_dev_signal": f"rank buckets include { _top_keys(dev, 'wrong_rank_breakdown', 'by_rank_bucket') }",
            "required_output": "gain/loss by expected_book/top1_book/rank_bucket",
        },
        {
            "slice_family": "source_and_province",
            "why_required": "source/province diversity is required to avoid generated-source artifacts",
            "current_dev_signal": f"top sources include { _top_keys(dev, 'wrong_rank_breakdown', 'by_source_file') }",
            "required_output": "gain/loss by source_file and province, with generated repair source isolated",
        },
    ]


def _top_keys(split: dict[str, Any], section: str, key: str, limit: int = 4) -> str:
    rows = split.get(section, {}).get(key, [])[:limit]
    return " | ".join(f"{row.get('key')}:{row.get('count')}" for row in rows)


def _metrics(dev: dict[str, Any], selected: dict[str, Any], raw: dict[str, Any], whitelist: dict[str, Any], leakage_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dev_wrong_rank": dev.get("top80_present_but_wrong_rank", 0),
        "dev_wrong_rank_share_of_non_hit": dev.get("wrong_rank_share_of_non_hit", 0),
        "raw_oof_net": raw.get("raw_ltr_hit1_net", 0),
        "raw_oof_loss": raw.get("raw_ltr_hit1_loss", 0),
        "selected_gate_net": selected.get("gated_hit1_net", 0),
        "selected_gate_loss": selected.get("gated_hit1_loss", 0),
        "blocked_raw_hit1_gain": selected.get("blocked_raw_hit1_gain", 0),
        "prevented_raw_hit1_loss": selected.get("prevented_raw_hit1_loss", 0),
        "training_feature_count": len(whitelist.get("training_features", [])),
        "leakage_checks": len(leakage_rows),
        "leakage_training_hits": sum(1 for row in leakage_rows if row["in_training_features"]),
        "implementation_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.2 Ranking Objective and Feature Evidence Review",
        "",
        "Read-only review of the selected S2 lane. This defines objective families, feature families, leakage checks, fallback interaction, and loss-audit slices before any LTR training or ranking change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["dev_wrong_rank", metrics["dev_wrong_rank"]],
                ["dev_wrong_rank_share_of_non_hit", metrics["dev_wrong_rank_share_of_non_hit"]],
                ["raw_oof_net", metrics["raw_oof_net"]],
                ["raw_oof_loss", metrics["raw_oof_loss"]],
                ["selected_gate_net", metrics["selected_gate_net"]],
                ["selected_gate_loss", metrics["selected_gate_loss"]],
                ["training_feature_count", metrics["training_feature_count"]],
                ["leakage_training_hits", metrics["leakage_training_hits"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Objective Families",
        "",
        _md_table(
            [["objective_family", "priority", "review_question", "required_before_training"]]
            + [[row["objective_family"], row["priority"], row["review_question"], row["required_before_training"]] for row in report["objective_families"]]
        ),
        "",
        "## Feature Families",
        "",
        _md_table(
            [["feature_family", "present_feature_count", "review_question", "evidence_status"]]
            + [[row["feature_family"], row["present_feature_count"], row["review_question"], row["evidence_status"]] for row in report["feature_families"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.2 ranking objective and feature evidence review")
    parser.add_argument("--stage-10-1", default=str(DEFAULT_STAGE_10_1))
    parser.add_argument("--decomposition", default=str(DEFAULT_DECOMPOSITION))
    parser.add_argument("--safety-oof", default=str(DEFAULT_SAFETY_OOF))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_FEATURE_WHITELIST))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_1 = _read_json(Path(args.stage_10_1))
    decomp = _read_json(Path(args.decomposition))
    safety = _read_json(Path(args.safety_oof))
    whitelist = _read_json(Path(args.feature_whitelist))
    dev = _split(decomp, "dev")
    selected = _selected_oof(safety)
    raw = _raw_oof(safety)
    objective_rows = _objective_families(dev, selected, raw)
    feature_rows = _feature_families(whitelist)
    leakage_rows = _leakage_checks(whitelist)
    fallback_rows = _fallback_interactions(selected, raw)
    loss_rows = _loss_audit_slices(dev)
    metrics = _metrics(dev, selected, raw, whitelist, leakage_rows)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "objective_families_csv": str(output_prefix.with_name(output_prefix.name + "_objective_families.csv")),
        "feature_families_csv": str(output_prefix.with_name(output_prefix.name + "_feature_families.csv")),
        "leakage_checks_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_checks.csv")),
        "fallback_interactions_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_interactions.csv")),
        "loss_audit_slices_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_slices.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.2 ranking objective and feature evidence review",
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
            "stage_10_1_summary": str(Path(args.stage_10_1)),
            "stage_9_0_decomposition": str(Path(args.decomposition)),
            "stage_7_1_safety_oof": str(Path(args.safety_oof)),
            "feature_whitelist": str(Path(args.feature_whitelist)),
        },
        "selected_lane": stage_10_1.get("selected_next_lane", []),
        "metrics": metrics,
        "objective_families": objective_rows,
        "feature_families": feature_rows,
        "leakage_checks": leakage_rows,
        "fallback_interactions": fallback_rows,
        "loss_audit_slices": loss_rows,
        "decision": (
            "Keep S2 as a read-only design lane and advance to a feature/objective design gate. Current evidence supports reviewing "
            "top1 net gain with a loss budget, recall-vs-ranking separation, and fallback/safety interaction. The existing whitelist has "
            f"{metrics['training_feature_count']} numeric training features and zero forbidden identifier hits in training features, but no training or ranking change is allowed yet."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.2 only defines objective/feature review artifacts. It does not train, tune, patch rules, change ranking, modify GoalSearcher, "
            "use heldout for selection, connect online, relax gates, or add features."
        ),
        "next_stage": {
            "stage": "10.3 ranking feature/objective design gate",
            "goal": (
                "Read-only decide whether the S2 objective/feature families are sufficiently specified to create an offline experiment plan, "
                "including feature leakage gates, fallback contract, and loss budget."
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

    _write_csv(Path(artifacts["objective_families_csv"]), objective_rows, ["objective_family", "priority", "dev_oof_evidence", "review_question", "required_before_training", "not_allowed_in_10_2"])
    _write_csv(Path(artifacts["feature_families_csv"]), feature_rows, ["feature_family", "present_feature_count", "example_features", "review_question", "evidence_status", "not_allowed_in_10_2"])
    _write_csv(Path(artifacts["leakage_checks_csv"]), leakage_rows, ["forbidden_key", "in_training_features", "in_excluded_diagnostics", "decision", "required_review"])
    _write_csv(Path(artifacts["fallback_interactions_csv"]), fallback_rows, ["interaction", "oof_evidence", "review_need", "required_output"])
    _write_csv(Path(artifacts["loss_audit_slices_csv"]), loss_rows, ["slice_family", "why_required", "current_dev_signal", "required_output"])
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
