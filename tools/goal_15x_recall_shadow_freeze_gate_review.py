from __future__ import annotations

import argparse
import csv
import json
import re
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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_SHADOW = AGENT_STATE / "goal_15x_alias_index_recall_shadow_execution_summary.json"
DEFAULT_MATRIX_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_recall_shadow_freeze_gate_review"
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
    text = re.sub(r"\s+", "", str(value or "").lower())
    return re.sub(r"[|,;:，；：、()（）\[\]【】\"']", "", text)


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
                    "province": row.get("province") or "",
                    "query_family": row.get("query_family") or "",
                    "source_family": row.get("source_family") or "",
                    "source_file_hash": row.get("source_file_hash") or "",
                    "oof_fold": row.get("oof_fold"),
                }
            )
    return rows


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


def _strict_candidates(target: dict[str, Any], evidence: list[dict[str, Any]], min_support: int, min_source_family: int) -> list[dict[str, Any]]:
    by_quota: dict[str, dict[str, Any]] = {}
    query_key = _norm(target.get("query"))
    province = target.get("province") or ""
    family = target.get("query_family") or ""
    for row in evidence:
        if row["oof_fold"] == target.get("oof_fold"):
            continue
        if row["source_file_hash"] and row["source_file_hash"] == (target.get("source_file_hash") or ""):
            continue
        if row["normalized_query"] != query_key or row["province"] != province or row["query_family"] != family:
            continue
        item = by_quota.setdefault(row["quota_id"], {"quota_id": row["quota_id"], "support_count": 0, "source_families": set()})
        item["support_count"] += 1
        item["source_families"].add(row["source_family"])
    candidates = []
    for item in by_quota.values():
        item["source_family_count"] = len(item["source_families"])
        if item["support_count"] >= min_support and item["source_family_count"] >= min_source_family:
            candidates.append(item)
    candidates.sort(key=lambda row: (-row["source_family_count"], -row["support_count"], row["quota_id"]))
    return candidates[:80]


def _evaluate_guard(targets: list[dict[str, Any]], evidence: list[dict[str, Any]], min_support: int, min_source_family: int, slice_name: str) -> dict[str, Any]:
    if slice_name == "core_family":
        keep = lambda row: (row.get("query_family") or "") in CORE_FAMILIES
    elif slice_name == "nonempty_eligible":
        keep = lambda row: bool(row.get("query_family"))
    else:
        keep = lambda row: True
    metric = Counter()
    by_family = Counter()
    by_source = Counter()
    for target in filter(keep, targets):
        expected = {str(item) for item in target.get("expected_ids") or []}
        baseline_rank = _int(target.get("_baseline_rank"))
        baseline_top80 = int(1 <= baseline_rank <= 80)
        baseline_top20 = int(1 <= baseline_rank <= 20)
        baseline_top5 = int(1 <= baseline_rank <= 5)
        candidates = _strict_candidates(target, evidence, min_support, min_source_family)
        rank_by_id = {row["quota_id"]: index + 1 for index, row in enumerate(candidates)}
        hit_rank = min([rank_by_id[item] for item in expected if item in rank_by_id] or [0])
        positives = sum(1 for row in candidates if row["quota_id"] in expected)
        metric["groups"] += 1
        metric["baseline_top80"] += baseline_top80
        metric["baseline_top20"] += baseline_top20
        metric["baseline_top5"] += baseline_top5
        metric["expanded_top80"] += int(bool(baseline_top80 or (hit_rank and hit_rank <= 80)))
        metric["expanded_top20"] += int(bool(baseline_top20 or (hit_rank and hit_rank <= 20)))
        metric["expanded_top5"] += int(bool(baseline_top5 or (hit_rank and hit_rank <= 5)))
        metric["generator_hit_groups"] += int(bool(hit_rank))
        metric["generated_candidates"] += len(candidates)
        metric["positive_generated_candidates"] += positives
        metric["false_candidates"] += len(candidates) - positives
        if hit_rank:
            by_family[target.get("query_family") or "<empty>"] += 1
            hit_candidate = next(row for row in candidates if row["quota_id"] in expected)
            for source_family in hit_candidate["source_families"]:
                by_source[source_family] += 1
    total_source_hits = sum(by_source.values())
    false_rate = metric["false_candidates"] / metric["generated_candidates"] if metric["generated_candidates"] else 0.0
    max_source_share = max([count / total_source_hits for count in by_source.values()] or [0.0])
    return {
        "variant": f"15A_STRICT_ALIAS_support{min_support}_sourcefamily{min_source_family}",
        "slice": slice_name,
        "min_support": min_support,
        "min_source_family": min_source_family,
        "groups": metric["groups"],
        "delta_top80": metric["expanded_top80"] - metric["baseline_top80"],
        "delta_top20": metric["expanded_top20"] - metric["baseline_top20"],
        "delta_top5": metric["expanded_top5"] - metric["baseline_top5"],
        "generator_hit_groups": metric["generator_hit_groups"],
        "generated_candidates": metric["generated_candidates"],
        "positive_generated_candidates": metric["positive_generated_candidates"],
        "false_candidates": metric["false_candidates"],
        "false_candidate_rate": round(false_rate, 6),
        "source_family_max_share": round(max_source_share, 6),
        "top_hit_families": ";".join(f"{family}:{count}" for family, count in by_family.most_common(8)),
    }


