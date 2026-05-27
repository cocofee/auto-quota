from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_STRATEGY = AGENT_STATE / "goal_15x_candidate_generation_recall_expansion_strategy_definition_summary.json"
DEFAULT_MATRIX_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_candidate_generation_evidence_inventory"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rank_bucket(rank: int) -> str:
    if rank <= 0:
        return "missing_top80"
    if rank == 1:
        return "rank_1"
    if rank <= 5:
        return "rank_2_5"
    if rank <= 10:
        return "rank_6_10"
    if rank <= 20:
        return "rank_11_20"
    if rank <= 40:
        return "rank_21_40"
    return "rank_41_80"


def _reason_has_conflict(top: list[dict[str, Any]]) -> bool:
    for item in top[:5]:
        reasons = " ".join(str(part) for part in item.get("reasons", []))
        if "conflict" in reasons.lower():
            return True
    return False


def _presence_summary(group_rows: list[dict[str, Any]], recall_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    total = len(group_rows) + len(recall_rows)
    top1 = sum(1 for row in group_rows if _int(row.get("positive_rank")) == 1)
    top5 = sum(1 for row in group_rows if 1 <= _int(row.get("positive_rank")) <= 5)
    top20 = sum(1 for row in group_rows if 1 <= _int(row.get("positive_rank")) <= 20)
    top80 = len(group_rows)
    buckets = Counter(_rank_bucket(_int(row.get("positive_rank"))) for row in group_rows)
    buckets["missing_top80"] += len(recall_rows)
    bucket_rows = [
        {
            "bucket": bucket,
            "groups": count,
            "share": round(count / total, 6) if total else 0.0,
            "candidate_generation_relevance": "high" if bucket in {"missing_top80", "rank_21_40", "rank_41_80"} else "medium" if bucket in {"rank_11_20"} else "low",
        }
        for bucket, count in sorted(buckets.items())
    ]
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "top1": 0, "top5": 0, "top20": 0, "top80": 0, "missing": 0})
    for row in group_rows:
        fam = _clean(row.get("query_family"))
        rank = _int(row.get("positive_rank"))
        item = by_family[fam]
        item["groups"] += 1
        item["top80"] += 1
        item["top20"] += int(rank <= 20)
        item["top5"] += int(rank <= 5)
        item["top1"] += int(rank == 1)
    for row in recall_rows:
        fam = _clean(row.get("query_family"))
        item = by_family[fam]
        item["groups"] += 1
        item["missing"] += 1
    family_rows = []
    for fam, values in by_family.items():
        groups = values["groups"]
        family_rows.append(
            {
                "query_family": fam,
                **values,
                "top80_rate": round(values["top80"] / groups, 6) if groups else 0.0,
                "top20_rate": round(values["top20"] / groups, 6) if groups else 0.0,
                "missing_rate": round(values["missing"] / groups, 6) if groups else 0.0,
            }
        )
    family_rows.sort(key=lambda row: (row["missing"], row["groups"]), reverse=True)
    summary = {
        "total_groups_in_inventory": total,
        "top1_present_groups": top1,
        "top5_present_groups": top5,
        "top20_present_groups": top20,
        "top80_present_groups": top80,
        "top80_missing_groups": len(recall_rows),
        "top1_rate": round(top1 / total, 6) if total else 0.0,
        "top5_rate": round(top5 / total, 6) if total else 0.0,
        "top20_rate": round(top20 / total, 6) if total else 0.0,
        "top80_rate": round(top80 / total, 6) if total else 0.0,
        "candidate_generation_target_groups": len(recall_rows) + buckets.get("rank_21_40", 0) + buckets.get("rank_41_80", 0),
    }
    return summary, bucket_rows, family_rows


