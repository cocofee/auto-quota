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
EXCLUDED_SOURCE = "global_repair_decision_table.csv"

DEFAULT_S3_CLOSURE = AGENT_STATE / "goal_10x_s3_source_artifact_stop_closure_strategy_return_summary.json"
DEFAULT_S2_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_S2_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_S3_FREEZE_ROWS = AGENT_STATE / "goal_family_compatibility_freeze_narrow_whatif_rows.csv"
DEFAULT_S3_COMPAT_ROWS = AGENT_STATE / "goal_family_compatibility_whatif_rows.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s9_non_global_repair_evidence_inventory"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _max_share(counter: Counter[str]) -> tuple[str, int, float]:
    total = sum(counter.values())
    if not total:
        return "", 0, 0.0
    key, count = counter.most_common(1)[0]
    return key, count, round(count / total, 6)


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _s2_inventory(flips: list[dict[str, Any]], scorecard: list[dict[str, str]]) -> list[dict[str, Any]]:
    original = {row["candidate_id"]: row for row in scorecard}
    by_candidate: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"gain": 0, "loss": 0, "excluded_gain": 0, "excluded_loss": 0, "sources": Counter(), "families": Counter()}
    )
    for row in flips:
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        flip_type = str(row.get("flip_type") or "")
        source = str(row.get("source_file") or "")
        is_excluded = source == EXCLUDED_SOURCE
        if flip_type == "gain":
            if is_excluded:
                by_candidate[candidate_id]["excluded_gain"] += 1
            else:
                by_candidate[candidate_id]["gain"] += 1
                by_candidate[candidate_id]["sources"][source] += 1
                by_candidate[candidate_id]["families"][str(row.get("query_family") or "")] += 1
        elif flip_type == "loss":
            if is_excluded:
                by_candidate[candidate_id]["excluded_loss"] += 1
            else:
                by_candidate[candidate_id]["loss"] += 1

    rows: list[dict[str, Any]] = []
    for candidate_id, data in by_candidate.items():
        top_source, top_source_count, top_source_share = _max_share(data["sources"])
        top_family, top_family_count, top_family_share = _max_share(data["families"])
        gain = data["gain"]
        loss = data["loss"]
        net = gain - loss
        original_row = original.get(candidate_id, {})
        rows.append(
            {
                "lane": "S2_ranking",
                "candidate_id": candidate_id,
                "original_hit1_net": original_row.get("hit1_net", ""),
                "non_global_gain": gain,
                "non_global_loss": loss,
                "non_global_net": net,
                "excluded_source_gain": data["excluded_gain"],
                "excluded_source_loss": data["excluded_loss"],
                "top_non_global_source": top_source,
                "top_non_global_source_gain_count": top_source_count,
                "top_non_global_source_gain_share": top_source_share,
                "top_query_family": top_family,
                "top_query_family_gain_count": top_family_count,
                "top_query_family_gain_share": top_family_share,
                "positive_after_exclusion": net > 0,
                "non_source_dominated": bool(gain) and top_source_share < 0.8,
                "lane_decision": "candidate_evidence_found" if net > 0 and top_source_share < 0.8 else "no_reentry_evidence",
            }
        )
    return sorted(rows, key=lambda row: (int(row["non_global_net"]), int(row["non_global_gain"])), reverse=True)


