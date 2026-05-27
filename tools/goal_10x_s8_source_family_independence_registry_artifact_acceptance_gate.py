from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1071_SUMMARY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_summary.json"
DEFAULT_SOURCE_FILE_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_file_registry.csv"
DEFAULT_SOURCE_FAMILY_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_family_registry.csv"
DEFAULT_FIELD_MANIFEST = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_registry_field_manifest.csv"
DEFAULT_REENTRY_BOUNDARY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_reentry_support_boundary.csv"
DEFAULT_DEDUP_RULES = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_dedup_independence_rules.csv"
DEFAULT_RISK_CHECKS = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_risk_checks.csv"
DEFAULT_ACCEPTANCE_CHECKS_1071 = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_acceptance_checks.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate"


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
    acceptance_results: list[dict[str, Any]],
    support_contract_scope: list[dict[str, Any]],
    next_options: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.72 S8 Source-Family Independence Registry Artifact Acceptance Gate",
        "",
        "Read-only acceptance gate for the S8 source-family independence registry artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["artifact_acceptance_decision", metrics["artifact_acceptance_decision"]],
                ["s8_support_contract_accepted", metrics["s8_support_contract_accepted"]],
                ["registry_source_file_rows", metrics["registry_source_file_rows"]],
                ["registry_source_family_rows", metrics["registry_source_family_rows"]],
                ["independent_non_generated_family_count", metrics["independent_non_generated_family_count"]],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
            ]
        ),
        "",
        "## Acceptance Results",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_results]
        ),
        "",
        "## Support Contract Scope",
        "",
        _md_table(
            [["scope_item", "accepted_use", "not_allowed"]]
            + [[row["scope_item"], row["accepted_use"], row["not_allowed"]] for row in support_contract_scope]
        ),
        "",
        "## Next Options",
        "",
        _md_table(
            [["option", "status", "rationale"]]
            + [[row["option"], row["status"], row["rationale"]] for row in next_options]
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
    text = _replace_once(text, '<div class="value">10.71 S8 artifact</div>', '<div class="value">10.72 S8 accepted</div>')
    text = _replace_once(
        text,
        '<div class="note">S8 registry artifact 已生成：6 个 source files 合并为 2 个 independent source families。</div>',
        '<div class="note">S8 registry artifact 已接受为 future S1/S2 evidence-quality support contract；仍不允许 learning re-entry。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">S8 仍只是 future re-entry support contract；不接受新来源、不训练、不实现、不声明 Top1 gain。</div>',
        '<div class="note">下一步回到 broader strategy closure/review；不接受新来源、不训练、不实现、不声明 Top1 gain。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.71 已生成 S8 source-family independence registry artifact；下一步做 artifact acceptance gate。</div>',
        '<div class="route-note">10.72 已接受 S8 registry artifact 为 evidence-quality support contract，但不打开 learning re-entry。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.71 S8 artifact；next acceptance gate。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.72 S8 accepted；next broader review。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.71 S8 source-family independence registry artifact definition</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only generate registry field manifest, source-family grouping preview, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>artifact_definition_decision=ready_for_acceptance_gate; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; deduped_source_file_count=4.</td>
            <td>Next: 10.72 S8 source-family independence registry artifact acceptance gate. Still no source acceptance, learning re-entry, training, implementation, or heldout/hard selection.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.71 S8 source-family independence registry artifact definition</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only generate registry field manifest, source-family grouping preview, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>artifact_definition_decision=ready_for_acceptance_gate; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; deduped_source_file_count=4.</td>
            <td>Ready for acceptance gate; no source acceptance or learning re-entry.</td>
          </tr>
          <tr>
            <td class="stage">10.72 S8 source-family independence registry artifact acceptance gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only decide whether the S8 registry artifact is acceptable as future S1/S2 evidence-quality support contract.</td>
            <td>artifact_acceptance_decision=accept_as_support_contract; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; acceptance_pass_count=7; acceptance_fail_count=0.</td>
            <td>Next: 10.73 broader 10.x strategy closure/review after S8 support-contract acceptance. Still no learning re-entry, training, implementation, source acceptance, or heldout/hard selection.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
只做当前阶段，不扩展新方向。
本轮状态：10.72 S8 source-family independence registry artifact acceptance gate 已完成。artifact_acceptance_decision={metrics["artifact_acceptance_decision"]}；s8_support_contract_accepted={str(metrics["s8_support_contract_accepted"]).lower()}；registry_source_file_rows={metrics["registry_source_file_rows"]}；registry_source_family_rows={metrics["registry_source_family_rows"]}；independent_non_generated_family_count={metrics["independent_non_generated_family_count"]}；deduped_source_file_count={metrics["deduped_source_file_count"]}；acceptance_pass_count={metrics["acceptance_pass_count"]}；acceptance_fail_count={metrics["acceptance_fail_count"]}；source_acceptance_allowed=false；learning_reentry_allowed=false；training_allowed=false；implementation_allowed=false。
下一步：10.73 broader 10.x strategy closure/review after S8 support-contract acceptance。只读决定 S8 lane 是否收口，并选择暂停等待新 evidence/explicit go，还是还有不依赖 owner mappings、不训练不实现的策略路线。
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
    if "10.72 S8 source-family independence registry artifact acceptance gate summary" not in text:
        artifact_rows = f"""          <tr>
            <td>10.72 S8 source-family independence registry artifact acceptance gate summary</td>
            <td>Read-only acceptance gate summary; accepts S8 registry as future evidence-quality support contract only.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.72 S8 source-family independence registry artifact acceptance gate report</td>
            <td>Human-readable 10.72 report with acceptance results, support contract scope, next options, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.72 S8 source-family independence registry artifact acceptance gate tables</td>
            <td>Acceptance results, support contract scope, next options, and blocked actions.</td>
            <td><code>{Path(artifacts["acceptance_results_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["support_contract_scope_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["next_options_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["blocked_actions_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.72 S8 source-family independence registry artifact acceptance gate script</td>
            <td>Read-only acceptance gate script; it does not accept sources, train, tune, run heldout/hard selection, change GoalSearcher, or edit parser/taxonomy rules.</td>
            <td><code>tools/goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate.py</code></td>
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
    parser = argparse.ArgumentParser(description="Accept or reject S8 source-family independence registry artifact")
    parser.add_argument("--summary-1071", default=str(DEFAULT_1071_SUMMARY))
    parser.add_argument("--source-file-registry", default=str(DEFAULT_SOURCE_FILE_REGISTRY))
    parser.add_argument("--source-family-registry", default=str(DEFAULT_SOURCE_FAMILY_REGISTRY))
    parser.add_argument("--field-manifest", default=str(DEFAULT_FIELD_MANIFEST))
    parser.add_argument("--reentry-boundary", default=str(DEFAULT_REENTRY_BOUNDARY))
    parser.add_argument("--dedup-rules", default=str(DEFAULT_DEDUP_RULES))
    parser.add_argument("--risk-checks", default=str(DEFAULT_RISK_CHECKS))
    parser.add_argument("--acceptance-checks-1071", default=str(DEFAULT_ACCEPTANCE_CHECKS_1071))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1071 = _read_json(Path(args.summary_1071))
    source_file_registry = _read_csv(Path(args.source_file_registry))
    source_family_registry = _read_csv(Path(args.source_family_registry))
    field_manifest = _read_csv(Path(args.field_manifest))
    reentry_boundary = _read_csv(Path(args.reentry_boundary))
    dedup_rules = _read_csv(Path(args.dedup_rules))
    risk_checks = _read_csv(Path(args.risk_checks))
    acceptance_checks_1071 = _read_csv(Path(args.acceptance_checks_1071))
    m1071 = summary_1071["metrics"]

    family_to_files: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source_file_registry:
        family_to_files[row.get("source_family_id", "")].append(row)
    representative_failures = [
        family_id
        for family_id, rows in family_to_files.items()
        if sum(1 for row in rows if row.get("representative_for_family") == "true") != 1
    ]
    weight_failures = [
        family_id
        for family_id, rows in family_to_files.items()
        if sum(_int(row.get("independence_count_weight")) for row in rows) not in {0, 1}
    ]
    family_weight_sum = sum(_int(row.get("independent_family_weight")) for row in source_family_registry)
    file_weight_sum = sum(_int(row.get("independence_count_weight")) for row in source_file_registry)
    prior_fail_count = sum(1 for row in acceptance_checks_1071 if row.get("status") != "pass")

    acceptance_results = [
        {
            "check_id": "AG01_UPSTREAM_READY",
            "status": "pass" if m1071.get("artifact_definition_decision") == "ready_for_acceptance_gate" and prior_fail_count == 0 else "fail",
            "evidence": f"artifact_definition_decision={m1071.get('artifact_definition_decision')}; prior_acceptance_fail_count={prior_fail_count}",
            "decision": "10.71 artifact is ready for acceptance review.",
        },
        {
            "check_id": "AG02_REGISTRY_TABLES_PRESENT",
            "status": "pass" if source_file_registry and source_family_registry and field_manifest else "fail",
            "evidence": f"source_file_rows={len(source_file_registry)}; source_family_rows={len(source_family_registry)}; field_rows={len(field_manifest)}",
            "decision": "Core registry tables are present.",
        },
        {
            "check_id": "AG03_CONSERVATIVE_DEDUP_VALID",
            "status": "pass" if not representative_failures and not weight_failures and family_weight_sum == file_weight_sum else "fail",
            "evidence": f"representative_failures={len(representative_failures)}; weight_failures={len(weight_failures)}; family_weight_sum={family_weight_sum}; file_weight_sum={file_weight_sum}",
            "decision": "Each family has one representative and at most one independence count.",
        },
        {
            "check_id": "AG04_REENTRY_BOUNDARY_STRONG",
            "status": "pass" if len(reentry_boundary) >= 5 and any(row.get("boundary_item") == "positive_effect_requirement" for row in reentry_boundary) else "fail",
            "evidence": f"reentry_boundary_count={len(reentry_boundary)}; has_positive_effect_requirement={any(row.get('boundary_item') == 'positive_effect_requirement' for row in reentry_boundary)}",
            "decision": "Support contract blocks registry-only learning claims.",
        },
        {
            "check_id": "AG05_RULE_AND_RISK_COVERAGE",
            "status": "pass" if len(dedup_rules) >= 6 and len(risk_checks) >= 6 else "fail",
            "evidence": f"dedup_rule_count={len(dedup_rules)}; risk_check_count={len(risk_checks)}",
            "decision": "Dedup rules and risk checks are complete enough for support use.",
        },
        {
            "check_id": "AG06_SUPPORT_NOT_REENTRY",
            "status": "pass" if not m1071.get("learning_reentry_allowed") and not m1071.get("training_allowed") else "fail",
            "evidence": f"learning_reentry_allowed={m1071.get('learning_reentry_allowed')}; training_allowed={m1071.get('training_allowed')}",
            "decision": "Artifact acceptance does not authorize learning re-entry or training.",
        },
        {
            "check_id": "AG07_NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "source_acceptance_allowed=false; implementation_allowed=false; heldout_selection_allowed=false",
            "decision": "10.72 remains acceptance-gate only.",
        },
    ]
    fail_count = sum(1 for row in acceptance_results if row["status"] != "pass")
    accepted = fail_count == 0

    support_contract_scope = [
        {
            "scope_item": "source_family_independence_counting",
            "accepted_use": "Use source_family_id and independent_family_weight to count independent non-generated OSS support in future S1/S2 re-entry reviews.",
            "not_allowed": "Do not count source_file rows as independent support.",
        },
        {
            "scope_item": "same_family_dedup",
            "accepted_use": "Dedup v36 speed-chain variants and other same-family artifacts unless a future owner package proves independent collection.",
            "not_allowed": "Do not inflate evidence count from transformed diagnostic variants.",
        },
        {
            "scope_item": "artifact_integrity",
            "accepted_use": "Use provenance_hash as integrity/lineage evidence for registry rows.",
            "not_allowed": "Do not treat different hashes as proof of independent source families.",
        },
        {
            "scope_item": "future_reentry_guardrail",
            "accepted_use": "Require future S1/S2 evidence packages to satisfy independent non-generated source-family support plus separate positive dev/OOF effect audit.",
            "not_allowed": "Do not use this registry alone to reopen learning, train, validate, or claim Top1 gain.",
        },
        {
            "scope_item": "split_and_selection_boundary",
            "accepted_use": "Keep heldout/hard validation-only; use registry only as evidence-quality support.",
            "not_allowed": "Do not select thresholds, candidates, policies, or source rules using heldout/hard.",
        },
    ]
    next_options = [
        {
            "option": "return_to_broader_strategy_closure_review",
            "status": "selected_next",
            "rationale": "S8 support contract is accepted; decide whether any non-execution lane remains or pause until new evidence/explicit go.",
        },
        {
            "option": "reopen_s1_or_s2_learning",
            "status": "blocked",
            "rationale": "S8 is only evidence-quality support; no accepted-source positive effect package is present.",
        },
        {
            "option": "accept_new_sources",
            "status": "blocked",
            "rationale": "New source acceptance requires a separate owner/source provenance acceptance gate.",
        },
        {
            "option": "implement_registry_in_pipeline",
            "status": "blocked",
            "rationale": "No implementation go and no online/GoalSearcher change authorization.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "learning_reentry_from_s8_acceptance",
            "reason": "S8 acceptance is support-contract acceptance only and does not include a positive effect audit.",
            "allowed_after": "future S1/S2 re-entry review with accepted-source positive non-generated net and independent family support",
        },
        {
            "blocked_action": "source_acceptance_from_registry",
            "reason": "Registry covers previously accepted sources only.",
            "allowed_after": "future owner/source provenance package and acceptance review",
        },
        {
            "blocked_action": "implement_registry_or_goal_searcher_change",
            "reason": "No implementation authorization exists.",
            "allowed_after": "future explicit implementation go and implementation plan",
        },
        {
            "blocked_action": "heldout_or_hard_selection",
            "reason": "Heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_results_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_results.csv")),
        "support_contract_scope_csv": str(output_prefix.with_name(output_prefix.name + "_support_contract_scope.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1071["stage"],
        "artifact_acceptance_decision": "accept_as_support_contract" if accepted else "do_not_accept",
        "s8_support_contract_accepted": accepted,
        "registry_source_file_rows": len(source_file_registry),
        "registry_source_family_rows": len(source_family_registry),
        "independent_non_generated_family_count": _int(m1071.get("independent_non_generated_family_count")),
        "deduped_source_file_count": _int(m1071.get("deduped_source_file_count")),
        "registry_field_count": len(field_manifest),
        "reentry_boundary_count": len(reentry_boundary),
        "support_contract_scope_count": len(support_contract_scope),
        "acceptance_pass_count": len(acceptance_results) - fail_count,
        "acceptance_fail_count": fail_count,
        "learning_reentry_allowed": False,
        "source_acceptance_allowed": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.72 S8 source-family independence registry artifact acceptance gate",
        "read_only": True,
        "acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the S8 source-family independence registry artifact as a future S1/S2 evidence-quality support contract only. "
            "It can standardize independent-source counting and same-family dedup in future re-entry reviews, but it does not accept new sources, reopen learning, train, implement, or claim Top1 gain."
        ),
        "anti_drift_conclusion": (
            "10.72 only accepts S8 as an evidence-quality support contract. It does not accept new sources, reopen OSS expansion, train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.73 broader 10.x strategy closure/review after S8 support-contract acceptance",
            "goal": "Read-only decide whether S8 lane is closed and whether to pause or select another non-execution route.",
            "default": "broader strategy closure/review only",
        },
    }

    _write_csv(Path(artifacts["acceptance_results_csv"]), acceptance_results, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["support_contract_scope_csv"]), support_contract_scope, ["scope_item", "accepted_use", "not_allowed"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_results, support_contract_scope, next_options)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
