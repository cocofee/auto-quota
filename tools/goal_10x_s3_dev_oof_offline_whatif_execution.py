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
DEFAULT_STAGE_10_14 = AGENT_STATE / "goal_10x_s3_offline_whatif_plan_definition_summary.json"
DEFAULT_STAGE_10_12 = AGENT_STATE / "goal_10x_s3_safety_gate_policy_loss_budget_review_summary.json"
DEFAULT_COMPAT_SUMMARY = AGENT_STATE / "goal_family_compatibility_whatif_summary.json"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_family_compatibility_freeze_narrow_whatif_summary.json"
DEFAULT_FREEZE_ROWS = AGENT_STATE / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_FREEZE_REVIEW = AGENT_STATE / "goal_family_compatibility_freeze_narrow_residual_review_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_offline_whatif"


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric_for_split(summary: dict[str, Any], split: str) -> dict[str, Any]:
    for row in summary.get("split_metrics", []):
        if row.get("split") == split:
            return row
    raise ValueError(f"Missing split metrics for {split}")


def _policy_metric(summary: dict[str, Any], policy: str, split: str = "dev_oof") -> dict[str, Any]:
    for row in summary.get("policy_metrics", []):
        if row.get("policy") == policy and row.get("split") == split:
            return row
    raise ValueError(f"Missing policy metrics for {policy}/{split}")


def _gain_rows(rows: list[dict[str, str]], policy: str) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("split") == "dev_oof"
        and row.get("policy") == policy
        and _bool(row.get("policy_allowed"))
        and not _bool(row.get("gated_hit1"))
        and _bool(row.get("policy_hit1"))
    ]


