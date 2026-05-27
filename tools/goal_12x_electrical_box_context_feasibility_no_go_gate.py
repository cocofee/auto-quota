from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_CONTEXT_AUDIT = AGENT_STATE / "goal_12x_electrical_box_installation_context_rank_depth_audit_summary.json"
DEFAULT_ROW_AUDIT = AGENT_STATE / "goal_12x_electrical_box_installation_context_rank_depth_audit_row_audit.csv"
DEFAULT_BENCHMARK_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_electrical_box_context_feasibility_no_go_gate"

CONTEXT_RE = re.compile(r"(安装方式|明装|暗装|悬挂|嵌入|落地|墙上|柱上|杆上|半周长|回路|规格[:：]|\d+\s*回路)")


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
        "# 12.14 Electrical-Box Context Feasibility / No-Go Gate",
        "",
        "Read-only feasibility gate for electrical_box context before any dev/OOF what-if.",
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


def _load_benchmark_items(root: Path) -> dict[str, list[dict[str, Any]]]:
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
        for item in data["items"]:
            if isinstance(item, dict):
                item = dict(item)
                item["_source_file"] = str(path)
                by_province.setdefault(province, []).append(item)
    return by_province


def _has_context(text: str) -> bool:
    return bool(CONTEXT_RE.search(text or ""))


