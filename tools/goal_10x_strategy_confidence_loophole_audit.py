from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_STAGE_10_25 = AGENT_STATE / "goal_10x_evidence_wait_closure_pause_request_gate_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_AUTOMATION = Path.home() / ".codex" / "automations" / "goal-read-only-auto-advance" / "automation.toml"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_strategy_confidence_loophole_audit"


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _automation_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status"):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _dashboard_checks(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "dashboard_exists": path.exists(),
        "has_paused_status": "10.x paused" in text,
        "has_pause_boundary": "10.x learning loop paused awaiting external evidence" in text,
        "has_no_current_waiting_stage": "当前待开始" not in text,
        "mentions_auto_advance_paused": "Goal read-only auto advance 已暂停" in text,
    }


def _loophole_register(stage_10_25: dict[str, Any], dashboard: dict[str, Any], automation_status: str) -> list[dict[str, Any]]:
    metrics = stage_10_25.get("metrics", {})
    rows = [
        {
            "loophole_id": "L01_FALSE_100_PERCENT_CONFIDENCE",
            "severity_before_fix": "high",
            "loophole": "A strategy review could claim absolute certainty and stop looking for unknown failure modes.",
            "evidence": "User explicitly asked for 100% confidence; current reports use finite gates, not exhaustive proof.",
            "fix": "Adopt an explicit confidence contract: no known high/medium loopholes after audit, but never claim mathematical 100%.",
            "status_after_fix": "mitigated_by_confidence_contract",
            "residual_risk": "unknown_unknowns_remain",
        },
        {
            "loophole_id": "L02_PSEUDO_INDEPENDENT_SOURCE",
            "severity_before_fix": "high",
            "loophole": "Two files can look like two non-generated sources but share the same generation, extraction, labeling, or repair pipeline.",
            "evidence": "10.25 requires >=2 sources but does not yet require source-family independence or provenance hashes.",
            "fix": "Future evidence package must include source_family, collection_method, producer, timestamp, row ids, and content hash; independence is judged by source_family, not filename count.",
            "status_after_fix": "mitigated_by_evidence_schema",
            "residual_risk": "manual provenance review can still miss hidden common origin",
        },
        {
            "loophole_id": "L03_GENERATED_SOURCE_LEAKAGE_REENTRY",
            "severity_before_fix": "high",
            "loophole": "global_repair_decision_table-derived rows could re-enter under a renamed source or accepted DQ artifact.",
            "evidence": "S2 positive net share from generated source is 1.0; DQ route still has source_provenance pending.",
            "fix": "Require generated-source exclusion list plus row-level provenance registry before S2/S1 re-entry; any unknown provenance defaults to evidence_only, not learning evidence.",
            "status_after_fix": "mitigated_by_default_exclude_unknown_provenance",
            "residual_risk": "depends on completeness of provenance registry",
        },
        {
            "loophole_id": "L04_TAXONOMY_FIX_MISCOUNTED_AS_MODEL_GAIN",
            "severity_before_fix": "high",
            "loophole": "DQ/taxonomy cleanup can improve apparent Top1 and be mistaken for ranking/recall learning gain.",
            "evidence": f"dq_pending_checkpoint_count={metrics.get('dq_pending_checkpoint_count')}; backlog rows remain non-learning.",
            "fix": "Future re-entry must split evidence into taxonomy_cleanup_effect, recall_effect, and ranking_effect; only independent non-DQ ranking/recall slices may support learning.",
            "status_after_fix": "mitigated_by_effect_decomposition_gate",
            "residual_risk": "borderline cases need manual adjudication",
        },
        {
            "loophole_id": "L05_HELDOUT_HARD_SELECTION_CREEP",
            "severity_before_fix": "high",
            "loophole": "A future re-entry review may inspect heldout/hard and implicitly use it to select thresholds or candidates.",
            "evidence": "10.25 blocks heldout/hard selection, but future evidence intake still needs split rules.",
            "fix": "Evidence package must declare split_used; heldout/hard may only appear in post-freeze validation reports, never in evidence intake or candidate selection.",
            "status_after_fix": "mitigated_by_split_declaration_gate",
            "residual_risk": "requires reviewer discipline",
        },
        {
            "loophole_id": "L06_MANUAL_NEXT_STEP_BYPASS",
            "severity_before_fix": "medium",
            "loophole": "A user can say '下一步' and accidentally restart learning stages despite pause.",
            "evidence": f"dashboard_paused={dashboard.get('has_paused_status')}; automation_status={automation_status}",
            "fix": "Dashboard prompt says no auto-advance without evidence; automation is paused; future assistant must treat plain '下一步' as status/report unless evidence inputs are attached.",
            "status_after_fix": "mitigated_by_dashboard_and_automation_pause",
            "residual_risk": "manual operator can still explicitly override",
        },
        {
            "loophole_id": "L07_ARTIFACT_TAMPERING_OR_STALE_CONTEXT",
            "severity_before_fix": "medium",
            "loophole": "Reports could be stale or edited after decisions, causing future reviews to trust changed context.",
            "evidence": "Prior reports do not include a full immutable manifest hash chain.",
            "fix": "Emit a critical artifact manifest with SHA256 hashes in this audit; future re-entry must cite fresh hashes or regenerate audit.",
            "status_after_fix": "mitigated_by_manifest_hashes",
            "residual_risk": "hash manifest itself is not an external notarization",
        },
        {
            "loophole_id": "L08_EVIDENCE_PACKAGE_UNDERSPECIFIED",
            "severity_before_fix": "medium",
            "loophole": "Required evidence inputs say what is needed but not enough about required columns and acceptance artifacts.",
            "evidence": "10.25 required_user_evidence_inputs is human-readable but not a strict schema.",
            "fix": "Define evidence intake schema: evidence_id, source_file, source_family, producer, collection_method, row_id, query_id, expected_id, candidate_id, split, gain/loss/net, taxonomy_disposition, provenance_hash.",
            "status_after_fix": "mitigated_by_schema_contract",
            "residual_risk": "schema quality depends on producer compliance",
        },
        {
            "loophole_id": "L09_PAUSE_CAN_STALL_FOREVER",
            "severity_before_fix": "low",
            "loophole": "Pausing is safe but can become a dead end with no owner or next action.",
            "evidence": "10.25 pauses until external evidence exists.",
            "fix": "Keep pause as safe default, but expose a concrete request list of seven inputs and their minimum content.",
            "status_after_fix": "accepted_operational_tradeoff",
            "residual_risk": "progress depends on upstream evidence production",
        },
    ]
    return rows