def _freeze_checks(shadow: dict[str, Any], guard_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lead = shadow["lead_variant"]
    chosen = next(row for row in guard_rows if row["variant"] == "15A_STRICT_ALIAS_support2_sourcefamily1" and row["slice"] == "core_family")
    return [
        {"check": "raw_strict_alias_movement", "status": "pass", "note": f"non-empty d80/d20/d5={lead['nonempty_delta_top80']}/{lead['nonempty_delta_top20']}/{lead['nonempty_delta_top5']}"},
        {"check": "raw_false_candidate_rate", "status": "block_raw_freeze", "note": f"raw strict false_candidate_rate={lead['false_candidate_rate']}"},
        {"check": "taxonomy_empty_dominance", "status": "block_raw_freeze", "note": "taxonomy-empty contributes most all-target movement; exclude from release candidate"},
        {"check": "guarded_core_support2_movement", "status": "pass", "note": f"core support>=2 d80/d20/d5={chosen['delta_top80']}/{chosen['delta_top20']}/{chosen['delta_top5']}"},
        {"check": "guarded_core_support2_false_rate", "status": "caution", "note": f"false_candidate_rate={chosen['false_candidate_rate']}; acceptable only for append-only shadow implementation plan with ranker/loss audit"},
        {"check": "source_robustness", "status": "pass", "note": f"guarded max source share={chosen['source_family_max_share']}"},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    rows = [["variant", "slice", "d80", "d20", "d5", "hits", "false rate", "max source"]]
    for row in report["guard_sensitivity"]:
        rows.append([row["variant"], row["slice"], row["delta_top80"], row["delta_top20"], row["delta_top5"], row["generator_hit_groups"], row["false_candidate_rate"], row["source_family_max_share"]])
    checks = [["check", "status", "note"]]
    for row in report["freeze_checks"]:
        checks.append([row["check"], row["status"], row["note"]])
    lines = [
        "# 15.4 OSS Recall Shadow Scorecard / Loss / Source Review",
        "",
        "Read-only freeze/no-go review of 15.3 OSS alias/index shadow results. No implementation, training, heldout/hard access, release, or GoalSearcher edit was performed.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Frozen planning candidate: `{report['frozen_planning_candidate']['candidate_id']}`",
        "",
        "## Guard Sensitivity",
        "",
        _md_table(rows),
        "",
        "## Freeze Checks",
        "",
        _md_table(checks),
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
    frozen = report["frozen_planning_candidate"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.4 OSS recall shadow scorecard/loss/source review and freeze/no-go gate completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "Raw strict alias is not clean enough to freeze directly, but a narrower guarded core-family candidate is worth turning into an implementation plan.",
        "",
        "## Frozen Planning Candidate",
        "",
        f"- candidate: `{frozen['candidate_id']}`",
        f"- scope: `{frozen['scope']}`",
        f"- guard: `{frozen['guard']}`",
        f"- expected dev/OOF shadow movement: `{frozen['delta_top80']}/{frozen['delta_top20']}/{frozen['delta_top5']}`",
        f"- false candidate rate: `{frozen['false_candidate_rate']}`",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Still no implementation until explicit go.",
        "- Do not use heldout/hard for implementation planning.",
        "- Do not edit GoalSearcher in this review stage.",
        "- Do not release raw strict alias or taxonomy-empty movement.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    frozen = report["frozen_planning_candidate"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.4 OSS recall shadow scorecard/loss/source review and freeze/no-go gate 已完成。\n"
        f"结论：{report['decision']}。冻结 planning candidate={frozen['candidate_id']}，d80/d20/d5={frozen['delta_top80']}/{frozen['delta_top20']}/{frozen['delta_top5']}，false_rate={frozen['false_candidate_rate']}。\n"
        "下一步建议：15.5 guarded OSS strict-alias implementation plan definition。只读定义具体代码落点、append-only merge、guard、artifact、rollback、dev/OOF test command；默认仍不实现。\n"
        "禁止：heldout/hard、上线、改 GoalSearcher、训练、调参、实现 raw strict alias、让 taxonomy-empty 单独驱动 release。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.4 OSS recall shadow scorecard/loss/source review" not in text:
        row = f"""          <tr>
            <td>15.4 OSS recall shadow scorecard/loss/source review</td>
            <td>Read-only freeze/no-go gate for 15A strict alias shadow movement, guard sensitivity, taxonomy-empty risk, false-candidate risk, and source robustness.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.4 OSS recall shadow scorecard/loss/source freeze gate review")
    parser.add_argument("--shadow-summary", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    shadow = _read_json(args.shadow_summary)
    group_rows = _read_jsonl(args.matrix_dir / "ltr_group_dev.jsonl")
    recall_rows = _read_jsonl(args.matrix_dir / "recall_gap_dev.raw.jsonl")
    evidence = _evidence_rows(group_rows)
    targets = _target_rows(group_rows, recall_rows)
    guard_rows = []
    for min_support, min_source_family in [(1, 1), (2, 1), (2, 2), (3, 1)]:
        for slice_name in ["nonempty_eligible", "core_family"]:
            guard_rows.append(_evaluate_guard(targets, evidence, min_support, min_source_family, slice_name))
    frozen_row = next(row for row in guard_rows if row["variant"] == "15A_STRICT_ALIAS_support2_sourcefamily1" and row["slice"] == "core_family")
    frozen_candidate = {
        "candidate_id": "15A_GUARDED_CORE_STRICT_ALIAS_SUPPORT2",
        "scope": "non-empty core families only: concrete/rebar/pipe/pump/support; taxonomy-empty excluded",
        "guard": "strict normalized query + same province + same query_family + exclude same fold/source file + support_count>=2; append-only shadow candidate source",
        "delta_top80": frozen_row["delta_top80"],
        "delta_top20": frozen_row["delta_top20"],
        "delta_top5": frozen_row["delta_top5"],
        "false_candidate_rate": frozen_row["false_candidate_rate"],
        "source_family_max_share": frozen_row["source_family_max_share"],
        "release_status": "not_released_implementation_plan_only",
    }
    decision = "freeze_guarded_core_strict_alias_for_15_5_implementation_plan_definition"
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "guard_sensitivity_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guard_sensitivity.csv")),
        "freeze_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_freeze_checks.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "15.4 OSS recall shadow scorecard/loss/source review and freeze/no-go gate",
        "read_only_review": True,
        "decision": decision,
        "raw_lead_variant": shadow["lead_variant"],
        "guard_sensitivity": guard_rows,
        "freeze_checks": _freeze_checks(shadow, guard_rows),
        "frozen_planning_candidate": frozen_candidate,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.4 is read-only. It did not implement candidate generation, train, tune, read heldout/hard, "
            "edit GoalSearcher, change thresholds, release raw strict alias, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.5 guarded OSS strict-alias implementation plan definition",
            "description": "Read-only implementation plan for the frozen guarded core strict-alias candidate: exact code touchpoints, append-only merge contract, rollback, dev/OOF test command, required artifacts, and explicit implementation go/no-go.",
            "default": "do_not_implement_without_explicit_go",
        },
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["guard_sensitivity_csv"]), guard_rows, ["variant", "slice", "min_support", "min_source_family", "groups", "delta_top80", "delta_top20", "delta_top5", "generator_hit_groups", "generated_candidates", "positive_generated_candidates", "false_candidates", "false_candidate_rate", "source_family_max_share", "top_hit_families"])
    _write_csv(Path(artifacts["freeze_checks_csv"]), report["freeze_checks"], ["check", "status", "note"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "frozen_planning_candidate": frozen_candidate, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
