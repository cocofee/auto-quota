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
DEFAULT_1072_SUMMARY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate_summary.json"
DEFAULT_1072_NEXT_OPTIONS = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate_next_options.csv"
DEFAULT_1072_SCOPE = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate_support_contract_scope.csv"
DEFAULT_1069_LANE_RECHECK = AGENT_STATE / "goal_10x_broader_strategy_review_after_s6_parking_lane_recheck.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_closure_after_s8_support_contract_acceptance"


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


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _md_table(rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, object],
    lane_closure: list[dict[str, object]],
    remaining_lane_review: list[dict[str, object]],
    pause_conditions: list[dict[str, object]],
    reentry_requirements: list[dict[str, object]],
) -> None:
    metrics = report["metrics"]  # type: ignore[index]
    lines = [
        "# 10.73 Broader Strategy Closure After S8 Support-Contract Acceptance",
        "",
        "Read-only broader strategy closure/review after accepting the S8 source-family independence support contract.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_decision", metrics["closure_decision"]],  # type: ignore[index]
                ["s8_lane_closed", metrics["s8_lane_closed"]],  # type: ignore[index]
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],  # type: ignore[index]
                ["remaining_non_execution_lane_count", metrics["remaining_non_execution_lane_count"]],  # type: ignore[index]
                ["pause_10x_loop_now", metrics["pause_10x_loop_now"]],  # type: ignore[index]
                ["reentry_requirement_count", metrics["reentry_requirement_count"]],  # type: ignore[index]
            ]
        ),
        "",
        "## Lane Closure",
        "",
        _md_table(
            [["lane", "status", "closure_decision", "evidence"]]
            + [[row["lane"], row["status"], row["closure_decision"], row["evidence"]] for row in lane_closure]
        ),
        "",
        "## Remaining Lane Review",
        "",
        _md_table(
            [["lane", "status", "blocking_condition", "review_decision"]]
            + [[row["lane"], row["status"], row["blocking_condition"], row["review_decision"]] for row in remaining_lane_review]
        ),
        "",
        "## Pause Conditions",
        "",
        _md_table(
            [["condition", "status", "effect"]]
            + [[row["condition"], row["status"], row["effect"]] for row in pause_conditions]
        ),
        "",
        "## Re-entry Requirements",
        "",
        _md_table(
            [["lane", "required_input", "minimum_gate"]]
            + [[row["lane"], row["required_input"], row["minimum_gate"]] for row in reentry_requirements]
        ),
        "",
        "## Decision",
        "",
        str(report["decision"]),
        "",
        "## Anti-drift",
        "",
        str(report["anti_drift_conclusion"]),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"dashboard marker not found: {old[:80]}")
    return text.replace(old, new, 1)


