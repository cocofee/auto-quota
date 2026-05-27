from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
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
DEFAULT_1061_SUMMARY = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_design_gate_summary.json"
DEFAULT_1061_INPUTS = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_design_gate_input_manifest.csv"
DEFAULT_QFE_ROWS = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit_rows.csv"
DEFAULT_QFE_SUBBUCKETS = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit_subbuckets.csv"
DEFAULT_TOP1_ROWS = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition"

NORMALIZATION_RE = re.compile(
    r"(DN\d+|[0-9]+(?:\.[0-9]+)?\s*(?:kW|KW|m3/h|m³/h|mm|MM|KG|kg)|[×xX*]|[()（）,:：、]|型号|规格|名称|类型|利旧|参\d+)",
    re.IGNORECASE,
)


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


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _hash_id(*parts: str) -> str:
    text = "|".join(parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _normalization_signals(query: str) -> str:
    signals = sorted(set(match.group(0) for match in NORMALIZATION_RE.finditer(query or "")))
    return ";".join(signals[:8])


def _source_family_guess(source_file: str) -> str:
    if source_file == "global_repair_decision_table.csv":
        return "generated_repair_decision_table"
    if source_file.startswith("v36_oss_r2"):
        return "accepted_oss_canonicalizer_alignment"
    if source_file.startswith("v36_oss_r3"):
        return "accepted_oss_speed_chain"
    if source_file.startswith("v36_"):
        return "v36_trace_or_shadow"
    return "unknown"


def _classify_qfe(row: dict[str, str]) -> tuple[str, list[str], bool, str, str]:
    primary_issue = row.get("primary_issue", "")
    learning_status = row.get("learning_status", "")
    source_pattern = row.get("source_pattern", "")
    dominant_rate = _float(row.get("subbucket_dominant_source_rate"))
    top1_family = row.get("top1_family", "")
    query = row.get("query", "")
    normalization = _normalization_signals(query)
    secondary: list[str] = []
    if not top1_family:
        secondary.append("top1_family_empty")
    if normalization:
        secondary.append("query_text_normalization_signal")
    if row.get("top1_book_relation") == "wrong_book":
        secondary.append("wrong_book_boundary")
    if source_pattern != "source_diverse_row" or dominant_rate >= 0.9 or row.get("source_file") == "global_repair_decision_table.csv":
        secondary.append("source_generated_artifact_risk")

    if primary_issue == "label_or_expected_mismatch" or learning_status == "exclude_label_or_expected_review":
        mode = "label_expected_mismatch"
        evidence = row.get("label_mismatch_explanation") or row.get("primary_explanation") or "label/expected mismatch marker"
    elif learning_status == "taxonomy_audit_candidate_not_rank_rule" and dominant_rate < 0.9:
        if top1_family:
            mode = "parser_unrecognized_with_top1_hint"
        else:
            mode = "parser_unrecognized_taxonomy_empty"
        evidence = f"taxonomy audit candidate; dominant_source_rate={dominant_rate:.6f}; matched_hint={row.get('matched_hint', '')}"
    elif "taxonomy_empty" in primary_issue or "taxonomy_missing" in primary_issue:
        mode = "taxonomy_coverage_gap_source_risky"
        evidence = f"{primary_issue}; dominant_source_rate={dominant_rate:.6f}; learning_status={learning_status}"
    elif row.get("top1_book_relation") == "wrong_book":
        mode = "cross_domain_or_wrong_book_absorption"
        evidence = "top1_book differs from expected_books"
    else:
        mode = "source_generated_artifact"
        evidence = f"learning_status={learning_status}; source_pattern={source_pattern}; dominant_source_rate={dominant_rate:.6f}"

    if "source_generated_artifact_risk" in secondary and mode not in {"source_generated_artifact", "label_expected_mismatch"}:
        review_status = "review_only_source_risky"
    elif mode in {"parser_unrecognized_with_top1_hint", "parser_unrecognized_taxonomy_empty"}:
        review_status = "candidate_for_future_inventory_review"
    else:
        review_status = "dq_backlog_only"
    future_fix_candidate = review_status == "candidate_for_future_inventory_review"
    return mode, secondary, future_fix_candidate, review_status, evidence


def _classify_top1(row: dict[str, str]) -> tuple[str, list[str], bool, str, str]:
    disposition = row.get("accepted_family_disposition", "")
    source_file = row.get("source_file", "")
    secondary: list[str] = ["top1_family_empty"] if not row.get("top1_family") else []
    if row.get("query_family") in {"", "<empty>"}:
        secondary.append("query_family_empty")
    if source_file == "global_repair_decision_table.csv":
        secondary.append("source_generated_artifact_risk")
    if _normalization_signals(row.get("query", "")):
        secondary.append("query_text_normalization_signal")

    if disposition == "same_domain_taxonomy_empty":
        mode = "top1_taxonomy_coverage_gap"
    elif disposition == "book_label_empty":
        mode = "book_label_empty_taxonomy_gap"
    elif disposition == "cross_domain_absorption":
        mode = "cross_domain_or_wrong_book_absorption"
    elif disposition == "label_taxonomy_mixture":
        mode = "label_taxonomy_mixture"
    else:
        mode = "taxonomy_coverage_gap_source_risky"

    accepted_oss = row.get("source_family", "").startswith("oss_")
    future_fix_candidate = mode in {"top1_taxonomy_coverage_gap", "book_label_empty_taxonomy_gap"} and accepted_oss
    review_status = "candidate_for_future_owner_review" if future_fix_candidate else "dq_backlog_only"
    evidence = row.get("disposition_reason", "") or row.get("coverage_issue", "")
    return mode, secondary, future_fix_candidate, review_status, evidence


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    rollup: list[dict[str, Any]],
    readiness: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.62 S6 Parser/Query Normalization Inventory Artifact Definition",
        "",
        "Read-only inventory artifacts for query_family_empty and top1_family_empty failure modes.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["inventory_rows", metrics["inventory_rows"]],
                ["qfe_inventory_rows", metrics["qfe_inventory_rows"]],
                ["top1_inventory_rows", metrics["top1_inventory_rows"]],
                ["future_fix_candidate_rows", metrics["future_fix_candidate_rows"]],
                ["source_generated_or_risky_rows", metrics["source_generated_or_risky_rows"]],
                ["artifact_definition_decision", metrics["artifact_definition_decision"]],
            ]
        ),
        "",
        "## Failure Mode Rollup",
        "",
        _md_table(
            [["failure_mode", "rows", "future_fix_candidate_rows", "source_risky_rows"]]
            + [[row["failure_mode"], row["rows"], row["future_fix_candidate_rows"], row["source_risky_rows"]] for row in rollup]
        ),
        "",
        "## Candidate Fix Readiness",
        "",
        _md_table(
            [["readiness_bucket", "rows", "representative_queries", "not_allowed"]]
            + [[row["readiness_bucket"], row["rows"], row["representative_queries"], row["not_allowed"]] for row in readiness]
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
    parser = argparse.ArgumentParser(description="Define S6 parser/query normalization inventory artifacts")
    parser.add_argument("--summary-1061", default=str(DEFAULT_1061_SUMMARY))
    parser.add_argument("--input-manifest-1061", default=str(DEFAULT_1061_INPUTS))
    parser.add_argument("--qfe-rows", default=str(DEFAULT_QFE_ROWS))
    parser.add_argument("--qfe-subbuckets", default=str(DEFAULT_QFE_SUBBUCKETS))
    parser.add_argument("--top1-rows", default=str(DEFAULT_TOP1_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1061 = _read_json(Path(args.summary_1061))
    input_manifest_1061 = _read_csv(Path(args.input_manifest_1061))
    qfe_rows = _read_csv(Path(args.qfe_rows))
    qfe_subbuckets = _read_csv(Path(args.qfe_subbuckets))
    top1_rows = _read_csv(Path(args.top1_rows))
    m1061 = summary_1061["metrics"]

    inventory_rows: list[dict[str, Any]] = []
    for index, row in enumerate(qfe_rows, start=1):
        mode, secondary, future_fix_candidate, review_status, evidence = _classify_qfe(row)
        query = row.get("query", "")
        inventory_rows.append(
            {
                "inventory_id": f"qfe_{index:04d}_{_hash_id(row.get('group_id', ''), row.get('sample_id', ''), query)}",
                "inventory_source": "9.25_query_family_empty_rows",
                "failure_mode": mode,
                "secondary_flags": ";".join(sorted(set(secondary))),
                "future_fix_candidate": _bool_text(future_fix_candidate),
                "review_status": review_status,
                "query": query,
                "normalization_signals": _normalization_signals(query),
                "matched_hint": row.get("matched_hint", ""),
                "inferred_bucket_or_domain": row.get("inferred_empty_subbucket", ""),
                "source_file": row.get("source_file", ""),
                "source_family": _source_family_guess(row.get("source_file", "")),
                "dominant_source_rate": row.get("subbucket_dominant_source_rate", ""),
                "province": row.get("province", ""),
                "query_family": row.get("query_family", "") or "<empty>",
                "top1_family": row.get("top1_family", "") or "<empty>",
                "top1_book_relation": row.get("top1_book_relation", ""),
                "rank_bucket": row.get("rank_bucket", ""),
                "primary_issue": row.get("primary_issue", ""),
                "learning_status": row.get("learning_status", ""),
                "evidence_note": evidence,
                "learning_use_allowed": "false",
                "implementation_allowed": "false",
            }
        )

    for index, row in enumerate(top1_rows, start=1):
        mode, secondary, future_fix_candidate, review_status, evidence = _classify_top1(row)
        query = row.get("query", "")
        inventory_rows.append(
            {
                "inventory_id": f"top1cov_{index:04d}_{_hash_id(row.get('row_id', ''), query)}",
                "inventory_source": "10.31_top1_family_coverage_rows",
                "failure_mode": mode,
                "secondary_flags": ";".join(sorted(set(secondary))),
                "future_fix_candidate": _bool_text(future_fix_candidate),
                "review_status": review_status,
                "query": query,
                "normalization_signals": _normalization_signals(query),
                "matched_hint": "",
                "inferred_bucket_or_domain": row.get("domain", ""),
                "source_file": row.get("source_file", ""),
                "source_family": row.get("source_family", ""),
                "dominant_source_rate": "",
                "province": row.get("province", ""),
                "query_family": row.get("query_family", "") or "<empty>",
                "top1_family": row.get("top1_family", "") or "<empty>",
                "top1_book_relation": row.get("book_relation", ""),
                "rank_bucket": "",
                "primary_issue": row.get("accepted_family_disposition", ""),
                "learning_status": row.get("learning_disposition", ""),
                "evidence_note": evidence,
                "learning_use_allowed": "false",
                "implementation_allowed": "false",
            }
        )

    rollup_counter: dict[str, Counter[str]] = defaultdict(Counter)
    example_queries: dict[str, list[str]] = defaultdict(list)
    for row in inventory_rows:
        mode = str(row["failure_mode"])
        rollup_counter[mode]["rows"] += 1
        if row["future_fix_candidate"] == "true":
            rollup_counter[mode]["future_fix_candidate_rows"] += 1
        if "source_generated_artifact_risk" in str(row["secondary_flags"]) or row["failure_mode"] == "source_generated_artifact":
            rollup_counter[mode]["source_risky_rows"] += 1
        if row["normalization_signals"]:
            rollup_counter[mode]["normalization_signal_rows"] += 1
        if len(example_queries[mode]) < 6 and row["query"]:
            example_queries[mode].append(str(row["query"]))

    rollup_rows = [
        {
            "failure_mode": mode,
            "rows": counts["rows"],
            "future_fix_candidate_rows": counts["future_fix_candidate_rows"],
            "source_risky_rows": counts["source_risky_rows"],
            "normalization_signal_rows": counts["normalization_signal_rows"],
            "example_queries": " | ".join(example_queries[mode]),
        }
        for mode, counts in rollup_counter.items()
    ]
    rollup_rows.sort(key=lambda row: (-_int(row["rows"]), row["failure_mode"]))

    readiness_counter: dict[str, Counter[str]] = defaultdict(Counter)
    readiness_examples: dict[str, list[str]] = defaultdict(list)
    for row in inventory_rows:
        if row["future_fix_candidate"] == "true":
            bucket = "future_owner_review_candidate"
        elif "source_generated_artifact_risk" in str(row["secondary_flags"]) or row["failure_mode"] == "source_generated_artifact":
            bucket = "blocked_source_generated_or_dominated"
        elif row["failure_mode"] in {"label_expected_mismatch", "label_taxonomy_mixture"}:
            bucket = "blocked_label_expected_or_mixture_review"
        else:
            bucket = "dq_backlog_only"
        readiness_counter[bucket]["rows"] += 1
        if len(readiness_examples[bucket]) < 8 and row["query"]:
            readiness_examples[bucket].append(str(row["query"]))

    readiness_rows = [
        {
            "readiness_bucket": bucket,
            "rows": counts["rows"],
            "representative_queries": " | ".join(readiness_examples[bucket]),
            "not_allowed": "no implementation, no parser/taxonomy edits, no training, no Top1 gain claim",
        }
        for bucket, counts in readiness_counter.items()
    ]
    readiness_rows.sort(key=lambda row: (-_int(row["rows"]), row["readiness_bucket"]))

    blocked_actions = [
        {
            "blocked_action": "edit_parser_or_text_normalizer",
            "reason": "10.62 defines inventory artifacts only; parser behavior remains unchanged.",
            "allowed_after": "future implementation plan with explicit go and reviewed mappings",
        },
        {
            "blocked_action": "edit_taxonomy_or_family_labels",
            "reason": "Inventory rows are DQ evidence, not accepted row mappings.",
            "allowed_after": "future owner acceptance and implementation plan",
        },
        {
            "blocked_action": "train_or_tune_from_inventory",
            "reason": "Inventory rows include source/generated and taxonomy backlog evidence, not learning labels.",
            "allowed_after": "future learning re-entry review with accepted non-generated effect evidence",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "10.62 uses existing dev/OOF/9.x artifacts and performs no selection.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain",
            "reason": "Inventory classification is not an algorithm or data fix.",
            "allowed_after": "future validated candidate or accepted DQ implementation with loss audit",
        },
    ]

    future_fix_candidate_rows = sum(1 for row in inventory_rows if row["future_fix_candidate"] == "true")
    source_risky_rows = sum(
        1
        for row in inventory_rows
        if "source_generated_artifact_risk" in str(row["secondary_flags"]) or row["failure_mode"] == "source_generated_artifact"
    )
    normalization_signal_rows = sum(1 for row in inventory_rows if row["normalization_signals"])
    qfe_inventory_rows = sum(1 for row in inventory_rows if row["inventory_source"] == "9.25_query_family_empty_rows")
    top1_inventory_rows = sum(1 for row in inventory_rows if row["inventory_source"] == "10.31_top1_family_coverage_rows")

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "input_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_input_manifest.csv")),
        "failure_mode_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_failure_mode_inventory.csv")),
        "failure_mode_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_failure_mode_rollup.csv")),
        "candidate_fix_readiness_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_fix_readiness.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1061["stage"],
        "design_gate_decision": m1061.get("s6_design_gate_decision"),
        "inventory_rows": len(inventory_rows),
        "qfe_inventory_rows": qfe_inventory_rows,
        "top1_inventory_rows": top1_inventory_rows,
        "qfe_subbucket_rows": len(qfe_subbuckets),
        "failure_mode_count": len(rollup_rows),
        "future_fix_candidate_rows": future_fix_candidate_rows,
        "future_fix_candidate_failure_modes": sum(1 for row in rollup_rows if _int(row["future_fix_candidate_rows"]) > 0),
        "source_generated_or_risky_rows": source_risky_rows,
        "normalization_signal_rows": normalization_signal_rows,
        "candidate_fix_readiness_bucket_count": len(readiness_rows),
        "artifact_definition_decision": "inventory_artifacts_defined",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.62 S6 parser/query normalization inventory artifact definition",
        "read_only": True,
        "inventory_artifact_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define the S6 inventory artifacts. Query-family-empty and top1-family-empty evidence is now classified into parser-unrecognized, "
            "taxonomy coverage, query normalization, source/generated artifact risk, label/expected mismatch, and future fix-candidate readiness buckets. "
            "These artifacts support future review only and do not authorize parser edits, taxonomy edits, training, ranking changes, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.62 only writes read-only inventory artifacts from existing evidence. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.63 S6 inventory artifact acceptance gate",
            "goal": "Read-only decide whether the S6 inventory artifacts are acceptable as future parser/taxonomy fix support, and whether any fix candidate can enter a later planning gate.",
            "default": "acceptance gate only; no parser edit, taxonomy edit, training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["input_manifest_csv"]), input_manifest_1061, ["input_id", "path", "rows", "use", "status"])
    _write_csv(
        Path(artifacts["failure_mode_inventory_csv"]),
        inventory_rows,
        [
            "inventory_id",
            "inventory_source",
            "failure_mode",
            "secondary_flags",
            "future_fix_candidate",
            "review_status",
            "query",
            "normalization_signals",
            "matched_hint",
            "inferred_bucket_or_domain",
            "source_file",
            "source_family",
            "dominant_source_rate",
            "province",
            "query_family",
            "top1_family",
            "top1_book_relation",
            "rank_bucket",
            "primary_issue",
            "learning_status",
            "evidence_note",
            "learning_use_allowed",
            "implementation_allowed",
        ],
    )
    _write_csv(
        Path(artifacts["failure_mode_rollup_csv"]),
        rollup_rows,
        ["failure_mode", "rows", "future_fix_candidate_rows", "source_risky_rows", "normalization_signal_rows", "example_queries"],
    )
    _write_csv(
        Path(artifacts["candidate_fix_readiness_csv"]),
        readiness_rows,
        ["readiness_bucket", "rows", "representative_queries", "not_allowed"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, rollup_rows, readiness_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
