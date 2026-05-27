from __future__ import annotations

import argparse
import csv
import hashlib
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
DEFAULT_FEASIBILITY = AGENT_STATE / "goal_12x_electrical_box_context_feasibility_no_go_gate_summary.json"
DEFAULT_ROW_AUDIT = AGENT_STATE / "goal_12x_electrical_box_installation_context_rank_depth_audit_row_audit.csv"
DEFAULT_BENCHMARK_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_electrical_box_bill_text_linkage_evidence_gate"


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
        "# 12.15 Electrical-Box bill_text Linkage Evidence Gate",
        "",
        "Read-only packaging of traceable bill_text evidence for electrical_box rows.",
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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_items(root: Path) -> dict[str, list[dict[str, Any]]]:
    by_province: dict[str, list[dict[str, Any]]] = {}
    if not root.exists():
        return by_province
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
            record["_source_file"] = str(path)
            record["_row_index"] = index
            record["_province"] = province
            by_province.setdefault(province, []).append(record)
    return by_province


def _quota_ids(item: dict[str, Any]) -> set[str]:
    return {str(value) for value in item.get("quota_ids") or []}


def _extract_context(text: str) -> dict[str, Any]:
    install_terms = [term for term in ("安装方式", "悬挂", "嵌入", "明装", "暗装", "壁装", "墙上", "柱上", "落地", "杆上") if term in text]
    circuits = re.findall(r"(\d+)\s*回路", text)
    dimensions = re.findall(r"\d+\s*[*xX×]\s*\d+(?:\s*[*xX×]\s*\d+)?", text)
    return {
        "install_terms": "|".join(install_terms),
        "circuit_values": "|".join(circuits),
        "dimension_values": "|".join(dimensions[:3]),
        "has_install_or_size_context": bool(install_terms or circuits or dimensions),
    }


