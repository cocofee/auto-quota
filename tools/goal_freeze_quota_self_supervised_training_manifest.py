from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_NARROW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates_narrow.csv"
DEFAULT_FAMILY_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_training_whitelist_family_plan.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_freeze_manifest.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_freeze_summary.md"
DEFAULT_FAMILY_CAPS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_family_caps.csv"
DEFAULT_FAMILY_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_family_whitelist.csv"
DEFAULT_FAMILY_EXCLUSIONS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_family_exclusions.csv"
DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_PAIR_WHITELIST_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.jsonl"
DEFAULT_PAIR_EXCLUSIONS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_exclusions.csv"
DEFAULT_GROUP_TXT = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_group.txt"
DEFAULT_GROUP_META_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_group_meta.jsonl"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_freeze_buckets.csv"

BOTH_DECISIONS = {
    "whitelist_both_pair_types",
    "whitelist_both_subtype_under_target",
    "whitelist_both_param_under_target",
}
SUBTYPE_ONLY_DECISIONS = {
    "whitelist_subtype_only_no_param",
    "review_param_low_support",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _training_mode(decision: str) -> tuple[str, str]:
    if decision in BOTH_DECISIONS:
        return "both", "eligible_param_and_subtype"
    if decision in SUBTYPE_ONLY_DECISIONS:
        return "subtype_only", "param_excluded_first_round"
    return "exclude", "family_not_whitelisted"


def _load_family_plan(path: Path, args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    families: dict[str, dict[str, Any]] = {}
    for row in _read_csv(path):
        family = _clean(row.get("family"))
        if not family:
            continue
        decision = _clean(row.get("whitelist_decision"))
        mode, mode_reason = _training_mode(decision)
        total_pairs = _int(row.get("total_pairs"))
        param_pairs = _int(row.get("param_pairs"))
        subtype_pairs = _int(row.get("subtype_pairs"))
        if mode == "subtype_only":
            family_cap = min(args.max_subtype_only_family_cap, subtype_pairs)
        elif mode == "both":
            family_cap = min(args.max_family_cap, total_pairs)
        else:
            family_cap = 0
        param_target = math.ceil(family_cap * args.target_param_rate)
        subtype_target = max(0, family_cap - param_target)

        if mode == "both":
            param_cap = min(param_pairs, param_target)
            subtype_cap = min(subtype_pairs, subtype_target)
            remaining = max(0, family_cap - param_cap - subtype_cap)
            if remaining and subtype_pairs > subtype_cap:
                add = min(remaining, subtype_pairs - subtype_cap)
                subtype_cap += add
                remaining -= add
            if remaining and param_pairs > param_cap:
                add = min(remaining, param_pairs - param_cap)
                param_cap += add
                remaining -= add
        elif mode == "subtype_only":
            param_cap = 0
            subtype_cap = min(subtype_pairs, family_cap)
        else:
            param_cap = 0
            subtype_cap = 0

        selected_cap = param_cap + subtype_cap
        families[family] = {
            "family": family,
            "training_mode": mode,
            "mode_reason": mode_reason,
            "source_whitelist_decision": decision,
            "source_reason": _clean(row.get("reason")),
            "total_pairs": total_pairs,
            "family_rate": _float(row.get("family_rate")),
            "province_count": _int(row.get("province_count")),
            "param_pairs": param_pairs,
            "subtype_pairs": subtype_pairs,
            "param_rate_within_family": _float(row.get("param_rate_within_family")),
            "subtype_rate_within_family": _float(row.get("subtype_rate_within_family")),
            "param_bucket_count": _int(row.get("param_bucket_count")),
            "family_cap": family_cap,
            "param_target": param_target,
            "subtype_target": subtype_target,
            "param_cap": param_cap,
            "subtype_cap": subtype_cap,
            "selected_cap": selected_cap,
            "cap_fill_rate": _rate(selected_cap, family_cap),
            "selected_param_target_rate": _rate(param_cap, selected_cap),
        }
    return families


def _load_pool(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return list(reader), fieldnames


def _sample_rows(rows: list[dict[str, Any]], target: int, rng: random.Random) -> tuple[list[dict[str, Any]], set[str]]:
    if target <= 0:
        return [], set()
    if len(rows) <= target:
        selected = list(rows)
    else:
        shuffled = list(rows)
        rng.shuffle(shuffled)
        selected = shuffled[:target]
    selected.sort(key=lambda row: (_clean(row.get("province")), _clean(row.get("pair_id"))))
    return selected, {_clean(row.get("pair_id")) for row in selected}


def _freeze(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    rng = random.Random(args.seed)
    family_plan = _load_family_plan(Path(args.family_plan_csv), args)
    pool_rows, pool_fieldnames = _load_pool(Path(args.narrow_csv))
    rows_by_family_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pool_family_counts = Counter()
    pool_pair_type_counts = Counter()
    pool_quality_counts = Counter()

    for row in pool_rows:
        family = _clean(row.get("family"))
        pair_type = _clean(row.get("pair_type"))
        rows_by_family_type[(family, pair_type)].append(row)
        pool_family_counts[family] += 1
        pool_pair_type_counts[pair_type] += 1
        pool_quality_counts[_clean(row.get("quality_bucket"))] += 1

    selected_rows: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    family_caps: list[dict[str, Any]] = []
    family_whitelist: list[dict[str, Any]] = []
    family_exclusions: list[dict[str, Any]] = []

    for family, plan in family_plan.items():
        param_rows = rows_by_family_type.get((family, "param_contrast"), [])
        subtype_rows = rows_by_family_type.get((family, "subtype_contrast"), [])
        selected_param, selected_param_ids = _sample_rows(param_rows, _int(plan["param_cap"]), rng)
        selected_subtype, selected_subtype_ids = _sample_rows(subtype_rows, _int(plan["subtype_cap"]), rng)
        selected_ids.update(selected_param_ids)
        selected_ids.update(selected_subtype_ids)
        selected_rows.extend(selected_param)
        selected_rows.extend(selected_subtype)

        selected_param_count = len(selected_param)
        selected_subtype_count = len(selected_subtype)
        selected_total = selected_param_count + selected_subtype_count
        excluded_param_count = max(0, len(param_rows) - selected_param_count)
        excluded_subtype_count = max(0, len(subtype_rows) - selected_subtype_count)
        cap_row = {
            **plan,
            "pool_total_pairs": len(param_rows) + len(subtype_rows),
            "pool_param_pairs": len(param_rows),
            "pool_subtype_pairs": len(subtype_rows),
            "selected_pairs": selected_total,
            "selected_param_pairs": selected_param_count,
            "selected_subtype_pairs": selected_subtype_count,
            "selected_param_rate": _rate(selected_param_count, selected_total),
            "excluded_pairs": excluded_param_count + excluded_subtype_count,
            "excluded_param_pairs": excluded_param_count,
            "excluded_subtype_pairs": excluded_subtype_count,
        }
        family_caps.append(cap_row)
        if plan["training_mode"] in {"both", "subtype_only"}:
            family_whitelist.append(cap_row)
        else:
            family_exclusions.append({**cap_row, "exclusion_reason": plan["mode_reason"]})

    selected_rows.sort(key=lambda row: (_clean(row.get("family")), _clean(row.get("pair_type")), _clean(row.get("province")), _clean(row.get("pair_id"))))
    pair_whitelist: list[dict[str, Any]] = []
    group_meta: list[dict[str, Any]] = []
    for index, row in enumerate(selected_rows, 1):
        family = _clean(row.get("family"))
        mode = family_plan.get(family, {}).get("training_mode", "unknown")
        group_id = f"quota_selfsup:{index:06d}:{_clean(row.get('pair_id'))}"
        training_row = {
            **row,
            "training_group_id": group_id,
            "training_group_size": 2,
            "training_role": "train_candidate",
            "training_mode": mode,
            "selection_stage": "stage_5_6_freeze",
        }
        pair_whitelist.append(training_row)
        group_meta.append(
            {
                "group_id": group_id,
                "row_count": 2,
                "label_schema": "positive=1,negative=0",
                "family": family,
                "province": _clean(row.get("province")),
                "pair_type": _clean(row.get("pair_type")),
                "contrast_field": _clean(row.get("contrast_field")),
                "positive_id": _clean(row.get("positive_id")),
                "negative_id": _clean(row.get("negative_id")),
                "training_mode": mode,
                "source_pair_id": _clean(row.get("pair_id")),
            }
        )

    pair_exclusions: list[dict[str, Any]] = []
    for row in pool_rows:
        pair_id = _clean(row.get("pair_id"))
        if pair_id in selected_ids:
            continue
        family = _clean(row.get("family"))
        pair_type = _clean(row.get("pair_type"))
        plan = family_plan.get(family, {})
        mode = _clean(plan.get("training_mode"))
        if mode == "exclude":
            reason = "family_excluded"
        elif mode == "subtype_only" and pair_type == "param_contrast":
            reason = "param_excluded_subtype_only_family"
        else:
            reason = f"{pair_type}_cap_overflow"
        pair_exclusions.append(
            {
                "pair_id": pair_id,
                "province": _clean(row.get("province")),
                "family": family,
                "pair_type": pair_type,
                "training_mode": mode,
                "exclusion_reason": reason,
                "positive_id": _clean(row.get("positive_id")),
                "negative_id": _clean(row.get("negative_id")),
                "positive_name": _clean(row.get("positive_name")),
                "negative_name": _clean(row.get("negative_name")),
            }
        )

    selected_pair_type = Counter(_clean(row.get("pair_type")) for row in pair_whitelist)
    selected_family = Counter(_clean(row.get("family")) for row in pair_whitelist)
    selected_mode = Counter(_clean(row.get("training_mode")) for row in pair_whitelist)
    family_mode = Counter(_clean(row.get("training_mode")) for row in family_caps)
    exclusion_reason = Counter(_clean(row.get("exclusion_reason")) for row in pair_exclusions)
    total_selected = len(pair_whitelist)
    selected_param = selected_pair_type.get("param_contrast", 0)
    selected_subtype = selected_pair_type.get("subtype_contrast", 0)

    buckets: list[dict[str, Any]] = []
    for dimension, counter, denominator in (
        ("selected_pair_type", selected_pair_type, total_selected),
        ("selected_family", selected_family, total_selected),
        ("selected_training_mode", selected_mode, total_selected),
        ("family_training_mode", family_mode, len(family_caps)),
        ("pair_exclusion_reason", exclusion_reason, len(pair_exclusions)),
        ("pool_pair_type", pool_pair_type_counts, len(pool_rows)),
        ("pool_quality_bucket", pool_quality_counts, len(pool_rows)),
    ):
        for key, count in counter.most_common():
            buckets.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, denominator)})

    first_round_both_families = [row["family"] for row in family_caps if row["training_mode"] == "both"]
    subtype_only_families = [row["family"] for row in family_caps if row["training_mode"] == "subtype_only"]
    excluded_families = [row["family"] for row in family_caps if row["training_mode"] == "exclude"]
    summary = {
        "stage": "Goal LTR v1 / stage 5.6 quota self-supervised training freeze manifest",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "narrow_csv": str(Path(args.narrow_csv)),
        "family_plan_csv": str(Path(args.family_plan_csv)),
        "seed": args.seed,
        "summary": {
            "pool_pairs": len(pool_rows),
            "selected_pairs": total_selected,
            "selected_param_pairs": selected_param,
            "selected_subtype_pairs": selected_subtype,
            "selected_param_rate": _rate(selected_param, total_selected),
            "excluded_pairs": len(pair_exclusions),
            "family_count": len(family_caps),
            "first_round_both_family_count": len(first_round_both_families),
            "subtype_only_family_count": len(subtype_only_families),
            "excluded_family_count": len(excluded_families),
            "group_count": len(group_meta),
            "group_file_rows": len(group_meta),
            "passes_freeze_gate": (
                total_selected >= args.min_selected_pairs
                and _rate(selected_param, total_selected) >= args.min_selected_param_rate
                and len(first_round_both_families) >= args.min_both_families
                and len(subtype_only_families) >= 1
            ),
            "first_round_both_families": first_round_both_families,
            "subtype_only_families": subtype_only_families,
            "excluded_families": excluded_families,
            "by_selected_pair_type": _counter_items(selected_pair_type, total_selected, args.top_limit),
            "by_selected_family": _counter_items(selected_family, total_selected, args.top_limit),
            "by_family_training_mode": _counter_items(family_mode, len(family_caps), args.top_limit),
            "by_pair_exclusion_reason": _counter_items(exclusion_reason, len(pair_exclusions), args.top_limit),
        },
        "thresholds": {
            "max_family_cap": args.max_family_cap,
            "max_subtype_only_family_cap": args.max_subtype_only_family_cap,
            "target_param_rate": args.target_param_rate,
            "min_selected_pairs": args.min_selected_pairs,
            "min_selected_param_rate": args.min_selected_param_rate,
            "min_both_families": args.min_both_families,
        },
    }
    whitelist_fieldnames = [
        *pool_fieldnames,
        "training_group_id",
        "training_group_size",
        "training_role",
        "training_mode",
        "selection_stage",
    ]
    return summary, family_caps, family_whitelist, family_exclusions, pair_whitelist, pair_exclusions, group_meta, whitelist_fieldnames, buckets


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows = [["key", "count", "rate"]]
    for item in items:
        rows.append([item.get("key", ""), item.get("count", ""), item.get("rate", "")])
    return rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Self-Supervised Training Freeze",
        "",
        "Stage 5.6 eval-only freeze manifest. It writes family caps, first-round family allowlists, pair allowlists, exclusions, and LightGBM-style group files. It does not train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["pool_pairs", summary["pool_pairs"]],
                ["selected_pairs", summary["selected_pairs"]],
                ["selected_param_pairs", summary["selected_param_pairs"]],
                ["selected_subtype_pairs", summary["selected_subtype_pairs"]],
                ["selected_param_rate", summary["selected_param_rate"]],
                ["excluded_pairs", summary["excluded_pairs"]],
                ["first_round_both_family_count", summary["first_round_both_family_count"]],
                ["subtype_only_family_count", summary["subtype_only_family_count"]],
                ["excluded_family_count", summary["excluded_family_count"]],
                ["group_count", summary["group_count"]],
                ["passes_freeze_gate", summary["passes_freeze_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## First Round Both",
        "",
        ", ".join(summary["first_round_both_families"]),
        "",
        "## Subtype Only",
        "",
        ", ".join(summary["subtype_only_families"]),
        "",
        "## Selected Pair Type",
        "",
        _md_table(_counter_table(summary["by_selected_pair_type"])),
        "",
        "## Family Mode",
        "",
        _md_table(_counter_table(summary["by_family_training_mode"])),
        "",
        "## Pair Exclusions",
        "",
        _md_table(_counter_table(summary["by_pair_exclusion_reason"])),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["family_caps_csv", report["artifacts"]["family_caps_csv"]],
                ["family_whitelist_csv", report["artifacts"]["family_whitelist_csv"]],
                ["family_exclusions_csv", report["artifacts"]["family_exclusions_csv"]],
                ["pair_whitelist_csv", report["artifacts"]["pair_whitelist_csv"]],
                ["pair_whitelist_jsonl", report["artifacts"]["pair_whitelist_jsonl"]],
                ["pair_exclusions_csv", report["artifacts"]["pair_exclusions_csv"]],
                ["group_txt", report["artifacts"]["group_txt"]],
                ["group_meta_jsonl", report["artifacts"]["group_meta_jsonl"]],
                ["buckets_csv", report["artifacts"]["buckets_csv"]],
                ["manifest_json", report["artifacts"]["manifest_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.6 eval-only self-supervised training freeze manifest")
    parser.add_argument("--narrow-csv", default=str(DEFAULT_NARROW_CSV))
    parser.add_argument("--family-plan-csv", default=str(DEFAULT_FAMILY_PLAN_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--family-caps-csv", default=str(DEFAULT_FAMILY_CAPS_CSV))
    parser.add_argument("--family-whitelist-csv", default=str(DEFAULT_FAMILY_WHITELIST_CSV))
    parser.add_argument("--family-exclusions-csv", default=str(DEFAULT_FAMILY_EXCLUSIONS_CSV))
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--pair-whitelist-jsonl", default=str(DEFAULT_PAIR_WHITELIST_JSONL))
    parser.add_argument("--pair-exclusions-csv", default=str(DEFAULT_PAIR_EXCLUSIONS_CSV))
    parser.add_argument("--group-txt", default=str(DEFAULT_GROUP_TXT))
    parser.add_argument("--group-meta-jsonl", default=str(DEFAULT_GROUP_META_JSONL))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument("--max-family-cap", type=int, default=4000)
    parser.add_argument("--max-subtype-only-family-cap", type=int, default=2000)
    parser.add_argument("--target-param-rate", type=float, default=0.30)
    parser.add_argument("--min-selected-pairs", type=int, default=50_000)
    parser.add_argument("--min-selected-param-rate", type=float, default=0.20)
    parser.add_argument("--min-both-families", type=int, default=8)
    parser.add_argument("--top-limit", type=int, default=30)
    args = parser.parse_args()

    started = time.perf_counter()
    (
        report,
        family_caps,
        family_whitelist,
        family_exclusions,
        pair_whitelist,
        pair_exclusions,
        group_meta,
        whitelist_fieldnames,
        buckets,
    ) = _freeze(args)
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)
    report["artifacts"] = {
        "manifest_json": str(Path(args.report_json)),
        "summary_md": str(Path(args.report_md)),
        "family_caps_csv": str(Path(args.family_caps_csv)),
        "family_whitelist_csv": str(Path(args.family_whitelist_csv)),
        "family_exclusions_csv": str(Path(args.family_exclusions_csv)),
        "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
        "pair_whitelist_jsonl": str(Path(args.pair_whitelist_jsonl)),
        "pair_exclusions_csv": str(Path(args.pair_exclusions_csv)),
        "group_txt": str(Path(args.group_txt)),
        "group_meta_jsonl": str(Path(args.group_meta_jsonl)),
        "buckets_csv": str(Path(args.buckets_csv)),
    }

    family_fields = [
        "family",
        "training_mode",
        "mode_reason",
        "source_whitelist_decision",
        "source_reason",
        "total_pairs",
        "province_count",
        "param_pairs",
        "subtype_pairs",
        "param_bucket_count",
        "family_cap",
        "param_target",
        "subtype_target",
        "param_cap",
        "subtype_cap",
        "selected_cap",
        "cap_fill_rate",
        "selected_param_target_rate",
        "pool_total_pairs",
        "pool_param_pairs",
        "pool_subtype_pairs",
        "selected_pairs",
        "selected_param_pairs",
        "selected_subtype_pairs",
        "selected_param_rate",
        "excluded_pairs",
        "excluded_param_pairs",
        "excluded_subtype_pairs",
    ]
    _write_csv(Path(args.family_caps_csv), family_caps, family_fields)
    _write_csv(Path(args.family_whitelist_csv), family_whitelist, family_fields)
    _write_csv(Path(args.family_exclusions_csv), family_exclusions, [*family_fields, "exclusion_reason"])
    _write_csv(Path(args.pair_whitelist_csv), pair_whitelist, whitelist_fieldnames)
    _write_jsonl(Path(args.pair_whitelist_jsonl), pair_whitelist)
    _write_csv(
        Path(args.pair_exclusions_csv),
        pair_exclusions,
        [
            "pair_id",
            "province",
            "family",
            "pair_type",
            "training_mode",
            "exclusion_reason",
            "positive_id",
            "negative_id",
            "positive_name",
            "negative_name",
        ],
    )
    Path(args.group_txt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.group_txt).write_text("\n".join("2" for _ in group_meta) + "\n", encoding="utf-8")
    _write_jsonl(Path(args.group_meta_jsonl), group_meta)
    _write_csv(Path(args.buckets_csv), buckets, ["dimension", "key", "count", "rate"])
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "selected_pairs": report["summary"]["selected_pairs"],
                    "selected_param_pairs": report["summary"]["selected_param_pairs"],
                    "selected_param_rate": report["summary"]["selected_param_rate"],
                    "first_round_both_family_count": report["summary"]["first_round_both_family_count"],
                    "subtype_only_family_count": report["summary"]["subtype_only_family_count"],
                    "excluded_pairs": report["summary"]["excluded_pairs"],
                    "group_count": report["summary"]["group_count"],
                    "passes_freeze_gate": report["summary"]["passes_freeze_gate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
