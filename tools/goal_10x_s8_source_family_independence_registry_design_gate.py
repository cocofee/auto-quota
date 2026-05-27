from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1069_SUMMARY = AGENT_STATE / "goal_10x_broader_strategy_review_after_s6_parking_summary.json"
DEFAULT_1069_SELECTED = AGENT_STATE / "goal_10x_broader_strategy_review_after_s6_parking_selected_next_lane.csv"
DEFAULT_ACCEPTED_SOURCES = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_sources.csv"
DEFAULT_S5_SCOPE = AGENT_STATE / "goal_10x_s5_artifact_acceptance_gate_support_contract_scope.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_design_gate"


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
    gate_checks: list[dict[str, Any]],
    field_manifest: list[dict[str, Any]],
    independence_rules: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.70 S8 Source-Family Independence Registry Design Gate",
        "",
        "Read-only design gate for a future source-family independence registry support contract.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["design_gate_decision", metrics["design_gate_decision"]],
                ["accepted_source_file_count", metrics["accepted_source_file_count"]],
                ["accepted_source_family_count", metrics["accepted_source_family_count"]],
                ["multi_file_source_family_count", metrics["multi_file_source_family_count"]],
                ["field_manifest_count", metrics["field_manifest_count"]],
                ["independence_rule_count", metrics["independence_rule_count"]],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Field Manifest",
        "",
        _md_table(
            [["field", "required", "purpose", "source"]]
            + [[row["field"], row["required"], row["purpose"], row["source"]] for row in field_manifest]
        ),
        "",
        "## Independence Rules",
        "",
        _md_table(
            [["rule_id", "rule", "effect", "risk_control"]]
            + [[row["rule_id"], row["rule"], row["effect"], row["risk_control"]] for row in independence_rules]
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
        return text
    return text.replace(old, new, 1)


def _update_dashboard(path: Path, report: dict[str, Any], artifacts: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    text = _replace_once(text, '<div class="value">10.69 broader review</div>', '<div class="value">10.70 S8 design gate</div>')
    text = _replace_once(
        text,
        '<div class="note">S6 implementation 已 parked；本轮 broader review 选择 S8 source-family independence registry design gate。</div>',
        '<div class="note">S8 design gate 已通过：下一步可只读定义 source-family independence registry artifact。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">下一步仍是只读设计 gate：不接受新来源、不训练、不实现、不跑 heldout/hard selection。</div>',
        '<div class="note">仍不接受新来源、不重开 learning、不训练、不实现；S8 只作为未来 evidence quality 支持合同。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.69 已完成 broader review：选择 S8 source-family independence registry design gate 作为下一条非执行路线。</div>',
        '<div class="route-note">10.70 已确认 S8 足够具体，可进入 source-family independence registry artifact definition。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.69 broader review；next S8 design gate。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.70 S8 design gate；next artifact definition。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.69 broader 10.x strategy review after S6 parking</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only select the next strategy lane that does not depend on S6 owner mappings or immediate training/implementation.</td>
            <td>active_learning_lane_count=0; parked_or_blocked_lane_count=7; selected_next_lane=S8_source_family_independence_registry_design; accepted_human_oss_source_file_count=6; accepted_source_family_count=2.</td>
            <td>Next: 10.70 S8 source-family independence registry design gate. No new source acceptance, no training, no implementation, no heldout/hard selection.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.69 broader 10.x strategy review after S6 parking</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only select the next strategy lane that does not depend on S6 owner mappings or immediate training/implementation.</td>
            <td>active_learning_lane_count=0; parked_or_blocked_lane_count=7; selected_next_lane=S8_source_family_independence_registry_design; accepted_human_oss_source_file_count=6; accepted_source_family_count=2.</td>
            <td>Selected S8 design gate as the next non-execution strategy lane.</td>
          </tr>
          <tr>
            <td class="stage">10.70 S8 source-family independence registry design gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only judge whether S8 is concrete enough to define source-family independence registry fields, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>design_gate_decision=pass_to_artifact_definition; accepted_source_file_count=6; accepted_source_family_count=2; multi_file_source_family_count=1; field_manifest_count=14; independence_rule_count=6.</td>
            <td>Next: 10.71 S8 source-family independence registry artifact definition. Still no source acceptance, training, implementation, GoalSearcher change, or heldout/hard selection.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
只做当前阶段，不扩展新方向。
本轮状态：10.70 S8 source-family independence registry design gate 已完成。design_gate_decision={metrics["design_gate_decision"]}；accepted_source_file_count={metrics["accepted_source_file_count"]}；accepted_source_family_count={metrics["accepted_source_family_count"]}；multi_file_source_family_count={metrics["multi_file_source_family_count"]}；field_manifest_count={metrics["field_manifest_count"]}；independence_rule_count={metrics["independence_rule_count"]}；acceptance_pass_count={metrics["acceptance_pass_count"]}；acceptance_fail_count={metrics["acceptance_fail_count"]}；source_acceptance_allowed=false；training_allowed=false；implementation_allowed=false。
下一步：10.71 S8 source-family independence registry artifact definition。只读生成 registry field manifest、source-family grouping preview、dedup/independence rules、risk checks、re-entry support boundary 和 acceptance checks；不接受新来源，不重开 S1/S2/S3/S6。
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
            <td>10.70 S8 source-family independence registry design gate summary</td>
            <td>Read-only design gate summary; confirms S8 can enter artifact definition without accepting sources or training.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.70 S8 source-family independence registry design gate report</td>
            <td>Human-readable 10.70 report with gate checks, field manifest, independence rules, risk checks, next gate, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.70 S8 source-family independence registry design gate tables</td>
            <td>Gate checks, field manifest, source family preview, independence rules, risk checks, next gate, and blocked actions.</td>
            <td><code>{Path(artifacts["gate_checks_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["field_manifest_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["source_family_preview_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["independence_rules_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.70 S8 source-family independence registry design gate script</td>
            <td>Read-only design gate script; it does not accept sources, train, tune, run heldout/hard selection, change GoalSearcher, or edit parser/taxonomy rules.</td>
            <td><code>tools/goal_10x_s8_source_family_independence_registry_design_gate.py</code></td>
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
    parser = argparse.ArgumentParser(description="S8 source-family independence registry design gate")
    parser.add_argument("--summary-1069", default=str(DEFAULT_1069_SUMMARY))
    parser.add_argument("--selected-1069", default=str(DEFAULT_1069_SELECTED))
    parser.add_argument("--accepted-sources", default=str(DEFAULT_ACCEPTED_SOURCES))
    parser.add_argument("--s5-scope", default=str(DEFAULT_S5_SCOPE))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1069 = _read_json(Path(args.summary_1069))
    selected_1069 = _read_csv(Path(args.selected_1069))
    accepted_sources = _read_csv(Path(args.accepted_sources))
    s5_scope = _read_csv(Path(args.s5_scope))
    m1069 = summary_1069["metrics"]

    source_family_counts = Counter(row.get("source_family", "") for row in accepted_sources)
    multi_file_families = [family for family, count in source_family_counts.items() if count > 1]
    source_family_preview = []
    for family, count in sorted(source_family_counts.items()):
        rows = [row for row in accepted_sources if row.get("source_family") == family]
        source_family_preview.append(
            {
                "source_family": family,
                "source_file_count": count,
                "source_files": "; ".join(row.get("source_file", "") for row in rows),
                "row_count_total": sum(_int(row.get("row_count_total")) for row in rows),
                "dev_row_count": sum(_int(row.get("dev_row_count")) for row in rows),
                "province_count_max": max((_int(row.get("province_count")) for row in rows), default=0),
                "independence_disposition": "single_independent_family" if count == 1 else "same_family_dedup_required",
            }
        )

    required_input_fields = [
        "source_file",
        "source_family",
        "producer",
        "collection_method",
        "is_human_quantity_surveyor_output",
        "is_generated_or_synthetic",
        "trust_level",
        "evidence_note",
        "provenance_hash",
        "row_count_total",
        "dev_row_count",
        "province_count",
        "s2_source_class",
        "accepted_scope",
    ]
    missing_fields = [field for field in required_input_fields if any(field not in row for row in accepted_sources)]
    selected_lane = selected_1069[0].get("selected_next_lane", "") if selected_1069 else ""
    gate_checks = [
        {
            "check_id": "DG01_SELECTED_FROM_1069",
            "status": "pass" if selected_lane == "S8_source_family_independence_registry_design" else "fail",
            "evidence": f"selected_next_lane={selected_lane}",
            "decision": "10.69 explicitly selected S8.",
        },
        {
            "check_id": "DG02_ACCEPTED_SOURCE_INPUTS_PRESENT",
            "status": "pass" if accepted_sources else "fail",
            "evidence": f"accepted_source_rows={len(accepted_sources)}",
            "decision": "Accepted OSS provenance rows can seed a registry preview.",
        },
        {
            "check_id": "DG03_REQUIRED_FIELDS_PRESENT",
            "status": "pass" if not missing_fields else "fail",
            "evidence": f"required_fields={len(required_input_fields)}; missing_fields={';'.join(missing_fields)}",
            "decision": "Registry fields can be defined from existing accepted-source artifacts.",
        },
        {
            "check_id": "DG04_MULTI_FILE_FAMILY_VISIBLE",
            "status": "pass" if multi_file_families else "fail",
            "evidence": f"multi_file_source_families={';'.join(multi_file_families)}",
            "decision": "Existing OSS data already shows why source-family dedup rules are needed.",
        },
        {
            "check_id": "DG05_S5_BOUNDARY_AVAILABLE",
            "status": "pass" if len(s5_scope) >= 4 else "fail",
            "evidence": f"s5_support_scope_rows={len(s5_scope)}",
            "decision": "Use S5 integrity/split/effect boundaries as guardrails.",
        },
        {
            "check_id": "DG06_NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "source_acceptance_allowed=false; training_allowed=false; implementation_allowed=false",
            "decision": "10.70 is design-gate only.",
        },
    ]
    fail_count = sum(1 for row in gate_checks if row["status"] != "pass")

    field_manifest = [
        {"field": "source_family_id", "required": "true", "purpose": "Stable family key used for independence counting and dedup.", "source": "derived from source_family"},
        {"field": "source_file", "required": "true", "purpose": "Original artifact filename used for lineage.", "source": "accepted_sources"},
        {"field": "source_family", "required": "true", "purpose": "Human-readable source family grouping.", "source": "accepted_sources"},
        {"field": "producer", "required": "true", "purpose": "Producer/origin label for source trust review.", "source": "accepted_sources"},
        {"field": "collection_method", "required": "true", "purpose": "How rows were collected or transformed.", "source": "accepted_sources"},
        {"field": "provenance_hash", "required": "true", "purpose": "Artifact integrity and stale-file guard.", "source": "accepted_sources"},
        {"field": "is_human_quantity_surveyor_output", "required": "true", "purpose": "Human OSS boundary flag.", "source": "accepted_sources"},
        {"field": "is_generated_or_synthetic", "required": "true", "purpose": "Generated/synthetic exclusion flag.", "source": "accepted_sources"},
        {"field": "trust_level", "required": "true", "purpose": "Evidence quality tier.", "source": "accepted_sources"},
        {"field": "split", "required": "true", "purpose": "Selection/validation boundary.", "source": "derived from row_count fields"},
        {"field": "row_count_total", "required": "true", "purpose": "Support size for registry entry.", "source": "accepted_sources"},
        {"field": "province_count", "required": "true", "purpose": "Cross-province support signal.", "source": "accepted_sources"},
        {"field": "independence_group_key", "required": "true", "purpose": "Canonical dedup key for same-chain variants.", "source": "derived"},
        {"field": "reentry_allowed_use", "required": "true", "purpose": "How the registry may support future S1/S2 evidence review.", "source": "derived policy"},
    ]
    independence_rules = [
        {
            "rule_id": "IR01_SAME_SOURCE_FAMILY_DEDUP",
            "rule": "Multiple source_file rows with the same source_family count as one independent family unless an owner later proves independent collection.",
            "effect": "Prevents v36 speed-chain variants from inflating independence.",
            "risk_control": "default to conservative dedup",
        },
        {
            "rule_id": "IR02_HASH_DOES_NOT_CREATE_INDEPENDENCE",
            "rule": "Different provenance_hash values prove distinct artifacts, not distinct evidence families.",
            "effect": "Avoids treating transformed traces as independent sources.",
            "risk_control": "hash supports integrity only",
        },
        {
            "rule_id": "IR03_GENERATED_EXCLUSION",
            "rule": "Any generated/synthetic source is excluded from positive non-generated support counts.",
            "effect": "Blocks generated-source dominance from re-entry claims.",
            "risk_control": "requires is_generated_or_synthetic=false",
        },
        {
            "rule_id": "IR04_ACCEPTED_SCOPE_LIMIT",
            "rule": "DQ provenance acceptance is not learning re-entry; registry can support review but cannot authorize training.",
            "effect": "Keeps source registry from becoming algorithm evidence by itself.",
            "risk_control": "requires separate effect audit",
        },
        {
            "rule_id": "IR05_SPLIT_BOUNDARY",
            "rule": "Dev/OOF evidence can support selection; heldout/hard remains validation-only and cannot define registry rules.",
            "effect": "Preserves S5 split policy.",
            "risk_control": "no heldout/hard selection",
        },
        {
            "rule_id": "IR06_SOURCE_FAMILY_MINIMUM_FOR_REENTRY",
            "rule": "Future S1/S2 re-entry evidence must show positive net across at least two independent non-generated source_family_id values.",
            "effect": "Turns prior source-dominance blockers into an explicit support check.",
            "risk_control": "requires future lane-specific review",
        },
    ]
    risk_checks = [
        {"risk": "same_chain_overcount", "check": "Count source_family_id rather than source_file.", "severity": "high"},
        {"risk": "generated_dominance", "check": "Exclude generated/synthetic rows from positive support.", "severity": "high"},
        {"risk": "hash_as_independence_confusion", "check": "Use provenance_hash for integrity only.", "severity": "medium"},
        {"risk": "dq_acceptance_as_learning_claim", "check": "Require separate effect audit before re-entry.", "severity": "high"},
        {"risk": "heldout_rule_selection", "check": "Registry design uses accepted provenance/dev/OOF support only.", "severity": "high"},
        {"risk": "owner_package_dependency", "check": "Design gate can proceed without accepting new sources.", "severity": "medium"},
    ]
    next_gate = [
        {
            "next_stage": "10.71 S8 source-family independence registry artifact definition",
            "goal": "Read-only generate the registry artifact design tables and preview source-family grouping from existing accepted OSS provenance.",
            "default": "artifact definition only",
            "not_allowed": "no source acceptance, no learning re-entry, no training, no implementation, no heldout/hard selection",
        }
    ]
    blocked_actions = [
        {
            "blocked_action": "accept_new_source_files",
            "reason": "10.70 is design-gate only, not owner provenance acceptance.",
            "allowed_after": "future owner/source provenance acceptance review",
        },
        {
            "blocked_action": "count_same_family_files_as_independent",
            "reason": "Existing accepted sources include multiple oss_v36_speed_chain files that should be deduped.",
            "allowed_after": "future owner proof of independent collection",
        },
        {
            "blocked_action": "use_s8_to_authorize_learning_reentry",
            "reason": "S8 support contract is not a lane-specific positive effect package.",
            "allowed_after": "future re-entry review with positive non-generated net and independent source-family support",
        },
        {
            "blocked_action": "run_training_or_heldout_selection",
            "reason": "Design gate has no execution permission.",
            "allowed_after": "future explicit execution authorization; heldout/hard never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "field_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_field_manifest.csv")),
        "source_family_preview_csv": str(output_prefix.with_name(output_prefix.name + "_source_family_preview.csv")),
        "independence_rules_csv": str(output_prefix.with_name(output_prefix.name + "_independence_rules.csv")),
        "risk_checks_csv": str(output_prefix.with_name(output_prefix.name + "_risk_checks.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1069["stage"],
        "design_gate_decision": "pass_to_artifact_definition" if fail_count == 0 else "do_not_continue",
        "selected_next_lane": selected_lane,
        "accepted_source_file_count": len(accepted_sources),
        "accepted_source_family_count": len(source_family_counts),
        "multi_file_source_family_count": len(multi_file_families),
        "multi_file_source_families": multi_file_families,
        "accepted_source_total_rows": sum(_int(row.get("row_count_total")) for row in accepted_sources),
        "accepted_source_dev_rows": sum(_int(row.get("dev_row_count")) for row in accepted_sources),
        "s5_support_scope_count": len(s5_scope),
        "field_manifest_count": len(field_manifest),
        "independence_rule_count": len(independence_rules),
        "risk_check_count": len(risk_checks),
        "acceptance_pass_count": len(gate_checks) - fail_count,
        "acceptance_fail_count": fail_count,
        "source_acceptance_allowed": False,
        "learning_reentry_allowed": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.70 S8 source-family independence registry design gate",
        "read_only": True,
        "design_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pass S8 to artifact definition. Existing accepted OSS provenance has enough fields to define a conservative source-family independence registry, "
            "and it already shows one multi-file source family that must be deduped. This does not accept new sources or reopen learning."
        ),
        "anti_drift_conclusion": (
            "10.70 only checks design readiness for a future registry artifact. It does not accept new sources, reopen OSS expansion, train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.71 S8 source-family independence registry artifact definition",
            "goal": "Read-only generate registry design artifacts and source-family grouping preview.",
            "default": "artifact definition only",
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["field_manifest_csv"]), field_manifest, ["field", "required", "purpose", "source"])
    _write_csv(Path(artifacts["source_family_preview_csv"]), source_family_preview, ["source_family", "source_file_count", "source_files", "row_count_total", "dev_row_count", "province_count_max", "independence_disposition"])
    _write_csv(Path(artifacts["independence_rules_csv"]), independence_rules, ["rule_id", "rule", "effect", "risk_control"])
    _write_csv(Path(artifacts["risk_checks_csv"]), risk_checks, ["risk", "check", "severity"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "goal", "default", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, field_manifest, independence_rules, next_gate)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
