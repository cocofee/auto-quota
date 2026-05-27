from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
VALIDATION_SUMMARY = AGENT_STATE / "goal_17x_h17b_heldout_hard_validation_summary.json"
VALIDATION_ROW_AUDIT = AGENT_STATE / "goal_17x_h17b_heldout_hard_validation_row_audit.csv"
DEV_OOF_COMPARISON = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_comparison.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_h17b_validation_failed_closure"


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


def _loss_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if _safe_int(row.get("top1_loss")) <= 0:
            continue
        out.append(
            {
                "split": row.get("split", ""),
                "anchor_group_id": row.get("anchor_group_id", ""),
                "bucket": row.get("bucket", ""),
                "query_family": row.get("query_family", ""),
                "expected_ids": row.get("expected_ids", ""),
                "baseline_rank": row.get("baseline_rank", ""),
                "treatment_rank": row.get("treatment_rank", ""),
                "baseline_top1_id": row.get("baseline_top1_id", ""),
                "treatment_top1_id": row.get("treatment_top1_id", ""),
                "prior_generated_candidates": row.get("prior_generated_candidates", ""),
                "prior_positive_candidates": row.get("prior_positive_candidates", ""),
                "prior_false_candidates": row.get("prior_false_candidates", ""),
                "prior_candidate_ids": row.get("prior_candidate_ids", ""),
                "diagnostic_use_only": "yes",
            }
        )
    return out


def _candidate_next_options(dev_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_candidate = {row["candidate"]: row for row in dev_rows}
    h17a = by_candidate.get("H17_A", {})
    h17c = by_candidate.get("H17_C", {})
    return [
        {
            "option_id": "recommended_next",
            "candidate": "H17_A",
            "action": "run_freeze_gate_validation_boundary_only",
            "why": "H17_A was pre-defined before H17_B validation, passed dev/OOF with Top1 loss 0, removes the pipe branch that caused the validation loss, and has lower dev/OOF false rate than H17_B.",
            "dev_oof_evidence": f"Top1/Top5/Top20/Top80={h17a.get('delta_top1')}/{h17a.get('delta_top5')}/{h17a.get('delta_top20')}/{h17a.get('delta_top80')}; loss={h17a.get('top1_losses')}; false_rate={h17a.get('prior_false_candidate_rate')}",
            "boundary": "Do not validate automatically; first do 17.15 H17_A freeze gate using existing dev/OOF artifacts.",
        },
        {
            "option_id": "secondary_safe_branch",
            "candidate": "H17_C",
            "action": "park_for_later_or_compare_after_h17a",
            "why": "H17_C also passed dev/OOF and has lower false rate, but it re-admits support and does not improve Top5 over H17_A in dev/OOF.",
            "dev_oof_evidence": f"Top1/Top5/Top20/Top80={h17c.get('delta_top1')}/{h17c.get('delta_top5')}/{h17c.get('delta_top20')}/{h17c.get('delta_top80')}; loss={h17c.get('top1_losses')}; false_rate={h17c.get('prior_false_candidate_rate')}",
            "boundary": "Do not jump to validation unless H17_A is rejected at a freeze gate.",
        },
        {
            "option_id": "blocked_current_candidate",
            "candidate": "H17_B",
            "action": "stop_do_not_release",
            "why": "H17_B validation failed Top1 loss and false-candidate dominance; pipe strict gate is still too noisy.",
            "dev_oof_evidence": "Dev/OOF passed, but heldout/hard validation overruled release eligibility.",
            "boundary": "Do not tune pipe thresholds from the validation loss row.",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["validation_headline"]["all"]
    lines = [
        "# 17.14 H17_B Validation-Failed Closure / Strategy Return",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Failure Summary",
        "",
        f"- all Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`.",
        f"- Top1 wins/losses: `{h['top1_wins']}/{h['top1_losses']}`.",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`.",
        f"- false rate: `{h['prior_false_candidate_rate']}`.",
        "",
        "## Why H17_B Stops",
        "",
    ]
    lines.extend(f"- {item}" for item in report["failure_reasons"])
    lines.extend(["", "## Loss Rows", "", "| split | anchor | family | baseline->treatment | candidates |", "|---|---|---|---|---|"])
    for row in report["top1_loss_rows"]:
        lines.append(
            f"| {row['split']} | {row['anchor_group_id']} | {row['query_family']} | {row['baseline_rank']}->{row['treatment_rank']} | {row['prior_candidate_ids']} |"
        )
    lines.extend(["", "## Next Options", "", "| option | candidate | action | why |", "|---|---|---|---|"])
    for row in report["next_options"]:
        lines.append(f"| {row['option_id']} | {row['candidate']} | {row['action']} | {row['why']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    validation = _read_json(VALIDATION_SUMMARY)
    row_audit = _read_csv(VALIDATION_ROW_AUDIT)
    dev_rows = _read_csv(DEV_OOF_COMPARISON)
    loss_rows = _loss_rows(row_audit)
    next_options = _candidate_next_options(dev_rows)
    failure_reasons = [
        "H17_B has real lift, but release requires zero Top1 loss; validation has Top1 loss=1.",
        "The loss is in pipe: baseline expected rank 1 moved to rank 3 after false pipe candidates were injected.",
        "False candidates still dominate: 409 false vs 61 positive, false rate 0.870213.",
        "Pipe branch remains the unsafe branch: pipe false rate 0.930556 and pipe_top1_losses=1.",
        "Heldout/hard may diagnose failure but must not be used to tune a new pipe threshold directly.",
    ]
    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    loss_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_top1_loss_rows.csv")
    options_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_next_options.csv")
    report = {
        "stage": "17.14 H17_B validation-failed closure / strategy return",
        "decision": "stop_h17b_return_to_h17a_freeze_gate",
        "validation_headline": validation["headline"],
        "failed_stop_conditions": [row for row in validation["stop_conditions"] if row["status"] == "fail"],
        "failure_reasons": failure_reasons,
        "top1_loss_rows": loss_rows,
        "next_options": next_options,
        "next_boundary": (
            "Next should be 17.15 H17_A freeze gate / validation boundary. "
            "Use only existing dev/OOF H17_A artifacts for the freeze decision; do not run validation and do not tune pipe thresholds from H17_B validation."
        ),
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used_for_new_selection": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "top1_loss_rows_csv": str(loss_csv),
            "next_options_csv": str(options_csv),
        },
        "anti_drift_conclusion": (
            "17.14 only closed the already completed H17_B validation failure and selected the next read-only gate. "
            "It did not run validation, train, tune from heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(loss_csv, loss_rows, ["split", "anchor_group_id", "bucket", "query_family", "expected_ids", "baseline_rank", "treatment_rank", "baseline_top1_id", "treatment_top1_id", "prior_generated_candidates", "prior_positive_candidates", "prior_false_candidates", "prior_candidate_ids", "diagnostic_use_only"])
    _write_csv(options_csv, next_options, ["option_id", "candidate", "action", "why", "dev_oof_evidence", "boundary"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
