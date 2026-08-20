from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
V2_SCOPE = AGENT_STATE / "goal_17x_broader_oss_recall_index_redesign_scope_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_v2_index_build_implementation_scope"


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


def _implementation_steps() -> list[dict[str, Any]]:
    return [
        {
            "step_id": "S1_clone_builder",
            "target_file": "tools/goal_17x_build_oss_recall_index_v2.py",
            "implementation": "Start from tools/goal_16x_build_oss_recall_index.py, keep XML parsing, province anchoring, source-family balancing, and local quota-id filtering.",
            "must_change": "Output only to data/goal_search/oss_recall_index_17x_v2.jsonl and reports/agent_state/goal_17x_oss_recall_index_v2_* artifacts.",
            "must_not_change": "Do not overwrite data/goal_search/oss_recall_index_17x_multifield.jsonl; do not edit src/goal_search/searcher.py or config defaults.",
        },
        {
            "step_id": "S2_conflict_pair_registry",
            "target_file": "reports/agent_state/goal_17x_oss_recall_index_v2_conflict_pairs.csv",
            "implementation": "For pump/rebar rows, group local same-book quota neighbors and OSS-supported quota ids by province/query_family/book/title-token overlap; emit near-neighbor pairs.",
            "must_change": "Add positive_anchor_terms, negative_anchor_terms, quota_concept_label, conflict_pair_ids.",
            "must_not_change": "Do not encode dev/OOF expected_id labels as conflict rules.",
        },
        {
            "step_id": "S3_signature_fields",
            "target_file": "reports/agent_state/goal_17x_oss_recall_index_v2_signature_fields.csv",
            "implementation": "Persist signatures derived from bill_name, bill_desc, bill_pattern, quota name, and extract_signal output.",
            "must_change": "Add bill_action_signature, bill_material_signature, bill_spec_signature, bill_location_signature, signature_conflict_flags.",
            "must_not_change": "Do not add new parser rules in 17.27; use existing extract_signal/tokenization only.",
        },
        {
            "step_id": "S4_source_quality",
            "target_file": "reports/agent_state/goal_17x_oss_recall_index_v2_source_quality.csv",
            "implementation": "Compute independent_source_family_count, duplicate_cluster_id, source_entropy, accepted_oss_support_count, and generated_or_trace_support_count from source files/families.",
            "must_change": "Keep raw support_count but add source-quality fields so future gates do not rely on raw count alone.",
            "must_not_change": "Do not reclassify owner acceptance or source provenance policy; only expose measurable fields.",
        },
        {
            "step_id": "S5_local_neighbor_graph",
            "target_file": "reports/agent_state/goal_17x_oss_recall_index_v2_local_neighbors.csv",
            "implementation": "For each candidate quota_id, collect same-book/local near-neighbor quota ids and title contrast terms from local quota.db.",
            "must_change": "Add local_neighbor_ids, local_neighbor_concept_gap, same_book_neighbor_rank, local_title_contrast_terms.",
            "must_not_change": "Do not run GoalSearcher or alter rank1 behavior.",
        },
        {
            "step_id": "S6_emit_v2_jsonl",
            "target_file": "data/goal_search/oss_recall_index_17x_v2.jsonl",
            "implementation": "Emit v2 JSONL rows with all v1 fields plus conflict/signature/source-quality/local-neighbor fields and evidence_vector_version=17x_v2.",
            "must_change": "Include build_manifest hash and family distribution; default-off artifact only.",
            "must_not_change": "Do not wire the v2 artifact into runtime defaults.",
        },
    ]


