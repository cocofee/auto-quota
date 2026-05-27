from __future__ import annotations

import argparse
import csv
import json
import re
import sys
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
DEFAULT_VALIDATION = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_validation_package_review_summary.json"
DEFAULT_DEV_OOF = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_execution_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_candidate_generation_recall_expansion_strategy_definition"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _validation_metrics(validation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for split in validation.get("split_reports", []):
        m = split.get("metrics", {})
        rows.append(
            {
                "split": m.get("split", ""),
                "hit1_gain": m.get("hit1_gain", 0),
                "hit1_loss": m.get("hit1_loss", 0),
                "hit1_net": m.get("hit1_net", 0),
                "rank1_loss_count": m.get("rank1_loss_count", 0),
                "applied_groups": m.get("applied_groups", 0),
                "recall_missing_groups": m.get("recall_missing_groups", 0),
                "top80_recall_rate": m.get("top80_recall_rate", 0),
            }
        )
    return rows


def _strategy_lanes() -> list[dict[str, Any]]:
    return [
        {
            "lane_id": "15A_oss_query_to_quota_alias_expansion",
            "lever": "candidate_generation",
            "idea": "Mine OSS bill_name/bill_text to quota_id pairs as query aliases, then inject alias-matched quota candidates before reranking.",
            "why_now": "Reranker gates are safe but rarely fire; alias expansion can make missing/low-rank correct quota appear earlier.",
            "first_metric": "top80_present_delta and top20_present_delta on dev/OOF",
            "risk": "alias overgeneralization can pull same-word wrong quotas",
            "guard": "province/book/family/action/material compatibility plus rank1-safe fallback",
            "priority": 1,
        },
        {
            "lane_id": "15B_oss_family_action_material_inverted_index",
            "lever": "candidate_pool_expansion",
            "idea": "Build an OSS-derived inverted index over family/action/material/numeric specs and merge it with current lexical candidates.",
            "why_now": "Many failures are top80 missing or rank-deep; structured OSS signals can recover same-domain candidates missed by BM25.",
            "first_metric": "candidate_pool_recall_delta by query_family and rank bucket",
            "risk": "taxonomy-empty and broad family labels may flood candidates",
            "guard": "taxonomy-empty guarded; require at least two observable supports for injection",
            "priority": 2,
        },
        {
            "lane_id": "15C_oss_province_crosswalk_candidate_backfill",
            "lever": "cross_province_candidate_generation",
            "idea": "Use OSS human pairs to map similar quota concepts across provinces/books, then backfill province-local candidates when lexical query misses.",
            "why_now": "OSS province differences are mostly numbering/book variations around similar quota concepts.",
            "first_metric": "cross_province_backfill_hit_rate and false_candidate_rate on dev/OOF",
            "risk": "wrong province/book crosswalk can create plausible but invalid candidates",
            "guard": "never output foreign quota_id; crosswalk may only suggest local province candidates",
            "priority": 3,
        },
        {
            "lane_id": "15D_oss_recall_expansion_shadow_ab",
            "lever": "measurement",
            "idea": "Run candidate expansion in shadow mode and measure whether expected quota moves from missing/rank>20 into top80/top20/top5 before any reranker release.",
            "why_now": "Need proof that recall expansion, not only reranking, changes the bottleneck.",
            "first_metric": "top80/top20/top5 movement without rank1 release",
            "risk": "shadow gains may not survive scoring/reranking",
            "guard": "separate recall movement from final ranking movement",
            "priority": 4,
        },
    ]


def _evidence_requirements() -> list[dict[str, Any]]:
    return [
        {"requirement": "dev_oof_only_first", "details": "All 15.x design/execution starts on dev/OOF or OSS-derived training artifacts; heldout/hard only after freeze."},
        {"requirement": "recall_metric_first", "details": "Primary evidence is top80/top20/top5 expected-quota presence delta, not Top1 reranker gain."},
        {"requirement": "rank1_safety_second", "details": "Any candidate expansion must preserve current baseline rank1 unless later rank1-safe reranker gate approves."},
        {"requirement": "source_family_robustness", "details": "Positive recall gains cannot be dominated by one OSS source_family/province/book."},
        {"requirement": "false_candidate_audit", "details": "Every expansion lane must audit false positives by family/action/material/numeric conflict."},
        {"requirement": "no_foreign_quota_release", "details": "Cross-province OSS evidence may suggest concepts but final candidates must be valid local province quota IDs."},
    ]


