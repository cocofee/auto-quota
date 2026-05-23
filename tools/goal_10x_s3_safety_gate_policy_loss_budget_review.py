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
DEFAULT_STAGE_10_11 = AGENT_STATE / "goal_10x_broader_strategy_reentry_review_summary.json"
DEFAULT_SAFETY_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_SAFETY_RESIDUAL = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_residual_audit_summary.json"
DEFAULT_COMPAT_WHATIF = AGENT_STATE / "goal_family_compatibility_whatif_summary.json"
DEFAULT_COMPAT_RESIDUAL = AGENT_STATE / "goal_family_compatibility_residual_audit_summary.json"
DEFAULT_FREEZE_NARROW_REVIEW = AGENT_STATE / "goal_family_compatibility_freeze_narrow_residual_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_safety_gate_policy_loss_budget_review"


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


def _selected_metrics(safety_oof: dict[str, Any]) -> dict[str, Any]:
    return safety_oof.get("selection", {}).get("selected_metrics", {})


def _raw_metrics(safety_oof: dict[str, Any]) -> dict[str, Any]:
    return safety_oof.get("selection", {}).get("raw_oof_metrics", {})


def _compat_split(compat: dict[str, Any], split: str = "dev_oof") -> dict[str, Any]:
    for row in compat.get("split_metrics", []):
        if row.get("split") == split:
            return row
    return {}


def _safety_residual_summary(safety_residual: dict[str, Any]) -> dict[str, Any]:
    return safety_residual.get("summary", {})


def _compat_dev_residual(compat_residual: dict[str, Any]) -> dict[str, Any]:
    for row in compat_residual.get("split_summary", []):
        if row.get("split") == "dev_oof":
            return row
    return {}


def _freeze_narrow_dev(freeze_narrow: dict[str, Any]) -> dict[str, Any]:
    for row in freeze_narrow.get("split_summary", []):
        if row.get("split") == "dev_oof":
            return row
    return {}


def _policy_contract(
    stage_10_11: dict[str, Any],
    safety_oof: dict[str, Any],
    compat: dict[str, Any],
    compat_residual: dict[str, Any],
) -> list[dict[str, Any]]:
    selected = _selected_metrics(safety_oof)
    raw = _raw_metrics(safety_oof)
    compat_dev = _compat_split(compat)
    compat_dev_residual = _compat_dev_residual(compat_residual)
    selected_lane = stage_10_11.get("metrics", {}).get("selected_next_strategy_id")
    return [
        {
            "contract_id": "S3_SCOPE",
            "policy_area": "lane_scope",
            "evidence": f"selected_next_strategy_id={selected_lane}",
            "required_policy": "S3 is a read-only safety gate and compatibility calibration policy review.",
            "not_allowed": "no threshold change, no rule patch, no ranking change, no GoalSearcher change",
        },
        {
            "contract_id": "S3_CURRENT_GATE_REFERENCE",
            "policy_area": "current_selected_gate",
            "evidence": (
                f"selected_variant={selected.get('variant')}; gated_hit1_net={selected.get('gated_hit1_net')}; "
                f"gated_hit1_loss={selected.get('gated_hit1_loss')}; blocked_raw_hit1_gain={selected.get('blocked_raw_hit1_gain')}; "
                f"prevented_raw_hit1_loss={selected.get('prevented_raw_hit1_loss')}"
            ),
            "required_policy": "Treat the current selected gate as the frozen comparator, not as a tunable threshold.",
            "not_allowed": "no reselecting margin or mode in 10.12",
        },
        {
            "contract_id": "S3_RAW_UPSIDE_REFERENCE",
            "policy_area": "raw_ltr_ceiling",
            "evidence": (
                f"raw_ltr_hit1_net={raw.get('raw_ltr_hit1_net')}; raw_ltr_hit1_gain={raw.get('raw_ltr_hit1_gain')}; "
                f"raw_ltr_hit1_loss={raw.get('raw_ltr_hit1_loss')}"
            ),
            "required_policy": "Use raw LTR as upside and loss-risk reference only.",
            "not_allowed": "no promoting raw LTR without loss budget and residual slices",
        },
        {
            "contract_id": "S3_COMPAT_REFERENCE",
            "policy_area": "compatibility_layer",
            "evidence": (
                f"compat_rescued_blocked_gain={compat_dev.get('rescued_blocked_gain')}; "
                f"compat_new_residual_loss={compat_dev.get('new_residual_loss')}; "
                f"blocked_gain_remaining={compat_dev.get('blocked_gain_remaining')}; "
                f"saved_loss_retained={compat_dev.get('saved_loss_retained')}"
            ),
            "required_policy": "Compatibility may be reviewed as relation-level rescue/loss policy, not as broad gate relaxation.",
            "not_allowed": "no single-family or single-relation patch claimed as general calibration",
        },
        {
            "contract_id": "S3_RELATION_SCOPE",
            "policy_area": "relation_freeze_narrow",
            "evidence": (
                f"freeze_candidate_relations={compat_residual.get('recommendation', {}).get('freeze_candidate_relations')}; "
                f"narrow_candidate_relations={compat_residual.get('recommendation', {}).get('narrow_candidate_relations')}; "
                f"dev_neutral_per_rescue={compat_dev_residual.get('neutral_per_rescue')}"
            ),
            "required_policy": "Any future S3 design must separate freeze, narrow, low-support, suspect, and out-of-scope relations.",
            "not_allowed": "no collapsing all compatibility relations into one permissive gate",
        },
    ]


