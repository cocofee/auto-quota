from __future__ import annotations

import argparse
import csv
import json
import re
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
DEFAULT_RESIDUAL_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_residual_audit_summary.json"
DEFAULT_POLICY_SPEC = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_policy_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_summary.md"
DEFAULT_ROWS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_buckets.csv"
DEFAULT_EXAMPLES_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_freeze_narrow_whatif_examples.jsonl"

SELECTED_POLICY = "freeze_plus_tight_sleeve_duct"
FREEZE_RELATIONS = {
    "sleeve_support_taxonomy_alias",
    "valve_duct_air_system_neighbor",
}
LOW_SUPPORT_RELATIONS = {
    "conduit_pipe_electrical_neighbor",
    "formwork_concrete_taxonomy_alias",
}
NARROW_RELATION = "sleeve_duct_closed_wall_neighbor"


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _default_policy_spec() -> dict[str, Any]:
    return {
        "stage": "Goal LTR v1 / stage 7.7 relation freeze/narrow eval-only what-if",
        "version": "v1",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "selected_policy": SELECTED_POLICY,
        "policies": [
            {
                "name": "freeze_high_support_only",
                "freeze_relations": sorted(FREEZE_RELATIONS),
                "narrow_relations": [],
                "review_blocked_relations": sorted(LOW_SUPPORT_RELATIONS | {NARROW_RELATION}),
                "description": "Only allow high-support relations from stage 7.6; block noisy and low-support relations.",
            },
            {
                "name": SELECTED_POLICY,
                "freeze_relations": sorted(FREEZE_RELATIONS),
                "narrow_relations": [NARROW_RELATION],
                "review_blocked_relations": sorted(LOW_SUPPORT_RELATIONS),
                "description": "Allow high-support relations and a tightened sleeve/duct relation; block low-support relations.",
            },
        ],
        "narrow_rules": {
            NARROW_RELATION: {
                "required_candidate_terms": ["密闭穿墙管"],
                "negative_candidate_terms": ["排气阀", "排气阀门", "阀门安装"],
                "diameter_policy": {
                    "query_diameter_must_be_lte_candidate_tier": True,
                    "max_candidate_tier_to_query_ratio": 1.5,
                },
                "reason": "sleeve/duct only remains compatible when the candidate is the wall-pipe item itself and the implicit diameter tier is plausible.",
            }
        },
        "dev_oof_gates": {
            "min_rescued_retention_vs_stage_7_5": 0.85,
            "min_neutral_override_reduction_vs_stage_7_5": 0.25,
            "max_new_residual_loss": 0,
            "require_non_sleeve_rescue": True,
        },
    }


def _load_or_create_policy(path: Path) -> dict[str, Any]:
    if path.exists():
        return _read_json(path)
    spec = _default_policy_spec()
    _write_json(path, spec)
    return spec


