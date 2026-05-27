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
DEFAULT_CLOSURE = AGENT_STATE / "goal_13x_validation_failed_closure_strategy_return_summary.json"
DEFAULT_PACKAGE = AGENT_STATE / "goal_13x_expanded_reranker_validation_package_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_top1_loss_guarded_reranker_redesign_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


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


def _design_rows() -> list[dict[str, Any]]:
    return [
        {
            "component": "training_data",
            "decision": "continue_using_OSS_XML_as_primary_training_reference",
            "details": "OSS XML is human quantity-surveyor output and remains the main high-value training source; do not discard it because one global reranker failed validation.",
        },
        {
            "component": "objective",
            "decision": "top1_loss_guarded_objective",
            "details": "Optimize positive movement only when it does not demote baseline rank_1; rank_1 losses receive a hard penalty larger than rank_2_5 gains.",
        },
        {
            "component": "baseline_rank1_protection",
            "decision": "default_keep_baseline_top1",
            "details": "If baseline top1 is high-confidence and not visibly conflicted, reranker must not override it.",
        },
        {
            "component": "application_gate",
            "decision": "apply_only_to_low_confidence_near_miss_or_conflict",
            "details": "Allow reranking only for low confidence, small score margin, family/book conflict, taxonomy-empty, or baseline non-rank1 diagnostic cases.",
        },
        {
            "component": "evaluation",
            "decision": "dev_oof_first_no_heldout_selection",
            "details": "Run new candidates only on OSS/dev OOF first; heldout/hard are reserved for final validation after a new freeze gate.",
        },
        {
            "component": "release_boundary",
            "decision": "current_frozen_candidate_parked",
            "details": "The 13.10 frozen candidate is not released, not integrated, and not used as a production fallback.",
        },
    ]


def _candidate_matrix_rows() -> list[dict[str, Any]]:
    return [
        {
            "candidate_family": "GATE_A_preserve_rank1_low_confidence_only",
            "objective": "top1_loss_guarded_lambdarank",
            "gate": "baseline_rank1 must be false OR confidence below dev-derived quantile OR top1/top2 score margin small",
            "loss_guard": "rank_1 -> non_rank_1 demotion is blocking unless dev/OOF net remains positive with zero/near-zero losses",
        },
        {
            "candidate_family": "GATE_B_conflict_only",
            "objective": "loss_budgeted_top1_net_with_demote_penalty",
            "gate": "apply only when baseline top1 has family/book/numeric conflict with query signal and a challenger matches query signal",
            "loss_guard": "baseline rank_1 protection stays on unless conflict evidence is explicit",
        },
        {
            "candidate_family": "GATE_C_near_miss_promotion_only",
            "objective": "pairwise_positive_promotion_with_top1_veto",
            "gate": "apply only when expected-like candidate is already rank_2_5/6_10 under dev/OOF diagnostics",
            "loss_guard": "candidate cannot demote baseline rank_1 outside gated near-miss rows",
        },
    ]


def _acceptance_rows() -> list[dict[str, Any]]:
    return [
        {"check": "dev_oof_hit1_net", "target": "> 0 on gated-applied rows and overall evaluated rows"},
        {"check": "dev_oof_rank1_loss_count", "target": "materially below 13.10 global reranker loss rate; ideally <= gain count / 2"},
        {"check": "baseline_rank1_preservation", "target": "rank_1 demotion rate explicitly reported and within budget"},
        {"check": "hit5_secondary_signal", "target": "positive, but cannot override Top1 loss guard"},
        {"check": "source_family_robustness", "target": "no single source_family/source_file dominates positive net"},
        {"check": "heldout_hard_boundary", "target": "not used until a later explicit validation go after freeze"},
    ]


def _forbidden_rows() -> list[dict[str, Any]]:
    return [
        {"forbidden": "release_current_frozen_candidate", "reason": "13.14 heldout/hard Top1 validation failed."},
        {"forbidden": "global_rerank_all_top80", "reason": "Global reranking caused more Top1 losses than gains on validation."},
        {"forbidden": "heldout_hard_tuning_or_selection", "reason": "Validation splits must remain independent."},
        {"forbidden": "claim_hit5_as_top1_improvement", "reason": "Hit5 was positive while Top1 was negative."},
        {"forbidden": "edit_GoalSearcher_or_online_thresholds", "reason": "13.16 is a design gate only."},
    ]


