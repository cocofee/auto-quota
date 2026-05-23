from __future__ import annotations

import argparse
import csv
import hashlib
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
DEFAULT_OSS = PROJECT_ROOT / "data" / "goal_search" / "oss_samples_expanded.jsonl"
DEFAULT_SPLITS = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded"
DEFAULT_S2_SOURCE_SUPPORT = AGENT_STATE / "goal_10x_s2_independent_source_robustness_gate_source_support.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_source_provenance_bootstrap_audit"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _classify_source(source_file: str) -> dict[str, str]:
    lower = source_file.lower()
    if source_file == "global_repair_decision_table.csv":
        return {
            "source_family": "generated_repair_decision_table",
            "producer": "system_repair_decision_pipeline",
            "collection_method": "generated repair-decision reuse from prior mining tables",
            "is_human_quantity_surveyor_output": "false",
            "is_generated_or_synthetic": "true",
            "trust_level": "exclude_from_learning_evidence",
            "acceptance_status": "generated_exclusion_required",
            "evidence_note": "S2 source robustness classified this source as generated; all positive net support came from this file.",
        }
    if lower.startswith("v36_oss_r2"):
        return {
            "source_family": "oss_v36_canonicalizer_alignment",
            "producer": "human_quantity_surveyor_oss_asserted_by_user",
            "collection_method": "OSS quantity-surveyor completed quota result, transformed through v36 canonicalizer alignment trace",
            "is_human_quantity_surveyor_output": "user_asserted_true_pending_owner_acceptance",
            "is_generated_or_synthetic": "false",
            "trust_level": "candidate_independent_oss_pending_acceptance",
            "acceptance_status": "pending_dq_owner_acceptance",
            "evidence_note": "Filename and user statement indicate OSS provenance; still needs registry acceptance before learning re-entry.",
        }
    if lower.startswith("v36_oss_r3"):
        return {
            "source_family": "oss_v36_speed_chain",
            "producer": "human_quantity_surveyor_oss_asserted_by_user",
            "collection_method": "OSS quantity-surveyor completed quota result, transformed through v36 speed diagnostic trace",
            "is_human_quantity_surveyor_output": "user_asserted_true_pending_owner_acceptance",
            "is_generated_or_synthetic": "false",
            "trust_level": "candidate_independent_oss_pending_acceptance",
            "acceptance_status": "pending_dq_owner_acceptance",
            "evidence_note": "Filename and user statement indicate OSS provenance; multiple speed variants remain one source family until owner accepts independence.",
        }
    if lower.startswith("v36_a2"):
        return {
            "source_family": "v36_primary_param_consumption_trace",
            "producer": "unknown_or_pipeline_trace",
            "collection_method": "historical primary param/consumption guarded speed trace",
            "is_human_quantity_surveyor_output": "unknown_pending_source_doc",
            "is_generated_or_synthetic": "false",
            "trust_level": "non_generated_trace_pending_provenance",
            "acceptance_status": "pending_source_documentation",
            "evidence_note": "Previously treated as non-generated by S2 gate, but bootstrap audit found no direct owner/provenance registry row.",
        }
    if lower.startswith("v36_a3"):
        return {
            "source_family": "v36_global_rank_miss_shadow_trace",
            "producer": "unknown_or_pipeline_trace",
            "collection_method": "global rank-miss shadow diagnostic trace",
            "is_human_quantity_surveyor_output": "unknown_pending_source_doc",
            "is_generated_or_synthetic": "false",
            "trust_level": "non_generated_trace_pending_provenance",
            "acceptance_status": "pending_source_documentation",
            "evidence_note": "Previously treated as non-generated by S2 gate, but it is still a diagnostic trace rather than accepted source provenance.",
        }
    if lower.startswith("v36_data_fuel"):
        return {
            "source_family": "v36_data_fuel_shadow_trace",
            "producer": "unknown_or_pipeline_trace",
            "collection_method": "data-fuel shadow comparator/guardrail diagnostic trace",
            "is_human_quantity_surveyor_output": "unknown_pending_source_doc",
            "is_generated_or_synthetic": "false",
            "trust_level": "non_generated_trace_pending_provenance",
            "acceptance_status": "pending_source_documentation",
            "evidence_note": "Previously treated as non-generated by S2 gate; requires owner documentation before it can count as independent OSS evidence.",
        }
    return {
        "source_family": "unknown_source_family",
        "producer": "unknown",
        "collection_method": "unknown",
        "is_human_quantity_surveyor_output": "unknown_pending_source_doc",
        "is_generated_or_synthetic": "unknown",
        "trust_level": "unknown_hold",
        "acceptance_status": "pending_source_documentation",
        "evidence_note": "No source classification rule matched this artifact.",
    }


