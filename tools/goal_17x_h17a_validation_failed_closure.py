from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
VALIDATION_SUMMARY = AGENT_STATE / "goal_17x_h17a_heldout_hard_validation_summary.json"
VALIDATION_STOP = AGENT_STATE / "goal_17x_h17a_heldout_hard_validation_stop_conditions.csv"
VALIDATION_FAMILY = AGENT_STATE / "goal_17x_h17a_heldout_hard_validation_family_signal.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_h17a_validation_failed_closure"


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


def _evidence_rows(validation: dict[str, Any], stop_rows: list[dict[str, str]], family_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    all_head = validation["headline"]["all"]
    failed = [row["check"] for row in stop_rows if row.get("status") == "fail"]
    return [
        {
            "evidence_id": "real_validation_lift",
            "status": "accepted",
            "evidence": f"all Top1/Top5/Top20/Top80={all_head['delta_top1']}/{all_head['delta_top5']}/{all_head['delta_top20']}/{all_head['delta_top80']}",
            "meaning": "OSS recall direction is real enough to keep exploring.",
        },
        {
            "evidence_id": "rank1_safety",
            "status": "accepted",
            "evidence": f"Top1 wins/losses={all_head['top1_wins']}/{all_head['top1_losses']}",
            "meaning": "The H17_A family veto removed the H17_B Top1-loss issue.",
        },
        {
            "evidence_id": "release_blocker",
            "status": "blocking",
            "evidence": f"failed gates={','.join(failed)}; false={all_head['prior_false_candidates']}, positive={all_head['prior_positive_candidates']}",
            "meaning": "Do not release/default-enable because false candidates still dominate.",
        },
        {
            "evidence_id": "family_noise",
            "status": "diagnostic_only",
            "evidence": "; ".join(
                f"{row['slice']} Top1={row['delta_top1']} false_rate={row['prior_false_candidate_rate']}"
                for row in family_rows
                if _safe_int(row.get("prior_generated_candidates")) > 0
            ),
            "meaning": "Use only as a high-level diagnostic; do not tune thresholds on heldout/hard rows.",
        },
    ]


def _next_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "recommended_next",
            "stage": "17.19 dev/OOF false-candidate precision guard redesign scope",
            "action": "return_to_dev_oof_design",
            "why": "H17_A has validation lift and zero Top1 loss, but release is blocked by false-candidate dominance.",
            "boundary": "Use dev/OOF only for design and selection. Heldout/hard results are closure evidence only, not tuning data.",
        },
        {
            "option_id": "blocked_release",
            "stage": "release/default-on",
            "action": "do_not_release",
            "why": "false candidates exceed positives, so default-on would inject too much candidate noise.",
            "boundary": "No online integration, default enablement, or GoalSearcher default change from 17.17.",
        },
        {
            "option_id": "blocked_hard_tuning",
            "stage": "heldout/hard parameter tuning",
            "action": "do_not_tune",
            "why": "Heldout/hard validated the frozen candidate; using it to tune the next guard would contaminate validation.",
            "boundary": "Any new precision guard must be specified and selected on dev/OOF, then frozen before future validation.",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {"action": "release_or_default_enable_h17a", "blocked": True, "reason": "false_candidate_dominance failed"},
        {"action": "claim_h17a_release_ready", "blocked": True, "reason": "validation lift exists but release gate failed"},
        {"action": "tune_thresholds_from_heldout_hard", "blocked": True, "reason": "would contaminate validation"},
        {"action": "reintroduce_pipe_or_support_from_validation", "blocked": True, "reason": "H17_A frozen validation vetoed those families; future branch must start on dev/OOF"},
        {"action": "change_goal_searcher_defaults", "blocked": True, "reason": "17.18 is closure only"},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["validation_headline"]["all"]
    lines = [
        "# 17.18 H17_A Validation-Failed Closure / Strategy Return",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Closure Summary",
        "",
        f"- all Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`.",
        f"- Top1 wins/losses: `{h['top1_wins']}/{h['top1_losses']}`.",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`.",
        f"- false rate: `{h['prior_false_candidate_rate']}`.",
        "",
        "## Meaning",
        "",
        "H17_A is a real validation lift signal, not a release candidate. Keep the OSS recall direction alive, but stop H17_A release/default-on because false candidates still dominate.",
        "",
        "## Evidence",
        "",
        "| evidence | status | meaning |",
        "|---|---|---|",
    ]
    for row in report["evidence_rows"]:
        lines.append(f"| {row['evidence_id']} | {row['status']} | {row['meaning']} |")
    lines.extend(["", "## Next Options", "", "| option | stage | action | why |", "|---|---|---|---|"])
    for row in report["next_options"]:
        lines.append(f"| {row['option_id']} | {row['stage']} | {row['action']} | {row['why']} |")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    validation = _read_json(VALIDATION_SUMMARY)
    stop_rows = _read_csv(VALIDATION_STOP)
    family_rows = _read_csv(VALIDATION_FAMILY)
    all_head = validation["headline"]["all"]
    failed = [row["check"] for row in stop_rows if row.get("status") == "fail"]
    decision = "stop_release_return_to_dev_oof_precision_guard_redesign"
    evidence_rows = _evidence_rows(validation, stop_rows, family_rows)
    next_options = _next_options()
    blocked_actions = _blocked_actions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    evidence_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_evidence.csv")
    next_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_next_options.csv")
    blocked_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv")
    report = {
        "stage": "17.18 H17_A validation-failed closure / strategy return",
        "decision": decision,
        "validation_decision": validation["decision"],
        "validation_headline": validation["headline"],
        "failed_gates": failed,
        "release_allowed": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "heldout_hard_tuning_allowed": False,
        "real_signal_preserved": _safe_int(all_head.get("delta_top1")) > 0 and _safe_int(all_head.get("top1_losses")) == 0,
        "release_blocker": "false_candidate_dominance" if "false_candidate_dominance" in failed else "",
        "evidence_rows": evidence_rows,
        "next_options": next_options,
        "blocked_actions": blocked_actions,
        "next_boundary": (
            "17.19 should define a dev/OOF-only false-candidate precision guard redesign scope. "
            "It may use H17_A validation only as closure evidence that the direction has lift, not as tuning data."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "evidence_csv": str(evidence_csv),
            "next_options_csv": str(next_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.18 only closed the already authorized H17_A validation result. It did not train, tune from heldout/hard, "
            "change thresholds, reintroduce pipe/support, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(evidence_csv, evidence_rows, ["evidence_id", "status", "evidence", "meaning"])
    _write_csv(next_csv, next_options, ["option_id", "stage", "action", "why", "boundary"])
    _write_csv(blocked_csv, blocked_actions, ["action", "blocked", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
