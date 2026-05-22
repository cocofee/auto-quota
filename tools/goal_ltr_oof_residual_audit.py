from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_GATE_CONFIG = PROJECT_ROOT / "data" / "goal_search" / "ltr_safety_gate_oof_v1.json"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_residual_audit.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_residual_audit.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_residual_audit.csv"

PARAM_KEYS = (
    "dn",
    "cable_section",
    "cable_cores",
    "circuits",
    "concrete_grade",
    "thickness",
    "width_height",
)
SEMANTIC_KEYS = ("action_match", "material_match", "connection_match", "install_method_match")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _num(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(row: dict[str, Any], key: str) -> int:
    return int(_num(row, key))


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_gate(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = payload.get("selected_gate") or {}
    name = _clean(selected.get("name"))
    if not name:
        raise ValueError(f"{path} missing selected_gate.name")
    return payload


def _load_details(details_dir: Path, split: str, variant: str) -> list[dict[str, Any]]:
    path = details_dir / f"goal_ltr_oof_safety_gate_details_{split}.jsonl"
    rows = [row for row in _iter_jsonl(path) if row.get("variant") == variant]
    if not rows:
        raise ValueError(f"{path} has no rows for variant {variant}")
    return rows


def _load_feature_groups(data_dir: Path, split: str, target_group_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    path = data_dir / f"ltr_features_{split}.jsonl"
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _iter_jsonl(path):
        group_id = _clean(row.get("group_id"))
        if group_id in target_group_ids:
            groups[group_id].append(row)
    for rows in groups.values():
        rows.sort(key=lambda item: int(item.get("candidate_rank") or 0))
    return groups


def _row_by_rank(rows: list[dict[str, Any]], rank: int | None) -> dict[str, Any]:
    if not rank:
        return {}
    for row in rows:
        if int(row.get("candidate_rank") or 0) == int(rank):
            return row
    return {}


def _positive_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _int(row, "label") == 1]


def _candidate_label(row: dict[str, Any]) -> str:
    if not row:
        return ""
    return f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip()


def _join_unique(values: list[Any]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _clean(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return "; ".join(result)


def _has_numeric_text(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _candidate_param_count(row: dict[str, Any]) -> int:
    return sum(_int(row, f"{key}_candidate_present") for key in PARAM_KEYS)


def _query_param_count(row: dict[str, Any]) -> int:
    return sum(_int(row, f"{key}_query_present") for key in PARAM_KEYS)


def _semantic_match_count(row: dict[str, Any]) -> int:
    return sum(_int(row, key) for key in SEMANTIC_KEYS)


def _param_profile(row: dict[str, Any]) -> str:
    if not row:
        return ""
    parts: list[str] = []
    for key in PARAM_KEYS:
        query_present = _int(row, f"{key}_query_present")
        candidate_present = _int(row, f"{key}_candidate_present")
        exact = _int(row, f"{key}_exact")
        tier = _int(row, f"{key}_tier_up") or _int(row, f"{key}_tier_match")
        gap = _num(row, f"{key}_gap_ratio") or _num(row, f"{key}_gap")
        if query_present or candidate_present or exact or tier or gap:
            parts.append(f"{key}:q{query_present}/c{candidate_present}/e{exact}/t{tier}/g{round(gap, 4)}")
    return "; ".join(parts) if parts else "no_param_signal"


def _semantic_profile(row: dict[str, Any]) -> str:
    if not row:
        return ""
    return "; ".join(f"{key}={_int(row, key)}" for key in SEMANTIC_KEYS)


def _expected_ref(rows: list[dict[str, Any]], detail: dict[str, Any]) -> dict[str, Any]:
    rank = detail.get("baseline_positive_rank") or detail.get("raw_ltr_positive_rank") or detail.get("gated_positive_rank")
    ref = _row_by_rank(rows, int(rank) if rank else None)
    if ref:
        return ref
    positives = _positive_rows(rows)
    return positives[0] if positives else {}


def _classify_loss(detail: dict[str, Any], expected: dict[str, Any], raw_top: dict[str, Any]) -> tuple[str, str]:
    gate_reason = _clean(detail.get("gate_reason"))
    same_family = bool(detail.get("same_family"))
    same_book = bool(detail.get("same_book"))
    raw_query_params = _query_param_count(raw_top)
    raw_candidate_params = _candidate_param_count(raw_top)
    expected_candidate_params = _candidate_param_count(expected)

    if gate_reason == "large_score_margin" and (not same_family or not same_book):
        return (
            "margin_override_cross_family_or_book",
            "Large score margin allowed a cross-family/book override; this is a gate rule risk plus LTR score overconfidence.",
        )
    if gate_reason == "large_score_margin":
        return (
            "margin_override_too_broad",
            "Large score margin allowed an override that was still semantically wrong.",
        )
    if gate_reason == "strict_same_family_book_param":
        if raw_query_params == 0 and raw_candidate_params > 0 and expected_candidate_params > 0:
            return (
                "implicit_param_tier_unprotected",
                "Query has no explicit tier parameter, but both expected and LTR candidates are tiered; strict gate cannot protect default tier choice.",
            )
        if _semantic_match_count(raw_top) == 0:
            return (
                "semantic_subtype_feature_missing",
                "Family/book/param look safe, but subtype/action/material signals are too weak to separate the candidates.",
            )
        return (
            "strict_gate_too_coarse",
            "Strict gate allows same family/book/no-conflict cases that still need finer subtype or default-tier protection.",
        )
    return ("residual_loss_other", "Residual loss does not match the main audited patterns.")


def _classify_blocked_gain(detail: dict[str, Any], raw_top: dict[str, Any], selected_margin: float | None) -> tuple[str, str]:
    same_family = bool(detail.get("same_family"))
    same_book = bool(detail.get("same_book"))
    no_param_conflict = bool(detail.get("no_param_conflict"))
    score_margin = float(detail.get("score_margin") or 0.0)
    shortfall = (selected_margin - score_margin) if selected_margin is not None else 0.0

    if not same_family and not same_book:
        return (
            "family_book_gate_overblocking",
            "Correct LTR candidate crosses both family and book from baseline; safety gate blocks it unless margin is high enough.",
        )
    if not same_family:
        return (
            "family_gate_overblocking",
            "Correct LTR candidate crosses family from baseline; this is often baseline-family error or missing alias coverage.",
        )
    if not same_book:
        return (
            "book_gate_overblocking",
            "Correct LTR candidate crosses book from baseline; this is often province book/chapter bias.",
        )
    if not no_param_conflict:
        return (
            "param_guard_overblocking",
            "Correct LTR candidate is blocked by parameter conflict features; audit whether the conflict is too strict.",
        )
    if selected_margin is not None and 0 <= shortfall <= 0.15:
        return (
            "near_margin_threshold_blocked_gain",
            "Correct LTR candidate is otherwise safe but just below the frozen margin threshold.",
        )
    if _query_param_count(raw_top) == 0 and _candidate_param_count(raw_top) > 0:
        return (
            "implicit_param_or_subtype_blocked_gain",
            "Correct LTR candidate depends on implicit parameter/subtype evidence that the gate does not trust enough.",
        )
    return (
        "margin_threshold_overblocking",
        "Correct LTR candidate is blocked mainly because the score margin is below the frozen threshold.",
    )


def _event_type(detail: dict[str, Any]) -> str | None:
    if bool(detail.get("baseline_hit1")) and not bool(detail.get("gated_hit1")):
        return "residual_loss"
    if (not bool(detail.get("baseline_hit1"))) and bool(detail.get("raw_ltr_hit1")) and not bool(detail.get("gated_hit1")):
        return "blocked_gain"
    return None


def _build_item(
    *,
    split: str,
    detail: dict[str, Any],
    feature_rows: list[dict[str, Any]],
    selected_margin: float | None,
) -> dict[str, Any]:
    event = _event_type(detail)
    baseline_top = _row_by_rank(feature_rows, 1)
    raw_top = _row_by_rank(feature_rows, int(detail.get("raw_ltr_top_original_rank") or 0))
    gated_top = _row_by_rank(feature_rows, int(detail.get("gated_top_original_rank") or 0))
    expected = _expected_ref(feature_rows, detail)
    positives = _positive_rows(feature_rows)

    if event == "residual_loss":
        category, diagnosis = _classify_loss(detail, expected, raw_top)
    elif event == "blocked_gain":
        category, diagnosis = _classify_blocked_gain(detail, raw_top, selected_margin)
    else:
        category, diagnosis = "ignored", "Not a residual loss or blocked gain."

    score_margin = float(detail.get("score_margin") or 0.0)
    return {
        "event_type": event,
        "category": category,
        "diagnosis": diagnosis,
        "split": split,
        "group_index": detail.get("group_index"),
        "group_id": _clean(detail.get("group_id")),
        "sample_id": _clean(detail.get("sample_id")),
        "source_file": _clean(detail.get("source_file")),
        "project_name": _clean(detail.get("project_name")),
        "province": _clean(detail.get("province")),
        "query": _clean(detail.get("query")),
        "expected_ids": _join_unique([row.get("quota_id") for row in positives]) or _clean(detail.get("expected_ids")),
        "expected_families": _join_unique([row.get("candidate_family") for row in positives]),
        "expected_books": _join_unique([row.get("quota_book") for row in positives]),
        "baseline_top": _candidate_label(baseline_top) or _clean(detail.get("baseline_top")),
        "baseline_family": _clean(baseline_top.get("candidate_family") or detail.get("baseline_top_family")),
        "baseline_book": _clean(baseline_top.get("quota_book") or detail.get("baseline_top_book")),
        "raw_ltr_top": _candidate_label(raw_top) or _clean(detail.get("raw_ltr_top")),
        "raw_ltr_family": _clean(raw_top.get("candidate_family") or detail.get("raw_ltr_top_family")),
        "raw_ltr_book": _clean(raw_top.get("quota_book") or detail.get("raw_ltr_top_book")),
        "gated_top": _candidate_label(gated_top) or _clean(detail.get("gated_top")),
        "expected_ref": _candidate_label(expected),
        "expected_ref_family": _clean(expected.get("candidate_family")),
        "expected_ref_book": _clean(expected.get("quota_book")),
        "gate_reason": _clean(detail.get("gate_reason")),
        "gate_allowed": bool(detail.get("gate_allowed")),
        "score_margin": round(score_margin, 8),
        "margin_shortfall": round((selected_margin - score_margin), 8) if selected_margin is not None else None,
        "same_family": bool(detail.get("same_family")),
        "same_book": bool(detail.get("same_book")),
        "no_param_conflict": bool(detail.get("no_param_conflict")),
        "strict_same_family_book_param": bool(detail.get("strict_same_family_book_param")),
        "ltr_param_support": bool(detail.get("ltr_param_support")),
        "raw_query_param_count": _query_param_count(raw_top),
        "raw_candidate_param_count": _candidate_param_count(raw_top),
        "expected_candidate_param_count": _candidate_param_count(expected),
        "raw_param_profile": _param_profile(raw_top),
        "expected_param_profile": _param_profile(expected),
        "raw_semantic_profile": _semantic_profile(raw_top),
        "expected_semantic_profile": _semantic_profile(expected),
        "raw_has_numeric_name": _has_numeric_text(_clean(raw_top.get("quota_name"))),
        "expected_has_numeric_name": _has_numeric_text(_clean(expected.get("quota_name"))),
    }


def _counter_rows(rows: list[dict[str, Any]], key: str, limit: int = 20) -> list[dict[str, Any]]:
    return [{"key": key_value or "<empty>", "count": count} for key_value, count in Counter(row.get(key) or "<empty>" for row in rows).most_common(limit)]


def _build_summary(rows: list[dict[str, Any]], selected_gate: dict[str, Any]) -> dict[str, Any]:
    by_split_event: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        by_split_event[row["split"]][row["event_type"]] += 1
    return {
        "scope": "Goal LTR v1 / stage 2.5 OOF safety gate residual audit; no tuning and no search integration",
        "selected_gate": selected_gate,
        "rows": len(rows),
        "by_split_event": {split: dict(counts) for split, counts in sorted(by_split_event.items())},
        "category_counts": _counter_rows(rows, "category"),
        "event_category_counts": {
            event: _counter_rows([row for row in rows if row["event_type"] == event], "category")
            for event in ("residual_loss", "blocked_gain")
        },
        "split_category_counts": {
            split: _counter_rows([row for row in rows if row["split"] == split], "category")
            for split in sorted({row["split"] for row in rows})
        },
        "family_counts": {
            "residual_loss_raw_family": _counter_rows([row for row in rows if row["event_type"] == "residual_loss"], "raw_ltr_family"),
            "blocked_gain_raw_family": _counter_rows([row for row in rows if row["event_type"] == "blocked_gain"], "raw_ltr_family"),
        },
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "event_type",
        "category",
        "diagnosis",
        "split",
        "group_index",
        "sample_id",
        "province",
        "query",
        "expected_ids",
        "baseline_top",
        "raw_ltr_top",
        "gated_top",
        "expected_ref",
        "gate_reason",
        "score_margin",
        "margin_shortfall",
        "same_family",
        "same_book",
        "no_param_conflict",
        "raw_query_param_count",
        "raw_candidate_param_count",
        "expected_candidate_param_count",
        "raw_param_profile",
        "expected_param_profile",
        "raw_semantic_profile",
        "expected_semantic_profile",
        "source_file",
        "project_name",
        "group_id",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _examples(rows: list[dict[str, Any]], event: str, limit: int = 12) -> list[list[object]]:
    selected = [row for row in rows if row["event_type"] == event]
    return [
        [
            row["split"],
            row["category"],
            row["query"],
            row["baseline_top"],
            row["raw_ltr_top"],
            row["score_margin"],
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    rows = report["rows"]
    lines = [
        "# Goal LTR OOF Residual Audit",
        "",
        "Stage 2.5 audits only the frozen OOF safety gate residuals: remaining Top1 losses and blocked raw-LTR gains on heldout/hard. No tuning, no model training, no search integration.",
        "",
        "## Gate",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["variant", summary["selected_gate"].get("name")],
                ["margin", summary["selected_gate"].get("margin")],
                ["rows", summary["rows"]],
            ]
        ),
        "",
        "## Counts",
        "",
        _md_table(
            [["split", "residual_loss", "blocked_gain"]]
            + [
                [
                    split,
                    counts.get("residual_loss", 0),
                    counts.get("blocked_gain", 0),
                ]
                for split, counts in summary["by_split_event"].items()
            ]
        ),
        "",
        "## Categories",
        "",
        _md_table(
            [["category", "count"]]
            + [[item["key"], item["count"]] for item in summary["category_counts"]]
        ),
        "",
        "## Residual Loss Examples",
        "",
        _md_table([["split", "category", "query", "baseline_top", "raw_ltr_top", "margin"]] + _examples(rows, "residual_loss")),
        "",
        "## Blocked Gain Examples",
        "",
        _md_table([["split", "category", "query", "baseline_top", "raw_ltr_top", "margin"]] + _examples(rows, "blocked_gain")),
        "",
        "## Reading",
        "",
        "- residual_loss means baseline Top1 was correct, but the frozen gate still allowed LTR to override it incorrectly.",
        "- blocked_gain means raw LTR Top1 was correct, but the frozen gate blocked it and kept a wrong baseline Top1.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit frozen OOF safety gate residuals")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    parser.add_argument("--gate-config", default=str(DEFAULT_GATE_CONFIG))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    gate_payload = _load_gate(Path(args.gate_config))
    selected_gate = gate_payload["selected_gate"]
    variant = selected_gate["name"]
    selected_margin = selected_gate.get("margin")
    selected_margin = float(selected_margin) if selected_margin is not None else None

    all_rows: list[dict[str, Any]] = []
    for split in args.splits:
        details = _load_details(Path(args.details_dir), split, variant)
        target_details = [detail for detail in details if _event_type(detail)]
        target_group_ids = {_clean(detail.get("group_id")) for detail in target_details}
        feature_groups = _load_feature_groups(Path(args.data_dir), split, target_group_ids)
        for detail in target_details:
            group_id = _clean(detail.get("group_id"))
            feature_rows = feature_groups.get(group_id, [])
            if not feature_rows:
                raise ValueError(f"missing feature rows for {split} {group_id}")
            all_rows.append(
                _build_item(
                    split=split,
                    detail=detail,
                    feature_rows=feature_rows,
                    selected_margin=selected_margin,
                )
            )

    summary = _build_summary(all_rows, selected_gate)
    report = {
        "summary": summary,
        "rows": all_rows,
    }

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(Path(args.csv_output), all_rows)
    _write_markdown(Path(args.md_output), report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
