from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INVENTORY = AGENT_STATE / "goal_12x_candidate_pool_rank_position_inventory_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_rank_position_design_gate"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.2 Rank-Position Bottleneck Design Gate",
        "",
        "Read-only gate for rank_2_5 / rank_6_10 support and slice purity.",
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
        "当前状态：12.2 rank-position bottleneck design gate 已完成。"
        f"gate_decision={report['metrics']['gate_decision']}；"
        f"global_repair_source_share={report['metrics']['global_repair_source_share']}；"
        f"non_global_rank_2_5_rows={report['metrics']['non_global_rank_2_5_rows']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.3 non-global rank_2_5 slice audit。只读审计剩余 24 条非 global-repair rank_2_5，"
        "判断是否存在窄域最小实现候选；默认仍不实现。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现全局 rank-position 规则、训练、调参、改阈值、改 GoalSearcher、"
            "使用 heldout/hard 做选择、或忽略 global_repair 单源支配风险。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.1 candidate-pool/rank-position inventory</td>"
    row = (
        "          <tr>\n"
        "            <td>12.2 rank-position bottleneck design gate</td>\n"
        "            <td>只读判断 rank_2_5/rank_6_10 切片是否足够干净，可否进入最小实现计划。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_rank_position_design_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_rank_position_design_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _slice_counts(rows: list[dict[str, str]], dims: list[str], label: str, limit: int = 15) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    total = len(rows)
    for dim in dims:
        counter = Counter(row.get(dim) or "<empty>" for row in rows)
        for key, count in counter.most_common(limit):
            output.append(
                {
                    "slice": label,
                    "dimension": dim,
                    "key": key,
                    "rows": count,
                    "share": round(count / total, 6) if total else 0,
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-summary", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    inventory = _read_json(args.inventory_summary)
    wrong_rank = _read_csv(args.wrong_rank)

    global_rows = [row for row in wrong_rank if row.get("source_file") == "global_repair_decision_table.csv"]
    non_global_rows = [row for row in wrong_rank if row.get("source_file") != "global_repair_decision_table.csv"]
    rank_2_5 = [row for row in wrong_rank if row.get("rank_bucket") == "rank_2_5"]
    rank_6_10 = [row for row in wrong_rank if row.get("rank_bucket") == "rank_6_10"]
    non_global_rank_2_5 = [row for row in non_global_rows if row.get("rank_bucket") == "rank_2_5"]
    non_global_rank_6_10 = [row for row in non_global_rows if row.get("rank_bucket") == "rank_6_10"]

    global_share = len(global_rows) / len(wrong_rank) if wrong_rank else 0.0
    direct_implementation_allowed = False
    design_gate_allowed = bool(non_global_rank_2_5)
    gate_decision = "hold_global_rank_position_implementation_audit_non_global_near_miss" if design_gate_allowed else "hold_no_clean_rank_position_support"

    gate_checks = [
        {
            "gate": "global_source_dominance",
            "status": "fail_for_direct_implementation" if global_share > 0.5 else "pass",
            "evidence": f"{len(global_rows)}/{len(wrong_rank)}={global_share:.6f}",
        },
        {
            "gate": "non_global_rank_2_5_support",
            "status": "pass_for_audit_only" if len(non_global_rank_2_5) >= 10 else "weak",
            "evidence": str(len(non_global_rank_2_5)),
        },
        {
            "gate": "non_global_rank_6_10_support",
            "status": "weak" if len(non_global_rank_6_10) < 10 else "pass_for_audit_only",
            "evidence": str(len(non_global_rank_6_10)),
        },
        {
            "gate": "implementation_boundary",
            "status": "blocked",
            "evidence": "no exact lever, loss budget, or slice-purity package yet",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass",
            "evidence": "read-only dev evidence; no heldout/hard selection",
        },
    ]
    candidate_lanes = [
        {
            "lane": "non_global_rank_2_5_slice_audit",
            "support_rows": len(non_global_rank_2_5),
            "status": "allow_next_read_only_audit",
            "why": "closest positives after excluding global_repair source dominance",
            "not_allowed": "implementation, training, threshold change",
        },
        {
            "lane": "rank_6_10_slice_audit",
            "support_rows": len(non_global_rank_6_10),
            "status": "defer_or_fold_into_12_3",
            "why": "lower support and further from rank1",
            "not_allowed": "implementation before rank_2_5 purity check",
        },
        {
            "lane": "global_repair_rank_position_rule",
            "support_rows": len(global_rows),
            "status": "blocked",
            "why": "single-source dominated; cannot generalize",
            "not_allowed": "direct implementation or broad Top1 claim",
        },
    ]
    slice_purity = (
        _slice_counts(non_global_rank_2_5, ["source_file", "province", "query_family", "top1_family"], "non_global_rank_2_5")
        + _slice_counts(non_global_rank_6_10, ["source_file", "province", "query_family", "top1_family"], "non_global_rank_6_10")
    )
    exact_lever_requirements = [
        {
            "requirement": "slice_purity",
            "meaning": "rank_2_5 candidate must not be explained by one source/province/query_family only",
        },
        {
            "requirement": "lever_specificity",
            "meaning": "future plan must name exact code path and scoring signal, not broad ranking intuition",
        },
        {
            "requirement": "loss_budget",
            "meaning": "future plan must define allowed new-loss count and rollback condition before implementation",
        },
        {
            "requirement": "fallback_contract",
            "meaning": "baseline behavior must remain default when conditions are not met",
        },
        {
            "requirement": "no_heldout_hard_selection",
            "meaning": "heldout/hard can only validate a frozen candidate after explicit go",
        },
    ]
    metrics = {
        "gate_decision": gate_decision,
        "wrong_rank_rows": len(wrong_rank),
        "global_repair_rows": len(global_rows),
        "global_repair_source_share": round(global_share, 6),
        "rank_2_5_rows": len(rank_2_5),
        "rank_6_10_rows": len(rank_6_10),
        "non_global_wrong_rank_rows": len(non_global_rows),
        "non_global_rank_2_5_rows": len(non_global_rank_2_5),
        "non_global_rank_6_10_rows": len(non_global_rank_6_10),
        "design_gate_allowed": design_gate_allowed,
        "implementation_allowed_now": direct_implementation_allowed,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "slice_purity_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_slice_purity.csv")),
        "exact_lever_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_exact_lever_requirements.csv")),
    }
    decision = (
        "Do not enter implementation from the global rank-position signal because wrong-rank evidence is dominated by "
        "global_repair_decision_table.csv. Allow only a narrow read-only 12.3 audit of the remaining non-global rank_2_5 "
        "near-miss rows to see whether an exact minimal lever exists."
    )
    report = {
        "stage": "Goal LTR v1 / 12.2 rank-position bottleneck design gate",
        "read_only": True,
        "source_artifacts": {
            "inventory_summary": str(args.inventory_summary),
            "wrong_rank": str(args.wrong_rank),
        },
        "metrics": metrics,
        "decision": decision,
        "inventory_context": {
            "primary_bottleneck": inventory["metrics"]["primary_bottleneck"],
            "dev_oof_source_dominated_gain": inventory["metrics"]["dev_oof_source_dominated_gain"],
        },
        "anti_drift_conclusion": (
            "12.2 is read-only. It blocks direct implementation due to global_repair source dominance and does not train, tune, "
            "change thresholds, edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, "
            "or use heldout/hard for selection."
        ),
        "next_stage": {
            "stage": "12.3 non-global rank_2_5 slice audit",
            "default": "read_only_audit_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["candidate_lanes_csv"]), candidate_lanes, list(candidate_lanes[0].keys()))
    _write_csv(Path(artifacts["slice_purity_csv"]), slice_purity, list(slice_purity[0].keys()) if slice_purity else ["slice"])
    _write_csv(Path(artifacts["exact_lever_requirements_csv"]), exact_lever_requirements, list(exact_lever_requirements[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
