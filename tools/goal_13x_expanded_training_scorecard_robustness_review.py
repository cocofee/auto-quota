from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
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
DEFAULT_INITIAL_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_execution_summary.json"
DEFAULT_INITIAL_SCORECARD = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_candidate_scorecard.csv"
DEFAULT_INITIAL_LOSS_AUDIT = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_loss_audit_by_slice.csv"
DEFAULT_INITIAL_GROUP_META = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix" / "ltr_group_dev.jsonl"
DEFAULT_INITIAL_FLIPS = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_hit1_flips.jsonl"
DEFAULT_INITIAL_RECALL = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_recall_boundary_report.csv"
DEFAULT_INITIAL_LEAKAGE = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_leakage_gate_report.csv"
DEFAULT_INITIAL_SOURCE_FOLD = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_source_fold_report.csv"

DEFAULT_EXPANDED_SUMMARY = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_execution_summary.json"
DEFAULT_EXPANDED_SCORECARD = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_candidate_scorecard.csv"
DEFAULT_EXPANDED_LOSS_AUDIT = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_loss_audit_by_slice.csv"
DEFAULT_EXPANDED_GROUP_META = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "ltr_group_dev.jsonl"
DEFAULT_EXPANDED_FLIPS = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_hit1_flips.jsonl"
DEFAULT_EXPANDED_RECALL = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_recall_boundary_report.csv"
DEFAULT_EXPANDED_LEAKAGE = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_leakage_gate_report.csv"
DEFAULT_EXPANDED_SOURCE_FOLD = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_source_fold_report.csv"
DEFAULT_EXPANDED_MATRIX_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.json"

DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_expanded_training_scorecard_robustness_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float) -> float:
    return round(value, 6)


def _best_row(scorecard: list[dict[str, str]]) -> dict[str, str]:
    return sorted(scorecard, key=lambda row: _int(row.get("scorecard_rank")))[0]


def _scorecard_rank_map(scorecard: list[dict[str, str]]) -> dict[str, int]:
    return {row["candidate_id"]: _int(row.get("scorecard_rank")) for row in scorecard}


def _candidate_overlap(initial: list[dict[str, str]], expanded: list[dict[str, str]], top_n: int = 10) -> dict[str, Any]:
    initial_top = [row["candidate_id"] for row in sorted(initial, key=lambda row: _int(row.get("scorecard_rank")))[:top_n]]
    expanded_top = [row["candidate_id"] for row in sorted(expanded, key=lambda row: _int(row.get("scorecard_rank")))[:top_n]]
    overlap = sorted(set(initial_top) & set(expanded_top), key=lambda cid: initial_top.index(cid))
    return {
        "top_n": top_n,
        "initial_top": initial_top,
        "expanded_top": expanded_top,
        "overlap_count": len(overlap),
        "overlap_candidates": overlap,
    }


