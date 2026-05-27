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
DEFAULT_INVENTORY = AGENT_STATE / "goal_15x_candidate_generation_evidence_inventory_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_alias_index_recall_expansion_plan_definition"
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


def _target_family_rows(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in inventory.get("top_missing_families", []):
        family = row["query_family"]
        missing = int(row["missing_groups"])
        share = float(row["share"])
        if family == "<empty>":
            disposition = "measure_only_until_parser_or_taxonomy_disposition"
        elif family in {"concrete", "rebar", "pipe", "pump", "support"}:
            disposition = "eligible_for_15A_15B_plan_with_strict_guards"
        else:
            disposition = "eligible_for_shadow_metrics_after_top_family_guards"
        rows.append(
            {
                "query_family": family,
                "top80_missing_groups": missing,
                "missing_share": share,
                "plan_disposition": disposition,
                "guard_note": "require observable family/action/material support; no taxonomy-empty takeover",
            }
        )
    return rows


def _candidate_lanes(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    p = inventory["presence_summary"]
    return [
        {
            "lane_id": "15A_oss_query_to_quota_alias_expansion",
            "status": "plan_ready",
            "primary_input": "OSS bill-to-quota pairs plus dev/OOF query and expected_ids",
            "expansion_source": "normalized OSS bill_name/bill_text aliases mapped to local quota_id",
            "merge_policy": "append alias candidates after baseline lexical pool in shadow mode; never replace baseline candidates",
            "dedup_key": "province|book|quota_id|normalized_alias",
            "first_metric": "top80_present_delta",
            "required_delta": "positive top80/top20 presence movement on dev/OOF target groups",
            "target_groups": p["top80_missing_groups"],
            "risk": "alias overgeneralization from broad construction terms",
            "guard": "require province/book compatibility plus at least one family/action/material or numeric support",
        },
        {
            "lane_id": "15B_oss_family_action_material_inverted_index",
            "status": "plan_ready",
            "primary_input": "OSS-derived family/action/material/numeric tokens plus current top80 feature rows",
            "expansion_source": "inverted index keyed by query_family/action/material/spec tokens",
            "merge_policy": "append bounded top-k index candidates behind baseline pool; score as recall-source evidence only",
            "dedup_key": "province|book|quota_id|index_key",
            "first_metric": "top20_present_delta",
            "required_delta": "positive top80/top20 movement with false-candidate risk below stop thresholds",
            "target_groups": p["candidate_generation_target_groups"],
            "risk": "taxonomy-empty or broad family labels can flood wrong same-domain candidates",
            "guard": "block query_family_empty takeover; require two observable supports for broad families",
        },
        {
            "lane_id": "15D_oss_recall_expansion_shadow_ab",
            "status": "measurement_harness_ready",
            "primary_input": "baseline candidate pool plus 15A/15B proposed expansion candidates",
            "expansion_source": "shadow-only merged candidate pool",
            "merge_policy": "measure baseline vs expanded presence; no online ranking or release",
            "dedup_key": "split|group_id|candidate_quota_id|candidate_source",
            "first_metric": "presence_movement_by_rank_bucket",
            "required_delta": "top80/top20/top5 movement decomposed separately from Top1 ranking",
            "target_groups": p["candidate_generation_target_groups"],
            "risk": "recall movement may not survive rank1-safe reranking",
            "guard": "report recall movement, rank movement, and false-candidate risk separately",
        },
    ]


def _metric_manifest() -> list[dict[str, Any]]:
    return [
        {"metric": "top80_present_delta", "definition": "expanded_top80_present - baseline_top80_present", "required": "positive"},
        {"metric": "top20_present_delta", "definition": "expanded_top20_present - baseline_top20_present", "required": "positive or explained"},
        {"metric": "top5_present_delta", "definition": "expanded_top5_present - baseline_top5_present", "required": "non-negative"},
        {"metric": "false_candidate_rate", "definition": "new candidates failing family/action/material/numeric guards divided by injected candidates", "required": "below stop threshold"},
        {"metric": "source_family_max_share", "definition": "largest source_family share of positive movement", "required": "not single-source dominated"},
        {"metric": "rank1_preservation_shadow", "definition": "baseline rank1 remains visible and not demoted in shadow output", "required": "must hold"},
    ]


def _guard_manifest() -> list[dict[str, Any]]:
    return [
        {"guard": "dev_oof_only", "rule": "15.2 plans future dev/OOF shadow execution only; heldout/hard stays blocked."},
        {"guard": "baseline_pool_preservation", "rule": "Candidate expansion can append candidates but cannot drop or reorder baseline candidates in the recall measurement stage."},
        {"guard": "local_quota_only", "rule": "OSS evidence may suggest concepts; injected candidate IDs must exist in the local province/book quota universe."},
        {"guard": "taxonomy_empty_no_takeover", "rule": "query_family_empty and top1_family_empty slices are measured separately and cannot drive unguarded injection."},
        {"guard": "broad_family_two_supports", "rule": "Concrete/rebar/pipe/pump/support require at least two observable supports among family, action, material, numeric/spec, or book compatibility."},
        {"guard": "source_robustness", "rule": "Positive movement must be decomposed by source_family/province/book; single-source wins are diagnostic only."},
        {"guard": "false_candidate_stop", "rule": "Stop if conflict-heavy families get more new false candidates than expected-presence movement."},
    ]


def _required_outputs() -> list[dict[str, Any]]:
    return [
        {"artifact": "alias_candidate_manifest", "format": "csv/jsonl", "purpose": "15A normalized OSS aliases with quota_id, source_family, province/book, support counts"},
        {"artifact": "inverted_index_manifest", "format": "csv/json", "purpose": "15B family/action/material/numeric keys and bounded candidate lists"},
        {"artifact": "shadow_presence_scorecard", "format": "json/md", "purpose": "baseline vs expanded top80/top20/top5 movement"},
        {"artifact": "false_candidate_audit", "format": "csv", "purpose": "new candidate conflicts by family/action/material/numeric guard"},
        {"artifact": "source_robustness_audit", "format": "csv", "purpose": "movement split by source_family/province/book/fold"},
        {"artifact": "loss_slice_report", "format": "csv/md", "purpose": "negative or ambiguous slices that must block freeze/release"},
    ]


def _stop_conditions() -> list[dict[str, Any]]:
    return [
        {"condition": "heldout_or_hard_access", "action": "stop_and_report_boundary_breach"},
        {"condition": "top80_present_delta_non_positive_for_target_groups", "action": "stop_do_not_continue_to_reranker"},
        {"condition": "false_candidate_risk_exceeds_presence_gain", "action": "stop_redesign_guards"},
        {"condition": "source_family_single_source_dominance", "action": "stop_mark_diagnostic_only"},
        {"condition": "taxonomy_empty_drives_majority_gain", "action": "stop_route_to_DQ_parser_taxonomy"},
        {"condition": "baseline_candidates_dropped_or_reordered_in_recall_stage", "action": "stop_fix_harness"},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "15.2",
            "command": "python tools/goal_15x_alias_index_recall_expansion_plan_definition.py",
            "allowed": "read-only plan definition, artifact contracts, roadmap update",
            "forbidden": "candidate injection, training, heldout/hard access, GoalSearcher edits",
        },
        {
            "stage": "15.3_future",
            "command": "python tools/goal_15x_alias_index_recall_shadow_execution.py",
            "allowed": "dev/OOF-only shadow candidate expansion if explicitly authorized",
            "forbidden": "online release, heldout/hard selection, replacing baseline candidate pool",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lanes = [["lane", "status", "first metric", "target groups", "guard"]]
    for row in report["candidate_lanes"]:
        lanes.append([row["lane_id"], row["status"], row["first_metric"], row["target_groups"], row["guard"]])
    metrics = [["metric", "required"]]
    for row in report["metric_manifest"]:
        metrics.append([row["metric"], row["required"]])
    targets = [["family", "missing", "disposition"]]
    for row in report["target_family_rows"][:10]:
        targets.append([row["query_family"], row["top80_missing_groups"], row["plan_disposition"]])
    lines = [
        "# 15.2 OSS Alias / Index Recall Expansion Plan Definition",
        "",
        "Read-only plan definition for 15A/15B/15D. No candidate injection, training, heldout/hard access, or GoalSearcher edit was performed.",
        "",
        "## Candidate Lanes",
        "",
        _md_table(lanes),
        "",
        "## Measurement Contract",
        "",
        _md_table(metrics),
        "",
        "## Target Families",
        "",
        _md_table(targets),
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
    p = report["inventory_presence_summary"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.2 OSS alias/index recall expansion plan definition completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "15.x is now ready to request a bounded dev/OOF-only shadow execution for OSS alias/index candidate expansion.",
        "",
        "## Why This Is The Right Lever",
        "",
        f"- top80 missing groups: `{p['top80_missing_groups']}`",
        f"- candidate-generation target groups: `{p['candidate_generation_target_groups']}`",
        "- 15A and 15B are plan-ready; 15D can measure movement without release.",
        "- 15C remains held until province/book crosswalk inventory exists.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not use heldout/hard for 15.3.",
        "- Do not edit GoalSearcher or online thresholds.",
        "- Do not replace baseline candidates; append and measure in shadow only.",
        "- Stop if presence delta is non-positive, false-candidate risk dominates, or source/taxonomy artifacts dominate.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    p = report["inventory_presence_summary"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.2 OSS alias/index recall expansion plan definition 已完成。\n"
        f"结论：{report['decision']}。target_groups={p['candidate_generation_target_groups']}，top80_missing={p['top80_missing_groups']}。\n"
        "下一步建议：15.3 OSS alias/index dev/OOF-only shadow execution authorization gate。默认不执行，除非明确 go。\n"
        "允许范围：只跑 dev/OOF shadow candidate expansion，产出 top80/top20/top5 presence delta、false-candidate audit、source robustness、loss slices。\n"
        "禁止：heldout/hard、上线、改 GoalSearcher、替换 baseline candidate pool、训练、调参、把 recall movement 宣称为 Top1 gain。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.2 OSS alias/index recall expansion plan definition" not in text:
        row = f"""          <tr>
            <td>15.2 OSS alias/index recall expansion plan definition</td>
            <td>Read-only definition of alias/index expansion source, merge/dedup policy, guards, metrics, stop conditions, and required shadow artifacts.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.2 OSS alias/index recall expansion plan definition")
    parser.add_argument("--inventory-summary", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    inventory = _read_json(args.inventory_summary)
    candidate_lanes = _candidate_lanes(inventory)
    metric_manifest = _metric_manifest()
    guard_manifest = _guard_manifest()
    required_outputs = _required_outputs()
    stop_conditions = _stop_conditions()
    command_contract = _command_contract()
    target_family_rows = _target_family_rows(inventory)

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "metric_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_metric_manifest.csv")),
        "guard_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guard_manifest.csv")),
        "required_outputs_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_required_outputs.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "target_family_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_target_family_manifest.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    decision = "plan_ready_for_15_3_dev_oof_shadow_execution_authorization_gate"
    report = {
        "stage": "15.2 OSS alias/index recall expansion plan definition",
        "read_only_plan": True,
        "decision": decision,
        "inventory_presence_summary": inventory["presence_summary"],
        "candidate_lanes": candidate_lanes,
        "metric_manifest": metric_manifest,
        "guard_manifest": guard_manifest,
        "required_outputs": required_outputs,
        "stop_conditions": stop_conditions,
        "command_contract": command_contract,
        "target_family_rows": target_family_rows,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.2 is read-only. It did not implement candidate injection, train, tune, read heldout/hard, "
            "edit GoalSearcher, change thresholds, release code, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.3 OSS alias/index dev/OOF-only shadow execution authorization gate",
            "description": (
                "Collect explicit go/no-go for a bounded dev/OOF shadow run that appends OSS alias/index candidates, "
                "measures top80/top20/top5 presence delta, audits false candidates and source robustness, and stops on any boundary breach."
            ),
            "default": "do_not_execute_without_explicit_go",
        },
    }

    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["candidate_lanes_csv"]), candidate_lanes, ["lane_id", "status", "primary_input", "expansion_source", "merge_policy", "dedup_key", "first_metric", "required_delta", "target_groups", "risk", "guard"])
    _write_csv(Path(artifacts["metric_manifest_csv"]), metric_manifest, ["metric", "definition", "required"])
    _write_csv(Path(artifacts["guard_manifest_csv"]), guard_manifest, ["guard", "rule"])
    _write_csv(Path(artifacts["required_outputs_csv"]), required_outputs, ["artifact", "format", "purpose"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, ["condition", "action"])
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, ["stage", "command", "allowed", "forbidden"])
    _write_csv(Path(artifacts["target_family_manifest_csv"]), target_family_rows, ["query_family", "top80_missing_groups", "missing_share", "plan_disposition", "guard_note"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