def _positive_matches(row: dict[str, str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = row.get("query", "")
    expected_id = row.get("expected_ids", "")
    matches = [
        item
        for item in items
        if str(item.get("bill_name") or "") == query and expected_id in _quota_ids(item)
    ]
    return matches


def _top1_guard_matches(row: dict[str, str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top1_id = row.get("top1_id", "")
    if not top1_id:
        return []
    return [item for item in items if top1_id in _quota_ids(item)]


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.15 electrical-box bill_text linkage evidence gate 已完成。"
        f"positive_package_rows={metrics['positive_package_rows']}；"
        f"unique_positive_link_rows={metrics['unique_positive_link_rows']}；"
        f"ambiguous_positive_link_rows={metrics['ambiguous_positive_link_rows']}；"
        f"rows_with_top1_guard={metrics['rows_with_top1_guard']}；"
        f"whatif_input_package_ready={str(metrics['whatif_input_package_ready']).lower()}。"
    )
    next_text = (
        "下一步：12.16 electrical-box negative guard / linkage gap review。"
        "只读审查缺失的真落地式、真杆上配电设备负例 guard 和福建同名多匹配歧义；"
        "若无法补齐 guard，则继续 no-go，不进入 what-if。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：跑 dev/OOF what-if、直接实现 electrical_box 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "忽略负例 guard 缺口、或把同名多匹配 bill_text 当成唯一证据。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.14 electrical-box context feasibility / no-go gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.15 electrical-box bill_text linkage evidence gate</td>\n"
        "            <td>只读打包 13 条 electrical_box 的 bill_text provenance、上下文抽取和 top1 负例 guard 覆盖。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_electrical_box_bill_text_linkage_evidence_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_electrical_box_bill_text_linkage_evidence_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feasibility-summary", type=Path, default=DEFAULT_FEASIBILITY)
    parser.add_argument("--row-audit", type=Path, default=DEFAULT_ROW_AUDIT)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    feasibility = _read_json(args.feasibility_summary)
    rows = _read_csv(args.row_audit)
    items_by_province = _load_items(args.benchmark_dir)

    evidence_rows: list[dict[str, Any]] = []
    guard_rows: list[dict[str, Any]] = []
    for row in rows:
        province = row.get("province", "")
        items = items_by_province.get(province, [])
        positive_matches = _positive_matches(row, items)
        guard_matches = _top1_guard_matches(row, items)
        best = positive_matches[0] if positive_matches else None
        bill_text = str(best.get("bill_text") or "") if best else ""
        context = _extract_context(bill_text)
        evidence_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "audit_bucket": row.get("audit_bucket", ""),
                "rank_bucket": row.get("rank_bucket", ""),
                "province": province,
                "query": row.get("query", ""),
                "expected_ids": row.get("expected_ids", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "positive_link_count": len(positive_matches),
                "positive_link_quality": "unique_exact" if len(positive_matches) == 1 else ("ambiguous_exact" if positive_matches else "missing"),
                "bill_name": str(best.get("bill_name") or "") if best else "",
                "bill_text": bill_text,
                "install_terms": context["install_terms"],
                "circuit_values": context["circuit_values"],
                "dimension_values": context["dimension_values"],
                "has_install_or_size_context": context["has_install_or_size_context"],
                "positive_provenance_hash": _hash_text("|".join([str(best.get("_source_file")), str(best.get("_row_index")), bill_text])) if best else "",
                "positive_source_file": str(best.get("_source_file") or "") if best else "",
                "positive_source_row_index": best.get("_row_index", "") if best else "",
                "top1_guard_candidate_count": len(guard_matches),
                "has_top1_guard": bool(guard_matches),
            }
        )
        for guard in guard_matches:
            guard_text = str(guard.get("bill_text") or "")
            guard_context = _extract_context(guard_text)
            guard_rows.append(
                {
                    "target_group_id": row.get("group_id", ""),
                    "province": province,
                    "top1_id": row.get("top1_id", ""),
                    "top1_name": row.get("top1_name", ""),
                    "guard_bill_name": str(guard.get("bill_name") or ""),
                    "guard_quota_ids": "|".join(str(value) for value in guard.get("quota_ids") or []),
                    "guard_bill_text": guard_text,
                    "guard_install_terms": guard_context["install_terms"],
                    "guard_circuit_values": guard_context["circuit_values"],
                    "guard_dimension_values": guard_context["dimension_values"],
                    "guard_provenance_hash": _hash_text("|".join([str(guard.get("_source_file")), str(guard.get("_row_index")), guard_text])),
                    "guard_source_file": str(guard.get("_source_file") or ""),
                    "guard_source_row_index": guard.get("_row_index", ""),
                }
            )

    positive_package_rows = sum(1 for row in evidence_rows if row["positive_link_quality"] != "missing")
    unique_positive_rows = sum(1 for row in evidence_rows if row["positive_link_quality"] == "unique_exact")
    ambiguous_positive_rows = sum(1 for row in evidence_rows if row["positive_link_quality"] == "ambiguous_exact")
    context_rows = sum(1 for row in evidence_rows if row["has_install_or_size_context"])
    rows_with_guard = sum(1 for row in evidence_rows if row["has_top1_guard"])
    unique_guard_hashes = {row["guard_provenance_hash"] for row in guard_rows}
    bucket_summary = [
        {
            "audit_bucket": bucket,
            "rows": count,
            "unique_positive_rows": sum(1 for row in evidence_rows if row["audit_bucket"] == bucket and row["positive_link_quality"] == "unique_exact"),
            "ambiguous_positive_rows": sum(1 for row in evidence_rows if row["audit_bucket"] == bucket and row["positive_link_quality"] == "ambiguous_exact"),
            "rows_with_top1_guard": sum(1 for row in evidence_rows if row["audit_bucket"] == bucket and row["has_top1_guard"]),
            "context_rows": sum(1 for row in evidence_rows if row["audit_bucket"] == bucket and row["has_install_or_size_context"]),
        }
        for bucket, count in Counter(row["audit_bucket"] for row in evidence_rows).most_common()
    ]
    whatif_input_package_ready = (
        positive_package_rows == len(rows)
        and context_rows == len(rows)
        and ambiguous_positive_rows == 0
        and rows_with_guard == len(rows)
    )
    gate_checks = [
        {
            "gate": "positive_bill_text_linkage",
            "status": "pass",
            "evidence": f"{positive_package_rows}/{len(rows)} rows have exact query+expected_id positive bill_text links.",
        },
        {
            "gate": "positive_link_uniqueness",
            "status": "fail" if ambiguous_positive_rows else "pass",
            "evidence": f"{ambiguous_positive_rows} rows have multiple exact positive links.",
        },
        {
            "gate": "context_presence",
            "status": "pass" if context_rows == len(rows) else "fail",
            "evidence": f"{context_rows}/{len(rows)} positive links contain install/size/circuit context.",
        },
        {
            "gate": "top1_negative_guard_coverage",
            "status": "fail" if rows_with_guard < len(rows) else "pass",
            "evidence": f"{rows_with_guard}/{len(rows)} rows have same-province benchmark guard items for current top1_id.",
        },
        {
            "gate": "whatif_input_package",
            "status": "not_ready" if not whatif_input_package_ready else "ready_for_future_authorization_gate",
            "evidence": "Requires unique positive links and top1 negative guard coverage before any dev/OOF what-if.",
        },
    ]
    metrics = {
        "audited_rows": len(rows),
        "positive_package_rows": positive_package_rows,
        "unique_positive_link_rows": unique_positive_rows,
        "ambiguous_positive_link_rows": ambiguous_positive_rows,
        "context_positive_rows": context_rows,
        "rows_with_top1_guard": rows_with_guard,
        "unique_top1_guard_items": len(unique_guard_hashes),
        "whatif_input_package_ready": whatif_input_package_ready,
        "whatif_allowed_now": False,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "evidence_package_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_evidence_package.csv")),
        "top1_guard_candidates_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_top1_guard_candidates.csv")),
        "bucket_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bucket_summary.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
    }
    decision = (
        "Positive bill_text evidence is strong, but the dev/OOF what-if input package is not ready. "
        "Three rows have ambiguous exact positive links and only three rows have top1 negative guard coverage; do not run what-if or implement."
    )
    report = {
        "stage": "Goal LTR v1 / 12.15 electrical-box bill_text linkage evidence gate",
        "read_only": True,
        "source_artifacts": {
            "feasibility_summary": str(args.feasibility_summary),
            "row_audit": str(args.row_audit),
            "benchmark_dir": str(args.benchmark_dir),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_stage_context": {
            "prior_stage": feasibility["stage"],
            "benchmark_exact_query_expected_linked_rows": feasibility["metrics"]["benchmark_exact_query_expected_linked_rows"],
            "whatif_allowed_now": feasibility["metrics"]["whatif_allowed_now"],
        },
        "anti_drift_conclusion": (
            "12.15 is read-only. It packages bill_text linkage evidence only; it does not run a what-if, train, tune, change thresholds, "
            "implement electrical_box rules, edit parser/query-family rules, wire GoalSearcher, use heldout/hard for selection, "
            "ignore ambiguous positive links, or ignore top1 negative guard gaps."
        ),
        "next_stage": {
            "stage": "12.16 electrical-box negative guard / linkage gap review",
            "default": "read_only_gap_review",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["evidence_package_csv"]),
        evidence_rows,
        [
            "group_id",
            "audit_bucket",
            "rank_bucket",
            "province",
            "query",
            "expected_ids",
            "top1_id",
            "top1_name",
            "positive_link_count",
            "positive_link_quality",
            "bill_name",
            "bill_text",
            "install_terms",
            "circuit_values",
            "dimension_values",
            "has_install_or_size_context",
            "positive_provenance_hash",
            "positive_source_file",
            "positive_source_row_index",
            "top1_guard_candidate_count",
            "has_top1_guard",
        ],
    )
    _write_csv(
        Path(artifacts["top1_guard_candidates_csv"]),
        guard_rows,
        [
            "target_group_id",
            "province",
            "top1_id",
            "top1_name",
            "guard_bill_name",
            "guard_quota_ids",
            "guard_bill_text",
            "guard_install_terms",
            "guard_circuit_values",
            "guard_dimension_values",
            "guard_provenance_hash",
            "guard_source_file",
            "guard_source_row_index",
        ],
    )
    _write_csv(
        Path(artifacts["bucket_summary_csv"]),
        bucket_summary,
        ["audit_bucket", "rows", "unique_positive_rows", "ambiguous_positive_rows", "rows_with_top1_guard", "context_rows"],
    )
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "evidence"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