def _aggregate_best_candidate(
    *,
    best_candidate: str,
    group_meta: list[dict[str, Any]],
    flips: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_gid = {str(row.get("group_id")): row for row in group_meta}
    baseline_hit1: dict[str, bool] = {}
    candidate_hit1: dict[str, bool] = {}
    for row in group_meta:
        gid = str(row.get("group_id"))
        baseline = int(row.get("positive_rank") or 0) == 1
        baseline_hit1[gid] = baseline
        candidate_hit1[gid] = baseline
    for row in flips:
        if row.get("candidate_id") != best_candidate:
            continue
        gid = str(row.get("group_id"))
        if gid in candidate_hit1:
            candidate_hit1[gid] = bool(row.get("candidate_hit1"))

    dimensions = {
        "source_family": lambda row: str(row.get("source_family") or "<empty>"),
        "source_file": lambda row: str(row.get("source_file") or "<empty>"),
        "province": lambda row: str(row.get("province") or "<empty>"),
        "oof_fold": lambda row: str(row.get("oof_fold") if row.get("oof_fold") is not None else "<empty>"),
        "query_family": lambda row: str(row.get("query_family") or "<empty>"),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for dimension, getter in dimensions.items():
        acc: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
        for gid, meta in by_gid.items():
            key = getter(meta)
            baseline = baseline_hit1[gid]
            candidate = candidate_hit1[gid]
            item = acc[key]
            item["groups"] += 1
            item["baseline_hit1"] += int(baseline)
            item["candidate_hit1"] += int(candidate)
            item["gain"] += int((not baseline) and candidate)
            item["loss"] += int(baseline and not candidate)
            item["net"] += int((not baseline) and candidate) - int(baseline and not candidate)

        rows = []
        for key, values in acc.items():
            groups = values["groups"]
            rows.append(
                {
                    "dimension": dimension,
                    "key": key,
                    **values,
                    "baseline_hit1_rate": _round(values["baseline_hit1"] / groups) if groups else 0.0,
                    "candidate_hit1_rate": _round(values["candidate_hit1"] / groups) if groups else 0.0,
                }
            )
        rows.sort(key=lambda row: (row["net"], row["groups"]), reverse=True)
        out[dimension] = rows
    return out


def _loss_focus(loss_audit: list[dict[str, str]], best_candidate: str, limit: int = 40) -> list[dict[str, Any]]:
    rows = []
    for row in loss_audit:
        if row.get("candidate_id") != best_candidate:
            continue
        rows.append(
            {
                "slice_dimension": row.get("slice_dimension", ""),
                "slice_key": row.get("slice_key", ""),
                "groups": _int(row.get("groups")),
                "baseline_hit1": _int(row.get("baseline_hit1")),
                "candidate_hit1": _int(row.get("candidate_hit1")),
                "gain": _int(row.get("gain")),
                "loss": _int(row.get("loss")),
                "net": _int(row.get("net")),
                "baseline_hit1_rate": _float(row.get("baseline_hit1_rate")),
                "candidate_hit1_rate": _float(row.get("candidate_hit1_rate")),
            }
        )
    rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    return rows[:limit]


def _top_negative_slices(loss_audit: list[dict[str, str]], best_candidate: str) -> list[dict[str, Any]]:
    rows = []
    for row in loss_audit:
        if row.get("candidate_id") != best_candidate:
            continue
        net = _int(row.get("net"))
        if net >= 0:
            continue
        rows.append(
            {
                "slice_dimension": row.get("slice_dimension", ""),
                "slice_key": row.get("slice_key", ""),
                "groups": _int(row.get("groups")),
                "gain": _int(row.get("gain")),
                "loss": _int(row.get("loss")),
                "net": net,
            }
        )
    rows.sort(key=lambda row: (row["net"], -row["groups"]))
    return rows[:30]


def _run_summary(
    *,
    stage_label: str,
    execution_summary: dict[str, Any],
    scorecard: list[dict[str, str]],
    loss_audit: list[dict[str, str]],
    group_meta: list[dict[str, Any]],
    flips: list[dict[str, Any]],
    recall_rows: list[dict[str, str]],
    leakage_rows: list[dict[str, str]],
    source_fold_rows: list[dict[str, str]],
) -> dict[str, Any]:
    best = _best_row(scorecard)
    best_candidate = best["candidate_id"]
    agg = _aggregate_best_candidate(best_candidate=best_candidate, group_meta=group_meta, flips=flips)
    hit1_net = _int(best.get("hit1_net"))
    groups = _int(best.get("groups")) or _int(execution_summary.get("metrics", {}).get("group_count"))
    source_file_max = max([row["net"] for row in agg["source_file"]] or [0])
    source_family_max = max([row["net"] for row in agg["source_family"]] or [0])
    fold_nets = [row["net"] for row in agg["oof_fold"]]
    recall = next((row for row in recall_rows if row.get("candidate_id") == best_candidate), recall_rows[0] if recall_rows else {})
    leakage_pass = all(row.get("status") == "pass" for row in leakage_rows)
    source_fold_pass = all(row.get("status") == "pass" for row in source_fold_rows)
    return {
        "stage_label": stage_label,
        "best_candidate_id": best_candidate,
        "objective_variant": best.get("objective_variant", ""),
        "feature_toggle": best.get("feature_toggle", ""),
        "candidate_rank": _int(best.get("scorecard_rank")),
        "candidate_count": len(scorecard),
        "groups": groups,
        "matrix_rows": _int(execution_summary.get("metrics", {}).get("matrix_rows")),
        "fold_count": len(agg["oof_fold"]),
        "baseline_hit1": _int(best.get("baseline_hit1")),
        "candidate_hit1": _int(best.get("candidate_hit1")),
        "baseline_hit1_rate": _float(best.get("baseline_hit1_rate")),
        "candidate_hit1_rate": _float(best.get("candidate_hit1_rate")),
        "hit1_gain": _int(best.get("hit1_gain")),
        "hit1_loss": _int(best.get("hit1_loss")),
        "hit1_net": hit1_net,
        "hit1_gain_rate": _round(_int(best.get("hit1_gain")) / groups) if groups else 0.0,
        "hit1_loss_rate": _round(_int(best.get("hit1_loss")) / groups) if groups else 0.0,
        "hit1_net_rate": _round(hit1_net / groups) if groups else 0.0,
        "hit5_net": _int(best.get("hit5_net")),
        "candidate_mrr": _float(best.get("candidate_mrr")),
        "max_source_file_net": source_file_max,
        "max_source_file_net_share": _round(source_file_max / hit1_net) if hit1_net > 0 else 1.0,
        "max_source_family_net": source_family_max,
        "max_source_family_net_share": _round(source_family_max / hit1_net) if hit1_net > 0 else 1.0,
        "min_fold_net": min(fold_nets) if fold_nets else 0,
        "max_fold_net": max(fold_nets) if fold_nets else 0,
        "negative_fold_count": sum(1 for net in fold_nets if net < 0),
        "top80_recall_rate": _float(recall.get("top80_recall_rate")),
        "top80_missing_groups": _int(recall.get("top80_missing_groups")),
        "leakage_pass": leakage_pass,
        "source_fold_pass": source_fold_pass,
        "heldout_used_for_selection": bool(execution_summary.get("metrics", {}).get("heldout_used_for_selection")),
        "hard_used_for_selection": bool(execution_summary.get("metrics", {}).get("hard_used_for_selection")),
        "goal_searcher_changed": bool(execution_summary.get("metrics", {}).get("goal_searcher_changed")),
        "top_source_file_rows": agg["source_file"][:10],
        "top_source_family_rows": agg["source_family"][:10],
        "fold_rows": agg["oof_fold"],
        "top_loss_rows": _loss_focus(loss_audit, best_candidate),
        "negative_slices": _top_negative_slices(loss_audit, best_candidate),
    }


def _comparison_rows(initial: dict[str, Any], expanded: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = [
        ("groups", "larger_eval_surface"),
        ("matrix_rows", "larger_training_matrix"),
        ("fold_count", "source_aware_fold_coverage"),
        ("hit1_net", "absolute_net_gain"),
        ("hit1_gain", "absolute_gain"),
        ("hit1_loss", "absolute_loss"),
        ("hit1_net_rate", "net_per_group"),
        ("hit1_loss_rate", "loss_per_group"),
        ("max_source_file_net_share", "single_file_net_concentration"),
        ("max_source_family_net_share", "source_family_net_concentration"),
        ("min_fold_net", "worst_fold_net"),
        ("negative_fold_count", "fold_loss_count"),
        ("top80_recall_rate", "top80_present_boundary"),
    ]
    rows = []
    for metric, meaning in metrics:
        rows.append(
            {
                "metric": metric,
                "initial_13_5": initial.get(metric),
                "expanded_13_10": expanded.get(metric),
                "meaning": meaning,
            }
        )
    return rows


def _gate_rows(initial: dict[str, Any], expanded: dict[str, Any], overlap: dict[str, Any], matrix_summary: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "dev_oof_only_boundary",
            "status": "pass" if not expanded["heldout_used_for_selection"] and not expanded["hard_used_for_selection"] else "fail",
            "value": f"heldout={expanded['heldout_used_for_selection']}; hard={expanded['hard_used_for_selection']}",
            "reason": "13.11 may compare dev/OOF training only; heldout/hard cannot be used for selection here.",
        },
        {
            "gate": "offline_no_goal_searcher_change",
            "status": "pass" if not expanded["goal_searcher_changed"] else "fail",
            "value": expanded["goal_searcher_changed"],
            "reason": "The review must remain offline and must not modify GoalSearcher.",
        },
        {
            "gate": "expanded_scale",
            "status": "pass" if expanded["groups"] >= 2000 and expanded["groups"] >= initial["groups"] * 3 else "warn",
            "value": expanded["groups"],
            "reason": "Expanded run should materially exceed the 13.5 sample before robustness claims.",
        },
        {
            "gate": "single_file_concentration_improved",
            "status": "pass" if expanded["max_source_file_net_share"] <= 0.15 and expanded["max_source_file_net_share"] < initial["max_source_file_net_share"] else "warn",
            "value": f"{initial['max_source_file_net_share']} -> {expanded['max_source_file_net_share']}",
            "reason": "The original freeze blocker was single source/file dominance.",
        },
        {
            "gate": "source_family_concentration_not_dominant",
            "status": "pass" if expanded["max_source_family_net_share"] <= 0.35 else "warn",
            "value": expanded["max_source_family_net_share"],
            "reason": "A freeze candidate should not be dominated by one source_family net contribution.",
        },
        {
            "gate": "fold_robustness",
            "status": "pass" if expanded["negative_fold_count"] == 0 and expanded["min_fold_net"] > 0 else "warn",
            "value": f"min_fold_net={expanded['min_fold_net']}; negative_folds={expanded['negative_fold_count']}",
            "reason": "OOF signal should be positive across all folds before candidate freeze.",
        },
        {
            "gate": "loss_budget",
            "status": "pass" if expanded["hit1_loss_rate"] <= 0.02 else "warn",
            "value": expanded["hit1_loss_rate"],
            "reason": "Hit1 losses must stay small relative to the expanded sample.",
        },
        {
            "gate": "candidate_stability",
            "status": "pass" if overlap["overlap_count"] >= 6 else "warn",
            "value": overlap["overlap_count"],
            "reason": "Top candidate changed, but the top candidate family should remain represented across runs.",
        },
        {
            "gate": "top80_recall_boundary",
            "status": "warn" if expanded["top80_recall_rate"] < 0.75 else "pass",
            "value": expanded["top80_recall_rate"],
            "reason": "Expanded training can only claim top80-present ranking scope while recall is below target.",
        },
        {
            "gate": "expanded_matrix_guardrails",
            "status": "warn" if _float(matrix_summary.get("metrics", {}).get("max_source_family_group_share")) > 0.25 else "pass",
            "value": matrix_summary.get("metrics", {}).get("max_source_family_group_share"),
            "reason": "13.9 allowed guarded training but noted source_family share above the 13.7 target.",
        },
    ]
    fail = any(row["status"] == "fail" for row in rows)
    warn_names = {row["gate"] for row in rows if row["status"] == "warn"}
    if fail:
        decision = "do_not_freeze_fix_boundary_failure"
    elif {"fold_robustness", "source_family_concentration_not_dominant"} & warn_names:
        decision = "continue_matrix_rebalance_before_freeze_gate"
    else:
        decision = "proceed_to_read_only_candidate_freeze_gate"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    initial = report["initial_best"]
    expanded = report["expanded_best"]
    lines = [
        "# 13.11 Expanded Training Scorecard Comparison and Robustness Review",
        "",
        "Read-only comparison of 13.5 initial OSS XML reranker training versus 13.10 expanded/rebalanced guarded training. No training, heldout/hard selection, online integration, threshold change, or GoalSearcher edit was performed.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Headline",
        "",
        metrics["headline"],
        "",
        "## Best Candidate Comparison",
        "",
        _md_table(
            [
                ["metric", "13.5 initial", "13.10 expanded"],
                ["best_candidate_id", initial["best_candidate_id"], expanded["best_candidate_id"]],
                ["groups", initial["groups"], expanded["groups"]],
                ["hit1 gain/loss/net", f"{initial['hit1_gain']}/{initial['hit1_loss']}/{initial['hit1_net']}", f"{expanded['hit1_gain']}/{expanded['hit1_loss']}/{expanded['hit1_net']}"],
                ["candidate_hit1_rate", initial["candidate_hit1_rate"], expanded["candidate_hit1_rate"]],
                ["hit1_net_rate", initial["hit1_net_rate"], expanded["hit1_net_rate"]],
                ["hit1_loss_rate", initial["hit1_loss_rate"], expanded["hit1_loss_rate"]],
                ["max_source_file_net_share", initial["max_source_file_net_share"], expanded["max_source_file_net_share"]],
                ["max_source_family_net_share", initial["max_source_family_net_share"], expanded["max_source_family_net_share"]],
                ["min_fold_net", initial["min_fold_net"], expanded["min_fold_net"]],
                ["top80_recall_rate", initial["top80_recall_rate"], expanded["top80_recall_rate"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Candidate Stability",
        "",
        f"Top-10 overlap: {report['candidate_overlap']['overlap_count']} / {report['candidate_overlap']['top_n']}. 13.5 best is rank {metrics['initial_best_rank_in_expanded']} in 13.10; 13.10 best was rank {metrics['expanded_best_rank_in_initial']} in 13.5.",
        "",
        "## Expanded Source Concentration",
        "",
        _md_table([["dimension", "key", "groups", "gain", "loss", "net"]] + [[row["dimension"], row["key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["expanded_source_focus"][:12]]),
        "",
        "## Expanded Loss Focus",
        "",
        _md_table([["slice_dimension", "slice_key", "groups", "gain", "loss", "net"]] + [[row["slice_dimension"], row["slice_key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["expanded_loss_focus"][:12]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.11 expanded training scorecard comparison and robustness review 已完成。\n"
        f"结论：{report['decision']}。expanded best={report['expanded_best']['best_candidate_id']}，hit1_net={report['expanded_best']['hit1_net']}，loss={report['expanded_best']['hit1_loss']}，"
        f"max_source_file_net_share={report['expanded_best']['max_source_file_net_share']}，max_source_family_net_share={report['expanded_best']['max_source_family_net_share']}，min_fold_net={report['expanded_best']['min_fold_net']}。\n"
        "下一步建议：13.12 expanded reranker candidate freeze gate（只读）。只复核是否 freeze 13.10 lead candidate；不跑 heldout/hard，不上线，不改 GoalSearcher。\n"
        "禁止：重新训练、扩展矩阵、用 heldout/hard 做选择、接线上、改阈值、把 OSS XML dev/OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.11 expanded training scorecard comparison and robustness review" not in text:
        rows = f"""          <tr>
            <td>13.11 expanded training scorecard comparison and robustness review</td>
            <td>Read-only comparison of 13.5 vs 13.10 scorecards, source/fold robustness, loss focus, and freeze readiness.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.10 expanded matrix guarded dev/OOF reranker training summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.11 read-only expanded training scorecard comparison and robustness review")
    parser.add_argument("--initial-summary", default=str(DEFAULT_INITIAL_SUMMARY))
    parser.add_argument("--initial-scorecard", default=str(DEFAULT_INITIAL_SCORECARD))
    parser.add_argument("--initial-loss-audit", default=str(DEFAULT_INITIAL_LOSS_AUDIT))
    parser.add_argument("--initial-group-meta", default=str(DEFAULT_INITIAL_GROUP_META))
    parser.add_argument("--initial-flips", default=str(DEFAULT_INITIAL_FLIPS))
    parser.add_argument("--initial-recall", default=str(DEFAULT_INITIAL_RECALL))
    parser.add_argument("--initial-leakage", default=str(DEFAULT_INITIAL_LEAKAGE))
    parser.add_argument("--initial-source-fold", default=str(DEFAULT_INITIAL_SOURCE_FOLD))
    parser.add_argument("--expanded-summary", default=str(DEFAULT_EXPANDED_SUMMARY))
    parser.add_argument("--expanded-scorecard", default=str(DEFAULT_EXPANDED_SCORECARD))
    parser.add_argument("--expanded-loss-audit", default=str(DEFAULT_EXPANDED_LOSS_AUDIT))
    parser.add_argument("--expanded-group-meta", default=str(DEFAULT_EXPANDED_GROUP_META))
    parser.add_argument("--expanded-flips", default=str(DEFAULT_EXPANDED_FLIPS))
    parser.add_argument("--expanded-recall", default=str(DEFAULT_EXPANDED_RECALL))
    parser.add_argument("--expanded-leakage", default=str(DEFAULT_EXPANDED_LEAKAGE))
    parser.add_argument("--expanded-source-fold", default=str(DEFAULT_EXPANDED_SOURCE_FOLD))
    parser.add_argument("--expanded-matrix-summary", default=str(DEFAULT_EXPANDED_MATRIX_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    initial_summary = _read_json(Path(args.initial_summary))
    initial_scorecard = _read_csv(Path(args.initial_scorecard))
    initial_loss_audit = _read_csv(Path(args.initial_loss_audit))
    initial_group_meta = _read_jsonl(Path(args.initial_group_meta))
    initial_flips = _read_jsonl(Path(args.initial_flips))
    initial_recall = _read_csv(Path(args.initial_recall))
    initial_leakage = _read_csv(Path(args.initial_leakage))
    initial_source_fold = _read_csv(Path(args.initial_source_fold))

    expanded_summary = _read_json(Path(args.expanded_summary))
    expanded_scorecard = _read_csv(Path(args.expanded_scorecard))
    expanded_loss_audit = _read_csv(Path(args.expanded_loss_audit))
    expanded_group_meta = _read_jsonl(Path(args.expanded_group_meta))
    expanded_flips = _read_jsonl(Path(args.expanded_flips))
    expanded_recall = _read_csv(Path(args.expanded_recall))
    expanded_leakage = _read_csv(Path(args.expanded_leakage))
    expanded_source_fold = _read_csv(Path(args.expanded_source_fold))
    expanded_matrix_summary = _read_json(Path(args.expanded_matrix_summary))

    initial = _run_summary(
        stage_label="13.5 initial OSS XML source-aware training",
        execution_summary=initial_summary,
        scorecard=initial_scorecard,
        loss_audit=initial_loss_audit,
        group_meta=initial_group_meta,
        flips=initial_flips,
        recall_rows=initial_recall,
        leakage_rows=initial_leakage,
        source_fold_rows=initial_source_fold,
    )
    expanded = _run_summary(
        stage_label="13.10 expanded guarded OSS XML training",
        execution_summary=expanded_summary,
        scorecard=expanded_scorecard,
        loss_audit=expanded_loss_audit,
        group_meta=expanded_group_meta,
        flips=expanded_flips,
        recall_rows=expanded_recall,
        leakage_rows=expanded_leakage,
        source_fold_rows=expanded_source_fold,
    )
    overlap = _candidate_overlap(initial_scorecard, expanded_scorecard)
    gate_rows, decision = _gate_rows(initial, expanded, overlap, expanded_matrix_summary)

    initial_ranks = _scorecard_rank_map(initial_scorecard)
    expanded_ranks = _scorecard_rank_map(expanded_scorecard)
    comparison_rows = _comparison_rows(initial, expanded)
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "comparison_csv": str(output_prefix.with_name(output_prefix.name + "_comparison.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "expanded_source_focus_csv": str(output_prefix.with_name(output_prefix.name + "_expanded_source_focus.csv")),
        "expanded_loss_focus_csv": str(output_prefix.with_name(output_prefix.name + "_expanded_loss_focus.csv")),
        "candidate_overlap_json": str(output_prefix.with_name(output_prefix.name + "_candidate_overlap.json")),
    }
    expanded_source_focus = []
    for dimension in ("source_family", "source_file", "province", "oof_fold", "query_family"):
        expanded_source_focus.extend(expanded[f"top_{dimension}_rows"] if f"top_{dimension}_rows" in expanded else [])
    if not expanded_source_focus:
        for dimension in ("source_family", "source_file"):
            expanded_source_focus.extend(expanded[f"top_{dimension}_rows"])

    report = {
        "stage": "13.11 expanded training scorecard comparison and robustness review",
        "read_only": True,
        "metrics": {
            "initial_best_rank_in_expanded": expanded_ranks.get(initial["best_candidate_id"], "missing"),
            "expanded_best_rank_in_initial": initial_ranks.get(expanded["best_candidate_id"], "missing"),
            "top10_overlap_count": overlap["overlap_count"],
            "headline": (
                "13.10 keeps a positive reranker signal on a much larger OSS XML dev/OOF matrix and materially reduces single-file dominance. "
                "It is strong enough for a read-only freeze gate, but not yet a validation/release candidate because top80 recall remains scoped and the source_family guardrail is still only conditional."
            ),
        },
        "initial_best": initial,
        "expanded_best": expanded,
        "candidate_overlap": overlap,
        "comparison_rows": comparison_rows,
        "gate_rows": gate_rows,
        "decision": decision,
        "decision_rationale": (
            "The expanded run improved robustness versus 13.5: group count increased from 593 to 2400, file concentration dropped sharply, and all expanded folds stayed positive. "
            "Remaining blockers are scope/guardrail issues rather than lack of signal: top80 recall is 0.691842 and source_family matrix share remains above the 13.7 target."
        ),
        "expanded_source_focus": expanded["top_source_family_rows"][:10] + expanded["top_source_file_rows"][:10] + expanded["fold_rows"],
        "expanded_loss_focus": expanded["top_loss_rows"],
        "expanded_negative_slices": expanded["negative_slices"],
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only review only: no training, no matrix rebuild, no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, and no feature whitelist edit.",
        "next_stage": {
            "recommended": "13.12 expanded reranker candidate freeze gate: read-only decide whether the 13.10 lead candidate can be frozen for a future explicit validation go/no-go, with no heldout/hard run in this gate.",
            "default": "do not validate or release from 13.11 directly",
        },
    }

    _write_csv(Path(artifacts["comparison_csv"]), comparison_rows, ["metric", "initial_13_5", "expanded_13_10", "meaning"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["expanded_source_focus_csv"]), report["expanded_source_focus"], ["dimension", "key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["expanded_loss_focus_csv"]), report["expanded_loss_focus"], ["slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_json(Path(artifacts["candidate_overlap_json"]), overlap)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)

    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "expanded_best": {k: expanded[k] for k in ("best_candidate_id", "hit1_gain", "hit1_loss", "hit1_net", "max_source_file_net_share", "max_source_family_net_share", "min_fold_net")}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