def _update_dashboard(path: Path, report: dict[str, object], artifacts: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]  # type: ignore[index]
    text = _replace_once(text, '<div class="value">10.72 S8 accepted</div>', '<div class="value">10.73 paused</div>')
    text = _replace_once(
        text,
        '<div class="note">S8 registry artifact 已接受为 future S1/S2 evidence-quality support contract；仍不允许 learning re-entry。</div>',
        '<div class="note">10.73 已收口 S8；当前没有剩余可推进的非执行策略路线，暂停等待新 evidence 或 explicit go。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">下一步回到 broader strategy closure/review；不接受新来源、不训练、不实现、不声明 Top1 gain。</div>',
        '<div class="note">只有新的 accepted evidence、owner mappings、owner provenance package 或 explicit go 才能重开对应 lane。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.72 已接受 S8 registry artifact 为 evidence-quality support contract，但不打开 learning re-entry。</div>',
        '<div class="route-note">10.73 已完成 broader closure：S8 收口，10.x loop 暂停等待新 evidence/explicit go。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.72 S8 accepted；next broader review。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.73 paused；await evidence/go。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.72 S8 source-family independence registry artifact acceptance gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only decide whether the S8 registry artifact is acceptable as future S1/S2 evidence-quality support contract.</td>
            <td>artifact_acceptance_decision=accept_as_support_contract; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; acceptance_pass_count=7; acceptance_fail_count=0.</td>
            <td>Next: 10.73 broader 10.x strategy closure/review after S8 support-contract acceptance. Still no learning re-entry, training, implementation, source acceptance, or heldout/hard selection.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.72 S8 source-family independence registry artifact acceptance gate</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only decide whether the S8 registry artifact is acceptable as future S1/S2 evidence-quality support contract.</td>
            <td>artifact_acceptance_decision=accept_as_support_contract; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; acceptance_pass_count=7; acceptance_fail_count=0.</td>
            <td>Accepted as support contract only; no learning re-entry, source acceptance, or implementation.</td>
          </tr>
          <tr>
            <td class="stage">10.73 broader 10.x strategy closure/review after S8 support-contract acceptance</td>
            <td><span class="pill paused">paused</span></td>
            <td>Read-only close S8 and decide whether a remaining non-execution route exists.</td>
            <td>closure_decision=pause_10x_loop_await_new_evidence_or_explicit_go; active_learning_lane_count=0; remaining_non_execution_lane_count=0; pause_10x_loop_now=true.</td>
            <td>No automatic next learning stage. Resume only with lane-specific accepted evidence, complete owner mappings/provenance package, or explicit execution/implementation go.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
当前状态：10.73 broader 10.x strategy closure/review after S8 support-contract acceptance 已完成。closure_decision={metrics["closure_decision"]}；s8_lane_closed=true；active_learning_lane_count=0；remaining_non_execution_lane_count=0；pause_10x_loop_now=true；reentry_requirement_count={metrics["reentry_requirement_count"]}；training_allowed=false；implementation_allowed=false；heldout_selection_allowed=false；goal_searcher_change_allowed=false。
不要继续自动推进 learning stages。只有提供以下之一才继续：S1/S2 的 accepted-source positive dev/OOF effect evidence package、S3 explicit execution go、S6/DQ complete owner mappings + explicit implementation go、OSS owner/source provenance package，或用户明确要求定义新的 broader strategy lane。
禁止：接受新来源、重开 OSS expansion、重开 S1/S2/S3/S6 execution、训练、调参、实现 parser/taxonomy/DQ 修复、改阈值、写规则、改 GoalSearcher、编辑 feature whitelist、跑 heldout/hard selection、上线或声明 Top1 gain。
如果没有新输入，停止并报告当前阻塞点、已接受的 support contracts、还需要什么信息或权限。"""
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )

    artifact_marker = """          <tr>
            <td>OOF safety gate summary</td>"""
    if "10.73 broader strategy closure after S8 support-contract acceptance summary" not in text:
        artifact_rows = f"""          <tr>
            <td>10.73 broader strategy closure after S8 support-contract acceptance summary</td>
            <td>Read-only closure summary; closes S8 and pauses 10.x loop until new evidence or explicit go.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.73 broader strategy closure after S8 support-contract acceptance report</td>
            <td>Human-readable 10.73 report with lane closure, remaining lane review, pause conditions, re-entry requirements, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.73 broader strategy closure after S8 support-contract acceptance tables</td>
            <td>Lane closure, remaining lane review, pause conditions, re-entry requirements, next options, and blocked actions.</td>
            <td><code>{Path(artifacts["lane_closure_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["remaining_lane_review_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["pause_conditions_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["reentry_requirements_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.73 broader strategy closure after S8 support-contract acceptance script</td>
            <td>Read-only closure script; it does not accept sources, train, tune, run heldout/hard selection, change GoalSearcher, or edit parser/taxonomy rules.</td>
            <td><code>tools/goal_10x_broader_strategy_closure_after_s8_support_contract_acceptance.py</code></td>
          </tr>
""" + artifact_marker
        text = _replace_once(text, artifact_marker, artifact_rows)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {stamp} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Close broader strategy review after S8 support-contract acceptance")
    parser.add_argument("--summary-1072", default=str(DEFAULT_1072_SUMMARY))
    parser.add_argument("--next-options-1072", default=str(DEFAULT_1072_NEXT_OPTIONS))
    parser.add_argument("--scope-1072", default=str(DEFAULT_1072_SCOPE))
    parser.add_argument("--lane-recheck-1069", default=str(DEFAULT_1069_LANE_RECHECK))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1072 = _read_json(Path(args.summary_1072))
    next_options_1072 = _read_csv(Path(args.next_options_1072))
    support_scope_1072 = _read_csv(Path(args.scope_1072))
    lane_recheck_1069 = _read_csv(Path(args.lane_recheck_1069))
    m1072 = summary_1072["metrics"]

    lane_closure = [
        {
            "lane": "S8_source_family_independence_registry",
            "status": "accepted_as_support_contract_closed",
            "closure_decision": "close_lane_no_learning_reentry",
            "evidence": f"s8_support_contract_accepted={m1072.get('s8_support_contract_accepted')}; learning_reentry_allowed={m1072.get('learning_reentry_allowed')}",
        },
        {
            "lane": "S5_measurement_integrity_slice_telemetry",
            "status": "accepted_as_support_contract_closed",
            "closure_decision": "reuse_as_guardrail_only",
            "evidence": "S5 support contract already accepted; no implementation authorization.",
        },
        {
            "lane": "S7_rank_position_candidate_pool_diagnostics",
            "status": "accepted_as_strategy_support_closed",
            "closure_decision": "reuse_as_context_only",
            "evidence": "S7 diagnostics already accepted and used to select S6.",
        },
    ]
    remaining_lane_review = lane_recheck_1069 + [
        {
            "lane": "S8_source_family_independence_registry",
            "status": "accepted_as_support_contract",
            "blocking_condition": "support contract accepted but no positive effect package, no source acceptance go, and no implementation go",
            "review_decision": "close_and_pause",
        }
    ]

    pause_conditions = [
        {
            "condition": "no_active_learning_lane",
            "status": "active",
            "effect": "pause_10x_loop_now",
        },
        {
            "condition": "support_contracts_are_not_learning_evidence",
            "status": "active",
            "effect": "do_not_reopen_S1_or_S2_without positive accepted-source effect evidence",
        },
        {
            "condition": "implementation_inputs_missing",
            "status": "active",
            "effect": "do_not_implement_DQ_S6_or_registry",
        },
        {
            "condition": "explicit_execution_go_missing",
            "status": "active",
            "effect": "do_not_execute_S2_or_S3",
        },
        {
            "condition": "heldout_hard_selection_forbidden",
            "status": "active",
            "effect": "do_not_select_on_heldout_or_hard",
        },
    ]
    reentry_requirements = [
        {
            "lane": "S1_recall_route_expansion",
            "required_input": "accepted-OSS non-generated recall evidence package",
            "minimum_gate": "positive dev/OOF recall net across independent source_family support; S8 counting applies",
        },
        {
            "lane": "S2_ranking_objective_and_feature_strategy",
            "required_input": "accepted-source positive dev/OOF ranking effect package",
            "minimum_gate": "non-generated positive net > 0, at least two independent source_family IDs, generated share not dominant",
        },
        {
            "lane": "S3_safety_gate_calibration_v2",
            "required_input": "explicit dev/OOF-only execution go",
            "minimum_gate": "OOF-only command boundary, loss budget, stop conditions, no heldout/hard selection",
        },
        {
            "lane": "S6_parser_taxonomy_implementation",
            "required_input": "explicit implementation go plus 16 complete owner mappings",
            "minimum_gate": "owner after-values/rationales complete and dry-run validation boundary defined",
        },
        {
            "lane": "DQ_implementation",
            "required_input": "explicit implementation go plus complete owner row mappings",
            "minimum_gate": "owner mappings accepted, rollback plan, dev/OOF dry-run loss audit",
        },
        {
            "lane": "OSS_expansion_provenance_lane",
            "required_input": "owner/source provenance package for new source files",
            "minimum_gate": "accepted provenance first, then accepted-source-only dev/OOF effect re-audit",
        },
        {
            "lane": "new_broader_strategy_lane",
            "required_input": "explicit user request to define a new non-execution strategy lane",
            "minimum_gate": "must not depend on owner mappings, training, implementation, source acceptance, or heldout/hard selection",
        },
    ]
    next_options = [
        {
            "option": "pause_10x_loop_await_new_evidence_or_explicit_go",
            "status": "selected",
            "rationale": "All current support/diagnostic lanes are accepted or closed, and execution/implementation lanes remain blocked.",
        },
        {
            "option": "define_new_broader_strategy_lane",
            "status": "available_only_by_explicit_user_request",
            "rationale": "A new lane should not be auto-invented now; it needs a fresh goal and must respect current blocked boundaries.",
        },
        {
            "option": "reopen_learning_or_execution",
            "status": "blocked",
            "rationale": "No lane-specific evidence package or explicit execution go is present.",
        },
        {
            "option": "implement_support_contracts",
            "status": "blocked",
            "rationale": "No implementation go and no online/GoalSearcher change authorization.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "auto_advance_learning_stages",
            "reason": "No active learning lane remains and support contracts do not satisfy re-entry.",
            "allowed_after": "new lane-specific evidence package, explicit go, or explicit new strategy request",
        },
        {
            "blocked_action": "train_or_tune",
            "reason": "No execution authorization or accepted positive effect package exists.",
            "allowed_after": "future explicit dev/OOF-only execution authorization",
        },
        {
            "blocked_action": "implement_dq_parser_taxonomy_or_registry",
            "reason": "Owner mappings/provenance packages and explicit implementation go are missing.",
            "allowed_after": "future explicit implementation go plus complete owner inputs",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain_from_support_contracts",
            "reason": "S5/S8 support contracts and S7 diagnostics do not include positive effect evidence.",
            "allowed_after": "future lane-specific dev/OOF effect audit and validation boundary",
        },
    ]

    active_learning_lane_count = 0
    remaining_non_execution_lane_count = 0
    pause_now = True
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "lane_closure_csv": str(output_prefix.with_name(output_prefix.name + "_lane_closure.csv")),
        "remaining_lane_review_csv": str(output_prefix.with_name(output_prefix.name + "_remaining_lane_review.csv")),
        "pause_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_pause_conditions.csv")),
        "reentry_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_requirements.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1072["stage"],
        "closure_decision": "pause_10x_loop_await_new_evidence_or_explicit_go",
        "s8_lane_closed": True,
        "s8_support_contract_accepted": _bool(m1072.get("s8_support_contract_accepted")),
        "support_contract_scope_count": len(support_scope_1072),
        "input_next_options_count": len(next_options_1072),
        "active_learning_lane_count": active_learning_lane_count,
        "remaining_non_execution_lane_count": remaining_non_execution_lane_count,
        "parked_or_blocked_lane_count": sum(1 for row in remaining_lane_review if "parked" in row.get("status", "") or "paused" in row.get("status", "")),
        "pause_condition_count": len(pause_conditions),
        "pause_10x_loop_now": pause_now,
        "reentry_requirement_count": len(reentry_requirements),
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "source_acceptance_allowed": False,
        "learning_reentry_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.73 broader 10.x strategy closure/review after S8 support-contract acceptance",
        "read_only": True,
        "closure_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Close the S8 lane and pause the 10.x loop awaiting new lane-specific evidence or explicit go. S5 and S8 are accepted support contracts, "
            "S7 is accepted diagnostic support, and the remaining S1/S2/S3/S6/DQ/OSS routes are parked or blocked by explicit missing inputs. "
            "Do not auto-invent another read-only lane without a new user request."
        ),
        "anti_drift_conclusion": (
            "10.73 only closes the broader strategy review after S8 support acceptance. It does not accept new sources, reopen OSS expansion, train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "paused awaiting new evidence, owner inputs, explicit go, or explicit new strategy request",
            "goal": "Stop automatic learning-stage advancement until a valid unblock input arrives.",
            "default": "pause",
        },
    }

    _write_csv(Path(artifacts["lane_closure_csv"]), lane_closure, ["lane", "status", "closure_decision", "evidence"])
    _write_csv(Path(artifacts["remaining_lane_review_csv"]), remaining_lane_review, ["lane", "status", "blocking_condition", "review_decision"])
    _write_csv(Path(artifacts["pause_conditions_csv"]), pause_conditions, ["condition", "status", "effect"])
    _write_csv(Path(artifacts["reentry_requirements_csv"]), reentry_requirements, ["lane", "required_input", "minimum_gate"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, lane_closure, remaining_lane_review, pause_conditions, reentry_requirements)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