def _input_manifest() -> list[dict[str, Any]]:
    return [
        {"input": "OSS XML samples", "path": "D:\\广联达临时文件\\oss_samples", "allowed_use": "human quantity-surveyor bill-to-quota evidence for alias/index/crosswalk construction"},
        {"input": "14.2 balanced OSS matrix", "path": "reports/agent_state/goal_14x_rank1_safe_source_robust_matrix", "allowed_use": "dev/OOF group/query/candidate labels and features"},
        {"input": "expanded splits dev/OOF", "path": "data/goal_search/splits_expanded/dev*.jsonl or existing dev artifacts", "allowed_use": "dev/OOF recall diagnostics only"},
        {"input": "heldout/hard", "path": "data/goal_search/splits_expanded/heldout.jsonl; hard.jsonl", "allowed_use": "blocked until future explicit validation go"},
        {"input": "GoalSearcher source code", "path": "src/goal_search/*", "allowed_use": "read-only interface analysis until separate implementation go"},
    ]


def _forbidden_actions() -> list[dict[str, Any]]:
    return [
        {"action": "do_not_release_14x_reranker", "reason": "14.14 validation neutral; no release evidence."},
        {"action": "do_not_use_heldout_hard_for_design", "reason": "15.x candidate generation must be selected on dev/OOF first."},
        {"action": "do_not_edit_GoalSearcher_in_15_0", "reason": "15.0 is strategy definition only."},
        {"action": "do_not_change_online_thresholds", "reason": "No release/integration gate exists."},
        {"action": "do_not_claim_general_Top1_gain", "reason": "Current validated gain is neutral and too sparse."},
        {"action": "do_not_treat_OSS_as_perfect_labels", "reason": "OSS is high-value human evidence, but still needs conflict/source audits."},
    ]