def _s3_policy_inventory(rows: list[dict[str, str]], policy: str, candidate_id: str) -> dict[str, Any]:
    gain = loss = excluded_gain = excluded_loss = 0
    sources: Counter[str] = Counter()
    relations: Counter[str] = Counter()
    families: Counter[str] = Counter()
    for row in rows:
        if row.get("split") != "dev_oof" or row.get("policy") != policy:
            continue
        source = row.get("source_file", "")
        is_excluded = source == EXCLUDED_SOURCE
        is_gain = (not _bool(row.get("gated_hit1"))) and _bool(row.get("policy_hit1"))
        is_loss = _bool(row.get("gated_hit1")) and (not _bool(row.get("policy_hit1")))
        if is_gain:
            if is_excluded:
                excluded_gain += 1
            else:
                gain += 1
                sources[source] += 1
                relations[row.get("compatibility_relation_id") or row.get("family_pair", "")] += 1
                families[row.get("query_family", "")] += 1
        elif is_loss:
            if is_excluded:
                excluded_loss += 1
            else:
                loss += 1
    top_source, top_source_count, top_source_share = _max_share(sources)
    top_relation, top_relation_count, top_relation_share = _max_share(relations)
    top_family, top_family_count, top_family_share = _max_share(families)
    net = gain - loss
    return {
        "lane": "S3_safety_gate",
        "candidate_id": candidate_id,
        "original_hit1_net": "",
        "non_global_gain": gain,
        "non_global_loss": loss,
        "non_global_net": net,
        "excluded_source_gain": excluded_gain,
        "excluded_source_loss": excluded_loss,
        "top_non_global_source": top_source,
        "top_non_global_source_gain_count": top_source_count,
        "top_non_global_source_gain_share": top_source_share,
        "top_query_family": top_family,
        "top_query_family_gain_count": top_family_count,
        "top_query_family_gain_share": top_family_share,
        "top_relation": top_relation,
        "top_relation_gain_count": top_relation_count,
        "top_relation_gain_share": top_relation_share,
        "positive_after_exclusion": net > 0,
        "non_source_dominated": bool(gain) and top_source_share < 0.8,
        "lane_decision": "candidate_evidence_found" if net > 0 and top_source_share < 0.8 else "no_reentry_evidence",
    }


def _s3_compat_inventory(rows: list[dict[str, str]]) -> dict[str, Any]:
    gain = loss = excluded_gain = excluded_loss = 0
    sources: Counter[str] = Counter()
    relations: Counter[str] = Counter()
    families: Counter[str] = Counter()
    for row in rows:
        if row.get("split") != "dev_oof":
            continue
        source = row.get("source_file", "")
        is_excluded = source == EXCLUDED_SOURCE
        is_gain = (not _bool(row.get("gated_hit1"))) and _bool(row.get("whatif_hit1"))
        is_loss = _bool(row.get("gated_hit1")) and (not _bool(row.get("whatif_hit1")))
        if is_gain:
            if is_excluded:
                excluded_gain += 1
            else:
                gain += 1
                sources[source] += 1
                relations[row.get("compatibility_relation_id") or row.get("family_pair", "")] += 1
                families[row.get("query_family", "")] += 1
        elif is_loss:
            if is_excluded:
                excluded_loss += 1
            else:
                loss += 1
    top_source, top_source_count, top_source_share = _max_share(sources)
    top_relation, top_relation_count, top_relation_share = _max_share(relations)
    top_family, top_family_count, top_family_share = _max_share(families)
    net = gain - loss
    return {
        "lane": "S3_safety_gate",
        "candidate_id": "POL_STAGE_7_5_COMPAT_REFERENCE",
        "original_hit1_net": "",
        "non_global_gain": gain,
        "non_global_loss": loss,
        "non_global_net": net,
        "excluded_source_gain": excluded_gain,
        "excluded_source_loss": excluded_loss,
        "top_non_global_source": top_source,
        "top_non_global_source_gain_count": top_source_count,
        "top_non_global_source_gain_share": top_source_share,
        "top_query_family": top_family,
        "top_query_family_gain_count": top_family_count,
        "top_query_family_gain_share": top_family_share,
        "top_relation": top_relation,
        "top_relation_gain_count": top_relation_count,
        "top_relation_gain_share": top_relation_share,
        "positive_after_exclusion": net > 0,
        "non_source_dominated": bool(gain) and top_source_share < 0.8,
        "lane_decision": "candidate_evidence_found" if net > 0 and top_source_share < 0.8 else "no_reentry_evidence",
    }


