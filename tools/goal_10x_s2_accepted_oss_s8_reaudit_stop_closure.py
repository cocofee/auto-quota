from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REAUDIT_SUMMARY = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit_summary.json"
DEFAULT_GATE_CHECKS = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit_gate_checks.csv"
DEFAULT_STOP_DECISION = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit_stop_decision.csv"
DEFAULT_CANDIDATE_SCORECARD = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit_candidate_reaudit_scorecard.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_reaudit_stop_closure"


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
    closure_checks: list[dict[str, Any]],
    future_requirements: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# S2 Accepted-OSS + S8 Re-audit Stop Closure",
        "",
        "Closure of the explicit S2 accepted-OSS + S8 constrained dev/OOF re-audit.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_decision", metrics["closure_decision"]],
                ["best_accepted_oss_positive_net", metrics["best_accepted_oss_positive_net"]],
                ["best_positive_independent_source_family_count", metrics["best_positive_independent_source_family_count"]],
                ["pass_candidate_count", metrics["pass_candidate_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Closure Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in closure_checks]
        ),
        "",
        "## Future Requirements",
        "",
        _md_table(
            [["requirement", "required_before", "current_status"]]
            + [[row["requirement"], row["required_before"], row["current_status"]] for row in future_requirements]
        ),
        "",
        "## Blocked Actions",
        "",
        _md_table(
            [["blocked_action", "reason", "allowed_after"]]
            + [[row["blocked_action"], row["reason"], row["allowed_after"]] for row in blocked_actions]
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


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"dashboard marker not found: {old[:80]}")
    return text.replace(old, new, 1)


def _update_dashboard(path: Path, report: dict[str, Any], artifacts: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    text = _replace_once(text, '<div class="value">S2 re-audit stopped</div>', '<div class="value">S2 closed</div>')
    text = _replace_once(
        text,
        '<div class="note">已按 explicit go 执行 S2 accepted-OSS + S8 constrained dev/OOF re-audit；最好候选 positive net=1 但只有 1 个独立 source_family，未通过重开门槛。</div>',
        '<div class="note">S2 constrained re-audit 已正式收口：positive net=1 但只有 1 个独立 source_family，不能训练或实现。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">当前 stop condition 触发：独立 accepted OSS source_family 不足，S2 不训练、不实现、不进 heldout/hard。</div>',
        '<div class="note">后续只有新的 accepted-source positive dev/OOF evidence 同时满足 net&gt;0 和至少 2 个独立 source_family，才能重开 S2。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">S2 constrained re-audit 已执行：用 S8 判重后无候选通过 accepted-OSS positive net + 独立 source_family gate。</div>',
        '<div class="route-note">S2 constrained re-audit 已收口：0 个候选通过 gate，当前回到等待新 evidence / explicit direction。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>S2 re-audit stopped；await new evidence。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>S2 closed；await new evidence。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">S2 accepted-OSS + S8 constrained dev/OOF re-audit / experiment</td>
            <td><span class="pill paused">stopped</span></td>
            <td>Dev/OOF-only constrained re-audit of existing S2 ranking experiment outputs using accepted OSS non-generated sources and S8 source-family dedup.</td>
            <td>execution_decision=stop_do_not_train_or_implement; pass_candidate_count=0; best_accepted_oss_positive_net=1; best_positive_independent_source_family_count=1.</td>
            <td>Stop condition triggered. Do not train, implement, use heldout/hard, change GoalSearcher, or claim Top1 gain.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">S2 accepted-OSS + S8 constrained dev/OOF re-audit / experiment</td>
            <td><span class="pill done">done</span></td>
            <td>Dev/OOF-only constrained re-audit of existing S2 ranking experiment outputs using accepted OSS non-generated sources and S8 source-family dedup.</td>
            <td>execution_decision=stop_do_not_train_or_implement; pass_candidate_count=0; best_accepted_oss_positive_net=1; best_positive_independent_source_family_count=1.</td>
            <td>Stop condition triggered; closed by the S2 re-audit stop closure.</td>
          </tr>
          <tr>
            <td class="stage">S2 accepted-OSS + S8 re-audit stop closure</td>
            <td><span class="pill paused">paused</span></td>
            <td>Close S2 after constrained dev/OOF re-audit and preserve exact future re-entry requirements.</td>
            <td>closure_decision=keep_S2_closed_await_new_accepted_source_positive_evidence; pass_candidate_count=0; best_accepted_oss_positive_net=1; best_positive_independent_source_family_count=1.</td>
            <td>No automatic next S2 stage. Resume only with new accepted-source positive dev/OOF evidence satisfying S8 independent source-family gates.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
当前状态：S2 accepted-OSS + S8 re-audit stop closure 已完成。closure_decision={metrics["closure_decision"]}；pass_candidate_count=0；best_accepted_oss_positive_net={metrics["best_accepted_oss_positive_net"]}；best_positive_independent_source_family_count={metrics["best_positive_independent_source_family_count"]}；training_allowed=false；implementation_allowed=false；heldout_selection_allowed=false；goal_searcher_change_allowed=false。
不要继续自动推进 S2。只有提供新的 accepted-source positive dev/OOF evidence，且 non_generated_positive_net > 0、至少 2 个 independent source_family、generated share 不主导时，才可开新的 S2 re-entry review。
禁止：训练、调参、实现、重开 heldout/hard selection、改 GoalSearcher、编辑 feature whitelist、上线、把 source-dominated 或独立来源不足的结果宣称为 Top1 gain。
如果没有新 evidence 或新的明确方向，停止并报告当前阻塞点。"""
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )

    artifact_marker = """          <tr>
            <td>OOF safety gate summary</td>"""
    if "S2 accepted-OSS + S8 re-audit stop closure summary" not in text:
        artifact_rows = f"""          <tr>
            <td>S2 accepted-OSS + S8 re-audit stop closure summary</td>
            <td>Closure summary preserving S2 stop conditions and future re-entry requirements.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 re-audit stop closure report</td>
            <td>Human-readable closure report with closure checks, future requirements, blocked actions, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 re-audit stop closure tables</td>
            <td>Closure checks, future evidence requirements, next options, and blocked actions.</td>
            <td><code>{Path(artifacts["closure_checks_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["future_requirements_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["next_options_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["blocked_actions_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 re-audit stop closure script</td>
            <td>Closure script; it does not train, tune, run heldout/hard selection, change GoalSearcher, or edit feature whitelists.</td>
            <td><code>tools/goal_10x_s2_accepted_oss_s8_reaudit_stop_closure.py</code></td>
          </tr>
""" + artifact_marker
        text = _replace_once(text, artifact_marker, artifact_rows)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\\.",
        f"Last updated: {stamp} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Close S2 accepted-OSS + S8 constrained re-audit")
    parser.add_argument("--reaudit-summary", default=str(DEFAULT_REAUDIT_SUMMARY))
    parser.add_argument("--gate-checks", default=str(DEFAULT_GATE_CHECKS))
    parser.add_argument("--stop-decision", default=str(DEFAULT_STOP_DECISION))
    parser.add_argument("--candidate-scorecard", default=str(DEFAULT_CANDIDATE_SCORECARD))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    reaudit_summary = _read_json(Path(args.reaudit_summary))
    gate_checks_input = _read_csv(Path(args.gate_checks))
    stop_decision_input = _read_csv(Path(args.stop_decision))
    candidate_scorecard = _read_csv(Path(args.candidate_scorecard))
    metrics_in = reaudit_summary["metrics"]

    pass_candidate_count = _int(metrics_in.get("pass_candidate_count"))
    best_positive_net = _int(metrics_in.get("best_accepted_oss_positive_net"))
    best_family_count = _int(metrics_in.get("best_positive_independent_source_family_count"))
    stop_triggered = bool(metrics_in.get("stop_condition_triggered"))
    best_candidate_id = str(metrics_in.get("best_candidate_id", ""))

    closure_checks = [
        {
            "check_id": "CL01_REAUDIT_COMPLETED",
            "status": "pass" if metrics_in.get("execution_decision") == "stop_do_not_train_or_implement" else "fail",
            "evidence": f"execution_decision={metrics_in.get('execution_decision')}; candidate_count={metrics_in.get('candidate_count')}",
            "decision": "Constrained re-audit completed and selected stop.",
        },
        {
            "check_id": "CL02_STOP_CONDITION_CONFIRMED",
            "status": "pass" if stop_triggered and pass_candidate_count == 0 else "fail",
            "evidence": f"stop_condition_triggered={stop_triggered}; pass_candidate_count={pass_candidate_count}",
            "decision": "No candidate passes both accepted-OSS positive-net and S8 independent-family gates.",
        },
        {
            "check_id": "CL03_POSITIVE_NET_TOO_NARROW",
            "status": "pass" if best_positive_net > 0 and best_family_count < 2 else "fail",
            "evidence": f"best_candidate_id={best_candidate_id}; best_accepted_oss_positive_net={best_positive_net}; best_positive_independent_source_family_count={best_family_count}",
            "decision": "The best signal is too narrow for S2 re-entry.",
        },
        {
            "check_id": "CL04_NO_EXECUTION_ESCALATION",
            "status": "pass" if not metrics_in.get("training_executed") and not metrics_in.get("implementation_allowed") else "fail",
            "evidence": f"training_executed={metrics_in.get('training_executed')}; implementation_allowed={metrics_in.get('implementation_allowed')}; heldout_used_for_selection={metrics_in.get('heldout_used_for_selection')}",
            "decision": "Closure does not authorize training, implementation, or heldout/hard selection.",
        },
    ]
    future_requirements = [
        {
            "requirement": "accepted_source_positive_dev_oof_effect",
            "required_before": "any S2 re-entry review",
            "current_status": f"best_accepted_oss_positive_net={best_positive_net}; pass_candidate_count={pass_candidate_count}",
        },
        {
            "requirement": "at_least_two_independent_source_families",
            "required_before": "any S2 re-entry review",
            "current_status": f"best_positive_independent_source_family_count={best_family_count}; required>=2",
        },
        {
            "requirement": "generated_share_not_dominant",
            "required_before": "any S2 re-entry review",
            "current_status": f"best_generated_positive_net={metrics_in.get('best_generated_positive_net')}",
        },
        {
            "requirement": "explicit_future_execution_or_implementation_go",
            "required_before": "any training or implementation",
            "current_status": "missing",
        },
    ]
    next_options = [
        {
            "option": "keep_S2_closed_await_new_evidence",
            "status": "selected",
            "rationale": "No candidate passed the accepted-OSS + S8 constrained gates.",
        },
        {
            "option": "train_or_implement_S2_now",
            "status": "blocked",
            "rationale": "Independent source-family gate failed and no implementation authorization exists.",
        },
        {
            "option": "run_heldout_or_hard_validation",
            "status": "blocked",
            "rationale": "Heldout/hard were explicitly excluded from selection and should not be used after a failed source gate.",
        },
        {
            "option": "define_new_strategy_direction",
            "status": "available_only_by_explicit_user_request",
            "rationale": "Requires a new user direction; should not be auto-invented from a stopped S2 lane.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "train_or_tune_S2",
            "reason": "No candidate passed accepted-OSS + S8 independent source-family gates.",
            "allowed_after": "new accepted-source positive dev/OOF evidence with positive net across at least two independent source families",
        },
        {
            "blocked_action": "implement_or_change_GoalSearcher",
            "reason": "S2 stop closure is not an implementation stage.",
            "allowed_after": "future validation pass and explicit implementation go",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard remain forbidden for selection.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain",
            "reason": "The only positive accepted-OSS signal is too narrow: positive net=1 from one independent source family.",
            "allowed_after": "future accepted-source effect audit with robust independent source-family support",
        },
    ]

    fail_count = sum(1 for row in closure_checks if row["status"] != "pass")
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_checks_csv": str(output_prefix.with_name(output_prefix.name + "_closure_checks.csv")),
        "future_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_future_requirements.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": reaudit_summary["stage"],
        "closure_decision": "keep_S2_closed_await_new_accepted_source_positive_evidence",
        "best_candidate_id": best_candidate_id,
        "best_accepted_oss_positive_net": best_positive_net,
        "best_positive_independent_source_family_count": best_family_count,
        "pass_candidate_count": pass_candidate_count,
        "candidate_count": len(candidate_scorecard),
        "input_gate_check_count": len(gate_checks_input),
        "input_stop_decision_count": len(stop_decision_input),
        "closure_pass_count": len(closure_checks) - fail_count,
        "closure_fail_count": fail_count,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "feature_whitelist_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / S2 accepted-OSS + S8 re-audit stop closure",
        "closure_only": True,
        "dev_oof_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Keep S2 closed. The constrained re-audit found a tiny accepted-OSS positive signal "
            "(positive net=1) but it is supported by only one independent S8 source_family, so it cannot justify training, implementation, heldout/hard validation, or a Top1 gain claim."
        ),
        "anti_drift_conclusion": (
            "This closure only records the S2 stop condition and future re-entry requirements. It does not train, tune, expand candidate matrices, use heldout/hard for selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, connect online, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "stopped awaiting new accepted-source positive evidence or different explicit direction",
            "goal": "Resume S2 only if future evidence passes positive-net and independent-source-family gates.",
            "default": "stop",
        },
    }

    _write_csv(Path(artifacts["closure_checks_csv"]), closure_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["future_requirements_csv"]), future_requirements, ["requirement", "required_before", "current_status"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_checks, future_requirements, blocked_actions)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