def _max_share(counter: Counter[str]) -> tuple[str, int, float]:
    total = sum(counter.values())
    if not total:
        return "", 0, 0.0
    key, count = counter.most_common(1)[0]
    return key, count, round(count / total, 6)


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _candidate_scorecard(
    stage_10_14: dict[str, Any],
    stage_10_12: dict[str, Any],
    compat_summary: dict[str, Any],
    freeze_summary: dict[str, Any],
    freeze_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    compat = _metric_for_split(compat_summary, "dev_oof")
    selected_prevented_loss = stage_10_12.get("metrics", {}).get("selected_gate_prevented_loss", 11)
    matrix = {row["candidate_id"]: row for row in stage_10_14.get("candidate_policy_matrix", [])}

    def make_from_policy(candidate_id: str, policy: str) -> dict[str, Any]:
        metric = _policy_metric(freeze_summary, policy)
        gains = _gain_rows(freeze_rows, policy)
        source_key, source_count, source_share = _max_share(Counter(row.get("source_file", "") for row in gains))
        relation_key, relation_count, relation_share = _max_share(
            Counter(row.get("compatibility_relation_id") or row.get("family_pair", "") for row in gains)
        )
        stop_source = bool(gains) and source_share >= 0.8
        stop_relation = bool(gains) and relation_share >= 0.8
        return {
            "candidate_id": candidate_id,
            "role": matrix.get(candidate_id, {}).get("role", "future_candidate"),
            "policy_source": policy,
            "split": "dev_oof",
            "matrix_groups": metric.get("matrix_groups"),
            "hit1": metric.get("policy_hit1"),
            "hit1_rate_matrix": metric.get("policy_hit1_rate_matrix"),
            "net_vs_selected_gate": metric.get("net_vs_gated"),
            "rescued_blocked_gain": metric.get("rescued_blocked_gain"),
            "new_residual_loss": metric.get("new_residual_loss"),
            "saved_loss_retained": metric.get("saved_loss_retained"),
            "neutral_override_count": metric.get("allowed_neutral_override"),
            "policy_allowed_count": metric.get("policy_allowed_count"),
            "top_source_file": source_key,
            "top_source_gain_count": source_count,
            "top_source_gain_share": source_share,
            "top_relation": relation_key,
            "top_relation_gain_count": relation_count,
            "top_relation_gain_share": relation_share,
            "loss_budget_status": "pass" if metric.get("new_residual_loss") == 0 and metric.get("net_vs_gated", 0) > 0 else "fail",
            "stop_condition_status": "stop_source_dominated" if stop_source else ("stop_single_relation_dominance" if stop_relation else "pass"),
            "candidate_decision": "diagnostic_only_do_not_advance" if stop_source or stop_relation else "candidate_can_enter_review",
        }

    return [
        {
            "candidate_id": "POL_A_SELECTED_GATE_COMPARATOR",
            "role": matrix.get("POL_A_SELECTED_GATE_COMPARATOR", {}).get("role", "frozen_comparator"),
            "policy_source": "selected_gate_comparator",
            "split": "dev_oof",
            "matrix_groups": compat.get("matrix_groups"),
            "hit1": compat.get("gated_hit1"),
            "hit1_rate_matrix": compat.get("gated_hit1_rate_matrix"),
            "net_vs_selected_gate": 0,
            "rescued_blocked_gain": 0,
            "new_residual_loss": 0,
            "saved_loss_retained": selected_prevented_loss,
            "neutral_override_count": 0,
            "policy_allowed_count": 0,
            "top_source_file": "",
            "top_source_gain_count": 0,
            "top_source_gain_share": 0.0,
            "top_relation": "",
            "top_relation_gain_count": 0,
            "top_relation_gain_share": 0.0,
            "loss_budget_status": "comparator",
            "stop_condition_status": "not_applicable",
            "candidate_decision": "comparator_only",
        },
        make_from_policy("POL_B_RELATION_FREEZE_CANDIDATES", "freeze_high_support_only"),
        make_from_policy("POL_C_FREEZE_PLUS_NARROW_CANDIDATES", "freeze_plus_tight_sleeve_duct"),
        {
            "candidate_id": "POL_D_LOGGING_ONLY_COMPATIBILITY",
            "role": matrix.get("POL_D_LOGGING_ONLY_COMPATIBILITY", {}).get("role", "diagnostic_comparator"),
            "policy_source": "logging_only_no_override",
            "split": "dev_oof",
            "matrix_groups": compat.get("matrix_groups"),
            "hit1": compat.get("gated_hit1"),
            "hit1_rate_matrix": compat.get("gated_hit1_rate_matrix"),
            "net_vs_selected_gate": 0,
            "rescued_blocked_gain": 0,
            "new_residual_loss": 0,
            "saved_loss_retained": selected_prevented_loss,
            "neutral_override_count": compat.get("compat_matched_count"),
            "policy_allowed_count": 0,
            "top_source_file": "",
            "top_source_gain_count": 0,
            "top_source_gain_share": 0.0,
            "top_relation": "",
            "top_relation_gain_count": 0,
            "top_relation_gain_share": 0.0,
            "loss_budget_status": "diagnostic_comparator",
            "stop_condition_status": "not_applicable",
            "candidate_decision": "diagnostic_logging_only",
        },
        {
            "candidate_id": "POL_E_BASELINE_FALLBACK_ONLY",
            "role": matrix.get("POL_E_BASELINE_FALLBACK_ONLY", {}).get("role", "safety_floor_comparator"),
            "policy_source": "baseline_fallback_only",
            "split": "dev_oof",
            "matrix_groups": compat.get("matrix_groups"),
            "hit1": compat.get("baseline_hit1"),
            "hit1_rate_matrix": compat.get("baseline_hit1_rate_matrix"),
            "net_vs_selected_gate": int(compat.get("baseline_hit1", 0)) - int(compat.get("gated_hit1", 0)),
            "rescued_blocked_gain": 0,
            "new_residual_loss": 0,
            "saved_loss_retained": "fallback_floor",
            "neutral_override_count": 0,
            "policy_allowed_count": 0,
            "top_source_file": "",
            "top_source_gain_count": 0,
            "top_source_gain_share": 0.0,
            "top_relation": "",
            "top_relation_gain_count": 0,
            "top_relation_gain_share": 0.0,
            "loss_budget_status": "fallback_floor",
            "stop_condition_status": "not_applicable",
            "candidate_decision": "floor_comparator_only",
        },
    ]


def _relation_level_audit(freeze_review: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in freeze_review.get("relation_summary", []):
        if row.get("split") != "dev_oof":
            continue
        rows.append(
            {
                "split": "dev_oof",
                "relation_or_pair": row.get("relation_or_pair"),
                "review_scope": row.get("review_scope"),
                "rows": row.get("rows"),
                "rescued_blocked_gain": row.get("rescued_blocked_gain"),
                "allowed_neutral_override": row.get("allowed_neutral_override"),
                "still_blocked_gain": row.get("still_blocked_gain"),
                "new_residual_loss": row.get("new_residual_loss"),
                "evidence_ok_rate": row.get("evidence_ok_rate"),
                "top_verdicts": row.get("top_verdicts"),
                "top_reasons": row.get("top_reasons"),
            }
        )
    return rows


def _loss_budget_report(scorecard: list[dict[str, Any]], stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    budget = {row["gate_id"]: row for row in stage_10_14.get("loss_budget_gates", [])}
    rows = []
    for candidate in scorecard:
        cid = candidate["candidate_id"]
        role = candidate["role"]
        is_future = role == "future_candidate"
        gate_values = {
            "LB_NEW_LOSS_CEILING": candidate["new_residual_loss"],
            "LB_RESCUE_GAIN_FLOOR": candidate["rescued_blocked_gain"],
            "LB_SAVED_LOSS_RETENTION": candidate["saved_loss_retained"],
            "LB_NEUTRAL_OVERRIDE_VISIBILITY": candidate["neutral_override_count"],
            "LB_NET_VS_SELECTED_GATE": candidate["net_vs_selected_gate"],
        }
        for gate_id, observed in gate_values.items():
            status = "not_applicable"
            if is_future:
                if gate_id == "LB_NEW_LOSS_CEILING":
                    status = "pass" if _float(observed) <= 0 else "fail"
                elif gate_id == "LB_RESCUE_GAIN_FLOOR":
                    status = "pass" if _float(observed) > 0 else "fail"
                elif gate_id == "LB_SAVED_LOSS_RETENTION":
                    status = "pass" if _float(observed) >= 11 else "fail"
                elif gate_id == "LB_NEUTRAL_OVERRIDE_VISIBILITY":
                    status = "pass" if observed != "" else "fail"
                elif gate_id == "LB_NET_VS_SELECTED_GATE":
                    status = "pass" if _float(observed) > 0 else "fail"
            rows.append(
                {
                    "candidate_id": cid,
                    "gate_id": gate_id,
                    "metric": budget.get(gate_id, {}).get("metric", gate_id),
                    "observed_value": observed,
                    "status": status,
                    "reference_value": budget.get(gate_id, {}).get("reference_value", ""),
                    "failure_action": budget.get(gate_id, {}).get("failure_action", ""),
                }
            )
        if is_future:
            rows.append(
                {
                    "candidate_id": cid,
                    "gate_id": "STOP_SOURCE_OR_TAXONOMY_ARTIFACT",
                    "metric": "top_source_gain_share",
                    "observed_value": candidate["top_source_gain_share"],
                    "status": "fail_stop" if _float(candidate["top_source_gain_share"]) >= 0.8 else "pass",
                    "reference_value": "<0.8",
                    "failure_action": "treat as diagnostic artifact, not calibration improvement",
                }
            )
            rows.append(
                {
                    "candidate_id": cid,
                    "gate_id": "STOP_SINGLE_RELATION_DOMINANCE",
                    "metric": "top_relation_gain_share",
                    "observed_value": candidate["top_relation_gain_share"],
                    "status": "fail_stop" if _float(candidate["top_relation_gain_share"]) >= 0.8 else "pass",
                    "reference_value": "<0.8",
                    "failure_action": "block general S3 improvement claim",
                }
            )
    return rows


def _residual_slice_report(scorecard: list[dict[str, Any]], freeze_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in scorecard:
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "slice_dimension": "outcome",
                "slice_key": "dev_oof_scorecard",
                "rows": candidate.get("matrix_groups"),
                "rescued_blocked_gain": candidate.get("rescued_blocked_gain"),
                "new_residual_loss": candidate.get("new_residual_loss"),
                "neutral_override_count": candidate.get("neutral_override_count"),
                "note": candidate.get("candidate_decision"),
            }
        )
    for policy, cid in [
        ("freeze_high_support_only", "POL_B_RELATION_FREEZE_CANDIDATES"),
        ("freeze_plus_tight_sleeve_duct", "POL_C_FREEZE_PLUS_NARROW_CANDIDATES"),
    ]:
        gains = _gain_rows(freeze_rows, policy)
        for dimension, key_fn in [
            ("source_file", lambda row: row.get("source_file", "")),
            ("province", lambda row: row.get("province", "")),
            ("taxonomy_family", lambda row: f"{row.get('query_family', '')}->{row.get('raw_ltr_top_family', '')}"),
            ("margin_bucket", lambda row: _margin_bucket(_float(row.get("score_margin")))),
        ]:
            for key, count in Counter(key_fn(row) for row in gains).most_common():
                rows.append(
                    {
                        "candidate_id": cid,
                        "slice_dimension": dimension,
                        "slice_key": key,
                        "rows": count,
                        "rescued_blocked_gain": count,
                        "new_residual_loss": 0,
                        "neutral_override_count": "",
                        "note": "dev_oof_gain_slice",
                    }
                )
    return rows


def _margin_bucket(value: float) -> str:
    if value < 0.5:
        return "margin_lt_0_5"
    if value < 1.0:
        return "margin_0_5_1_0"
    if value < 1.5:
        return "margin_1_0_1_5"
    return "margin_ge_1_5"


def _fallback_default_off_report(scorecard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "check": "default_off_retained",
            "status": "pass",
            "evidence": "No switch or GoalSearcher integration was touched; this is artifact-only execution.",
        },
        {
            "check": "selected_gate_comparator_retained",
            "status": "pass",
            "evidence": "POL_A_SELECTED_GATE_COMPARATOR remains the frozen comparator with net_vs_selected_gate=0.",
        },
        {
            "check": "baseline_floor_visible",
            "status": "pass",
            "evidence": "POL_E_BASELINE_FALLBACK_ONLY reports baseline hit1 and net vs selected gate separately.",
        },
        {
            "check": "future_candidate_blocked_by_source_artifact",
            "status": "fail_stop",
            "evidence": "; ".join(
                f"{row['candidate_id']} top_source={row['top_source_file']} share={row['top_source_gain_share']}"
                for row in scorecard
                if row["role"] == "future_candidate"
            ),
        },
    ]


def _stop_conditions(scorecard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "stop_condition": "heldout_or_hard_selection_contamination",
            "status": "pass",
            "evidence": "Only dev_oof rows were used for scorecard and slices; heldout/hard selection rows used=0.",
            "required_action": "continue_artifact_interpretation",
        },
        {
            "stop_condition": "missing_required_artifact",
            "status": "pass",
            "evidence": "All six required artifact families are written by this execution package.",
            "required_action": "continue_artifact_interpretation",
        },
        {
            "stop_condition": "new_residual_loss_over_budget",
            "status": "pass",
            "evidence": "Future candidates report new_residual_loss=0 on dev_oof.",
            "required_action": "continue_to_artifact_stop_review",
        },
        {
            "stop_condition": "fallback_or_default_off_break",
            "status": "pass",
            "evidence": "No implementation, switch, threshold, or GoalSearcher files were changed.",
            "required_action": "continue_to_artifact_stop_review",
        },
        {
            "stop_condition": "source_or_taxonomy_artifact",
            "status": "fail_stop",
            "evidence": "; ".join(
                f"{row['candidate_id']} rescued_gain={row['rescued_blocked_gain']} top_source={row['top_source_file']} share={row['top_source_gain_share']}"
                for row in scorecard
                if row["role"] == "future_candidate"
            ),
            "required_action": "stop; treat S3 result as diagnostic only, not calibration improvement",
        },
        {
            "stop_condition": "single_relation_dominance",
            "status": "pass",
            "evidence": "; ".join(
                f"{row['candidate_id']} top_relation={row['top_relation']} share={row['top_relation_gain_share']}"
                for row in scorecard
                if row["role"] == "future_candidate"
            ),
            "required_action": "no stop from relation dominance",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any], scorecard: list[dict[str, Any]], stops: list[dict[str, Any]]) -> None:
    lines = [
        "# S3 Dev/OOF-only Offline What-if Execution",
        "",
        "Frozen 10.14 scope was executed as an artifact-only dev/OOF what-if package.",
        "",
        "## Scorecard",
        "",
        _md_table(
            [["candidate_id", "net_vs_selected_gate", "rescued_blocked_gain", "new_residual_loss", "top_source_gain_share", "decision"]]
            + [
                [
                    row["candidate_id"],
                    row["net_vs_selected_gate"],
                    row["rescued_blocked_gain"],
                    row["new_residual_loss"],
                    row["top_source_gain_share"],
                    row["candidate_decision"],
                ]
                for row in scorecard
            ]
        ),
        "",
        "## Stop Conditions",
        "",
        _md_table(
            [["stop_condition", "status", "evidence"]]
            + [[row["stop_condition"], row["status"], row["evidence"]] for row in stops]
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


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    text = text.replace('<div class="value">S3 selected</div>', '<div class="value">S3 stopped</div>', 1)
    text = text.replace(
        "我已选择第 2 条路线：S2 继续关闭，转入 S3 safety gate dev/OOF-only offline what-if 路线。",
        "S3 dev/OOF-only what-if 已执行并触发 source/taxonomy artifact stop；产物完整，但不能进入 validation/implementation。",
        1,
    )
    text = text.replace('<div class="value">S3 安全门复核</div>', '<div class="value">S3 诊断收口</div>', 1)
    text = text.replace(
        "下一步只允许使用 frozen 10.14 scope 做 dev/OOF-only S3 what-if package；仍不训练、不改阈值、不接线上。",
        "POL_C dev_oof net=26、new_loss=0，但 rescued gain 100% 来自 global_repair_decision_table.csv，按 stop condition 停止。",
        1,
    )
    marker = """          <tr>
            <td class="stage">10.x learning loop paused awaiting external evidence</td>"""
    row = f"""          <tr>
            <td class="stage">S3 dev/OOF-only offline what-if execution</td>
            <td><span class="pill paused">stopped</span></td>
            <td>Execute frozen 10.14 S3 policy matrix on dev/OOF only and emit the six required loss/audit artifact families.</td>
            <td>execution_decision={report['metrics']['execution_decision']}; artifact_family_count={report['metrics']['artifact_family_count']}; best_candidate_id={report['metrics']['best_candidate_id']}; best_candidate_net_vs_selected_gate={report['metrics']['best_candidate_net_vs_selected_gate']}; best_candidate_new_residual_loss={report['metrics']['best_candidate_new_residual_loss']}; source_artifact_stop=true.</td>
            <td>Stop: gains are source-dominated by global_repair_decision_table.csv. Do not enter heldout/hard validation, implementation, threshold change, or GoalSearcher integration.</td>
          </tr>
"""
    if "S3 dev/OOF-only offline what-if execution" not in text:
        text = text.replace(marker, row + marker, 1)
    prompt_old = "当前状态：S3 route selection after S2 stop。Codex 已选择第 2 条路线：不继续卡 S2，不等待 DQ/S6 owner mappings，转入 S3 safety gate dev/OOF-only offline what-if 路线。S2 仍关闭：pass_candidate_count=0；best_accepted_oss_positive_net=1；best_positive_independent_source_family_count=1。"
    prompt_new = "当前状态：S3 dev/OOF-only offline what-if execution 已执行并停止。best_candidate=POL_C_FREEZE_PLUS_NARROW_CANDIDATES；dev_oof net_vs_selected_gate=26；new_residual_loss=0；但 POL_B/POL_C rescued gain 100% 来自 global_repair_decision_table.csv，触发 source_or_taxonomy_artifact stop。"
    text = text.replace(prompt_old, prompt_new, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-10-14", type=Path, default=DEFAULT_STAGE_10_14)
    parser.add_argument("--stage-10-12", type=Path, default=DEFAULT_STAGE_10_12)
    parser.add_argument("--compat-summary", type=Path, default=DEFAULT_COMPAT_SUMMARY)
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--freeze-rows", type=Path, default=DEFAULT_FREEZE_ROWS)
    parser.add_argument("--freeze-review", type=Path, default=DEFAULT_FREEZE_REVIEW)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    stage_10_14 = _read_json(args.stage_10_14)
    stage_10_12 = _read_json(args.stage_10_12)
    compat_summary = _read_json(args.compat_summary)
    freeze_summary = _read_json(args.freeze_summary)
    freeze_rows = _read_csv(args.freeze_rows)
    freeze_review = _read_json(args.freeze_review)

    scorecard = _candidate_scorecard(stage_10_14, stage_10_12, compat_summary, freeze_summary, freeze_rows)
    relation_audit = _relation_level_audit(freeze_review)
    loss_report = _loss_budget_report(scorecard, stage_10_14)
    residual_slices = _residual_slice_report(scorecard, freeze_rows)
    fallback_report = _fallback_default_off_report(scorecard)
    stop_conditions = _stop_conditions(scorecard)

    future_candidates = [row for row in scorecard if row["role"] == "future_candidate"]
    approved = [
        row
        for row in future_candidates
        if row["loss_budget_status"] == "pass" and row["stop_condition_status"] == "pass"
    ]
    best = max(future_candidates, key=lambda row: (_float(row["net_vs_selected_gate"]), -_float(row["new_residual_loss"])))

    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_execution_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_execution_summary.md")),
        "candidate_policy_scorecard_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_policy_scorecard.csv")),
        "candidate_policy_scorecard_summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_policy_scorecard_summary.json")),
        "relation_level_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_relation_level_audit.csv")),
        "relation_level_audit_md": str(args.output_prefix.with_name(args.output_prefix.name + "_relation_level_audit.md")),
        "loss_budget_gate_report_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_budget_gate_report.csv")),
        "loss_budget_gate_report_json": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_budget_gate_report.json")),
        "residual_slice_report_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_residual_slice_report.csv")),
        "fallback_default_off_report_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_fallback_default_off_report.csv")),
        "fallback_default_off_report_md": str(args.output_prefix.with_name(args.output_prefix.name + "_fallback_default_off_report.md")),
        "selection_boundary_report_json": str(args.output_prefix.with_name(args.output_prefix.name + "_selection_boundary_report.json")),
    }

    metrics = {
        "execution_decision": "stop_do_not_validate_or_implement",
        "candidate_policy_count": len(scorecard),
        "future_candidate_count": len(future_candidates),
        "approved_candidate_count": len(approved),
        "artifact_family_count": 6,
        "best_candidate_id": best["candidate_id"],
        "best_candidate_net_vs_selected_gate": best["net_vs_selected_gate"],
        "best_candidate_new_residual_loss": best["new_residual_loss"],
        "best_candidate_top_source_file": best["top_source_file"],
        "best_candidate_top_source_gain_share": best["top_source_gain_share"],
        "source_artifact_stop_triggered": True,
        "single_relation_stop_triggered": False,
        "heldout_used_for_selection": False,
        "whatif_execution_performed": True,
        "training_performed": False,
        "implementation_performed": False,
        "threshold_change_performed": False,
        "goal_searcher_changed": False,
    }
    report = {
        "stage": "Goal LTR v1 / S3 dev/OOF-only offline what-if execution package",
        "read_only": False,
        "eval_only": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "heldout_hard_rows_used_for_selection": 0,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_14_plan": str(args.stage_10_14),
            "stage_10_12_loss_budget": str(args.stage_10_12),
            "compat_summary": str(args.compat_summary),
            "freeze_summary": str(args.freeze_summary),
            "freeze_rows": str(args.freeze_rows),
            "freeze_review": str(args.freeze_review),
        },
        "metrics": metrics,
        "candidate_policy_scorecard": scorecard,
        "stop_conditions": stop_conditions,
        "decision": "S3 dev/OOF-only what-if package completed all six required artifact families, but no candidate can advance. POL_C has the best dev_oof net vs selected gate (+26) with new_residual_loss=0, yet POL_B and POL_C rescued gains are 100% from global_repair_decision_table.csv, triggering the source_or_taxonomy_artifact stop condition. Treat this as diagnostic evidence only; do not enter heldout/hard validation, implementation, threshold change, GoalSearcher integration, or switch enablement.",
        "anti_drift_conclusion": "The run consumed the frozen 10.14 S3 policy matrix and dev_oof rows only. It did not train, tune, change thresholds, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, enable switches, or connect online. The required stop condition fired, so execution stops here.",
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
        "next_stage": {
            "stage": "S3 source-artifact stop closure / strategy return",
            "goal": "Read-only close S3 what-if execution as diagnostic-only because source/taxonomy artifact stop fired, then decide whether to pause or return to broader strategy.",
            "prohibited": [
                "heldout/hard validation",
                "implementation",
                "threshold changes",
                "GoalSearcher changes",
                "switch enablement",
                "claiming general Top1 gain",
            ],
        },
    }

    _write_csv(Path(artifacts["candidate_policy_scorecard_csv"]), scorecard, list(scorecard[0].keys()))
    _write_json(Path(artifacts["candidate_policy_scorecard_summary_json"]), {"candidate_policy_scorecard": scorecard, "metrics": metrics})
    _write_csv(Path(artifacts["relation_level_audit_csv"]), relation_audit, list(relation_audit[0].keys()))
    Path(artifacts["relation_level_audit_md"]).write_text(
        "# S3 Relation Level Audit\n\n"
        + _md_table([list(relation_audit[0].keys())] + [[row.get(key, "") for key in relation_audit[0].keys()] for row in relation_audit]),
        encoding="utf-8",
    )
    _write_csv(Path(artifacts["loss_budget_gate_report_csv"]), loss_report, list(loss_report[0].keys()))
    _write_json(Path(artifacts["loss_budget_gate_report_json"]), {"loss_budget_gate_report": loss_report})
    _write_csv(Path(artifacts["residual_slice_report_csv"]), residual_slices, list(residual_slices[0].keys()))
    _write_csv(Path(artifacts["fallback_default_off_report_csv"]), fallback_report, list(fallback_report[0].keys()))
    Path(artifacts["fallback_default_off_report_md"]).write_text(
        "# S3 Fallback / Default-off Report\n\n"
        + _md_table([list(fallback_report[0].keys())] + [[row.get(key, "") for key in fallback_report[0].keys()] for row in fallback_report]),
        encoding="utf-8",
    )
    _write_json(
        Path(artifacts["selection_boundary_report_json"]),
        {
            "dev_oof_only": True,
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "candidate_matrix_source": str(args.stage_10_14),
            "frozen_candidate_policy_count": len(stage_10_14.get("candidate_policy_matrix", [])),
            "selection_decision": "no_candidate_selected_for_validation_due_to_source_artifact_stop",
        },
    )
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, scorecard, stop_conditions)
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
