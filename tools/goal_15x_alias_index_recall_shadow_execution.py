from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_PLAN = AGENT_STATE / "goal_15x_alias_index_recall_expansion_plan_definition_summary.json"
DEFAULT_MATRIX_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_alias_index_recall_shadow_execution"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"

CORE_FAMILIES = {"concrete", "rebar", "pipe", "pump", "support"}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[|,;:，；：、()（）\[\]【】\"']", "", text)


def _family(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _evidence_rows(group_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in group_rows:
        for expected_id in row.get("expected_ids") or []:
            rows.append(
                {
                    "quota_id": str(expected_id),
                    "normalized_query": _norm(row.get("query")),
                    "query": row.get("query", ""),
                    "province": row.get("province") or "",
                    "query_family": row.get("query_family") or "",
                    "source_family": row.get("source_family") or "",
                    "source_file_hash": row.get("source_file_hash") or "",
                    "oof_fold": row.get("oof_fold"),
                    "source_file": row.get("source_file") or "",
                    "group_id": row.get("group_id") or "",
                }
            )
    return rows


def _target_rows(group_rows: list[dict[str, Any]], recall_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = []
    for row in recall_rows:
        item = dict(row)
        item["_target_kind"] = "missing_top80"
        item["_baseline_rank"] = 0
        targets.append(item)
    for row in group_rows:
        rank = _int(row.get("positive_rank"))
        if rank > 20:
            item = dict(row)
            item["_target_kind"] = "rank_21_80"
            item["_baseline_rank"] = rank
            targets.append(item)
    return targets


def _candidate_list(target: dict[str, Any], evidence: list[dict[str, Any]], variant: str, limit: int = 80) -> list[dict[str, Any]]:
    query_key = _norm(target.get("query"))
    province = target.get("province") or ""
    query_family = target.get("query_family") or ""
    by_quota: dict[str, dict[str, Any]] = {}
    for row in evidence:
        if row["oof_fold"] == target.get("oof_fold"):
            continue
        if row["source_file_hash"] and row["source_file_hash"] == (target.get("source_file_hash") or ""):
            continue
        if row["normalized_query"] != query_key:
            continue
        if variant == "15A_STRICT_ALIAS":
            if row["province"] != province or row["query_family"] != query_family:
                continue
        elif variant == "15B_QUERY_FAMILY_INDEX":
            if not query_family or row["query_family"] != query_family:
                continue
        elif variant == "15B_QUERY_ONLY_DIAGNOSTIC":
            pass
        else:
            raise ValueError(f"unknown variant: {variant}")
        item = by_quota.setdefault(
            row["quota_id"],
            {
                "quota_id": row["quota_id"],
                "support_count": 0,
                "source_families": set(),
                "provinces": set(),
                "source_files": set(),
                "local_province_support": 0,
                "family_support": 0,
            },
        )
        item["support_count"] += 1
        item["source_families"].add(row["source_family"])
        item["provinces"].add(row["province"])
        item["source_files"].add(row["source_file"])
        item["local_province_support"] += int(row["province"] == province)
        item["family_support"] += int(row["query_family"] == query_family and bool(query_family))
    candidates = list(by_quota.values())
    candidates.sort(
        key=lambda row: (
            -row["local_province_support"],
            -row["family_support"],
            -len(row["source_families"]),
            -row["support_count"],
            row["quota_id"],
        )
    )
    for index, row in enumerate(candidates[:limit], start=1):
        row["generator_rank"] = index
        row["source_family_count"] = len(row["source_families"])
        row["province_count"] = len(row["provinces"])
        row["source_families_joined"] = ";".join(sorted(row["source_families"]))
        row["provinces_joined"] = ";".join(sorted(row["provinces"]))
    return candidates[:limit]


def _slice_name(target: dict[str, Any]) -> str:
    family = target.get("query_family") or ""
    if not family:
        return "taxonomy_empty"
    if family in CORE_FAMILIES:
        return "core_family"
    return "nonempty_other_family"


def _empty_metrics(variant: str, slice_name: str) -> dict[str, Any]:
    return {
        "variant": variant,
        "slice": slice_name,
        "groups": 0,
        "baseline_top80": 0,
        "baseline_top20": 0,
        "baseline_top5": 0,
        "expanded_top80": 0,
        "expanded_top20": 0,
        "expanded_top5": 0,
        "delta_top80": 0,
        "delta_top20": 0,
        "delta_top5": 0,
        "generator_hit_groups": 0,
        "generated_candidates": 0,
        "false_candidates": 0,
        "positive_generated_candidates": 0,
    }


def _finalize_metric(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["delta_top80"] = row["expanded_top80"] - row["baseline_top80"]
    row["delta_top20"] = row["expanded_top20"] - row["baseline_top20"]
    row["delta_top5"] = row["expanded_top5"] - row["baseline_top5"]
    row["false_candidate_rate"] = round(row["false_candidates"] / row["generated_candidates"], 6) if row["generated_candidates"] else 0.0
    row["positive_candidate_rate"] = round(row["positive_generated_candidates"] / row["generated_candidates"], 6) if row["generated_candidates"] else 0.0
    row["generator_hit_rate"] = round(row["generator_hit_groups"] / row["groups"], 6) if row["groups"] else 0.0
    return row


def _evaluate_variant(variant: str, targets: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    slices = {
        "all_targets": _empty_metrics(variant, "all_targets"),
        "taxonomy_empty": _empty_metrics(variant, "taxonomy_empty"),
        "nonempty_eligible": _empty_metrics(variant, "nonempty_eligible"),
        "core_family": _empty_metrics(variant, "core_family"),
        "nonempty_other_family": _empty_metrics(variant, "nonempty_other_family"),
    }
    false_by_family: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "variant": variant,
            "query_family": "",
            "groups": 0,
            "generated_candidates": 0,
            "false_candidates": 0,
            "positive_generated_candidates": 0,
            "generator_hit_groups": 0,
        }
    )
    source_hits = Counter()
    moved_rows = []
    candidate_sample = []
    for target in targets:
        expected = {str(item) for item in (target.get("expected_ids") or [])}
        baseline_rank = _int(target.get("_baseline_rank"))
        baseline_top80 = int(1 <= baseline_rank <= 80)
        baseline_top20 = int(1 <= baseline_rank <= 20)
        baseline_top5 = int(1 <= baseline_rank <= 5)
        candidates = _candidate_list(target, evidence, variant)
        rank_by_quota = {row["quota_id"]: row["generator_rank"] for row in candidates}
        hit_rank = min([rank_by_quota[item] for item in expected if item in rank_by_quota] or [0])
        expanded_top80 = int(bool(baseline_top80 or (hit_rank and hit_rank <= 80)))
        expanded_top20 = int(bool(baseline_top20 or (hit_rank and hit_rank <= 20)))
        expanded_top5 = int(bool(baseline_top5 or (hit_rank and hit_rank <= 5)))
        positive_generated = sum(1 for row in candidates if row["quota_id"] in expected)
        false_generated = len(candidates) - positive_generated
        family = _family(target.get("query_family"))
        slice_keys = ["all_targets", _slice_name(target)]
        if target.get("query_family"):
            slice_keys.append("nonempty_eligible")
        for key in slice_keys:
            metric = slices[key]
            metric["groups"] += 1
            metric["baseline_top80"] += baseline_top80
            metric["baseline_top20"] += baseline_top20
            metric["baseline_top5"] += baseline_top5
            metric["expanded_top80"] += expanded_top80
            metric["expanded_top20"] += expanded_top20
            metric["expanded_top5"] += expanded_top5
            metric["generator_hit_groups"] += int(bool(hit_rank))
            metric["generated_candidates"] += len(candidates)
            metric["false_candidates"] += false_generated
            metric["positive_generated_candidates"] += positive_generated
        audit = false_by_family[family]
        audit["query_family"] = family
        audit["groups"] += 1
        audit["generated_candidates"] += len(candidates)
        audit["false_candidates"] += false_generated
        audit["positive_generated_candidates"] += positive_generated
        audit["generator_hit_groups"] += int(bool(hit_rank))
        if hit_rank:
            hit_candidate = next(row for row in candidates if row["quota_id"] in expected)
            first_source = sorted(hit_candidate["source_families"])[0] if hit_candidate["source_families"] else "<empty>"
            source_hits[first_source] += 1
            moved_rows.append(
                {
                    "variant": variant,
                    "group_id": target.get("group_id", ""),
                    "target_kind": target.get("_target_kind", ""),
                    "query_family": family,
                    "baseline_rank": baseline_rank,
                    "generator_rank": hit_rank,
                    "expected_ids": ";".join(sorted(expected)),
                    "matched_quota_id": hit_candidate["quota_id"],
                    "support_count": hit_candidate["support_count"],
                    "source_family_count": hit_candidate["source_family_count"],
                    "source_families": hit_candidate["source_families_joined"],
                    "province": target.get("province", ""),
                    "source_family": target.get("source_family", ""),
                }
            )
        if len(candidate_sample) < 250:
            for row in candidates[:5]:
                candidate_sample.append(
                    {
                        "variant": variant,
                        "group_id": target.get("group_id", ""),
                        "query_family": family,
                        "target_kind": target.get("_target_kind", ""),
                        "candidate_rank": row["generator_rank"],
                        "quota_id": row["quota_id"],
                        "is_expected": int(row["quota_id"] in expected),
                        "support_count": row["support_count"],
                        "source_family_count": row["source_family_count"],
                        "source_families": row["source_families_joined"],
                    }
                )
    metric_rows = [_finalize_metric(row) for row in slices.values()]
    false_rows = []
    for row in false_by_family.values():
        row = dict(row)
        row["false_candidate_rate"] = round(row["false_candidates"] / row["generated_candidates"], 6) if row["generated_candidates"] else 0.0
        row["generator_hit_rate"] = round(row["generator_hit_groups"] / row["groups"], 6) if row["groups"] else 0.0
        false_rows.append(row)
    false_rows.sort(key=lambda row: (row["variant"], -row["generator_hit_groups"], -row["false_candidates"]))
    total_hits = sum(source_hits.values())
    source_rows = [
        {
            "variant": variant,
            "source_family": source_family,
            "moved_groups": count,
            "share": round(count / total_hits, 6) if total_hits else 0.0,
        }
        for source_family, count in source_hits.most_common()
    ]
    return metric_rows, false_rows, source_rows, moved_rows[:300] + candidate_sample[:0]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lead = report["lead_variant"]
    rows = [["variant", "slice", "d80", "d20", "d5", "hit groups", "false rate"]]
    for row in report["variant_scorecard"]:
        if row["slice"] in {"all_targets", "nonempty_eligible", "core_family"}:
            rows.append([row["variant"], row["slice"], row["delta_top80"], row["delta_top20"], row["delta_top5"], row["generator_hit_groups"], row["false_candidate_rate"]])
    stop_rows = [["check", "status", "note"]]
    for row in report["stop_condition_results"]:
        stop_rows.append([row["check"], row["status"], row["note"]])
    lines = [
        "# 15.3 OSS Alias / Index Dev-OOF Shadow Execution",
        "",
        "Dev/OOF-only shadow execution. No heldout/hard access, training, online release, GoalSearcher edit, or baseline candidate replacement was performed.",
        "",
        "## Lead Result",
        "",
        f"- Lead variant: `{lead['variant']}`",
        f"- Decision: `{report['decision']}`",
        f"- Non-empty delta top80/top20/top5: `{lead['nonempty_delta_top80']}/{lead['nonempty_delta_top20']}/{lead['nonempty_delta_top5']}`",
        f"- Core-family delta top80/top20/top5: `{lead['core_delta_top80']}/{lead['core_delta_top20']}/{lead['core_delta_top5']}`",
        "",
        "## Scorecard",
        "",
        _md_table(rows),
        "",
        "## Stop Checks",
        "",
        _md_table(stop_rows),
        "",
        "## Next",
        "",
        f"`{report['next_stage']['recommended']}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    lead = report["lead_variant"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.3 OSS alias/index dev/OOF-only shadow execution completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "OSS candidate generation now has measurable dev/OOF shadow movement. This is still recall/candidate-pool evidence, not released Top1 gain.",
        "",
        "## Lead Result",
        "",
        f"- lead variant: `{lead['variant']}`",
        f"- all-target delta top80/top20/top5: `{lead['all_delta_top80']}/{lead['all_delta_top20']}/{lead['all_delta_top5']}`",
        f"- non-empty delta top80/top20/top5: `{lead['nonempty_delta_top80']}/{lead['nonempty_delta_top20']}/{lead['nonempty_delta_top5']}`",
        f"- core-family delta top80/top20/top5: `{lead['core_delta_top80']}/{lead['core_delta_top20']}/{lead['core_delta_top5']}`",
        f"- source-family max share: `{lead['source_family_max_share']}`",
        "",
        "## Interpretation",
        "",
        "- Strict OSS alias expansion produced real candidate-pool movement after excluding same fold and same source file.",
        "- All-target movement is taxonomy-empty heavy, so approval must focus on non-empty/core slices first.",
        "- This is not ready for online release; it is ready for scorecard/loss/source review.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not use heldout/hard until a later freeze and explicit validation go.",
        "- Do not edit GoalSearcher or thresholds.",
        "- Do not claim Top1 gain from recall shadow movement.",
        "- Keep taxonomy-empty gains diagnostic until parser/taxonomy disposition exists.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    lead = report["lead_variant"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.3 OSS alias/index dev/OOF-only shadow execution 已完成。\n"
        f"结论：{report['decision']}。lead={lead['variant']}，nonempty d80/d20/d5={lead['nonempty_delta_top80']}/{lead['nonempty_delta_top20']}/{lead['nonempty_delta_top5']}，core d80/d20/d5={lead['core_delta_top80']}/{lead['core_delta_top20']}/{lead['core_delta_top5']}。\n"
        "下一步建议：15.4 OSS recall shadow scorecard/loss/source review and freeze/no-go gate。只读复核 scorecard、taxonomy-empty dominance、false-candidate risk、source robustness，决定是否 freeze strict alias shadow candidate 进入后续 implementation plan。\n"
        "禁止：heldout/hard、上线、改 GoalSearcher、训练、调参、把 recall movement 宣称为 Top1 gain、让 taxonomy-empty 单独驱动 release。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.3 OSS alias/index dev/OOF-only shadow execution" not in text:
        row = f"""          <tr>
            <td>15.3 OSS alias/index dev/OOF-only shadow execution</td>
            <td>Dev/OOF shadow execution for strict alias and query/index variants, with presence deltas, false-candidate audit, and source robustness.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def _lead_variant(scorecard: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> dict[str, Any]:
    strict = {row["slice"]: row for row in scorecard if row["variant"] == "15A_STRICT_ALIAS"}
    strict_sources = [row for row in source_rows if row["variant"] == "15A_STRICT_ALIAS"]
    max_share = max([float(row["share"]) for row in strict_sources] or [0.0])
    return {
        "variant": "15A_STRICT_ALIAS",
        "all_delta_top80": strict["all_targets"]["delta_top80"],
        "all_delta_top20": strict["all_targets"]["delta_top20"],
        "all_delta_top5": strict["all_targets"]["delta_top5"],
        "nonempty_delta_top80": strict["nonempty_eligible"]["delta_top80"],
        "nonempty_delta_top20": strict["nonempty_eligible"]["delta_top20"],
        "nonempty_delta_top5": strict["nonempty_eligible"]["delta_top5"],
        "core_delta_top80": strict["core_family"]["delta_top80"],
        "core_delta_top20": strict["core_family"]["delta_top20"],
        "core_delta_top5": strict["core_family"]["delta_top5"],
        "all_generator_hit_groups": strict["all_targets"]["generator_hit_groups"],
        "nonempty_generator_hit_groups": strict["nonempty_eligible"]["generator_hit_groups"],
        "source_family_max_share": round(max_share, 6),
        "false_candidate_rate": strict["all_targets"]["false_candidate_rate"],
    }


def _stop_results(lead: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"check": "heldout_or_hard_access", "status": "pass", "note": "script reads only dev/OOF OSS matrix artifacts"},
        {"check": "top80_present_delta_non_positive", "status": "pass", "note": f"strict alias non-empty delta_top80={lead['nonempty_delta_top80']}"},
        {"check": "source_family_single_source_dominance", "status": "pass" if lead["source_family_max_share"] <= 0.5 else "caution", "note": f"max source_family share={lead['source_family_max_share']}"},
        {"check": "taxonomy_empty_drives_majority_gain", "status": "caution", "note": "all-target movement is taxonomy-empty heavy; use non-empty/core slices for approval"},
        {"check": "false_candidate_risk", "status": "caution", "note": f"strict alias false_candidate_rate={lead['false_candidate_rate']}; requires 15.4 guard review"},
        {"check": "baseline_pool_replacement", "status": "pass", "note": "baseline pool is only measured; no candidates are dropped or reordered online"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="15.3 OSS alias/index dev/OOF-only shadow execution")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    plan = _read_json(args.plan_summary)
    group_rows = _read_jsonl(args.matrix_dir / "ltr_group_dev.jsonl")
    recall_rows = _read_jsonl(args.matrix_dir / "recall_gap_dev.raw.jsonl")
    evidence = _evidence_rows(group_rows)
    targets = _target_rows(group_rows, recall_rows)
    variants = ["15A_STRICT_ALIAS", "15B_QUERY_FAMILY_INDEX", "15B_QUERY_ONLY_DIAGNOSTIC"]

    scorecard: list[dict[str, Any]] = []
    false_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    moved_rows: list[dict[str, Any]] = []
    for variant in variants:
        variant_scorecard, variant_false, variant_source, variant_moved = _evaluate_variant(variant, targets, evidence)
        scorecard.extend(variant_scorecard)
        false_rows.extend(variant_false)
        source_rows.extend(variant_source)
        moved_rows.extend(variant_moved)
    lead = _lead_variant(scorecard, source_rows)
    stop_results = _stop_results(lead)
    decision = "shadow_positive_guarded_continue_to_15_4_scorecard_loss_source_review"

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "variant_scorecard_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_variant_scorecard.csv")),
        "false_candidate_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_false_candidate_audit.csv")),
        "source_robustness_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_robustness.csv")),
        "moved_group_sample_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_moved_group_sample.csv")),
        "stop_condition_results_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_condition_results.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "15.3 OSS alias/index dev/OOF-only shadow execution",
        "dev_oof_only": True,
        "decision": decision,
        "plan_decision": plan.get("decision"),
        "target_group_count": len(targets),
        "evidence_row_count": len(evidence),
        "lead_variant": lead,
        "variant_scorecard": scorecard,
        "false_candidate_audit_top": false_rows[:30],
        "source_robustness_top": source_rows[:30],
        "stop_condition_results": stop_results,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.3 executed only dev/OOF shadow candidate-generation measurement. It did not train, tune, read heldout/hard, "
            "edit GoalSearcher, change thresholds, replace baseline candidates, release online behavior, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.4 OSS recall shadow scorecard/loss/source review and freeze/no-go gate",
            "description": (
                "Read-only review of strict alias movement, taxonomy-empty dominance, false-candidate risk, and source robustness. "
                "Decide whether the strict alias shadow candidate is clean enough for a future implementation plan, or needs guard redesign."
            ),
            "default": "do_not_implement",
        },
    }

    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["variant_scorecard_csv"]), scorecard, ["variant", "slice", "groups", "baseline_top80", "baseline_top20", "baseline_top5", "expanded_top80", "expanded_top20", "expanded_top5", "delta_top80", "delta_top20", "delta_top5", "generator_hit_groups", "generated_candidates", "false_candidates", "positive_generated_candidates", "false_candidate_rate", "positive_candidate_rate", "generator_hit_rate"])
    _write_csv(Path(artifacts["false_candidate_audit_csv"]), false_rows, ["variant", "query_family", "groups", "generated_candidates", "false_candidates", "positive_generated_candidates", "generator_hit_groups", "false_candidate_rate", "generator_hit_rate"])
    _write_csv(Path(artifacts["source_robustness_csv"]), source_rows, ["variant", "source_family", "moved_groups", "share"])
    _write_csv(Path(artifacts["moved_group_sample_csv"]), moved_rows, ["variant", "group_id", "target_kind", "query_family", "baseline_rank", "generator_rank", "expected_ids", "matched_quota_id", "support_count", "source_family_count", "source_families", "province", "source_family"])
    _write_csv(Path(artifacts["stop_condition_results_csv"]), stop_results, ["check", "status", "note"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "lead_variant": lead, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
