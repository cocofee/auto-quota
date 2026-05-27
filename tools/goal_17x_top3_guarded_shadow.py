from __future__ import annotations

import argparse
import csv
import json
import sys
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
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"
DEFAULT_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_BROAD_AUDIT = AGENT_STATE / "goal_17x_oss_multifield_dev_oof_shadow_row_audit.csv"
DEFAULT_BROAD_SUMMARY = AGENT_STATE / "goal_17x_oss_multifield_dev_oof_shadow_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_top3_guarded_dev_oof_shadow"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _split_expected_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        return [clean_text(item) for item in value.split("|") if clean_text(item)]
    return []


def _normalize_oof_rows(path: Path, group_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _read_jsonl(path):
        if clean_text(row.get("variant")) != "baseline_only":
            continue
        group_id = clean_text(row.get("anchor_group_id") or row.get("group_id"))
        if group_id not in group_ids:
            continue
        out = dict(row)
        out["anchor_group_id"] = group_id
        if not clean_text(out.get("bill_name") or out.get("name")):
            out["bill_name"] = clean_text(out.get("query"))
        out["expected_ids"] = _split_expected_ids(out.get("expected_ids"))
        if out["expected_ids"]:
            rows.append(out)
    rows.sort(key=lambda row: clean_text(row.get("anchor_group_id")))
    return rows


def _head(summary: dict[str, Any]) -> dict[str, Any]:
    return dict(summary.get("headline") or {})


def _delta_row(metric: str, broad: dict[str, Any], guarded: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric": metric,
        "broad": broad.get(metric, 0),
        "top3_guarded": guarded.get(metric, 0),
        "delta_vs_broad": guarded.get(metric, 0) - broad.get(metric, 0),
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    b = report["broad_headline"]
    lines = [
        "# 17.4 Top3 Precision-Guarded Dev/OOF Shadow",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "The top3_per_row guard was run on the same 29 impacted OOF rows from 17.2. This is still offline shadow only.",
        "",
        "## Result",
        "",
        "| metric | 17.2 broad | 17.4 top3 guarded | delta vs broad |",
        "|---|---:|---:|---:|",
    ]
    for metric in (
        "delta_top1",
        "delta_top5",
        "delta_top20",
        "delta_top80",
        "top1_wins",
        "top1_losses",
        "top80_gains",
        "top80_losses",
        "prior_generated_candidates",
        "prior_positive_candidates",
        "prior_false_candidates",
    ):
        lines.append(f"| {metric} | {b.get(metric, 0)} | {h.get(metric, 0)} | {h.get(metric, 0) - b.get(metric, 0)} |")
    lines.extend(
        [
            f"| prior_false_candidate_rate | {b.get('prior_false_candidate_rate', 0)} | {h.get('prior_false_candidate_rate', 0)} | {round(h.get('prior_false_candidate_rate', 0) - b.get('prior_false_candidate_rate', 0), 6)} |",
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.4 top3 precision-guarded OOF shadow on known impacted rows")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--broad-row-audit", type=Path, default=DEFAULT_BROAD_AUDIT)
    parser.add_argument("--broad-summary", type=Path, default=DEFAULT_BROAD_SUMMARY)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    group_ids = {clean_text(row.get("anchor_group_id")) for row in _read_csv(args.broad_row_audit) if clean_text(row.get("anchor_group_id"))}
    rows = _normalize_oof_rows(args.oof, group_ids)

    core_families = {"concrete", "pipe", "pump", "rebar", "support"}
    config.OSS_RECALL_INDEX_PATH = str(args.index)
    config.OSS_RECALL_INDEX_TOP_K = 3
    config.OSS_RECALL_INDEX_MIN_SUPPORT = 2
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = 1
    config.OSS_RECALL_INDEX_MIN_OVERLAP = 2
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = "broad"
    config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(core_families))
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    source = OssRecallPriorSource(
        args.index,
        min_support=2,
        min_source_families=1,
        min_overlap=2,
        intervention_mode="broad",
        core_families=core_families,
    )

    row_audit, scorecard = _evaluate_split(
        "dev_oof_17x_top3_guarded_baseline_only",
        rows,
        source,
        "recall",
        progress_every=args.progress_every,
        province_cache={},
    )
    headline = next(row for row in scorecard if row["slice"] == "all")
    broad_summary = json.loads(args.broad_summary.read_text(encoding="utf-8"))
    broad_headline = _head(broad_summary)

    false_rate_pass = float(headline.get("prior_false_candidate_rate", 0)) < 0.85
    movement_pass = int(headline.get("delta_top1", 0)) > 0 and int(headline.get("delta_top5", 0)) > 0
    top1_loss_pass = int(headline.get("top1_losses", 0)) == 0
    if false_rate_pass and movement_pass and top1_loss_pass:
        decision = "top3_guarded_shadow_pass_continue_to_guarded_harness_scope"
        interpretation = (
            "Top3 preserved positive Top1/Top5 movement, kept Top1 losses at zero, and reduced false candidate rate below the 0.85 stop threshold. "
            "It is still not a release candidate; the next step is a scoped default-off guarded harness, not online enablement."
        )
    else:
        decision = "top3_guarded_shadow_not_ready_redesign_again"
        interpretation = (
            "Top3 did not satisfy all stop conditions. Continue redesign before any implementation or validation gate."
        )

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    compare_csv = args.output_prefix.with_name(args.output_prefix.name + "_broad_comparison.csv")
    compare_rows = [
        _delta_row(metric, broad_headline, headline)
        for metric in (
            "delta_top1",
            "delta_top5",
            "delta_top20",
            "delta_top80",
            "top1_wins",
            "top1_losses",
            "top80_gains",
            "top80_losses",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
        )
    ]
    compare_rows.append(
        {
            "metric": "prior_false_candidate_rate",
            "broad": broad_headline.get("prior_false_candidate_rate", 0),
            "top3_guarded": headline.get("prior_false_candidate_rate", 0),
            "delta_vs_broad": round(
                float(headline.get("prior_false_candidate_rate", 0)) - float(broad_headline.get("prior_false_candidate_rate", 0)),
                6,
            ),
        }
    )
    stop_conditions = [
        {"gate": "top1_loss_guard", "status": "pass" if top1_loss_pass else "fail", "evidence": f"top1_losses={headline.get('top1_losses')}"},
        {"gate": "top1_top5_movement", "status": "pass" if movement_pass else "fail", "evidence": f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}"},
        {"gate": "false_rate_below_0_85", "status": "pass" if false_rate_pass else "fail", "evidence": f"false_rate={headline.get('prior_false_candidate_rate')}"},
        {"gate": "no_heldout_hard", "status": "pass", "evidence": "dev/OOF known impacted rows only"},
        {"gate": "online_default_unchanged", "status": "pass", "evidence": "config changed in-process only"},
    ]
    report = {
        "stage": "17.4 top3 precision-guarded dev/OOF shadow",
        "decision": decision,
        "rows_evaluated": len(rows),
        "guard": "top3_per_row",
        "candidate": {
            "top_k": 3,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": sorted(core_families),
        },
        "headline": headline,
        "broad_headline": broad_headline,
        "comparison": compare_rows,
        "stop_conditions": stop_conditions,
        "interpretation": interpretation,
        "trained": False,
        "tuned": False,
        "heldout_hard_used": False,
        "online_default_changed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "comparison_csv": str(compare_csv),
        },
        "anti_drift_conclusion": (
            "17.4 ran only a dev/OOF shadow replay on the known 17.2 impacted rows with TopK=3. "
            "It did not train, tune, use heldout/hard, enable online behavior, overwrite 16.x artifacts, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(scorecard_csv, scorecard, list(headline.keys()))
    _write_csv(row_csv, row_audit, list(row_audit[0].keys()) if row_audit else ["split", "row_ordinal"])
    _write_csv(compare_csv, compare_rows, ["metric", "broad", "top3_guarded", "delta_vs_broad"])
    print(json.dumps({"summary": str(summary_json), "decision": decision, "headline": headline}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
