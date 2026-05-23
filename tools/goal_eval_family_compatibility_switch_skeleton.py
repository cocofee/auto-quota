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

DEFAULT_POLICY_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_summary.json"
DEFAULT_POLICY_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_REVIEW_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_residual_review_summary.json"
DEFAULT_SWITCH_CONFIG = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_switch_config_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state"

SELECTED_POLICY = "freeze_plus_tight_sleeve_duct"
FREEZE_RELATIONS = {
    "sleeve_support_taxonomy_alias",
    "valve_duct_air_system_neighbor",
}
NARROW_RELATIONS = {"sleeve_duct_closed_wall_neighbor"}
BLOCKED_REVIEW_RELATIONS = {
    "conduit_pipe_electrical_neighbor",
    "formwork_concrete_taxonomy_alias",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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


def _default_switch_config() -> dict[str, Any]:
    return {
        "stage": "Goal LTR v1 / stage 7.9 family compatibility eval-only switch skeleton",
        "version": "v1",
        "goal_family_compatibility_switch_enabled": False,
        "default_behavior": "fallback_to_existing_safety_gate",
        "selected_policy": SELECTED_POLICY,
        "frozen_relations": sorted(FREEZE_RELATIONS),
        "narrowed_relations": sorted(NARROW_RELATIONS),
        "blocked_review_relations": sorted(BLOCKED_REVIEW_RELATIONS),
        "required_logs": [
            "switch_enabled",
            "switch_active",
            "fallback_reason",
            "policy",
            "policy_event",
            "policy_reason",
            "compatibility_relation_id",
            "effective_allowed",
            "effective_event",
            "neutral_override_diagnostic",
        ],
        "fallback_policy": {
            "disabled": "always keep safety-gate top1",
            "missing_inputs": "fallback_to_existing_safety_gate",
            "policy_not_allowed": "fallback_to_existing_safety_gate",
        },
        "forbidden_actions": [
            "do_not_modify_GoalSearcher",
            "do_not_change_default_search_chain",
            "do_not_train_or_tune",
            "do_not_use_heldout_for_selection",
        ],
    }


def _load_or_create_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return _read_json(path)
    config = _default_switch_config()
    _write_json(path, config)
    return config


def _selected_policy_metrics(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for row in summary.get("policy_metrics") or []:
        if _clean(row.get("policy")) != SELECTED_POLICY:
            continue
        split = _clean(row.get("split"))
        if split:
            metrics[split] = row
    return metrics


def _selected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _clean(row.get("policy")) == SELECTED_POLICY]


def _mode_label(active: bool) -> str:
    return "explicit_enabled_eval_only" if active else "default_off"


def _effective_event(row: dict[str, Any], active: bool) -> str:
    if not active:
        if _to_bool(row.get("gate_allowed")):
            selected = _clean(row.get("whatif_event"))
            return selected if selected else "baseline_safety_gate"
        if _to_bool(row.get("baseline_hit1")) and not _to_bool(row.get("raw_ltr_hit1")):
            return "saved_loss_retained"
        if not _to_bool(row.get("baseline_hit1")) and _to_bool(row.get("raw_ltr_hit1")):
            return "blocked_gain_retained_by_default_off"
        return "baseline_safety_gate"
    return _clean(row.get("policy_event")) or "baseline_safety_gate"


def _fallback_reason(row: dict[str, Any], active: bool) -> str:
    if not active:
        return "switch_disabled_default_off"
    if _to_bool(row.get("policy_allowed")):
        return ""
    reason = _clean(row.get("policy_reason"))
    if reason:
        return reason
    return "policy_not_allowed_fallback_to_safety_gate"


def _neutral_diag(row: dict[str, Any], active: bool) -> str:
    policy_event = _clean(row.get("policy_event"))
    if policy_event == "allowed_neutral_override":
        return "neutral_override_active" if active else "would_be_neutral_override_if_enabled"
    if policy_event == "rescued_blocked_gain":
        return "rescued_gain_active" if active else "would_rescue_if_enabled"
    if policy_event == "new_residual_loss":
        return "new_loss_active" if active else "would_be_new_loss_if_enabled"
    return ""


def _relation_log_rows(rows: list[dict[str, Any]], *, active: bool, include_all_rows: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    interesting_events = {
        "rescued_blocked_gain",
        "allowed_neutral_override",
        "still_blocked_gain",
        "new_residual_loss",
        "saved_loss_retained",
    }
    for row in rows:
        policy_event = _clean(row.get("policy_event"))
        relation = _clean(row.get("compatibility_relation_id"))
        if not include_all_rows and not relation and policy_event not in interesting_events:
            continue
        effective_allowed = bool(active and _to_bool(row.get("policy_allowed")))
        effective_event = _effective_event(row, active)
        result.append(
            {
                "split": _clean(row.get("split")),
                "switch_mode": _mode_label(active),
                "switch_enabled": active,
                "switch_enabled_requested": active,
                "switch_active": active,
                "fallback_reason": _fallback_reason(row, active),
                "policy": SELECTED_POLICY,
                "policy_event": policy_event,
                "policy_reason": _clean(row.get("policy_reason")),
                "policy_allowed": _to_bool(row.get("policy_allowed")),
                "effective_allowed": effective_allowed,
                "effective_event": effective_event,
                "neutral_override_diagnostic": _neutral_diag(row, active),
                "compatibility_relation_id": relation,
                "family_pair": _clean(row.get("family_pair")),
                "query_family": _clean(row.get("query_family")),
                "raw_ltr_top_family": _clean(row.get("raw_ltr_top_family")),
                "query_diameter": _clean(row.get("query_diameter")),
                "candidate_tier": _clean(row.get("candidate_tier")),
                "tier_ratio": _clean(row.get("tier_ratio")),
                "baseline_hit1": _to_bool(row.get("baseline_hit1")),
                "raw_ltr_hit1": _to_bool(row.get("raw_ltr_hit1")),
                "gated_hit1": _to_bool(row.get("gated_hit1")),
                "effective_hit1": _to_bool(row.get("policy_hit1")) if effective_allowed else _to_bool(row.get("gated_hit1")),
                "group_id": _clean(row.get("group_id")),
                "sample_id": _clean(row.get("sample_id")),
                "source_file": _clean(row.get("source_file")),
                "province": _clean(row.get("province")),
                "query": _clean(row.get("query")),
                "baseline_top_name": _clean(row.get("baseline_top_name")),
                "raw_ltr_top_name": _clean(row.get("raw_ltr_top_name")),
            }
        )
    return result


def _split_summary(
    relation_rows: list[dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
    *,
    active: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in relation_rows:
        by_split[_clean(row.get("split"))].append(row)
    for split in sorted(metrics):
        base = metrics[split]
        subset = by_split.get(split, [])
        matrix_groups = _to_int(base.get("matrix_groups"))
        eligible = _to_int(base.get("eligible_anchor_rows"))
        gated_hit1 = _to_int(base.get("gated_hit1"))
        policy_hit1 = _to_int(base.get("policy_hit1"))
        effective_hit1 = policy_hit1 if active else gated_hit1
        events = Counter(_clean(row.get("effective_event")) for row in subset)
        policy_events = Counter(_clean(row.get("policy_event")) for row in subset)
        result.append(
            {
                "split": split,
                "switch_mode": _mode_label(active),
                "matrix_groups": matrix_groups,
                "eligible_anchor_rows": eligible,
                "gated_hit1": gated_hit1,
                "policy_hit1_if_enabled": policy_hit1,
                "effective_hit1": effective_hit1,
                "effective_hit1_rate_matrix": _rate(effective_hit1, matrix_groups),
                "effective_hit1_rate_eligible": _rate(effective_hit1, eligible),
                "effective_net_vs_gated": effective_hit1 - gated_hit1,
                "relation_log_rows": len(subset),
                "effective_allowed_count": sum(1 for row in subset if _to_bool(row.get("effective_allowed"))),
                "would_allow_count": sum(1 for row in subset if _to_bool(row.get("policy_allowed"))),
                "neutral_override_count": events["allowed_neutral_override"],
                "would_neutral_override_count": policy_events["allowed_neutral_override"],
                "rescued_blocked_gain_count": events["rescued_blocked_gain"],
                "would_rescued_blocked_gain_count": policy_events["rescued_blocked_gain"],
                "new_residual_loss_count": events["new_residual_loss"],
                "fallback_to_safety_gate_count": sum(1 for row in subset if _clean(row.get("fallback_reason"))),
            }
        )
    return result


def _neutral_rows(relation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "split": row["split"],
            "switch_mode": row["switch_mode"],
            "diagnostic": row["neutral_override_diagnostic"],
            "compatibility_relation_id": row["compatibility_relation_id"],
            "family_pair": row["family_pair"],
            "policy_reason": row["policy_reason"],
            "query_diameter": row["query_diameter"],
            "candidate_tier": row["candidate_tier"],
            "tier_ratio": row["tier_ratio"],
            "group_id": row["group_id"],
            "sample_id": row["sample_id"],
            "source_file": row["source_file"],
            "province": row["province"],
            "query": row["query"],
            "baseline_top_name": row["baseline_top_name"],
            "raw_ltr_top_name": row["raw_ltr_top_name"],
        }
        for row in relation_rows
        if _clean(row.get("neutral_override_diagnostic")) in {"neutral_override_active", "would_be_neutral_override_if_enabled"}
    ]


def _bucket_rows(relation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "effective_event",
        "policy_event",
        "fallback_reason",
        "neutral_override_diagnostic",
        "compatibility_relation_id",
        "family_pair",
        "source_file",
        "province",
    ]
    totals: Counter[tuple[str, str]] = Counter()
    counters: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row in relation_rows:
        split = _clean(row.get("split"))
        mode = _clean(row.get("switch_mode"))
        totals[(mode, split)] += 1
        for field in fields:
            counters[(mode, split, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (mode, split, field), counter in sorted(counters.items()):
        total = totals[(mode, split)]
        for key, count in counter.most_common(20):
            result.append({"switch_mode": mode, "split": split, "bucket": field, "key": key, "count": count, "rate": _rate(count, total)})
    return result


def _contract_checks(
    *,
    active: bool,
    config: dict[str, Any],
    review_summary: dict[str, Any],
    split_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    neutral_diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    review_allows = bool((review_summary.get("recommendation") or {}).get("default_off_switch_skeleton_allowed_next"))
    checks = [
        {
            "gate": "config_default_disabled",
            "passed": config.get("goal_family_compatibility_switch_enabled") is False,
            "value": config.get("goal_family_compatibility_switch_enabled"),
            "threshold": "False",
        },
        {
            "gate": "explicit_flag_required_for_active_switch",
            "passed": True,
            "value": "active only when --enable-eval-only-switch is provided",
        },
        {
            "gate": "review_stage_allows_skeleton",
            "passed": review_allows,
            "value": review_allows,
        },
        {
            "gate": "no_search_integration",
            "passed": True,
            "value": "wrapper reads offline artifacts only",
        },
        {
            "gate": "relation_logs_present",
            "passed": len(relation_rows) > 0,
            "value": len(relation_rows),
            "threshold": ">0",
        },
        {
            "gate": "neutral_diagnostics_present",
            "passed": len(neutral_diagnostics) > 0,
            "value": len(neutral_diagnostics),
            "threshold": ">0",
        },
    ]
    if not active:
        checks.append(
            {
                "gate": "default_off_keeps_gated_hit1",
                "passed": all(_to_int(row.get("effective_hit1")) == _to_int(row.get("gated_hit1")) for row in split_rows),
                "value": "effective_hit1==gated_hit1",
            }
        )
        checks.append(
            {
                "gate": "default_off_no_effective_overrides",
                "passed": all(_to_int(row.get("effective_allowed_count")) == 0 for row in split_rows),
                "value": sum(_to_int(row.get("effective_allowed_count")) for row in split_rows),
                "threshold": "0",
            }
        )
    else:
        checks.append(
            {
                "gate": "enabled_remains_eval_only",
                "passed": True,
                "value": "explicit enabled run writes diagnostics only",
            }
        )
        checks.append(
            {
                "gate": "enabled_has_no_new_residual_loss",
                "passed": all(_to_int(row.get("new_residual_loss_count")) == 0 for row in split_rows),
                "value": sum(_to_int(row.get("new_residual_loss_count")) for row in split_rows),
                "threshold": "0",
            }
        )
    return checks


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Goal Family Compatibility Switch Skeleton",
        "",
        "Stage 7.9 creates an eval-only wrapper contract for the freeze/narrow family compatibility policy. It does not train, tune, modify GoalSearcher, or connect to the default search path.",
        "",
        "## Switch Contract",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["switch_mode", report["switch_mode"]],
                ["enable_eval_only_switch_requested", report["enable_eval_only_switch_requested"]],
                ["runtime_active", report["runtime_active"]],
                ["default_config_enabled", report["switch_config"].get("goal_family_compatibility_switch_enabled")],
                ["contract_passed", report["contract_passed"]],
                ["recommendation", report["recommendation"]],
            ]
        ),
        "",
        "## Split Metrics",
        "",
        _md_table(
            [["split", "effective_top1_matrix", "net_vs_gated", "effective_allowed", "neutral", "would_neutral", "fallback"]]
            + [
                [
                    row["split"],
                    row["effective_hit1_rate_matrix"],
                    row["effective_net_vs_gated"],
                    row["effective_allowed_count"],
                    row["neutral_override_count"],
                    row["would_neutral_override_count"],
                    row["fallback_to_safety_gate_count"],
                ]
                for row in report["split_metrics"]
            ]
        ),
        "",
        "## Contract Checks",
        "",
        _md_table([["gate", "passed", "value", "threshold"]] + [[row["gate"], row["passed"], row.get("value", ""), row.get("threshold", "")] for row in report["contract_checks"]]),
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


def _relation_log_fields() -> list[str]:
    return [
        "split",
        "switch_mode",
        "switch_enabled",
        "switch_enabled_requested",
        "switch_active",
        "fallback_reason",
        "policy",
        "policy_event",
        "policy_reason",
        "policy_allowed",
        "effective_allowed",
        "effective_event",
        "neutral_override_diagnostic",
        "compatibility_relation_id",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "query_diameter",
        "candidate_tier",
        "tier_ratio",
        "baseline_hit1",
        "raw_ltr_hit1",
        "gated_hit1",
        "effective_hit1",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "baseline_top_name",
        "raw_ltr_top_name",
    ]


def _neutral_fields() -> list[str]:
    return [
        "split",
        "switch_mode",
        "diagnostic",
        "compatibility_relation_id",
        "family_pair",
        "policy_reason",
        "query_diameter",
        "candidate_tier",
        "tier_ratio",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "baseline_top_name",
        "raw_ltr_top_name",
    ]


def _default_paths(output_dir: Path, mode: str) -> dict[str, Path]:
    prefix = f"goal_family_compatibility_switch_skeleton_{mode}"
    return {
        "summary_json": output_dir / f"{prefix}_summary.json",
        "summary_md": output_dir / f"{prefix}_summary.md",
        "relation_log_csv": output_dir / f"{prefix}_relation_log.csv",
        "neutral_diagnostics_csv": output_dir / f"{prefix}_neutral_diagnostics.csv",
        "bucket_csv": output_dir / f"{prefix}_buckets.csv",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.9 eval-only family compatibility switch skeleton")
    parser.add_argument("--policy-summary", default=str(DEFAULT_POLICY_SUMMARY))
    parser.add_argument("--policy-rows", default=str(DEFAULT_POLICY_ROWS))
    parser.add_argument("--review-summary", default=str(DEFAULT_REVIEW_SUMMARY))
    parser.add_argument("--switch-config", default=str(DEFAULT_SWITCH_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--enable-eval-only-switch", action="store_true")
    parser.add_argument("--include-all-rows", action="store_true")
    parser.add_argument("--report-json", default="")
    parser.add_argument("--report-md", default="")
    parser.add_argument("--relation-log-csv", default="")
    parser.add_argument("--neutral-diagnostics-csv", default="")
    parser.add_argument("--bucket-csv", default="")
    args = parser.parse_args()

    started = time.perf_counter()
    active = bool(args.enable_eval_only_switch)
    mode = _mode_label(active)
    output_dir = Path(args.output_dir)
    paths = _default_paths(output_dir, mode)
    report_json = Path(args.report_json) if args.report_json else paths["summary_json"]
    report_md = Path(args.report_md) if args.report_md else paths["summary_md"]
    relation_log_csv = Path(args.relation_log_csv) if args.relation_log_csv else paths["relation_log_csv"]
    neutral_csv = Path(args.neutral_diagnostics_csv) if args.neutral_diagnostics_csv else paths["neutral_diagnostics_csv"]
    bucket_csv = Path(args.bucket_csv) if args.bucket_csv else paths["bucket_csv"]

    policy_summary = _read_json(Path(args.policy_summary))
    review_summary = _read_json(Path(args.review_summary))
    switch_config = _load_or_create_config(Path(args.switch_config))
    metrics = _selected_policy_metrics(policy_summary)
    rows = _selected_rows(_read_csv(Path(args.policy_rows)))

    relation_rows = _relation_log_rows(rows, active=active, include_all_rows=bool(args.include_all_rows))
    split_metrics = _split_summary(relation_rows, metrics, active=active)
    neutral_diagnostics = _neutral_rows(relation_rows)
    bucket_rows = _bucket_rows(relation_rows)
    checks = _contract_checks(
        active=active,
        config=switch_config,
        review_summary=review_summary,
        split_rows=split_metrics,
        relation_rows=relation_rows,
        neutral_diagnostics=neutral_diagnostics,
    )
    contract_passed = all(check["passed"] for check in checks)
    recommendation = (
        "default_off_contract_ready_for_broader_eval"
        if contract_passed and not active
        else "explicit_enabled_eval_only_contract_ready_for_broader_eval"
        if contract_passed
        else "fix_switch_contract_before_any_next_stage"
    )

    report = {
        "stage": "Goal LTR v1 / stage 7.9 family compatibility eval-only switch skeleton",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "enable_eval_only_switch_requested": active,
        "runtime_active": active,
        "switch_mode": mode,
        "selected_policy": SELECTED_POLICY,
        "policy_summary": str(Path(args.policy_summary)),
        "policy_rows": str(Path(args.policy_rows)),
        "review_summary": str(Path(args.review_summary)),
        "switch_config_path": str(Path(args.switch_config)),
        "switch_config": switch_config,
        "split_metrics": split_metrics,
        "contract_checks": checks,
        "contract_passed": contract_passed,
        "recommendation": recommendation,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 8.0: run a broader eval-only switch contract audit on anchor-clean splits; keep default-off and do not tune heldout.",
        "artifacts": {
            "summary_json": str(report_json),
            "summary_md": str(report_md),
            "relation_log_csv": str(relation_log_csv),
            "neutral_diagnostics_csv": str(neutral_csv),
            "bucket_csv": str(bucket_csv),
            "switch_config": str(Path(args.switch_config)),
        },
    }

    _write_json(report_json, report)
    _write_markdown(report_md, report)
    _write_csv(relation_log_csv, relation_rows, _relation_log_fields())
    _write_csv(neutral_csv, neutral_diagnostics, _neutral_fields())
    _write_csv(bucket_csv, bucket_rows, ["switch_mode", "split", "bucket", "key", "count", "rate"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "switch_mode": mode,
                    "runtime_active": active,
                    "contract_passed": contract_passed,
                    "recommendation": recommendation,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_metrics": split_metrics,
                "contract_checks": checks,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
