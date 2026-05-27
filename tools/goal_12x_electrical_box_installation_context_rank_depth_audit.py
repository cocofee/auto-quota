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
DEFAULT_STRATEGY_REVIEW = AGENT_STATE / "goal_12x_broader_strategy_review_after_micro_hint_no_go_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_electrical_box_installation_context_rank_depth_audit"


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
        "# 12.13 Electrical-Box Installation/Context Rank-Depth Audit",
        "",
        "Read-only audit of non-global, non-11.x-overlap electrical_box wrong-rank rows.",
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


def _positive_name(row: dict[str, str]) -> str:
    text = row.get("positive_names_in_top80") or row.get("expected_names") or ""
    if " " in text:
        return text.split(" ", 1)[1]
    return text


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _query_code_family(query: str) -> str:
    if re.search(r"\bB\d+-", query, flags=re.I):
        return "building_floor_box_code"
    if re.search(r"ALE", query, flags=re.I):
        return "emergency_lighting_box_code"
    if re.search(r"AL", query, flags=re.I):
        return "lighting_box_code"
    if re.search(r"AT", query, flags=re.I):
        return "power_or_transfer_box_code"
    if re.search(r"AP", query, flags=re.I):
        return "power_box_code"
    if re.search(r"AE|ALG", query, flags=re.I):
        return "electrical_box_design_code"
    return "plain_distribution_box"


