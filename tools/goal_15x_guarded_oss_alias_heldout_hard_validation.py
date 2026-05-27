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
from src.goal_search.national_index import clean_text, extract_signal
from src.goal_search.oss_alias_prior import GuardedOssAliasPriorSource


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_HELDOUT = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "heldout_validation.jsonl"
DEFAULT_HARD = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "hard_validation.jsonl"
DEFAULT_INDEX = Path(getattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", PROJECT_ROOT / "data" / "goal_search" / "guarded_oss_alias_index.jsonl"))
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_guarded_oss_alias_heldout_hard_validation"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
CORE_FAMILIES = {"concrete", "rebar", "pipe", "pump", "support"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def _query_text(row: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            clean_text(row.get("bill_name") or row.get("name")),
            clean_text(row.get("bill_text") or row.get("description") or row.get("feature_text")),
            clean_text(row.get("specialty")),
            clean_text(row.get("unit") or row.get("bill_unit")),
        )
        if part
    )


def _slice_name(query_family: str) -> str:
    if query_family in CORE_FAMILIES:
        return "core_family"
    if query_family:
        return "nonempty_other_family"
    return "taxonomy_empty"


def _candidate_source_rows(
    source: GuardedOssAliasPriorSource,
    row: dict[str, Any],
    query_family: str,
) -> list[dict[str, Any]]:
    seen_queries: set[str] = set()
    seen_quota_ids: set[str] = set()
    output: list[dict[str, Any]] = []
    texts = [
        clean_text(row.get("bill_name") or row.get("name")),
        clean_text(row.get("bill_text") or row.get("description") or row.get("feature_text")),
        _query_text(row),
    ]
    for text in texts:
        if not text or text in seen_queries:
            continue
        seen_queries.add(text)
        for candidate in source.collect(
            province=clean_text(row.get("province")),
            query_text=text,
            query_family=query_family,
            item=row,
            top_k=int(getattr(config, "OSS_GUARDED_ALIAS_TOP_K", 6) or 6),
        ):
            quota_id = clean_text(candidate.get("quota_id"))
            if not quota_id or quota_id in seen_quota_ids:
                continue
            seen_quota_ids.add(quota_id)
            output.append(candidate)
    return output


def _evaluate_split(
    split_name: str,
    rows: list[dict[str, Any]],
    source: GuardedOssAliasPriorSource,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audits: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, start=1):
        query_text = _query_text(row)
        query_family = extract_signal(query_text).family
        expected = {clean_text(item) for item in row.get("expected_ids") or [] if clean_text(item)}
        candidates = _candidate_source_rows(source, row, query_family)
        positive = sum(1 for candidate in candidates if clean_text(candidate.get("quota_id")) in expected)
        source_family_counts = [int(candidate.get("oss_alias_source_family_count") or 0) for candidate in candidates]
        audits.append(
            {
                "split": split_name,
                "row_ordinal": ordinal,
                "anchor_group_id": row.get("anchor_group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "bucket": row.get("bucket", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "query_family": query_family or "<empty>",
                "slice": _slice_name(query_family),
                "expected_ids": "|".join(sorted(expected)),
                "alias_generated_candidates": len(candidates),
                "alias_positive_candidates": positive,
                "alias_false_candidates": len(candidates) - positive,
                "alias_hit_group": int(positive > 0),
                "alias_max_source_family_count": max(source_family_counts) if source_family_counts else 0,
                "alias_candidate_ids": "|".join(clean_text(candidate.get("quota_id")) for candidate in candidates),
            }
        )
    return audits, _scorecard(audits)


def _scorecard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for name in ("all", row["slice"], f"bucket:{row['bucket']}", f"family:{row['query_family']}"):
            m = metrics[name]
            m["groups"] += 1
            m["alias_hit_groups"] += int(row["alias_hit_group"])
            m["alias_generated_candidates"] += int(row["alias_generated_candidates"])
            m["alias_positive_candidates"] += int(row["alias_positive_candidates"])
            m["alias_false_candidates"] += int(row["alias_false_candidates"])
            m["max_source_family_count_ge2_groups"] += int(row["alias_max_source_family_count"] >= 2)
    scorecard: list[dict[str, Any]] = []
    for name, m in metrics.items():
        generated = int(m["alias_generated_candidates"])
        groups = int(m["groups"])
        hit_groups = int(m["alias_hit_groups"])
        scorecard.append(
            {
                "slice": name,
                "groups": groups,
                "alias_hit_groups": hit_groups,
                "alias_hit_rate": round(hit_groups / groups, 6) if groups else 0.0,
                "alias_generated_candidates": generated,
                "alias_positive_candidates": int(m["alias_positive_candidates"]),
                "alias_false_candidates": int(m["alias_false_candidates"]),
                "alias_false_candidate_rate": round(m["alias_false_candidates"] / generated, 6) if generated else 0.0,
                "max_source_family_count_ge2_groups": int(m["max_source_family_count_ge2_groups"]),
            }
        )
    scorecard.sort(key=lambda row: (0 if row["slice"] == "all" else 1, row["slice"]))
    return scorecard


def _write_summary_md(path: Path, report: dict[str, Any]) -> None:
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    lines = [
        "# 15.9 Guarded OSS Alias Heldout/Hard Validation",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "This is a heldout/hard candidate-source validation of the already implemented default-off `15A_GUARDED_CORE_STRICT_ALIAS_SUPPORT2` package. Full Top1 A/B was not available because this workspace has no local province `quota.db` files for `GoalSearcher` validation.",
        "",
        "## Headline",
        "",
        f"- heldout alias hit groups: `{heldout['alias_hit_groups']}/{heldout['groups']}`; generated/positive/false candidates: `{heldout['alias_generated_candidates']}/{heldout['alias_positive_candidates']}/{heldout['alias_false_candidates']}`",
        f"- hard alias hit groups: `{hard['alias_hit_groups']}/{hard['groups']}`; generated/positive/false candidates: `{hard['alias_generated_candidates']}/{hard['alias_positive_candidates']}/{hard['alias_false_candidates']}`",
        "",
        "## Stop Conditions",
        "",
    ]
    for item in report["stop_conditions"]:
        lines.append(f"- {item['check']}: `{item['status']}` - {item['evidence']}")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_status(path: Path, report: dict[str, Any]) -> None:
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.9 guarded OSS alias heldout/hard candidate-source validation completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "## Validation Result",
        "",
        f"- heldout alias hit groups: `{heldout['alias_hit_groups']}/{heldout['groups']}`; generated/positive/false: `{heldout['alias_generated_candidates']}/{heldout['alias_positive_candidates']}/{heldout['alias_false_candidates']}`.",
        f"- hard alias hit groups: `{hard['alias_hit_groups']}/{hard['groups']}`; generated/positive/false: `{hard['alias_generated_candidates']}/{hard['alias_positive_candidates']}/{hard['alias_false_candidates']}`.",
        "- Full Top1 A/B was blocked because no local province `quota.db` files are present for these validation provinces.",
        "",
        "## What This Means",
        "",
        report["interpretation"],
        "",
        "## Next Meaningful Action",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not train or tune from this validation.",
        "- Do not enable online behavior by default.",
        "- Do not release this package as a Top1 improvement without a real GoalSearcher A/B.",
        "- Do not expand raw strict alias or taxonomy-empty movement.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    import re

    text = path.read_text(encoding="utf-8")
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.9 guarded OSS alias heldout/hard candidate-source validation 已完成。\n"
        f"结论：{report['decision']}。\n"
        f"heldout hit={heldout['alias_hit_groups']}/{heldout['groups']}，generated/positive/false={heldout['alias_generated_candidates']}/{heldout['alias_positive_candidates']}/{heldout['alias_false_candidates']}；"
        f"hard hit={hard['alias_hit_groups']}/{hard['groups']}，generated/positive/false={hard['alias_generated_candidates']}/{hard['alias_positive_candidates']}/{hard['alias_false_candidates']}。\n"
        f"下一步：{report['next_stage']['recommended']}。\n"
        "禁止：从本次 heldout/hard 结果调参、训练、扩 scope、默认启用、上线、释放 raw strict alias 或 taxonomy-empty movement。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.9 guarded OSS alias heldout/hard candidate-source validation" not in text:
        row = f"""          <tr>
            <td>15.9 guarded OSS alias heldout/hard candidate-source validation</td>
            <td>One-time validation of whether the default-off guarded OSS alias package can generate expected candidates on anchor-clean heldout/hard; no training, tuning, or online enablement.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.9 heldout/hard validation for guarded OSS alias default-off package")
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    source = GuardedOssAliasPriorSource(args.index, min_support=2, core_families=CORE_FAMILIES)
    heldout_rows, heldout_scorecard = _evaluate_split("heldout", _read_jsonl(args.heldout), source)
    hard_rows, hard_scorecard = _evaluate_split("hard", _read_jsonl(args.hard), source)
    all_rows = heldout_rows + hard_rows
    all_scorecard = _scorecard(all_rows)
    heldout_head = next(row for row in heldout_scorecard if row["slice"] == "all")
    hard_head = next(row for row in hard_scorecard if row["slice"] == "all")
    all_head = next(row for row in all_scorecard if row["slice"] == "all")
    taxonomy_generated = sum(
        int(row["alias_generated_candidates"]) for row in all_scorecard if row["slice"] == "taxonomy_empty"
    )
    positive_signal = int(all_head["alias_hit_groups"]) > 0
    false_dominant = int(all_head["alias_false_candidates"]) > int(all_head["alias_positive_candidates"])
    stop_conditions = [
        {
            "check": "heldout_hard_used_only_for_validation",
            "status": "pass",
            "evidence": "Read anchor_audit heldout/hard JSONL only; no training, tuning, or selection loop.",
        },
        {
            "check": "top1_ab_unavailable",
            "status": "blocked",
            "evidence": "GoalSearcher A/B requires local province quota.db files; this workspace has no usable province quota.db for the validation rows.",
        },
        {
            "check": "taxonomy_empty_block",
            "status": "pass" if taxonomy_generated == 0 else "fail",
            "evidence": f"taxonomy_empty alias_generated_candidates={taxonomy_generated}.",
        },
        {
            "check": "positive_candidate_source_signal",
            "status": "pass" if positive_signal else "fail",
            "evidence": f"alias_hit_groups={all_head['alias_hit_groups']}/{all_head['groups']}.",
        },
        {
            "check": "false_candidate_dominance",
            "status": "fail" if false_dominant else "pass",
            "evidence": f"false={all_head['alias_false_candidates']}, positive={all_head['alias_positive_candidates']}.",
        },
    ]
    failed = [item for item in stop_conditions if item["status"] == "fail"]
    if positive_signal and not failed:
        decision = "candidate_source_validation_pass_but_requires_goal_searcher_ab"
        interpretation = "The alias source produced heldout/hard positives without taxonomy-empty movement, but this is still not a Top1 validation because local quota databases are missing."
        next_stage = {
            "recommended": "provide/build validation quota.db then run real GoalSearcher A/B",
            "description": "Before release, provide the matching province quota.db files or build the validation province indexes, then rerun a real GoalSearcher A/B. Do not tune from heldout/hard.",
        }
    elif positive_signal:
        decision = "candidate_source_mixed_stop_do_not_release"
        interpretation = "The alias source can find some correct heldout/hard candidates, but false candidates dominate. This is useful evidence for redesign, not a releasable package."
        next_stage = {
            "recommended": "redesign OSS recall expansion with stronger guards",
            "description": "Stop this guarded strict-alias lane as a release candidate. The next real accuracy route should use OSS as a broader recall/index asset with stronger source/province/family guards, not this tiny strict-alias package.",
        }
    else:
        decision = "candidate_source_validation_failed_stop_lane"
        interpretation = "The alias source did not produce useful heldout/hard positives. Continuing this lane would be busywork."
        next_stage = {
            "recommended": "stop guarded OSS alias lane",
            "description": "Stop this package and move to a larger OSS recall/index redesign if continuing accuracy work.",
        }

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    report = {
        "stage": "15.9 guarded OSS alias heldout/hard candidate-source validation",
        "validation_only": True,
        "candidate": "15A_GUARDED_CORE_STRICT_ALIAS_SUPPORT2",
        "top1_ab_available": False,
        "top1_ab_blocker": "No local province quota.db files available for GoalSearcher validation rows.",
        "trained": False,
        "tuned": False,
        "online_default_changed": False,
        "index": str(args.index),
        "inputs": {"heldout": str(args.heldout), "hard": str(args.hard)},
        "headline": {"heldout": heldout_head, "hard": hard_head, "all": all_head},
        "scorecard": {"heldout": heldout_scorecard, "hard": hard_scorecard, "all": all_scorecard},
        "stop_conditions": stop_conditions,
        "decision": decision,
        "interpretation": interpretation,
        "next_stage": next_stage,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "stop_conditions_csv": str(stop_csv),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": "15.9 only ran candidate-source validation of the existing default-off guarded OSS alias package on authorized heldout/hard files. It did not train, tune, expand scope, alter thresholds, enable online behavior, modify GoalSearcher behavior, or release raw strict alias/taxonomy-empty movement.",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_summary_md(summary_md, report)
    score_rows = []
    for split, rows in (("heldout", heldout_scorecard), ("hard", hard_scorecard), ("all", all_scorecard)):
        for row in rows:
            score_rows.append({"split": split, **row})
    _write_csv(
        scorecard_csv,
        score_rows,
        [
            "split",
            "slice",
            "groups",
            "alias_hit_groups",
            "alias_hit_rate",
            "alias_generated_candidates",
            "alias_positive_candidates",
            "alias_false_candidates",
            "alias_false_candidate_rate",
            "max_source_family_count_ge2_groups",
        ],
    )
    _write_csv(row_csv, all_rows, list(all_rows[0].keys()) if all_rows else [])
    _write_csv(stop_csv, stop_conditions, ["check", "status", "evidence"])
    _write_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "headline": report["headline"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