def _oof_requirements(
    safety_oof: dict[str, Any],
    safety_residual: dict[str, Any],
    compat: dict[str, Any],
    freeze_narrow: dict[str, Any],
) -> list[dict[str, Any]]:
    selected = _selected_metrics(safety_oof)
    safety_summary = _safety_residual_summary(safety_residual)
    compat_dev = _compat_split(compat)
    freeze_dev = _freeze_narrow_dev(freeze_narrow)
    return [
        {
            "requirement_id": "OOF_ONLY_SELECTION",
            "required_evidence": "All S3 policy selection must use dev_oof only.",
            "current_reference": f"selected_split={selected.get('split')}; heldout_hard_status={compat.get('heldout_hard_status')}",
            "pass_condition": "heldout/hard are validation-only after policy freeze",
            "blocked_if_missing": "selection contaminated by heldout/hard",
        },
        {
            "requirement_id": "GAIN_LOSS_BALANCE",
            "required_evidence": "Report rescued gain, new loss, saved loss, blocked gain, and net vs gated.",
            "current_reference": (
                f"gated_hit1_net={selected.get('gated_hit1_net')}; gated_hit1_loss={selected.get('gated_hit1_loss')}; "
                f"compat_net_vs_gated={compat_dev.get('whatif_net_vs_gated')}; compat_new_residual_loss={compat_dev.get('new_residual_loss')}"
            ),
            "pass_condition": "positive OOF net vs selected gate with explicit new-loss accounting",
            "blocked_if_missing": "score-only policy claim",
        },
        {
            "requirement_id": "RESIDUAL_DIAGNOSIS",
            "required_evidence": "Separate residual loss feature gaps from gate-too-broad losses and blocked-gain feature gaps.",
            "current_reference": (
                f"residual_loss_total={safety_summary.get('residual_loss_total')}; "
                f"blocked_gain_total={safety_summary.get('blocked_gain_total')}; "
                f"residual_loss_main_read={safety_summary.get('residual_loss_main_read')}; "
                f"blocked_gain_main_read={safety_summary.get('blocked_gain_main_read')}"
            ),
            "pass_condition": "residual diagnosis table exists before any future threshold or policy proposal",
            "blocked_if_missing": "cannot distinguish safe rescue from new loss",
        },
        {
            "requirement_id": "RELATION_LEVEL_AUDIT",
            "required_evidence": "Report relation-level freeze/narrow/suspect/low-support decisions.",
            "current_reference": (
                f"clean_retained_gain={freeze_dev.get('clean_retained_gain')}; "
                f"clean_narrow_block={freeze_dev.get('clean_narrow_block')}; "
                f"neutral_review={freeze_dev.get('neutral_review')}; "
                f"new_residual_loss={freeze_dev.get('new_residual_loss')}"
            ),
            "pass_condition": "relation policy is auditable by relation and split",
            "blocked_if_missing": "single relation artifact risk",
        },
        {
            "requirement_id": "SOURCE_AND_TAXONOMY_VISIBILITY",
            "required_evidence": "Report source_file, province, query_family, raw_top_family, book, rank bucket, and taxonomy-empty slices.",
            "current_reference": "9.x taxonomy and source provenance issues remain outside learning evidence",
            "pass_condition": "generated repair/source dominated artifacts are isolated",
            "blocked_if_missing": "source/taxonomy artifact may be mistaken for calibration gain",
        },
    ]