def _recall_gap_summary(recall_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_reason = Counter(_clean(row.get("recall_gap_reason")) for row in recall_rows)
    by_family = Counter(_clean(row.get("query_family")) for row in recall_rows)
    by_source = Counter(_clean(row.get("source_family")) for row in recall_rows)
    reason_rows = [{"reason": key, "groups": count, "share": round(count / len(recall_rows), 6) if recall_rows else 0.0} for key, count in by_reason.most_common()]
    family_rows = [{"query_family": key, "missing_groups": count, "share": round(count / len(recall_rows), 6) if recall_rows else 0.0} for key, count in by_family.most_common(30)]
    source_rows = [{"source_family": key, "missing_groups": count, "share": round(count / len(recall_rows), 6) if recall_rows else 0.0} for key, count in by_source.most_common()]
    return reason_rows, family_rows, source_rows


def _false_candidate_risk_rows(recall_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"missing_groups": 0, "top5_conflict_groups": 0, "top5_zero_score_groups": 0, "top5_family_supported_groups": 0})
    for row in recall_rows:
        fam = _clean(row.get("query_family"))
        top = row.get("top") or []
        item = by_family[fam]
        item["missing_groups"] += 1
        item["top5_conflict_groups"] += int(_reason_has_conflict(top))
        item["top5_zero_score_groups"] += int(any(float(hit.get("score") or 0.0) == 0.0 for hit in top[:5]))
        item["top5_family_supported_groups"] += int(any("family:" in " ".join(str(part) for part in hit.get("reasons", [])) for hit in top[:5]))
    for fam, values in by_family.items():
        groups = values["missing_groups"]
        conflict_rate = values["top5_conflict_groups"] / groups if groups else 0.0
        zero_score_rate = values["top5_zero_score_groups"] / groups if groups else 0.0
        if conflict_rate > 0.4:
            risk_level = "high"
        elif zero_score_rate > 0:
            risk_level = "medium"
        else:
            risk_level = "low"
        rows.append(
            {
                "query_family": fam,
                **values,
                "conflict_rate": round(conflict_rate, 6),
                "zero_score_rate": round(zero_score_rate, 6),
                "risk_level": risk_level,
            }
        )
    risk_order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda row: (risk_order.get(row["risk_level"], 9), -row["missing_groups"]))
    return rows


