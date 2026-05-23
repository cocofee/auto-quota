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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_CANDIDATES = AGENT_STATE / "goal_ranked_gap_reselection_after_support_same_family_9x_candidates.csv"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_FIRE_DAMPER_ROWS = AGENT_STATE / "goal_duct_fire_damper_evidence_9x_review_rows.csv"
DEFAULT_VALVE_DUCT_ROWS = AGENT_STATE / "goal_valve_duct_size_tier_transferability_9x_review_rows.csv"
DEFAULT_VALVE_SAME_ROWS = AGENT_STATE / "goal_valve_same_family_unknown_9x_audit_rows.csv"
DEFAULT_LAMP_SAME_ROWS = AGENT_STATE / "goal_lamp_same_family_unknown_9x_audit_rows.csv"
DEFAULT_LAMP_NEAR_ROWS = AGENT_STATE / "goal_lamp_near_miss_9x_audit_rows.csv"
DEFAULT_SUPPORT_SAME_ROWS = AGENT_STATE / "goal_support_same_family_unknown_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_no_eligible_gate_review_9x"

MIN_SUPPORT = 20
MIN_PROVINCES = 3
MIN_SOURCES = 2

EMPTY_SUBBUCKET_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "weak_current_display_broadcast",
        (
            "LED屏",
            "视频",
            "扩声",
            "广播",
            "摄像",
            "监控",
            "读卡",
            "出入口",
            "识别",
            "处理器",
            "矩阵",
            "控制器",
            "主机",
            "传输",
            "背景音乐",
            "报警系统控制主机",
            "网络",
        ),
    ),
    (
        "fire_sanitary_water",
        ("消火栓", "水表", "喷淋", "灭火", "消防", "水泵", "泵", "试验消火栓", "隔油器", "卫生器具"),
    ),
    (
        "electrical_lighting",
        ("电箱", "配电箱", "强弱电", "浪涌", "电源", "LED", "灯", "吸顶", "防水防尘", "开关", "插座", "电缆", "电线"),
    ),
    (
        "hvac_duct_fan_noise",
        ("风管", "消声器", "风机", "通风", "空调", "软接头", "橡胶软接头", "轴流", "阀门"),
    ),
    (
        "earthwork_road_site",
        ("余方", "弃置", "填方", "挖方", "土方", "碎石", "安砌侧", "平石", "缘石", "标志板", "道路", "路面"),
    ),
    (
        "landscape_planting",
        ("栽植", "乔木", "灌木", "色带", "绿篱", "花卉", "草坪", "园林", "养护", "胸径", "地径", "冠幅"),
    ),
    (
        "doors_windows_curtain",
        ("门窗", "塑钢", "断桥", "窗", "门", "窗帘盒", "窗台板", "百叶", "卷帘", "纱窗"),
    ),
    (
        "masonry_concrete_plaster",
        ("砌", "混凝土", "垫层", "砂浆", "抹灰", "找平", "墙面", "块料", "钢丝网", "楼地面", "天棚", "吊顶", "涂料", "油漆", "腻子", "石材"),
    ),
    (
        "scaffold_temp",
        ("脚手架", "支架", "模板", "超高"),
    ),
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_group_ids(path: Path, status: str | None = None) -> set[str]:
    if not path.exists():
        return set()
    rows = _read_csv(path)
    if status:
        rows = [row for row in rows if _clean(row.get("transferability_status")) == status]
    return {_clean(row.get("group_id")) for row in rows if _clean(row.get("group_id"))}


def _remaining_wrong_rank(args: argparse.Namespace) -> list[dict[str, Any]]:
    blocked_group_ids = (
        _load_group_ids(Path(args.fire_damper_rows))
        | _load_group_ids(Path(args.valve_duct_rows), status="blocked_single_source")
        | _load_group_ids(Path(args.valve_same_rows))
        | _load_group_ids(Path(args.lamp_same_rows))
        | _load_group_ids(Path(args.lamp_near_rows))
        | _load_group_ids(Path(args.support_same_rows))
    )
    all_wrong_rank = [
        row
        for row in _read_csv(Path(args.wrong_rank))
        if _clean(row.get("split")) == "dev" and _clean(row.get("status")) == "top80_present_but_wrong_rank"
    ]
    return [row for row in all_wrong_rank if _clean(row.get("group_id")) not in blocked_group_ids]


