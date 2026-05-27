from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_VALIDATION_SUMMARY = AGENT_STATE / "goal_17x_default_off_harness_validation_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_default_off_harness_validation_closure"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _head(report: dict[str, Any], split: str) -> dict[str, Any]:
    return dict(report["headline"][split])


def _family_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report["scorecard"]["all"]:
        slice_name = str(row["slice"])
        if not slice_name.startswith("family:"):
            continue
        generated = int(row["prior_generated_candidates"])
        if generated <= 0 and int(row["delta_top1"]) == 0 and int(row["top1_losses"]) == 0:
            continue
        rows.append(
            {
                "slice": slice_name,
                "groups": row["groups"],
                "delta_top1": row["delta_top1"],
                "delta_top5": row["delta_top5"],
                "delta_top80": row["delta_top80"],
                "top1_wins": row["top1_wins"],
                "top1_losses": row["top1_losses"],
                "prior_generated_candidates": row["prior_generated_candidates"],
                "prior_positive_candidates": row["prior_positive_candidates"],
                "prior_false_candidates": row["prior_false_candidates"],
                "prior_false_candidate_rate": row["prior_false_candidate_rate"],
            }
        )
    rows.sort(key=lambda row: (-int(row["top1_losses"]), -int(row["delta_top1"]), -int(row["prior_false_candidates"])))
    return rows


def _write_markdown(path: Path, closure: dict[str, Any]) -> None:
    heldout = closure["headline"]["heldout"]
    hard = closure["headline"]["hard"]
    all_head = closure["headline"]["all"]
    lines = [
        "# 17.8 Guarded OSS Multifield Default-Off Harness Validation Closure",
        "",
        f"Updated: {closure['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{closure['decision']}**",
        "",
        "## Headline",
        "",
        "| split | groups | Top1 | Top5 | Top20 | Top80 | wins/losses | generated/positive/false | false rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in (("heldout", heldout), ("hard", hard), ("all", all_head)):
        lines.append(
            f"| {label} | {row['groups']} | {row['delta_top1']} | {row['delta_top5']} | {row['delta_top20']} | {row['delta_top80']} | {row['top1_wins']}/{row['top1_losses']} | {row['prior_generated_candidates']}/{row['prior_positive_candidates']}/{row['prior_false_candidates']} | {row['prior_false_candidate_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Stop Conditions",
            "",
            "| check | status | evidence |",
            "|---|---|---|",
        ]
    )
    for row in closure["stop_conditions"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            closure["interpretation"],
            "",
            "## Next Boundary",
            "",
            closure["next_boundary"],
            "",
            "## Anti-Drift",
            "",
            closure["anti_drift_conclusion"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    validation = json.loads(DEFAULT_VALIDATION_SUMMARY.read_text(encoding="utf-8"))
    stop_conditions = [dict(row) for row in validation["stop_conditions"]]
    failed = [row for row in stop_conditions if row["status"] == "fail"]
    all_head = _head(validation, "all")
    heldout = _head(validation, "heldout")
    hard = _head(validation, "hard")
    decision = "validation_stop_do_not_release_return_to_dev_oof_precision_hardening"
    if not failed:
        decision = "validation_pass_ready_for_default_off_release_review"
    interpretation = (
        "The frozen 17.5/17.6 Top3 default-off harness has real heldout/hard lift "
        f"(all Top1 +{all_head['delta_top1']}, Top5 +{all_head['delta_top5']}, Top80 +{all_head['delta_top80']}), "
        "but it fails the release boundary because Top1 losses are non-zero and false candidates still dominate. "
        "Keep the harness as a default-off research artifact; do not default-enable or integrate online."
    )
    next_boundary = (
        "Next should be 17.9 validation-failed precision hardening plan: use heldout/hard only as diagnostic evidence of failure modes, "
        "then redesign guards on dev/OOF. Do not tune thresholds directly on heldout/hard and do not rerun validation until a new frozen candidate exists."
    )
    family_rows = _family_rows(validation)
    closure = {
        "stage": "17.8 guarded OSS multifield default-off harness validation closure",
        "decision": decision,
        "validation_command_note": "Ran the 17.7 locked validation command with OSS_RECALL_INDEX_TOP_K=3 set explicitly to honor the frozen Top3 contract.",
        "headline": {"heldout": heldout, "hard": hard, "all": all_head},
        "stop_conditions": stop_conditions,
        "failed_stop_conditions": failed,
        "family_signal_rows": family_rows,
        "interpretation": interpretation,
        "next_boundary": next_boundary,
        "validation_run": True,
        "trained": False,
        "tuned": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "source_validation_summary": str(DEFAULT_VALIDATION_SUMMARY),
            "source_scorecard_csv": str(AGENT_STATE / "goal_17x_default_off_harness_validation_scorecard.csv"),
            "source_row_audit_csv": str(AGENT_STATE / "goal_17x_default_off_harness_validation_row_audit.csv"),
            "source_stop_conditions_csv": str(AGENT_STATE / "goal_17x_default_off_harness_validation_stop_conditions.csv"),
            "closure_summary_json": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")),
            "closure_summary_md": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")),
            "family_signal_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_family_signal.csv")),
        },
        "anti_drift_conclusion": (
            "17.8 ran only the explicitly authorized frozen default-off heldout/hard validation. "
            "It did not train, tune, change thresholds, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(Path(closure["artifacts"]["closure_summary_json"]), closure)
    _write_markdown(Path(closure["artifacts"]["closure_summary_md"]), closure)
    _write_csv(
        Path(closure["artifacts"]["family_signal_csv"]),
        family_rows,
        [
            "slice",
            "groups",
            "delta_top1",
            "delta_top5",
            "delta_top80",
            "top1_wins",
            "top1_losses",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "prior_false_candidate_rate",
        ],
    )
    print(json.dumps({"summary": closure["artifacts"]["closure_summary_json"], "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
