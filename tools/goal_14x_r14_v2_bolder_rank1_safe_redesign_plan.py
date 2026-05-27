from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_CLOSURE = AGENT_STATE / "goal_14x_post_validation_closure_strategy_decision_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_candidate_scorecard.csv"
DEFAULT_GATE_COVERAGE = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_strong_challenger_gate_coverage.csv"
DEFAULT_LOSS_SLICES = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_loss_audit_by_slice.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


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


def _candidate(scorecard: list[dict[str, str]], candidate_id: str) -> dict[str, str]:
    for row in scorecard:
        if row.get("candidate_id") == candidate_id:
            return row
    raise KeyError(f"missing candidate: {candidate_id}")


def _r14d_loss_summary(loss_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in loss_rows if row.get("candidate_id") == "R14_D_near_miss_proxy_no_clean_rank1"]
    def find(dimension: str, key: str) -> dict[str, str]:
        return next((row for row in rows if row.get("slice_dimension") == dimension and row.get("slice_key") == key), {})

    gate = find("gate_reason", "small_margin_weak_baseline_support2")
    rank1 = find("baseline_rank_bucket", "rank_1")
    rank25 = find("baseline_rank_bucket", "rank_2_5")
    rank610 = find("baseline_rank_bucket", "rank_6_10")
    taxonomy_present = find("taxonomy_empty", "False")
    taxonomy_empty = find("taxonomy_empty", "True")
    return {
        "gate_small_margin_weak_support2": {
            "groups": _int(gate.get("groups")),
            "gain": _int(gate.get("gain")),
            "loss": _int(gate.get("loss")),
            "net": _int(gate.get("net")),
        },
        "rank1_bucket": {"loss": _int(rank1.get("loss")), "net": _int(rank1.get("net"))},
        "rank_2_5": {"gain": _int(rank25.get("gain")), "loss": _int(rank25.get("loss")), "net": _int(rank25.get("net"))},
        "rank_6_10": {"gain": _int(rank610.get("gain")), "loss": _int(rank610.get("loss")), "net": _int(rank610.get("net"))},
        "taxonomy_present": {"gain": _int(taxonomy_present.get("gain")), "loss": _int(taxonomy_present.get("loss")), "net": _int(taxonomy_present.get("net"))},
        "taxonomy_empty": {"gain": _int(taxonomy_empty.get("gain")), "loss": _int(taxonomy_empty.get("loss")), "net": _int(taxonomy_empty.get("net"))},
    }


