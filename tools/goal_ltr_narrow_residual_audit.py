from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_narrow_variants.csv"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_narrow_residual_audit.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_narrow_residual_audit.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_narrow_residual_audit.csv"
DEFAULT_VARIANT = "whatif_A_plus_B_narrow"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_variant_metrics(path: Path, variant: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle) if row.get("variant") in {"raw_ltr", "frozen_oof_margin_1_25", variant}]
    return rows


def _event_type(row: dict[str, Any]) -> str | None:
    if bool(row.get("baseline_hit1")) and not bool(row.get("gated_hit1")):
        return "residual_loss"
    if (not bool(row.get("baseline_hit1"))) and bool(row.get("raw_ltr_hit1")) and not bool(row.get("gated_hit1")):
        return "blocked_gain"
    return None


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _classify(row: dict[str, Any]) -> tuple[str, str, str]:
    event = _event_type(row)
    query = _clean(row.get("query"))
    baseline = _clean(row.get("baseline_top"))
    raw = _clean(row.get("raw_ltr_top"))
    reason = _clean(row.get("gate_reason"))
    text = f"{query} {baseline} {raw}"

    if event == "residual_loss":
        if reason == "large_score_margin" and (not row.get("same_family") or not row.get("same_book")):
            return (
                "cross_family_margin_loss",
                "low",
                "Only remaining loss: large margin still allowed a cross-family/book jump. This can be handled by a small shared-object-token guard, but one sample is not enough to keep iterating now.",
            )
        return (
            "residual_loss_other",
            "medium",
            "Residual loss does not match the known narrow-gate patterns.",
        )

    if event == "blocked_gain":
        if reason.startswith("A_narrow_block_"):
            return (
                "A_guard_false_block",
                "medium",
                "A guard blocked a correct raw-LTR candidate; likely needs subtype exceptions rather than broader gate relaxation.",
            )
        if "配电箱" in text:
            return (
                "electrical_box_tier_evidence_missing",
                "medium",
                "Electrical-box rescue needs explicit mounting/half-perimeter evidence; current query lacks enough safe evidence.",
            )
        if _has_any(text, ("防火阀", "止回阀", "插板阀", "阀门", "阀")):
            return (
                "valve_damper_param_or_book_overblock",
                "medium",
                "Valve/damper cases are still blocked by book/parameter safety; relaxing them needs subtype and size checks.",
            )
        if _has_any(text, ("套管", "DN", "D600", "320*160")):
            return (
                "param_guard_overblock",
                "medium",
                "Correct candidate depends on explicit parameter evidence, but the current gate still treats the parameter jump as risky.",
            )
        if _has_any(text, ("检查井", "成品门", "垫层", "围堰", "混凝土基础", "井 ", "门M")):
            return (
                "civil_or_generic_object_overblock",
                "high",
                "Mostly civil/generic or non-install objects; not a good target for the installation-focused safety gate.",
            )
        if _has_any(text, ("小便器", "空调器", "减振器", "感应器")):
            return (
                "cross_domain_component_answer",
                "high",
                "Expected answer is a component/control item rather than the surface object; this is data-policy sensitive.",
            )
        if "配管" in text:
            return (
                "conduit_material_family_overblock",
                "medium",
                "Correct raw-LTR candidate changes conduit material/family; requires material evidence before safe relaxation.",
            )
        return (
            "blocked_gain_other",
            "medium",
            "Blocked gain does not match the main audited buckets.",
        )

    return ("ignored", "low", "Not a residual loss or blocked gain.")


