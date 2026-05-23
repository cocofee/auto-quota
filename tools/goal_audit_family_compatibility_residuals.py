from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_WHATIF_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_summary.json"
DEFAULT_WHATIF_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_rows.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_summary.md"
DEFAULT_RESIDUAL_ROWS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_rows.csv"
DEFAULT_RELATION_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_relations.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_buckets.csv"
DEFAULT_EXAMPLES_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_examples.jsonl"

TARGET_EVENTS = {
    "still_blocked_gain",
    "allowed_neutral_override",
    "rescued_blocked_gain",
    "new_residual_loss",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _relation_key(row: dict[str, Any]) -> str:
    relation = _clean(row.get("compatibility_relation_id"))
    if relation:
        return relation
    return _clean(row.get("family_pair")) or "<empty>"


def _audit_class(row: dict[str, Any]) -> str:
    event = _clean(row.get("whatif_event"))
    if event == "rescued_blocked_gain":
        return "rescued_by_compatibility"
    if event == "new_residual_loss":
        return "new_loss_from_compatibility"
    if event == "allowed_neutral_override":
        return "allowed_but_no_top1_change"
    if event == "still_blocked_gain":
        if _to_bool(row.get("compatibility_matched")) and not _to_bool(row.get("compat_override_allowed")):
            return "blocked_by_explicit_non_allow_relation"
        if not _to_bool(row.get("compatibility_matched")):
            return "missing_relation_or_evidence"
        return "blocked_by_gate_after_compat_check"
    return "not_target"


def _risk_class(row: dict[str, Any]) -> str:
    event = _clean(row.get("whatif_event"))
    if event == "new_residual_loss":
        return "high"
    if event == "allowed_neutral_override":
        return "medium"
    if event == "still_blocked_gain":
        return "missed_gain"
    if event == "rescued_blocked_gain":
        return "benefit"
    return "low"


def _enrich_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        event = _clean(row.get("whatif_event"))
        if event not in TARGET_EVENTS:
            continue
        item = dict(row)
        item["relation_key"] = _relation_key(row)
        item["audit_class"] = _audit_class(row)
        item["risk_class"] = _risk_class(row)
        item["is_eval_split"] = _clean(row.get("split")) in {"heldout", "hard"}
        result.append(item)
    return result


def _relation_decision(row: dict[str, Any]) -> str:
    rescued = int(row["rescued_blocked_gain"])
    neutral = int(row["allowed_neutral_override"])
    new_loss = int(row["new_residual_loss"])
    split = row["split"]
    relation = row["relation_key"]

    if new_loss:
        return "narrow_or_block_before_switch"
    if split == "dev_oof":
        if rescued >= 3 and neutral <= rescued:
            return "freeze_candidate_relation"
        if rescued > 0 and neutral > rescued:
            return "narrow_evidence_before_freeze"
        if rescued > 0:
            return "review_low_support"
        if neutral > 0:
            return "narrow_or_drop_no_oof_gain"
        return "residual_only"
    if split in {"heldout", "hard"}:
        if rescued > 0 and neutral <= rescued:
            return "eval_supports_relation"
        if rescued > 0 and neutral > rescued:
            return "eval_mixed_relation"
        if neutral > 0:
            return "eval_neutral_only"
        return "eval_residual_only"
    return f"review_{relation}"


def _relation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            _clean(row.get("split")),
            _clean(row.get("relation_key")),
            _clean(row.get("family_pair")),
            _clean(row.get("compatibility_class")) or "not_compatible",
            _clean(row.get("compatibility_confidence")),
        )
        grouped[key].append(row)

    result: list[dict[str, Any]] = []
    for (split, relation_key, family_pair, compatibility_class, confidence), subset in sorted(grouped.items()):
        events = Counter(_clean(row.get("whatif_event")) for row in subset)
        rescued = events["rescued_blocked_gain"]
        neutral = events["allowed_neutral_override"]
        new_loss = events["new_residual_loss"]
        still_blocked = events["still_blocked_gain"]
        row = {
            "split": split,
            "relation_key": relation_key,
            "family_pair": family_pair,
            "compatibility_class": compatibility_class,
            "compatibility_confidence": confidence,
            "rescued_blocked_gain": rescued,
            "allowed_neutral_override": neutral,
            "new_residual_loss": new_loss,
            "still_blocked_gain": still_blocked,
            "total_target_rows": len(subset),
            "neutral_per_rescue": round(neutral / rescued, 6) if rescued else None,
            "new_loss_per_rescue": round(new_loss / rescued, 6) if rescued else None,
            "top_source_files": json.dumps(Counter(_clean(item.get("source_file")) for item in subset).most_common(3), ensure_ascii=False),
            "top_provinces": json.dumps(Counter(_clean(item.get("province")) for item in subset).most_common(3), ensure_ascii=False),
        }
        row["decision"] = _relation_decision(row)
        result.append(row)
    return result


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "audit_class",
        "risk_class",
        "relation_key",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "compatibility_relation_id",
        "compatibility_class",
        "source_file",
        "province",
    ]
    totals: Counter[tuple[str, str]] = Counter()
    counters: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        split = _clean(row.get("split"))
        event = _clean(row.get("whatif_event"))
        totals[(split, event)] += 1
        for field in fields:
            counters[(split, event, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (split, event, field), counter in sorted(counters.items()):
        total = totals[(split, event)]
        for key, count in counter.most_common(20):
            result.append(
                {
                    "split": split,
                    "whatif_event": event,
                    "bucket": field,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )
    return result


def _examples(rows: list[dict[str, Any]], limit_per_bucket: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str]] = Counter()
    order = {
        "new_residual_loss": 0,
        "allowed_neutral_override": 1,
        "still_blocked_gain": 2,
        "rescued_blocked_gain": 3,
    }
    for row in sorted(
        rows,
        key=lambda item: (
            _clean(item.get("split")),
            order.get(_clean(item.get("whatif_event")), 9),
            _clean(item.get("relation_key")),
            _clean(item.get("group_id")),
        ),
    ):
        key = (_clean(row.get("split")), _clean(row.get("whatif_event")), _clean(row.get("relation_key")))
        if seen[key] >= limit_per_bucket:
            continue
        seen[key] += 1
        result.append(row)
    return result


def _split_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for split in sorted({row["split"] for row in rows}):
        subset = [row for row in rows if row["split"] == split]
        events = Counter(_clean(row.get("whatif_event")) for row in subset)
        relations = Counter(_clean(row.get("relation_key")) for row in subset if _clean(row.get("compatibility_relation_id")))
        result.append(
            {
                "split": split,
                "target_rows": len(subset),
                "rescued_blocked_gain": events["rescued_blocked_gain"],
                "allowed_neutral_override": events["allowed_neutral_override"],
                "new_residual_loss": events["new_residual_loss"],
                "still_blocked_gain": events["still_blocked_gain"],
                "neutral_per_rescue": round(events["allowed_neutral_override"] / events["rescued_blocked_gain"], 6)
                if events["rescued_blocked_gain"]
                else None,
                "top_relations": [{"key": key, "count": count, "rate": _rate(count, len(subset))} for key, count in relations.most_common(8)],
            }
        )
    return result


def _overall_recommendation(relation_rows: list[dict[str, Any]], split_summary: list[dict[str, Any]]) -> dict[str, Any]:
    all_new_loss = sum(int(row["new_residual_loss"]) for row in relation_rows)
    oof_relations = [row for row in relation_rows if row["split"] == "dev_oof"]
    freeze_candidates = [row["relation_key"] for row in oof_relations if row["decision"] == "freeze_candidate_relation"]
    narrow_candidates = [row["relation_key"] for row in oof_relations if row["decision"] in {"narrow_evidence_before_freeze", "narrow_or_drop_no_oof_gain"}]
    low_support = [row["relation_key"] for row in oof_relations if row["decision"] == "review_low_support"]

    if all_new_loss:
        action = "narrow_before_any_switch"
        reason = "compatibility layer introduced residual loss in what-if rows."
    elif narrow_candidates:
        action = "defer_eval_only_switch_and_narrow_relations"
        reason = "some relations have more neutral overrides than rescued gains on OOF."
    elif freeze_candidates:
        action = "partial_freeze_candidates_keep_switch_off"
        reason = "relation-level gains are clean, but this is still an audit artifact rather than a wired switch."
    else:
        action = "keep_eval_only_and_collect_more_evidence"
        reason = "no relation has enough clean OOF support to freeze."

    return {
        "action": action,
        "reason": reason,
        "freeze_candidate_relations": sorted(set(freeze_candidates)),
        "narrow_candidate_relations": sorted(set(narrow_candidates)),
        "low_support_relations": sorted(set(low_support)),
        "switch_recommendation": "do_not_connect_eval_only_switch_yet",
        "notes": [
            "This audit does not tune thresholds or modify compatibility spec.",
            "Freeze/narrow labels are relation-level recommendations for the next design review, not production changes.",
        ],
        "split_summary": split_summary,
    }


def _anti_drift_checks(whatif_summary: dict[str, Any], relation_rows: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = [
        {
            "gate": "source_is_stage_7_5_whatif",
            "passed": _clean(whatif_summary.get("stage")) == "Goal LTR v1 / stage 7.5 family compatibility what-if",
            "value": _clean(whatif_summary.get("stage")),
        },
        {
            "gate": "does_not_create_new_rules_or_thresholds",
            "passed": True,
            "value": "read-only audit over frozen 7.5 rows",
        },
        {
            "gate": "reports_heldout_hard_distribution_without_selection",
            "passed": bool({row["split"] for row in rows} & {"heldout", "hard"}),
            "value": sorted({row["split"] for row in rows}),
        },
        {
            "gate": "explicit_switch_block",
            "passed": True,
            "value": "recommendation cannot enable eval-only switch in this stage",
        },
    ]
    any_relation_with_new_loss = any(int(row["new_residual_loss"]) > 0 for row in relation_rows)
    checks.append(
        {
            "gate": "relation_new_loss_visible",
            "passed": not any_relation_with_new_loss,
            "value": "new_loss_found" if any_relation_with_new_loss else "no_new_loss",
        }
    )
    return checks


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    recommendation = report["recommendation"]
    split_summary = report["split_summary"]
    relation_rows = report["relation_rows"][:30]
    lines = [
        "# Goal Family Compatibility Residual Audit",
        "",
        "Stage 7.6 audits stage 7.5 compatibility residuals by relation. No training, no tuning, no search integration, no GoalSearcher change.",
        "",
        "## Recommendation",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["action", recommendation["action"]],
                ["switch_recommendation", recommendation["switch_recommendation"]],
                ["freeze_candidate_relations", ", ".join(recommendation["freeze_candidate_relations"])],
                ["narrow_candidate_relations", ", ".join(recommendation["narrow_candidate_relations"])],
                ["low_support_relations", ", ".join(recommendation["low_support_relations"])],
                ["reason", recommendation["reason"]],
            ]
        ),
        "",
        "## Split Residuals",
        "",
        _md_table(
            [["split", "target_rows", "rescued", "neutral_override", "new_loss", "still_blocked", "neutral_per_rescue"]]
            + [
                [
                    row["split"],
                    row["target_rows"],
                    row["rescued_blocked_gain"],
                    row["allowed_neutral_override"],
                    row["new_residual_loss"],
                    row["still_blocked_gain"],
                    row["neutral_per_rescue"],
                ]
                for row in split_summary
            ]
        ),
        "",
        "## Relation Decisions",
        "",
        _md_table(
            [["split", "relation", "pair", "rescued", "neutral", "new_loss", "still_blocked", "decision"]]
            + [
                [
                    row["split"],
                    row["relation_key"],
                    row["family_pair"],
                    row["rescued_blocked_gain"],
                    row["allowed_neutral_override"],
                    row["new_residual_loss"],
                    row["still_blocked_gain"],
                    row["decision"],
                ]
                for row in relation_rows
            ]
        ),
        "",
        "## Anti-Drift Checks",
        "",
        _md_table([["gate", "passed", "value"]] + [[row["gate"], row["passed"], row["value"]] for row in report["anti_drift_checks"]]),
        "",
        "## Artifacts",
        "",
        _md_table([["name", "path"]] + [[key, value] for key, value in report["artifacts"].items()]),
        "",
        "## Next",
        "",
        report["recommended_next_stage"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _residual_fields() -> list[str]:
    return [
        "split",
        "whatif_event",
        "audit_class",
        "risk_class",
        "relation_key",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "compatibility_relation_id",
        "compatibility_class",
        "compatibility_confidence",
        "compat_override_allowed",
        "compatibility_matched",
        "score_margin",
        "no_family_conflict",
        "no_book_conflict",
        "no_param_conflict",
        "query_family_conflict",
        "source_file",
        "province",
        "group_id",
        "sample_id",
        "query",
        "baseline_top_name",
        "raw_ltr_top_name",
        "compatibility_reason",
    ]


def _relation_fields() -> list[str]:
    return [
        "split",
        "relation_key",
        "family_pair",
        "compatibility_class",
        "compatibility_confidence",
        "rescued_blocked_gain",
        "allowed_neutral_override",
        "new_residual_loss",
        "still_blocked_gain",
        "total_target_rows",
        "neutral_per_rescue",
        "new_loss_per_rescue",
        "decision",
        "top_source_files",
        "top_provinces",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.6 audit family compatibility residuals by relation")
    parser.add_argument("--whatif-summary", default=str(DEFAULT_WHATIF_SUMMARY))
    parser.add_argument("--whatif-rows", default=str(DEFAULT_WHATIF_ROWS))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--residual-rows-csv", default=str(DEFAULT_RESIDUAL_ROWS_CSV))
    parser.add_argument("--relation-csv", default=str(DEFAULT_RELATION_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--examples-jsonl", default=str(DEFAULT_EXAMPLES_JSONL))
    parser.add_argument("--examples-per-bucket", type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    whatif_summary = _read_json(Path(args.whatif_summary))
    rows = _enrich_rows(_read_csv(Path(args.whatif_rows)))
    relation_rows = _relation_rows(rows)
    bucket_rows = _bucket_rows(rows)
    examples = _examples(rows, args.examples_per_bucket)
    split_summary = _split_summary(rows)
    recommendation = _overall_recommendation(relation_rows, split_summary)
    anti_drift_checks = _anti_drift_checks(whatif_summary, relation_rows, rows)

    report = {
        "stage": "Goal LTR v1 / stage 7.6 family compatibility residual audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "input_summary": str(Path(args.whatif_summary)),
        "input_rows": str(Path(args.whatif_rows)),
        "row_count": len(rows),
        "split_summary": split_summary,
        "relation_rows": relation_rows,
        "recommendation": recommendation,
        "anti_drift_checks": anti_drift_checks,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 7.7: do not wire switch yet; first review relation-level decisions and, if accepted, freeze only high-support relations while narrowing noisy ones in another eval-only what-if.",
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "residual_rows_csv": str(Path(args.residual_rows_csv)),
            "relation_csv": str(Path(args.relation_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "examples_jsonl": str(Path(args.examples_jsonl)),
        },
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.residual_rows_csv), rows, _residual_fields())
    _write_csv(Path(args.relation_csv), relation_rows, _relation_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, ["split", "whatif_event", "bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.examples_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "row_count": len(rows),
                    "action": recommendation["action"],
                    "switch_recommendation": recommendation["switch_recommendation"],
                    "freeze_candidate_relations": recommendation["freeze_candidate_relations"],
                    "narrow_candidate_relations": recommendation["narrow_candidate_relations"],
                    "low_support_relations": recommendation["low_support_relations"],
                    "anti_drift_passed": all(row["passed"] for row in anti_drift_checks),
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_summary": split_summary,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
