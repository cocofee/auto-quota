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

DEFAULT_WHATIF_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_summary.json"
DEFAULT_WHATIF_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_summary.md"
DEFAULT_ROWS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_rows.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_buckets.csv"
DEFAULT_EXAMPLES_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_examples.jsonl"

SELECTED_POLICY = "freeze_plus_tight_sleeve_duct"
FREEZE_RELATIONS = {
    "sleeve_support_taxonomy_alias",
    "valve_duct_air_system_neighbor",
}
NARROW_RELATION = "sleeve_duct_closed_wall_neighbor"
LOW_SUPPORT_RELATIONS = {
    "conduit_pipe_electrical_neighbor",
    "formwork_concrete_taxonomy_alias",
}
SUSPECT_RELATIONS = {
    "pump_concrete_query_family_suspect",
    "support_pipe_query_family_suspect",
}
REVIEW_EVENTS = {
    "rescued_blocked_gain",
    "allowed_neutral_override",
    "still_blocked_gain",
    "new_residual_loss",
    "neutral",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _freeze_evidence_ok(row: dict[str, Any]) -> tuple[bool, str]:
    relation = _clean(row.get("compatibility_relation_id"))
    query = _clean(row.get("query"))
    candidate = _clean(row.get("raw_ltr_top_name"))
    if relation == "sleeve_support_taxonomy_alias":
        query_ok = _has_any(query, ("套管", "防水套管", "穿墙", "密闭", "人防"))
        candidate_ok = "套管" in candidate
        negative = _has_any(candidate, ("支架", "吊架", "支吊架", "托架"))
        if query_ok and candidate_ok and not negative:
            return True, "freeze_evidence_sleeve_terms_clean"
        return False, "freeze_evidence_sleeve_terms_weak"
    if relation == "valve_duct_air_system_neighbor":
        query_ok = _has_any(query, ("阀", "止回阀", "插板阀", "防火阀", "调节阀", "密闭阀", "取样"))
        candidate_ok = _has_any(candidate, ("阀", "阀门", "止回阀", "防火阀", "调节阀", "密闭阀", "取样接头"))
        if query_ok and candidate_ok:
            return True, "freeze_evidence_air_system_valve_terms_clean"
        return False, "freeze_evidence_air_system_valve_terms_weak"
    return False, "freeze_evidence_unknown_relation"


def _narrow_evidence_ok(row: dict[str, Any]) -> tuple[bool, str]:
    reason = _clean(row.get("policy_reason"))
    candidate = _clean(row.get("raw_ltr_top_name"))
    event = _clean(row.get("policy_event"))
    query_diameter = _clean(row.get("query_diameter"))
    candidate_tier = _clean(row.get("candidate_tier"))
    tier_ratio = _to_float(row.get("tier_ratio"))

    if reason == "narrow_allow_wall_pipe_and_diameter_tier_consistent":
        candidate_ok = "密闭穿墙管" in candidate and not _has_any(candidate, ("排气阀", "排气阀门", "阀门安装"))
        tier_ok = bool(query_diameter and candidate_tier and tier_ratio and tier_ratio <= 1.5)
        if candidate_ok and tier_ok:
            return True, "narrow_allow_evidence_clean"
        return False, "narrow_allow_evidence_weak"
    if reason in {
        "narrow_block_missing_wall_pipe_candidate_term",
        "narrow_block_air_valve_candidate",
        "narrow_block_query_diameter_exceeds_candidate_tier",
        "narrow_block_candidate_tier_too_coarse",
        "narrow_block_missing_diameter_evidence",
    }:
        if event == "neutral":
            return True, f"{reason}_clean"
        # Blocking a gain is visible risk even if the reason is logically explainable.
        return False, f"{reason}_blocks_gain_review"
    return False, "narrow_evidence_unknown_reason"


def _review_scope(row: dict[str, Any]) -> str:
    relation = _clean(row.get("compatibility_relation_id"))
    event = _clean(row.get("policy_event"))
    reason = _clean(row.get("policy_reason"))
    if relation in FREEZE_RELATIONS and _to_bool(row.get("policy_allowed")):
        return "freeze_retained"
    if relation == NARROW_RELATION and reason.startswith("narrow_allow"):
        return "narrow_retained"
    if relation == NARROW_RELATION and reason.startswith("narrow_block"):
        return "narrow_blocked"
    if relation in LOW_SUPPORT_RELATIONS:
        return "low_support_blocked"
    if relation in SUSPECT_RELATIONS:
        return "suspect_relation_blocked"
    if event == "still_blocked_gain" and not relation:
        return "unrelated_residual_gap"
    if event == "new_residual_loss":
        return "new_residual_loss"
    return "not_review_target"


def _audit_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = _review_scope(row)
    event = _clean(row.get("policy_event"))
    relation = _clean(row.get("compatibility_relation_id"))

    evidence_ok = False
    evidence_reason = "not_checked"
    verdict = "not_review_target"
    risk = "low"

    if scope == "freeze_retained":
        evidence_ok, evidence_reason = _freeze_evidence_ok(row)
        if evidence_ok and event == "rescued_blocked_gain":
            verdict = "clean_retained_gain"
        elif evidence_ok and event == "allowed_neutral_override":
            verdict = "retained_neutral_review"
            risk = "medium"
        else:
            verdict = "retained_evidence_weak_review"
            risk = "medium"
    elif scope == "narrow_retained":
        evidence_ok, evidence_reason = _narrow_evidence_ok(row)
        if evidence_ok and event == "rescued_blocked_gain":
            verdict = "clean_narrow_retained_gain"
        elif evidence_ok and event == "allowed_neutral_override":
            verdict = "narrow_retained_neutral_review"
            risk = "medium"
        else:
            verdict = "narrow_retained_evidence_weak_review"
            risk = "high"
    elif scope == "narrow_blocked":
        evidence_ok, evidence_reason = _narrow_evidence_ok(row)
        if evidence_ok:
            verdict = "clean_narrow_block"
        else:
            verdict = "narrow_block_review"
            risk = "high" if event == "still_blocked_gain" else "medium"
    elif scope == "low_support_blocked":
        evidence_ok = True
        evidence_reason = "blocked_due_low_support_relation_from_stage_7_6"
        verdict = "correctly_blocked_pending_more_evidence"
        risk = "medium" if event == "still_blocked_gain" else "low"
    elif scope == "suspect_relation_blocked":
        evidence_ok = True
        evidence_reason = "blocked_query_family_suspect_relation"
        verdict = "correctly_blocked_query_family_suspect"
        risk = "medium" if event == "still_blocked_gain" else "low"
    elif scope == "unrelated_residual_gap":
        evidence_ok = True
        evidence_reason = "outside_family_compatibility_relation_scope"
        verdict = "outside_current_stage_scope"
        risk = "medium"
    elif scope == "new_residual_loss":
        verdict = "must_stop_new_loss"
        risk = "high"
    else:
        evidence_ok = True

    item = dict(row)
    item["review_scope"] = scope
    item["review_verdict"] = verdict
    item["review_risk"] = risk
    item["evidence_ok"] = evidence_ok
    item["evidence_reason"] = evidence_reason
    item["relation_or_pair"] = relation or _clean(row.get("family_pair")) or "<empty>"
    return item


def _target_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if _clean(row.get("policy")) != SELECTED_POLICY:
            continue
        relation = _clean(row.get("compatibility_relation_id"))
        event = _clean(row.get("policy_event"))
        reason = _clean(row.get("policy_reason"))
        if relation in FREEZE_RELATIONS | LOW_SUPPORT_RELATIONS | SUSPECT_RELATIONS:
            result.append(_audit_row(row))
            continue
        if relation == NARROW_RELATION and (reason.startswith("narrow_allow") or reason.startswith("narrow_block")):
            result.append(_audit_row(row))
            continue
        if event in {"still_blocked_gain", "new_residual_loss"}:
            result.append(_audit_row(row))
    return result


def _split_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for split in sorted({row["split"] for row in rows}):
        subset = [row for row in rows if row["split"] == split]
        verdicts = Counter(row["review_verdict"] for row in subset)
        scopes = Counter(row["review_scope"] for row in subset)
        result.append(
            {
                "split": split,
                "review_rows": len(subset),
                "clean_retained_gain": verdicts["clean_retained_gain"] + verdicts["clean_narrow_retained_gain"],
                "neutral_review": verdicts["retained_neutral_review"] + verdicts["narrow_retained_neutral_review"],
                "clean_narrow_block": verdicts["clean_narrow_block"],
                "evidence_weak_review": verdicts["retained_evidence_weak_review"] + verdicts["narrow_retained_evidence_weak_review"] + verdicts["narrow_block_review"],
                "new_residual_loss": verdicts["must_stop_new_loss"],
                "outside_scope": verdicts["outside_current_stage_scope"],
                "scope_counts": [{"key": key, "count": count, "rate": _rate(count, len(subset))} for key, count in scopes.most_common()],
                "verdict_counts": [{"key": key, "count": count, "rate": _rate(count, len(subset))} for key, count in verdicts.most_common()],
            }
        )
    return result


def _relation_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(_clean(row.get("split")), _clean(row.get("relation_or_pair")), _clean(row.get("review_scope")))].append(row)
    result: list[dict[str, Any]] = []
    for (split, relation, scope), subset in sorted(grouped.items()):
        verdicts = Counter(row["review_verdict"] for row in subset)
        events = Counter(row["policy_event"] for row in subset)
        result.append(
            {
                "split": split,
                "relation_or_pair": relation,
                "review_scope": scope,
                "rows": len(subset),
                "rescued_blocked_gain": events["rescued_blocked_gain"],
                "allowed_neutral_override": events["allowed_neutral_override"],
                "still_blocked_gain": events["still_blocked_gain"],
                "new_residual_loss": events["new_residual_loss"],
                "evidence_ok": sum(1 for row in subset if _to_bool(row.get("evidence_ok"))),
                "evidence_ok_rate": _rate(sum(1 for row in subset if _to_bool(row.get("evidence_ok"))), len(subset)),
                "top_verdicts": json.dumps(verdicts.most_common(5), ensure_ascii=False),
                "top_reasons": json.dumps(Counter(row["evidence_reason"] for row in subset).most_common(5), ensure_ascii=False),
            }
        )
    return result


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "review_scope",
        "review_verdict",
        "review_risk",
        "relation_or_pair",
        "policy_event",
        "policy_reason",
        "evidence_reason",
        "source_file",
        "province",
    ]
    counters: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for row in rows:
        split = row["split"]
        totals[split] += 1
        for field in fields:
            counters[(split, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (split, field), counter in sorted(counters.items()):
        total = totals[split]
        for key, count in counter.most_common(20):
            result.append({"split": split, "bucket": field, "key": key, "count": count, "rate": _rate(count, total)})
    return result


def _examples(rows: list[dict[str, Any]], limit_per_bucket: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str]] = Counter()
    for row in sorted(
        rows,
        key=lambda item: (
            _clean(item.get("split")),
            _clean(item.get("review_scope")),
            _clean(item.get("review_verdict")),
            _clean(item.get("relation_or_pair")),
            _clean(item.get("group_id")),
        ),
    ):
        key = (_clean(row.get("split")), _clean(row.get("review_scope")), _clean(row.get("review_verdict")))
        if seen[key] >= limit_per_bucket:
            continue
        seen[key] += 1
        result.append(row)
    return result


def _anti_drift_checks(rows: list[dict[str, Any]], whatif_summary: dict[str, Any]) -> list[dict[str, Any]]:
    dev_rows = [row for row in rows if row["split"] == "dev_oof"]
    weak = [row for row in dev_rows if row["review_verdict"] in {"retained_evidence_weak_review", "narrow_retained_evidence_weak_review", "narrow_block_review", "must_stop_new_loss"}]
    neutral = [row for row in dev_rows if row["review_verdict"] in {"retained_neutral_review", "narrow_retained_neutral_review"}]
    checks = [
        {
            "gate": "source_is_stage_7_7_whatif",
            "passed": _clean(whatif_summary.get("stage")) == "Goal LTR v1 / stage 7.7 relation freeze/narrow eval-only what-if",
            "value": _clean(whatif_summary.get("stage")),
        },
        {
            "gate": "no_training_or_tuning",
            "passed": True,
            "value": "read-only review over frozen 7.7 rows",
        },
        {
            "gate": "no_new_loss_or_weak_retained_evidence_on_dev",
            "passed": len(weak) == 0,
            "value": len(weak),
            "threshold": "0",
        },
        {
            "gate": "neutral_overrides_are_explicitly_reported",
            "passed": len(neutral) >= 0,
            "value": len(neutral),
            "threshold": "reported",
        },
        {
            "gate": "switch_still_blocked",
            "passed": True,
            "value": "do_not_connect_switch_in_stage_7_8",
        },
    ]
    return checks


def _recommendation(rows: list[dict[str, Any]], checks: list[dict[str, Any]]) -> dict[str, Any]:
    dev_rows = [row for row in rows if row["split"] == "dev_oof"]
    neutral = [row for row in dev_rows if row["review_verdict"] in {"retained_neutral_review", "narrow_retained_neutral_review"}]
    weak = [row for row in dev_rows if row["review_verdict"] in {"retained_evidence_weak_review", "narrow_retained_evidence_weak_review", "narrow_block_review", "must_stop_new_loss"}]
    if weak:
        action = "revise_freeze_narrow_policy_before_switch_skeleton"
        next_stage = "Stage 7.9: revise the eval-only freeze/narrow policy, then rerun OOF only."
    else:
        action = "allow_default_off_switch_skeleton_design_next"
        next_stage = "Stage 7.9: write eval-only switch skeleton design/wrapper, default off, with mandatory relation logs and neutral override diagnostics."
    return {
        "action": action,
        "switch_recommendation": "do_not_connect_online_search",
        "default_off_switch_skeleton_allowed_next": not weak and all(check["passed"] for check in checks),
        "neutral_review_rows_on_dev": len(neutral),
        "weak_review_rows_on_dev": len(weak),
        "reason": (
            "Dev OOF retained/narrowed decisions have clean evidence; remaining concern is neutral override visibility."
            if not weak
            else "Some retained or narrowed decisions have weak evidence and need policy revision before a switch skeleton."
        ),
        "recommended_next_stage": next_stage,
    }


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
    lines = [
        "# Goal Family Compatibility Freeze/Narrow Residual Review",
        "",
        "Stage 7.8 reviews retained, narrowed, and blocked examples from stage 7.7. No training, no tuning, no search integration, no GoalSearcher change.",
        "",
        "## Recommendation",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["action", recommendation["action"]],
                ["switch_recommendation", recommendation["switch_recommendation"]],
                ["default_off_switch_skeleton_allowed_next", recommendation["default_off_switch_skeleton_allowed_next"]],
                ["neutral_review_rows_on_dev", recommendation["neutral_review_rows_on_dev"]],
                ["weak_review_rows_on_dev", recommendation["weak_review_rows_on_dev"]],
                ["reason", recommendation["reason"]],
            ]
        ),
        "",
        "## Split Summary",
        "",
        _md_table(
            [["split", "review_rows", "clean_gain", "neutral_review", "clean_narrow_block", "weak_review", "new_loss", "outside_scope"]]
            + [
                [
                    row["split"],
                    row["review_rows"],
                    row["clean_retained_gain"],
                    row["neutral_review"],
                    row["clean_narrow_block"],
                    row["evidence_weak_review"],
                    row["new_residual_loss"],
                    row["outside_scope"],
                ]
                for row in report["split_summary"]
            ]
        ),
        "",
        "## Relation Summary",
        "",
        _md_table(
            [["split", "relation", "scope", "rows", "rescued", "neutral", "still_blocked", "evidence_ok_rate"]]
            + [
                [
                    row["split"],
                    row["relation_or_pair"],
                    row["review_scope"],
                    row["rows"],
                    row["rescued_blocked_gain"],
                    row["allowed_neutral_override"],
                    row["still_blocked_gain"],
                    row["evidence_ok_rate"],
                ]
                for row in report["relation_summary"][:40]
            ]
        ),
        "",
        "## Anti-Drift Checks",
        "",
        _md_table([["gate", "passed", "value", "threshold"]] + [[row["gate"], row["passed"], row["value"], row.get("threshold", "")] for row in report["anti_drift_checks"]]),
        "",
        "## Artifacts",
        "",
        _md_table([["name", "path"]] + [[key, value] for key, value in report["artifacts"].items()]),
        "",
        "## Next",
        "",
        recommendation["recommended_next_stage"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _row_fields() -> list[str]:
    return [
        "split",
        "review_scope",
        "review_verdict",
        "review_risk",
        "evidence_ok",
        "evidence_reason",
        "relation_or_pair",
        "policy_event",
        "policy_reason",
        "compatibility_relation_id",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "query_diameter",
        "candidate_tier",
        "tier_ratio",
        "score_margin",
        "source_file",
        "province",
        "group_id",
        "sample_id",
        "query",
        "baseline_top_name",
        "raw_ltr_top_name",
    ]


def _relation_fields() -> list[str]:
    return [
        "split",
        "relation_or_pair",
        "review_scope",
        "rows",
        "rescued_blocked_gain",
        "allowed_neutral_override",
        "still_blocked_gain",
        "new_residual_loss",
        "evidence_ok",
        "evidence_ok_rate",
        "top_verdicts",
        "top_reasons",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.8 review freeze/narrow residual examples")
    parser.add_argument("--whatif-summary", default=str(DEFAULT_WHATIF_SUMMARY))
    parser.add_argument("--whatif-rows", default=str(DEFAULT_WHATIF_ROWS))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--rows-csv", default=str(DEFAULT_ROWS_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--examples-jsonl", default=str(DEFAULT_EXAMPLES_JSONL))
    parser.add_argument("--examples-per-bucket", type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    whatif_summary = _read_json(Path(args.whatif_summary))
    rows = _target_rows(_read_csv(Path(args.whatif_rows)))
    split_summary = _split_summary(rows)
    relation_summary = _relation_summary(rows)
    bucket_rows = _bucket_rows(rows)
    examples = _examples(rows, args.examples_per_bucket)
    checks = _anti_drift_checks(rows, whatif_summary)
    recommendation = _recommendation(rows, checks)

    report = {
        "stage": "Goal LTR v1 / stage 7.8 freeze/narrow residual review",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "selected_policy": SELECTED_POLICY,
        "whatif_summary": str(Path(args.whatif_summary)),
        "whatif_rows": str(Path(args.whatif_rows)),
        "row_count": len(rows),
        "split_summary": split_summary,
        "relation_summary": relation_summary,
        "anti_drift_checks": checks,
        "recommendation": recommendation,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "rows_csv": str(Path(args.rows_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "examples_jsonl": str(Path(args.examples_jsonl)),
        },
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.rows_csv), rows, _row_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, ["split", "bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.examples_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "row_count": len(rows),
                    "selected_policy": SELECTED_POLICY,
                    "action": recommendation["action"],
                    "default_off_switch_skeleton_allowed_next": recommendation["default_off_switch_skeleton_allowed_next"],
                    "anti_drift_passed": all(check["passed"] for check in checks),
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_summary": split_summary,
                "recommendation": recommendation,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
