from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
P17_SUMMARY = AGENT_STATE / "goal_17x_false_candidate_precision_guard_dev_oof_summary.json"
P17B_ROW_AUDIT = AGENT_STATE / "goal_17x_false_candidate_precision_guard_dev_oof_p17_b_topk1_strong_guard_row_audit.csv"
P17B_SCORECARD = AGENT_STATE / "goal_17x_false_candidate_precision_guard_dev_oof_p17_b_topk1_strong_guard_scorecard.csv"
H17A_ROW_AUDIT = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_row_audit.csv"
H17A_SCORECARD = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_scorecard.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_p17b_retention_gap_review"


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


def _family_stats(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        family = row.get("query_family") or "<empty>"
        stats = out[family]
        stats["rows"] += 1
        stats["generated"] += _safe_int(row.get("prior_generated_candidates"))
        stats["positive"] += _safe_int(row.get("prior_positive_candidates"))
        stats["false"] += _safe_int(row.get("prior_false_candidates"))
        stats["top1_wins"] += _safe_int(row.get("top1_win"))
        stats["top1_losses"] += _safe_int(row.get("top1_loss"))
        stats["top80_gains"] += _safe_int(row.get("top80_gain"))
        stats["top80_losses"] += _safe_int(row.get("top80_loss"))
    return {family: dict(stats) for family, stats in out.items()}


def _retention_rows(h17a_rows: list[dict[str, str]], p17b_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    p17b_by_anchor = {row.get("anchor_group_id"): row for row in p17b_rows}
    rows: list[dict[str, Any]] = []
    for hrow in h17a_rows:
        anchor = hrow.get("anchor_group_id")
        prow = p17b_by_anchor.get(anchor, {})
        h_pos = _safe_int(hrow.get("prior_positive_candidates"))
        p_pos = _safe_int(prow.get("prior_positive_candidates"))
        h_gen = _safe_int(hrow.get("prior_generated_candidates"))
        p_gen = _safe_int(prow.get("prior_generated_candidates"))
        if h_pos <= 0 and p_pos <= 0 and h_gen == p_gen:
            continue
        rows.append(
            {
                "anchor_group_id": anchor,
                "query_family": hrow.get("query_family"),
                "expected_ids": hrow.get("expected_ids"),
                "h17a_generated": h_gen,
                "h17a_positive": h_pos,
                "h17a_false": _safe_int(hrow.get("prior_false_candidates")),
                "p17b_generated": p_gen,
                "p17b_positive": p_pos,
                "p17b_false": _safe_int(prow.get("prior_false_candidates")),
                "positive_delta": p_pos - h_pos,
                "false_delta": _safe_int(prow.get("prior_false_candidates")) - _safe_int(hrow.get("prior_false_candidates")),
                "p17b_top1_win": _safe_int(prow.get("top1_win")),
                "p17b_top1_loss": _safe_int(prow.get("top1_loss")),
                "note": "retained_or_improved" if p_pos >= h_pos else "positive_retention_gap",
            }
        )
    return rows


def _redesign_matrix() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "P17_E_topk1_plus_rebar_rescue",
            "role": "recommended next",
            "design": "Keep P17_B TopK=1 strong guard as the trunk; add rebar-only rescue with exact_name OR support>=3 + overlap>=2, still TopK=1 per family.",
            "why": "P17_B solved false dominance but lost non-concrete positive-family retention; H17_A had rebar positives with manageable false volume.",
            "dev_oof_gate": "top1_loss=0; delta_top1>=5; false<=12; positive>=8; positive_family_count>=2",
            "blocked": "No heldout/hard tuning; no validation until a future freeze gate passes.",
        },
        {
            "candidate_id": "P17_F_topk1_plus_pump_rebar_exact_rescue",
            "role": "stricter rescue branch",
            "design": "Keep P17_B trunk; add pump/rebar rescue only for exact_name candidates.",
            "why": "Tests whether family retention can be restored with minimal false expansion.",
            "dev_oof_gate": "top1_loss=0; delta_top1>=5; false<=10; positive>=8; positive_family_count>=2",
            "blocked": "If no family retention improves, do not freeze.",
        },
        {
            "candidate_id": "P17_G_p17b_plus_rank_position_cap",
            "role": "noise cap branch",
            "design": "Keep P17_B trunk; allow a second candidate only when the first candidate is positive-looking by exact_name/source_family evidence and no conflict.",
            "why": "Attempts to preserve P17_B's low false rate while recovering occasional Top5/Top20 positives.",
            "dev_oof_gate": "top1_loss=0; delta_top1>=5; delta_top5>=4; false<=15; positive_family_count>=2",
            "blocked": "No baseline expected-rank or expected_id leakage in the gate.",
        },
        {
            "candidate_id": "P17_H_relaxed_retention_gate_review",
            "role": "policy check only",
            "design": "Consider whether positive_family_count>=1 is acceptable for this tiny 29-row dev/OOF impacted audit if false<=7 and Top1 gain is +5.",
            "why": "P17_B is strong but may be under-credited by a small-sample family-diversity gate.",
            "dev_oof_gate": "read-only policy decision; no execution",
            "blocked": "Cannot use this to skip freeze/validation safeguards.",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {"action": "freeze_p17b_now", "blocked": True, "reason": "positive_family_count=1 failed the locked retention gate"},
        {"action": "run_heldout_hard_validation", "blocked": True, "reason": "no P17 candidate passed a freeze gate"},
        {"action": "release_or_default_enable", "blocked": True, "reason": "17.20 produced no freeze candidate"},
        {"action": "tune_from_heldout_hard", "blocked": True, "reason": "heldout/hard are not part of 17.21 redesign"},
        {"action": "change_goal_searcher_defaults", "blocked": True, "reason": "17.21 is read-only review/redesign"},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    p = report["p17b_headline"]
    lines = [
        "# 17.21 P17_B Retention-Gap Review / Redesign",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## P17_B Signal",
        "",
        f"- Top1/Top5/Top20/Top80: `{p['delta_top1']}/{p['delta_top5']}/{p['delta_top20']}/{p['delta_top80']}`.",
        f"- Top1 wins/losses: `{p['top1_wins']}/{p['top1_losses']}`.",
        f"- generated/positive/false: `{p['prior_generated_candidates']}/{p['prior_positive_candidates']}/{p['prior_false_candidates']}`.",
        f"- false rate: `{p['prior_false_candidate_rate']}`.",
        "",
        "## Retention Diagnosis",
        "",
        report["retention_diagnosis"],
        "",
        "## Redesign Matrix",
        "",
        "| candidate | role | design | dev/OOF gate |",
        "|---|---|---|---|",
    ]
    for row in report["redesign_matrix"]:
        lines.append(f"| {row['candidate_id']} | {row['role']} | {row['design']} | {row['dev_oof_gate']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p17 = _read_json(P17_SUMMARY)
    p17b_scorecard = _read_csv(P17B_SCORECARD)
    p17b_rows = _read_csv(P17B_ROW_AUDIT)
    h17a_rows = _read_csv(H17A_ROW_AUDIT)
    h17a_scorecard = _read_csv(H17A_SCORECARD)
    p17b_headline = next(row for row in p17["comparison"] if row["candidate"] == "P17_B")
    h17a_family = _family_stats(h17a_rows)
    p17b_family = _family_stats(p17b_rows)
    retention = _retention_rows(h17a_rows, p17b_rows)
    gap_families = [
        family
        for family, stats in h17a_family.items()
        if stats.get("positive", 0) > 0 and p17b_family.get(family, {}).get("positive", 0) == 0
    ]
    diagnosis = (
        "P17_B is a strong precision trunk: it reduces false candidates from H17_A dev/OOF false=40 to 7, keeps Top1 loss=0, and improves Top1 to +5. "
        f"The failure is retention, not safety: positive family count drops to 1 because {', '.join(gap_families) or 'non-concrete families'} lose positive candidates. "
        "The next design should keep P17_B's TopK=1 strong guard as the trunk and add a narrow pump/rebar rescue lane on dev/OOF only."
    )
    redesign = _redesign_matrix()
    blocked = _blocked_actions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    family_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_family_retention.csv")
    retention_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_row_retention.csv")
    redesign_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_redesign_matrix.csv")
    blocked_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv")
    family_rows = []
    for family in sorted(set(h17a_family) | set(p17b_family)):
        h = h17a_family.get(family, {})
        p = p17b_family.get(family, {})
        family_rows.append(
            {
                "family": family,
                "h17a_positive": h.get("positive", 0),
                "h17a_false": h.get("false", 0),
                "p17b_positive": p.get("positive", 0),
                "p17b_false": p.get("false", 0),
                "positive_delta": p.get("positive", 0) - h.get("positive", 0),
                "false_delta": p.get("false", 0) - h.get("false", 0),
                "p17b_top1_wins": p.get("top1_wins", 0),
                "p17b_top1_losses": p.get("top1_losses", 0),
            }
        )
    report = {
        "stage": "17.21 P17_B retention-gap review / redesign",
        "decision": "p17b_retention_gap_confirmed_redesign_scope_ready",
        "p17_matrix_decision": p17["decision"],
        "p17b_headline": p17b_headline,
        "h17a_family_retention": h17a_family,
        "p17b_family_retention": p17b_family,
        "gap_families": gap_families,
        "retention_diagnosis": diagnosis,
        "redesign_matrix": redesign,
        "blocked_actions": blocked,
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "next_boundary": (
            "17.22 may define or run a dev/OOF-only P17_B retention-rescue matrix only after explicit go. "
            "Do not validate, release, or tune from heldout/hard."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "family_retention_csv": str(family_csv),
            "row_retention_csv": str(retention_csv),
            "redesign_matrix_csv": str(redesign_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.21 only reviewed P17_B dev/OOF retention gaps and defined redesign options. "
            "It did not execute a new matrix, train, read heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(family_csv, family_rows, ["family", "h17a_positive", "h17a_false", "p17b_positive", "p17b_false", "positive_delta", "false_delta", "p17b_top1_wins", "p17b_top1_losses"])
    _write_csv(retention_csv, retention, ["anchor_group_id", "query_family", "expected_ids", "h17a_generated", "h17a_positive", "h17a_false", "p17b_generated", "p17b_positive", "p17b_false", "positive_delta", "false_delta", "p17b_top1_win", "p17b_top1_loss", "note"])
    _write_csv(redesign_csv, redesign, ["candidate_id", "role", "design", "why", "dev_oof_gate", "blocked"])
    _write_csv(blocked_csv, blocked, ["action", "blocked", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "gap_families": gap_families, "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
