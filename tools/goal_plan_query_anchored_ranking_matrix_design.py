from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_TASK_REDEFINITION_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_summary.json"
DEFAULT_UNDIRECTED_SCHEMA_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_schema_summary.json"
DEFAULT_FEATURE_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_design_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_design_summary.md"
DEFAULT_INPUT_SOURCES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_input_sources.csv"
DEFAULT_GROUP_SCHEMA_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_group_schema.csv"
DEFAULT_ROW_SCHEMA_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_row_schema.csv"
DEFAULT_LABEL_POLICY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_label_policy.csv"
DEFAULT_FEATURE_GROUPS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_feature_groups.csv"
DEFAULT_GATES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_gates.csv"
DEFAULT_FORBIDDEN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_forbidden_actions.csv"
DEFAULT_NEXT_STAGE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_next_stage.csv"
ANCHOR_VALIDATION_STATUSES = {"anchor_reliable", "anchor_usable_no_strong_conflict"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["available"] = True
    payload["path"] = str(path)
    return payload


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _expected_ids(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("expected_id", "quota_id", "correct_quota_id", "positive_id"):
        if row.get(key):
            values.append(str(row[key]))
    for key in ("expected_ids", "oracle_quota_ids", "expected_quota_ids", "stored_ids"):
        raw = row.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            values.extend(str(value) for value in raw)
            continue
        try:
            parsed = json.loads(str(raw))
        except Exception:
            parsed = raw
        if isinstance(parsed, list):
            values.extend(str(value) for value in parsed)
        else:
            values.append(str(parsed))
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in str(value).split("|") if part.strip())
    return sorted(set(result))


def _query_text(row: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _clean(row.get("bill_name") or row.get("name")),
            _clean(row.get("bill_text") or row.get("description")),
        )
        if part
    )


def _has_explicit_target(row: dict[str, Any]) -> bool:
    target_fields = [
        "target_family",
        "target_param_type",
        "target_param_value",
        "target_dn",
        "target_cable_section",
        "target_cable_cores",
        "target_circuits",
        "target_concrete_grade",
        "target_thickness",
    ]
    return any(_clean(row.get(field)) for field in target_fields)


def _source_specs() -> list[dict[str, Any]]:
    return [
        {
            "source_name": "expanded_dev_raw",
            "path": PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "dev.jsonl",
            "split_role": "train_dev_candidate",
            "anchor_policy": "must_run_anchor_audit_before_training",
        },
        {
            "source_name": "expanded_heldout_raw",
            "path": PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "heldout.jsonl",
            "split_role": "heldout_raw_reference",
            "anchor_policy": "use_anchor_clean_validation_instead",
        },
        {
            "source_name": "expanded_hard_raw",
            "path": PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "hard.jsonl",
            "split_role": "hard_raw_reference",
            "anchor_policy": "use_anchor_clean_validation_instead",
        },
        {
            "source_name": "anchor_clean_heldout",
            "path": PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "heldout_validation.jsonl",
            "split_role": "heldout_eval_anchor_clean",
            "anchor_policy": "allowed_for_eval_only_not_training",
        },
        {
            "source_name": "anchor_clean_hard",
            "path": PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "hard_validation.jsonl",
            "split_role": "hard_eval_anchor_clean",
            "anchor_policy": "allowed_for_eval_only_not_training",
        },
    ]


def _source_rows() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for spec in _source_specs():
        rows = _iter_jsonl(Path(spec["path"]))
        total = len(rows)
        query_count = sum(1 for row in rows if _query_text(row))
        province_count = sum(1 for row in rows if _clean(row.get("province")))
        expected_count = sum(1 for row in rows if _expected_ids(row))
        explicit_target_count = sum(1 for row in rows if _has_explicit_target(row))
        anchor_reliable_count = sum(1 for row in rows if _clean(row.get("anchor_status")) == "anchor_reliable")
        anchor_usable_count = sum(1 for row in rows if _clean(row.get("anchor_status")) == "anchor_usable_no_strong_conflict")
        anchor_validation_count = sum(1 for row in rows if _clean(row.get("anchor_status")) in ANCHOR_VALIDATION_STATUSES)
        query_expected_count = sum(1 for row in rows if _query_text(row) and _clean(row.get("province")) and _expected_ids(row))
        query_target_count = sum(1 for row in rows if _query_text(row) and _clean(row.get("province")) and _has_explicit_target(row))
        result.append(
            {
                "source_name": spec["source_name"],
                "path": str(spec["path"]),
                "exists": str(Path(spec["path"]).exists()).lower(),
                "split_role": spec["split_role"],
                "anchor_policy": spec["anchor_policy"],
                "rows": total,
                "query_text_rows": query_count,
                "province_rows": province_count,
                "expected_id_rows": expected_count,
                "explicit_target_rows": explicit_target_count,
                "anchor_reliable_rows": anchor_reliable_count,
                "anchor_usable_no_strong_conflict_rows": anchor_usable_count,
                "anchor_validation_rows": anchor_validation_count,
                "query_expected_anchor_candidates": query_expected_count,
                "query_explicit_target_candidates": query_target_count,
                "query_expected_anchor_rate": _rate(query_expected_count, total),
                "status": _source_status(spec["source_name"], total, query_expected_count, anchor_validation_count),
            }
        )
    return result