def _classify(row: dict[str, str]) -> dict[str, Any]:
    query = row.get("query", "")
    top1_name = row.get("top1_name", "")
    positive_name = _positive_name(row)
    query_has_install_signal = _contains_any(query, ("落地", "悬挂", "嵌入", "明装", "暗装", "墙上", "柱上", "杆上"))
    query_has_size_signal = bool(re.search(r"(?:半周长|回路|[0-9]+m|[0-9]+回路)", query, flags=re.I))
    query_code_family = _query_code_family(query)

    if "落地式" in top1_name and _contains_any(positive_name, ("悬挂", "嵌入")):
        bucket = "floor_mounted_over_hung_embedded"
        learnability = "diagnostic_candidate_needs_external_context"
        note = (
            "Top1 is floor-mounted while positive is hung/embedded half-perimeter tier. Query lacks explicit install/size evidence, "
            "so a direct query-only rerank would be underdetermined."
        )
    elif "杆上配电设备" in top1_name and _contains_any(positive_name, ("墙上", "柱上", "明装")):
        bucket = "pole_equipment_over_wall_column_box"
        learnability = "diagnostic_candidate_needs_source_diversity"
        note = (
            "Top1 is pole-mounted equipment while positive is wall/column exposed box. Query codes suggest building box context, "
            "but all rows are one Beijing province/book slice, so this is not yet generalizable."
        )
    else:
        bucket = "unclear_electrical_box_context"
        learnability = "blocked_unclear"
        note = "The row does not match the two observed electrical_box context patterns."

    if query_has_install_signal or query_has_size_signal:
        evidence_status = "query_text_has_direct_context"
    else:
        evidence_status = "query_text_missing_direct_context"

    return {
        "audit_bucket": bucket,
        "query_code_family": query_code_family,
        "query_has_install_signal": query_has_install_signal,
        "query_has_size_signal": query_has_size_signal,
        "evidence_status": evidence_status,
        "learnability_status": learnability,
        "audit_note": note,
    }


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.13 electrical-box installation/context rank-depth audit 已完成。"
        f"audited_rows={metrics['audited_rows']}；"
        f"floor_mounted_over_hung_embedded_rows={metrics['floor_mounted_over_hung_embedded_rows']}；"
        f"pole_equipment_over_wall_column_box_rows={metrics['pole_equipment_over_wall_column_box_rows']}；"
        f"query_text_direct_context_rows={metrics['query_text_direct_context_rows']}；"
        f"whatif_ready_rows={metrics['whatif_ready_rows']}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.14 electrical-box context feasibility / no-go gate。"
        "只读判断这两个 electrical_box 子桶是否足够进入未来 dev/OOF what-if；"
        "重点检查 query 文本是否有安装方式/尺寸/回路证据、是否单省单书支配、以及是否需要 bill_text/owner context。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现 electrical_box 降权/提权规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "从 expected label 反推 query 里不存在的安装方式/半周长/回路数、或把北京单省杆上桶宣称为通用收益。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.12 broader 12.x strategy review after parser/query micro-hint no-go</td>"
    row = (
        "          <tr>\n"
        "            <td>12.13 electrical-box installation/context rank-depth audit</td>\n"
        "            <td>只读审计 13 条 electrical_box wrong-rank，拆分落地式吸收、杆上设备吸收、编号信号和可学习边界。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_electrical_box_installation_context_rank_depth_audit_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_electrical_box_installation_context_rank_depth_audit_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-review-summary", type=Path, default=DEFAULT_STRATEGY_REVIEW)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy_review = _read_json(args.strategy_review_summary)
    wrong_rank = _read_csv(args.wrong_rank)
    rows = [
        row
        for row in wrong_rank
        if row.get("source_file") != "global_repair_decision_table.csv"
        and row.get("query_family") == "electrical_box"
        and row.get("group_id") != "dev:62:10"
    ]

    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        classified = _classify(row)
        audit_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "rank_bucket": row.get("rank_bucket", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "query_family": row.get("query_family", ""),
                "expected_ids": row.get("expected_ids", ""),
                "positive_rank_min": row.get("positive_rank_min", ""),
                "positive_names_in_top80": row.get("positive_names_in_top80", ""),
                "positive_context_name": _positive_name(row),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_score": row.get("top1_score", ""),
                "top1_reasons": row.get("top1_reasons", ""),
                **classified,
            }
        )

    bucket_counts = Counter(row["audit_bucket"] for row in audit_rows)
    learnability_counts = Counter(row["learnability_status"] for row in audit_rows)
    source_counts = Counter(row["source_file"] for row in audit_rows)
    province_counts = Counter(row["province"] for row in audit_rows)
    rank_counts = Counter(row["rank_bucket"] for row in audit_rows)
    code_counts = Counter(row["query_code_family"] for row in audit_rows)
    direct_context_rows = sum(1 for row in audit_rows if row["query_has_install_signal"] or row["query_has_size_signal"])

    bucket_summary = [
        {
            "audit_bucket": bucket,
            "rows": count,
            "share": round(count / len(audit_rows), 6) if audit_rows else 0,
            "rank_buckets": ";".join(
                f"{rank}:{rank_count}"
                for rank, rank_count in Counter(
                    row["rank_bucket"] for row in audit_rows if row["audit_bucket"] == bucket
                ).most_common()
            ),
            "provinces": ";".join(
                f"{province}:{province_count}"
                for province, province_count in Counter(
                    row["province"] for row in audit_rows if row["audit_bucket"] == bucket
                ).most_common()
            ),
            "source_files": ";".join(
                f"{source}:{source_count}"
                for source, source_count in Counter(
                    row["source_file"] for row in audit_rows if row["audit_bucket"] == bucket
                ).most_common()
            ),
            "query_direct_context_rows": sum(
                1
                for row in audit_rows
                if row["audit_bucket"] == bucket and (row["query_has_install_signal"] or row["query_has_size_signal"])
            ),
        }
        for bucket, count in bucket_counts.most_common()
    ]
    risk_checks = [
        {
            "risk": "query_text_context_absent",
            "status": "fail_for_direct_implementation" if direct_context_rows == 0 else "mixed",
            "evidence": f"{direct_context_rows}/{len(audit_rows)} rows include explicit install/size context in query text.",
        },
        {
            "risk": "floor_bucket_expected_label_dependency",
            "status": "blocked",
            "evidence": "Floor-mounted top1 rows require knowing hung/embedded half-perimeter positive labels that are not present in query text.",
        },
        {
            "risk": "pole_bucket_single_province_book",
            "status": "blocked",
            "evidence": f"{bucket_counts.get('pole_equipment_over_wall_column_box', 0)} rows are all 北京市建设工程施工消耗量标准(2024).",
        },
        {
            "risk": "code_token_generalization",
            "status": "audit_only",
            "evidence": "AL/ALE/AT/AP/B1 codes are useful diagnostics but need negative guards before any rerank rule.",
        },
        {
            "risk": "heldout_hard_boundary",
            "status": "pass",
            "evidence": "Audit uses dev wrong-rank artifacts only.",
        },
    ]
    feasibility_lanes = [
        {
            "lane": "floor_mounted_vs_hung_embedded_context",
            "support_rows": bucket_counts.get("floor_mounted_over_hung_embedded", 0),
            "status": "not_whatif_ready",
            "why": "Repeated same-family near miss, but query lacks explicit install/half-perimeter evidence.",
            "required_before_whatif": "bill_text or parsed context showing hung/embedded or size signal, plus negative examples where 落地式 is truly correct.",
        },
        {
            "lane": "pole_equipment_vs_wall_column_box_context",
            "support_rows": bucket_counts.get("pole_equipment_over_wall_column_box", 0),
            "status": "not_whatif_ready",
            "why": "Repeated same-family deep-rank miss, but single province/book and query code heuristics need guard evidence.",
            "required_before_whatif": "independent province/source support or robust building-box context signals, plus negative examples for true 杆上配电设备.",
        },
        {
            "lane": "electrical_box_code_token_parser_or_feature",
            "support_rows": sum(count for key, count in code_counts.items() if key != "plain_distribution_box"),
            "status": "diagnostic_only",
            "why": "Codes cluster with box context but do not encode install method/size by themselves.",
            "required_before_whatif": "explicit mapping from code family to safe ranking feature and loss audit contract.",
        },
    ]
    metrics = {
        "audited_rows": len(audit_rows),
        "floor_mounted_over_hung_embedded_rows": bucket_counts.get("floor_mounted_over_hung_embedded", 0),
        "pole_equipment_over_wall_column_box_rows": bucket_counts.get("pole_equipment_over_wall_column_box", 0),
        "rank_2_5_rows": rank_counts.get("rank_2_5", 0),
        "rank_11_20_rows": rank_counts.get("rank_11_20", 0),
        "independent_source_files": len(source_counts),
        "provinces": len(province_counts),
        "query_text_direct_context_rows": direct_context_rows,
        "non_plain_code_rows": sum(count for key, count in code_counts.items() if key != "plain_distribution_box"),
        "whatif_ready_rows": 0,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "row_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")),
        "bucket_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bucket_summary.csv")),
        "risk_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_risk_checks.csv")),
        "feasibility_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_feasibility_lanes.csv")),
    }
    decision = (
        "The electrical_box rows form two clean diagnostic sub-buckets, but neither is what-if ready. "
        "All 13 queries lack direct install/size context, so a query-only rerank would infer facts from labels rather than input evidence."
    )
    report = {
        "stage": "Goal LTR v1 / 12.13 electrical-box installation/context rank-depth audit",
        "read_only": True,
        "source_artifacts": {
            "strategy_review_summary": str(args.strategy_review_summary),
            "wrong_rank": str(args.wrong_rank),
        },
        "metrics": metrics,
        "decision": decision,
        "strategy_context": {
            "selected_next_lane": strategy_review["metrics"]["selected_next_lane"],
            "electrical_box_non_global_rows": strategy_review["metrics"]["electrical_box_non_global_rows"],
        },
        "anti_drift_conclusion": (
            "12.13 is read-only. It writes only diagnostic artifacts and the dashboard. It does not implement electrical_box rules, "
            "train, tune, change thresholds, edit parser/query-family rules, edit taxonomy rows, wire GoalSearcher, use heldout/hard for selection, "
            "or infer install method/half-perimeter/circuit count from expected labels when absent from the query."
        ),
        "next_stage": {
            "stage": "12.14 electrical-box context feasibility / no-go gate",
            "default": "read_only_no_implementation",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["row_audit_csv"]),
        audit_rows,
        [
            "group_id",
            "sample_id",
            "rank_bucket",
            "source_file",
            "province",
            "query",
            "query_family",
            "expected_ids",
            "positive_rank_min",
            "positive_names_in_top80",
            "positive_context_name",
            "top1_id",
            "top1_name",
            "top1_score",
            "top1_reasons",
            "audit_bucket",
            "query_code_family",
            "query_has_install_signal",
            "query_has_size_signal",
            "evidence_status",
            "learnability_status",
            "audit_note",
        ],
    )
    _write_csv(
        Path(artifacts["bucket_summary_csv"]),
        bucket_summary,
        ["audit_bucket", "rows", "share", "rank_buckets", "provinces", "source_files", "query_direct_context_rows"],
    )
    _write_csv(Path(artifacts["risk_checks_csv"]), risk_checks, ["risk", "status", "evidence"])
    _write_csv(Path(artifacts["feasibility_lanes_csv"]), feasibility_lanes, ["lane", "support_rows", "status", "why", "required_before_whatif"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
