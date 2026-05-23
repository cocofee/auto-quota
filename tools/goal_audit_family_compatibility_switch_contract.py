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

DEFAULT_AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_POLICY_SUMMARY = DEFAULT_AGENT_STATE / "goal_family_compatibility_freeze_narrow_whatif_summary.json"
DEFAULT_POLICY_ROWS = DEFAULT_AGENT_STATE / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_SWITCH_CONFIG = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_config_v1.json"
DEFAULT_DEFAULT_SUMMARY = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_default_off_summary.json"
DEFAULT_ENABLED_SUMMARY = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_explicit_enabled_eval_only_summary.json"
DEFAULT_DEFAULT_LOG = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_default_off_relation_log.csv"
DEFAULT_ENABLED_LOG = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_explicit_enabled_eval_only_relation_log.csv"
DEFAULT_OUTPUT_PREFIX = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_contract_audit"

SELECTED_POLICY = "freeze_plus_tight_sleeve_duct"
EXPECTED_SPLITS = {"dev_oof", "heldout", "hard"}
LOG_ALIASES = {
    "switch_enabled": "switch_enabled_requested",
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
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def _selected_policy_metrics(policy_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for row in policy_summary.get("policy_metrics") or []:
        if _clean(row.get("policy")) == SELECTED_POLICY:
            split = _clean(row.get("split"))
            if split:
                metrics[split] = row
    return metrics


def _split_summary_rows(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in summary.get("split_metrics") or []:
        split = _clean(row.get("split"))
        if split:
            result[split] = row
    return result


def _shadow_neutral_diag(row: dict[str, Any], mode: str) -> str:
    event = _clean(row.get("policy_event"))
    if event == "allowed_neutral_override":
        return "neutral_override_active" if mode == "explicit_shadow" else "would_be_neutral_override_if_enabled"
    if event == "rescued_blocked_gain":
        return "rescued_gain_active" if mode == "explicit_shadow" else "would_rescue_if_enabled"
    if event == "new_residual_loss":
        return "new_loss_active" if mode == "explicit_shadow" else "would_be_new_loss_if_enabled"
    return ""


def _fallback_reason(row: dict[str, Any], mode: str) -> str:
    if mode == "default_off_broader":
        return "switch_disabled_default_off"
    if _to_bool(row.get("policy_allowed")):
        return ""
    return _clean(row.get("policy_reason")) or "policy_not_allowed_fallback_to_safety_gate"


def _effective_event(row: dict[str, Any], mode: str) -> str:
    if mode == "explicit_shadow":
        return _clean(row.get("policy_event")) or "baseline_safety_gate"
    if _to_bool(row.get("policy_allowed")):
        return f"would_{_clean(row.get('policy_event')) or 'allow'}"
    return "fallback_to_safety_gate"


def _audit_rows(policy_rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    active = mode == "explicit_shadow"
    for row in policy_rows:
        policy_allowed = _to_bool(row.get("policy_allowed"))
        effective_allowed = bool(active and policy_allowed)
        result.append(
            {
                "audit_mode": mode,
                "split": _clean(row.get("split")),
                "policy": SELECTED_POLICY,
                "runtime_active": active,
                "policy_event": _clean(row.get("policy_event")),
                "policy_allowed": policy_allowed,
                "policy_reason": _clean(row.get("policy_reason")),
                "effective_allowed": effective_allowed,
                "effective_event": _effective_event(row, mode),
                "fallback_reason": _fallback_reason(row, mode),
                "neutral_override_diagnostic": _shadow_neutral_diag(row, mode),
                "compatibility_relation_id": _clean(row.get("compatibility_relation_id")),
                "family_pair": _clean(row.get("family_pair")),
                "query_family": _clean(row.get("query_family")),
                "raw_ltr_top_family": _clean(row.get("raw_ltr_top_family")),
                "baseline_hit1": _to_bool(row.get("baseline_hit1")),
                "raw_ltr_hit1": _to_bool(row.get("raw_ltr_hit1")),
                "gated_hit1": _to_bool(row.get("gated_hit1")),
                "policy_hit1": _to_bool(row.get("policy_hit1")),
                "effective_hit1": _to_bool(row.get("policy_hit1")) if effective_allowed else _to_bool(row.get("gated_hit1")),
                "query_diameter": _clean(row.get("query_diameter")),
                "candidate_tier": _clean(row.get("candidate_tier")),
                "tier_ratio": _clean(row.get("tier_ratio")),
                "score_margin": _clean(row.get("score_margin")),
                "source_file": _clean(row.get("source_file")),
                "province": _clean(row.get("province")),
                "group_id": _clean(row.get("group_id")),
                "sample_id": _clean(row.get("sample_id")),
                "query": _clean(row.get("query")),
                "baseline_top_name": _clean(row.get("baseline_top_name")),
                "raw_ltr_top_name": _clean(row.get("raw_ltr_top_name")),
            }
        )
    return result


def _split_metrics(
    audit_rows: list[dict[str, Any]],
    policy_metrics: dict[str, dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audit_rows:
        by_split[_clean(row.get("split"))].append(row)
    result: list[dict[str, Any]] = []
    for split in sorted(policy_metrics):
        base = policy_metrics[split]
        rows = by_split.get(split, [])
        event_counts = Counter(_clean(row.get("policy_event")) for row in rows)
        fallback_counts = Counter(_clean(row.get("fallback_reason")) for row in rows)
        matrix_groups = _to_int(base.get("matrix_groups"))
        eligible = _to_int(base.get("eligible_anchor_rows"))
        gated_hit1 = _to_int(base.get("gated_hit1"))
        policy_hit1 = _to_int(base.get("policy_hit1"))
        effective_hit1 = gated_hit1 if mode == "default_off_broader" else policy_hit1
        result.append(
            {
                "audit_mode": mode,
                "split": split,
                "policy_rows": len(rows),
                "matrix_groups": matrix_groups,
                "eligible_anchor_rows": eligible,
                "gated_hit1": gated_hit1,
                "policy_hit1_if_enabled": policy_hit1,
                "effective_hit1": effective_hit1,
                "effective_hit1_rate_matrix": _rate(effective_hit1, matrix_groups),
                "effective_hit1_rate_eligible": _rate(effective_hit1, eligible),
                "net_vs_gated": effective_hit1 - gated_hit1,
                "effective_allowed_count": sum(1 for row in rows if _to_bool(row.get("effective_allowed"))),
                "fallback_count": sum(1 for row in rows if _clean(row.get("fallback_reason"))),
                "would_allow_count": sum(1 for row in rows if _to_bool(row.get("policy_allowed"))),
                "rescued_blocked_gain": event_counts["rescued_blocked_gain"],
                "allowed_neutral_override": event_counts["allowed_neutral_override"],
                "still_blocked_gain": event_counts["still_blocked_gain"],
                "saved_loss_retained": event_counts["saved_loss_retained"],
                "existing_residual_loss": event_counts["existing_residual_loss"],
                "new_residual_loss": event_counts["new_residual_loss"],
                "top_fallback_reason": fallback_counts.most_common(1)[0][0] if fallback_counts else "",
            }
        )
    return result


def _fallback_rows(audit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: Counter[tuple[str, str]] = Counter()
    counters: Counter[tuple[str, str, str]] = Counter()
    for row in audit_rows:
        mode = _clean(row.get("audit_mode"))
        split = _clean(row.get("split"))
        reason = _clean(row.get("fallback_reason")) or "<allowed>"
        totals[(mode, split)] += 1
        counters[(mode, split, reason)] += 1
    result: list[dict[str, Any]] = []
    for (mode, split, reason), count in sorted(counters.items()):
        total = totals[(mode, split)]
        result.append({"audit_mode": mode, "split": split, "fallback_reason": reason, "count": count, "rate": _rate(count, total)})
    return result


def _bucket_rows(audit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ["policy_event", "effective_event", "compatibility_relation_id", "family_pair", "province", "source_file"]
    totals: Counter[tuple[str, str]] = Counter()
    counters: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row in audit_rows:
        mode = _clean(row.get("audit_mode"))
        split = _clean(row.get("split"))
        totals[(mode, split)] += 1
        for field in fields:
            counters[(mode, split, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (mode, split, field), counter in sorted(counters.items()):
        total = totals[(mode, split)]
        for key, count in counter.most_common(20):
            result.append({"audit_mode": mode, "split": split, "bucket": field, "key": key, "count": count, "rate": _rate(count, total)})
    return result


def _schema_rows(
    *,
    source_name: str,
    path: Path,
    required_fields: list[str],
) -> list[dict[str, Any]]:
    rows = _read_csv(path)
    fields = set(rows[0].keys()) if rows else set()
    result: list[dict[str, Any]] = []
    for required in required_fields:
        alias = LOG_ALIASES.get(required)
        if required in fields:
            status = "present"
            observed = required
        elif alias and alias in fields:
            status = "alias_present"
            observed = alias
        else:
            status = "missing"
            observed = ""
        result.append(
            {
                "source": source_name,
                "path": str(path),
                "required_field": required,
                "status": status,
                "observed_field": observed,
                "row_count": len(rows),
            }
        )
    return result


def _compare_summary(
    expected_rows: list[dict[str, Any]],
    summary_rows: dict[str, dict[str, Any]],
    fields: list[str],
    label: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for expected in expected_rows:
        split = _clean(expected.get("split"))
        actual = summary_rows.get(split, {})
        for field in fields:
            expected_value = expected.get(field)
            actual_field = {
                "net_vs_gated": "effective_net_vs_gated",
                "new_residual_loss": "new_residual_loss_count",
            }.get(field, field)
            actual_value = actual.get(actual_field)
            result.append(
                {
                    "check": label,
                    "split": split,
                    "field": field,
                    "expected": expected_value,
                    "actual": actual_value,
                    "matched": _clean(expected_value) == _clean(actual_value),
                }
            )
    return result


def _contract_checks(
    *,
    config: dict[str, Any],
    policy_summary: dict[str, Any],
    policy_rows: list[dict[str, Any]],
    default_summary: dict[str, Any],
    enabled_summary: dict[str, Any],
    default_metrics: list[dict[str, Any]],
    enabled_metrics: list[dict[str, Any]],
    schema_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    splits = {_clean(row.get("split")) for row in policy_rows}
    selected = policy_summary.get("selected_policy_summary") or {}
    oof = policy_summary.get("oof_gates") or {}
    hard_checks = [
        {
            "gate": "config_default_disabled",
            "passed": config.get("goal_family_compatibility_switch_enabled") is False,
            "value": config.get("goal_family_compatibility_switch_enabled"),
            "threshold": "False",
        },
        {
            "gate": "default_summary_contract_passed",
            "passed": bool(default_summary.get("contract_passed")) and not bool(default_summary.get("runtime_active")),
            "value": {"contract_passed": default_summary.get("contract_passed"), "runtime_active": default_summary.get("runtime_active")},
        },
        {
            "gate": "enabled_summary_eval_only_contract_passed",
            "passed": bool(enabled_summary.get("contract_passed")) and bool(enabled_summary.get("eval_only")) and bool(enabled_summary.get("no_search_integration")),
            "value": {
                "contract_passed": enabled_summary.get("contract_passed"),
                "eval_only": enabled_summary.get("eval_only"),
                "no_search_integration": enabled_summary.get("no_search_integration"),
            },
        },
        {
            "gate": "all_expected_splits_present",
            "passed": EXPECTED_SPLITS.issubset(splits),
            "value": sorted(splits),
            "threshold": sorted(EXPECTED_SPLITS),
        },
        {
            "gate": "broader_rows_cover_selected_policy",
            "passed": len(policy_rows) > 0,
            "value": len(policy_rows),
            "threshold": ">0",
        },
        {
            "gate": "default_off_keeps_gated_on_all_rows",
            "passed": all(_to_int(row.get("net_vs_gated")) == 0 for row in default_metrics),
            "value": {row["split"]: row["net_vs_gated"] for row in default_metrics},
            "threshold": "0 for every split",
        },
        {
            "gate": "default_off_no_effective_overrides_all_rows",
            "passed": all(_to_int(row.get("effective_allowed_count")) == 0 for row in default_metrics),
            "value": {row["split"]: row["effective_allowed_count"] for row in default_metrics},
            "threshold": "0 for every split",
        },
        {
            "gate": "default_off_all_rows_fallback",
            "passed": all(_to_int(row.get("fallback_count")) == _to_int(row.get("policy_rows")) for row in default_metrics),
            "value": {row["split"]: f"{row['fallback_count']}/{row['policy_rows']}" for row in default_metrics},
        },
        {
            "gate": "explicit_shadow_has_no_new_residual_loss",
            "passed": all(_to_int(row.get("new_residual_loss")) == 0 for row in enabled_metrics),
            "value": {row["split"]: row["new_residual_loss"] for row in enabled_metrics},
            "threshold": "0 for every split",
        },
        {
            "gate": "summaries_match_broader_metrics",
            "passed": all(_to_bool(row.get("matched")) for row in comparison_rows),
            "value": sum(1 for row in comparison_rows if not _to_bool(row.get("matched"))),
            "threshold": "0 mismatches",
        },
        {
            "gate": "heldout_not_used_for_policy_selection",
            "passed": _clean(selected.get("split")) == "dev_oof" and bool(oof.get("passed")),
            "value": {"selected_split": selected.get("split"), "oof_gates_passed": oof.get("passed")},
        },
        {
            "gate": "no_training_or_search_integration",
            "passed": all(
                bool(default_summary.get(flag)) and bool(enabled_summary.get(flag))
                for flag in ("eval_only", "no_training", "no_model_tuning", "no_search_integration", "no_goal_searcher_change")
            ),
            "value": "summary flags only; no production code path read by this audit",
        },
    ]
    warnings = [
        {
            "warning": "canonical_log_field_missing_alias_present",
            "present": any(row["status"] == "alias_present" for row in schema_rows),
            "count": sum(1 for row in schema_rows if row["status"] == "alias_present"),
            "detail": "switch_enabled is represented as switch_enabled_requested in 7.9 logs",
        },
        {
            "warning": "exact_required_log_fields_missing",
            "present": any(row["status"] == "missing" for row in schema_rows),
            "count": sum(1 for row in schema_rows if row["status"] == "missing"),
        },
    ]
    return hard_checks, warnings


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Goal Family Compatibility Switch Contract Audit",
        "",
        "Stage 8.0 audits the default-off eval-only switch contract over all selected policy rows. It does not train, tune, modify GoalSearcher, or connect to the default search path.",
        "",
        "## Result",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["hard_gate_passed", report["hard_gate_passed"]],
                ["schema_warning_count", report["schema_warning_count"]],
                ["selected_policy_rows", report["selected_policy_rows"]],
                ["recommendation", report["recommendation"]],
            ]
        ),
        "",
        "## Split Metrics",
        "",
        _md_table(
            [["mode", "split", "rows", "effective_top1_matrix", "net_vs_gated", "allowed", "fallback", "new_loss"]]
            + [
                [
                    row["audit_mode"],
                    row["split"],
                    row["policy_rows"],
                    row["effective_hit1_rate_matrix"],
                    row["net_vs_gated"],
                    row["effective_allowed_count"],
                    row["fallback_count"],
                    row["new_residual_loss"],
                ]
                for row in report["split_metrics"]
            ]
        ),
        "",
        "## Gates",
        "",
        _md_table([["gate", "passed", "value", "threshold"]] + [[row["gate"], row["passed"], row.get("value", ""), row.get("threshold", "")] for row in report["hard_checks"]]),
        "",
        "## Warnings",
        "",
        _md_table([["warning", "present", "count", "detail"]] + [[row["warning"], row.get("present", ""), row.get("count", ""), row.get("detail", "")] for row in report["warnings"]]),
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
    result = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        result.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(result)


def _audit_row_fields() -> list[str]:
    return [
        "audit_mode",
        "split",
        "policy",
        "runtime_active",
        "policy_event",
        "policy_allowed",
        "policy_reason",
        "effective_allowed",
        "effective_event",
        "fallback_reason",
        "neutral_override_diagnostic",
        "compatibility_relation_id",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "baseline_hit1",
        "raw_ltr_hit1",
        "gated_hit1",
        "policy_hit1",
        "effective_hit1",
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


def _split_metric_fields() -> list[str]:
    return [
        "audit_mode",
        "split",
        "policy_rows",
        "matrix_groups",
        "eligible_anchor_rows",
        "gated_hit1",
        "policy_hit1_if_enabled",
        "effective_hit1",
        "effective_hit1_rate_matrix",
        "effective_hit1_rate_eligible",
        "net_vs_gated",
        "effective_allowed_count",
        "fallback_count",
        "would_allow_count",
        "rescued_blocked_gain",
        "allowed_neutral_override",
        "still_blocked_gain",
        "saved_loss_retained",
        "existing_residual_loss",
        "new_residual_loss",
        "top_fallback_reason",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 8.0 broader eval-only switch contract audit")
    parser.add_argument("--policy-summary", default=str(DEFAULT_POLICY_SUMMARY))
    parser.add_argument("--policy-rows", default=str(DEFAULT_POLICY_ROWS))
    parser.add_argument("--switch-config", default=str(DEFAULT_SWITCH_CONFIG))
    parser.add_argument("--default-summary", default=str(DEFAULT_DEFAULT_SUMMARY))
    parser.add_argument("--enabled-summary", default=str(DEFAULT_ENABLED_SUMMARY))
    parser.add_argument("--default-relation-log", default=str(DEFAULT_DEFAULT_LOG))
    parser.add_argument("--enabled-relation-log", default=str(DEFAULT_ENABLED_LOG))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    output_prefix = Path(args.output_prefix)
    policy_summary = _read_json(Path(args.policy_summary))
    policy_rows = [row for row in _read_csv(Path(args.policy_rows)) if _clean(row.get("policy")) == SELECTED_POLICY]
    config = _read_json(Path(args.switch_config))
    default_summary = _read_json(Path(args.default_summary))
    enabled_summary = _read_json(Path(args.enabled_summary))
    policy_metrics = _selected_policy_metrics(policy_summary)

    default_rows = _audit_rows(policy_rows, "default_off_broader")
    enabled_rows = _audit_rows(policy_rows, "explicit_shadow")
    all_audit_rows = default_rows + enabled_rows
    default_metrics = _split_metrics(default_rows, policy_metrics, "default_off_broader")
    enabled_metrics = _split_metrics(enabled_rows, policy_metrics, "explicit_shadow")
    split_metrics = default_metrics + enabled_metrics
    fallback_rows = _fallback_rows(all_audit_rows)
    bucket_rows = _bucket_rows(all_audit_rows)

    required_logs = list(config.get("required_logs") or [])
    schema_rows = _schema_rows(source_name="default_off_relation_log", path=Path(args.default_relation_log), required_fields=required_logs)
    schema_rows += _schema_rows(source_name="explicit_enabled_relation_log", path=Path(args.enabled_relation_log), required_fields=required_logs)

    comparison_rows = _compare_summary(
        default_metrics,
        _split_summary_rows(default_summary),
        ["effective_hit1", "effective_hit1_rate_matrix", "net_vs_gated", "effective_allowed_count"],
        "default_summary_matches_broader",
    )
    comparison_rows += _compare_summary(
        enabled_metrics,
        _split_summary_rows(enabled_summary),
        ["effective_hit1", "effective_hit1_rate_matrix", "net_vs_gated", "effective_allowed_count", "new_residual_loss"],
        "enabled_summary_matches_broader",
    )

    checks, warnings = _contract_checks(
        config=config,
        policy_summary=policy_summary,
        policy_rows=policy_rows,
        default_summary=default_summary,
        enabled_summary=enabled_summary,
        default_metrics=default_metrics,
        enabled_metrics=enabled_metrics,
        schema_rows=schema_rows,
        comparison_rows=comparison_rows,
    )
    hard_gate_passed = all(_to_bool(row.get("passed")) for row in checks)
    schema_warning_count = sum(_to_int(row.get("count")) for row in warnings if _to_bool(row.get("present")))
    recommendation = (
        "contract_passed_with_schema_alias_warning_keep_default_off_and_harden_logs_next"
        if hard_gate_passed and schema_warning_count
        else "contract_passed_prepare_default_off_readiness_review"
        if hard_gate_passed
        else "contract_failed_fix_audit_or_inputs_before_any_next_stage"
    )

    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "all_rows_csv": str(output_prefix.with_name(output_prefix.name + "_all_rows.csv")),
        "split_metrics_csv": str(output_prefix.with_name(output_prefix.name + "_split_metrics.csv")),
        "fallback_audit_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_audit.csv")),
        "event_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_event_buckets.csv")),
        "log_schema_csv": str(output_prefix.with_name(output_prefix.name + "_log_schema.csv")),
        "summary_comparison_csv": str(output_prefix.with_name(output_prefix.name + "_summary_comparison.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 8.0 broader eval-only switch contract audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "default_off_only": True,
        "selected_policy": SELECTED_POLICY,
        "selected_policy_rows": len(policy_rows),
        "hard_gate_passed": hard_gate_passed,
        "schema_warning_count": schema_warning_count,
        "hard_checks": checks,
        "warnings": warnings,
        "split_metrics": split_metrics,
        "recommendation": recommendation,
        "recommended_next_stage": "Stage 8.1: harden switch log schema/readiness only if needed; keep default-off, do not train, tune, or connect GoalSearcher.",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "inputs": {
            "policy_summary": str(Path(args.policy_summary)),
            "policy_rows": str(Path(args.policy_rows)),
            "switch_config": str(Path(args.switch_config)),
            "default_summary": str(Path(args.default_summary)),
            "enabled_summary": str(Path(args.enabled_summary)),
            "default_relation_log": str(Path(args.default_relation_log)),
            "enabled_relation_log": str(Path(args.enabled_relation_log)),
        },
        "artifacts": artifacts,
        "anti_drift_conclusion": "This audit is split-wide and relation-agnostic. It only checks the default-off eval-only switch contract, fallback behavior, log schema, and metric stability; it does not add family-specific rules or use heldout for selection.",
    }

    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["all_rows_csv"]), all_audit_rows, _audit_row_fields())
    _write_csv(Path(artifacts["split_metrics_csv"]), split_metrics, _split_metric_fields())
    _write_csv(Path(artifacts["fallback_audit_csv"]), fallback_rows, ["audit_mode", "split", "fallback_reason", "count", "rate"])
    _write_csv(Path(artifacts["event_buckets_csv"]), bucket_rows, ["audit_mode", "split", "bucket", "key", "count", "rate"])
    _write_csv(Path(artifacts["log_schema_csv"]), schema_rows, ["source", "path", "required_field", "status", "observed_field", "row_count"])
    _write_csv(Path(artifacts["summary_comparison_csv"]), comparison_rows, ["check", "split", "field", "expected", "actual", "matched"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "hard_gate_passed": hard_gate_passed,
                    "schema_warning_count": schema_warning_count,
                    "recommendation": recommendation,
                    "selected_policy_rows": len(policy_rows),
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_metrics": split_metrics,
                "hard_checks": checks,
                "warnings": warnings,
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