def _best_link(row: dict[str, str], items_by_province: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    province = row.get("province", "")
    query = row.get("query", "")
    expected_id = row.get("expected_ids", "")
    items = items_by_province.get(province, [])
    exact_query_expected = []
    expected_only = []
    query_only = []
    for item in items:
        bill_name = str(item.get("bill_name") or "")
        quota_ids = {str(value) for value in item.get("quota_ids") or []}
        has_expected = expected_id in quota_ids
        has_query = bill_name == query
        if has_expected and has_query:
            exact_query_expected.append(item)
        elif has_expected:
            expected_only.append(item)
        elif has_query:
            query_only.append(item)
    best = (exact_query_expected or expected_only or query_only or [None])[0]
    best_text = str(best.get("bill_text") or "") if best else ""
    link_quality = "none"
    if exact_query_expected:
        link_quality = "exact_query_and_expected_id"
    elif expected_only:
        link_quality = "expected_id_only"
    elif query_only:
        link_quality = "query_only"
    return {
        "benchmark_link_quality": link_quality,
        "exact_query_expected_hits": len(exact_query_expected),
        "expected_id_only_hits": len(expected_only),
        "query_only_hits": len(query_only),
        "linked_bill_text_has_context": _has_context(best_text),
        "linked_bill_name": str(best.get("bill_name") or "") if best else "",
        "linked_quota_ids": "|".join(str(value) for value in (best.get("quota_ids") or [])) if best else "",
        "linked_bill_text_excerpt": best_text[:220],
        "linked_source_file": str(best.get("_source_file") or "") if best else "",
    }


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.14 electrical-box context feasibility / no-go gate 已完成。"
        f"row_query_context_rows={metrics['row_query_context_rows']}；"
        f"benchmark_linked_rows={metrics['benchmark_linked_rows']}；"
        f"benchmark_context_rows={metrics['benchmark_context_rows']}；"
        f"whatif_allowed_now={str(metrics['whatif_allowed_now']).lower()}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.15 electrical-box bill_text linkage evidence gate。"
        "只读把 tests/benchmark_papers 中可追溯 bill_text 与这 13 条 dev/OOF electrical_box 行做 exact query + expected_id / expected_id-only 链接分级，"
        "判断是否能形成 dev/OOF-only what-if 输入包；仍不跑 what-if、不实现。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现 electrical_box 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "把 benchmark bill_text 未经 linkage/provenance 就当作 dev/OOF 输入、或从 expected label 反推 query 里不存在的上下文。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.13 electrical-box installation/context rank-depth audit</td>"
    row = (
        "          <tr>\n"
        "            <td>12.14 electrical-box context feasibility / no-go gate</td>\n"
        "            <td>只读判断 electrical_box 子桶是否可进入 dev/OOF what-if；当前 no-go，但发现 benchmark bill_text linkage 入口。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_electrical_box_context_feasibility_no_go_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_electrical_box_context_feasibility_no_go_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-audit-summary", type=Path, default=DEFAULT_CONTEXT_AUDIT)
    parser.add_argument("--row-audit", type=Path, default=DEFAULT_ROW_AUDIT)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    context_audit = _read_json(args.context_audit_summary)
    rows = _read_csv(args.row_audit)
    items_by_province = _load_benchmark_items(args.benchmark_dir)

    linked_rows: list[dict[str, Any]] = []
    for row in rows:
        link = _best_link(row, items_by_province)
        linked_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "rank_bucket": row.get("rank_bucket", ""),
                "audit_bucket": row.get("audit_bucket", ""),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "expected_ids": row.get("expected_ids", ""),
                "row_query_has_context": row.get("query_has_install_signal") == "True" or row.get("query_has_size_signal") == "True",
                **link,
            }
        )

    row_query_context_rows = sum(1 for row in linked_rows if row["row_query_has_context"])
    benchmark_linked_rows = sum(1 for row in linked_rows if row["benchmark_link_quality"] != "none")
    exact_linked_rows = sum(1 for row in linked_rows if row["benchmark_link_quality"] == "exact_query_and_expected_id")
    benchmark_context_rows = sum(1 for row in linked_rows if row["linked_bill_text_has_context"])
    ambiguous_link_rows = sum(1 for row in linked_rows if row["benchmark_link_quality"] == "expected_id_only")

    whatif_allowed_now = False
    gate_checks = [
        {
            "gate": "row_query_context",
            "status": "fail",
            "evidence": f"{row_query_context_rows}/{len(rows)} dev/OOF gap rows have direct query context.",
        },
        {
            "gate": "benchmark_bill_text_linkage",
            "status": "candidate_input_found" if benchmark_linked_rows else "missing",
            "evidence": f"{benchmark_linked_rows}/{len(rows)} rows link to local benchmark_papers bill_text; {exact_linked_rows} exact query+expected_id.",
        },
        {
            "gate": "benchmark_context_presence",
            "status": "candidate_input_found" if benchmark_context_rows else "missing",
            "evidence": f"{benchmark_context_rows}/{len(rows)} linked bill_text rows contain install/size/circuit context.",
        },
        {
            "gate": "dev_oof_artifact_boundary",
            "status": "blocked_for_immediate_whatif",
            "evidence": "bill_text is not present in the dev/OOF gap artifact; linkage/provenance package is required first.",
        },
        {
            "gate": "ambiguous_plain_query_rows",
            "status": "needs_review" if ambiguous_link_rows else "pass",
            "evidence": f"{ambiguous_link_rows} rows link by expected_id-only rather than exact query+expected_id.",
        },
        {
            "gate": "implementation_boundary",
            "status": "blocked",
            "evidence": "No electrical_box rule or what-if is authorized by this gate.",
        },
    ]
    next_requirements = [
        {
            "requirement": "traceable_bill_text_package",
            "definition": "For each row, provide source_file, bill_name, bill_text, quota_ids, link_quality, and context flags.",
        },
        {
            "requirement": "dev_oof_boundary",
            "definition": "Only rows traceably tied to the existing dev/OOF gap rows may be used; no heldout/hard selection.",
        },
        {
            "requirement": "negative_guard_set",
            "definition": "Include true floor-mounted and true pole-mounted electrical_box examples before any rerank what-if.",
        },
    ]
    metrics = {
        "row_audit_rows": len(rows),
        "row_query_context_rows": row_query_context_rows,
        "benchmark_linked_rows": benchmark_linked_rows,
        "benchmark_exact_query_expected_linked_rows": exact_linked_rows,
        "benchmark_expected_id_only_rows": ambiguous_link_rows,
        "benchmark_context_rows": benchmark_context_rows,
        "whatif_allowed_now": whatif_allowed_now,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "linked_rows_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_linked_rows.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "next_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_next_requirements.csv")),
    }
    decision = (
        "No-go for immediate dev/OOF what-if or implementation. The dev/OOF gap rows themselves still have zero direct query context, "
        "but local benchmark_papers contain candidate bill_text context that can be assembled in a separate read-only linkage evidence gate."
    )
    report = {
        "stage": "Goal LTR v1 / 12.14 electrical-box context feasibility / no-go gate",
        "read_only": True,
        "source_artifacts": {
            "context_audit_summary": str(args.context_audit_summary),
            "row_audit": str(args.row_audit),
            "benchmark_dir": str(args.benchmark_dir),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_stage_context": {
            "prior_stage": context_audit["stage"],
            "prior_whatif_ready_rows": context_audit["metrics"]["whatif_ready_rows"],
            "prior_query_text_direct_context_rows": context_audit["metrics"]["query_text_direct_context_rows"],
        },
        "anti_drift_conclusion": (
            "12.14 is read-only. It does not run a what-if, train, tune, change thresholds, implement electrical_box rules, edit parser/query-family rules, "
            "edit taxonomy rows, wire GoalSearcher, use heldout/hard for selection, or treat benchmark bill_text as dev/OOF input before linkage/provenance review."
        ),
        "next_stage": {
            "stage": "12.15 electrical-box bill_text linkage evidence gate",
            "default": "read_only_linkage_gate_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["linked_rows_csv"]),
        linked_rows,
        [
            "group_id",
            "rank_bucket",
            "audit_bucket",
            "province",
            "query",
            "expected_ids",
            "row_query_has_context",
            "benchmark_link_quality",
            "exact_query_expected_hits",
            "expected_id_only_hits",
            "query_only_hits",
            "linked_bill_text_has_context",
            "linked_bill_name",
            "linked_quota_ids",
            "linked_bill_text_excerpt",
            "linked_source_file",
        ],
    )
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "evidence"])
    _write_csv(Path(artifacts["next_requirements_csv"]), next_requirements, ["requirement", "definition"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
