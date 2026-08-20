from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
BRANCH_DECISION = AGENT_STATE / "goal_17x_branch_blocker_audit_strategy_decision_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_broader_oss_recall_index_redesign_scope"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _design_lanes() -> list[dict[str, Any]]:
    return [
        {
            "lane_id": "R17_A_family_conflict_pair_index",
            "role": "near-neighbor disambiguation",
            "target_families": "pump,rebar",
            "index_delta": "Build same-family conflict-pair records for OSS quota ids that share bill_name_key/query tokens but differ by quota concept.",
            "new_fields": "conflict_pair_ids, conflict_pair_reason, positive_anchor_terms, negative_anchor_terms, quota_concept_label",
            "example_need": "rebar: distinguish 均压环 / 避雷引下线 / 柱主筋与圈梁钢筋焊接 instead of treating all strong OSS support as interchangeable.",
            "future_shadow_gate": "dev/OOF only; positive_family_count>=2; top1_losses=0; false candidates below P17_G=19; branch false-only rows reduced.",
            "blocked_now": "Do not build the index in 17.26; this lane only defines what 17.27 may build.",
        },
        {
            "lane_id": "R17_B_bill_text_signature_index",
            "role": "query evidence representation",
            "target_families": "pump,rebar",
            "index_delta": "Persist bill_text-derived action/material/spec/location signatures separately from generic overlap.",
            "new_fields": "bill_action_signature, bill_material_signature, bill_spec_signature, bill_location_signature, signature_conflict_flags",
            "example_need": "pump: distinguish pump installation/equipment variants; rebar: use 卫生间/底板/钢筋网/焊接/均压/引下线-style signatures as explicit evidence.",
            "future_shadow_gate": "candidate must have positive signature match and no hard signature conflict unless exact local quota match exists.",
            "blocked_now": "Do not add parser rules or tune thresholds in 17.26.",
        },
        {
            "lane_id": "R17_C_source_family_contrast_index",
            "role": "support quality redesign",
            "target_families": "pump,rebar,concrete",
            "index_delta": "Separate duplicated files from independent source-family support and add per-family support entropy.",
            "new_fields": "independent_source_family_count, source_entropy, duplicate_cluster_id, accepted_oss_support_count, generated_or_trace_support_count",
            "example_need": "Current support_count/source_family_count can be high for the wrong near-neighbor; support must prove independent concept agreement, not just repeated bill_name.",
            "future_shadow_gate": "non-exact rescue requires independent_source_family_count>=2 and source_entropy above branch minimum.",
            "blocked_now": "Do not reclassify source provenance or accept new sources in 17.26.",
        },
        {
            "lane_id": "R17_D_local_quota_neighbor_graph",
            "role": "local-province safety alignment",
            "target_families": "pump,rebar",
            "index_delta": "Attach local quota neighbor graph features so OSS concept candidates can be compared against same-book/local near-neighbors before injection.",
            "new_fields": "local_neighbor_ids, local_neighbor_concept_gap, same_book_neighbor_rank, local_title_contrast_terms",
            "example_need": "Before injecting 4-9-40, compare it against local 4-9-44 and require the query signature to favor the challenger over the baseline neighbor.",
            "future_shadow_gate": "no injection when baseline rank1 is a same-book near-neighbor and challenger lacks a strictly stronger local concept match.",
            "blocked_now": "Do not change GoalSearcher rank1 behavior in 17.26.",
        },
        {
            "lane_id": "R17_E_family_specific_candidate_pool",
            "role": "future execution matrix wrapper",
            "target_families": "pump,rebar",
            "index_delta": "Define a future candidate-pool generator that uses R17_A-D features, not current P17 threshold tweaks.",
            "new_fields": "family_specific_pool_reason, rescue_branch_id, safety_veto_reason, evidence_vector_version",
            "example_need": "Only after richer evidence exists should pump/rebar re-enter dev/OOF shadow as a fixed matrix.",
            "future_shadow_gate": "must beat P17_F on positive_family_count while preserving loss=0 and false<=12 preferred / <=15 hard max.",
            "blocked_now": "Do not execute a new shadow matrix until at least one richer index artifact exists.",
        },
    ]


def _artifact_manifest() -> list[dict[str, Any]]:
    return [
        {
            "artifact": "conflict_pair_registry",
            "required_for_future_execution": True,
            "path_pattern": "reports/agent_state/goal_17x_oss_recall_index_v2_conflict_pairs.csv",
            "purpose": "List same-family near-neighbor quota concepts and the positive/negative anchor terms that distinguish them.",
        },
        {
            "artifact": "signature_field_manifest",
            "required_for_future_execution": True,
            "path_pattern": "reports/agent_state/goal_17x_oss_recall_index_v2_signature_fields.csv",
            "purpose": "Define action/material/spec/location signatures extracted from OSS bill text and local quota names.",
        },
        {
            "artifact": "source_support_quality_manifest",
            "required_for_future_execution": True,
            "path_pattern": "reports/agent_state/goal_17x_oss_recall_index_v2_source_quality.csv",
            "purpose": "Separate independent OSS source-family support from duplicated or trace-derived support.",
        },
        {
            "artifact": "local_neighbor_graph_manifest",
            "required_for_future_execution": True,
            "path_pattern": "reports/agent_state/goal_17x_oss_recall_index_v2_local_neighbors.csv",
            "purpose": "Expose same-book/local near-neighbor conflicts before recall injection.",
        },
        {
            "artifact": "v2_index_build_manifest",
            "required_for_future_execution": True,
            "path_pattern": "reports/agent_state/goal_17x_oss_recall_index_v2_build_manifest.json",
            "purpose": "Record input XML/assets, row counts, family distribution, provenance hash, and default-off path.",
        },
    ]