def _source_status(source_name: str, total: int, query_expected: int, anchor_validation: int) -> str:
    if total <= 0:
        return "missing_or_empty"
    if source_name.startswith("anchor_clean"):
        return "usable_eval_source" if anchor_validation == total and query_expected == total else "anchor_clean_needs_review"
    if source_name == "expanded_dev_raw":
        return "usable_after_dev_anchor_audit" if query_expected > 0 else "needs_query_expected_rows"
    return "reference_only"


def _group_schema_rows() -> list[dict[str, Any]]:
    rows = [
        ("split", "string", "diagnostic", "dev/heldout/hard; split must be leakage-safe."),
        ("group_id", "string", "group", "One query sample group; all TopK candidate rows for that query are contiguous."),
        ("province", "string", "diagnostic", "Target local quota database; never a training feature by default."),
        ("query_text", "string", "diagnostic", "bill_name + bill_text or equivalent; required for directed labels."),
        ("query_unit", "string", "diagnostic", "Original bill unit, if present."),
        ("query_specialty", "string", "diagnostic", "Requested book/specialty, if present."),
        ("sample_id", "string", "leakage_key", "Used for split and exclusion; not a feature."),
        ("source_file", "string", "leakage_key", "Used for leakage-safe split; not a feature."),
        ("project_name", "string", "leakage_key", "Used for leakage-safe split; not a feature."),
        ("anchor_type", "enum", "anchor", "expected_id_anchor or explicit_target_anchor."),
        ("anchor_status", "enum", "anchor", "anchor_reliable or anchor_usable_no_strong_conflict required for expected_id anchors."),
        ("expected_ids", "json_array", "target_diagnostic", "Local expected quota ids; not a feature."),
        ("target_param_json", "json_object", "target_diagnostic", "Explicit target family/param/tier when expected_id is absent."),
        ("topk", "integer", "diagnostic", "Candidate recall depth, default Top80."),
        ("positive_count", "integer", "audit", "Must be >=1 for training/eval matrix groups."),
        ("candidate_count", "integer", "audit", "Must be >=2 for ranking group."),
        ("recall_gap_reason", "string", "audit", "Set when expected id is not in TopK; group goes to recall gap, not LTR training."),
    ]
    return [{"field_name": name, "dtype": dtype, "role": role, "notes": notes} for name, dtype, role, notes in rows]


def _row_schema_rows() -> list[dict[str, Any]]:
    rows = [
        ("label", "integer", "target", "1 if candidate is anchored positive, otherwise 0. Only valid inside accepted query-anchored groups."),
        ("candidate_rank", "integer", "diagnostic", "Baseline retrieval rank before rerank; may be transformed to base_rank feature."),
        ("quota_id", "string", "diagnostic", "Candidate id; not a feature."),
        ("quota_name", "string", "diagnostic", "Candidate display text; raw text not passed directly as a numeric feature."),
        ("quota_unit", "string", "diagnostic", "Candidate unit."),
        ("quota_book", "string", "diagnostic", "Candidate book."),
        ("quota_chapter", "string", "diagnostic", "Candidate chapter."),
        ("candidate_family", "string", "diagnostic", "Extracted candidate family."),
        ("baseline_score", "float", "feature_source", "Pure-search score before rerank."),
        ("bm25_score", "float", "feature_source", "Lexical score from candidate retrieval."),
        ("national_cluster_bonus", "float", "feature_source", "National index cluster signal."),
        ("query_candidate_feature_json", "json_object", "feature_source", "Query/candidate match features materialized into numeric matrix."),
        ("reasons", "json_array", "diagnostic", "Search reasons; may derive counts, raw text excluded."),
    ]
    return [{"field_name": name, "dtype": dtype, "role": role, "notes": notes} for name, dtype, role, notes in rows]


