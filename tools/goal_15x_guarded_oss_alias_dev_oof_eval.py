from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.oss_alias_prior import GuardedOssAliasPriorSource


DEFAULT_MATRIX_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_INDEX = Path(getattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", PROJECT_ROOT / "data" / "goal_search" / "guarded_oss_alias_index.jsonl"))
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_15x_guarded_oss_alias_dev_oof_eval"
DEFAULT_STATUS = PROJECT_ROOT / "reports" / "agent_state" / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = PROJECT_ROOT / "reports" / "agent_state" / "goal_learning_roadmap_dashboard.html"
CORE_FAMILIES = {"concrete", "rebar", "pipe", "pump", "support"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _target_rows(group_rows: list[dict[str, Any]], recall_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in recall_rows:
        item = dict(row)
        item["_baseline_rank"] = 0
        item["_target_kind"] = "missing_top80"
        rows.append(item)
    for row in group_rows:
        rank = _int(row.get("positive_rank"))
        if rank > 20:
            item = dict(row)
            item["_baseline_rank"] = rank
            item["_target_kind"] = "rank_21_80"
            rows.append(item)
    return rows


def _slice(target: dict[str, Any]) -> str:
    family = str(target.get("query_family") or "")
    if family in CORE_FAMILIES:
        return "core_family"
    if family:
        return "nonempty_other_family"
    return "taxonomy_empty"


def evaluate(index_path: Path, targets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source = GuardedOssAliasPriorSource(index_path, min_support=2, core_families=CORE_FAMILIES)
    metrics: dict[str, Counter] = defaultdict(Counter)
    false_by_family: dict[str, Counter] = defaultdict(Counter)
    moved_rows = []
    for target in targets:
        expected = {str(item) for item in target.get("expected_ids") or []}
        family = str(target.get("query_family") or "")
        slices = ["all_targets", _slice(target)]
        if family:
            slices.append("nonempty_eligible")
        candidates = source.collect(
            province=str(target.get("province") or ""),
            query_text=str(target.get("query") or ""),
            query_family=family,
            item=target,
            top_k=80,
        )
        rank_by_id = {str(row.get("quota_id")): idx + 1 for idx, row in enumerate(candidates)}
        hit_rank = min([rank_by_id[item] for item in expected if item in rank_by_id] or [0])
        baseline_rank = _int(target.get("_baseline_rank"))
        baseline_top80 = int(1 <= baseline_rank <= 80)
        baseline_top20 = int(1 <= baseline_rank <= 20)
        baseline_top5 = int(1 <= baseline_rank <= 5)
        positive = sum(1 for row in candidates if str(row.get("quota_id")) in expected)
        false = len(candidates) - positive
        for name in slices:
            m = metrics[name]
            m["groups"] += 1
            m["baseline_top80"] += baseline_top80
            m["baseline_top20"] += baseline_top20
            m["baseline_top5"] += baseline_top5
            m["expanded_top80"] += int(bool(baseline_top80 or (hit_rank and hit_rank <= 80)))
            m["expanded_top20"] += int(bool(baseline_top20 or (hit_rank and hit_rank <= 20)))
            m["expanded_top5"] += int(bool(baseline_top5 or (hit_rank and hit_rank <= 5)))
            m["generator_hit_groups"] += int(bool(hit_rank))
            m["generated_candidates"] += len(candidates)
            m["positive_generated_candidates"] += positive
            m["false_candidates"] += false
        fam_key = family or "<empty>"
        f = false_by_family[fam_key]
        f["groups"] += 1
        f["generated_candidates"] += len(candidates)
        f["positive_generated_candidates"] += positive
        f["false_candidates"] += false
        f["generator_hit_groups"] += int(bool(hit_rank))
        if hit_rank:
            hit = next(row for row in candidates if str(row.get("quota_id")) in expected)
            moved_rows.append(
                {
                    "group_id": target.get("group_id", ""),
                    "target_kind": target.get("_target_kind", ""),
                    "query_family": fam_key,
                    "baseline_rank": baseline_rank,
                    "generator_rank": hit_rank,
                    "matched_quota_id": hit.get("quota_id", ""),
                    "support_count": hit.get("oss_alias_support_count", 0),
                    "source_family_count": hit.get("oss_alias_source_family_count", 0),
                }
            )
    scorecard = []
    for name, m in metrics.items():
        generated = int(m["generated_candidates"])
        scorecard.append(
            {
                "slice": name,
                "groups": int(m["groups"]),
                "delta_top80": int(m["expanded_top80"] - m["baseline_top80"]),
                "delta_top20": int(m["expanded_top20"] - m["baseline_top20"]),
                "delta_top5": int(m["expanded_top5"] - m["baseline_top5"]),
                "generator_hit_groups": int(m["generator_hit_groups"]),
                "generated_candidates": generated,
                "positive_generated_candidates": int(m["positive_generated_candidates"]),
                "false_candidates": int(m["false_candidates"]),
                "false_candidate_rate": round(m["false_candidates"] / generated, 6) if generated else 0.0,
            }
        )
    scorecard.sort(key=lambda row: row["slice"])
    false_rows = []
    for family, m in false_by_family.items():
        generated = int(m["generated_candidates"])
        false_rows.append(
            {
                "query_family": family,
                "groups": int(m["groups"]),
                "generated_candidates": generated,
                "positive_generated_candidates": int(m["positive_generated_candidates"]),
                "false_candidates": int(m["false_candidates"]),
                "generator_hit_groups": int(m["generator_hit_groups"]),
                "false_candidate_rate": round(m["false_candidates"] / generated, 6) if generated else 0.0,
            }
        )
    false_rows.sort(key=lambda row: (-row["generator_hit_groups"], row["query_family"]))
    return scorecard, false_rows, moved_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate guarded OSS alias prior on dev/OOF targets")
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    group_rows = _read_jsonl(args.matrix_dir / "ltr_group_dev.jsonl")
    recall_rows = _read_jsonl(args.matrix_dir / "recall_gap_dev.raw.jsonl")
    targets = _target_rows(group_rows, recall_rows)
    scorecard, false_rows, moved_rows = evaluate(args.index, targets)
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    false_csv = args.output_prefix.with_name(args.output_prefix.name + "_false_candidate_audit.csv")
    moved_csv = args.output_prefix.with_name(args.output_prefix.name + "_moved_groups.csv")
    core = next((row for row in scorecard if row["slice"] == "core_family"), {})
    report = {
        "stage": "15.6 guarded OSS alias dev/OOF eval",
        "dev_oof_only": True,
        "index": str(args.index),
        "target_groups": len(targets),
        "scorecard": scorecard,
        "decision": "guarded_alias_eval_positive" if int(core.get("delta_top80", 0)) > 0 else "guarded_alias_eval_no_go",
        "heldout_hard_used": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_csv(scorecard_csv, scorecard, ["slice", "groups", "delta_top80", "delta_top20", "delta_top5", "generator_hit_groups", "generated_candidates", "positive_generated_candidates", "false_candidates", "false_candidate_rate"])
    _write_csv(false_csv, false_rows, ["query_family", "groups", "generated_candidates", "positive_generated_candidates", "false_candidates", "generator_hit_groups", "false_candidate_rate"])
    _write_csv(moved_csv, moved_rows, ["group_id", "target_kind", "query_family", "baseline_rank", "generator_rank", "matched_quota_id", "support_count", "source_family_count"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "core": core}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