def _future_command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "17.27 future build scope or explicit build go",
            "command": "python tools\\goal_17x_build_oss_recall_index_v2.py --family pump,rebar --output data\\goal_search\\oss_recall_index_17x_v2.jsonl --manifest reports\\agent_state\\goal_17x_oss_recall_index_v2_build_manifest.json",
            "allowed_after": "explicit go to build a default-off v2 index artifact from the locked 17.26 scope",
            "blocked_now": True,
        },
        {
            "stage": "future dev/OOF shadow only after v2 artifact acceptance",
            "command": "python tools\\goal_17x_oss_recall_index_v2_dev_oof_shadow.py --index data\\goal_search\\oss_recall_index_17x_v2.jsonl --candidate all --progress-every 10",
            "allowed_after": "v2 index artifact exists, manifest passes acceptance, and explicit dev/OOF execution go is provided",
            "blocked_now": True,
        },
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "scope_only", "target": "no index build, no dev/OOF execution, no validation", "failure_action": "invalidate 17.26"},
        {"check": "pump_rebar_evidence_specificity", "target": "future artifact must add conflict/signature evidence beyond support_count/overlap", "failure_action": "do not proceed to build"},
        {"check": "source_quality", "target": "future artifact must separate independent source support from duplicate/source trace support", "failure_action": "do not use source_family_count as gate"},
        {"check": "local_neighbor_safety", "target": "future artifact must compare same-book/local near-neighbors before injection", "failure_action": "block rank1-changing recall injection"},
        {"check": "dev_oof_future_gate", "target": "future shadow must beat P17_F positive_family_count while keeping top1_loss=0", "failure_action": "no freeze"},
        {"check": "heldout_hard_boundary", "target": "heldout/hard not used for design or selection", "failure_action": "stop and report drift"},
        {"check": "default_off_boundary", "target": "no default enablement, no online integration, no GoalSearcher default change", "failure_action": "stop and report drift"},
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {"action": "reopen_p17_threshold_tweaks", "blocked": True, "reason": "17.25 showed P17 tweaks do not create pump/rebar positive family retention."},
        {"action": "build_v2_index_now", "blocked": True, "reason": "17.26 defines scope only; build requires explicit future go."},
        {"action": "run_dev_oof_shadow_now", "blocked": True, "reason": "No v2 artifact exists and 17.26 is not an execution stage."},
        {"action": "heldout_hard_validation", "blocked": True, "reason": "No frozen candidate exists."},
        {"action": "default_enable_or_goal_searcher_change", "blocked": True, "reason": "This is offline/default-off strategy work only."},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.26 Broader OSS Recall/Index Redesign Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Why This Turn",
        "",
        "17.25 stopped the P17 family-retention lane: a baseline-rank1 veto fixes safety but not pump/rebar positive evidence. The next useful work is to redesign OSS index evidence, not tweak P17 thresholds.",
        "",
        "## Design Lanes",
        "",
        "| lane | role | target families | index delta | future gate |",
        "|---|---|---|---|---|",
    ]
    for row in report["design_lanes"]:
        lines.append(f"| {row['lane_id']} | {row['role']} | {row['target_families']} | {row['index_delta']} | {row['future_shadow_gate']} |")
    lines.extend(["", "## Acceptance Checks", "", "| check | target | failure action |", "|---|---|---|"])
    for row in report["acceptance_checks"]:
        lines.append(f"| {row['check']} | {row['target']} | {row['failure_action']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    source = _read_json(BRANCH_DECISION)
    if source.get("decision") != "stop_p17_family_retention_lane_return_to_broader_oss_recall_index_redesign":
        raise ValueError(f"unexpected 17.25 decision: {source.get('decision')}")

    design_lanes = _design_lanes()
    artifact_manifest = _artifact_manifest()
    command_contract = _future_command_contract()
    acceptance_checks = _acceptance_checks()
    blocked_actions = _blocked_actions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    lanes_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_design_lanes.csv")
    artifacts_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_artifact_manifest.csv")
    commands_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_command_contract.csv")
    checks_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv")
    blocked_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv")

    report = {
        "stage": "17.26 broader OSS recall/index redesign scope",
        "decision": "scope_locked_request_explicit_v2_index_build_scope_or_go",
        "read_only_scope": True,
        "index_build_performed": False,
        "dev_oof_execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "source_stage": source["stage"],
        "source_decision": source["decision"],
        "design_lanes": design_lanes,
        "artifact_manifest": artifact_manifest,
        "command_contract": command_contract,
        "acceptance_checks": acceptance_checks,
        "blocked_actions": blocked_actions,
        "next_boundary": (
            "17.27 may either define exact v2 index build implementation scope or, with explicit go, build a default-off OSS recall index v2 artifact. "
            "It must not run heldout/hard, default-enable recall, change GoalSearcher defaults, or return to P17 threshold tweaks."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "design_lanes_csv": str(lanes_csv),
            "artifact_manifest_csv": str(artifacts_csv),
            "command_contract_csv": str(commands_csv),
            "acceptance_checks_csv": str(checks_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.26 only defines the broader OSS recall/index redesign scope. It does not build an index, run dev/OOF, train, tune from heldout/hard, "
            "default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(lanes_csv, design_lanes, ["lane_id", "role", "target_families", "index_delta", "new_fields", "example_need", "future_shadow_gate", "blocked_now"])
    _write_csv(artifacts_csv, artifact_manifest, ["artifact", "required_for_future_execution", "path_pattern", "purpose"])
    _write_csv(commands_csv, command_contract, ["stage", "command", "allowed_after", "blocked_now"])
    _write_csv(checks_csv, acceptance_checks, ["check", "target", "failure_action"])
    _write_csv(blocked_csv, blocked_actions, ["action", "blocked", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
