from __future__ import annotations

import argparse
import csv
import json
import re
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
DEFAULT_LINKAGE_SUMMARY = AGENT_STATE / "goal_12x_electrical_box_bill_text_linkage_evidence_gate_summary.json"
DEFAULT_EVIDENCE_PACKAGE = AGENT_STATE / "goal_12x_electrical_box_bill_text_linkage_evidence_gate_evidence_package.csv"
DEFAULT_GUARD_CANDIDATES = AGENT_STATE / "goal_12x_electrical_box_bill_text_linkage_evidence_gate_top1_guard_candidates.csv"
DEFAULT_BENCHMARK_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_electrical_box_negative_guard_linkage_gap_review"


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.16 Electrical-Box Negative Guard / Linkage Gap Review",
        "",
        "Read-only review of guard coverage and positive-link ambiguity before any electrical_box what-if.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _load_items(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not root.exists():
        return items
    for path in root.glob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            continue
        province = str(data.get("province") or path.stem.replace("_脏数据", ""))
        for index, item in enumerate(data["items"]):
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record["_province"] = province
            record["_source_file"] = str(path)
            record["_row_index"] = index
            items.append(record)
    return items


def _quota_ids(item: dict[str, Any]) -> set[str]:
    return {str(value) for value in item.get("quota_ids") or []}


def _quota_names(item: dict[str, Any]) -> list[str]:
    return [str(value) for value in item.get("quota_names") or []]