def _candidate_matrix() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "R14V2_A_near_miss_safe_support2_margin_q70",
            "objective_variant": "OBJ_R14_pairwise_near_miss_proxy",
            "feature_toggle": "FT_R14_SAFE_CORE_NO_BOOK_ID",
            "gate_formula": "small_margin_q35 AND baseline_weak_or_conflicted AND challenger_support_score>=2 AND challenger_margin_delta>=q70",
            "rank1_protection_veto": "if baseline looks clean OR raw challenger cannot beat top1 by q70 margin, preserve baseline",
            "taxonomy_guard": "taxonomy_empty requires challenger_support_score>=3 and family/book/action evidence not empty",
            "purpose": "recover part of R14_D coverage while reducing loose rank1 demotion",
            "expected_coverage": "medium",
        },
        {
            "candidate_id": "R14V2_B_near_miss_safe_support3_no_tax_empty",
            "objective_variant": "OBJ_R14_pairwise_near_miss_proxy",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CHALLENGER",
            "gate_formula": "small_margin_q35 AND baseline_weak_or_conflicted AND challenger_support_score>=3 AND taxonomy_empty=False",
            "rank1_protection_veto": "preserve baseline unless explicit conflict OR low_conf_q25 plus small_margin_q25",
            "taxonomy_guard": "hard exclude taxonomy_empty from intervention; audit only",
            "purpose": "precision-first version of R14_D signal",
            "expected_coverage": "low_medium",
        },
        {
            "candidate_id": "R14V2_C_conflict_or_weak_plus_near_miss_q65",
            "objective_variant": "OBJ_R14_conflict_weighted",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CONFLICT",
            "gate_formula": "(explicit_conflict OR low_reason_count_q25) AND small_margin_q35 AND challenger_support_score>=2 AND challenger_margin_delta>=q65",
            "rank1_protection_veto": "clean baseline and no explicit conflict cannot be demoted",
            "taxonomy_guard": "taxonomy_empty cannot pass unless explicit conflict is present and support>=3",
            "purpose": "bolder conflict/weak-evidence gate without low-confidence-only behavior",
            "expected_coverage": "medium",
        },
        {
            "candidate_id": "R14V2_D_top5_rescue_rank1_hard_veto",
            "objective_variant": "OBJ_R14_hit5_rescue_rank1_hard_veto",
            "feature_toggle": "FT_R14_SAFE_CORE_NO_BOOK_ID",
            "gate_formula": "baseline_positive_proxy_not_top1 AND challenger_support_score>=2 AND candidate moves positive-like item into top5 proxy",
            "rank1_protection_veto": "never demote clean baseline rank1; if baseline currently top1-like, fall back",
            "taxonomy_guard": "taxonomy_empty is diagnostic-only for freeze support",
            "purpose": "capture R14_D's Hit5/near-miss signal as diagnostic upper bound",
            "expected_coverage": "medium_high_diagnostic",
        },
        {
            "candidate_id": "R14V2_E_rank1_shadow_no_demote",
            "objective_variant": "OBJ_R14_top1_loss_guarded",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CHALLENGER",
            "gate_formula": "score challenger and emit shadow recommendation, but never alter baseline rank1",
            "rank1_protection_veto": "hard no-demotion; candidate can only reorder below rank1",
            "taxonomy_guard": "report all taxonomy-empty shadow moves separately",
            "purpose": "measure coverage and candidate quality without rank1 risk",
            "expected_coverage": "diagnostic_high_no_release",
        },
    ]


def _gate_feature_contract() -> list[dict[str, Any]]:
    return [
        {"field": "small_margin_q25/q35", "source": "dev/OOF baseline score margin only", "runtime_observable": True, "allowed_use": "gate uncertainty; cannot alone demote rank1"},
        {"field": "confidence_q25/q35", "source": "dev/OOF baseline confidence only", "runtime_observable": True, "allowed_use": "weak evidence indicator; cannot alone demote rank1"},
        {"field": "reason_count_q25", "source": "dev/OOF top1 reason count", "runtime_observable": True, "allowed_use": "weak evidence indicator"},
        {"field": "explicit_conflict", "source": "family/book/unit/domain/param conflict flags", "runtime_observable": True, "allowed_use": "stronger permission to challenge baseline"},
        {"field": "challenger_support_score", "source": "family/book/action/material/connection/numeric_score_superior", "runtime_observable": True, "allowed_use": "minimum support threshold for every intervention"},
        {"field": "challenger_margin_delta", "source": "dev/OOF model prediction delta threshold", "runtime_observable": True, "allowed_use": "must clear fixed dev/OOF threshold; no heldout/hard calibration"},
        {"field": "taxonomy_empty", "source": "query_family or candidate_family empty", "runtime_observable": True, "allowed_use": "guard/audit; not a positive trigger"},
        {"field": "source_family/province/oof_fold", "source": "metadata", "runtime_observable": False, "allowed_use": "audit only; never model feature"},
    ]


