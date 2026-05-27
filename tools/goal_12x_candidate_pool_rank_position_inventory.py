from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_STRATEGY = AGENT_STATE / "goal_12x_accuracy_strategy_definition_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_RECALL_BOUNDARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_recall_boundary_report.csv"
DEFAULT_LOSS_SLICES = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_candidate_pool_rank_position_inventory"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.1 Candidate-Pool / Rank-Position Inventory",
        "",
        "Read-only inventory of existing dev/dev-OOF evidence.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：12.1 candidate-pool/rank-position loss decomposition evidence inventory 已完成。"
        f"primary_bottleneck={report['metrics']['primary_bottleneck']}；"
        f"wrong_rank_rows={report['metrics']['wrong_rank_rows']}；"
        f"top80_missing_rows={report['metrics']['top80_missing_rows']}；"
        f"near_miss_rank_2_5_rows={report['metrics']['near_miss_rank_2_5_rows']}；"
        f"dev_oof_top80_recall_rate={report['metrics']['dev_oof_top80_recall_rate']}。"
    )
    next_text = (
        "下一步：12.2 rank-position bottleneck design gate。只读判断是否能从 rank_2_5 / rank_6_10 "
        "高支持切片进入最小实现计划定义；仍不训练、不调参、不改阈值、不改 GoalSearcher。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：重开 11.x、扩展 11.x hints、训练、调参、改阈值、改 GoalSearcher、"
            "使用 heldout/hard 做选择、或把 dev/OOF inventory 宣称为上线收益。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.0 accuracy strategy definition</td>"
    row = (
        "          <tr>\n"
        "            <td>12.1 candidate-pool/rank-position inventory</td>\n"
        "            <td>只读盘点现有 dev/dev-OOF 证据，拆分候选池缺失、排名位置和排序损失瓶颈。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_candidate_pool_rank_position_inventory_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_candidate_pool_rank_position_inventory_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _count_by(rows: Iterable[dict[str, Any]], key: str, *, limit: int = 20) -> list[dict[str, Any]]:
    counter = Counter(str(row.get(key) or "<empty>") for row in rows)
    return [{"slice_key": name, "rows": count} for name, count in counter.most_common(limit)]


def _slice_rows(rows: list[dict[str, Any]], dimensions: list[str], label: str, limit: int = 20) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for dimension in dimensions:
        counter = Counter(str(row.get(dimension) or "<empty>") for row in rows)
        for key, count in counter.most_common(limit):
            output.append({"bottleneck": label, "slice_dimension": dimension, "slice_key": key, "rows": count})
    return output


def _rank_bucket_order(bucket: str) -> int:
    order = {"rank_2_5": 1, "rank_6_10": 2, "rank_11_20": 3, "rank_21_40": 4, "rank_41_80": 5}
    return order.get(bucket, 99)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--top80-missing", type=Path, default=DEFAULT_TOP80_MISSING)
    parser.add_argument("--recall-boundary", type=Path, default=DEFAULT_RECALL_BOUNDARY)
    parser.add_argument("--loss-slices", type=Path, default=DEFAULT_LOSS_SLICES)
    parser.add_argument("--hit1-flips", type=Path, default=DEFAULT_HIT1_FLIPS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy = _read_json(args.strategy_summary)
    wrong_rank = _read_csv(args.wrong_rank)
    top80_missing = _read_csv(args.top80_missing)
    recall_boundary = _read_csv(args.recall_boundary)
    loss_slices = _read_csv(args.loss_slices)
    hit1_flips = _read_jsonl(args.hit1_flips)

    rank_counts = Counter(row.get("rank_bucket") or "<empty>" for row in wrong_rank)
    missing_reason_counts = Counter(row.get("reason") or "<empty>" for row in top80_missing)
    near_miss_rows = [row for row in wrong_rank if row.get("rank_bucket") == "rank_2_5"]
    rank_6_10_rows = [row for row in wrong_rank if row.get("rank_bucket") == "rank_6_10"]
    deep_rank_rows = [row for row in wrong_rank if row.get("rank_bucket") in {"rank_21_40", "rank_41_80"}]
    total_gap_rows = len(wrong_rank) + len(top80_missing)

    recall_baseline = next(
        (
            row
            for row in recall_boundary
            if row.get("candidate_id") == "OBJ_A_current_lambda_rank_baseline__FT_ALL_CURRENT_WHITELIST"
        ),
        recall_boundary[0] if recall_boundary else {},
    )
    dev_oof_recall_rate = float(recall_baseline.get("top80_recall_rate") or 0.0)
    dev_oof_present_groups = int(float(recall_baseline.get("top80_present_groups") or 0))
    dev_oof_missing_groups = int(float(recall_baseline.get("top80_missing_groups") or 0))

    gains = [row for row in hit1_flips if row.get("flip_type") == "gain"]
    losses = [row for row in hit1_flips if row.get("flip_type") == "loss"]
    source_dominated = False
    if gains:
        top_source_gain = Counter(str(row.get("source_file") or "<empty>") for row in gains).most_common(1)[0][1]
        source_dominated = top_source_gain / len(gains) > 0.5

    bottleneck_overview = [
        {
            "bottleneck": "post_recall_rank_position_loss",
            "evidence_rows": len(wrong_rank),
            "share_of_9x_gap_rows": round(len(wrong_rank) / total_gap_rows, 6) if total_gap_rows else 0,
            "interpretation": "Expected quota is in top80 but not rank1; ranking/positioning is the primary observable bottleneck.",
            "next_gate_relevance": "high",
        },
        {
            "bottleneck": "candidate_pool_absence",
            "evidence_rows": len(top80_missing),
            "share_of_9x_gap_rows": round(len(top80_missing) / total_gap_rows, 6) if total_gap_rows else 0,
            "interpretation": "Expected quota is absent from top80; ranking-only changes cannot fix these rows.",
            "next_gate_relevance": "medium",
        },
        {
            "bottleneck": "near_miss_rank_2_5",
            "evidence_rows": len(near_miss_rows),
            "share_of_wrong_rank_rows": round(len(near_miss_rows) / len(wrong_rank), 6) if wrong_rank else 0,
            "interpretation": "High-value rank-position slice because positives are already close to rank1.",
            "next_gate_relevance": "high",
        },
        {
            "bottleneck": "rank_6_10",
            "evidence_rows": len(rank_6_10_rows),
            "share_of_wrong_rank_rows": round(len(rank_6_10_rows) / len(wrong_rank), 6) if wrong_rank else 0,
            "interpretation": "Second-tier rank-position slice; may need feature/objective evidence before implementation.",
            "next_gate_relevance": "medium",
        },
    ]
    rank_distribution = [
        {
            "rank_bucket": bucket,
            "rows": count,
            "share_of_wrong_rank_rows": round(count / len(wrong_rank), 6) if wrong_rank else 0,
        }
        for bucket, count in sorted(rank_counts.items(), key=lambda item: _rank_bucket_order(item[0]))
    ]
    candidate_pool_absence = [
        {
            "reason": reason,
            "rows": count,
            "share_of_top80_missing_rows": round(count / len(top80_missing), 6) if top80_missing else 0,
            "interpretation": (
                "taxonomy/query-family coverage issue likely; not a ranking-only lever"
                if reason in {"query_family_empty", "top1_family_empty"}
                else "candidate pool/retrieval boundary issue"
            ),
        }
        for reason, count in missing_reason_counts.most_common()
    ]
    slice_dimensions = ["source_file", "province", "query_family", "top1_family"]
    slice_inventory = (
        _slice_rows(wrong_rank, slice_dimensions, "post_recall_rank_position_loss")
        + _slice_rows(top80_missing, slice_dimensions, "candidate_pool_absence")
    )
    source_summary = _slice_rows(wrong_rank, ["source_file"], "wrong_rank_source", limit=10)
    source_summary += _slice_rows(top80_missing, ["source_file"], "missing_source", limit=10)
    dev_oof_loss_slice_review = []
    for row in loss_slices[:200]:
        net = int(float(row.get("net") or 0))
        groups = int(float(row.get("groups") or 0))
        if groups >= 10 and net != 0:
            dev_oof_loss_slice_review.append(
                {
                    "candidate_id": row.get("candidate_id", ""),
                    "slice_dimension": row.get("slice_dimension", ""),
                    "slice_key": row.get("slice_key", ""),
                    "groups": groups,
                    "gain": row.get("gain", ""),
                    "loss": row.get("loss", ""),
                    "net": net,
                    "inventory_note": "dev/OOF ranking evidence only; not an implementation approval",
                }
            )

    implementation_readiness = [
        {
            "candidate": "rank_2_5_positioning_lane",
            "evidence": f"{len(near_miss_rows)} dev wrong-rank rows; positives already in ranks 2-5",
            "ready_for_implementation": False,
            "ready_for_design_gate": True,
            "required_next_check": "slice purity: source/province/query_family concentration, loss budget, and exact lever boundary",
        },
        {
            "candidate": "candidate_pool_absence_lane",
            "evidence": f"{len(top80_missing)} dev top80-missing rows; dev/OOF recall boundary reports {dev_oof_missing_groups} missing groups",
            "ready_for_implementation": False,
            "ready_for_design_gate": False,
            "required_next_check": "separate parser/taxonomy/retrieval coverage plan; ranking-only route cannot fix absence",
        },
        {
            "candidate": "rank_6_10_positioning_lane",
            "evidence": f"{len(rank_6_10_rows)} dev wrong-rank rows in rank_6_10",
            "ready_for_implementation": False,
            "ready_for_design_gate": True,
            "required_next_check": "compare with rank_2_5 slice; likely second priority",
        },
    ]

    primary_bottleneck = "post_recall_rank_position_loss"
    metrics = {
        "primary_bottleneck": primary_bottleneck,
        "wrong_rank_rows": len(wrong_rank),
        "top80_missing_rows": len(top80_missing),
        "near_miss_rank_2_5_rows": len(near_miss_rows),
        "rank_6_10_rows": len(rank_6_10_rows),
        "deep_rank_21_80_rows": len(deep_rank_rows),
        "dev_oof_top80_present_groups": dev_oof_present_groups,
        "dev_oof_top80_missing_groups": dev_oof_missing_groups,
        "dev_oof_top80_recall_rate": dev_oof_recall_rate,
        "dev_oof_hit1_gain_flips": len(gains),
        "dev_oof_hit1_loss_flips": len(losses),
        "dev_oof_source_dominated_gain": source_dominated,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "heldout_hard_used": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "bottleneck_overview_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bottleneck_overview.csv")),
        "rank_distribution_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_rank_distribution.csv")),
        "candidate_pool_absence_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_pool_absence.csv")),
        "slice_inventory_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_slice_inventory.csv")),
        "source_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_summary.csv")),
        "dev_oof_loss_slice_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_dev_oof_loss_slice_review.csv")),
        "implementation_readiness_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_implementation_readiness.csv")),
    }
    decision = (
        "The dominant existing evidence points to post-recall rank-position loss, especially rank_2_5 near misses. "
        "Candidate-pool absence remains material, but it is less suitable for a ranking-only 12A lever. "
        "Proceed next to a read-only 12.2 design gate for rank_2_5/rank_6_10 slice purity and minimal lever boundaries; "
        "do not implement, train, tune, or change thresholds yet."
    )
    report = {
        "stage": "Goal LTR v1 / 12.1 candidate-pool/rank-position loss decomposition evidence inventory",
        "read_only": True,
        "source_artifacts": {
            "strategy_summary": str(args.strategy_summary),
            "wrong_rank": str(args.wrong_rank),
            "top80_missing": str(args.top80_missing),
            "dev_oof_recall_boundary": str(args.recall_boundary),
            "dev_oof_loss_slices": str(args.loss_slices),
            "dev_oof_hit1_flips": str(args.hit1_flips),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "12.1 is read-only. It inventories existing dev/dev-OOF artifacts only; it does not train, tune, implement, "
            "change thresholds, edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, "
            "or use heldout/hard for selection."
        ),
        "next_stage": {
            "stage": "12.2 rank-position bottleneck design gate",
            "default": "read_only_design_gate_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_csv(Path(artifacts["bottleneck_overview_csv"]), bottleneck_overview, list(bottleneck_overview[0].keys()))
    _write_csv(Path(artifacts["rank_distribution_csv"]), rank_distribution, list(rank_distribution[0].keys()))
    _write_csv(Path(artifacts["candidate_pool_absence_csv"]), candidate_pool_absence, list(candidate_pool_absence[0].keys()))
    _write_csv(Path(artifacts["slice_inventory_csv"]), slice_inventory, list(slice_inventory[0].keys()))
    _write_csv(Path(artifacts["source_summary_csv"]), source_summary, list(source_summary[0].keys()))
    _write_csv(Path(artifacts["dev_oof_loss_slice_review_csv"]), dev_oof_loss_slice_review, list(dev_oof_loss_slice_review[0].keys()) if dev_oof_loss_slice_review else ["candidate_id"])
    _write_csv(Path(artifacts["implementation_readiness_csv"]), implementation_readiness, list(implementation_readiness[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
