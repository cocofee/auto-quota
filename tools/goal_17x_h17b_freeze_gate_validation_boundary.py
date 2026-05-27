from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
H17B_SUMMARY = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_b_pipe_strict_evidence_gate_summary.json"
H17B_SCORECARD = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_b_pipe_strict_evidence_gate_scorecard.csv"
H17B_ROW_AUDIT = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_b_pipe_strict_evidence_gate_row_audit.csv"
COMPARISON = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_comparison.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_h17b_freeze_gate_validation_boundary"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _headline(summary: dict[str, Any]) -> dict[str, Any]:
    return dict(summary["headline"])


def _family_rows(scorecard: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for row in scorecard:
        slice_name = row.get("slice", "")
        if not slice_name.startswith("family:"):
            continue
        generated = _safe_int(row.get("prior_generated_candidates"))
        if generated == 0 and _safe_int(row.get("delta_top1")) == 0 and _safe_int(row.get("delta_top5")) == 0:
            continue
        rows.append(
            {
                "family": slice_name.replace("family:", ""),
                "groups": _safe_int(row.get("groups")),
                "delta_top1": _safe_int(row.get("delta_top1")),
                "delta_top5": _safe_int(row.get("delta_top5")),
                "delta_top20": _safe_int(row.get("delta_top20")),
                "delta_top80": _safe_int(row.get("delta_top80")),
                "top1_losses": _safe_int(row.get("top1_losses")),
                "generated": generated,
                "positive": _safe_int(row.get("prior_positive_candidates")),
                "false": _safe_int(row.get("prior_false_candidates")),
                "false_rate": _safe_float(row.get("prior_false_candidate_rate")),
                "risk_note": "watch" if _safe_float(row.get("prior_false_candidate_rate")) >= 0.9 else "ok",
            }
        )
    return rows


def _row_audit_checks(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    source_counts = Counter(row.get("source_file", "") for row in rows)
    family_counts = Counter(row.get("query_family", "") for row in rows)
    top1_losses = [row for row in rows if _safe_int(row.get("top1_loss")) > 0]
    top1_wins = [row for row in rows if _safe_int(row.get("top1_win")) > 0]
    top80_gains = [row for row in rows if _safe_int(row.get("top80_gain")) > 0]
    false_only_rows = [
        row
        for row in rows
        if _safe_int(row.get("prior_generated_candidates")) > 0 and _safe_int(row.get("prior_positive_candidates")) == 0
    ]
    return [
        {
            "check": "row_count",
            "status": "pass" if len(rows) == 29 else "watch",
            "evidence": f"rows={len(rows)}",
        },
        {
            "check": "top1_loss_rows",
            "status": "pass" if not top1_losses else "fail",
            "evidence": f"top1_loss_rows={len(top1_losses)}",
        },
        {
            "check": "top1_win_rows",
            "status": "pass" if len(top1_wins) >= 3 else "watch",
            "evidence": f"top1_win_rows={len(top1_wins)}",
        },
        {
            "check": "top80_gain_rows",
            "status": "pass" if len(top80_gains) >= 1 else "watch",
            "evidence": f"top80_gain_rows={len(top80_gains)}",
        },
        {
            "check": "false_only_rows",
            "status": "watch",
            "evidence": f"false_only_rows={len(false_only_rows)}",
        },
        {
            "check": "source_file_concentration",
            "status": "watch",
            "evidence": "; ".join(f"{key or '<empty>'}={value}" for key, value in source_counts.most_common()),
        },
        {
            "check": "family_coverage",
            "status": "pass" if len([key for key in family_counts if key]) >= 4 else "watch",
            "evidence": "; ".join(f"{key or '<empty>'}={value}" for key, value in family_counts.most_common()),
        },
    ]


def _gate_checks(summary: dict[str, Any], scorecard: list[dict[str, str]], row_checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    h = _headline(summary)
    failed_stop = [row for row in summary["stop_conditions"] if row.get("status") != "pass"]
    false_rate = _safe_float(h.get("prior_false_candidate_rate"))
    pipe = next((row for row in scorecard if row.get("slice") == "family:pipe"), {})
    return [
        {
            "check": "h17b_stop_conditions",
            "status": "pass" if not failed_stop else "fail",
            "evidence": f"failed={len(failed_stop)}",
        },
        {
            "check": "top1_loss_budget",
            "status": "pass" if _safe_int(h.get("top1_losses")) == 0 else "fail",
            "evidence": f"top1_losses={h.get('top1_losses')}",
        },
        {
            "check": "positive_movement",
            "status": "pass" if _safe_int(h.get("delta_top1")) > 0 and _safe_int(h.get("delta_top5")) > 0 else "fail",
            "evidence": f"delta_top1={h.get('delta_top1')}; delta_top5={h.get('delta_top5')}",
        },
        {
            "check": "false_rate_boundary",
            "status": "watch" if false_rate >= 0.84 else "pass",
            "evidence": f"false_rate={false_rate}; margin_to_0.85={round(0.85 - false_rate, 6)}",
        },
        {
            "check": "pipe_branch_risk",
            "status": "watch",
            "evidence": f"pipe_delta_top1={pipe.get('delta_top1')}; pipe_delta_top5={pipe.get('delta_top5')}; pipe_false_rate={pipe.get('prior_false_candidate_rate')}",
        },
        {
            "check": "default_off_boundary",
            "status": "pass" if not summary.get("default_enable_allowed") and not summary.get("online_integration_allowed") else "fail",
            "evidence": "default_enable_allowed=false; online_integration_allowed=false",
        },
        {
            "check": "heldout_hard_boundary",
            "status": "pass" if not summary.get("heldout_hard_used") else "fail",
            "evidence": f"heldout_hard_used={summary.get('heldout_hard_used')}",
        },
        {
            "check": "source_concentration",
            "status": "watch" if any(row["check"] == "source_file_concentration" for row in row_checks) else "pass",
            "evidence": next(row["evidence"] for row in row_checks if row["check"] == "source_file_concentration"),
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    lines = [
        "# 17.12 H17_B Freeze Gate / Validation Boundary",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## H17_B Scorecard",
        "",
        f"- Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`.",
        f"- Top1 wins/losses: `{h['top1_wins']}/{h['top1_losses']}`.",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`.",
        f"- false rate: `{h['prior_false_candidate_rate']}`.",
        "",
        "## Gate Checks",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    for row in report["gate_checks"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    lines.extend(["", "## Family Slices", "", "| family | Top1 | Top5 | loss | generated/positive/false | false rate | risk |", "|---|---:|---:|---:|---|---:|---|"])
    for row in report["family_slices"]:
        lines.append(
            f"| {row['family']} | {row['delta_top1']} | {row['delta_top5']} | {row['top1_losses']} | "
            f"{row['generated']}/{row['positive']}/{row['false']} | {row['false_rate']} | {row['risk_note']} |"
        )
    lines.extend(
        [
            "",
            "## Validation Boundary",
            "",
            report["validation_boundary"],
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    summary = _read_json(H17B_SUMMARY)
    scorecard = _read_csv(H17B_SCORECARD)
    rows = _read_csv(H17B_ROW_AUDIT)
    comparison = _read_csv(COMPARISON)
    row_checks = _row_audit_checks(rows)
    gate_checks = _gate_checks(summary, scorecard, row_checks)
    family_slices = _family_rows(scorecard)
    blocking_failures = [row for row in gate_checks if row["status"] == "fail"]
    watch_items = [row for row in gate_checks if row["status"] == "watch"]
    decision = "freeze_h17b_for_validation_request_boundary" if not blocking_failures else "do_not_freeze_h17b"
    validation_boundary = (
        "H17_B may be frozen as a validation candidate, but validation is not authorized by this stage. "
        "Next step should request explicit heldout/hard validation go for the frozen H17_B contract only. "
        "If validation is authorized, it must use the fixed H17_B core families and strict pipe gate, must not tune from heldout/hard, and must stop on any Top1 loss, false-candidate dominance, or default-off boundary break."
        if decision.startswith("freeze")
        else "Do not request validation until failed gates are resolved on dev/OOF."
    )
    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    gates_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_gate_checks.csv")
    family_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_family_slices.csv")
    row_checks_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_row_audit_checks.csv")
    report = {
        "stage": "17.12 H17_B freeze gate / validation boundary",
        "decision": decision,
        "headline": _headline(summary),
        "comparison_rows": comparison,
        "gate_checks": gate_checks,
        "row_audit_checks": row_checks,
        "family_slices": family_slices,
        "watch_items": watch_items,
        "validation_boundary": validation_boundary,
        "next_boundary": "17.13 explicit validation go/no-go for frozen H17_B. Default is do_not_validate without explicit go.",
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "gate_checks_csv": str(gates_csv),
            "family_slices_csv": str(family_csv),
            "row_audit_checks_csv": str(row_checks_csv),
        },
        "anti_drift_conclusion": (
            "17.12 only reviewed existing H17_B dev/OOF artifacts and defined the validation boundary. "
            "It did not run heldout/hard validation, train, tune, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(gates_csv, gate_checks, ["check", "status", "evidence"])
    _write_csv(family_csv, family_slices, ["family", "groups", "delta_top1", "delta_top5", "delta_top20", "delta_top80", "top1_losses", "generated", "positive", "false", "false_rate", "risk_note"])
    _write_csv(row_checks_csv, row_checks, ["check", "status", "evidence"])
    print(json.dumps({"summary": str(summary_json), "decision": decision, "watch_items": watch_items}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