def _v2_field_schema() -> list[dict[str, Any]]:
    return [
        {"field": "evidence_vector_version", "type": "string", "required": True, "source": "builder constant", "purpose": "Mark v2 rows without changing v1 artifact."},
        {"field": "quota_concept_label", "type": "string", "required": True, "source": "quota name + anchor terms", "purpose": "Separate near-neighbor concepts inside the same family."},
        {"field": "conflict_pair_ids", "type": "list[string]", "required": False, "source": "local neighbor grouping", "purpose": "Expose likely same-family false-neighbor conflicts."},
        {"field": "positive_anchor_terms", "type": "list[string]", "required": False, "source": "bill/quota term contrast", "purpose": "Terms that should favor this quota concept."},
        {"field": "negative_anchor_terms", "type": "list[string]", "required": False, "source": "neighbor term contrast", "purpose": "Terms that should block or demote this quota concept."},
        {"field": "bill_action_signature", "type": "list[string]", "required": False, "source": "extract_signal + tokens", "purpose": "Action evidence separate from generic overlap."},
        {"field": "bill_material_signature", "type": "list[string]", "required": False, "source": "extract_signal + tokens", "purpose": "Material evidence separate from generic overlap."},
        {"field": "bill_spec_signature", "type": "list[string]", "required": False, "source": "extract_signal + numeric/spec tokens", "purpose": "Spec evidence for pump/rebar conflicts."},
        {"field": "bill_location_signature", "type": "list[string]", "required": False, "source": "bill text tokens", "purpose": "Location/context evidence such as bottom slab, roof, bathroom, foundation."},
        {"field": "signature_conflict_flags", "type": "list[string]", "required": False, "source": "signature comparison", "purpose": "Explicit conflicts between query signature and candidate concept."},
        {"field": "independent_source_family_count", "type": "integer", "required": True, "source": "source families after duplicate grouping", "purpose": "Avoid raw duplicate support dominance."},
        {"field": "source_entropy", "type": "float", "required": True, "source": "source support distribution", "purpose": "Measure whether support is spread across source families."},
        {"field": "duplicate_cluster_id", "type": "string", "required": False, "source": "source file hash/path grouping", "purpose": "Expose repeated files or duplicate trace clusters."},
        {"field": "local_neighbor_ids", "type": "list[string]", "required": False, "source": "quota.db same-book/title neighbors", "purpose": "Enable local near-neighbor safety comparison."},
        {"field": "local_title_contrast_terms", "type": "list[string]", "required": False, "source": "candidate vs neighbor quota title diff", "purpose": "Explain why candidate should beat local neighbor."},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "17.28 explicit build go",
            "command": "python tools\\goal_17x_build_oss_recall_index_v2.py --family pump,rebar --output data\\goal_search\\oss_recall_index_17x_v2.jsonl --manifest reports\\agent_state\\goal_17x_oss_recall_index_v2_build_manifest.json",
            "allowed_after": "explicit user go to build the default-off v2 artifact",
            "blocked_now": True,
        },
        {
            "stage": "post-build artifact acceptance",
            "command": "python tools\\goal_17x_oss_recall_index_v2_acceptance_gate.py --manifest reports\\agent_state\\goal_17x_oss_recall_index_v2_build_manifest.json",
            "allowed_after": "v2 index build completes",
            "blocked_now": True,
        },
        {
            "stage": "future dev/OOF shadow",
            "command": "python tools\\goal_17x_oss_recall_index_v2_dev_oof_shadow.py --index data\\goal_search\\oss_recall_index_17x_v2.jsonl --candidate all --progress-every 10",
            "allowed_after": "artifact acceptance passes and explicit dev/OOF execution go is provided",
            "blocked_now": True,
        },
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "scope_only", "target": "no v2 build in 17.27", "failure_action": "invalidate 17.27"},
        {"check": "v1_artifact_preserved", "target": "data/goal_search/oss_recall_index_17x_multifield.jsonl untouched", "failure_action": "stop and report drift"},
        {"check": "default_off_output", "target": "v2 output path is separate and not wired into config defaults", "failure_action": "stop and report drift"},
        {"check": "field_schema_complete", "target": "all R17_A-D fields represented in schema", "failure_action": "do not request build go"},
        {"check": "pump_rebar_focus", "target": "build scope targets pump/rebar first, with concrete only for source-quality comparison", "failure_action": "narrow scope before build"},
        {"check": "no_label_leakage", "target": "do not encode dev/OOF expected ids or heldout/hard labels into index rules", "failure_action": "reject build design"},
        {"check": "future_gate_defined", "target": "future shadow must beat P17_F positive_family_count with top1_loss=0", "failure_action": "no freeze"},
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {"action": "build_v2_index_now", "blocked": True, "reason": "User asked for analysis; no explicit build go was provided."},
        {"action": "run_dev_oof_shadow_now", "blocked": True, "reason": "No v2 artifact exists and artifact acceptance has not passed."},
        {"action": "edit_oss_recall_prior_runtime", "blocked": True, "reason": "17.27 scopes artifact build only; runtime reader changes need later gate."},
        {"action": "default_enable_v2", "blocked": True, "reason": "No validation or release gate exists."},
        {"action": "return_to_p17_threshold_tweaks", "blocked": True, "reason": "17.25 stopped that lane."},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.27 Exact V2 Index Build Implementation Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Recommendation",
        "",
        report["recommendation"],
        "",
        "## Implementation Steps",
        "",
        "| step | target file | implementation | must not change |",
        "|---|---|---|---|",
    ]
    for row in report["implementation_steps"]:
        lines.append(f"| {row['step_id']} | {row['target_file']} | {row['implementation']} {row['must_change']} | {row['must_not_change']} |")
    lines.extend(["", "## V2 Field Schema", "", "| field | type | required | purpose |", "|---|---|---|---|"])
    for row in report["v2_field_schema"]:
        lines.append(f"| {row['field']} | {row['type']} | {row['required']} | {row['purpose']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    source = _read_json(V2_SCOPE)
    if source.get("decision") != "scope_locked_request_explicit_v2_index_build_scope_or_go":
        raise ValueError(f"unexpected 17.26 decision: {source.get('decision')}")

    implementation_steps = _implementation_steps()
    field_schema = _v2_field_schema()
    commands = _command_contract()
    checks = _acceptance_checks()
    blocked = _blocked_actions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    steps_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_implementation_steps.csv")
    schema_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_field_schema.csv")
    commands_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_command_contract.csv")
    checks_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv")
    blocked_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv")

    report = {
        "stage": "17.27 exact v2 index build implementation scope",
        "decision": "implementation_scope_locked_request_explicit_v2_build_go",
        "recommendation": (
            "Do not build the v2 index yet. The next execution should require explicit build go, because v2 changes the evidence schema. "
            "17.27 locks the implementation boundary, output paths, field schema, command contract, and acceptance checks."
        ),
        "read_only_scope": True,
        "index_build_performed": False,
        "dev_oof_execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "source_stage": source["stage"],
        "source_decision": source["decision"],
        "implementation_steps": implementation_steps,
        "v2_field_schema": field_schema,
        "command_contract": commands,
        "acceptance_checks": checks,
        "blocked_actions": blocked,
        "next_boundary": (
            "17.28 may build the default-off v2 OSS recall index artifact only after explicit go. "
            "The build must write separate v2 files, preserve v1 artifacts, avoid runtime/default changes, and still not run heldout/hard or dev/OOF shadow."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "implementation_steps_csv": str(steps_csv),
            "field_schema_csv": str(schema_csv),
            "command_contract_csv": str(commands_csv),
            "acceptance_checks_csv": str(checks_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.27 only defines exact v2 index build implementation scope. It does not build an index, run dev/OOF, train, tune from heldout/hard, "
            "default-enable OSS recall, integrate online behavior, edit runtime readers, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(steps_csv, implementation_steps, ["step_id", "target_file", "implementation", "must_change", "must_not_change"])
    _write_csv(schema_csv, field_schema, ["field", "type", "required", "source", "purpose"])
    _write_csv(commands_csv, commands, ["stage", "command", "allowed_after", "blocked_now"])
    _write_csv(checks_csv, checks, ["check", "target", "failure_action"])
    _write_csv(blocked_csv, blocked, ["action", "blocked", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