def _loss_budget(safety_oof: dict[str, Any], compat: dict[str, Any], freeze_narrow: dict[str, Any]) -> list[dict[str, Any]]:
    selected = _selected_metrics(safety_oof)
    raw = _raw_metrics(safety_oof)
    compat_dev = _compat_split(compat)
    freeze_dev = _freeze_narrow_dev(freeze_narrow)
    return [
        {
            "budget_id": "LB_NEW_LOSS_CEILING",
            "metric": "new_residual_loss",
            "reference_value": compat_dev.get("new_residual_loss"),
            "future_ceiling": "must be <= selected_gate_loss_reference and explicitly reviewed; zero preferred for relation-level compatibility",
            "reason": f"selected_gate_loss_reference={selected.get('gated_hit1_loss')}; raw_loss_reference={raw.get('raw_ltr_hit1_loss')}",
            "promotion_block_if": "new losses are hidden, unbucketed, or exceed the frozen loss ceiling",
        },
        {
            "budget_id": "LB_RESCUE_GAIN_FLOOR",
            "metric": "rescued_blocked_gain",
            "reference_value": compat_dev.get("rescued_blocked_gain"),
            "future_ceiling": "must remain positive on dev_oof and not be dominated by one unsupported relation",
            "reason": f"blocked_gain_remaining={compat_dev.get('blocked_gain_remaining')}",
            "promotion_block_if": "rescue gain is zero, source-dominated, or single-relation only",
        },
        {
            "budget_id": "LB_SAVED_LOSS_RETENTION",
            "metric": "saved_loss_retained",
            "reference_value": compat_dev.get("saved_loss_retained"),
            "future_ceiling": "must retain prevented-loss behavior from the selected safety gate",
            "reason": f"selected_prevented_raw_hit1_loss={selected.get('prevented_raw_hit1_loss')}",
            "promotion_block_if": "policy bypasses fallback or turns saved losses into new losses",
        },
        {
            "budget_id": "LB_NEUTRAL_OVERRIDE_VISIBILITY",
            "metric": "neutral_review",
            "reference_value": freeze_dev.get("neutral_review"),
            "future_ceiling": "neutral overrides must be logged, reviewed, and bounded per relation",
            "reason": f"clean_retained_gain={freeze_dev.get('clean_retained_gain')}; clean_narrow_block={freeze_dev.get('clean_narrow_block')}",
            "promotion_block_if": "neutral overrides are omitted or dominate relation evidence",
        },
        {
            "budget_id": "LB_NET_VS_SELECTED_GATE",
            "metric": "whatif_net_vs_gated",
            "reference_value": compat_dev.get("whatif_net_vs_gated"),
            "future_ceiling": "must report net vs selected gate, raw LTR, and baseline separately",
            "reason": (
                f"whatif_net_vs_baseline={compat_dev.get('whatif_net_vs_baseline')}; "
                f"whatif_net_vs_raw_ltr={compat_dev.get('whatif_net_vs_raw_ltr')}"
            ),
            "promotion_block_if": "net gain is reported without comparator separation",
        },
    ]


