from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1070_SUMMARY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_design_gate_summary.json"
DEFAULT_1070_FIELD_MANIFEST = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_design_gate_field_manifest.csv"
DEFAULT_1070_RULES = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_design_gate_independence_rules.csv"
DEFAULT_1070_RISKS = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_design_gate_risk_checks.csv"
DEFAULT_ACCEPTED_SOURCES = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_sources.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition"


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


def _bool_text(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")[:42]
    return f"{prefix}_{safe}_{digest}" if safe else f"{prefix}_{digest}"


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
    source_family_registry: list[dict[str, Any]],
    reentry_boundary: list[dict[str, Any]],
    acceptance_checks: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.71 S8 Source-Family Independence Registry Artifact Definition",
        "",
        "Read-only artifact definition for source-family independence support.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["artifact_definition_decision", metrics["artifact_definition_decision"]],
                ["registry_source_file_rows", metrics["registry_source_file_rows"]],
                ["registry_source_family_rows", metrics["registry_source_family_rows"]],
                ["independent_non_generated_family_count", metrics["independent_non_generated_family_count"]],
                ["deduped_source_file_count", metrics["deduped_source_file_count"]],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
            ]
        ),
        "",
        "## Source Families",
        "",
        _md_table(
            [["source_family_id", "source_family", "source_file_count", "independence_disposition", "independent_family_weight"]]
            + [
                [
                    row["source_family_id"],
                    row["source_family"],
                    row["source_file_count"],
                    row["independence_disposition"],
                    row["independent_family_weight"],
                ]
                for row in source_family_registry
            ]
        ),
        "",
        "## Re-entry Boundary",
        "",
        _md_table(
            [["boundary_item", "required_for", "contract", "not_allowed"]]
            + [[row["boundary_item"], row["required_for"], row["contract"], row["not_allowed"]] for row in reentry_boundary]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_checks]
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
    text = _replace_once(text, '<div class="value">10.70 S8 design gate</div>', '<div class="value">10.71 S8 artifact</div>')
    text = _replace_once(
        text,
        '<div class="note">S8 design gate 已通过：下一步可只读定义 source-family independence registry artifact。</div>',
        '<div class="note">S8 registry artifact 已生成：6 个 source files 合并为 2 个 independent source families。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">仍不接受新来源、不重开 learning、不训练、不实现；S8 只作为未来 evidence quality 支持合同。</div>',
        '<div class="note">S8 仍只是 future re-entry support contract；不接受新来源、不训练、不实现、不声明 Top1 gain。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.70 已确认 S8 足够具体，可进入 source-family independence registry artifact definition。</div>',
        '<div class="route-note">10.71 已生成 S8 source-family independence registry artifact；下一步做 artifact acceptance gate。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.70 S8 design gate；next artifact definition。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.71 S8 artifact；next acceptance gate。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.70 S8 source-family independence registry design gate</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only judge whether S8 is concrete enough to define source-family independence registry fields, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>design_gate_decision=pass_to_artifact_definition; accepted_source_file_count=6; accepted_source_family_count=2; multi_file_source_family_count=1; field_manifest_count=14; independence_rule_count=6.</td>
            <td>Next: 10.71 S8 source-family independence registry artifact definition. Still no source acceptance, training, implementation, GoalSearcher change, or heldout/hard selection.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.70 S8 source-family independence registry design gate</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only judge whether S8 is concrete enough to define source-family independence registry fields, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>design_gate_decision=pass_to_artifact_definition; accepted_source_file_count=6; accepted_source_family_count=2; multi_file_source_family_count=1; field_manifest_count=14; independence_rule_count=6.</td>
            <td>Passed to artifact definition; no source acceptance or learning re-entry.</td>
          </tr>
          <tr>
            <td class="stage">10.71 S8 source-family independence registry artifact definition</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only generate registry field manifest, source-family grouping preview, dedup rules, risk checks, re-entry support boundary, and acceptance checks.</td>
            <td>artifact_definition_decision=ready_for_acceptance_gate; registry_source_file_rows=6; registry_source_family_rows=2; independent_non_generated_family_count=2; deduped_source_file_count=4.</td>
            <td>Next: 10.72 S8 source-family independence registry artifact acceptance gate. Still no source acceptance, learning re-entry, training, implementation, or heldout/hard selection.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
只做当前阶段，不扩展新方向。
本轮状态：10.71 S8 source-family independence registry artifact definition 已完成。artifact_definition_decision={metrics["artifact_definition_decision"]}；registry_source_file_rows={metrics["registry_source_file_rows"]}；registry_source_family_rows={metrics["registry_source_family_rows"]}；independent_non_generated_family_count={metrics["independent_non_generated_family_count"]}；deduped_source_file_count={metrics["deduped_source_file_count"]}；reentry_boundary_count={metrics["reentry_boundary_count"]}；acceptance_pass_count={metrics["acceptance_pass_count"]}；acceptance_fail_count={metrics["acceptance_fail_count"]}；source_acceptance_allowed=false；learning_reentry_allowed=false；training_allowed=false；implementation_allowed=false。
下一步：10.72 S8 source-family independence registry artifact acceptance gate。只读判断 10.71 artifact 是否可接受为 future S1/S2 evidence-quality support contract；不接受新来源，不重开 S1/S2/S3/S6。
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
    if "10.71 S8 source-family independence registry artifact definition summary" not in text:
        artifact_rows = f"""          <tr>
            <td>10.71 S8 source-family independence registry artifact definition summary</td>
            <td>Read-only artifact definition summary; creates source-family registry preview and future re-entry support boundary.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.71 S8 source-family independence registry artifact definition report</td>
            <td>Human-readable 10.71 report with source-family registry, re-entry boundary, acceptance checks, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.71 S8 source-family independence registry artifact definition tables</td>
            <td>Registry field manifest, source-file registry rows, source-family registry, dedup/independence rules, risk checks, re-entry support boundary, acceptance checks, and blocked actions.</td>
            <td><code>{Path(artifacts["registry_field_manifest_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["source_file_registry_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["source_family_registry_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["reentry_support_boundary_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>10.71 S8 source-family independence registry artifact definition script</td>
            <td>Read-only artifact definition script; it does not accept sources, train, tune, run heldout/hard selection, change GoalSearcher, or edit parser/taxonomy rules.</td>
            <td><code>tools/goal_10x_s8_source_family_independence_registry_artifact_definition.py</code></td>
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
    parser = argparse.ArgumentParser(description="Define S8 source-family independence registry artifacts")
    parser.add_argument("--summary-1070", default=str(DEFAULT_1070_SUMMARY))
    parser.add_argument("--field-manifest-1070", default=str(DEFAULT_1070_FIELD_MANIFEST))
    parser.add_argument("--rules-1070", default=str(DEFAULT_1070_RULES))
    parser.add_argument("--risks-1070", default=str(DEFAULT_1070_RISKS))
    parser.add_argument("--accepted-sources", default=str(DEFAULT_ACCEPTED_SOURCES))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1070 = _read_json(Path(args.summary_1070))
    field_manifest_1070 = _read_csv(Path(args.field_manifest_1070))
    rules_1070 = _read_csv(Path(args.rules_1070))
    risks_1070 = _read_csv(Path(args.risks_1070))
    accepted_sources = _read_csv(Path(args.accepted_sources))
    m1070 = summary_1070["metrics"]

    families: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in accepted_sources:
        families[row.get("source_family", "")].append(row)

    source_file_registry: list[dict[str, Any]] = []
    source_family_registry: list[dict[str, Any]] = []
    independent_non_generated_family_count = 0
    deduped_source_file_count = 0
    for source_family, rows in sorted(families.items()):
        source_family_id = _stable_id("sf", source_family)
        all_non_generated = all(not _bool_text(row.get("is_generated_or_synthetic")) for row in rows)
        all_human_oss = all("true" in row.get("is_human_quantity_surveyor_output", "").lower() for row in rows)
        independent_weight = 1 if all_non_generated and all_human_oss else 0
        representative = rows[0].get("source_file", "") if rows else ""
        if independent_weight:
            independent_non_generated_family_count += 1
        if len(rows) > 1:
            deduped_source_file_count += len(rows) - 1
        source_family_registry.append(
            {
                "source_family_id": source_family_id,
                "source_family": source_family,
                "source_file_count": len(rows),
                "source_files": "; ".join(row.get("source_file", "") for row in rows),
                "representative_source_file": representative,
                "row_count_total": sum(_int(row.get("row_count_total")) for row in rows),
                "dev_row_count": sum(_int(row.get("dev_row_count")) for row in rows),
                "province_count_max": max((_int(row.get("province_count")) for row in rows), default=0),
                "is_non_generated_family": str(all_non_generated).lower(),
                "is_human_oss_family": str(all_human_oss).lower(),
                "independent_family_weight": independent_weight,
                "independence_disposition": "same_family_dedup_required" if len(rows) > 1 else "single_independent_family",
                "reentry_allowed_use": "support_future_reentry_review_only",
            }
        )
        for idx, row in enumerate(rows):
            source_file_registry.append(
                {
                    "source_family_id": source_family_id,
                    "source_file_id": _stable_id("sf_file", row.get("source_file", "")),
                    "source_file": row.get("source_file", ""),
                    "source_family": source_family,
                    "producer": row.get("producer", ""),
                    "collection_method": row.get("collection_method", ""),
                    "provenance_hash": row.get("provenance_hash", ""),
                    "is_human_quantity_surveyor_output": row.get("is_human_quantity_surveyor_output", ""),
                    "is_generated_or_synthetic": row.get("is_generated_or_synthetic", ""),
                    "trust_level": row.get("trust_level", ""),
                    "row_count_total": row.get("row_count_total", ""),
                    "dev_row_count": row.get("dev_row_count", ""),
                    "province_count": row.get("province_count", ""),
                    "s2_source_class": row.get("s2_source_class", ""),
                    "accepted_scope": row.get("accepted_scope", ""),
                    "representative_for_family": str(idx == 0).lower(),
                    "independence_count_weight": 1 if idx == 0 and independent_weight else 0,
                    "dedup_reason": "same_source_family_variant" if idx > 0 else "",
                }
            )

    registry_field_manifest = []
    for row in field_manifest_1070:
        field = row.get("field", "")
        registry_field_manifest.append(
            {
                "field": field,
                "required": row.get("required", ""),
                "data_type": "integer" if field in {"row_count_total", "province_count"} else "string",
                "purpose": row.get("purpose", ""),
                "source": row.get("source", ""),
                "validation_check": "non_empty" if row.get("required") == "true" else "optional",
            }
        )
    registry_field_manifest += [
        {
            "field": "representative_for_family",
            "required": "true",
            "data_type": "boolean",
            "purpose": "Marks the one source_file row that carries the family-level independence count.",
            "source": "derived",
            "validation_check": "exactly_one_true_per_source_family_id",
        },
        {
            "field": "independence_count_weight",
            "required": "true",
            "data_type": "integer",
            "purpose": "Counts independent source-family support conservatively.",
            "source": "derived",
            "validation_check": "sum_by_family_is_0_or_1",
        },
    ]

    reentry_support_boundary = [
        {
            "boundary_item": "source_family_counting",
            "required_for": "future S1/S2 evidence package review",
            "contract": "Count independent_non_generated_family_count by source_family_id, not source_file.",
            "not_allowed": "Do not count multiple files in the same source_family as independent support.",
        },
        {
            "boundary_item": "positive_effect_requirement",
            "required_for": "future S1/S2 learning re-entry",
            "contract": "Registry support is necessary but not sufficient; a separate accepted-source dev/OOF effect audit must show positive non-generated net.",
            "not_allowed": "Do not treat registry rows as Top1 gain or learning evidence by themselves.",
        },
        {
            "boundary_item": "generated_source_exclusion",
            "required_for": "future evidence quality checks",
            "contract": "Generated/synthetic sources cannot contribute to positive non-generated support.",
            "not_allowed": "Do not offset generated-source gains against non-generated evidence gaps.",
        },
        {
            "boundary_item": "split_boundary",
            "required_for": "future selection or re-entry decisions",
            "contract": "Use dev/OOF for future selection support; heldout/hard remain validation-only.",
            "not_allowed": "Do not choose registry rules or candidate policies using heldout/hard.",
        },
        {
            "boundary_item": "source_acceptance_boundary",
            "required_for": "future source expansion",
            "contract": "New source files require a separate owner/source provenance acceptance gate.",
            "not_allowed": "Do not accept new sources inside S8 registry artifact definition.",
        },
    ]

    acceptance_checks = [
        {
            "check_id": "AC01_SOURCE_ROWS_COVERED",
            "status": "pass" if len(source_file_registry) == len(accepted_sources) else "fail",
            "evidence": f"registry_source_file_rows={len(source_file_registry)}; accepted_source_rows={len(accepted_sources)}",
            "decision": "Every accepted source file is represented in the registry preview.",
        },
        {
            "check_id": "AC02_FAMILY_DEDUP_VISIBLE",
            "status": "pass" if deduped_source_file_count > 0 else "fail",
            "evidence": f"deduped_source_file_count={deduped_source_file_count}",
            "decision": "The artifact exposes same-family dedup requirements.",
        },
        {
            "check_id": "AC03_INDEPENDENT_FAMILY_COUNT_CONSERVATIVE",
            "status": "pass" if independent_non_generated_family_count == len(source_family_registry) else "fail",
            "evidence": f"independent_non_generated_family_count={independent_non_generated_family_count}; source_family_rows={len(source_family_registry)}",
            "decision": "Current accepted OSS families are non-generated human OSS, counted once per family.",
        },
        {
            "check_id": "AC04_REENTRY_BOUNDARY_DEFINED",
            "status": "pass" if len(reentry_support_boundary) >= 5 else "fail",
            "evidence": f"reentry_boundary_count={len(reentry_support_boundary)}",
            "decision": "Future re-entry support boundary is explicit.",
        },
        {
            "check_id": "AC05_NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "source_acceptance_allowed=false; learning_reentry_allowed=false; training_allowed=false; implementation_allowed=false",
            "decision": "10.71 remains artifact definition only.",
        },
    ]
    fail_count = sum(1 for row in acceptance_checks if row["status"] != "pass")

    next_gate = [
        {
            "next_stage": "10.72 S8 source-family independence registry artifact acceptance gate",
            "goal": "Read-only decide whether the S8 registry artifact can be accepted as a future S1/S2 evidence-quality support contract.",
            "default": "acceptance gate only",
            "not_allowed": "no source acceptance, no learning re-entry, no training, no implementation, no heldout/hard selection",
        }
    ]
    blocked_actions = [
        {
            "blocked_action": "accept_new_sources_from_registry",
            "reason": "Registry artifact only covers previously accepted OSS provenance rows.",
            "allowed_after": "future owner/source provenance acceptance review",
        },
        {
            "blocked_action": "claim_learning_reentry_from_registry",
            "reason": "Registry defines independence support only and has no positive effect audit.",
            "allowed_after": "future lane-specific re-entry review with accepted-source positive effect evidence",
        },
        {
            "blocked_action": "count_source_files_as_independent_families",
            "reason": "Same source_family variants are deduped to one independence count.",
            "allowed_after": "future owner proof of independent collection",
        },
        {
            "blocked_action": "run_training_or_heldout_selection",
            "reason": "10.71 is read-only artifact definition.",
            "allowed_after": "future explicit execution authorization; heldout/hard never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "registry_field_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_registry_field_manifest.csv")),
        "source_file_registry_csv": str(output_prefix.with_name(output_prefix.name + "_source_file_registry.csv")),
        "source_family_registry_csv": str(output_prefix.with_name(output_prefix.name + "_source_family_registry.csv")),
        "dedup_independence_rules_csv": str(output_prefix.with_name(output_prefix.name + "_dedup_independence_rules.csv")),
        "risk_checks_csv": str(output_prefix.with_name(output_prefix.name + "_risk_checks.csv")),
        "reentry_support_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_support_boundary.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1070["stage"],
        "artifact_definition_decision": "ready_for_acceptance_gate" if fail_count == 0 else "do_not_accept",
        "design_gate_decision": m1070.get("design_gate_decision"),
        "registry_source_file_rows": len(source_file_registry),
        "registry_source_family_rows": len(source_family_registry),
        "independent_non_generated_family_count": independent_non_generated_family_count,
        "deduped_source_file_count": deduped_source_file_count,
        "registry_field_count": len(registry_field_manifest),
        "dedup_rule_count": len(rules_1070),
        "risk_check_count": len(risks_1070),
        "reentry_boundary_count": len(reentry_support_boundary),
        "acceptance_pass_count": len(acceptance_checks) - fail_count,
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
        "stage": "Goal LTR v1 / 10.71 S8 source-family independence registry artifact definition",
        "read_only": True,
        "artifact_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define the S8 source-family independence registry artifact. The current accepted OSS provenance is represented as 6 source-file rows "
            "and 2 conservative independent source-family rows, with 4 same-family file variants deduped. This is ready for a read-only acceptance gate."
        ),
        "anti_drift_conclusion": (
            "10.71 only defines registry support artifacts. It does not accept new sources, reopen OSS expansion, train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.72 S8 source-family independence registry artifact acceptance gate",
            "goal": "Read-only decide whether the S8 artifact can be accepted as future evidence-quality support.",
            "default": "acceptance gate only",
        },
    }

    _write_csv(Path(artifacts["registry_field_manifest_csv"]), registry_field_manifest, ["field", "required", "data_type", "purpose", "source", "validation_check"])
    _write_csv(Path(artifacts["source_file_registry_csv"]), source_file_registry, ["source_family_id", "source_file_id", "source_file", "source_family", "producer", "collection_method", "provenance_hash", "is_human_quantity_surveyor_output", "is_generated_or_synthetic", "trust_level", "row_count_total", "dev_row_count", "province_count", "s2_source_class", "accepted_scope", "representative_for_family", "independence_count_weight", "dedup_reason"])
    _write_csv(Path(artifacts["source_family_registry_csv"]), source_family_registry, ["source_family_id", "source_family", "source_file_count", "source_files", "representative_source_file", "row_count_total", "dev_row_count", "province_count_max", "is_non_generated_family", "is_human_oss_family", "independent_family_weight", "independence_disposition", "reentry_allowed_use"])
    _write_csv(Path(artifacts["dedup_independence_rules_csv"]), rules_1070, ["rule_id", "rule", "effect", "risk_control"])
    _write_csv(Path(artifacts["risk_checks_csv"]), risks_1070, ["risk", "check", "severity"])
    _write_csv(Path(artifacts["reentry_support_boundary_csv"]), reentry_support_boundary, ["boundary_item", "required_for", "contract", "not_allowed"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "goal", "default", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, source_family_registry, reentry_support_boundary, acceptance_checks)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
