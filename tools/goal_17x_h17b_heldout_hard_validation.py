from __future__ import annotations

import argparse
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
from src.goal_search.oss_recall_prior import reset_oss_recall_prior_source
from src.goal_search.searcher import clear_goal_search_cache
from tools.goal_16x_local_assets_guarded_alias_ab_validation import (
    DEFAULT_DB_DIR,
    DEFAULT_HARD,
    DEFAULT_HELDOUT,
    _configure_db_root,
    _evaluate_split,
    _read_jsonl,
    _scorecard,
    _write_csv,
    _write_json,
)
from tools.goal_17x_precision_hardening_dev_oof_shadow import _candidate_spec, _configure_candidate


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"
DEFAULT_FREEZE = AGENT_STATE / "goal_17x_h17b_freeze_gate_validation_boundary_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_h17b_heldout_hard_validation"


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _family_signal(scorecard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in scorecard:
        name = str(row.get("slice", ""))
        if not name.startswith("family:"):
            continue
        generated = _safe_int(row.get("prior_generated_candidates"))
        if generated == 0 and _safe_int(row.get("delta_top1")) == 0 and _safe_int(row.get("delta_top5")) == 0:
            continue
        rows.append(
            {
                "slice": name,
                "groups": row.get("groups", 0),
                "delta_top1": row.get("delta_top1", 0),
                "delta_top5": row.get("delta_top5", 0),
                "delta_top80": row.get("delta_top80", 0),
                "top1_wins": row.get("top1_wins", 0),
                "top1_losses": row.get("top1_losses", 0),
                "prior_generated_candidates": row.get("prior_generated_candidates", 0),
                "prior_positive_candidates": row.get("prior_positive_candidates", 0),
                "prior_false_candidates": row.get("prior_false_candidates", 0),
                "prior_false_candidate_rate": row.get("prior_false_candidate_rate", 0),
            }
        )
    return rows


def _stop_conditions(all_head: dict[str, Any], scorecard: list[dict[str, Any]], resolved_provinces: int, db_dir: Path) -> list[dict[str, str]]:
    taxonomy_generated = sum(_safe_int(row["prior_generated_candidates"]) for row in scorecard if row["slice"] == "taxonomy_empty")
    false_dominant = _safe_int(all_head["prior_false_candidates"]) > _safe_int(all_head["prior_positive_candidates"])
    pipe = next((row for row in scorecard if row.get("slice") == "family:pipe"), {})
    return [
        {"check": "validation_substrate", "status": "pass", "evidence": f"resolved_provinces={resolved_provinces}, db_dir={db_dir}"},
        {"check": "frozen_candidate_contract", "status": "pass", "evidence": "candidate=H17_B; core=concrete,pipe,pump,rebar; pipe strict evidence gate enabled"},
        {"check": "taxonomy_empty_block", "status": "pass" if taxonomy_generated == 0 else "fail", "evidence": f"taxonomy_empty prior_generated_candidates={taxonomy_generated}"},
        {"check": "top1_loss_guard", "status": "pass" if _safe_int(all_head["top1_losses"]) == 0 else "fail", "evidence": f"top1_losses={all_head['top1_losses']}, top1_wins={all_head['top1_wins']}"},
        {"check": "top1_positive_net", "status": "pass" if _safe_int(all_head["delta_top1"]) > 0 else "fail", "evidence": f"delta_top1={all_head['delta_top1']}"},
        {"check": "top80_positive_net", "status": "pass" if _safe_int(all_head["delta_top80"]) > 0 else "fail", "evidence": f"delta_top80={all_head['delta_top80']}"},
        {"check": "false_candidate_dominance", "status": "fail" if false_dominant else "pass", "evidence": f"false={all_head['prior_false_candidates']}, positive={all_head['prior_positive_candidates']}"},
        {"check": "pipe_branch_loss", "status": "pass" if _safe_int(pipe.get("top1_losses")) == 0 else "fail", "evidence": f"pipe_top1_losses={pipe.get('top1_losses', 0)}; pipe_false_rate={pipe.get('prior_false_candidate_rate', 0)}"},
        {"check": "default_off_boundary", "status": "pass", "evidence": "config changed in-process only; no online default change"},
    ]


def _decision(stop_conditions: list[dict[str, str]]) -> str:
    failed = [row["check"] for row in stop_conditions if row["status"] == "fail"]
    if failed:
        return "validation_stop_do_not_release_h17b"
    return "validation_pass_request_post_validation_integration_review"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    all_head = report["headline"]["all"]
    lines = [
        "# 17.13 H17_B Heldout/Hard Validation",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Result",
        "",
        f"- heldout Top1/Top5/Top20/Top80: `{heldout['delta_top1']}/{heldout['delta_top5']}/{heldout['delta_top20']}/{heldout['delta_top80']}`; wins/losses `{heldout['top1_wins']}/{heldout['top1_losses']}`.",
        f"- hard Top1/Top5/Top20/Top80: `{hard['delta_top1']}/{hard['delta_top5']}/{hard['delta_top20']}/{hard['delta_top80']}`; wins/losses `{hard['top1_wins']}/{hard['top1_losses']}`.",
        f"- all Top1/Top5/Top20/Top80: `{all_head['delta_top1']}/{all_head['delta_top5']}/{all_head['delta_top20']}/{all_head['delta_top80']}`; wins/losses `{all_head['top1_wins']}/{all_head['top1_losses']}`.",
        f"- generated/positive/false: `{all_head['prior_generated_candidates']}/{all_head['prior_positive_candidates']}/{all_head['prior_false_candidates']}`; false rate `{all_head['prior_false_candidate_rate']}`.",
        "",
        "## Stop Conditions",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    for row in report["stop_conditions"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    lines.extend(["", "## Family Signal", "", "| family | Top1 | Top5 | Top80 | wins/losses | generated/positive/false | false rate |", "|---|---:|---:|---:|---|---|---:|"])
    for row in report["family_signal_rows"]:
        lines.append(
            f"| {row['slice']} | {row['delta_top1']} | {row['delta_top5']} | {row['delta_top80']} | "
            f"{row['top1_wins']}/{row['top1_losses']} | {row['prior_generated_candidates']}/{row['prior_positive_candidates']}/{row['prior_false_candidates']} | {row['prior_false_candidate_rate']} |"
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.13 heldout/hard validation for frozen H17_B only")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--limit-per-split", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    freeze = json.loads(args.freeze_summary.read_text(encoding="utf-8"))
    if freeze.get("decision") != "freeze_h17b_for_validation_request_boundary":
        raise ValueError(f"H17_B is not frozen for validation: {freeze.get('decision')}")

    _configure_db_root(args.db_dir)
    heldout_input = _read_jsonl(args.heldout)
    hard_input = _read_jsonl(args.hard)
    if args.limit_per_split > 0:
        heldout_input = heldout_input[: args.limit_per_split]
        hard_input = hard_input[: args.limit_per_split]
    provinces = sorted({clean_text(row.get("province")) for row in heldout_input + hard_input if clean_text(row.get("province"))})
    province_cache = {province: config.resolve_province(province) for province in provinces}

    spec = _candidate_spec("H17_B")
    source = _configure_candidate(spec, args.index)
    heldout_rows, heldout_scorecard = _evaluate_split(
        "heldout",
        heldout_input,
        source,
        "recall",
        progress_every=args.progress_every,
        province_cache=province_cache,
    )
    hard_rows, hard_scorecard = _evaluate_split(
        "hard",
        hard_input,
        source,
        "recall",
        progress_every=args.progress_every,
        province_cache=province_cache,
    )
    all_rows = heldout_rows + hard_rows
    all_scorecard = _scorecard(all_rows)
    heldout_head = next(row for row in heldout_scorecard if row["slice"] == "all")
    hard_head = next(row for row in hard_scorecard if row["slice"] == "all")
    all_head = next(row for row in all_scorecard if row["slice"] == "all")
    score_rows = []
    for split, rows in (("heldout", heldout_scorecard), ("hard", hard_scorecard), ("all", all_scorecard)):
        for row in rows:
            score_rows.append({"split": split, **row})
    stop_conditions = _stop_conditions(all_head, all_scorecard, len(provinces), args.db_dir)
    decision = _decision(stop_conditions)
    if decision.startswith("validation_pass"):
        interpretation = (
            "Frozen H17_B passed the heldout/hard validation stop conditions. It is still not default-enabled; next step is a post-validation integration/release gate."
        )
    else:
        interpretation = (
            "Frozen H17_B did not pass heldout/hard validation. Keep it default-off and return to precision hardening or a safer candidate."
        )

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    family_csv = args.output_prefix.with_name(args.output_prefix.name + "_family_signal.csv")
    family_rows = _family_signal(all_scorecard)
    report = {
        "stage": "17.13 frozen H17_B heldout/hard validation",
        "decision": decision,
        "validation_authorized": True,
        "frozen_candidate": "H17_B",
        "contract": {
            "top_k": 3,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": ["concrete", "pipe", "pump", "rebar"],
            "pipe_strict_evidence_gate": "exact_name OR source_family>=2 + quota_specific_overlap>=2 + quota_name_overlap>=1",
        },
        "smoke_limit_per_split": args.limit_per_split,
        "db_dir": str(args.db_dir),
        "resolved_validation_provinces": len(provinces),
        "trained": False,
        "tuned": False,
        "online_default_changed": False,
        "headline": {"heldout": heldout_head, "hard": hard_head, "all": all_head},
        "scorecard": {"heldout": heldout_scorecard, "hard": hard_scorecard, "all": all_scorecard},
        "stop_conditions": stop_conditions,
        "family_signal_rows": family_rows,
        "interpretation": interpretation,
        "next_stage": "17.14 post-validation integration/release gate" if decision.startswith("validation_pass") else "17.14 validation-failed closure / strategy return",
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "stop_conditions_csv": str(stop_csv),
            "family_signal_csv": str(family_csv),
        },
        "anti_drift_conclusion": (
            "17.13 ran only the explicitly authorized heldout/hard A/B validation for frozen H17_B. "
            "It did not train, tune from validation, change thresholds, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(scorecard_csv, score_rows, list(score_rows[0].keys()) if score_rows else [])
    _write_csv(row_csv, all_rows, list(all_rows[0].keys()) if all_rows else [])
    _write_csv(stop_csv, stop_conditions, ["check", "status", "evidence"])
    _write_csv(family_csv, family_rows, list(family_rows[0].keys()) if family_rows else [])
    config.OSS_RECALL_INDEX_ENABLED = False
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    print(json.dumps({"summary": str(summary_json), "decision": decision, "headline": report["headline"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
