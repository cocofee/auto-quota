from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_MANIFEST = AGENT_STATE / "goal_17x_oss_recall_index_v2_build_manifest.json"
DEFAULT_SUMMARY_JSON = AGENT_STATE / "goal_17x_oss_recall_index_v2_acceptance_gate_summary.json"
DEFAULT_SUMMARY_MD = AGENT_STATE / "goal_17x_oss_recall_index_v2_acceptance_gate_summary.md"
DEFAULT_CHECKS = AGENT_STATE / "goal_17x_oss_recall_index_v2_acceptance_gate_checks.csv"
DEFAULT_FAMILY_QUALITY = AGENT_STATE / "goal_17x_oss_recall_index_v2_acceptance_gate_family_quality.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_17x_oss_recall_index_v2_acceptance_gate_blocked_actions.csv"

REQUIRED_FIELDS = {
    "evidence_vector_version",
    "quota_concept_label",
    "conflict_pair_ids",
    "positive_anchor_terms",
    "negative_anchor_terms",
    "bill_action_signature",
    "bill_material_signature",
    "bill_spec_signature",
    "bill_location_signature",
    "signature_conflict_flags",
    "independent_source_family_count",
    "source_entropy",
    "duplicate_cluster_id",
    "local_neighbor_ids",
    "local_title_contrast_terms",
    "build_manifest_hash",
}