def _split_rows(split_dir: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "dev": _read_jsonl(split_dir / "dev.jsonl"),
        "heldout_reference_only": _read_jsonl(split_dir / "heldout.jsonl"),
        "hard_reference_only": _read_jsonl(split_dir / "hard.jsonl"),
    }


def _write_markdown(path: Path, report: dict[str, Any], registry_rows: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    top_rows = registry_rows[:12]
    lines = [
        "# 10.26 DQ/source provenance bootstrap audit",
        "",
        "Read-only bootstrap registry for source provenance. This does not reopen learning and does not accept evidence by itself.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["source_file_count", metrics["source_file_count"]],
                ["generated_exclusion_source_count", metrics["generated_exclusion_source_count"]],
                ["candidate_human_oss_source_count", metrics["candidate_human_oss_source_count"]],
                ["pending_source_documentation_count", metrics["pending_source_documentation_count"]],
                ["accepted_dq_artifact_count", metrics["accepted_dq_artifact_count"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Registry Preview",
        "",
        _md_table(
            [["source_file", "source_family", "trust_level", "acceptance_status", "s2_net"]]
            + [
                [
                    row["source_file"],
                    row["source_family"],
                    row["trust_level"],
                    row["acceptance_status"],
                    row["s2_net"],
                ]
                for row in top_rows
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap source provenance registry without learning execution")
    parser.add_argument("--oss-samples", default=str(DEFAULT_OSS))
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLITS))
    parser.add_argument("--s2-source-support", default=str(DEFAULT_S2_SOURCE_SUPPORT))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    oss_rows = _read_jsonl(Path(args.oss_samples))
    split_rows = _split_rows(Path(args.split_dir))
    s2_rows = _read_csv(Path(args.s2_source_support))

    source_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in oss_rows:
        source_records[str(row.get("source_file") or "<empty>")].append(row)

    split_counts: dict[str, Counter[str]] = {}
    for split, rows in split_rows.items():
        counter: Counter[str] = Counter()
        for row in rows:
            counter[str(row.get("source_file") or "<empty>")] += 1
        split_counts[split] = counter

    s2_by_source = {row.get("source_file", ""): row for row in s2_rows}
    all_sources = sorted(set(source_records) | set(s2_by_source))
    registry_rows: list[dict[str, Any]] = []
    for source_file in all_sources:
        records = source_records.get(source_file, [])
        class_info = _classify_source(source_file)
        provinces = sorted({str(row.get("province") or "") for row in records if str(row.get("province") or "")})
        buckets = Counter(str(row.get("bucket") or "<empty>") for row in records)
        sample_ids = sorted(str(row.get("sample_id") or "") for row in records if str(row.get("sample_id") or ""))
        s2 = s2_by_source.get(source_file, {})
        hash_payload = json.dumps(
            {
                "source_file": source_file,
                "row_count": len(records),
                "sample_ids": sample_ids[:200],
                "provinces": provinces,
                "buckets": dict(sorted(buckets.items())),
                "s2_source_class": s2.get("source_class", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        registry_rows.append(
            {
                "source_file": source_file,
                "source_family": class_info["source_family"],
                "producer": class_info["producer"],
                "collection_method": class_info["collection_method"],
                "is_human_quantity_surveyor_output": class_info["is_human_quantity_surveyor_output"],
                "is_generated_or_synthetic": class_info["is_generated_or_synthetic"],
                "trust_level": class_info["trust_level"],
                "evidence_note": class_info["evidence_note"],
                "provenance_hash": _sha256_text(hash_payload),
                "row_count_total": len(records),
                "dev_row_count": split_counts["dev"].get(source_file, 0),
                "heldout_reference_row_count": split_counts["heldout_reference_only"].get(source_file, 0),
                "hard_reference_row_count": split_counts["hard_reference_only"].get(source_file, 0),
                "province_count": len(provinces),
                "bucket_summary": ";".join(f"{key}:{value}" for key, value in sorted(buckets.items())),
                "s2_source_class": s2.get("source_class", "not_in_s2_source_support"),
                "s2_groups": s2.get("groups", 0),
                "s2_gain": s2.get("gain", 0),
                "s2_loss": s2.get("loss", 0),
                "s2_net": s2.get("net", 0),
                "s2_positive_net": s2.get("positive_net", 0),
                "learning_disposition": (
                    "exclude_generated_source"
                    if class_info["is_generated_or_synthetic"] == "true"
                    else "evidence_only_pending_acceptance"
                ),
                "acceptance_status": class_info["acceptance_status"],
            }
        )

    generated_exclusions = [
        row for row in registry_rows
        if row["learning_disposition"] == "exclude_generated_source"
    ]
    gap_rows = []
    for row in registry_rows:
        if row["acceptance_status"] != "pending_dq_owner_acceptance":
            gap_rows.append(
                {
                    "source_file": row["source_file"],
                    "gap_type": row["acceptance_status"],
                    "why_it_matters": (
                        "Generated sources cannot count as learning evidence."
                        if row["learning_disposition"] == "exclude_generated_source"
                        else "Needs source owner documentation before it can count as independent non-generated evidence."
                    ),
                    "required_fix": (
                        "Keep on generated-source exclusion list."
                        if row["learning_disposition"] == "exclude_generated_source"
                        else "Add owner/provenance row with producer and collection method."
                    ),
                }
            )

    blocked_actions = [
        {
            "blocked_action": "open_learning_reentry_review",
            "reason": "Bootstrap registry is not an accepted DQ artifact and S2 still has no non-generated positive net.",
            "allowed_after": "DQ owner accepts source registry and independent non-generated S2 evidence is supplied",
        },
        {
            "blocked_action": "count_generated_rows_as_learning_evidence",
            "reason": "global_repair_decision_table.csv remains generated-source dominated and must be excluded.",
            "allowed_after": "never for direct learning evidence; it may remain evidence-only/DQ backlog context",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "10.26 is a read-only DQ bootstrap audit.",
            "allowed_after": "future explicit execution stage after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No frozen validation candidate and no source robustness pass exists.",
            "allowed_after": "future validation gate after independent-source and DQ acceptance pass",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "registry_csv": str(output_prefix.with_name(output_prefix.name + "_registry.csv")),
        "generated_exclusion_csv": str(output_prefix.with_name(output_prefix.name + "_generated_exclusion_list.csv")),
        "provenance_gaps_csv": str(output_prefix.with_name(output_prefix.name + "_provenance_gaps.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_file_count": len(registry_rows),
        "generated_exclusion_source_count": len(generated_exclusions),
        "candidate_human_oss_source_count": sum(
            1 for row in registry_rows
            if row["is_human_quantity_surveyor_output"] == "user_asserted_true_pending_owner_acceptance"
        ),
        "pending_source_documentation_count": sum(
            1 for row in registry_rows
            if row["acceptance_status"] == "pending_source_documentation"
        ),
        "pending_dq_owner_acceptance_count": sum(
            1 for row in registry_rows
            if row["acceptance_status"] == "pending_dq_owner_acceptance"
        ),
        "accepted_dq_artifact_count": 0,
        "s2_non_generated_positive_net": 0,
        "s2_generated_positive_net": sum(int(row.get("s2_positive_net") or 0) for row in generated_exclusions),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.26 DQ source provenance bootstrap audit",
        "read_only": True,
        "dq_bootstrap_only": True,
        "heldout_hard_reference_only_not_selection": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Bootstrap registry created, but no DQ artifact is accepted yet and learning re-entry remains blocked. "
            "The generated-source exclusion is explicit for global_repair_decision_table.csv; OSS-looking v36 sources are recorded as user-asserted human quantity-surveyor outputs pending DQ owner acceptance."
        ),
        "anti_drift_conclusion": (
            "10.26 only classifies source provenance. It does not train, tune, expand candidates, validate on heldout/hard, change thresholds or rules, modify GoalSearcher, edit feature whitelists, or claim S2 general Top1 gain."
        ),
        "next_stage": {
            "stage": "10.27 DQ source provenance owner acceptance review",
            "goal": "Read-only decide whether the bootstrap registry can be owner-accepted as DQ source provenance, and identify whether additional source docs are needed.",
            "blocked_until": "DQ owner acceptance and independent non-generated S2 evidence remain required before learning re-entry.",
        },
    }

    registry_fields = [
        "source_file", "source_family", "producer", "collection_method",
        "is_human_quantity_surveyor_output", "is_generated_or_synthetic", "trust_level",
        "evidence_note", "provenance_hash", "row_count_total", "dev_row_count",
        "heldout_reference_row_count", "hard_reference_row_count", "province_count",
        "bucket_summary", "s2_source_class", "s2_groups", "s2_gain", "s2_loss", "s2_net",
        "s2_positive_net", "learning_disposition", "acceptance_status",
    ]
    _write_csv(Path(artifacts["registry_csv"]), registry_rows, registry_fields)
    _write_csv(Path(artifacts["generated_exclusion_csv"]), generated_exclusions, registry_fields)
    _write_csv(Path(artifacts["provenance_gaps_csv"]), gap_rows, ["source_file", "gap_type", "why_it_matters", "required_fix"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, registry_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
