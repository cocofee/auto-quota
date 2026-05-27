from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
H17A_SUMMARY = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_summary.json"
H17A_SCORECARD = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_scorecard.csv"
H17A_ROW_AUDIT = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_row_audit.csv"
H17B_CLOSURE = AGENT_STATE / "goal_17x_h17b_validation_failed_closure_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_h17a_freeze_gate_validation_boundary"


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


def _family_slices(scorecard: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for row in scorecard:
        name = row.get("slice", "")
        if not name.startswith("family:"):
            continue
        generated = _safe_int(row.get("prior_generated_candidates"))
        if generated == 0 and _safe_int(row.get("delta_top1")) == 0 and _safe_int(row.get("delta_top5")) == 0:
            continue
        out.append(
            {
                "family": name.replace("family:", ""),
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
                "risk_note": "watch" if _safe_float(row.get("prior_false_candidate_rate")) >= 0.85 else "ok",
            }
        )
    return out


def _row_checks(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    source_counts = Counter(row.get("source_file", "") for row in rows)
    family_counts = Counter(row.get("query_family", "") for row in rows)
    generated_pipe_or_support = [
        row
        for row in rows
        if row.get("query_family") in {"pipe", "support"} and _safe_int(row.get("prior_generated_candidates")) > 0
    ]
    return [
        {"check": "row_count", "status": "pass" if len(rows) == 29 else "watch", "evidence": f"rows={len(rows)}"},
        {"check": "top1_loss_rows", "status": "pass" if not any(_safe_int(row.get("top1_loss")) for row in rows) else "fail", "evidence": f"top1_loss_rows={sum(_safe_int(row.get('top1_loss')) for row in rows)}"},
        {"check": "pipe_support_veto", "status": "pass" if not generated_pipe_or_support else "fail", "evidence": f"pipe_or_support_rows_with_candidates={len(generated_pipe_or_support)}"},
        {"check": "source_file_concentration", "status": "watch", "evidence": "; ".join(f"{key or '<empty>'}={value}" for key, value in source_counts.most_common())},
        {"check": "family_coverage", "status": "pass", "evidence": "; ".join(f"{key or '<empty>'}={value}" for key, value in family_counts.most_common())},
    ]


def _gate_checks(summary: dict[str, Any], row_checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    h = summary["headline"]
    failed_stop = [row for row in summary["stop_conditions"] if row["status"] != "pass"]
    return [
        {"check": "h17a_stop_conditions", "status": "pass" if not failed_stop else "fail", "evidence": f"failed={len(failed_stop)}"},
        {"check": "top1_loss_budget", "status": "pass" if _safe_int(h.get("top1_losses")) == 0 else "fail", "evidence": f"top1_losses={h.get('top1_losses')}"},
        {"check": "positive_movement", "status": "pass" if _safe_int(h.get("delta_top1")) > 0 and _safe_int(h.get("delta_top5")) > 0 else "fail", "evidence": f"delta_top1={h.get('delta_top1')}; delta_top5={h.get('delta_top5')}"},
        {"check": "false_rate_boundary", "status": "pass" if _safe_float(h.get("prior_false_candidate_rate")) < 0.85 else "fail", "evidence": f"false_rate={h.get('prior_false_candidate_rate')}"},
        {"check": "pipe_support_veto", "status": next(row["status"] for row in row_checks if row["check"] == "pipe_support_veto"), "evidence": next(row["evidence"] for row in row_checks if row["check"] == "pipe_support_veto")},
        {"check": "default_off_boundary", "status": "pass" if not summary.get("default_enable_allowed") and not summary.get("online_integration_allowed") else "fail", "evidence": "default_enable_allowed=false; online_integration_allowed=false"},
        {"check": "heldout_hard_boundary", "status": "pass" if not summary.get("heldout_hard_used") else "fail", "evidence": f"heldout_hard_used={summary.get('heldout_hard_used')}"},
        {"check": "source_concentration", "status": "watch", "evidence": next(row["evidence"] for row in row_checks if row["check"] == "source_file_concentration")},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    lines = [
        "# 17.15 H17_A Freeze Gate / Validation Boundary",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## H17_A Scorecard",
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
    lines.extend(["", "## Validation Boundary", "", report["validation_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    summary = _read_json(H17A_SUMMARY)
    scorecard = _read_csv(H17A_SCORECARD)
    row_audit = _read_csv(H17A_ROW_AUDIT)
    closure = _read_json(H17B_CLOSURE)
    row_checks = _row_checks(row_audit)
    gate_checks = _gate_checks(summary, row_checks)
    family_slices = _family_slices(scorecard)
    blocking_failures = [row for row in gate_checks if row["status"] == "fail"]
    decision = "freeze_h17a_for_validation_request_boundary" if not blocking_failures else "do_not_freeze_h17a"
    validation_boundary = (
        "H17_A may be frozen as the next validation candidate, but validation is not authorized by this stage. "
        "The future validation command must use only the frozen H17_A contract: TopK=3, support>=2, source_family>=1, overlap>=2, broad mode, and core families concrete,pump,rebar. "
        "It must not include pipe or support, must not tune from H17_B heldout/hard failure, and must stop on any Top1 loss or false-candidate dominance."
        if decision.startswith("freeze")
        else "Do not request H17_A validation until failed freeze gates are resolved."
    )
    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    gates_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_gate_checks.csv")
    family_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_family_slices.csv")
    row_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_row_audit_checks.csv")
    report = {
        "stage": "17.15 H17_A freeze gate / validation boundary",
        "decision": decision,
        "headline": summary["headline"],
        "h17b_closure_decision": closure["decision"],
        "gate_checks": gate_checks,
        "row_audit_checks": row_checks,
        "family_slices": family_slices,
        "validation_boundary": validation_boundary,
        "next_boundary": "17.16 explicit heldout/hard validation go/no-go for frozen H17_A. Default is do_not_validate without explicit go.",
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
            "row_audit_checks_csv": str(row_csv),
        },
        "anti_drift_conclusion": (
            "17.15 only reviewed existing H17_A dev/OOF artifacts and defined the validation boundary. "
            "It did not run heldout/hard validation, train, tune from H17_B validation, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(gates_csv, gate_checks, ["check", "status", "evidence"])
    _write_csv(family_csv, family_slices, ["family", "groups", "delta_top1", "delta_top5", "delta_top20", "delta_top80", "top1_losses", "generated", "positive", "false", "false_rate", "risk_note"])
    _write_csv(row_csv, row_checks, ["check", "status", "evidence"])
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