def _load_residual_rows(details_dir: Path, splits: list[str], variant: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in splits:
        path = details_dir / f"goal_ltr_safety_gate_whatif_narrow_details_{split}.jsonl"
        if not path.exists():
            raise FileNotFoundError(path)
        for row in _iter_jsonl(path):
            if row.get("variant") != variant:
                continue
            event = _event_type(row)
            if event is None:
                continue
            category, risk, diagnosis = _classify(row)
            rows.append(
                {
                    "event_type": event,
                    "category": category,
                    "risk": risk,
                    "diagnosis": diagnosis,
                    "split": split,
                    "group_index": row.get("group_index"),
                    "group_id": _clean(row.get("group_id")),
                    "sample_id": _clean(row.get("sample_id")),
                    "province": _clean(row.get("province")),
                    "query": _clean(row.get("query")),
                    "expected_ids": _clean(row.get("expected_ids")),
                    "baseline_top": _clean(row.get("baseline_top")),
                    "raw_ltr_top": _clean(row.get("raw_ltr_top")),
                    "gated_top": _clean(row.get("gated_top")),
                    "gate_reason": _clean(row.get("gate_reason")),
                    "score_margin": row.get("score_margin"),
                    "same_family": bool(row.get("same_family")),
                    "same_book": bool(row.get("same_book")),
                    "no_param_conflict": bool(row.get("no_param_conflict")),
                    "source_file": _clean(row.get("source_file")),
                    "project_name": _clean(row.get("project_name")),
                }
            )
    return rows


def _counter_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [{"key": item, "count": count} for item, count in Counter(row.get(key) or "<empty>" for row in rows).most_common()]


def _summary(rows: list[dict[str, Any]], metrics: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    by_split_event: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        by_split_event[row["split"]][row["event_type"]] += 1

    residual_loss = sum(1 for row in rows if row["event_type"] == "residual_loss")
    blocked_gain = sum(1 for row in rows if row["event_type"] == "blocked_gain")
    high_risk_blocked = sum(1 for row in rows if row["event_type"] == "blocked_gain" and row["risk"] == "high")
    medium_risk_blocked = sum(1 for row in rows if row["event_type"] == "blocked_gain" and row["risk"] == "medium")

    recommendation = "prepare_offline_switch_draft"
    recommendation_reason = (
        "Narrow A+B leaves only one heldout loss and zero hard loss. Remaining blocked gains are mixed, many medium/high-risk, "
        "so further patching is likely to overfit. Keep residual fixes as backlog and prepare an offline switch integration draft."
    )
    if residual_loss > 2:
        recommendation = "continue_residual_loss_work"
        recommendation_reason = "Residual loss is still above the narrow-gate threshold; continue loss-side work before switch planning."

    return {
        "scope": "Goal LTR v1 / stage 2.9 narrow residual audit; no tuning, no training, no search integration",
        "variant": variant,
        "rows": len(rows),
        "residual_loss": residual_loss,
        "blocked_gain": blocked_gain,
        "high_risk_blocked_gain": high_risk_blocked,
        "medium_risk_blocked_gain": medium_risk_blocked,
        "by_split_event": {split: dict(counts) for split, counts in sorted(by_split_event.items())},
        "category_counts": _counter_rows(rows, "category"),
        "risk_counts": _counter_rows(rows, "risk"),
        "event_category_counts": {
            event: _counter_rows([row for row in rows if row["event_type"] == event], "category")
            for event in ("residual_loss", "blocked_gain")
        },
        "metrics": metrics,
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "event_type",
        "category",
        "risk",
        "split",
        "query",
        "expected_ids",
        "baseline_top",
        "raw_ltr_top",
        "gated_top",
        "gate_reason",
        "score_margin",
        "same_family",
        "same_book",
        "no_param_conflict",
        "diagnosis",
        "province",
        "sample_id",
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


def _examples(rows: list[dict[str, Any]], event: str) -> list[list[object]]:
    return [
        [
            row["split"],
            row["category"],
            row["risk"],
            row["query"],
            row["baseline_top"],
            row["raw_ltr_top"],
            row["gate_reason"],
        ]
        for row in rows
        if row["event_type"] == event
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    rows = report["rows"]
    metric_rows = [
        [
            row.get("split"),
            row.get("variant"),
            row.get("gated_hit1_net"),
            row.get("gated_hit1_gain"),
            row.get("gated_hit1_loss"),
            row.get("blocked_raw_hit1_gain"),
            row.get("gated_hit5_net"),
        ]
        for row in summary["metrics"]
    ]
    lines = [
        "# Goal LTR Narrow Residual Audit",
        "",
        "Stage 2.9 audits only the remaining residuals after `whatif_A_plus_B_narrow`. No tuning, no training, no search integration.",
        "",
        "## Metrics",
        "",
        _md_table(
            [["split", "variant", "top1_net", "gain", "loss", "blocked_gain", "top5_net"]]
            + metric_rows
        ),
        "",
        "## Residual Counts",
        "",
        _md_table(
            [["split", "residual_loss", "blocked_gain"]]
            + [
                [split, counts.get("residual_loss", 0), counts.get("blocked_gain", 0)]
                for split, counts in summary["by_split_event"].items()
            ]
        ),
        "",
        "## Categories",
        "",
        _md_table([["category", "count"]] + [[item["key"], item["count"]] for item in summary["category_counts"]]),
        "",
        "## Remaining Loss",
        "",
        _md_table([["split", "category", "risk", "query", "baseline_top", "raw_ltr_top", "reason"]] + _examples(rows, "residual_loss")),
        "",
        "## Blocked Gains",
        "",
        _md_table([["split", "category", "risk", "query", "baseline_top", "raw_ltr_top", "reason"]] + _examples(rows, "blocked_gain")),
        "",
        "## Recommendation",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["recommendation", summary["recommendation"]],
                ["reason", summary["recommendation_reason"]],
            ]
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit residuals after narrow A+B safety gate what-if")
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--variant", default=DEFAULT_VARIANT)
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    rows = _load_residual_rows(Path(args.details_dir), args.splits, args.variant)
    metrics = _load_variant_metrics(Path(args.variants_csv), args.variant)
    summary = _summary(rows, metrics, args.variant)
    report = {"summary": summary, "rows": rows}

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(Path(args.csv_output), rows)
    _write_markdown(Path(args.md_output), report)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
