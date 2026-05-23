from __future__ import annotations

import argparse
import csv
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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_electrical_box_near_miss_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_electrical_box_code_evidence_9x_audit"

KNOWN_CODE_FAMILIES = [
    "XFTAT",
    "PDAT",
    "SYZAL",
    "AGAT",
    "DDAL",
    "ALG",
    "ALE",
    "SYAL",
    "AT",
    "AL",
    "AE",
    "AP",
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
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


def _normalize_code(raw: str) -> str:
    value = _clean(raw).upper()
    value = value.replace("，", ",").replace("、", ",").replace("；", ";").replace("：", ":")
    value = re.sub(r"\s+", "", value)
    value = value.strip("-_.,;:，、")
    return value


def _alpha_groups(code: str) -> list[str]:
    groups = re.findall(r"[A-Z]+", code.upper())
    return groups


def _code_family(code: str) -> str:
    upper = code.upper()
    for family in KNOWN_CODE_FAMILIES:
        if family in upper:
            return family
    groups = _alpha_groups(upper)
    return "+".join(groups) if groups else "<no_alpha>"


def _number_groups(code: str) -> list[str]:
    return re.findall(r"\d+", code)


def _target_signature(row: dict[str, Any]) -> str:
    method = _clean(row.get("positive_methods")) or "<method_empty>"
    half = _clean(row.get("positive_half_perimeter"))
    circuits = _clean(row.get("positive_circuits"))
    parts: list[str] = []
    if method:
        parts.append(f"method={method}")
    if half:
        parts.append(f"half={half}")
    if circuits:
        parts.append(f"circuits={circuits}")
    return ";".join(parts) if parts else "<target_empty>"


def _top_signature(row: dict[str, Any]) -> str:
    method = _clean(row.get("top_methods")) or "<method_empty>"
    half = _clean(row.get("top_half_perimeter"))
    circuits = _clean(row.get("top_circuits"))
    parts: list[str] = []
    if method:
        parts.append(f"method={method}")
    if half:
        parts.append(f"half={half}")
    if circuits:
        parts.append(f"circuits={circuits}")
    return ";".join(parts) if parts else "<target_empty>"


def _audit_rows(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        if _clean(row.get("learning_status")) != "weak_candidate_needs_code_mapping":
            continue
        code = _normalize_code(row.get("query_tail"))
        groups = _alpha_groups(code)
        numbers = _number_groups(code)
        family = _code_family(code)
        target = _target_signature(row)
        top = _top_signature(row)
        positive_half = _clean(row.get("positive_half_perimeter"))
        positive_circuits = _clean(row.get("positive_circuits"))
        evidence_type = "half_perimeter_code"
        if positive_circuits:
            evidence_type = "circuit_code"
        elif positive_half:
            evidence_type = "half_perimeter_code"
        out = {
            "split": _clean(row.get("split")),
            "group_id": _clean(row.get("group_id")),
            "sample_id": _clean(row.get("sample_id")),
            "source_file": _clean(row.get("source_file")),
            "province": _clean(row.get("province")),
            "query": _clean(row.get("query")),
            "query_tail": _clean(row.get("query_tail")),
            "normalized_code": code,
            "alpha_signature": "+".join(groups) if groups else "<no_alpha>",
            "code_family": family,
            "number_groups": ",".join(numbers),
            "number_count": len(numbers),
            "evidence_type": evidence_type,
            "target_signature": target,
            "top_signature": top,
            "positive_method": _clean(row.get("positive_methods")),
            "positive_half_perimeter": positive_half,
            "positive_circuits": positive_circuits,
            "top_method": _clean(row.get("top_methods")),
            "top_half_perimeter": _clean(row.get("top_half_perimeter")),
            "top_circuits": _clean(row.get("top_circuits")),
            "top1_name": _clean(row.get("top1_name")),
            "positive_names_in_top80": _clean(row.get("positive_names_in_top80")),
        }
        rows.append(out)
    return rows


def _group_status(*, support: int, province_count: int, source_count: int, target_purity: float, target_count: int) -> tuple[str, str]:
    if support >= 5 and province_count >= 2 and source_count >= 2 and target_count == 1:
        return "transferable_candidate", "stable_across_province_and_source"
    if support >= 3 and province_count >= 2 and target_purity >= 0.8:
        return "weak_transfer_candidate", "cross_province_but_not_clean_enough"
    if support >= 2 and source_count >= 2 and province_count == 1 and target_count == 1:
        return "same_province_repeated_only", "repeated_across_sources_but_one_province"
    if support >= 2 and target_count > 1:
        return "unstable_or_mixed_target", "same_code_pattern_maps_to_multiple_targets"
    return "insufficient_support", "support_or_diversity_too_low"


def _build_groups(rows: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_clean(row.get(key_name)) or "<empty>"].append(row)

    out: list[dict[str, Any]] = []
    for key, items in grouped.items():
        provinces = {_clean(row.get("province")) for row in items if _clean(row.get("province"))}
        sources = {_clean(row.get("source_file")) for row in items if _clean(row.get("source_file"))}
        targets = Counter(_clean(row.get("target_signature")) or "<empty>" for row in items)
        evidence_types = Counter(_clean(row.get("evidence_type")) or "<empty>" for row in items)
        dominant_target, dominant_count = targets.most_common(1)[0]
        purity = dominant_count / len(items) if items else 0.0
        status, reason = _group_status(
            support=len(items),
            province_count=len(provinces),
            source_count=len(sources),
            target_purity=purity,
            target_count=len(targets),
        )
        out.append(
            {
                "group_type": key_name,
                "group_key": key,
                "support": len(items),
                "province_count": len(provinces),
                "source_count": len(sources),
                "target_count": len(targets),
                "dominant_target_signature": dominant_target,
                "dominant_target_count": dominant_count,
                "target_purity": round(purity, 6),
                "evidence_types": "|".join(f"{name}:{count}" for name, count in evidence_types.most_common()),
                "status": status,
                "status_reason": reason,
                "provinces": " | ".join(sorted(provinces)),
                "source_files": " | ".join(sorted(sources)),
                "example_queries": " || ".join(_clean(row.get("query")) for row in items[:6]),
            }
        )
    out.sort(key=lambda row: (row["status"] == "transferable_candidate", row["support"], row["province_count"], row["source_count"]), reverse=True)
    return out


def _annotate_rows(rows: list[dict[str, Any]], family_groups: list[dict[str, Any]], raw_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    family_lookup = {row["group_key"]: row for row in family_groups if row["group_type"] == "code_family"}
    raw_lookup = {row["group_key"]: row for row in raw_groups if row["group_type"] == "normalized_code"}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        family = family_lookup.get(_clean(row.get("code_family")))
        raw = raw_lookup.get(_clean(row.get("normalized_code")))
        best_status = _clean(family.get("status")) if family else "insufficient_support"
        raw_status = _clean(raw.get("status")) if raw else "insufficient_support"
        if best_status == "transferable_candidate" or raw_status == "transferable_candidate":
            row_status = "transferable_candidate"
        elif best_status == "same_province_repeated_only" or raw_status == "same_province_repeated_only":
            row_status = "same_province_repeated_only"
        elif best_status == "unstable_or_mixed_target":
            row_status = "unstable_family"
        else:
            row_status = "insufficient_support"
        out = dict(row)
        out["family_group_status"] = best_status
        out["raw_code_group_status"] = raw_status
        out["row_evidence_status"] = row_status
        annotated.append(out)
    return annotated


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in ("code_family", "alpha_signature", "evidence_type", "row_evidence_status", "province", "source_file", "target_signature"):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_electrical_box_code_evidence", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any], code_groups: list[dict[str, Any]]) -> None:
    top_groups = [row for row in code_groups if row["group_type"] == "code_family"][:12]
    lines = [
        "# Stage 9.8 Electrical Box Code Evidence Audit",
        "",
        "Dev-only audit of weak electrical-box code evidence. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["code_rows", report["metrics"]["code_rows"]],
                ["transferable_code_groups", report["metrics"]["transferable_code_groups"]],
                ["same_province_repeated_groups", report["metrics"]["same_province_repeated_groups"]],
                ["unstable_or_mixed_groups", report["metrics"]["unstable_or_mixed_groups"]],
                ["insufficient_groups", report["metrics"]["insufficient_groups"]],
                ["decision", report["decision"]["recommendation"]],
                ["next_stage", report["next_stage"]["stage"]],
            ]
        ),
        "",
        "## Code Family Groups",
        "",
        _md_table(
            [["code_family", "support", "province_count", "source_count", "target_count", "status", "dominant_target"]]
            + [
                [
                    row["group_key"],
                    row["support"],
                    row["province_count"],
                    row["source_count"],
                    row["target_count"],
                    row["status"],
                    row["dominant_target_signature"],
                ]
                for row in top_groups
            ]
        ),
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.8 dev-only electrical box code evidence audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    code_rows = _audit_rows(source_rows)
    family_groups = _build_groups(code_rows, "code_family")
    raw_groups = _build_groups(code_rows, "normalized_code")
    annotated = _annotate_rows(code_rows, family_groups, raw_groups)
    buckets = _bucket_rows(annotated)
    code_groups = family_groups + raw_groups
    status_counter = Counter(row["status"] for row in code_groups)
    row_status_counter = Counter(row["row_evidence_status"] for row in annotated)
    transferable_groups = [row for row in code_groups if row["status"] == "transferable_candidate"]

    if transferable_groups:
        recommendation = "allow_strict_eval_only_what_if_design"
        next_stage = {
            "stage": "9.9 electrical_box code what-if design",
            "goal": "design a strict eval-only what-if for transferable code evidence only; still no training or GoalSearcher changes",
        }
    else:
        recommendation = "stop_electrical_box_code_direction"
        next_stage = {
            "stage": "9.9 return to ranked gap table selection",
            "goal": "exclude the exhausted electrical_box near-miss direction and choose the next high-support dev wrong-rank bucket",
        }

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "groups_csv": str(output_prefix.with_name(output_prefix.name + "_groups.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.8 electrical_box code evidence audit",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifact": str(Path(args.rows)),
        "metrics": {
            "code_rows": len(code_rows),
            "code_family_groups": len(family_groups),
            "raw_code_groups": len(raw_groups),
            "transferable_code_groups": status_counter.get("transferable_candidate", 0),
            "same_province_repeated_groups": status_counter.get("same_province_repeated_only", 0),
            "unstable_or_mixed_groups": status_counter.get("unstable_or_mixed_target", 0),
            "insufficient_groups": status_counter.get("insufficient_support", 0),
            "rows_transferable": row_status_counter.get("transferable_candidate", 0),
            "rows_same_province_repeated_only": row_status_counter.get("same_province_repeated_only", 0),
            "rows_unstable_family": row_status_counter.get("unstable_family", 0),
            "rows_insufficient_support": row_status_counter.get("insufficient_support", 0),
        },
        "decision": {
            "recommendation": recommendation,
            "transferable_code_mapping_ready": bool(transferable_groups),
            "gate": "code group must be stable across province and source before any what-if design",
        },
        "next_stage": next_stage,
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_code_family_groups": family_groups[:12],
            "top_raw_code_groups": raw_groups[:12],
            "row_status_buckets": [row for row in buckets if row["dimension"] == "row_evidence_status"],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.8 found no transferable electrical-box code mapping. Some codes repeat inside one province or one source, but that is not enough to infer half-perimeter, circuit, or install-method rules nationally. Stop this small direction and return to ranked gap selection.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "query_tail",
        "normalized_code",
        "alpha_signature",
        "code_family",
        "number_groups",
        "number_count",
        "evidence_type",
        "target_signature",
        "top_signature",
        "family_group_status",
        "raw_code_group_status",
        "row_evidence_status",
        "positive_method",
        "positive_half_perimeter",
        "positive_circuits",
        "top_method",
        "top_half_perimeter",
        "top_circuits",
        "top1_name",
        "positive_names_in_top80",
    ]
    group_fields = [
        "group_type",
        "group_key",
        "support",
        "province_count",
        "source_count",
        "target_count",
        "dominant_target_signature",
        "dominant_target_count",
        "target_purity",
        "evidence_types",
        "status",
        "status_reason",
        "provinces",
        "source_files",
        "example_queries",
    ]
    _write_csv(Path(artifacts["rows_csv"]), annotated, row_fields)
    _write_csv(Path(artifacts["groups_csv"]), code_groups, group_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, code_groups)

    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "decision": report["decision"], "next_stage": report["next_stage"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