def _label_policy_rows() -> list[dict[str, Any]]:
    return [
        {
            "anchor_type": "expected_id_anchor",
            "positive_rule": "candidate.quota_id in expected_ids and expected_ids are verified against the target province local quota.db",
            "negative_rule": "candidate.quota_id not in expected_ids within the same query group",
            "group_acceptance": "query_text present; province present; anchor_status in {anchor_reliable, anchor_usable_no_strong_conflict}; at least one expected_id appears in TopK; candidate_count>=2",
            "group_rejection": "expected_id missing; expected_id not in local quota.db; expected positive absent from TopK; split leakage violation",
            "training_allowed": "true",
            "notes": "Multiple expected_ids may produce multiple positive rows.",
        },
        {
            "anchor_type": "explicit_target_anchor",
            "positive_rule": "candidate matches explicit target family plus all declared target params/tier constraints",
            "negative_rule": "candidate conflicts with target family/subtype/param inside same TopK group",
            "group_acceptance": "query_text present; province present; target_family present; at least one exact target parameter declared; positive_count>=1; candidate_count>=2",
            "group_rejection": "target only inferred weakly from pair order; target params absent; ambiguous multi-target query without expected_id",
            "training_allowed": "design_only_until_stage_6_5",
            "notes": "Use very conservatively; v1 should prefer expected_id anchors.",
        },
        {
            "anchor_type": "recall_gap",
            "positive_rule": "none",
            "negative_rule": "none",
            "group_acceptance": "not accepted for LTR matrix",
            "group_rejection": "expected anchor absent from TopK",
            "training_allowed": "false",
            "notes": "Keep for recall-gap analysis, not ranking training.",
        },
    ]


def _feature_group_rows(feature_whitelist_path: Path) -> list[dict[str, Any]]:
    existing_features: list[str] = []
    if feature_whitelist_path.exists():
        payload = json.loads(feature_whitelist_path.read_text(encoding="utf-8"))
        existing_features = [str(item) for item in payload.get("training_features") or []]
    groups = [
        ("baseline_rank_score", "base_rank|current_score|confidence", "ready_from_search", "Baseline ordering and score; allowed because it is query/candidate context."),
        ("lexical_match", "bm25_score|token_overlap|domain_label_overlap_count", "ready_from_search", "Query-candidate lexical evidence."),
        ("unit_book_chapter", "unit_exact|book_match|book_conflict|chapter_book_match", "ready_from_search", "Local book/unit constraints."),
        ("family_signal", "query_family_present|candidate_family_present|family_match|family_conflict", "ready_from_signal", "Query and candidate family alignment."),
        ("semantic_parts", "action_match|material_match|connection_match|install_method_match", "ready_from_signal", "Structured rule signal matches."),
        ("param_match", "param_exact_count|dn_exact|cable_section_exact|thickness_exact|width_height_exact", "ready_from_signal", "Explicit query parameter and candidate tier comparison."),
        ("conflict_flags", "has_domain_conflict|has_book_conflict_reason|has_param_conflict_reason", "ready_from_search", "Safety/conflict features derived from rules."),
        ("national_signal", "national_cluster_bonus|has_national_reason", "ready_from_search", "Offline national index support."),
    ]
    return [
        {
            "feature_group": group,
            "example_features": examples,
            "status": status,
            "notes": notes,
            "existing_whitelist_overlap": sum(1 for feature in examples.split("|") if feature in existing_features),
        }
        for group, examples, status, notes in groups
    ]