def _residual_slices(safety_residual: dict[str, Any], compat_residual: dict[str, Any], freeze_narrow: dict[str, Any]) -> list[dict[str, Any]]:
    safety_summary = _safety_residual_summary(safety_residual)
    compat_dev = _compat_dev_residual(compat_residual)
    freeze_dev = _freeze_narrow_dev(freeze_narrow)
    return [
        {
            "slice_id": "SAFETY_RESIDUAL_OUTCOME",
            "slice_dimension": "outcome",
            "required_buckets": "blocked_gain, residual_loss, saved_loss, passed_gain",
            "current_signal": (
                f"blocked_gain_total={safety_summary.get('blocked_gain_total')}; "
                f"residual_loss_total={safety_summary.get('residual_loss_total')}"
            ),
            "why_required": "Separates gate conservatism from gate looseness.",
        },
        {
            "slice_id": "SAFETY_DIAGNOSIS",
            "slice_dimension": "diagnosis",
            "required_buckets": "feature_gap, gate_coarse, gate_too_broad, param/subtype missing",
            "current_signal": (
                f"blocked_gain_feature_gap={safety_summary.get('blocked_gain_feature_gap')}; "
                f"blocked_gain_gate_coarse={safety_summary.get('blocked_gain_gate_coarse')}; "
                f"residual_loss_feature_gap={safety_summary.get('residual_loss_feature_gap')}; "
                f"residual_loss_gate_broad={safety_summary.get('residual_loss_gate_broad')}"
            ),
            "why_required": "Prevents threshold tuning from masking missing feature or taxonomy evidence.",
        },
        {
            "slice_id": "COMPAT_RELATION",
            "slice_dimension": "relation_or_pair",
            "required_buckets": "freeze_retained, narrow_retained, narrow_blocked, low_support_blocked, suspect_relation_blocked, unrelated_residual_gap",
            "current_signal": (
                f"dev_top_relations={compat_dev.get('top_relations')}; "
                f"freeze_clean_retained_gain={freeze_dev.get('clean_retained_gain')}"
            ),
            "why_required": "Compatibility must stay relation-level and auditable.",
        },
        {
            "slice_id": "SOURCE_PROVINCE",
            "slice_dimension": "source_file and province",
            "required_buckets": "source_file, province, generated repair source, independent trace",
            "current_signal": "prior residual audits expose source/province slices; 9.x source dominance remains a known artifact risk",
            "why_required": "Blocks source-dominated or province-specific calibration claims.",
        },
        {
            "slice_id": "TAXONOMY_FAMILY",
            "slice_dimension": "query_family and raw_ltr_top_family",
            "required_buckets": "query_family, raw_ltr_top_family, top1_family_empty, query_family_empty",
            "current_signal": "9.x backlog records taxonomy empty and label mixture as data-quality issues",
            "why_required": "Keeps taxonomy/data-quality backlog outside learning evidence unless re-entry criteria are met.",
        },
        {
            "slice_id": "RANK_BOOK_MARGIN",
            "slice_dimension": "rank bucket, book, margin bucket, gate reason",
            "required_buckets": "raw_rank_bucket, raw_ltr_top_book, margin_bucket, gate_reason",
            "current_signal": "safety residual audit already records margin and rank buckets",
            "why_required": "Distinguishes near-miss rescues from broad high-margin overrides.",
        },
    ]


def _freeze_validation_boundaries() -> list[dict[str, Any]]:
    return [
        {
            "boundary_id": "FREEZE_BEFORE_VALIDATION",
            "policy": "Freeze S3 policy, relation scope, loss budget, and residual slices before heldout/hard validation.",
            "allowed_split": "dev_oof for selection; heldout/hard validation only after freeze",
            "not_allowed": "no heldout/hard threshold, relation, objective, or candidate selection",
        },
        {
            "boundary_id": "DEFAULT_OFF_CONTRACT",
            "policy": "Any future switch or wrapper remains default-off until a separate integration readiness review.",
            "allowed_split": "offline eval-only",
            "not_allowed": "no online integration, no GoalSearcher change, no production default-on",
        },
        {
            "boundary_id": "NO_THRESHOLD_CHANGE_IN_10_12",
            "policy": "10.12 defines requirements and budgets only.",
            "allowed_split": "read-only summaries and existing audit artifacts",
            "not_allowed": "no margin/mode retuning, no rule patch, no gate relaxation",
        },
        {
            "boundary_id": "FALLBACK_RETENTION",
            "policy": "Baseline fallback and prevented-loss retention must remain visible in any future S3 design.",
            "allowed_split": "dev_oof loss audit and later frozen validation",
            "not_allowed": "no bypassing fallback to chase net gain",
        },
        {
            "boundary_id": "DATA_QUALITY_SEPARATION",
            "policy": "Taxonomy/provenance issues may be logged as blockers or prerequisites, not as Top1 gain evidence.",
            "allowed_split": "diagnostic reporting only",
            "not_allowed": "no converting source or taxonomy backlog rows into calibration learning evidence",
        },
    ]