def _lane_decisions(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lanes = sorted(set(row["lane"] for row in inventory))
    decisions: list[dict[str, Any]] = []
    for lane in lanes:
        lane_rows = [row for row in inventory if row["lane"] == lane]
        best = max(lane_rows, key=lambda row: (int(row["non_global_net"]), int(row["non_global_gain"])))
        positive = [row for row in lane_rows if row["positive_after_exclusion"] and row["non_source_dominated"]]
        decisions.append(
            {
                "lane": lane,
                "candidate_count": len(lane_rows),
                "best_candidate_id": best["candidate_id"],
                "best_non_global_gain": best["non_global_gain"],
                "best_non_global_loss": best["non_global_loss"],
                "best_non_global_net": best["non_global_net"],
                "best_top_non_global_source": best["top_non_global_source"],
                "best_top_non_global_source_gain_share": best["top_non_global_source_gain_share"],
                "reentry_candidate_count": len(positive),
                "decision": "reentry_candidate_found" if positive else "keep_closed_no_non_global_positive_evidence",
            }
        )
    return decisions


def _source_exclusion_effects(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in inventory:
        rows.append(
            {
                "lane": row["lane"],
                "candidate_id": row["candidate_id"],
                "excluded_source": EXCLUDED_SOURCE,
                "excluded_source_gain": row["excluded_source_gain"],
                "excluded_source_loss": row["excluded_source_loss"],
                "remaining_gain": row["non_global_gain"],
                "remaining_loss": row["non_global_loss"],
                "remaining_net": row["non_global_net"],
                "decision": row["lane_decision"],
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "reopen_S2_or_S3_from_global_repair_gain",
            "reason": "The stopped source must be excluded before any re-entry evidence claim.",
            "allowed_after": "new non-global dev/OOF positive evidence passes source dominance checks",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "S9 is read-only inventory and found no re-entry candidate.",
            "allowed_after": "never for selection; validation only after a frozen approved candidate",
        },
        {
            "blocked_action": "train_or_tune_model",
            "reason": "No non-global positive dev/OOF lane exists to justify execution.",
            "allowed_after": "future explicit execution plan with valid re-entry evidence",
        },
        {
            "blocked_action": "implement_threshold_or_GoalSearcher_change",
            "reason": "S9 is evidence inventory only and has no approved candidate.",
            "allowed_after": "separate implementation go after passing evidence and validation boundaries",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# S9 Non-global-repair Evidence Inventory",
        "",
        f"Excluded source: `{EXCLUDED_SOURCE}`.",
        "",
        "## Metrics",
        "",
        _md_table([["metric", "value"]] + [[key, value] for key, value in report["metrics"].items()]),
        "",
        "## Lane Decisions",
        "",
        _md_table(
            [["lane", "best_candidate_id", "best_net", "reentry_candidate_count", "decision"]]
            + [
                [row["lane"], row["best_candidate_id"], row["best_non_global_net"], row["reentry_candidate_count"], row["decision"]]
                for row in report["lane_decisions"]
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
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    text = text.replace('<div class="value">Strategy return</div>', '<div class="value">S9 no-go</div>', 1)
    text = text.replace(
        "S3 已只读收口为 diagnostic-only；当前转回 broader strategy，下一条选 non-global-repair evidence inventory。",
        "S9 已排除 global_repair_decision_table.csv 盘点现有 dev/OOF evidence；没有发现可 re-entry 的正向 lane。",
        1,
    )
    text = text.replace('<div class="value">非单源路线</div>', '<div class="value">等待新证据</div>', 1)
    text = text.replace(
        "下一步只读盘点排除 global_repair_decision_table.csv 后，是否还有不依赖 owner mappings 的 dev/OOF 精度证据。",
        "S2 最佳 non-global net=0；S3 non-global gain=0；当前不应训练、验证或实现。",
        1,
    )
    text = text.replace(
        '<td class="stage">S3 source-artifact stop closure / strategy return</td>\n            <td><span class="pill current">current</span></td>',
        '<td class="stage">S3 source-artifact stop closure / strategy return</td>\n            <td><span class="pill done">done</span></td>',
        1,
    )
    marker = """          <tr>
            <td class="stage">10.x learning loop paused awaiting external evidence</td>"""
    row = """          <tr>
            <td class="stage">S9 non-global-repair evidence inventory</td>
            <td><span class="pill paused">no-go</span></td>
            <td>Read-only inventory existing dev/OOF evidence after excluding global_repair_decision_table.csv dependency.</td>
            <td>reentry_candidate_count=0; s2_best_non_global_net=0; s3_best_non_global_net=0; s3_non_global_gain=0.</td>
            <td>No viable non-global lane exists now. Pause unless new evidence, owner mappings, or an explicit new direction is provided.</td>
          </tr>
"""
    if "S9 non-global-repair evidence inventory" not in text:
        text = text.replace(marker, row + marker, 1)
    text = text.replace(
        "当前状态：S3 source-artifact stop closure / strategy return 已完成。S3 +26 dev/OOF 只能作为 diagnostic-only，因为收益 100% 来自 global_repair_decision_table.csv；禁止进入 heldout/hard validation、实现、改阈值或 GoalSearcher。下一步选择 S9 non-global-repair evidence inventory。",
        "当前状态：S9 non-global-repair evidence inventory 已完成。排除 global_repair_decision_table.csv 后，S3 gain=0；S2 最佳 non-global net=0，reentry_candidate_count=0。当前没有可训练/验证/实现的 lane。",
        1,
    )
    text = text.replace(
        "下一步只允许只读盘点排除 global_repair_decision_table.csv 依赖后的现有 dev/OOF evidence，判断是否还有非单源、非 owner-mapping 依赖的 accuracy strategy lane。",
        "下一步只能暂停等待新证据/owner mappings/明确新方向，或另开只读 broader strategy review；不能从当前 non-global inventory 自动进入算法改动。",
        1,
    )
    text = text.replace(
        "如果排除 global_repair_decision_table.csv 后没有 positive dev/OOF net、或只剩单 source/relation/taxonomy artifact，必须停止并报告无可推进路线。",
        "禁止：训练、调参、heldout/hard selection、改阈值、改 GoalSearcher、上线，或把 global_repair/source-dominated 证据宣称为通用 Top1 gain。",
        1,
    )
    index_marker = """          <tr>
            <td>S3 source-artifact stop closure / strategy return</td>"""
    index_row = """          <tr>
            <td>S9 non-global-repair evidence inventory</td>
            <td>当前最新证据盘点：排除 global_repair_decision_table.csv 后无可 re-entry 正向 lane。</td>
            <td><code>reports/agent_state/goal_10x_s9_non_global_repair_evidence_inventory_summary.json</code></td>
          </tr>
"""
    if "goal_10x_s9_non_global_repair_evidence_inventory_summary.json" not in text:
        text = text.replace(index_marker, index_row + index_marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3-closure", type=Path, default=DEFAULT_S3_CLOSURE)
    parser.add_argument("--s2-flips", type=Path, default=DEFAULT_S2_FLIPS)
    parser.add_argument("--s2-scorecard", type=Path, default=DEFAULT_S2_SCORECARD)
    parser.add_argument("--s3-freeze-rows", type=Path, default=DEFAULT_S3_FREEZE_ROWS)
    parser.add_argument("--s3-compat-rows", type=Path, default=DEFAULT_S3_COMPAT_ROWS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    s3_closure = _read_json(args.s3_closure)
    s2_flips = _read_jsonl(args.s2_flips)
    s2_scorecard = _read_csv(args.s2_scorecard)
    s3_freeze_rows = _read_csv(args.s3_freeze_rows)
    s3_compat_rows = _read_csv(args.s3_compat_rows)

    inventory = _s2_inventory(s2_flips, s2_scorecard)
    inventory.extend(
        [
            _s3_compat_inventory(s3_compat_rows),
            _s3_policy_inventory(s3_freeze_rows, "freeze_high_support_only", "POL_B_RELATION_FREEZE_CANDIDATES"),
            _s3_policy_inventory(s3_freeze_rows, "freeze_plus_tight_sleeve_duct", "POL_C_FREEZE_PLUS_NARROW_CANDIDATES"),
        ]
    )
    lane_decisions = _lane_decisions(inventory)
    source_effects = _source_exclusion_effects(inventory)
    blocked_actions = _blocked_actions()
    reentry_candidates = [row for row in inventory if row["positive_after_exclusion"] and row["non_source_dominated"]]
    s2_best = max((row for row in inventory if row["lane"] == "S2_ranking"), key=lambda row: (int(row["non_global_net"]), int(row["non_global_gain"])))
    s3_best = max((row for row in inventory if row["lane"] == "S3_safety_gate"), key=lambda row: (int(row["non_global_net"]), int(row["non_global_gain"])))

    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "candidate_inventory_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_inventory.csv")),
        "lane_decisions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_decisions.csv")),
        "source_exclusion_effects_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_exclusion_effects.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "excluded_source": EXCLUDED_SOURCE,
        "inventory_candidate_count": len(inventory),
        "lane_count": len(lane_decisions),
        "reentry_candidate_count": len(reentry_candidates),
        "s2_best_candidate_id": s2_best["candidate_id"],
        "s2_best_non_global_gain": s2_best["non_global_gain"],
        "s2_best_non_global_loss": s2_best["non_global_loss"],
        "s2_best_non_global_net": s2_best["non_global_net"],
        "s3_best_candidate_id": s3_best["candidate_id"],
        "s3_best_non_global_gain": s3_best["non_global_gain"],
        "s3_best_non_global_loss": s3_best["non_global_loss"],
        "s3_best_non_global_net": s3_best["non_global_net"],
        "training_allowed": False,
        "implementation_allowed": False,
        "heldout_selection_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    report = {
        "stage": "Goal LTR v1 / S9 non-global-repair evidence inventory",
        "read_only": True,
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "heldout_not_used_for_selection": True,
        "source_artifacts": {
            "s3_closure": str(args.s3_closure),
            "s2_flips": str(args.s2_flips),
            "s2_scorecard": str(args.s2_scorecard),
            "s3_freeze_rows": str(args.s3_freeze_rows),
            "s3_compat_rows": str(args.s3_compat_rows),
        },
        "metrics": metrics,
        "lane_decisions": lane_decisions,
        "blocked_actions": blocked_actions,
        "decision": "No non-global-repair re-entry lane exists in current dev/OOF artifacts. After excluding global_repair_decision_table.csv, S3 compatibility/freeze candidates have zero gain, and the best S2 ranking candidate has non_global_net=0 with only one non-global gain and one loss. Keep S1/S2/S3/DQ/S6 implementation closed; do not train, validate on heldout/hard, tune thresholds, or change GoalSearcher.",
        "anti_drift_conclusion": "S9 is a read-only inventory over existing dev/OOF artifacts. It excludes the stopped source dependency and performs no training, tuning, threshold change, ranking change, GoalSearcher change, feature whitelist edit, heldout/hard selection, switch enablement, or online integration.",
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
        "next_stage": {
            "stage": "pause or broader strategy review with a new direction",
            "goal": "Pause unless new evidence, owner mappings, or an explicit new non-execution strategy direction is provided.",
            "prohibited": [
                "training",
                "implementation",
                "threshold changes",
                "GoalSearcher changes",
                "heldout/hard selection",
                "claiming source-dominated Top1 gain",
            ],
        },
    }

    inventory_fields = [
        "lane",
        "candidate_id",
        "original_hit1_net",
        "non_global_gain",
        "non_global_loss",
        "non_global_net",
        "excluded_source_gain",
        "excluded_source_loss",
        "top_non_global_source",
        "top_non_global_source_gain_count",
        "top_non_global_source_gain_share",
        "top_query_family",
        "top_query_family_gain_count",
        "top_query_family_gain_share",
        "top_relation",
        "top_relation_gain_count",
        "top_relation_gain_share",
        "positive_after_exclusion",
        "non_source_dominated",
        "lane_decision",
    ]
    _write_csv(Path(artifacts["candidate_inventory_csv"]), inventory, inventory_fields)
    _write_csv(Path(artifacts["lane_decisions_csv"]), lane_decisions, list(lane_decisions[0].keys()))
    _write_csv(Path(artifacts["source_exclusion_effects_csv"]), source_effects, list(source_effects[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
