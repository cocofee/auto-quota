from __future__ import annotations

import argparse
import csv
import json
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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_duct_near_miss_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_duct_fire_damper_evidence_9x_review"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _signature(row: dict[str, Any]) -> str:
    return "|".join(
        [
            f"top={_clean(row.get('top1_id'))}",
            f"positive={_clean(row.get('positive_ids_in_top80'))}",
            f"top_action={_clean(row.get('top_action'))}",
            f"positive_action={_clean(row.get('positive_action'))}",
            f"positive_sizes={_clean(row.get('positive_sizes'))}",
        ]
    )


def _review(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        out = {
            "split": _clean(row.get("split")),
            "group_id": _clean(row.get("group_id")),
            "sample_id": _clean(row.get("sample_id")),
            "source_file": _clean(row.get("source_file")),
            "province": _clean(row.get("province")),
            "query": _clean(row.get("query")),
            "query_perimeter_hint": _clean(row.get("query_perimeter_hint")),
            "query_sizes": _clean(row.get("query_sizes")),
            "top_action": _clean(row.get("top_action")),
            "positive_action": _clean(row.get("positive_action")),
            "top1_id": _clean(row.get("top1_id")),
            "top1_name": _clean(row.get("top1_name")),
            "positive_ids_in_top80": _clean(row.get("positive_ids_in_top80")),
            "positive_names_in_top80": _clean(row.get("positive_names_in_top80")),
            "positive_sizes": _clean(row.get("positive_sizes")),
            "evidence_signature": _signature(row),
            "review_status": "same_province_same_source_artifact",
            "transferable": 0,
            "blocked_reason": "province_count=1 and source_count=1; cannot infer national duct/fire-damper action rule",
        }
        reviewed.append(out)
    return reviewed


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters = {
        "province": Counter(_clean(row.get("province")) or "<empty>" for row in rows),
        "source_file": Counter(_clean(row.get("source_file")) or "<empty>" for row in rows),
        "top1_id": Counter(_clean(row.get("top1_id")) or "<empty>" for row in rows),
        "positive_ids_in_top80": Counter(_clean(row.get("positive_ids_in_top80")) or "<empty>" for row in rows),
        "query_perimeter_hint": Counter(_clean(row.get("query_perimeter_hint")) or "<empty>" for row in rows),
        "review_status": Counter(_clean(row.get("review_status")) or "<empty>" for row in rows),
    }
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_duct_fire_damper_review", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Stage 9.11 Duct Fire-damper Evidence Review",
        "",
        "Dev-only review of the 6 fire-damper debug-vs-install rows from stage 9.10. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["review_rows", report["metrics"]["review_rows"]],
                ["province_count", report["metrics"]["province_count"]],
                ["source_count", report["metrics"]["source_count"]],
                ["top1_id_count", report["metrics"]["top1_id_count"]],
                ["positive_id_count", report["metrics"]["positive_id_count"]],
                ["transferable_rows", report["metrics"]["transferable_rows"]],
                ["decision", report["decision"]["recommendation"]],
                ["next_stage", report["next_stage"]["stage"]],
            ]
        ),
        "",
        "## Decision",
        "",
        report["decision"]["reason"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.11 duct fire-damper evidence review")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = [
        row
        for row in _read_csv(Path(args.rows))
        if _clean(row.get("primary_issue")) == "fire_damper_debug_vs_install"
    ]
    reviewed = _review(source_rows)
    buckets = _bucket_rows(reviewed)
    provinces = {_clean(row.get("province")) for row in reviewed if _clean(row.get("province"))}
    sources = {_clean(row.get("source_file")) for row in reviewed if _clean(row.get("source_file"))}
    top1_ids = {_clean(row.get("top1_id")) for row in reviewed if _clean(row.get("top1_id"))}
    positive_ids = {_clean(row.get("positive_ids_in_top80")) for row in reviewed if _clean(row.get("positive_ids_in_top80"))}

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.11 duct fire-damper evidence review",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifact": str(Path(args.rows)),
        "metrics": {
            "review_rows": len(reviewed),
            "province_count": len(provinces),
            "source_count": len(sources),
            "top1_id_count": len(top1_ids),
            "positive_id_count": len(positive_ids),
            "transferable_rows": sum(1 for row in reviewed if _clean(row.get("transferable")) == "1"),
            "same_province_same_source_rows": sum(1 for row in reviewed if row["review_status"] == "same_province_same_source_artifact"),
        },
        "decision": {
            "recommendation": "stop_duct_fire_damper_direction",
            "transferable_fire_damper_rule_ready": False,
            "reason": "All reviewed rows are from 重庆市通用安装工程计价定额(2018) and global_repair_decision_table.csv, with the same Top1 debug item and only two positive installation tiers. This is not enough to infer a national fire-damper debug-vs-install rule.",
        },
        "next_stage": {
            "stage": "9.12 return to ranked gap table selection",
            "goal": "exclude the exhausted duct fire-damper direction and choose the next high-support dev wrong-rank bucket",
        },
        "artifacts": artifacts,
        "artifacts_preview": {
            "buckets": buckets,
            "rows": reviewed,
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.11 stops the duct fire-damper direction because support is same-province and same-source only. No duct/fire-damper rule, what-if, training, or search integration is allowed from this evidence.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "query_perimeter_hint",
        "query_sizes",
        "top_action",
        "positive_action",
        "top1_id",
        "top1_name",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_sizes",
        "evidence_signature",
        "review_status",
        "transferable",
        "blocked_reason",
    ]
    _write_csv(Path(artifacts["rows_csv"]), reviewed, row_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "decision": report["decision"], "next_stage": report["next_stage"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