def _contains_guard_context(text: str, bucket: str) -> bool:
    if bucket == "floor_mounted_over_hung_embedded":
        return "落地" in text
    if bucket == "pole_equipment_over_wall_column_box":
        return "杆上" in text or "杆上配电设备" in text
    return False


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.16 electrical-box negative guard / linkage gap review 已完成。"
        f"missing_top1_guard_rows={metrics['missing_top1_guard_rows']}；"
        f"ambiguous_positive_link_rows={metrics['ambiguous_positive_link_rows']}；"
        f"pole_bucket_guard_rows={metrics['pole_bucket_guard_rows']}；"
        f"floor_bucket_guard_rows={metrics['floor_bucket_guard_rows']}；"
        f"whatif_allowed_now={str(metrics['whatif_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.17 electrical-box lane no-go closure and broader 12.x return。"
        "只读正式 park electrical_box lane，保留 13 条 bill_text 正向证据，列清未来重开所需负例 guard/唯一链接条件，"
        "然后回到 broader 12.x strategy review；仍不跑 what-if、不实现。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：跑 dev/OOF what-if、直接实现 electrical_box 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "忽略 top1 guard 缺口、忽略福建同名多匹配、或把跨省/非同 ID guard 当成已接受负例。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.15 electrical-box bill_text linkage evidence gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.16 electrical-box negative guard / linkage gap review</td>\n"
        "            <td>只读审查真落地式、真杆上配电设备负例 guard 和福建同名多匹配歧义；结论为 no-go。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_electrical_box_negative_guard_linkage_gap_review_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_electrical_box_negative_guard_linkage_gap_review_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linkage-summary", type=Path, default=DEFAULT_LINKAGE_SUMMARY)
    parser.add_argument("--evidence-package", type=Path, default=DEFAULT_EVIDENCE_PACKAGE)
    parser.add_argument("--guard-candidates", type=Path, default=DEFAULT_GUARD_CANDIDATES)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    linkage_summary = _read_json(args.linkage_summary)
    evidence_rows = _read_csv(args.evidence_package)
    guard_rows = _read_csv(args.guard_candidates)
    benchmark_items = _load_items(args.benchmark_dir)

    accepted_guard_by_group = {row.get("target_group_id", "") for row in guard_rows if row.get("guard_provenance_hash")}
    gap_rows: list[dict[str, Any]] = []
    broad_guard_rows: list[dict[str, Any]] = []
    for row in evidence_rows:
        group_id = row.get("group_id", "")
        bucket = row.get("audit_bucket", "")
        province = row.get("province", "")
        top1_id = row.get("top1_id", "")
        top1_name = row.get("top1_name", "")
        positive_quality = row.get("positive_link_quality", "")
        has_same_province_guard = group_id in accepted_guard_by_group
        # Broader guards are diagnostic only: same top1_id anywhere, or matching top1_name words.
        broad_matches: list[dict[str, Any]] = []
        for item in benchmark_items:
            item_text = " ".join(
                [
                    str(item.get("bill_text") or ""),
                    str(item.get("bill_name") or ""),
                    " ".join(_quota_names(item)),
                ]
            )
            same_id = top1_id and top1_id in _quota_ids(item)
            same_name = bool(top1_name and top1_name in item_text)
            if (same_id or same_name) and _contains_guard_context(item_text, bucket):
                broad_matches.append(item)
        for item in broad_matches[:5]:
            broad_guard_rows.append(
                {
                    "target_group_id": group_id,
                    "guard_scope": "same_province_exact" if item.get("_province") == province and top1_id in _quota_ids(item) else "diagnostic_broad_only",
                    "target_top1_id": top1_id,
                    "target_top1_name": top1_name,
                    "guard_province": item.get("_province", ""),
                    "guard_bill_name": item.get("bill_name", ""),
                    "guard_quota_ids": "|".join(str(value) for value in item.get("quota_ids") or []),
                    "guard_quota_names": "|".join(_quota_names(item)),
                    "guard_bill_text": str(item.get("bill_text") or "")[:260],
                    "guard_source_file": item.get("_source_file", ""),
                    "guard_source_row_index": item.get("_row_index", ""),
                }
            )
        if positive_quality != "unique_exact" or not has_same_province_guard:
            reasons = []
            if positive_quality != "unique_exact":
                reasons.append("ambiguous_positive_link")
            if not has_same_province_guard:
                reasons.append("missing_same_province_top1_guard")
            gap_rows.append(
                {
                    "group_id": group_id,
                    "audit_bucket": bucket,
                    "province": province,
                    "query": row.get("query", ""),
                    "expected_ids": row.get("expected_ids", ""),
                    "top1_id": top1_id,
                    "top1_name": top1_name,
                    "positive_link_quality": positive_quality,
                    "same_province_top1_guard": has_same_province_guard,
                    "diagnostic_broad_guard_candidates": len(broad_matches),
                    "gap_reasons": "|".join(reasons),
                    "closure_disposition": "block_whatif_until_unique_positive_and_same_province_guard",
                }
            )

    bucket_summary = []
    for bucket, count in Counter(row.get("audit_bucket", "") for row in evidence_rows).most_common():
        bucket_rows = [row for row in evidence_rows if row.get("audit_bucket") == bucket]
        bucket_group_ids = {row.get("group_id") for row in bucket_rows}
        bucket_summary.append(
            {
                "audit_bucket": bucket,
                "rows": count,
                "ambiguous_positive_rows": sum(1 for row in bucket_rows if row.get("positive_link_quality") != "unique_exact"),
                "same_province_guard_rows": sum(1 for group_id in bucket_group_ids if group_id in accepted_guard_by_group),
                "missing_guard_rows": sum(1 for group_id in bucket_group_ids if group_id not in accepted_guard_by_group),
                "decision": "no_go_needs_guard_or_linkage_cleanup",
            }
        )

    missing_guard_rows = sum(1 for row in evidence_rows if row.get("group_id") not in accepted_guard_by_group)
    ambiguous_positive_rows = sum(1 for row in evidence_rows if row.get("positive_link_quality") != "unique_exact")
    floor_guard_rows = sum(
        1
        for row in evidence_rows
        if row.get("audit_bucket") == "floor_mounted_over_hung_embedded" and row.get("group_id") in accepted_guard_by_group
    )
    pole_guard_rows = sum(
        1
        for row in evidence_rows
        if row.get("audit_bucket") == "pole_equipment_over_wall_column_box" and row.get("group_id") in accepted_guard_by_group
    )
    accepted_same_province_guard_rows = len(accepted_guard_by_group)
    whatif_allowed_now = False

    gate_checks = [
        {
            "gate": "positive_linkage_uniqueness",
            "status": "fail" if ambiguous_positive_rows else "pass",
            "evidence": f"{ambiguous_positive_rows}/{len(evidence_rows)} rows remain ambiguous exact positive links.",
        },
        {
            "gate": "same_province_top1_guard_coverage",
            "status": "fail" if missing_guard_rows else "pass",
            "evidence": f"{accepted_same_province_guard_rows}/{len(evidence_rows)} rows have same-province exact top1 guard coverage.",
        },
        {
            "gate": "pole_bucket_guard_coverage",
            "status": "fail" if pole_guard_rows == 0 else "pass",
            "evidence": f"{pole_guard_rows}/6 pole-equipment rows have accepted top1 guard coverage.",
        },
        {
            "gate": "broad_guard_substitution",
            "status": "blocked",
            "evidence": "Cross-province or non-identical-id guard candidates are diagnostic only and cannot replace accepted same-province top1 guards.",
        },
        {
            "gate": "whatif_authorization",
            "status": "blocked",
            "evidence": "Do not run dev/OOF what-if until unique positives and accepted negative guards are complete.",
        },
    ]
    reentry_requirements = [
        {
            "requirement": "unique_positive_for_fujian_plain_box",
            "needed": "Resolve dev:36:37, dev:40:41, dev:52:11 to one accepted bill_text each, or exclude them.",
            "current_gap": "3 ambiguous exact positive links for plain 配电箱 in 福建.",
        },
        {
            "requirement": "top1_negative_guard_for_henan_floor",
            "needed": "Same-province accepted bill_text where 4-2-74 is truly correct, or explicit exclusion of 河南 floor bucket.",
            "current_gap": "0/4 河南 floor rows have same-province 4-2-74 guard.",
        },
        {
            "requirement": "top1_negative_guard_for_beijing_pole",
            "needed": "Same-province accepted bill_text where C4-10-114 杆上配电设备安装 配电箱 is truly correct, or exclude pole bucket.",
            "current_gap": "0/6 北京 pole rows have same-province C4-10-114 guard.",
        },
    ]
    metrics = {
        "reviewed_rows": len(evidence_rows),
        "ambiguous_positive_link_rows": ambiguous_positive_rows,
        "accepted_same_province_guard_rows": accepted_same_province_guard_rows,
        "missing_top1_guard_rows": missing_guard_rows,
        "floor_bucket_guard_rows": floor_guard_rows,
        "pole_bucket_guard_rows": pole_guard_rows,
        "gap_rows": len(gap_rows),
        "diagnostic_broad_guard_rows": len(broad_guard_rows),
        "whatif_allowed_now": whatif_allowed_now,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gap_rows_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gap_rows.csv")),
        "bucket_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bucket_summary.csv")),
        "broad_guard_candidates_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_broad_guard_candidates.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "reentry_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_reentry_requirements.csv")),
    }
    decision = (
        "Continue no-go and do not run electrical_box what-if. Positive bill_text evidence is useful, but negative guard coverage "
        "and the Fujian plain distribution-box linkage ambiguity are not resolved; cross-province/broad guard candidates are diagnostic only."
    )
    report = {
        "stage": "Goal LTR v1 / 12.16 electrical-box negative guard / linkage gap review",
        "read_only": True,
        "source_artifacts": {
            "linkage_summary": str(args.linkage_summary),
            "evidence_package": str(args.evidence_package),
            "guard_candidates": str(args.guard_candidates),
            "benchmark_dir": str(args.benchmark_dir),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_stage_context": {
            "prior_stage": linkage_summary["stage"],
            "prior_whatif_input_package_ready": linkage_summary["metrics"]["whatif_input_package_ready"],
            "prior_rows_with_top1_guard": linkage_summary["metrics"]["rows_with_top1_guard"],
        },
        "anti_drift_conclusion": (
            "12.16 is read-only. It does not run dev/OOF what-if, train, tune, change thresholds, implement electrical_box rules, "
            "edit parser/query-family rules, wire GoalSearcher, use heldout/hard for selection, accept broad/cross-province guards as replacements, "
            "or ignore the Fujian plain distribution-box positive-link ambiguity."
        ),
        "next_stage": {
            "stage": "12.17 electrical-box lane no-go closure and broader 12.x return",
            "default": "read_only_closure_review",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["gap_rows_csv"]),
        gap_rows,
        [
            "group_id",
            "audit_bucket",
            "province",
            "query",
            "expected_ids",
            "top1_id",
            "top1_name",
            "positive_link_quality",
            "same_province_top1_guard",
            "diagnostic_broad_guard_candidates",
            "gap_reasons",
            "closure_disposition",
        ],
    )
    _write_csv(Path(artifacts["bucket_summary_csv"]), bucket_summary, ["audit_bucket", "rows", "ambiguous_positive_rows", "same_province_guard_rows", "missing_guard_rows", "decision"])
    _write_csv(
        Path(artifacts["broad_guard_candidates_csv"]),
        broad_guard_rows,
        [
            "target_group_id",
            "guard_scope",
            "target_top1_id",
            "target_top1_name",
            "guard_province",
            "guard_bill_name",
            "guard_quota_ids",
            "guard_quota_names",
            "guard_bill_text",
            "guard_source_file",
            "guard_source_row_index",
        ],
    )
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "evidence"])
    _write_csv(Path(artifacts["reentry_requirements_csv"]), reentry_requirements, ["requirement", "needed", "current_gap"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