def _loss_budget() -> list[dict[str, Any]]:
    return [
        {"metric": "dev_oof_hit1_net", "freeze_requirement": "> 0", "stop_if_failed": True},
        {"metric": "dev_oof_rank1_loss_count", "freeze_requirement": "0 preferred; hard stop > 1", "stop_if_failed": True},
        {"metric": "baseline_rank1_demotion_rate", "freeze_requirement": "<= 0.0015 preferred; hard stop > 0.0025", "stop_if_failed": True},
        {"metric": "applied_group_rate", "freeze_requirement": "> R14_A 0.00232 and <= 0.04", "stop_if_failed": True},
        {"metric": "hit5_net", "freeze_requirement": ">= 0; positive is support but not enough alone", "stop_if_failed": True},
        {"metric": "taxonomy_empty_net", "freeze_requirement": "must not provide majority of positive net", "stop_if_failed": True},
        {"metric": "source_family_positive_net_share", "freeze_requirement": "<= 0.35 pass, >0.50 fail", "stop_if_failed": True},
        {"metric": "fold_negative_slices", "freeze_requirement": "no material negative fold; any rank1 loss fold requires review", "stop_if_failed": True},
    ]


def _required_artifacts(output_prefix: Path) -> list[dict[str, Any]]:
    base = output_prefix.with_name("goal_14x_r14_v2_bolder_rank1_safe_dev_oof")
    return [
        {"artifact": "execution_summary_json", "path": _safe_rel(base.with_name(base.name + "_execution_summary.json")), "required": True},
        {"artifact": "candidate_scorecard_csv", "path": _safe_rel(base.with_name(base.name + "_candidate_scorecard.csv")), "required": True},
        {"artifact": "rank1_preservation_report_csv", "path": _safe_rel(base.with_name(base.name + "_rank1_preservation_report.csv")), "required": True},
        {"artifact": "gate_coverage_csv", "path": _safe_rel(base.with_name(base.name + "_gate_coverage.csv")), "required": True},
        {"artifact": "loss_audit_by_slice_csv", "path": _safe_rel(base.with_name(base.name + "_loss_audit_by_slice.csv")), "required": True},
        {"artifact": "taxonomy_empty_audit_csv", "path": _safe_rel(base.with_name(base.name + "_taxonomy_empty_audit.csv")), "required": True},
        {"artifact": "source_fold_robustness_csv", "path": _safe_rel(base.with_name(base.name + "_source_fold_robustness.csv")), "required": True},
        {"artifact": "fallback_contract_report_csv", "path": _safe_rel(base.with_name(base.name + "_fallback_contract_report.csv")), "required": True},
        {"artifact": "threshold_manifest_csv", "path": _safe_rel(base.with_name(base.name + "_threshold_manifest.csv")), "required": True},
        {"artifact": "hit1_flips_jsonl", "path": _safe_rel(base.with_name(base.name + "_hit1_flips.jsonl")), "required": True},
    ]


def _stop_conditions() -> list[dict[str, Any]]:
    return [
        {"condition": "heldout_or_hard_read_before_new_freeze", "action": "invalidate run"},
        {"condition": "candidate_uses_label_positive_rank_expected_id_at_runtime", "action": "invalidate candidate"},
        {"condition": "low_confidence_or_small_margin_alone_demotes_rank1", "action": "invalidate candidate"},
        {"condition": "rank1_loss_count_gt_1", "action": "do not freeze"},
        {"condition": "applied_group_rate_le_R14_A_noop_rate", "action": "do not freeze; coverage too low"},
        {"condition": "applied_group_rate_gt_0.04", "action": "do not freeze; coverage too broad for rank1-safe release"},
        {"condition": "taxonomy_empty_drives_positive_net", "action": "do not freeze; route to DQ/parser"},
        {"condition": "source_family_positive_net_share_gt_0.50", "action": "do not freeze; source-dominated"},
        {"condition": "required_artifact_missing", "action": "stop and report missing artifact"},
        {"condition": "GoalSearcher_or_online_threshold_changed", "action": "reject run"},
    ]


