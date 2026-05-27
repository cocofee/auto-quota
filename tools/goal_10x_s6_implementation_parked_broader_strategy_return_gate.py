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
DEFAULT_1067_SUMMARY = AGENT_STATE / "goal_10x_s6_implementation_held_request_closure_summary.json"
DEFAULT_1067_NEXT_OPTIONS = AGENT_STATE / "goal_10x_s6_implementation_held_request_closure_next_options.csv"
DEFAULT_1067_REOPEN = AGENT_STATE / "goal_10x_s6_implementation_held_request_closure_reopen_requirements.csv"
DEFAULT_1064_ROLLUP = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_scope_rollup.csv"
DEFAULT_OWNER_TEMPLATE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_owner_mapping_template.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_implementation_parked_broader_strategy_return_gate"


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
    closure_decision: list[dict[str, Any]],
    parked_requirements: list[dict[str, Any]],
    broader_strategy_options: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.68 S6 Implementation Parked And Broader Strategy Return Gate",
        "",
        "Read-only parking decision for the S6 parser/taxonomy implementation lane after the user delegated the choice.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_decision", metrics["closure_decision"]],
                ["s6_lane_status", metrics["s6_lane_status"]],
                ["owner_mapping_template_rows", metrics["owner_mapping_template_rows"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["return_to_broader_strategy_now", metrics["return_to_broader_strategy_now"]],
                ["selected_next_route", metrics["selected_next_route"]],
            ]
        ),
        "",
        "## Closure Decision",
        "",
        _md_table(
            [["decision", "status", "rationale"]]
            + [[row["decision"], row["status"], row["rationale"]] for row in closure_decision]
        ),
        "",
        "## Parked Requirements",
        "",
        _md_table(
            [["requirement", "required_count", "status", "preserved_for"]]
            + [[row["requirement"], row["required_count"], row["status"], row["preserved_for"]] for row in parked_requirements]
        ),
        "",
        "## Broader Strategy Options",
        "",
        _md_table(
            [["route", "status", "why", "blocked_boundary"]]
            + [[row["route"], row["status"], row["why"], row["blocked_boundary"]] for row in broader_strategy_options]
        ),
        "",
        "## Next Gate",
        "",
        _md_table(
            [["next_stage", "goal", "default", "not_allowed"]]
            + [[row["next_stage"], row["goal"], row["default"], row["not_allowed"]] for row in next_gate]
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
    text = _replace_once(text, '<div class="value">10.67 S6 paused</div>', '<div class="value">10.68 S6 parked</div>')
    text = _replace_once(
        text,
        '<div class="note">S6 implementation request loop 已收口：等待 explicit go + 16 条 owner mappings，或用户明确转回 broader strategy。</div>',
        '<div class="note">已按用户授权选择 broader strategy：S6 implementation lane 正式 parked，16 条 owner mappings 作为未来重开条件保留。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">每小时 Goal read-only auto advance 已暂停；后续不得自动跑 heldout 选择、上线或改 GoalSearcher。</div>',
        '<div class="note">当前回到 broader 10.x strategy review；仍不得自动训练、实现、跑 heldout/hard selection、上线或改 GoalSearcher。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.67 已收口 S6 held 状态；默认暂停，不自动实现、不自动转 broader strategy。</div>',
        '<div class="route-note">10.68 已正式 park S6 implementation，保留 16 条 owner mapping 重开条件，并转回 broader strategy review。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.67 S6 paused；await go+mappings or redirect。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.68 S6 parked；return to broader strategy。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.67 S6 implementation held / request closure</td>
            <td><span class="pill paused">paused</span></td>
            <td>Read-only close the S6 implementation request loop: keep held unless explicit go plus complete owner mappings are provided, or user redirects to broader strategy.</td>
            <td>closure_decision=pause_await_explicit_go_plus_owner_mappings; explicit_go_present=false; owner_mappings_complete=false; owner_pending_rows=16; owner_after_values_missing=16; owner_rationales_missing=16; implementation_ready_rows=0; return_to_broader_strategy_now=false.</td>
            <td>Stop automatic S6 implementation progress. Required to continue: explicit go + 16 complete owner mappings, or explicit user redirect to broader strategy.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.67 S6 implementation held / request closure</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only close the S6 implementation request loop: keep held unless explicit go plus complete owner mappings are provided, or user redirects to broader strategy.</td>
            <td>closure_decision=pause_await_explicit_go_plus_owner_mappings; explicit_go_present=false; owner_mappings_complete=false; owner_pending_rows=16; owner_after_values_missing=16; owner_rationales_missing=16; implementation_ready_rows=0; return_to_broader_strategy_now=false.</td>
            <td>Closed as held; user then delegated the choice between mappings vs broader strategy.</td>
          </tr>
          <tr>
            <td class="stage">10.68 S6 implementation parked / broader strategy return gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only park S6 implementation lane and choose broader 10.x strategy review because explicit go and 16 owner mappings are unavailable.</td>
            <td>closure_decision=park_s6_return_to_broader_strategy; owner_mapping_template_rows=16; owner_after_values_missing=16; implementation_allowed=false; return_to_broader_strategy_now=true.</td>
            <td>Next: 10.69 broader 10.x strategy review after S6 parking. Still no training, implementation, parser/taxonomy edits, GoalSearcher change, or heldout/hard selection.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
只做当前阶段，不扩展新方向。
本轮状态：10.68 S6 implementation parked / broader strategy return gate 已完成。closure_decision={metrics["closure_decision"]}；s6_lane_status={metrics["s6_lane_status"]}；owner_mapping_template_rows={metrics["owner_mapping_template_rows"]}；owner_after_values_missing={metrics["owner_after_values_missing"]}；explicit_go_present={str(metrics["explicit_go_present"]).lower()}；implementation_allowed=false；return_to_broader_strategy_now=true；selected_next_route={metrics["selected_next_route"]}。S6 不实现，只保留未来重开条件。
下一步：10.69 broader 10.x strategy review after S6 parking。只读选择下一条不依赖 owner mappings、不重开 S1/S2/S3/S6 implementation、不需要立即训练或实现的策略路线。
禁止：训练、调参、实现 parser/taxonomy/DQ 修复、改阈值、写规则、改 GoalSearcher、编辑 feature whitelist、跑 heldout/hard selection、上线、把 S6 planning candidates 当学习证据或 Top1 gain。
结束时必须更新 HTML 看板，并报告：改动文件、产物、命令、指标、防跑偏结论、下一步。"""
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )

    artifact_marker = """          <tr>
            <td>OOF safety gate summary</td>"""
    artifact_rows = f"""          <tr>
            <td>10.68 S6 implementation parked / broader strategy return gate summary</td>
            <td>Read-only parking summary; preserves S6 reopen requirements and selects return to broader 10.x strategy review.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.68 S6 implementation parked / broader strategy return gate report</td>
            <td>Human-readable 10.68 report with closure decision, parked requirements, broader strategy options, next gate, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.68 S6 implementation parked / broader strategy return gate tables</td>
            <td>Closure decision, parked requirements, broader strategy options, next gate, and blocked actions.</td>
            <td><code>{Path(artifacts["closure_decision_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["parked_requirements_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["broader_strategy_options_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["next_gate_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.68 S6 implementation parked / broader strategy return gate script</td>
            <td>Read-only parking script; it does not train, tune, expand candidate matrices, run heldout/hard selection, change GoalSearcher, edit parser/taxonomy rules, or edit feature whitelists.</td>
            <td><code>tools/goal_10x_s6_implementation_parked_broader_strategy_return_gate.py</code></td>
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
    parser = argparse.ArgumentParser(description="Park S6 implementation lane and return to broader strategy review")
    parser.add_argument("--summary-1067", default=str(DEFAULT_1067_SUMMARY))
    parser.add_argument("--next-options-1067", default=str(DEFAULT_1067_NEXT_OPTIONS))
    parser.add_argument("--reopen-requirements-1067", default=str(DEFAULT_1067_REOPEN))
    parser.add_argument("--scope-rollup-1064", default=str(DEFAULT_1064_ROLLUP))
    parser.add_argument("--owner-template", default=str(DEFAULT_OWNER_TEMPLATE))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1067 = _read_json(Path(args.summary_1067))
    next_options_1067 = _read_csv(Path(args.next_options_1067))
    reopen_requirements_input = _read_csv(Path(args.reopen_requirements_1067))
    scope_rollup = _read_csv(Path(args.scope_rollup_1064))
    owner_template = _read_csv(Path(args.owner_template))
    m1067 = summary_1067["metrics"]

    owner_mapping_template_rows = len(owner_template)
    owner_after_values_missing = sum(1 for row in owner_template if not row.get("proposed_after_value"))
    owner_rationales_missing = sum(1 for row in owner_template if not row.get("owner_rationale"))
    owner_pending_rows = sum(1 for row in owner_template if row.get("owner_decision") == "pending_accept_or_reject")
    parser_planning_rows = sum(_int(row.get("candidate_rows")) for row in scope_rollup if row.get("planned_fix_lane") == "parser_query_family_hint_planning")
    taxonomy_planning_rows = sum(_int(row.get("candidate_rows")) for row in scope_rollup if row.get("planned_fix_lane") == "taxonomy_top1_family_coverage_planning")
    redirect_available = any(row.get("option") == "return_to_broader_strategy_review" for row in next_options_1067)

    closure_decision = [
        {
            "decision": "provide_explicit_go_plus_16_owner_mappings",
            "status": "not_selected",
            "rationale": "This requires owner authority and concrete after-values/rationales; current package has none.",
        },
        {
            "decision": "return_to_broader_strategy_review",
            "status": "selected_user_delegated_choice",
            "rationale": "User asked Codex to choose; S6 is blocked on owner mappings, so parking it avoids fake implementation and keeps progress moving.",
        },
        {
            "decision": "implement_s6_now",
            "status": "blocked",
            "rationale": "No explicit go, no complete owner mappings, and no implementation-ready rows.",
        },
    ]

    parked_requirements = [
        {
            "requirement": row.get("requirement", ""),
            "required_count": row.get("required_count", ""),
            "status": row.get("status", ""),
            "preserved_for": "future_s6_reopen_only",
        }
        for row in reopen_requirements_input
    ]

    broader_strategy_options = [
        {
            "route": "broader_10x_strategy_review_after_s6_parking",
            "status": "selected_next_route",
            "why": "Does not require inventing owner mappings and can choose a non-implementation strategy lane from existing evidence.",
            "blocked_boundary": "must remain read-only until a later explicit execution or implementation go exists",
        },
        {
            "route": "wait_for_owner_mappings",
            "status": "parked",
            "why": "Valid future S6 reopen path, but it stalls current progress because all 16 mappings are missing.",
            "blocked_boundary": "requires explicit go plus complete owner mapping package",
        },
        {
            "route": "resume_s1_s2_s3_execution",
            "status": "not_selected",
            "why": "Prior gates parked these lanes without accepted independent evidence or explicit execution go.",
            "blocked_boundary": "no training, tuning, heldout/hard selection, or candidate matrix expansion",
        },
        {
            "route": "implement_dq_or_parser_fixes",
            "status": "blocked",
            "why": "S6/DQ fixes need owner accepted mappings and implementation authorization.",
            "blocked_boundary": "no parser/taxonomy/DQ edits",
        },
    ]

    next_gate = [
        {
            "next_stage": "10.69 broader 10.x strategy review after S6 parking",
            "goal": "Read-only choose the next strategy lane that does not depend on S6 owner mappings or immediate training/implementation.",
            "default": "strategy review only",
            "not_allowed": "no training, no tuning, no implementation, no parser/taxonomy edits, no GoalSearcher change, no heldout/hard selection",
        }
    ]

    blocked_actions = [
        {
            "blocked_action": "fabricate_owner_mappings",
            "reason": "Owner mappings are domain decisions; 16 proposed_after_value and rationales are missing.",
            "allowed_after": "owner supplies complete accepted/rejected mappings",
        },
        {
            "blocked_action": "implement_s6_parser_taxonomy_fixes",
            "reason": "implementation_allowed=false and S6 is parked.",
            "allowed_after": "future explicit go plus complete owner mappings and implementation gate",
        },
        {
            "blocked_action": "train_or_tune_from_s6_planning",
            "reason": "S6 planning candidates are DQ/parser support only, not learning evidence.",
            "allowed_after": "separate future learning re-entry gate with accepted evidence",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "10.68 is a read-only route decision and cannot select on heldout/hard sets.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_decision_csv": str(output_prefix.with_name(output_prefix.name + "_closure_decision.csv")),
        "parked_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_parked_requirements.csv")),
        "broader_strategy_options_csv": str(output_prefix.with_name(output_prefix.name + "_broader_strategy_options.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1067["stage"],
        "closure_decision": "park_s6_return_to_broader_strategy",
        "s6_lane_status": "parked",
        "user_delegated_choice": True,
        "redirect_option_available_from_1067": redirect_available,
        "explicit_go_present": bool(m1067.get("explicit_go_present")),
        "owner_mappings_complete": bool(m1067.get("owner_mappings_complete")),
        "owner_mapping_template_rows": owner_mapping_template_rows,
        "owner_pending_rows": owner_pending_rows,
        "owner_after_values_missing": owner_after_values_missing,
        "owner_rationales_missing": owner_rationales_missing,
        "parser_planning_rows": parser_planning_rows,
        "taxonomy_planning_rows": taxonomy_planning_rows,
        "implementation_ready_rows": _int(m1067.get("implementation_ready_rows")),
        "implementation_allowed": False,
        "return_to_broader_strategy_now": True,
        "selected_next_route": "10.69 broader_10x_strategy_review_after_s6_parking",
        "reopen_requirement_count": len(parked_requirements),
        "broader_strategy_option_count": len(broader_strategy_options),
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.68 S6 implementation parked and broader strategy return gate",
        "read_only": True,
        "parking_and_route_selection_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Park the S6 parser/taxonomy implementation lane because explicit go is absent and all 16 owner mappings remain incomplete. "
            "The selected path is to return to broader 10.x strategy review rather than fabricate mappings or implement fixes."
        ),
        "anti_drift_conclusion": (
            "10.68 only records a route decision and preserves S6 reopen requirements. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.69 broader 10.x strategy review after S6 parking",
            "goal": "Read-only choose the next non-implementation strategy lane that does not depend on S6 owner mappings.",
            "default": "strategy review only",
        },
    }

    _write_csv(Path(artifacts["closure_decision_csv"]), closure_decision, ["decision", "status", "rationale"])
    _write_csv(Path(artifacts["parked_requirements_csv"]), parked_requirements, ["requirement", "required_count", "status", "preserved_for"])
    _write_csv(Path(artifacts["broader_strategy_options_csv"]), broader_strategy_options, ["route", "status", "why", "blocked_boundary"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "goal", "default", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_decision, parked_requirements, broader_strategy_options, next_gate)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