def _design_gate_checks(
    policy: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    budgets: list[dict[str, Any]],
    slices: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "check": "policy_contract_defined",
            "status": "pass" if len(policy) >= 5 else "fail",
            "observed": f"policy_contract_count={len(policy)}",
            "required": "scope, comparator, raw upside, compatibility, and relation scope policies",
        },
        {
            "check": "oof_requirements_defined",
            "status": "pass" if len(requirements) >= 5 else "fail",
            "observed": f"oof_requirement_count={len(requirements)}",
            "required": "OOF-only selection, gain/loss, residual diagnosis, relation audit, source/taxonomy visibility",
        },
        {
            "check": "loss_budget_defined",
            "status": "pass" if len(budgets) >= 5 else "fail",
            "observed": f"loss_budget_count={len(budgets)}",
            "required": "new loss, rescue gain, saved loss, neutral override, and comparator net budgets",
        },
        {
            "check": "residual_slices_defined",
            "status": "pass" if len(slices) >= 6 else "fail",
            "observed": f"residual_slice_count={len(slices)}",
            "required": "outcome, diagnosis, relation, source/province, taxonomy, rank/book/margin slices",
        },
        {
            "check": "freeze_validation_boundaries_defined",
            "status": "pass" if len(boundaries) >= 5 else "fail",
            "observed": f"freeze_validation_boundary_count={len(boundaries)}",
            "required": "freeze-before-validation, default-off, no-threshold-change, fallback, and data-quality boundaries",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "10.12 defines policy and loss budget only.",
            "allowed_after": "separate future OOF calibration experiment plan and explicit implementation approval, if ever reached",
        },
        {
            "blocked_action": "enable_or_connect_compatibility_switch",
            "reason": "switch work remains default-off and outside this review.",
            "allowed_after": "separate integration readiness review after frozen validation",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 review is not model training or tuning.",
            "allowed_after": "separate explicitly opened execution stage",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no implementation is opened by a policy/loss-budget review.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard are validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "convert_taxonomy_or_source_backlog_to_learning_signal",
            "reason": "taxonomy/provenance remains diagnostic or prerequisite evidence.",
            "allowed_after": "only after explicit data-quality re-entry criteria are met",
        },
    ]


