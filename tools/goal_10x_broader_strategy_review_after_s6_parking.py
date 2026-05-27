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
DEFAULT_1068_SUMMARY = AGENT_STATE / "goal_10x_s6_implementation_parked_broader_strategy_return_gate_summary.json"
DEFAULT_1068_OPTIONS = AGENT_STATE / "goal_10x_s6_implementation_parked_broader_strategy_return_gate_broader_strategy_options.csv"
DEFAULT_LANE_CANDIDATES = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_candidate_lanes.csv"
DEFAULT_1056_RECHECK = AGENT_STATE / "goal_10x_broader_strategy_review_after_oss_pause_lane_recheck.csv"
DEFAULT_S5_SUMMARY = AGENT_STATE / "goal_10x_s5_artifact_acceptance_gate_summary.json"
DEFAULT_S7_SUMMARY = AGENT_STATE / "goal_10x_s7_diagnostic_implications_next_lane_selection_summary.json"
DEFAULT_S6_SUMMARY = AGENT_STATE / "goal_10x_s6_implementation_parked_broader_strategy_return_gate_summary.json"
DEFAULT_PROVENANCE_SUMMARY = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_summary.json"
DEFAULT_ACCEPTED_SOURCES = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_sources.csv"
DEFAULT_OSS_PAUSE_SUMMARY = AGENT_STATE / "goal_10x_oss_provenance_gap_closure_pause_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_review_after_s6_parking"


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


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
    lane_recheck: list[dict[str, Any]],
    candidate_lanes: list[dict[str, Any]],
    selected_lane: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.69 Broader Strategy Review After S6 Parking",
        "",
        "Read-only strategy review after S6 implementation was parked.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s6_lane_status", metrics["s6_lane_status"]],
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],
                ["parked_or_blocked_lane_count", metrics["parked_or_blocked_lane_count"]],
                ["accepted_human_oss_source_file_count", metrics["accepted_human_oss_source_file_count"]],
                ["accepted_source_family_count", metrics["accepted_source_family_count"]],
                ["selected_next_lane", metrics["selected_next_lane"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Lane Recheck",
        "",
        _md_table(
            [["lane", "status", "blocking_condition", "review_decision"]]
            + [[row["lane"], row["status"], row["blocking_condition"], row["review_decision"]] for row in lane_recheck]
        ),
        "",
        "## Candidate Lanes",
        "",
        _md_table(
            [["lane_id", "decision", "score", "selection_reason"]]
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
    text = _replace_once(text, '<div class="value">10.68 S6 parked</div>', '<div class="value">10.69 broader review</div>')
    text = _replace_once(
        text,
        '<div class="note">已按用户授权选择 broader strategy：S6 implementation lane 正式 parked，16 条 owner mappings 作为未来重开条件保留。</div>',
        '<div class="note">S6 implementation 已 parked；本轮 broader review 选择 S8 source-family independence registry design gate。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">当前回到 broader 10.x strategy review；仍不得自动训练、实现、跑 heldout/hard selection、上线或改 GoalSearcher。</div>',
        '<div class="note">下一步仍是只读设计 gate：不接受新来源、不训练、不实现、不跑 heldout/hard selection。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.68 已正式 park S6 implementation，保留 16 条 owner mapping 重开条件，并转回 broader strategy review。</div>',
        '<div class="route-note">10.69 已完成 broader review：选择 S8 source-family independence registry design gate 作为下一条非执行路线。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.68 S6 parked；return to broader strategy。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.69 broader review；next S8 design gate。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.68 S6 implementation parked / broader strategy return gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only park S6 implementation lane and choose broader 10.x strategy review because explicit go and 16 owner mappings are unavailable.</td>
            <td>closure_decision=park_s6_return_to_broader_strategy; owner_mapping_template_rows=16; owner_after_values_missing=16; implementation_allowed=false; return_to_broader_strategy_now=true.</td>
            <td>Next: 10.69 broader 10.x strategy review after S6 parking. Still no training, implementation, parser/taxonomy edits, GoalSearcher change, or heldout/hard selection.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.68 S6 implementation parked / broader strategy return gate</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only park S6 implementation lane and choose broader 10.x strategy review because explicit go and 16 owner mappings are unavailable.</td>
            <td>closure_decision=park_s6_return_to_broader_strategy; owner_mapping_template_rows=16; owner_after_values_missing=16; implementation_allowed=false; return_to_broader_strategy_now=true.</td>
            <td>S6 is parked; future reopen still requires explicit go + 16 complete owner mappings.</td>
          </tr>
          <tr>
            <td class="stage">10.69 broader 10.x strategy review after S6 parking</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only select the next strategy lane that does not depend on S6 owner mappings or immediate training/implementation.</td>
            <td>active_learning_lane_count=0; parked_or_blocked_lane_count=7; selected_next_lane=S8_source_family_independence_registry_design; accepted_human_oss_source_file_count=6; accepted_source_family_count=2.</td>
            <td>Next: 10.70 S8 source-family independence registry design gate. No new source acceptance, no training, no implementation, no heldout/hard selection.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
只做当前阶段，不扩展新方向。
本轮状态：10.69 broader 10.x strategy review after S6 parking 已完成。selected_next_lane={metrics["selected_next_lane"]}；active_learning_lane_count=0；parked_or_blocked_lane_count={metrics["parked_or_blocked_lane_count"]}；accepted_human_oss_source_file_count={metrics["accepted_human_oss_source_file_count"]}；accepted_source_family_count={metrics["accepted_source_family_count"]}；s6_lane_status=parked；implementation_allowed=false；training_allowed=false；heldout_selection_allowed=false。
下一步：10.70 S8 source-family independence registry design gate。只读判断 S8 是否足够具体，能否用现有 accepted OSS/source_family/provenance_hash/dev/OOF artifacts 定义 source-family independence registry 的字段、判重规则、risk checks、re-entry support boundary 和 acceptance checks。
禁止：接受新来源、重开 OSS expansion、重开 S1/S2/S3/S6 execution、训练、调参、实现 parser/taxonomy/DQ 修复、改阈值、写规则、改 GoalSearcher、编辑 feature whitelist、跑 heldout/hard selection、上线或声明 Top1 gain。
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
            <td>10.69 broader strategy review after S6 parking summary</td>
            <td>Read-only broader strategy summary; selects S8 source-family independence registry design gate after parking S6.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.69 broader strategy review after S6 parking report</td>
            <td>Human-readable 10.69 report with lane recheck, candidate scoring, selected next lane, next gate, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.69 broader strategy review after S6 parking tables</td>
            <td>Lane recheck, candidate next lanes, selected next lane, next gate, and blocked actions.</td>
            <td><code>{Path(artifacts["lane_recheck_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["candidate_next_lanes_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["selected_next_lane_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["next_gate_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.69 broader strategy review after S6 parking script</td>
            <td>Read-only broader strategy script; it does not train, tune, reopen S6, run heldout/hard selection, change GoalSearcher, or edit parser/taxonomy rules.</td>
            <td><code>tools/goal_10x_broader_strategy_review_after_s6_parking.py</code></td>
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
    parser = argparse.ArgumentParser(description="Review broader 10.x strategy after S6 parking")
    parser.add_argument("--summary-1068", default=str(DEFAULT_1068_SUMMARY))
    parser.add_argument("--options-1068", default=str(DEFAULT_1068_OPTIONS))
    parser.add_argument("--lane-candidates", default=str(DEFAULT_LANE_CANDIDATES))
    parser.add_argument("--lane-recheck-1056", default=str(DEFAULT_1056_RECHECK))
    parser.add_argument("--s5-summary", default=str(DEFAULT_S5_SUMMARY))
    parser.add_argument("--s7-summary", default=str(DEFAULT_S7_SUMMARY))
    parser.add_argument("--s6-summary", default=str(DEFAULT_S6_SUMMARY))
    parser.add_argument("--provenance-summary", default=str(DEFAULT_PROVENANCE_SUMMARY))
    parser.add_argument("--accepted-sources", default=str(DEFAULT_ACCEPTED_SOURCES))
    parser.add_argument("--oss-pause-summary", default=str(DEFAULT_OSS_PAUSE_SUMMARY))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1068 = _read_json(Path(args.summary_1068))
    options_1068 = _read_csv(Path(args.options_1068))
    lane_candidates_input = _read_csv(Path(args.lane_candidates))
    prior_lane_recheck = _read_csv(Path(args.lane_recheck_1056))
    s5_summary = _read_json(Path(args.s5_summary))
    s7_summary = _read_json(Path(args.s7_summary))
    s6_summary = _read_json(Path(args.s6_summary))
    provenance_summary = _read_json(Path(args.provenance_summary))
    accepted_sources = _read_csv(Path(args.accepted_sources))
    oss_pause_summary = _read_json(Path(args.oss_pause_summary))

    m1068 = summary_1068["metrics"]
    ms5 = s5_summary["metrics"]
    ms7 = s7_summary["metrics"]
    ms6 = s6_summary["metrics"]
    mp = provenance_summary["metrics"]
    moss = oss_pause_summary["metrics"]

    lane_recheck = [
        {
            "lane": "S1_recall_route_expansion",
            "status": "parked_pending_independent_evidence",
            "blocking_condition": "no accepted-OSS non-generated recall evidence package and no active learnable slice",
            "review_decision": "do_not_reopen",
        },
        {
            "lane": "S2_ranking_objective_and_feature_strategy",
            "status": "parked_pending_independent_accepted_oss_evidence",
            "blocking_condition": "accepted OSS S2 positive net remains 0; prior generated positive net cannot justify validation",
            "review_decision": "do_not_reopen",
        },
        {
            "lane": "S3_safety_gate_calibration_v2",
            "status": "parked_pending_explicit_execution_go",
            "blocking_condition": "prior default was do_not_execute and no new explicit go is present",
            "review_decision": "do_not_reopen",
        },
        {
            "lane": "S6_parser_taxonomy_implementation",
            "status": "parked_pending_owner_mappings",
            "blocking_condition": f"owner_after_values_missing={m1068.get('owner_after_values_missing')}; implementation_allowed=false",
            "review_decision": "do_not_reopen",
        },
        {
            "lane": "OSS_expansion_provenance_lane",
            "status": "paused_pending_owner_provenance_package",
            "blocking_condition": f"owner_provenance_required_count={moss.get('owner_provenance_required_count')}; effect_gate_pass_count={moss.get('effect_gate_pass_count')}",
            "review_decision": "do_not_reopen",
        },
        {
            "lane": "S5_measurement_integrity_slice_telemetry",
            "status": "accepted_as_support_contract",
            "blocking_condition": "support contract accepted but implementation_allowed=false and satisfies_lane_reentry=false",
            "review_decision": "reuse_as_guardrail_not_next_lane",
        },
        {
            "lane": "S7_rank_position_candidate_pool_diagnostics",
            "status": "accepted_as_strategy_support",
            "blocking_condition": "already used to select S6; repeating S7 would duplicate diagnostics",
            "review_decision": "reuse_as_context_not_next_lane",
        },
    ]

    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    for row in lane_candidates_input:
        lane_id = row.get("lane_id", "")
        scores[lane_id] = 0
        reasons[lane_id] = []
        if lane_id == "S8_source_family_independence_registry_design":
            scores[lane_id] += 5
            reasons[lane_id].append("Source independence has repeatedly blocked S1/S2 re-entry and can be designed from existing provenance/source_family fields.")
            reasons[lane_id].append("It does not require accepting new sources or owner row mappings if kept as a design gate.")
        elif lane_id == "S5_measurement_integrity_slice_telemetry":
            scores[lane_id] += 2
            reasons[lane_id].append("S5 is valuable but already accepted as a support contract; use it as a guardrail.")
        elif lane_id == "S7_rank_position_distribution_diagnostics":
            scores[lane_id] += 1
            reasons[lane_id].append("S7 diagnostics are already accepted and already routed into S6.")
        elif lane_id == "S6_parser_query_normalization_inventory":
            scores[lane_id] += 0
            reasons[lane_id].append("S6 inventory/planning is now parked pending owner mappings; do not loop back.")
        else:
            reasons[lane_id].append("No stronger fit than S8 after S6 parking.")

    candidate_lanes: list[dict[str, Any]] = []
    for row in lane_candidates_input:
        lane_id = row.get("lane_id", "")
        selected = lane_id == "S8_source_family_independence_registry_design"
        candidate_lanes.append(
            {
                "lane_id": lane_id,
                "lane_name": row.get("lane_name", ""),
                "decision": "selected_next_read_only_lane" if selected else "defer",
                "score": scores.get(lane_id, 0),
                "selection_reason": " ".join(reasons.get(lane_id, [])),
                "does_not_depend_on": row.get("does_not_depend_on", ""),
                "evidence_requirement": row.get("evidence_requirement", ""),
                "not_allowed": row.get("not_allowed", ""),
            }
        )
    candidate_lanes = sorted(candidate_lanes, key=lambda row: (-_int(row["score"]), row["lane_id"]))

    selected_lane = [
        {
            "selected_next_lane": "S8_source_family_independence_registry_design",
            "next_stage": "10.70 S8 source-family independence registry design gate",
            "scope": "Read-only decide whether S8 can define source-family independence fields, deduplication rules, risk checks, and re-entry support boundaries from existing OSS/source provenance artifacts.",
            "required_inputs": "accepted OSS sources, source_file, source_family, producer, collection_method, provenance_hash, split, prior S5 artifact integrity contract, prior S1/S2 blockers",
            "not_allowed": "no new source acceptance, no OSS expansion reopen, no training, no implementation, no heldout/hard selection, no GoalSearcher change",
            "reason": "The next useful support gap is source-family independence. It directly addresses repeated source-dominance blockers while staying read-only and non-executing.",
        }
    ]
    next_gate = [
        {
            "next_stage": "10.70 S8 source-family independence registry design gate",
            "goal": "Read-only test whether S8 is concrete enough to become a future evidence-quality support contract.",
            "default": "design gate only",
            "not_allowed": "no source acceptance claim, no learning re-entry, no training, no implementation, no heldout/hard selection",
        }
    ]
    blocked_actions = [
        {
            "blocked_action": "reopen_s1_s2_s3_or_s6_execution",
            "reason": "All execution/implementation lanes remain parked by prior gates.",
            "allowed_after": "future lane-specific explicit go or accepted evidence package",
        },
        {
            "blocked_action": "accept_new_source_files_in_s8",
            "reason": "S8 is a registry design lane, not an owner provenance acceptance gate.",
            "allowed_after": "future owner/source provenance acceptance review",
        },
        {
            "blocked_action": "train_or_tune_from_source_registry_design",
            "reason": "A registry design contract is not learning evidence and has no effect audit.",
            "allowed_after": "future re-entry review with accepted evidence and explicit execution authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "10.69 only chooses a read-only design lane from existing dev/OOF/provenance context.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "lane_recheck_csv": str(output_prefix.with_name(output_prefix.name + "_lane_recheck.csv")),
        "candidate_next_lanes_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_next_lanes.csv")),
        "selected_next_lane_csv": str(output_prefix.with_name(output_prefix.name + "_selected_next_lane.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    active_learning_lane_count = 0
    parked_or_blocked_lane_count = sum(1 for row in lane_recheck if row["status"].startswith(("parked", "paused"))) + 2
    metrics = {
        "source_stage": summary_1068["stage"],
        "s6_lane_status": m1068.get("s6_lane_status"),
        "s6_owner_mapping_template_rows": m1068.get("owner_mapping_template_rows"),
        "s6_owner_after_values_missing": m1068.get("owner_after_values_missing"),
        "prior_lane_recheck_rows": len(prior_lane_recheck),
        "route_option_count_from_1068": len(options_1068),
        "active_learning_lane_count": active_learning_lane_count,
        "parked_or_blocked_lane_count": parked_or_blocked_lane_count,
        "s5_support_contract_accepted": _bool(ms5.get("s5_support_contract_accepted")),
        "s7_artifacts_accepted_for_strategy_support": _bool(ms7.get("s7_artifacts_accepted_for_strategy_support")),
        "s6_implementation_allowed": _bool(ms6.get("implementation_allowed")),
        "accepted_human_oss_source_file_count": _int(mp.get("accepted_human_oss_source_file_count")),
        "accepted_source_family_count": _int(mp.get("accepted_source_family_count")),
        "accepted_human_oss_dev_row_count": _int(mp.get("accepted_human_oss_dev_row_count")),
        "accepted_source_rows_loaded": len(accepted_sources),
        "oss_expansion_paused": _bool(moss.get("pause_oss_expansion_lane_now")),
        "oss_effect_gate_pass_count": _int(moss.get("effect_gate_pass_count")),
        "candidate_lane_count": len(candidate_lanes),
        "selected_next_lane": "S8_source_family_independence_registry_design",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "source_acceptance_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.69 broader 10.x strategy review after S6 parking",
        "read_only": True,
        "broader_strategy_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "After parking S6, keep S1/S2/S3/S6 execution and OSS expansion closed. Select S8_source_family_independence_registry_design as the next read-only lane because "
            "source-family independence repeatedly blocks learning re-entry, and a registry design can be defined from existing accepted OSS/source provenance artifacts without owner mappings, training, implementation, or heldout/hard selection."
        ),
        "anti_drift_conclusion": (
            "10.69 only selects a read-only design lane. It does not accept new sources, reopen OSS expansion, train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.70 S8 source-family independence registry design gate",
            "goal": "Read-only decide whether S8 can define a source-family independence support contract from existing artifacts.",
            "default": "design gate only",
        },
    }

    _write_csv(Path(artifacts["lane_recheck_csv"]), lane_recheck, ["lane", "status", "blocking_condition", "review_decision"])
    _write_csv(Path(artifacts["candidate_next_lanes_csv"]), candidate_lanes, ["lane_id", "lane_name", "decision", "score", "selection_reason", "does_not_depend_on", "evidence_requirement", "not_allowed"])
    _write_csv(Path(artifacts["selected_next_lane_csv"]), selected_lane, ["selected_next_lane", "next_stage", "scope", "required_inputs", "not_allowed", "reason"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "goal", "default", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, lane_recheck, candidate_lanes, selected_lane, next_gate)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