def _command_contract(output_prefix: Path) -> list[dict[str, Any]]:
    plan_csv = output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv")
    return [
        {
            "stage": "14.8",
            "command": "python tools/goal_14x_r14_v2_bolder_rank1_safe_redesign_plan.py",
            "allowed": "read-only plan and artifact contract generation",
            "forbidden": "training, heldout/hard, release, GoalSearcher edits",
        },
        {
            "stage": "14.9_if_explicit_go",
            "command": f"python tools/goal_14x_r14_v2_bolder_rank1_safe_dev_oof_execute.py --data-dir reports/agent_state/goal_14x_rank1_safe_source_robust_matrix --candidate-plan {_safe_rel(plan_csv)} --dev-oof-only --emit-loss-audit",
            "allowed": "dev/OOF-only execution of fixed R14 v2 candidate matrix",
            "forbidden": "heldout/hard selection, release, online integration, GoalSearcher edits",
        },
        {
            "stage": "14.10_after_execution",
            "command": "python tools/goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review.py",
            "allowed": "read-only freeze/no-freeze review using dev/OOF scorecard and loss slices",
            "forbidden": "heldout/hard unless a new candidate is frozen and user gives explicit validation go",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    candidates = [["candidate", "gate", "rank1 protection", "purpose"]]
    for row in report["candidate_matrix"]:
        candidates.append([row["candidate_id"], row["gate_formula"], row["rank1_protection_veto"], row["purpose"]])
    budgets = [["metric", "freeze requirement"]]
    for row in report["loss_budget"]:
        budgets.append([row["metric"], row["freeze_requirement"]])
    lines = [
        "# 14.8 R14 v2 Bolder Rank1-Safe Dev/OOF Redesign Plan",
        "",
        "This is a read-only plan. It closes the no-op R14_A path and defines a bolder dev/OOF-only candidate matrix that borrows R14_D's near-miss signal without accepting its rank1 losses.",
        "",
        "## Evidence",
        "",
        f"- R14_A validation: heldout/hard applied `0`, Top1 net `0`, rank1 loss `0`.",
        f"- R14_D dev/OOF signal: Top1 net `{report['evidence']['r14d_hit1_net']}`, gain `{report['evidence']['r14d_hit1_gain']}`, loss `{report['evidence']['r14d_hit1_loss']}`, applied `{report['evidence']['r14d_applied_groups']}`.",
        f"- R14_D risk: rank1 loss `{report['evidence']['r14d_rank1_loss_count']}`; therefore it is a signal source, not a release candidate.",
        "",
        "## Candidate Matrix",
        "",
        _md_table(candidates),
        "",
        "## Loss Budget",
        "",
        _md_table(budgets),
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Next stage: `{report['next_stage']['recommended']}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "R14_A is closed as no-release. The next route is a bolder dev/OOF-only R14 v2 experiment that increases intervention coverage while preserving an online-observable rank1 protection veto.",
        "",
        "## Evidence Used",
        "",
        f"- R14_A heldout/hard validation: applied `0`, Top1 net `0`, rank1 loss `0`.",
        f"- R14_D dev/OOF: Top1 gain/loss/net `{report['evidence']['r14d_hit1_gain']}/{report['evidence']['r14d_hit1_loss']}/{report['evidence']['r14d_hit1_net']}`, rank1 loss `{report['evidence']['r14d_rank1_loss_count']}`, applied `{report['evidence']['r14d_applied_groups']}`.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not use heldout/hard until a new dev/OOF-frozen candidate passes freeze gate.",
        "- Do not release R14_A.",
        "- Do not switch directly to R14_D.",
        "- Do not change GoalSearcher or online thresholds.",
        "- Do not allow low-confidence or small-margin alone to demote rank1.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan 已完成。\n"
        "结论：R14_A no-release；R14_D 只能作为覆盖信号，不可直接上线。已定义 R14 v2 candidate matrix、gate/feature contract、loss budget、required artifacts、stop conditions 和 command contract。\n"
        "下一步建议：14.9 R14 v2 dev/OOF execution authorization gate。默认不训练；只有明确 go 才跑 dev/OOF-only execution。\n"
        "禁止：用 heldout/hard 调参、直接切 R14_D、发布 R14_A、改阈值、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.8 R14 v2 bolder rank1-safe redesign plan" not in text:
        row = f"""          <tr>
            <td>14.8 R14 v2 bolder rank1-safe redesign plan</td>
            <td>Dev/OOF-only R14 v2 candidate matrix, gate/feature contract, loss budget, artifacts, stop conditions, and execution command boundary.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {report['updated_at']} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan")
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE_COVERAGE)
    parser.add_argument("--loss-slices", type=Path, default=DEFAULT_LOSS_SLICES)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    closure = _read_json(args.closure)
    scorecard = _read_csv(args.scorecard)
    loss_rows = _read_csv(args.loss_slices)
    r14_a = _candidate(scorecard, "R14_A_rank1_veto_strong_challenger")
    r14_d = _candidate(scorecard, "R14_D_near_miss_proxy_no_clean_rank1")
    r14d_loss = _r14d_loss_summary(loss_rows)
    candidate_matrix = _candidate_matrix()
    gate_feature_contract = _gate_feature_contract()
    loss_budget = _loss_budget()
    required_artifacts = _required_artifacts(args.output_prefix)
    stop_conditions = _stop_conditions()
    command_contract = _command_contract(args.output_prefix)

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "candidate_matrix_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_matrix.csv")),
        "gate_feature_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_feature_contract.csv")),
        "loss_budget_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_budget.csv")),
        "required_artifacts_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_required_artifacts.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan",
        "read_only_plan": True,
        "decision": "plan_ready_request_explicit_dev_oof_execution_go",
        "closure_decision": closure.get("decision"),
        "evidence": {
            "r14a_validation_status": "safe_noop_no_release",
            "r14a_dev_oof_hit1_net": _int(r14_a.get("hit1_net")),
            "r14a_applied_groups": _int(r14_a.get("applied_groups")),
            "r14d_hit1_gain": _int(r14_d.get("hit1_gain")),
            "r14d_hit1_loss": _int(r14_d.get("hit1_loss")),
            "r14d_hit1_net": _int(r14_d.get("hit1_net")),
            "r14d_rank1_loss_count": _int(r14_d.get("rank1_loss_count")),
            "r14d_applied_groups": _int(r14_d.get("applied_groups")),
            "r14d_loss_summary": r14d_loss,
        },
        "candidate_matrix": candidate_matrix,
        "gate_feature_contract": gate_feature_contract,
        "loss_budget": loss_budget,
        "required_artifacts": required_artifacts,
        "stop_conditions": stop_conditions,
        "command_contract": command_contract,
        "next_stage": {
            "recommended": "14.9 R14 v2 dev/OOF execution authorization gate",
            "description": "Read-only go/no-go gate. Default is do_not_train; only an explicit user go may run the fixed R14 v2 dev/OOF-only execution command.",
            "default": "do_not_train",
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "14.8 generated a read-only redesign plan only. It did not train, tune, read heldout/hard, release R14_A, "
            "switch to R14_D, edit GoalSearcher, or change online thresholds."
        ),
    }

    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["candidate_matrix_csv"]), candidate_matrix, ["candidate_id", "objective_variant", "feature_toggle", "gate_formula", "rank1_protection_veto", "taxonomy_guard", "purpose", "expected_coverage"])
    _write_csv(Path(artifacts["gate_feature_contract_csv"]), gate_feature_contract, ["field", "source", "runtime_observable", "allowed_use"])
    _write_csv(Path(artifacts["loss_budget_csv"]), loss_budget, ["metric", "freeze_requirement", "stop_if_failed"])
    _write_csv(Path(artifacts["required_artifacts_csv"]), required_artifacts, ["artifact", "path", "required"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, ["condition", "action"])
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, ["stage", "command", "allowed", "forbidden"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