def _success_criteria() -> list[dict[str, Any]]:
    return [
        {"stage": "15.1_inventory_gate", "criterion": "Can identify existing artifacts sufficient to measure top80/top20/top5 candidate absence/presence deltas."},
        {"stage": "15.2_plan", "criterion": "Defines exact expansion source, merge policy, dedup key, safety guards, and required artifacts."},
        {"stage": "15.x_dev_oof_execution", "criterion": "Improves dev/OOF top80/top20 expected presence without material false-candidate or source-dominated artifacts."},
        {"stage": "future_freeze", "criterion": "Candidate generation plus rank1-safe reranker shows positive dev/OOF Top1 or clear recall movement with no rank1 regression."},
        {"stage": "future_validation", "criterion": "Heldout/hard only after freeze and explicit go; no validation tuning."},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "15.0",
            "command": "python tools/goal_15x_candidate_generation_recall_expansion_strategy_definition.py",
            "allowed": "read-only strategy definition and roadmap update",
            "forbidden": "training, implementation, heldout/hard, GoalSearcher edits",
        },
        {
            "stage": "15.1_next",
            "command": "python tools/goal_15x_candidate_generation_evidence_inventory.py",
            "allowed": "read-only inventory of existing OSS/dev/OOF artifacts and candidate-pool bottleneck metrics",
            "forbidden": "candidate injection, index changes, heldout/hard selection, online changes",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lane_rows = [["lane", "lever", "first metric", "guard"]]
    for row in report["strategy_lanes"]:
        lane_rows.append([row["lane_id"], row["lever"], row["first_metric"], row["guard"]])
    val_rows = [["split", "Top1 net", "rank1 loss", "applied", "top80 recall"]]
    for row in report["prior_validation_metrics"]:
        val_rows.append([row["split"], row["hit1_net"], row["rank1_loss_count"], row["applied_groups"], row["top80_recall_rate"]])
    lines = [
        "# 15.0 Candidate Generation / Recall Expansion Strategy Definition",
        "",
        "This is a read-only strategy definition. It closes the 14.x reranker-only release path and opens a recall/candidate-generation route using OSS evidence.",
        "",
        "## Why Switch",
        "",
        _md_table(val_rows),
        "",
        "R14V2_E is rank1-safe but validation-neutral. The next bottleneck is candidate-pool coverage and narrow gate activation, not another reranker gate tweak.",
        "",
        "## Candidate Lanes",
        "",
        _md_table(lane_rows),
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Next stage: `{report['next_stage']['recommended']}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.0 candidate generation / recall expansion strategy definition completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "14.x reranker-only path is closed for release because validation was rank1-safe but neutral. 15.x shifts the accuracy strategy toward OSS-driven candidate generation and recall expansion.",
        "",
        "## Why",
        "",
        "- R14V2_E dev/OOF improved to `+11` with zero rank1 loss.",
        "- Heldout/hard validation did not show enough coverage: heldout `0/0/0`, hard `1/0/+1`.",
        "- Correct next lever is to make the right quota enter top80/top20/top5 more often before rank1-safe reranking.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not release R14V2_E.",
        "- Do not use heldout/hard for 15.x design or selection.",
        "- Do not edit GoalSearcher until a future explicit implementation gate.",
        "- Treat OSS as high-value human evidence, but still audit source/family/false-candidate risks.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.0 candidate generation / recall expansion strategy definition 已完成。\n"
        "结论：14.x reranker-only release path 暂不继续；15.x 转向 OSS-driven candidate generation / recall expansion。\n"
        "下一步建议：15.1 candidate-pool/OSS recall expansion evidence inventory。只读盘点现有 OSS/dev/OOF artifacts，确认能否度量 top80/top20/top5 presence delta 和 false-candidate risk。\n"
        "禁止：用 heldout/hard 设计、改 GoalSearcher、上线、改阈值、宣称通用 Top1 gain。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.0 candidate generation / recall expansion strategy" not in text:
        row = f"""          <tr>
            <td>15.0 candidate generation / recall expansion strategy</td>
            <td>Read-only strategy definition for OSS-driven candidate generation, recall expansion, and rank1-safe reranking.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.0 candidate generation / recall expansion strategy definition")
    parser.add_argument("--validation-summary", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--dev-oof-summary", type=Path, default=DEFAULT_DEV_OOF)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    validation = _read_json(args.validation_summary)
    dev_oof = _read_json(args.dev_oof_summary)
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "strategy_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_strategy_lanes.csv")),
        "evidence_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_evidence_requirements.csv")),
        "input_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_input_manifest.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
        "success_criteria_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_success_criteria.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "15.0 candidate generation / recall expansion strategy definition",
        "read_only_strategy": True,
        "decision": "open_15x_candidate_generation_recall_expansion_route",
        "prior_validation_decision": validation.get("decision"),
        "prior_validation_metrics": _validation_metrics(validation),
        "prior_dev_oof_metrics": dev_oof.get("metrics", {}),
        "strategy_lanes": _strategy_lanes(),
        "evidence_requirements": _evidence_requirements(),
        "input_manifest": _input_manifest(),
        "forbidden_actions": _forbidden_actions(),
        "success_criteria": _success_criteria(),
        "command_contract": _command_contract(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.0 is read-only. It did not train, implement candidate expansion, read heldout/hard for selection, "
            "release R14V2_E, edit GoalSearcher, change thresholds, or claim general Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.1 candidate-pool/OSS recall expansion evidence inventory",
            "description": "Read-only inventory of existing OSS/dev/OOF artifacts to determine whether candidate generation can be measured by top80/top20/top5 presence deltas and false-candidate risk.",
            "default": "do_not_implement",
        },
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["strategy_lanes_csv"]), report["strategy_lanes"], ["lane_id", "lever", "idea", "why_now", "first_metric", "risk", "guard", "priority"])
    _write_csv(Path(artifacts["evidence_requirements_csv"]), report["evidence_requirements"], ["requirement", "details"])
    _write_csv(Path(artifacts["input_manifest_csv"]), report["input_manifest"], ["input", "path", "allowed_use"])
    _write_csv(Path(artifacts["forbidden_actions_csv"]), report["forbidden_actions"], ["action", "reason"])
    _write_csv(Path(artifacts["success_criteria_csv"]), report["success_criteria"], ["stage", "criterion"])
    _write_csv(Path(artifacts["command_contract_csv"]), report["command_contract"], ["stage", "command", "allowed", "forbidden"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