def _metrics(
    policy: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    budgets: list[dict[str, Any]],
    slices: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    safety_oof: dict[str, Any],
    compat: dict[str, Any],
) -> dict[str, Any]:
    selected = _selected_metrics(safety_oof)
    compat_dev = _compat_split(compat)
    pass_count = sum(1 for row in checks if row["status"] == "pass")
    return {
        "policy_contract_count": len(policy),
        "oof_requirement_count": len(requirements),
        "loss_budget_item_count": len(budgets),
        "residual_slice_count": len(slices),
        "freeze_validation_boundary_count": len(boundaries),
        "design_gate_check_count": len(checks),
        "design_gate_pass_count": pass_count,
        "design_gate_fail_count": len(checks) - pass_count,
        "blocked_action_count": len(blocked),
        "selected_gate_variant": selected.get("variant"),
        "selected_gate_hit1_net": selected.get("gated_hit1_net"),
        "selected_gate_hit1_loss": selected.get("gated_hit1_loss"),
        "selected_gate_blocked_gain": selected.get("blocked_raw_hit1_gain"),
        "selected_gate_prevented_loss": selected.get("prevented_raw_hit1_loss"),
        "compat_oof_rescued_blocked_gain": compat_dev.get("rescued_blocked_gain"),
        "compat_oof_new_residual_loss": compat_dev.get("new_residual_loss"),
        "s3_policy_review_ready_for_design_gate": pass_count == len(checks),
        "threshold_change_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.12 S3 Safety Gate Calibration V2 Policy/Loss-Budget Review",
        "",
        "Read-only S3 policy review. This defines evidence requirements, loss budget, residual slices, and freeze/validation boundaries. It does not change thresholds or implementation.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_gate_variant", metrics["selected_gate_variant"]],
                ["selected_gate_hit1_net", metrics["selected_gate_hit1_net"]],
                ["selected_gate_hit1_loss", metrics["selected_gate_hit1_loss"]],
                ["compat_oof_rescued_blocked_gain", metrics["compat_oof_rescued_blocked_gain"]],
                ["compat_oof_new_residual_loss", metrics["compat_oof_new_residual_loss"]],
                ["design_gate_pass_count", metrics["design_gate_pass_count"]],
                ["s3_policy_review_ready_for_design_gate", metrics["s3_policy_review_ready_for_design_gate"]],
                ["threshold_change_allowed", metrics["threshold_change_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Loss Budget",
        "",
        _md_table(
            [["budget_id", "metric", "reference_value", "promotion_block_if"]]
            + [[row["budget_id"], row["metric"], row["reference_value"], row["promotion_block_if"]] for row in report["loss_budget"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.12 S3 safety gate calibration v2 policy/loss-budget review")
    parser.add_argument("--stage-10-11", default=str(DEFAULT_STAGE_10_11))
    parser.add_argument("--safety-oof", default=str(DEFAULT_SAFETY_OOF))
    parser.add_argument("--safety-residual", default=str(DEFAULT_SAFETY_RESIDUAL))
    parser.add_argument("--compat-whatif", default=str(DEFAULT_COMPAT_WHATIF))
    parser.add_argument("--compat-residual", default=str(DEFAULT_COMPAT_RESIDUAL))
    parser.add_argument("--freeze-narrow-review", default=str(DEFAULT_FREEZE_NARROW_REVIEW))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_11 = _read_json(Path(args.stage_10_11))
    safety_oof = _read_json(Path(args.safety_oof))
    safety_residual = _read_json(Path(args.safety_residual))
    compat = _read_json(Path(args.compat_whatif))
    compat_residual = _read_json(Path(args.compat_residual))
    freeze_narrow = _read_json(Path(args.freeze_narrow_review))

    policy = _policy_contract(stage_10_11, safety_oof, compat, compat_residual)
    requirements = _oof_requirements(safety_oof, safety_residual, compat, freeze_narrow)
    budgets = _loss_budget(safety_oof, compat, freeze_narrow)
    slices = _residual_slices(safety_residual, compat_residual, freeze_narrow)
    boundaries = _freeze_validation_boundaries()
    checks = _design_gate_checks(policy, requirements, budgets, slices, boundaries)
    blocked = _blocked_actions()
    metrics = _metrics(policy, requirements, budgets, slices, boundaries, checks, blocked, safety_oof, compat)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "policy_contract_csv": str(output_prefix.with_name(output_prefix.name + "_policy_contract.csv")),
        "oof_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_oof_requirements.csv")),
        "loss_budget_csv": str(output_prefix.with_name(output_prefix.name + "_loss_budget.csv")),
        "residual_slices_csv": str(output_prefix.with_name(output_prefix.name + "_residual_slices.csv")),
        "freeze_validation_boundaries_csv": str(output_prefix.with_name(output_prefix.name + "_freeze_validation_boundaries.csv")),
        "design_gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_design_gate_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.12 S3 safety gate calibration v2 policy/loss-budget review",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_11_reentry": str(Path(args.stage_10_11)),
            "safety_oof_summary": str(Path(args.safety_oof)),
            "safety_residual_summary": str(Path(args.safety_residual)),
            "compat_whatif_summary": str(Path(args.compat_whatif)),
            "compat_residual_summary": str(Path(args.compat_residual)),
            "freeze_narrow_review_summary": str(Path(args.freeze_narrow_review)),
        },
        "metrics": metrics,
        "policy_contract": policy,
        "oof_requirements": requirements,
        "loss_budget": budgets,
        "residual_slices": slices,
        "freeze_validation_boundaries": boundaries,
        "design_gate_checks": checks,
        "blocked_actions": blocked,
        "decision": (
            "Define the S3 safety gate calibration v2 review contract for policy/loss-budget analysis only. "
            "The current selected gate remains the frozen comparator, compatibility evidence is relation-level, OOF selection requirements and loss budgets are explicit, "
            "and heldout/hard remain validation-only after freeze. The next admissible step is a read-only S3 design gate, not threshold change or implementation."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.12 only defines S3 policy requirements, loss budget, residual slices, and freeze/validation boundaries. It does not train, tune, change thresholds, "
            "patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, connect online, or convert "
            "taxonomy/source backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.13 S3 safety gate calibration design gate",
            "goal": (
                "Read-only decide whether the 10.12 S3 policy, OOF evidence, loss budget, residual slices, and freeze/validation boundaries are concrete enough "
                "to define a future offline what-if plan. Still no threshold change, tuning, rule patch, ranking change, or implementation."
            ),
            "prohibited": [
                "training",
                "tuning",
                "threshold changes",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
            ],
        },
    }

    _write_csv(Path(artifacts["policy_contract_csv"]), policy, ["contract_id", "policy_area", "evidence", "required_policy", "not_allowed"])
    _write_csv(Path(artifacts["oof_requirements_csv"]), requirements, ["requirement_id", "required_evidence", "current_reference", "pass_condition", "blocked_if_missing"])
    _write_csv(Path(artifacts["loss_budget_csv"]), budgets, ["budget_id", "metric", "reference_value", "future_ceiling", "reason", "promotion_block_if"])
    _write_csv(Path(artifacts["residual_slices_csv"]), slices, ["slice_id", "slice_dimension", "required_buckets", "current_signal", "why_required"])
    _write_csv(Path(artifacts["freeze_validation_boundaries_csv"]), boundaries, ["boundary_id", "policy", "allowed_split", "not_allowed"])
    _write_csv(Path(artifacts["design_gate_checks_csv"]), checks, ["check", "status", "observed", "required"])
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