def _policy_by_name(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policies = spec.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ValueError("policy spec missing policies")
    result: dict[str, dict[str, Any]] = {}
    for policy in policies:
        if isinstance(policy, dict) and _clean(policy.get("name")):
            result[_clean(policy.get("name"))] = policy
    if SELECTED_POLICY not in result:
        raise ValueError(f"policy spec missing selected policy {SELECTED_POLICY}")
    return result


def _extract_query_diameter(text: str) -> int | None:
    normalized = text.upper().replace("Ｄ", "D").replace("Ｎ", "N")
    match = re.search(r"\bD\s*N?\s*([0-9]{1,4})\b|\bDN\s*([0-9]{1,4})\b", normalized)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return int(value) if value else None


def _extract_candidate_tier(text: str) -> int | None:
    normalized = text.replace("≤", "<=").replace("以内", "<=")
    matches = re.findall(r"(?:<=|<)\s*([0-9]{1,4})", normalized)
    if matches:
        return int(matches[-1])
    # Fallback for compact expressions such as "直径200(mm)" when no upper-bound marker exists.
    match = re.search(r"直径\s*([0-9]{1,4})\s*\(?mm", normalized, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _tight_sleeve_duct_allowed(row: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    candidate = _clean(row.get("raw_ltr_top_name"))
    query = _clean(row.get("query"))
    required_terms = rules.get("required_candidate_terms") or []
    negative_terms = rules.get("negative_candidate_terms") or []

    if any(term not in candidate for term in required_terms):
        return False, "narrow_block_missing_wall_pipe_candidate_term", {}
    if any(term in candidate for term in negative_terms):
        return False, "narrow_block_air_valve_candidate", {}

    query_diameter = _extract_query_diameter(query)
    candidate_tier = _extract_candidate_tier(candidate)
    if not query_diameter or not candidate_tier:
        return False, "narrow_block_missing_diameter_evidence", {
            "query_diameter": query_diameter,
            "candidate_tier": candidate_tier,
        }
    if query_diameter > candidate_tier:
        return False, "narrow_block_query_diameter_exceeds_candidate_tier", {
            "query_diameter": query_diameter,
            "candidate_tier": candidate_tier,
        }
    max_ratio = _to_float((rules.get("diameter_policy") or {}).get("max_candidate_tier_to_query_ratio")) or 1.5
    ratio = candidate_tier / query_diameter if query_diameter else 999.0
    if ratio > max_ratio:
        return False, "narrow_block_candidate_tier_too_coarse", {
            "query_diameter": query_diameter,
            "candidate_tier": candidate_tier,
            "tier_ratio": round(ratio, 6),
        }
    return True, "narrow_allow_wall_pipe_and_diameter_tier_consistent", {
        "query_diameter": query_diameter,
        "candidate_tier": candidate_tier,
        "tier_ratio": round(ratio, 6),
    }


def _relation_id(row: dict[str, Any]) -> str:
    return _clean(row.get("compatibility_relation_id"))


def _allow_for_policy(row: dict[str, Any], policy: dict[str, Any], spec: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    if not _to_bool(row.get("compat_checked")):
        return False, "not_compat_checked", {}
    relation = _relation_id(row)
    if not relation:
        return False, "no_relation", {}
    freeze_relations = set(policy.get("freeze_relations") or [])
    narrow_relations = set(policy.get("narrow_relations") or [])
    review_blocked = set(policy.get("review_blocked_relations") or [])

    if relation in freeze_relations:
        return True, "freeze_allow_high_support_relation", {}
    if relation in narrow_relations:
        rules = ((spec.get("narrow_rules") or {}).get(relation) or {})
        return _tight_sleeve_duct_allowed(row, rules)
    if relation in review_blocked:
        return False, "blocked_low_support_or_noisy_review_relation", {}
    return False, "blocked_not_in_frozen_policy", {}


def _event_for_policy(row: dict[str, Any], allowed: bool) -> str:
    gate_allowed = _to_bool(row.get("gate_allowed"))
    baseline_hit1 = _to_bool(row.get("baseline_hit1"))
    raw_hit1 = _to_bool(row.get("raw_ltr_hit1"))
    if gate_allowed:
        original_event = _clean(row.get("selected_outcome"))
        if original_event == "passed_gain":
            return "already_passed_gain"
        if original_event == "residual_loss":
            return "existing_residual_loss"
        return "neutral"
    if allowed:
        if not baseline_hit1 and raw_hit1:
            return "rescued_blocked_gain"
        if baseline_hit1 and not raw_hit1:
            return "new_residual_loss"
        if not baseline_hit1 and not raw_hit1:
            return "allowed_neutral_override"
        return "allowed_both_hit"
    if not baseline_hit1 and raw_hit1:
        return "still_blocked_gain"
    if baseline_hit1 and not raw_hit1:
        return "saved_loss_retained"
    return "neutral"


def _enrich_rows(
    rows: list[dict[str, Any]],
    policy_name: str,
    policy: dict[str, Any],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        allowed, reason, evidence = _allow_for_policy(row, policy, spec)
        event = _event_for_policy(row, allowed)
        item = dict(row)
        item["policy"] = policy_name
        item["policy_allowed"] = allowed
        item["policy_reason"] = reason
        item["policy_event"] = event
        item["query_diameter"] = evidence.get("query_diameter")
        item["candidate_tier"] = evidence.get("candidate_tier")
        item["tier_ratio"] = evidence.get("tier_ratio")
        item["policy_hit1"] = _to_bool(row.get("raw_ltr_hit1")) if allowed and not _to_bool(row.get("gate_allowed")) else _to_bool(row.get("gated_hit1"))
        result.append(item)
    return result


def _split_metrics(
    rows: list[dict[str, Any]],
    original_split_metrics: dict[str, dict[str, Any]],
    *,
    include_splits: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    policies = sorted({row["policy"] for row in rows})
    for policy in policies:
        policy_rows = [row for row in rows if row["policy"] == policy and row["split"] in include_splits]
        for split in sorted({row["split"] for row in policy_rows}):
            subset = [row for row in policy_rows if row["split"] == split]
            base = original_split_metrics.get(split) or {}
            matrix_groups = _to_int(base.get("matrix_groups"))
            eligible = _to_int(base.get("eligible_anchor_rows"))
            gated_hit1 = _to_int(base.get("gated_hit1"))
            current_75_hit1 = _to_int(base.get("whatif_hit1"))

            rescued = sum(1 for row in subset if row["policy_event"] == "rescued_blocked_gain")
            new_loss = sum(1 for row in subset if row["policy_event"] == "new_residual_loss")
            policy_hit1 = gated_hit1 + rescued - new_loss
            original_rescued = sum(1 for row in subset if _clean(row.get("whatif_event")) == "rescued_blocked_gain")
            original_neutral = sum(1 for row in subset if _clean(row.get("whatif_event")) == "allowed_neutral_override")
            neutral = sum(1 for row in subset if row["policy_event"] == "allowed_neutral_override")
            non_sleeve_rescue = sum(
                1
                for row in subset
                if row["policy_event"] == "rescued_blocked_gain" and _clean(row.get("query_family")) != "sleeve"
            )
            result.append(
                {
                    "policy": policy,
                    "split": split,
                    "matrix_groups": matrix_groups,
                    "eligible_anchor_rows": eligible,
                    "gated_hit1": gated_hit1,
                    "stage_7_5_compat_hit1": current_75_hit1,
                    "policy_hit1": policy_hit1,
                    "policy_hit1_rate_matrix": _rate(policy_hit1, matrix_groups),
                    "policy_hit1_rate_eligible": _rate(policy_hit1, eligible),
                    "net_vs_gated": policy_hit1 - gated_hit1,
                    "net_vs_stage_7_5": policy_hit1 - current_75_hit1,
                    "rescued_blocked_gain": rescued,
                    "original_stage_7_5_rescued_blocked_gain": original_rescued,
                    "rescued_retention_vs_stage_7_5": _rate(rescued, original_rescued),
                    "allowed_neutral_override": neutral,
                    "original_stage_7_5_allowed_neutral_override": original_neutral,
                    "neutral_override_reduction": original_neutral - neutral,
                    "neutral_override_reduction_rate": _rate(original_neutral - neutral, original_neutral),
                    "new_residual_loss": new_loss,
                    "still_blocked_gain": sum(1 for row in subset if row["policy_event"] == "still_blocked_gain"),
                    "saved_loss_retained": sum(1 for row in subset if row["policy_event"] == "saved_loss_retained"),
                    "non_sleeve_rescue_count": non_sleeve_rescue,
                    "policy_allowed_count": sum(1 for row in subset if row["policy_allowed"]),
                }
            )
    return result


def _original_split_metrics(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in summary.get("split_metrics") or []:
        split = _clean(row.get("split"))
        if split:
            result[split] = row
    return result


def _selected_oof_gates(selected_oof: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    gates_spec = spec.get("dev_oof_gates") or {}
    original_rescued = _to_int(selected_oof.get("original_stage_7_5_rescued_blocked_gain"))
    retention = _to_float(selected_oof.get("rescued_retention_vs_stage_7_5"))
    neutral_reduction = _to_float(selected_oof.get("neutral_override_reduction_rate"))
    new_loss = _to_int(selected_oof.get("new_residual_loss"))
    non_sleeve_rescue = _to_int(selected_oof.get("non_sleeve_rescue_count"))
    gates = [
        {
            "gate": "rescued_gain_retention",
            "passed": retention >= _to_float(gates_spec.get("min_rescued_retention_vs_stage_7_5")),
            "value": retention,
            "threshold": f">={gates_spec.get('min_rescued_retention_vs_stage_7_5')}",
        },
        {
            "gate": "neutral_override_reduction",
            "passed": neutral_reduction >= _to_float(gates_spec.get("min_neutral_override_reduction_vs_stage_7_5")),
            "value": neutral_reduction,
            "threshold": f">={gates_spec.get('min_neutral_override_reduction_vs_stage_7_5')}",
        },
        {
            "gate": "no_new_residual_loss",
            "passed": new_loss <= _to_int(gates_spec.get("max_new_residual_loss")),
            "value": new_loss,
            "threshold": f"<={gates_spec.get('max_new_residual_loss')}",
        },
        {
            "gate": "keeps_non_sleeve_rescue",
            "passed": non_sleeve_rescue > 0 if gates_spec.get("require_non_sleeve_rescue") else True,
            "value": non_sleeve_rescue,
            "threshold": ">0",
        },
        {
            "gate": "has_stage_7_5_rescue_reference",
            "passed": original_rescued > 0,
            "value": original_rescued,
            "threshold": ">0",
        },
    ]
    return {"passed": all(gate["passed"] for gate in gates), "gates": gates}


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ["policy_event", "policy_reason", "compatibility_relation_id", "family_pair", "query_family", "raw_ltr_top_family", "source_file", "province"]
    totals: Counter[tuple[str, str, str]] = Counter()
    counters: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        key_base = (_clean(row.get("policy")), _clean(row.get("split")), _clean(row.get("policy_event")))
        totals[key_base] += 1
        for field in fields:
            counters[(*key_base, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (policy, split, event, field), counter in sorted(counters.items()):
        total = totals[(policy, split, event)]
        for key, count in counter.most_common(20):
            result.append(
                {
                    "policy": policy,
                    "split": split,
                    "policy_event": event,
                    "bucket": field,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )
    return result


def _examples(rows: list[dict[str, Any]], limit_per_bucket: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str, str]] = Counter()
    interesting = {"rescued_blocked_gain", "allowed_neutral_override", "still_blocked_gain", "new_residual_loss"}
    for row in sorted(
        rows,
        key=lambda item: (
            _clean(item.get("policy")),
            _clean(item.get("split")),
            _clean(item.get("policy_event")),
            _clean(item.get("compatibility_relation_id")),
            _clean(item.get("group_id")),
        ),
    ):
        if row["policy_event"] not in interesting:
            continue
        key = (
            _clean(row.get("policy")),
            _clean(row.get("split")),
            _clean(row.get("policy_event")),
            _clean(row.get("compatibility_relation_id")) or _clean(row.get("family_pair")),
        )
        if seen[key] >= limit_per_bucket:
            continue
        seen[key] += 1
        result.append(row)
    return result


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
    selected = report["selected_policy_summary"]
    lines = [
        "# Goal Family Compatibility Freeze/Narrow What-if",
        "",
        "Stage 7.7 simulates relation freeze/narrow policies over stage 7.5 compatibility rows. No training, no tuning, no search integration, no GoalSearcher change.",
        "",
        "## Selected Policy",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_policy", report["selected_policy"]],
                ["oof_gates_passed", report["oof_gates"]["passed"]],
                ["heldout_hard_status", report["heldout_hard_status"]],
                ["action", report["recommendation"]["action"]],
                ["switch_recommendation", report["recommendation"]["switch_recommendation"]],
                ["dev_oof_rescued_retention", selected.get("rescued_retention_vs_stage_7_5")],
                ["dev_oof_neutral_reduction", selected.get("neutral_override_reduction_rate")],
                ["dev_oof_new_residual_loss", selected.get("new_residual_loss")],
            ]
        ),
        "",
        "## Policy Metrics",
        "",
        _md_table(
            [["policy", "split", "policy_top1_matrix", "net_vs_7_5", "rescued", "retention", "neutral", "neutral_reduction", "new_loss"]]
            + [
                [
                    row["policy"],
                    row["split"],
                    row["policy_hit1_rate_matrix"],
                    row["net_vs_stage_7_5"],
                    row["rescued_blocked_gain"],
                    row["rescued_retention_vs_stage_7_5"],
                    row["allowed_neutral_override"],
                    row["neutral_override_reduction_rate"],
                    row["new_residual_loss"],
                ]
                for row in report["policy_metrics"]
            ]
        ),
        "",
        "## OOF Gates",
        "",
        _md_table([["gate", "passed", "value", "threshold"]] + [[row["gate"], row["passed"], row["value"], row["threshold"]] for row in report["oof_gates"]["gates"]]),
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


def _row_fields() -> list[str]:
    return [
        "policy",
        "split",
        "policy_event",
        "policy_allowed",
        "policy_reason",
        "compatibility_relation_id",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "whatif_event",
        "gate_allowed",
        "baseline_hit1",
        "raw_ltr_hit1",
        "gated_hit1",
        "policy_hit1",
        "query_diameter",
        "candidate_tier",
        "tier_ratio",
        "score_margin",
        "no_param_conflict",
        "query_family_conflict",
        "source_file",
        "province",
        "group_id",
        "sample_id",
        "query",
        "baseline_top_name",
        "raw_ltr_top_name",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.7 eval-only family compatibility relation freeze/narrow what-if")
    parser.add_argument("--whatif-summary", default=str(DEFAULT_WHATIF_SUMMARY))
    parser.add_argument("--whatif-rows", default=str(DEFAULT_WHATIF_ROWS))
    parser.add_argument("--residual-summary", default=str(DEFAULT_RESIDUAL_SUMMARY))
    parser.add_argument("--policy-spec", default=str(DEFAULT_POLICY_SPEC))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--rows-csv", default=str(DEFAULT_ROWS_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--examples-jsonl", default=str(DEFAULT_EXAMPLES_JSONL))
    parser.add_argument("--examples-per-bucket", type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    whatif_summary = _read_json(Path(args.whatif_summary))
    residual_summary = _read_json(Path(args.residual_summary))
    policy_spec = _load_or_create_policy(Path(args.policy_spec))
    policies = _policy_by_name(policy_spec)
    original_metrics = _original_split_metrics(whatif_summary)
    source_rows = _read_csv(Path(args.whatif_rows))

    oof_rows = [row for row in source_rows if _clean(row.get("split")) == "dev_oof"]
    all_policy_rows: list[dict[str, Any]] = []
    for policy_name, policy in policies.items():
        all_policy_rows.extend(_enrich_rows(oof_rows, policy_name, policy, policy_spec))
    oof_metrics = _split_metrics(all_policy_rows, original_metrics, include_splits={"dev_oof"})
    selected_oof = next(row for row in oof_metrics if row["policy"] == SELECTED_POLICY and row["split"] == "dev_oof")
    oof_gates = _selected_oof_gates(selected_oof, policy_spec)

    heldout_hard_status = "skipped_due_to_oof_gate_failure"
    eval_policy_rows: list[dict[str, Any]] = []
    if oof_gates["passed"]:
        eval_rows = [row for row in source_rows if _clean(row.get("split")) in {"heldout", "hard"}]
        for policy_name, policy in policies.items():
            eval_policy_rows.extend(_enrich_rows(eval_rows, policy_name, policy, policy_spec))
        heldout_hard_status = "evaluated_once_after_oof_gates_passed"

    all_rows = all_policy_rows + eval_policy_rows
    policy_metrics = _split_metrics(all_rows, original_metrics, include_splits={row["split"] for row in all_rows})
    bucket_rows = _bucket_rows(all_rows)
    examples = _examples(all_rows, args.examples_per_bucket)
    selected_metrics = [row for row in policy_metrics if row["policy"] == SELECTED_POLICY]
    selected_policy_summary = next(row for row in selected_metrics if row["split"] == "dev_oof")

    recommendation = {
        "action": "keep_eval_only_switch_off_and_prepare_freeze_narrow_review",
        "switch_recommendation": "do_not_connect_eval_only_switch_yet",
        "reason": "Selected policy reduces OOF neutral overrides while retaining most rescued gains, but it still changes ranking decisions and needs relation-level review before any switch skeleton.",
        "frozen_relations": sorted(FREEZE_RELATIONS),
        "narrowed_relations": [NARROW_RELATION],
        "blocked_review_relations": sorted(LOW_SUPPORT_RELATIONS),
    }

    report = {
        "stage": "Goal LTR v1 / stage 7.7 relation freeze/narrow eval-only what-if",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "whatif_summary": str(Path(args.whatif_summary)),
        "whatif_rows": str(Path(args.whatif_rows)),
        "residual_summary": str(Path(args.residual_summary)),
        "policy_spec": str(Path(args.policy_spec)),
        "selected_policy": SELECTED_POLICY,
        "selected_policy_summary": selected_policy_summary,
        "oof_gates": oof_gates,
        "heldout_hard_status": heldout_hard_status,
        "policy_metrics": policy_metrics,
        "recommendation": recommendation,
        "residual_audit_action": ((residual_summary.get("recommendation") or {}).get("action")),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 7.8: review freeze/narrow residual examples; if accepted, write an eval-only switch skeleton that remains default-off.",
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "rows_csv": str(Path(args.rows_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "examples_jsonl": str(Path(args.examples_jsonl)),
            "policy_spec": str(Path(args.policy_spec)),
        },
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.rows_csv), all_rows, _row_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, ["policy", "split", "policy_event", "bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.examples_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "selected_policy": SELECTED_POLICY,
                    "oof_gates_passed": oof_gates["passed"],
                    "heldout_hard_status": heldout_hard_status,
                    "action": recommendation["action"],
                    "switch_recommendation": recommendation["switch_recommendation"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "selected_policy_metrics": selected_metrics,
                "oof_gates": oof_gates,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
