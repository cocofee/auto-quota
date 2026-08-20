from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.national_index import clean_text
from src.goal_search.oss_recall_prior import OssRecallPriorSource, reset_oss_recall_prior_source
from src.goal_search.searcher import clear_goal_search_cache
from tools.goal_16x_local_assets_guarded_alias_ab_validation import (
    DEFAULT_DB_DIR,
    _configure_db_root,
    _evaluate_split,
    _read_jsonl,
    _write_csv,
    _write_json,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_v2_1.jsonl"
DEFAULT_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_v2_1_dev_oof_shadow"
CORE_FAMILIES = {"pump", "rebar"}


def _split_expected_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        return [clean_text(item) for item in value.split("|") if clean_text(item)]
    return []


def _normalize_oof_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _read_jsonl(path):
        if clean_text(row.get("variant")) != "baseline_only":
            continue
        out = dict(row)
        out["anchor_group_id"] = clean_text(out.get("anchor_group_id") or out.get("group_id"))
        if not clean_text(out.get("bill_name") or out.get("name")):
            out["bill_name"] = clean_text(out.get("query"))
        out["expected_ids"] = _split_expected_ids(out.get("expected_ids"))
        if out["expected_ids"]:
            rows.append(out)
    rows.sort(key=lambda row: clean_text(row.get("anchor_group_id")))
    return rows


def _read_index_family_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                counts[clean_text(json.loads(line).get("query_family"))] += 1
    return counts


def _headline(scorecard: list[dict[str, Any]]) -> dict[str, Any]:
    return next(row for row in scorecard if row["slice"] == "all")


def _slice_rows(scorecard: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    return [row for row in scorecard if str(row.get("slice", "")).startswith(prefix)]


def _source_file_robustness(row_audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, Counter[str]] = {}
    for row in row_audit:
        source_file = clean_text(row.get("source_file")) or "<empty>"
        counter = grouped.setdefault(source_file, Counter())
        counter["groups"] += 1
        counter["delta_top1"] += int(row["treatment_top1"]) - int(row["baseline_top1"])
        counter["delta_top5"] += int(row["treatment_top5"]) - int(row["baseline_top5"])
        counter["top1_wins"] += int(row["top1_win"])
        counter["top1_losses"] += int(row["top1_loss"])
        counter["prior_generated_candidates"] += int(row["prior_generated_candidates"])
        counter["prior_positive_candidates"] += int(row["prior_positive_candidates"])
        counter["prior_false_candidates"] += int(row["prior_false_candidates"])
    rows = []
    for source_file, counter in grouped.items():
        generated = int(counter["prior_generated_candidates"])
        rows.append(
            {
                "source_file": source_file,
                "groups": int(counter["groups"]),
                "delta_top1": int(counter["delta_top1"]),
                "delta_top5": int(counter["delta_top5"]),
                "top1_wins": int(counter["top1_wins"]),
                "top1_losses": int(counter["top1_losses"]),
                "prior_generated_candidates": generated,
                "prior_positive_candidates": int(counter["prior_positive_candidates"]),
                "prior_false_candidates": int(counter["prior_false_candidates"]),
                "prior_false_candidate_rate": round(counter["prior_false_candidates"] / generated, 6) if generated else 0.0,
            }
        )
    rows.sort(key=lambda row: (-int(row["prior_generated_candidates"]), row["source_file"]))
    return rows


def _stop_conditions(headline: dict[str, Any], family_slices: list[dict[str, Any]], source_robustness: list[dict[str, Any]]) -> list[dict[str, str]]:
    generated = int(headline.get("prior_generated_candidates") or 0)
    positive = int(headline.get("prior_positive_candidates") or 0)
    false = int(headline.get("prior_false_candidates") or 0)
    top1_losses = int(headline.get("top1_losses") or 0)
    source_generated = [row for row in source_robustness if int(row.get("prior_generated_candidates") or 0) > 0]
    max_source_share = 0.0
    if generated and source_generated:
        max_source_share = max(int(row["prior_generated_candidates"]) for row in source_generated) / generated
    positive_families = [
        row["slice"]
        for row in family_slices
        if int(row.get("prior_positive_candidates") or 0) > 0
    ]
    return [
        {"check": "dev_oof_only", "status": "pass", "evidence": "input=dev_oof_safety_gate_details baseline_only rows"},
        {"check": "top1_loss_guard", "status": "pass" if top1_losses == 0 else "fail", "evidence": f"top1_losses={top1_losses}"},
        {
            "check": "positive_movement",
            "status": "pass" if int(headline.get("delta_top1") or 0) > 0 or int(headline.get("delta_top5") or 0) > 0 or int(headline.get("delta_top80") or 0) > 0 else "fail",
            "evidence": f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}; delta_top80={headline.get('delta_top80')}",
        },
        {"check": "false_candidate_dominance", "status": "fail" if false > positive else "pass", "evidence": f"false={false}; positive={positive}"},
        {
            "check": "family_slice_reporting",
            "status": "pass" if family_slices else "fail",
            "evidence": f"families={','.join(str(row['slice']) for row in family_slices)}; positive_families={','.join(positive_families) or '<none>'}",
        },
        {
            "check": "source_file_not_single_dominant",
            "status": "pass" if max_source_share <= 0.70 else "fail",
            "evidence": f"max_source_generated_share={round(max_source_share, 6)}",
        },
        {"check": "no_heldout_hard_training_runtime", "status": "pass", "evidence": "shadow harness used dev/OOF only; no training/runtime/default/GoalSearcher edits"},
    ]


def _decision(stop_conditions: list[dict[str, str]]) -> str:
    failed = [row["check"] for row in stop_conditions if row["status"] != "pass"]
    if failed:
        return "v2_1_dev_oof_shadow_completed_no_freeze_failed_" + "_".join(failed)
    return "v2_1_dev_oof_shadow_pass_request_scorecard_loss_review"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    lines = [
        "# 17.33 v2.1 Dev/OOF Shadow",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Headline",
        "",
        f"- groups: `{h['groups']}`",
        f"- Top1/Top5/Top20/Top80 delta: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`",
        f"- Top1 wins/losses: `{h['top1_wins']}/{h['top1_losses']}`",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`",
        f"- false candidate rate: `{h['prior_false_candidate_rate']}`",
        "",
        "## Family Slices",
        "",
        "| slice | groups | Top1 | Top5 | Top20 | Top80 | generated/positive/false |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["family_slices"]:
        lines.append(
            f"| {row['slice']} | {row['groups']} | {row['delta_top1']} | {row['delta_top5']} | {row['delta_top20']} | {row['delta_top80']} | "
            f"{row['prior_generated_candidates']}/{row['prior_positive_candidates']}/{row['prior_false_candidates']} |"
        )
    lines.extend(["", "## Stop Conditions", "", "| check | status | evidence |", "|---|---|---|"])
    for row in report["stop_conditions"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.33 v2.1 dev/OOF-only OSS recall shadow")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--candidate", choices=("all",), default="all")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    rows = _normalize_oof_rows(args.oof)
    family_counts = _read_index_family_counts(args.index)

    config.OSS_RECALL_INDEX_PATH = str(args.index)
    config.OSS_RECALL_INDEX_TOP_K = 3
    config.OSS_RECALL_INDEX_MIN_SUPPORT = 2
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = 1
    config.OSS_RECALL_INDEX_MIN_OVERLAP = 2
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = "broad"
    config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(CORE_FAMILIES))
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    source = OssRecallPriorSource(
        args.index,
        min_support=2,
        min_source_families=1,
        min_overlap=2,
        intervention_mode="broad",
        core_families=set(CORE_FAMILIES),
    )

    row_audit, scorecard = _evaluate_split(
        "dev_oof_17x_v2_1_shadow",
        rows,
        source,
        "recall",
        progress_every=args.progress_every,
        province_cache={},
    )
    headline = _headline(scorecard)
    family_slices = _slice_rows(scorecard, "family:")
    bucket_slices = _slice_rows(scorecard, "bucket:")
    source_robustness = _source_file_robustness(row_audit)
    stops = _stop_conditions(headline, family_slices, source_robustness)
    decision = _decision(stops)

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    source_csv = args.output_prefix.with_name(args.output_prefix.name + "_source_file_robustness.csv")
    bucket_csv = args.output_prefix.with_name(args.output_prefix.name + "_bucket_slices.csv")

    report = {
        "stage": "17.33 v2.1 dev/OOF shadow",
        "decision": decision,
        "rows_evaluated": len(rows),
        "index": str(args.index),
        "index_family_counts": dict(family_counts),
        "contract": {
            "top_k": 3,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": sorted(CORE_FAMILIES),
        },
        "headline": headline,
        "family_slices": family_slices,
        "bucket_slices": bucket_slices,
        "source_file_robustness_top": source_robustness[:20],
        "stop_conditions": stops,
        "execution_performed": True,
        "training_performed": False,
        "heldout_hard_used": False,
        "runtime_changed": False,
        "default_enable_allowed": False,
        "goal_searcher_changed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "stop_conditions_csv": str(stop_csv),
            "source_file_robustness_csv": str(source_csv),
            "bucket_slices_csv": str(bucket_csv),
        },
        "anti_drift_conclusion": (
            "17.33 ran only dev/OOF baseline_only shadow with the separate v2.1 default-off OSS recall artifact. "
            "It did not train, tune, read heldout/hard, default-enable OSS recall, integrate online behavior, overwrite v1/v2 artifacts, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(scorecard_csv, scorecard, list(headline.keys()))
    _write_csv(row_csv, row_audit, list(row_audit[0].keys()) if row_audit else ["split", "row_ordinal"])
    _write_csv(stop_csv, stops, ["check", "status", "evidence"])
    _write_csv(
        source_csv,
        source_robustness,
        [
            "source_file",
            "groups",
            "delta_top1",
            "delta_top5",
            "top1_wins",
            "top1_losses",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "prior_false_candidate_rate",
        ],
    )
    _write_csv(bucket_csv, bucket_slices, list(headline.keys()))
    config.OSS_RECALL_INDEX_ENABLED = False
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    print(json.dumps({"summary": str(summary_json), "decision": decision, "headline": headline}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