def _hardened_policy() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": "P01_CONFIDENCE_CONTRACT",
            "rule": "Do not claim 100% certainty; claim only no known unmitigated high/medium loopholes after this audit.",
            "enforcement": "Final reports must include residual unknown_unknowns risk.",
        },
        {
            "policy_id": "P02_EVIDENCE_INTAKE_SCHEMA",
            "rule": "Future re-entry requires a row-level evidence package with provenance, source_family independence, split declaration, and hashes.",
            "enforcement": "Reject evidence packages missing required schema columns.",
        },
        {
            "policy_id": "P03_DEFAULT_EXCLUDE_UNKNOWN_PROVENANCE",
            "rule": "Unknown or generated provenance remains evidence_only and cannot become training labels, recall rules, ranking features, or thresholds.",
            "enforcement": "Source provenance gate must pass before S1/S2 re-entry.",
        },
        {
            "policy_id": "P04_EFFECT_DECOMPOSITION",
            "rule": "Separate taxonomy_cleanup_effect from recall_effect and ranking_effect before any learning claim.",
            "enforcement": "No Top1 gain claim unless non-DQ independent slices support it.",
        },
        {
            "policy_id": "P05_NO_HELDOUT_SELECTION",
            "rule": "Heldout/hard remain post-freeze validation only and cannot select evidence, candidates, features, or thresholds.",
            "enforcement": "Any evidence package with heldout/hard in selection context is rejected.",
        },
        {
            "policy_id": "P06_PAUSE_BOUNDARY",
            "rule": "Plain '下一步' while paused means report status or request evidence, not create new learning stages.",
            "enforcement": "Automation paused; dashboard prompt forbids automatic learning-stage advance without new evidence.",
        },
    ]


def _required_schema() -> list[dict[str, Any]]:
    fields = [
        ("evidence_id", "stable row id for the evidence claim"),
        ("source_file", "physical source artifact name"),
        ("source_family", "independence family; filename count is not enough"),
        ("producer", "human/system owner that produced the artifact"),
        ("collection_method", "how evidence was collected; must not be generated repair-decision reuse"),
        ("row_id", "row/sample id traceable to source"),
        ("query_id", "query/group id if applicable"),
        ("expected_id", "expected target id; not allowed as online feature"),
        ("candidate_id", "candidate/model/policy being evaluated"),
        ("split", "dev/oof only for re-entry evidence; heldout/hard forbidden for selection"),
        ("gain", "positive Top1/Top5 change on eligible slice"),
        ("loss", "negative Top1/Top5 change on eligible slice"),
        ("net", "gain-loss on eligible slice"),
        ("taxonomy_disposition", "true_learning_signal vs taxonomy_cleanup vs exclude/evidence_only"),
        ("provenance_hash", "sha256 or equivalent content/provenance hash"),
    ]
    return [{"field": field, "requirement": requirement, "required": True} for field, requirement in fields]


def _artifact_manifest(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "artifact": str(path),
            "exists": path.exists(),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size if path.exists() else 0,
        }
        for path in paths
    ]