def _gate_rows(closure: dict[str, Any], package: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "previous_candidate_parked",
            "status": "pass" if closure.get("decision") == "park_frozen_candidate_return_to_top1_loss_guarded_redesign" else "fail",
            "value": closure.get("decision"),
            "reason": "The failed global reranker must be parked before redesign.",
        },
        {
            "gate": "validation_failure_understood",
            "status": "pass" if package.get("decision") == "do_not_release_validation_failed" else "fail",
            "value": package.get("decision"),
            "reason": "Redesign must respond to failed Top1 validation, not continue release.",
        },
        {
            "gate": "oss_training_value_preserved",
            "status": "pass",
            "value": "OSS_XML_primary_reference",
            "reason": "OSS data remains valuable human-labeled quota evidence.",
        },
        {
            "gate": "top1_loss_guard_required",
            "status": "pass",
            "value": "required",
            "reason": "Hit5-positive / Top1-negative behavior requires a first-place loss guard.",
        },
        {
            "gate": "heldout_hard_selection_blocked",
            "status": "pass",
            "value": "blocked_until_future_validation_go",
            "reason": "13.16 must not use heldout/hard to choose the redesigned candidate.",
        },
    ]
    decision = "ready_for_13_17_dev_oof_plan_definition" if all(row["status"] == "pass" for row in rows) else "not_ready_fix_gate_inputs"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 13.16 Top1-Loss-Guarded Reranker Redesign Gate",
        "",
        "Read-only redesign gate after the global expanded reranker failed heldout/hard Top1 validation. This stage defines the next strategy; it does not train, validate, tune, release, or modify GoalSearcher.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Design",
        "",
        _md_table([["component", "decision", "details"]] + [[row["component"], row["decision"], row["details"]] for row in report["design_rows"]]),
        "",
        "## Candidate Families",
        "",
        _md_table([["candidate_family", "objective", "gate", "loss_guard"]] + [[row["candidate_family"], row["objective"], row["gate"], row["loss_guard"]] for row in report["candidate_matrix_rows"]]),
        "",
        "## Acceptance Checks",
        "",
        _md_table([["check", "target"]] + [[row["check"], row["target"]] for row in report["acceptance_rows"]]),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.16 Top1-loss-guarded reranker redesign gate 已完成。\n"
        f"结论：{report['decision']}。新策略：继续用 OSS 大数据训练，但 baseline rank_1 默认保护，reranker 只在低置信/近失误/冲突场景介入，dev/OOF 先行。\n"
        "下一步建议：13.17 Top1-loss-guarded dev/OOF experiment plan definition。定义具体 objective、gating features、loss budget、artifact manifest 和 stop conditions；仍不跑 heldout/hard。\n"
        "禁止：release 当前 frozen candidate、全局重排 top80、用 heldout/hard 调参或选候选、改 GoalSearcher、改阈值、把 Hit5 正收益宣称为 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.16 Top1-loss-guarded reranker redesign gate" not in text:
        rows = f"""          <tr>
            <td>13.16 Top1-loss-guarded reranker redesign gate</td>
            <td>Read-only redesign gate for a guarded reranker strategy that preserves baseline rank_1 and gates intervention scope.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.15 validation-failed closure / strategy return</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.16 Top1-loss-guarded reranker redesign gate")
    parser.add_argument("--closure-summary", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--package-summary", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    closure = _read_json(args.closure_summary)
    package = _read_json(args.package_summary)
    gate_rows, decision = _gate_rows(closure, package)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "design_csv": str(output_prefix.with_name(output_prefix.name + "_design.csv")),
        "candidate_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "forbidden_actions_csv": str(output_prefix.with_name(output_prefix.name + "_forbidden_actions.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
    }
    report = {
        "stage": "13.16 Top1-loss-guarded reranker redesign gate",
        "read_only": True,
        "decision": decision,
        "design_rows": _design_rows(),
        "candidate_matrix_rows": _candidate_matrix_rows(),
        "acceptance_rows": _acceptance_rows(),
        "forbidden_rows": _forbidden_rows(),
        "gate_rows": gate_rows,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only redesign gate only: no training, no heldout/hard validation, no candidate reselection on validation splits, no release, no GoalSearcher edit, no threshold change, and no claim of Top1 improvement.",
        "next_stage": {
            "recommended": "13.17 Top1-loss-guarded dev/OOF experiment plan definition: define exact objective variants, gating feature families, loss budget, artifact manifest, and stop conditions before any execution.",
            "default": "do_not_train_yet",
        },
    }
    _write_csv(Path(artifacts["design_csv"]), report["design_rows"], ["component", "decision", "details"])
    _write_csv(Path(artifacts["candidate_matrix_csv"]), report["candidate_matrix_rows"], ["candidate_family", "objective", "gate", "loss_guard"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_rows"], ["check", "target"])
    _write_csv(Path(artifacts["forbidden_actions_csv"]), report["forbidden_rows"], ["forbidden", "reason"])
    _write_csv(Path(artifacts["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