def _gates_rows(source_rows: list[dict[str, Any]], undirected_summary: dict[str, Any]) -> list[dict[str, Any]]:
    heldout_ok = any(row["source_name"] == "anchor_clean_heldout" and row["status"] == "usable_eval_source" for row in source_rows)
    hard_ok = any(row["source_name"] == "anchor_clean_hard" and row["status"] == "usable_eval_source" for row in source_rows)
    dev_raw = next((row for row in source_rows if row["source_name"] == "expanded_dev_raw"), {})
    anchor_clean_evidence = "; ".join(
        f"{row['source_name']} validation_rows={row.get('anchor_validation_rows', '')}/{row.get('rows', '')}"
        for row in source_rows
        if row["source_name"].startswith("anchor_clean")
    )
    undirected_gate = bool(_value(undirected_summary, "summary", "passes_undirected_schema_gate", default=False))
    return [
        {
            "gate": "query_anchor_presence_gate",
            "rule": "Every directed group must have query_text and either validated expected_ids or explicit target params.",
            "current_evidence": f"expanded_dev query_expected_anchor_candidates={dev_raw.get('query_expected_anchor_candidates', '')}",
            "status": "required_for_matrix_generator",
            "action": "reject groups without query anchor",
        },
        {
            "gate": "local_expected_id_gate",
            "rule": "expected_ids must resolve in the target province local quota.db before labels are created.",
            "current_evidence": anchor_clean_evidence if heldout_ok or hard_ok else "needs_anchor_audit",
            "status": "required_for_matrix_generator",
            "action": "write unresolved samples to anchor_excluded, not matrix",
        },
        {
            "gate": "topk_positive_presence_gate",
            "rule": "At least one positive candidate must appear in TopK; otherwise the sample is a recall gap, not an LTR group.",
            "current_evidence": "not_evaluated_in_stage_6_4",
            "status": "required_for_stage_6_5",
            "action": "write TopK missing positives to recall_gap.jsonl",
        },
        {
            "gate": "leakage_safe_split_gate",
            "rule": "No source_file/project_name/sample_id group may be split across train and heldout.",
            "current_evidence": "reuse existing OSS split policy",
            "status": "required_for_matrix_generator",
            "action": "build from split files only; never random-row split after matrix generation",
        },
        {
            "gate": "no_answer_prior_feature_gate",
            "rule": "expected_ids, quota_id, sample_id, source_file, project_name, raw query text and province are diagnostics only.",
            "current_evidence": "feature whitelist must exclude identifiers and target fields",
            "status": "required_for_matrix_generator",
            "action": "fail build if forbidden fields enter training_features",
        },
        {
            "gate": "undirected_pair_not_ranking_gate",
            "rule": "Current self-supervised pair labels may not be mixed into query-anchored ranking labels.",
            "current_evidence": f"passes_undirected_schema_gate={undirected_gate}",
            "status": "pass_policy_available" if undirected_gate else "needs_review",
            "action": "use undirected contrast only for conflict features or diagnostics",
        },
        {
            "gate": "eval_only_design_gate",
            "rule": "Stage 6.4 writes schema and gates only.",
            "current_evidence": "no matrix generation in this stage",
            "status": "pass",
            "action": "defer matrix dry run to stage 6.5",
        },
    ]


def _forbidden_rows() -> list[dict[str, Any]]:
    return [
        {
            "forbidden_action": "create_label_from_unanchored_pair_order",
            "reason": "Stage 6.1 proved self-supervised pair direction is random.",
            "replacement": "Use expected_id_anchor or explicit_target_anchor only.",
        },
        {
            "forbidden_action": "train_on_recall_gap_samples_as_all_negative",
            "reason": "A group without a positive candidate cannot teach reranking.",
            "replacement": "Send to recall-gap audit and improve retrieval.",
        },
        {
            "forbidden_action": "use_expected_id_as_feature",
            "reason": "Answer ids leak the target and are province-local.",
            "replacement": "Use expected_ids only to assign labels and evaluate.",
        },
        {
            "forbidden_action": "use_foreign_province_candidate_as_positive",
            "reason": "Final candidates must come from target province quota.db.",
            "replacement": "Resolve labels against target local quota.db only.",
        },
        {
            "forbidden_action": "tune_thresholds_on_heldout",
            "reason": "Heldout must stay validation-only for final progress measurement.",
            "replacement": "Use dev/calibration for thresholds, then report heldout once.",
        },
    ]