def _gate_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    total_groups = len(candidates)
    total_rows = sum(_to_int(row.get("count")) for row in candidates)
    by_eligibility: dict[str, dict[str, int]] = {}
    for eligibility in sorted({_clean(row.get("eligibility")) for row in candidates}):
        rows = [row for row in candidates if _clean(row.get("eligibility")) == eligibility]
        by_eligibility[eligibility] = {
            "candidate_groups": len(rows),
            "support_rows": sum(_to_int(row.get("count")) for row in rows),
        }
    by_reason: dict[str, dict[str, int]] = {}
    for reason in sorted({_clean(row.get("eligibility_reason")) for row in candidates}):
        rows = [row for row in candidates if _clean(row.get("eligibility_reason")) == reason]
        by_reason[reason] = {
            "candidate_groups": len(rows),
            "support_rows": sum(_to_int(row.get("count")) for row in rows),
        }
    return {
        "candidate_groups": total_groups,
        "support_rows": total_rows,
        "eligible_candidates": sum(1 for row in candidates if _clean(row.get("eligibility")) == "eligible"),
        "by_eligibility": by_eligibility,
        "by_first_failing_reason": by_reason,
    }


def _what_if(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    active = [
        row
        for row in candidates
        if _clean(row.get("eligibility")) not in {"blocked", "excluded"}
    ]
    loosen_support = [
        row
        for row in active
        if _to_int(row.get("count")) >= 10
        and _to_int(row.get("province_count")) >= MIN_PROVINCES
        and _to_int(row.get("source_count")) >= MIN_SOURCES
    ]
    loosen_source = [
        row
        for row in active
        if _to_int(row.get("count")) >= MIN_SUPPORT
        and _to_int(row.get("province_count")) >= MIN_PROVINCES
        and _to_int(row.get("source_count")) >= 1
    ]
    loosen_province = [
        row
        for row in active
        if _to_int(row.get("count")) >= MIN_SUPPORT
        and _to_int(row.get("province_count")) >= 1
        and _to_int(row.get("source_count")) >= MIN_SOURCES
    ]
    blocked_empty = [
        row
        for row in candidates
        if _clean(row.get("eligibility_reason")) == "query_family_empty_is_too_broad_for_one_bucket"
    ]
    return {
        "current_gate": {
            "min_support": MIN_SUPPORT,
            "min_province_count": MIN_PROVINCES,
            "min_source_count": MIN_SOURCES,
            "eligible_candidates": 0,
        },
        "lower_support_to_10_keep_diversity": {
            "would_admit_groups": len(loosen_support),
            "would_admit_support_rows": sum(_to_int(row.get("count")) for row in loosen_support),
            "top_groups": [_candidate_label(row) for row in loosen_support[:8]],
            "interpretation": "Admits fragmented low-support buckets; this changes the high-support audit premise.",
        },
        "lower_source_to_1_keep_support": {
            "would_admit_groups": len(loosen_source),
            "would_admit_support_rows": sum(_to_int(row.get("count")) for row in loosen_source),
            "top_groups": [_candidate_label(row) for row in loosen_source[:8]],
            "interpretation": "Admits single-source buckets; this weakens the anti-same-source guard.",
        },
        "lower_province_to_1_keep_support_source": {
            "would_admit_groups": len(loosen_province),
            "would_admit_support_rows": sum(_to_int(row.get("count")) for row in loosen_province),
            "top_groups": [_candidate_label(row) for row in loosen_province[:8]],
            "interpretation": "Province diversity is not the bottleneck after prior exclusions.",
        },
        "select_blocked_query_family_empty_as_one_bucket": {
            "would_admit_groups": len(blocked_empty),
            "would_admit_support_rows": sum(_to_int(row.get("count")) for row in blocked_empty),
            "top_groups": [_candidate_label(row) for row in blocked_empty],
            "interpretation": "Not acceptable as-is because it mixes unrelated domains under empty query_family.",
        },
    }


def _candidate_label(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_family": _clean(row.get("query_family")),
        "reason": _clean(row.get("reason")),
        "count": _to_int(row.get("count")),
        "province_count": _to_int(row.get("province_count")),
        "source_count": _to_int(row.get("source_count")),
        "eligibility_reason": _clean(row.get("eligibility_reason")),
    }


def _infer_empty_subbucket(row: dict[str, Any]) -> tuple[str, str]:
    text = " ".join(
        _clean(row.get(field))
        for field in ("query", "top1_name", "positive_names_in_top80", "top1_chapter", "top1_reasons")
    )
    for bucket, patterns in EMPTY_SUBBUCKET_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return bucket, pattern
    top1_family = _clean(row.get("top1_family"))
    if top1_family:
        return f"top1_family_{top1_family}", "top1_family"
    return "other_unclassified", "no_keyword_or_top1_family_hint"


def _decompose_empty_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    annotated: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket, matched_hint = _infer_empty_subbucket(row)
        out = dict(row)
        out["inferred_empty_subbucket"] = bucket
        out["matched_hint"] = matched_hint
        annotated.append(out)
        grouped[bucket].append(out)

    total = len(rows)
    decomposition: list[dict[str, Any]] = []
    for bucket, items in grouped.items():
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        rank_buckets = Counter(_clean(row.get("rank_bucket")) for row in items if _clean(row.get("rank_bucket")))
        top1_families = Counter(_clean(row.get("top1_family")) or "<empty>" for row in items)
        pass_current_gate = len(items) >= MIN_SUPPORT and len(provinces) >= MIN_PROVINCES and len(sources) >= MIN_SOURCES
        dominant_source_count = sources.most_common(1)[0][1] if sources else 0
        decomposition.append(
            {
                "inferred_empty_subbucket": bucket,
                "count": len(items),
                "rate_within_query_family_empty": _rate(len(items), total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": sources.most_common(1)[0][0] if sources else "",
                "dominant_source_count": dominant_source_count,
                "dominant_source_rate": _rate(dominant_source_count, len(items)),
                "top_query": queries.most_common(1)[0][0] if queries else "",
                "top_query_count": queries.most_common(1)[0][1] if queries else 0,
                "top_rank_bucket": rank_buckets.most_common(1)[0][0] if rank_buckets else "",
                "top_rank_bucket_count": rank_buckets.most_common(1)[0][1] if rank_buckets else 0,
                "top1_family_mode": top1_families.most_common(1)[0][0] if top1_families else "",
                "top1_family_mode_count": top1_families.most_common(1)[0][1] if top1_families else 0,
                "current_gate_if_split": "passes_support_province_source" if pass_current_gate else "needs_manual_review_or_lower_scope",
                "example_queries": " | ".join(query for query, _ in queries.most_common(6)),
            }
        )
    decomposition.sort(key=lambda row: int(row["count"]), reverse=True)
    return annotated, decomposition


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any], candidates: list[dict[str, Any]], decomposition: list[dict[str, Any]]) -> None:
    gate = report["gate_review"]
    lines = [
        "# Stage 9.24 No Eligible Gate Review",
        "",
        "Read-only review of why stage 9.23 produced no eligible dev wrong-rank audit bucket. This does not train, tune, patch rules, change ranking, use heldout for selection, or touch GoalSearcher.",
        "",
        "## Gate Result",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_groups", gate["candidate_groups"]],
                ["support_rows_after_exclusions", gate["support_rows"]],
                ["eligible_candidates", gate["eligible_candidates"]],
                ["blocked_groups", gate["by_eligibility"].get("blocked", {}).get("candidate_groups", 0)],
                ["blocked_rows", gate["by_eligibility"].get("blocked", {}).get("support_rows", 0)],
                ["excluded_groups", gate["by_eligibility"].get("excluded", {}).get("candidate_groups", 0)],
                ["excluded_rows", gate["by_eligibility"].get("excluded", {}).get("support_rows", 0)],
                ["deprioritized_groups", gate["by_eligibility"].get("deprioritized", {}).get("candidate_groups", 0)],
                ["deprioritized_rows", gate["by_eligibility"].get("deprioritized", {}).get("support_rows", 0)],
            ]
        ),
        "",
        "## First Failing Reasons",
        "",
        _md_table(
            [["reason", "candidate_groups", "support_rows"]]
            + [
                [reason, values["candidate_groups"], values["support_rows"]]
                for reason, values in gate["by_first_failing_reason"].items()
            ]
        ),
        "",
        "## Top 9.23 Buckets",
        "",
        _md_table(
            [["family", "reason", "count", "province_count", "source_count", "eligibility_reason"]]
            + [
                [
                    row["query_family"],
                    row["reason"],
                    row["count"],
                    row["province_count"],
                    row["source_count"],
                    row["eligibility_reason"],
                ]
                for row in candidates[:12]
            ]
        ),
        "",
        "## Query Family Empty Decomposition",
        "",
        _md_table(
            [["subbucket", "count", "province_count", "source_count", "dominant_source_rate", "top_query", "gate_if_split"]]
            + [
                [
                    row["inferred_empty_subbucket"],
                    row["count"],
                    row["province_count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["top_query"],
                    row["current_gate_if_split"],
                ]
                for row in decomposition[:12]
            ]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.24 no eligible dev wrong-rank bucket gate review")
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--wrong-rank", default=str(DEFAULT_WRONG_RANK))
    parser.add_argument("--fire-damper-rows", default=str(DEFAULT_FIRE_DAMPER_ROWS))
    parser.add_argument("--valve-duct-rows", default=str(DEFAULT_VALVE_DUCT_ROWS))
    parser.add_argument("--valve-same-rows", default=str(DEFAULT_VALVE_SAME_ROWS))
    parser.add_argument("--lamp-same-rows", default=str(DEFAULT_LAMP_SAME_ROWS))
    parser.add_argument("--lamp-near-rows", default=str(DEFAULT_LAMP_NEAR_ROWS))
    parser.add_argument("--support-same-rows", default=str(DEFAULT_SUPPORT_SAME_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    candidates = _read_csv(Path(args.candidates))
    remaining = _remaining_wrong_rank(args)
    empty_rows = [
        row
        for row in remaining
        if not _clean(row.get("query_family")) and _clean(row.get("reason")) == "query_family_empty"
    ]
    annotated_empty_rows, empty_decomposition = _decompose_empty_rows(empty_rows)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
        "query_family_empty_rows_csv": str(output_prefix.with_name(output_prefix.name + "_query_family_empty_rows.csv")),
        "query_family_empty_decomposition_csv": str(output_prefix.with_name(output_prefix.name + "_query_family_empty_decomposition.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.24 no eligible dev wrong-rank bucket gate review",
        "read_only": True,
        "eval_only": True,
        "dev_only_analysis": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_9_23_candidates": str(Path(args.candidates)),
            "wrong_rank_table": str(Path(args.wrong_rank)),
        },
        "gate_review": _gate_summary(candidates),
        "gate_adjustment_what_if": _what_if(candidates),
        "query_family_empty_review": {
            "rows": len(empty_rows),
            "province_count": len({_clean(row.get("province")) for row in empty_rows if _clean(row.get("province"))}),
            "source_count": len({_clean(row.get("source_file")) for row in empty_rows if _clean(row.get("source_file"))}),
            "decomposition_subbuckets": len(empty_decomposition),
            "subbuckets_passing_current_support_province_source_gate": sum(
                1 for row in empty_decomposition if row["current_gate_if_split"] == "passes_support_province_source"
            ),
            "top_decomposition_subbuckets": empty_decomposition[:10],
        },
        "decision": (
            "Do not adjust the selection gate yet. The zero-eligible state is expected after prior exclusions: the only high-support diversified "
            "remaining bucket is blocked query_family_empty, already-known audited directions remain excluded, and the non-blocked alternatives are "
            "either low-support fragments or single-source buckets. The next step should be a read-only query_family_empty decomposition audit, not "
            "a threshold relaxation."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.24 only reviews the dev wrong-rank gate failure and decomposes the blocked empty-family bucket for audit planning. "
            "It does not select heldout thresholds, train, tune, patch rules, change ranking, or modify GoalSearcher."
        ),
        "next_stage": {
            "stage": "9.25 query_family_empty decomposition audit",
            "goal": (
                "Read-only audit of the blocked <empty> + query_family_empty bucket by inferred subbucket, separating true cross-domain ranking gaps "
                "from missing/empty taxonomy labels and source-dominated artifacts before any future reselection."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
            ],
        },
    }

    bucket_fields = [
        "query_family",
        "reason",
        "count",
        "rate_within_dev_wrong_rank_after_exclusions",
        "province_count",
        "source_count",
        "top_rank_bucket",
        "top_rank_bucket_count",
        "top_expected_book",
        "top_expected_book_count",
        "min_positive_rank",
        "median_positive_rank_hint",
        "eligibility",
        "eligibility_reason",
        "selection_score",
        "provinces",
        "source_files",
    ]
    empty_row_fields = [
        "inferred_empty_subbucket",
        "matched_hint",
        "split",
        "status",
        "reason",
        "rank_bucket",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_books",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_ranks",
        "positive_rank_min",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
        "top1_score",
        "top1_reasons",
        "top80_rows",
    ]
    decomposition_fields = [
        "inferred_empty_subbucket",
        "count",
        "rate_within_query_family_empty",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "top_query",
        "top_query_count",
        "top_rank_bucket",
        "top_rank_bucket_count",
        "top1_family_mode",
        "top1_family_mode_count",
        "current_gate_if_split",
        "example_queries",
    ]
    _write_csv(Path(artifacts["buckets_csv"]), candidates, bucket_fields)
    _write_csv(Path(artifacts["query_family_empty_rows_csv"]), annotated_empty_rows, empty_row_fields)
    _write_csv(Path(artifacts["query_family_empty_decomposition_csv"]), empty_decomposition, decomposition_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, candidates, empty_decomposition)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "gate_review": report["gate_review"],
                "query_family_empty_review": report["query_family_empty_review"],
                "decision": report["decision"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