def _lane_readiness(strategy: dict[str, Any], presence: dict[str, Any], recall_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lanes = strategy.get("strategy_lanes", [])
    missing = presence["top80_missing_groups"]
    deep = presence["candidate_generation_target_groups"] - missing
    rows = []
    for lane in lanes:
        lane_id = lane["lane_id"]
        if lane_id.startswith("15A"):
            status = "ready_for_plan"
            evidence = f"OSS matrix has query/expected_ids/source/province for {presence['total_groups_in_inventory']} groups; {missing} top80-missing groups can seed alias evaluation."
            priority = 1
        elif lane_id.startswith("15B"):
            status = "ready_for_plan"
            evidence = f"Feature rows include family/action/material/numeric fields; {missing + deep} missing-or-deep groups are candidate-pool targets."
            priority = 2
        elif lane_id.startswith("15C"):
            status = "hold_for_crosswalk_inventory"
            evidence = "Need province/book concept mapping manifest before cross-province backfill can be evaluated safely."
            priority = 4
        else:
            status = "ready_as_measurement_harness"
            evidence = "Presence buckets are measurable now; shadow A/B can report top80/top20/top5 movement without release."
            priority = 3
        rows.append(
            {
                "lane_id": lane_id,
                "readiness": status,
                "priority": priority,
                "evidence": evidence,
                "next_artifact_needed": "15.2 plan definition" if status.startswith("ready") else "crosswalk/province-book concept inventory",
            }
        )
    rows.sort(key=lambda row: row["priority"])
    return rows


def _artifact_inventory(matrix_dir: Path) -> list[dict[str, Any]]:
    items = [
        ("group_meta", matrix_dir / "ltr_group_dev.jsonl", "positive_rank, expected_ids, query_family, top1_family, source/province"),
        ("feature_rows", matrix_dir / "ltr_features_dev.jsonl", "top80 candidates with labels and false-candidate features"),
        ("recall_gap", matrix_dir / "recall_gap_dev.raw.jsonl", "top80 missing groups with top candidates and reasons"),
        ("anchor_excluded", matrix_dir / "anchor_excluded_dev.raw.jsonl", "anchors rejected because expected id not in region db"),
        ("source_balance", matrix_dir / "source_balance_checks.csv", "source/fold balance gates"),
        ("feature_contract", matrix_dir / "feature_contract_report.csv", "feature leakage/contract metadata"),
    ]
    rows = []
    for name, path, fields in items:
        rows.append(
            {
                "artifact": name,
                "path": _safe_rel(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "15x_use": fields,
            }
        )
    return rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    p = report["presence_summary"]
    lane_rows = [["lane", "readiness", "priority", "next"]]
    for row in report["lane_readiness"]:
        lane_rows.append([row["lane_id"], row["readiness"], row["priority"], row["next_artifact_needed"]])
    lines = [
        "# 15.1 Candidate-Pool / OSS Recall Expansion Evidence Inventory",
        "",
        "Read-only inventory of existing OSS/dev/OOF artifacts. No candidate expansion, training, heldout/hard selection, or GoalSearcher edit was performed.",
        "",
        "## Presence Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["total_groups", p["total_groups_in_inventory"]],
                ["top1_rate", p["top1_rate"]],
                ["top5_rate", p["top5_rate"]],
                ["top20_rate", p["top20_rate"]],
                ["top80_rate", p["top80_rate"]],
                ["top80_missing_groups", p["top80_missing_groups"]],
                ["candidate_generation_target_groups", p["candidate_generation_target_groups"]],
            ]
        ),
        "",
        "## Lane Readiness",
        "",
        _md_table(lane_rows),
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Next stage: `{report['next_stage']['recommended']}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    p = report["presence_summary"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.1 candidate-pool/OSS recall expansion evidence inventory completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "Existing OSS/dev/OOF artifacts are sufficient to measure recall-expansion movement before implementation.",
        "",
        "## Key Metrics",
        "",
        f"- total inventory groups: `{p['total_groups_in_inventory']}`",
        f"- top80 present rate: `{p['top80_rate']}`",
        f"- top20 present rate: `{p['top20_rate']}`",
        f"- top5 present rate: `{p['top5_rate']}`",
        f"- top80 missing groups: `{p['top80_missing_groups']}`",
        f"- candidate-generation target groups: `{p['candidate_generation_target_groups']}`",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not implement candidate injection yet.",
        "- Do not use heldout/hard for design or selection.",
        "- Do not edit GoalSearcher until a future explicit implementation gate.",
        "- Continue treating OSS as high-value human evidence with false-candidate audits.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    p = report["presence_summary"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.1 candidate-pool/OSS recall expansion evidence inventory 已完成。\n"
        f"结论：{report['decision']}。top80_rate={p['top80_rate']}，top20_rate={p['top20_rate']}，top80_missing={p['top80_missing_groups']}，candidate_generation_target_groups={p['candidate_generation_target_groups']}。\n"
        "下一步建议：15.2 OSS alias/index recall expansion plan definition。只读定义 15A/15B 的 exact expansion source、merge policy、dedup key、false-candidate guards 和 required artifacts。\n"
        "禁止：实现 candidate injection、用 heldout/hard 设计、改 GoalSearcher、上线、改阈值。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.1 candidate-pool/OSS recall expansion evidence inventory" not in text:
        row = f"""          <tr>
            <td>15.1 candidate-pool/OSS recall expansion evidence inventory</td>
            <td>Read-only inventory of OSS/dev/OOF artifacts, presence buckets, recall gaps, false-candidate risks, and lane readiness.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.1 candidate-pool/OSS recall expansion evidence inventory")
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    strategy = _read_json(args.strategy_summary)
    group_rows = _read_jsonl(args.matrix_dir / "ltr_group_dev.jsonl")
    recall_rows = _read_jsonl(args.matrix_dir / "recall_gap_dev.raw.jsonl")
    anchor_excluded_rows = _read_jsonl(args.matrix_dir / "anchor_excluded_dev.raw.jsonl")
    presence, rank_bucket_rows, family_presence_rows = _presence_summary(group_rows, recall_rows)
    recall_reason_rows, recall_family_rows, recall_source_rows = _recall_gap_summary(recall_rows)
    false_risk_rows = _false_candidate_risk_rows(recall_rows)
    lane_rows = _lane_readiness(strategy, presence, recall_rows)
    artifacts_inventory = _artifact_inventory(args.matrix_dir)

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "artifact_inventory_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_artifact_inventory.csv")),
        "presence_rank_buckets_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_presence_rank_buckets.csv")),
        "family_presence_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_family_presence.csv")),
        "recall_gap_reasons_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_recall_gap_reasons.csv")),
        "recall_gap_family_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_recall_gap_family.csv")),
        "recall_gap_source_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_recall_gap_source.csv")),
        "false_candidate_risk_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_false_candidate_risk.csv")),
        "lane_readiness_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_readiness.csv")),
        "anchor_excluded_sample_jsonl": str(args.output_prefix.with_name(args.output_prefix.name + "_anchor_excluded_sample.jsonl")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    decision = "evidence_ready_for_15_2_alias_index_plan_definition"
    report = {
        "stage": "15.1 candidate-pool/OSS recall expansion evidence inventory",
        "read_only_inventory": True,
        "decision": decision,
        "presence_summary": presence,
        "anchor_excluded_count": len(anchor_excluded_rows),
        "artifact_inventory": artifacts_inventory,
        "rank_bucket_rows": rank_bucket_rows,
        "top_missing_families": recall_family_rows[:12],
        "top_false_candidate_risks": false_risk_rows[:12],
        "lane_readiness": lane_rows,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.1 is read-only. It did not implement candidate injection, train, read heldout/hard for selection, "
            "release rerankers, edit GoalSearcher, change thresholds, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.2 OSS alias/index recall expansion plan definition",
            "description": "Read-only plan for 15A/15B: exact OSS expansion source, merge policy, dedup key, safety guards, presence-delta metrics, false-candidate audits, and explicit execution go/no-go.",
            "default": "do_not_implement",
        },
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["artifact_inventory_csv"]), artifacts_inventory, ["artifact", "path", "exists", "size_bytes", "15x_use"])
    _write_csv(Path(artifacts["presence_rank_buckets_csv"]), rank_bucket_rows, ["bucket", "groups", "share", "candidate_generation_relevance"])
    _write_csv(Path(artifacts["family_presence_csv"]), family_presence_rows, ["query_family", "groups", "top1", "top5", "top20", "top80", "missing", "top80_rate", "top20_rate", "missing_rate"])
    _write_csv(Path(artifacts["recall_gap_reasons_csv"]), recall_reason_rows, ["reason", "groups", "share"])
    _write_csv(Path(artifacts["recall_gap_family_csv"]), recall_family_rows, ["query_family", "missing_groups", "share"])
    _write_csv(Path(artifacts["recall_gap_source_csv"]), recall_source_rows, ["source_family", "missing_groups", "share"])
    _write_csv(Path(artifacts["false_candidate_risk_csv"]), false_risk_rows, ["query_family", "missing_groups", "top5_conflict_groups", "top5_zero_score_groups", "top5_family_supported_groups", "conflict_rate", "zero_score_rate", "risk_level"])
    _write_csv(Path(artifacts["lane_readiness_csv"]), lane_rows, ["lane_id", "readiness", "priority", "evidence", "next_artifact_needed"])
    _write_jsonl(Path(artifacts["anchor_excluded_sample_jsonl"]), anchor_excluded_rows[:50])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next": report["next_stage"]["recommended"], "presence": presence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