def _next_stage_rows() -> list[dict[str, Any]]:
    return [
        {
            "stage": "6.5",
            "name": "query anchored matrix dry run",
            "scope": "Generate dev/heldout/hard Top80 matrix with expected_id anchors and recall-gap outputs.",
            "train": "false",
            "output": "ltr_matrix_<split>.csv + group + ltr_features_<split>.jsonl + build summary",
        },
        {
            "stage": "6.6",
            "name": "query anchored loader audit",
            "scope": "Verify labels, groups, forbidden fields, positive TopK presence and feature coverage.",
            "train": "false",
            "output": "loader audit + gap buckets",
        },
        {
            "stage": "6.7",
            "name": "dev-only ranking trial",
            "scope": "Only after 6.5/6.6 pass, train on dev and evaluate heldout/hard.",
            "train": "true_but_not_online",
            "output": "offline metrics; no search integration",
        },
    ]


def _summary(
    *,
    task_redefinition: dict[str, Any],
    undirected_summary: dict[str, Any],
    source_rows: list[dict[str, Any]],
    gates: list[dict[str, Any]],
) -> dict[str, Any]:
    usable_eval_sources = sum(1 for row in source_rows if row["status"] == "usable_eval_source")
    dev_candidate_rows = next((int(row["query_expected_anchor_candidates"]) for row in source_rows if row["source_name"] == "expanded_dev_raw"), 0)
    gate_failures = [row for row in gates if row["status"].startswith("fail")]
    required_gates = sum(1 for row in gates if row["status"].startswith("required"))
    return {
        "decision": "design_query_anchored_directed_ranking_matrix",
        "eval_only": True,
        "no_training": True,
        "matrix_generation": False,
        "current_task_redefinition_gate": _value(task_redefinition, "summary", "passes_task_redefinition_gate", default=False),
        "undirected_schema_gate": _value(undirected_summary, "summary", "passes_undirected_schema_gate", default=False),
        "input_sources": len(source_rows),
        "usable_eval_sources": usable_eval_sources,
        "expanded_dev_query_expected_anchor_candidates": dev_candidate_rows,
        "gate_count": len(gates),
        "required_gate_count": required_gates,
        "gate_failure_count": len(gate_failures),
        "passes_query_anchored_design_gate": (
            _value(task_redefinition, "summary", "passes_task_redefinition_gate", default=False) is True
            and _value(undirected_summary, "summary", "passes_undirected_schema_gate", default=False) is True
            and len(gate_failures) == 0
            and dev_candidate_rows > 0
        ),
        "recommended_next_stage": "Stage 6.5 eval-only query anchored matrix dry run; generate matrix but do not train.",
    }


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Query-Anchored Ranking Matrix Design",
        "",
        "Stage 6.4 eval-only design. It defines the directed ranking matrix that may use label=1/0 only when a query has a validated expected_id anchor or an explicit target parameter. It does not generate a matrix or train.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["decision", summary["decision"]],
                ["matrix_generation", summary["matrix_generation"]],
                ["input_sources", summary["input_sources"]],
                ["usable_eval_sources", summary["usable_eval_sources"]],
                ["expanded_dev_query_expected_anchor_candidates", summary["expanded_dev_query_expected_anchor_candidates"]],
                ["gate_count", summary["gate_count"]],
                ["required_gate_count", summary["required_gate_count"]],
                ["gate_failure_count", summary["gate_failure_count"]],
                ["passes_query_anchored_design_gate", summary["passes_query_anchored_design_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Input Sources",
        "",
        _md_table(
            [
                ["source", "rows", "query_expected", "anchor_validation", "status"],
                *[
                    [row["source_name"], row["rows"], row["query_expected_anchor_candidates"], row["anchor_validation_rows"], row["status"]]
                    for row in report["input_sources"]
                ],
            ]
        ),
        "",
        "## Gates",
        "",
        _md_table([["gate", "status", "action"], *[[row["gate"], row["status"], row["action"]] for row in report["gates"]]]),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6.4 eval-only query anchored ranking matrix design")
    parser.add_argument("--task-redefinition-json", default=str(DEFAULT_TASK_REDEFINITION_JSON))
    parser.add_argument("--undirected-schema-json", default=str(DEFAULT_UNDIRECTED_SCHEMA_JSON))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_FEATURE_WHITELIST))
    parser.add_argument("--input-sources-csv", default=str(DEFAULT_INPUT_SOURCES_CSV))
    parser.add_argument("--group-schema-csv", default=str(DEFAULT_GROUP_SCHEMA_CSV))
    parser.add_argument("--row-schema-csv", default=str(DEFAULT_ROW_SCHEMA_CSV))
    parser.add_argument("--label-policy-csv", default=str(DEFAULT_LABEL_POLICY_CSV))
    parser.add_argument("--feature-groups-csv", default=str(DEFAULT_FEATURE_GROUPS_CSV))
    parser.add_argument("--gates-csv", default=str(DEFAULT_GATES_CSV))
    parser.add_argument("--forbidden-csv", default=str(DEFAULT_FORBIDDEN_CSV))
    parser.add_argument("--next-stage-csv", default=str(DEFAULT_NEXT_STAGE_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    task_redefinition = _read_json(Path(args.task_redefinition_json))
    undirected_summary = _read_json(Path(args.undirected_schema_json))
    source_rows = _source_rows()
    group_schema = _group_schema_rows()
    row_schema = _row_schema_rows()
    label_policy = _label_policy_rows()
    feature_groups = _feature_group_rows(Path(args.feature_whitelist))
    gates = _gates_rows(source_rows, undirected_summary)
    forbidden = _forbidden_rows()
    next_stage = _next_stage_rows()
    summary = _summary(
        task_redefinition=task_redefinition,
        undirected_summary=undirected_summary,
        source_rows=source_rows,
        gates=gates,
    )

    _write_csv(
        Path(args.input_sources_csv),
        source_rows,
        [
            "source_name",
            "path",
            "exists",
            "split_role",
            "anchor_policy",
            "rows",
            "query_text_rows",
            "province_rows",
            "expected_id_rows",
            "explicit_target_rows",
            "anchor_reliable_rows",
            "anchor_usable_no_strong_conflict_rows",
            "anchor_validation_rows",
            "query_expected_anchor_candidates",
            "query_explicit_target_candidates",
            "query_expected_anchor_rate",
            "status",
        ],
    )
    _write_csv(Path(args.group_schema_csv), group_schema, ["field_name", "dtype", "role", "notes"])
    _write_csv(Path(args.row_schema_csv), row_schema, ["field_name", "dtype", "role", "notes"])
    _write_csv(Path(args.label_policy_csv), label_policy, ["anchor_type", "positive_rule", "negative_rule", "group_acceptance", "group_rejection", "training_allowed", "notes"])
    _write_csv(Path(args.feature_groups_csv), feature_groups, ["feature_group", "example_features", "status", "notes", "existing_whitelist_overlap"])
    _write_csv(Path(args.gates_csv), gates, ["gate", "rule", "current_evidence", "status", "action"])
    _write_csv(Path(args.forbidden_csv), forbidden, ["forbidden_action", "reason", "replacement"])
    _write_csv(Path(args.next_stage_csv), next_stage, ["stage", "name", "scope", "train", "output"])

    report = {
        "stage": "Goal LTR v1 / stage 6.4 query anchored ranking matrix design",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "no_matrix_generation": True,
        "inputs": {
            "task_redefinition_json": str(Path(args.task_redefinition_json)),
            "undirected_schema_json": str(Path(args.undirected_schema_json)),
            "feature_whitelist": str(Path(args.feature_whitelist)),
        },
        "summary": summary,
        "input_sources": source_rows,
        "group_schema": group_schema,
        "row_schema": row_schema,
        "label_policy": label_policy,
        "feature_groups": feature_groups,
        "gates": gates,
        "forbidden_actions": forbidden,
        "next_stage": next_stage,
        "artifacts": {
            "input_sources_csv": str(Path(args.input_sources_csv)),
            "group_schema_csv": str(Path(args.group_schema_csv)),
            "row_schema_csv": str(Path(args.row_schema_csv)),
            "label_policy_csv": str(Path(args.label_policy_csv)),
            "feature_groups_csv": str(Path(args.feature_groups_csv)),
            "gates_csv": str(Path(args.gates_csv)),
            "forbidden_csv": str(Path(args.forbidden_csv)),
            "next_stage_csv": str(Path(args.next_stage_csv)),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "decision": summary["decision"],
                    "matrix_generation": summary["matrix_generation"],
                    "input_sources": summary["input_sources"],
                    "usable_eval_sources": summary["usable_eval_sources"],
                    "expanded_dev_query_expected_anchor_candidates": summary["expanded_dev_query_expected_anchor_candidates"],
                    "gate_count": summary["gate_count"],
                    "gate_failure_count": summary["gate_failure_count"],
                    "passes_query_anchored_design_gate": summary["passes_query_anchored_design_gate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