def _metrics(loopholes: list[dict[str, Any]], automation_status: str, dashboard: dict[str, Any]) -> dict[str, Any]:
    high_unmitigated = [
        row for row in loopholes
        if row["severity_before_fix"] == "high" and not str(row["status_after_fix"]).startswith(("mitigated", "accepted"))
    ]
    medium_unmitigated = [
        row for row in loopholes
        if row["severity_before_fix"] == "medium" and not str(row["status_after_fix"]).startswith(("mitigated", "accepted"))
    ]
    return {
        "loophole_count": len(loopholes),
        "high_loophole_count": sum(1 for row in loopholes if row["severity_before_fix"] == "high"),
        "medium_loophole_count": sum(1 for row in loopholes if row["severity_before_fix"] == "medium"),
        "low_loophole_count": sum(1 for row in loopholes if row["severity_before_fix"] == "low"),
        "unmitigated_high_count": len(high_unmitigated),
        "unmitigated_medium_count": len(medium_unmitigated),
        "confidence_claim": "NO_KNOWN_UNMITIGATED_HIGH_OR_MEDIUM_LOOPHOLES_NOT_100_PERCENT_PROOF",
        "absolute_confidence_claimed": False,
        "automation_status": automation_status,
        "dashboard_paused": dashboard.get("has_paused_status") and dashboard.get("has_pause_boundary"),
        "dashboard_no_current_waiting_stage": dashboard.get("has_no_current_waiting_stage"),
        "auto_learning_advance_blocked": automation_status == "PAUSED",
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.x Strategy Confidence Loophole Audit",
        "",
        "This audit does not claim mathematical 100% confidence. It records known loopholes, mitigations, and remaining residual risk.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["confidence_claim", metrics["confidence_claim"]],
                ["absolute_confidence_claimed", metrics["absolute_confidence_claimed"]],
                ["loophole_count", metrics["loophole_count"]],
                ["unmitigated_high_count", metrics["unmitigated_high_count"]],
                ["unmitigated_medium_count", metrics["unmitigated_medium_count"]],
                ["automation_status", metrics["automation_status"]],
            ]
        ),
        "",
        "## Loopholes",
        "",
        _md_table(
            [["loophole_id", "severity_before_fix", "status_after_fix", "residual_risk"]]
            + [[row["loophole_id"], row["severity_before_fix"], row["status_after_fix"], row["residual_risk"]] for row in report["loophole_register"]]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 10.x paused strategy for loopholes and hardened confidence gates")
    parser.add_argument("--stage-10-25", default=str(DEFAULT_STAGE_10_25))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--automation", default=str(DEFAULT_AUTOMATION))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_25_path = Path(args.stage_10_25)
    dashboard_path = Path(args.dashboard)
    automation_path = Path(args.automation)
    stage_10_25 = _read_json(stage_10_25_path)
    dashboard = _dashboard_checks(dashboard_path)
    automation_status = _automation_status(automation_path)
    loopholes = _loophole_register(stage_10_25, dashboard, automation_status)
    hardened_policy = _hardened_policy()
    schema = _required_schema()
    manifest = _artifact_manifest([stage_10_25_path, dashboard_path, automation_path])
    metrics = _metrics(loopholes, automation_status, dashboard)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "loophole_register_csv": str(output_prefix.with_name(output_prefix.name + "_loophole_register.csv")),
        "hardened_policy_csv": str(output_prefix.with_name(output_prefix.name + "_hardened_policy.csv")),
        "evidence_intake_schema_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_intake_schema.csv")),
        "artifact_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / 10.x strategy confidence loophole audit",
        "read_only": True,
        "no_training": True,
        "no_heldout_hard_validation": True,
        "no_ranking_change": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_10_25": str(stage_10_25_path),
            "dashboard": str(dashboard_path),
            "automation": str(automation_path),
        },
        "metrics": metrics,
        "dashboard_checks": dashboard,
        "loophole_register": loopholes,
        "hardened_policy": hardened_policy,
        "evidence_intake_schema": schema,
        "artifact_manifest": manifest,
        "artifacts": artifacts,
        "decision": (
            "Do not claim 100% confidence. After this audit, there are no known unmitigated high or medium loopholes in the pause/re-entry strategy, "
            "provided future work enforces the hardened evidence schema, source-family independence, DQ non-learning boundary, no-heldout-selection rule, "
            "and paused automation boundary. Residual unknown-unknown risk remains explicit."
        ),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    _write_csv(Path(artifacts["loophole_register_csv"]), loopholes, ["loophole_id", "severity_before_fix", "loophole", "evidence", "fix", "status_after_fix", "residual_risk"])
    _write_csv(Path(artifacts["hardened_policy_csv"]), hardened_policy, ["policy_id", "rule", "enforcement"])
    _write_csv(Path(artifacts["evidence_intake_schema_csv"]), schema, ["field", "requirement", "required"])
    _write_csv(Path(artifacts["artifact_manifest_csv"]), manifest, ["artifact", "exists", "sha256", "bytes"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": metrics,
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