TERMS = {
    "concrete": "\u6df7\u51dd\u571f",
    "non_pump": "\u975e\u6cf5\u9001",
    "pump_process": "\u6cf5\u9001",
    "steel_rebar": "\u94a2\u7b4b",
    "water_pump": "\u6c34\u6cf5",
    "pump_station": "\u6cf5\u7ad9",
    "aluminum": "\u94dd\u5408\u91d1",
    "keel": "\u9f99\u9aa8",
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _row_text(row: dict[str, Any]) -> str:
    parts: list[str] = [str(row.get("quota_concept_label") or "")]
    for key in ("quota_names", "terms", "quota_terms", "positive_anchor_terms", "negative_anchor_terms"):
        value = row.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(parts)


def _ratio(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _check(name: str, passed: bool, observed: object, threshold: object, failure_action: str) -> dict[str, Any]:
    return {
        "check": name,
        "passed": bool(passed),
        "observed": observed,
        "threshold": threshold,
        "failure_action": failure_action,
    }


def run_acceptance(manifest_path: Path, stage_label: str) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    index_path = PROJECT_ROOT / manifest["output"]
    rows = _load_rows(index_path)
    total = len(rows)
    missing = Counter()
    for row in rows:
        for field in REQUIRED_FIELDS:
            if field not in row:
                missing[field] += 1

    family_counts = Counter(str(row.get("query_family") or "") for row in rows)
    source_ge2 = sum(1 for row in rows if int(row.get("independent_source_family_count") or 0) >= 2)
    conflict_rows = sum(1 for row in rows if row.get("conflict_pair_ids"))
    local_neighbor_rows = sum(1 for row in rows if row.get("local_neighbor_ids"))

    family_quality: list[dict[str, Any]] = []
    for family in sorted(family_counts):
        family_rows = [row for row in rows if row.get("query_family") == family]
        term_counts: Counter[str] = Counter()
        for row in family_rows:
            text = _row_text(row)
            for name, term in TERMS.items():
                if term in text:
                    term_counts[name] += 1
        family_quality.append(
            {
                "query_family": family,
                "rows": len(family_rows),
                "concrete_term_rows": term_counts["concrete"],
                "non_pump_term_rows": term_counts["non_pump"],
                "pump_process_term_rows": term_counts["pump_process"],
                "water_pump_term_rows": term_counts["water_pump"],
                "pump_station_term_rows": term_counts["pump_station"],
                "steel_rebar_term_rows": term_counts["steel_rebar"],
                "aluminum_term_rows": term_counts["aluminum"],
                "keel_term_rows": term_counts["keel"],
                "concrete_term_ratio": _ratio(term_counts["concrete"], len(family_rows)),
                "steel_rebar_term_ratio": _ratio(term_counts["steel_rebar"], len(family_rows)),
            }
        )

    pump_quality = next((row for row in family_quality if row["query_family"] == "pump"), {})
    rebar_quality = next((row for row in family_quality if row["query_family"] == "rebar"), {})
    checks = [
        _check("manifest_matches_index_rows", total == int(manifest.get("rows") or -1), total, manifest.get("rows"), "reject artifact"),
        _check("required_schema_complete", not missing, dict(missing), "no missing required v2 fields", "reject artifact"),
        _check("families_are_locked", set(family_counts) <= {"pump", "rebar"} and set(family_counts) == {"pump", "rebar"}, dict(family_counts), "exactly pump,rebar", "reject artifact"),
        _check("source_family_support_sufficient", _ratio(source_ge2, total) >= 0.40, _ratio(source_ge2, total), ">=0.40 rows with independent_source_family_count>=2", "hold before shadow"),
        _check("conflict_pair_coverage_sufficient", _ratio(conflict_rows, total) >= 0.80, _ratio(conflict_rows, total), ">=0.80 rows with conflict_pair_ids", "hold before shadow"),
        _check("local_neighbor_coverage_sufficient", _ratio(local_neighbor_rows, total) >= 0.95, _ratio(local_neighbor_rows, total), ">=0.95 rows with local_neighbor_ids", "hold before shadow"),
        _check("pump_family_semantic_contamination_guard", float(pump_quality.get("concrete_term_ratio") or 0.0) <= 0.25, pump_quality.get("concrete_term_ratio"), "<=0.25 pump rows with concrete terms", "rebuild v2 with pump semantic filter"),
        _check("rebar_family_semantic_guard", float(rebar_quality.get("steel_rebar_term_ratio") or 0.0) >= 0.70, rebar_quality.get("steel_rebar_term_ratio"), ">=0.70 rebar rows with steel/rebar terms", "rebuild rebar slice"),
        _check("default_off_boundary_preserved", not manifest.get("dev_oof_shadow_run") and not manifest.get("heldout_hard_used") and not manifest.get("online_default_changed") and not manifest.get("goal_searcher_changed"), manifest.get("decision"), "no execution/runtime/default changes", "stop and report drift"),
    ]
    failed = [row for row in checks if not row["passed"]]
    decision = "artifact_acceptance_failed_rebuild_required" if failed else "artifact_accepted_request_dev_oof_shadow_go"
    blocked_actions = [
        {
            "action": "run_dev_oof_shadow_now",
            "blocked": bool(failed),
            "reason": "Artifact acceptance failed; pump slice is dominated by concrete/non-pump process rows." if failed else "Allowed only after explicit dev/OOF shadow go.",
        },
        {
            "action": "heldout_hard_validation",
            "blocked": True,
            "reason": "No frozen dev/OOF candidate exists.",
        },
        {
            "action": "runtime_integration_or_default_enable",
            "blocked": True,
            "reason": "Artifact acceptance only; runtime integration/default enablement requires a later explicit gate.",
        },
    ]
    return {
        "stage": stage_label,
        "decision": decision,
        "index_path": str(index_path),
        "manifest": str(manifest_path),
        "rows": total,
        "family_counts": dict(family_counts),
        "source_family_ge2_rows": source_ge2,
        "conflict_pair_rows": conflict_rows,
        "local_neighbor_rows": local_neighbor_rows,
        "missing_required_fields": dict(missing),
        "family_quality": family_quality,
        "checks": checks,
        "failed_checks": failed,
        "blocked_actions": blocked_actions,
        "dev_oof_shadow_allowed": not failed,
        "heldout_hard_used": False,
        "runtime_changed": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _write_summary_md(path: Path, result: dict[str, Any]) -> None:
    failed = result["failed_checks"]
    lines = [
        f"# {result['stage']}",
        "",
        f"Updated: {result['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{result['decision']}**",
        "",
        "## Metrics",
        "",
        f"- Rows: {result['rows']}",
        f"- Family counts: `{result['family_counts']}`",
        f"- independent_source_family_count>=2 rows: {result['source_family_ge2_rows']}",
        f"- conflict_pair rows: {result['conflict_pair_rows']}",
        f"- local_neighbor rows: {result['local_neighbor_rows']}",
        "",
        "## Failed Checks",
        "",
    ]
    if failed:
        for row in failed:
            lines.append(f"- {row['check']}: observed `{row['observed']}`, threshold `{row['threshold']}`")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Anti-Drift",
            "",
            f"{result['stage']} only evaluates the artifact. It does not run dev/OOF shadow, heldout/hard, training, runtime integration, default enablement, or GoalSearcher changes.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.29 v2 OSS recall artifact acceptance gate")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--summary-md", type=Path, default=DEFAULT_SUMMARY_MD)
    parser.add_argument("--checks", type=Path, default=DEFAULT_CHECKS)
    parser.add_argument("--family-quality", type=Path, default=DEFAULT_FAMILY_QUALITY)
    parser.add_argument("--blocked-actions", type=Path, default=DEFAULT_BLOCKED)
    parser.add_argument("--stage-label", default="17.29 v2 artifact acceptance gate")
    args = parser.parse_args()

    result = run_acceptance(args.manifest, args.stage_label)
    _write_json(args.summary_json, result)
    _write_summary_md(args.summary_md, result)
    _write_csv(args.checks, result["checks"], ["check", "passed", "observed", "threshold", "failure_action"])
    _write_csv(
        args.family_quality,
        result["family_quality"],
        [
            "query_family",
            "rows",
            "concrete_term_rows",
            "non_pump_term_rows",
            "pump_process_term_rows",
            "water_pump_term_rows",
            "pump_station_term_rows",
            "steel_rebar_term_rows",
            "aluminum_term_rows",
            "keel_term_rows",
            "concrete_term_ratio",
            "steel_rebar_term_ratio",
        ],
    )
    _write_csv(args.blocked_actions, result["blocked_actions"], ["action", "blocked", "reason"])
    print(json.dumps({"decision": result["decision"], "failed_checks": result["failed_checks"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
