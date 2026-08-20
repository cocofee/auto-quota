from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
P17_SUMMARY = AGENT_STATE / "goal_17x_p17b_precision_family_retention_dev_oof_summary.json"
P17_H_ROW_AUDIT = AGENT_STATE / "goal_17x_p17b_precision_family_retention_dev_oof_p17_h_p17f_plus_rebar_specific_rescue_row_audit.csv"
P17_I_ROW_AUDIT = AGENT_STATE / "goal_17x_p17b_precision_family_retention_dev_oof_p17_i_p17f_plus_pump_specific_rescue_row_audit.csv"
P17_J_ROW_AUDIT = AGENT_STATE / "goal_17x_p17b_precision_family_retention_dev_oof_p17_j_p17f_plus_family_specific_rescue_row_audit.csv"
P17_K_ROW_AUDIT = AGENT_STATE / "goal_17x_p17b_precision_family_retention_dev_oof_p17_k_p17f_plus_capped_second_family_slot_row_audit.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_branch_blocker_audit_strategy_decision"


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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


def _candidate_row_audits() -> dict[str, list[dict[str, str]]]:
    return {
        "P17_H": _read_csv(P17_H_ROW_AUDIT),
        "P17_I": _read_csv(P17_I_ROW_AUDIT),
        "P17_J": _read_csv(P17_J_ROW_AUDIT),
        "P17_K": _read_csv(P17_K_ROW_AUDIT),
    }


def _headline_from_rows(rows: list[dict[str, Any]], *, apply_baseline_rank1_veto: bool = False) -> dict[str, Any]:
    baseline_top1 = treatment_top1 = baseline_top5 = treatment_top5 = 0
    baseline_top20 = treatment_top20 = baseline_top80 = treatment_top80 = 0
    top1_wins = top1_losses = top80_gains = top80_losses = 0
    generated = positive = false = 0
    family_positive: set[str] = set()
    for row in rows:
        vetoed = apply_baseline_rank1_veto and _safe_int(row.get("baseline_rank")) == 1 and _safe_int(row.get("prior_generated_candidates")) > 0
        b1 = _safe_int(row.get("baseline_top1"))
        b5 = _safe_int(row.get("baseline_top5"))
        b20 = _safe_int(row.get("baseline_top20"))
        b80 = _safe_int(row.get("baseline_top80"))
        t1 = b1 if vetoed else _safe_int(row.get("treatment_top1"))
        t5 = b5 if vetoed else _safe_int(row.get("treatment_top5"))
        t20 = b20 if vetoed else _safe_int(row.get("treatment_top20"))
        t80 = b80 if vetoed else _safe_int(row.get("treatment_top80"))
        pg = 0 if vetoed else _safe_int(row.get("prior_generated_candidates"))
        pp = 0 if vetoed else _safe_int(row.get("prior_positive_candidates"))
        pf = 0 if vetoed else _safe_int(row.get("prior_false_candidates"))
        baseline_top1 += b1
        baseline_top5 += b5
        baseline_top20 += b20
        baseline_top80 += b80
        treatment_top1 += t1
        treatment_top5 += t5
        treatment_top20 += t20
        treatment_top80 += t80
        top1_wins += int(t1 > b1)
        top1_losses += int(t1 < b1)
        top80_gains += int(t80 > b80)
        top80_losses += int(t80 < b80)
        generated += pg
        positive += pp
        false += pf
        if pp > 0:
            family_positive.add(str(row.get("query_family", "")))
    return {
        "groups": len(rows),
        "baseline_top1": baseline_top1,
        "treatment_top1": treatment_top1,
        "delta_top1": treatment_top1 - baseline_top1,
        "baseline_top5": baseline_top5,
        "treatment_top5": treatment_top5,
        "delta_top5": treatment_top5 - baseline_top5,
        "baseline_top20": baseline_top20,
        "treatment_top20": treatment_top20,
        "delta_top20": treatment_top20 - baseline_top20,
        "baseline_top80": baseline_top80,
        "treatment_top80": treatment_top80,
        "delta_top80": treatment_top80 - baseline_top80,
        "top1_wins": top1_wins,
        "top1_losses": top1_losses,
        "top80_gains": top80_gains,
        "top80_losses": top80_losses,
        "prior_generated_candidates": generated,
        "prior_positive_candidates": positive,
        "prior_false_candidates": false,
        "positive_family_count": len(family_positive),
        "positive_families": "|".join(sorted(family_positive)),
    }


def _veto_effect_rows(audits: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate, candidate_rows in audits.items():
        original = _headline_from_rows(candidate_rows)
        vetoed = _headline_from_rows(candidate_rows, apply_baseline_rank1_veto=True)
        rows.append(
            {
                "candidate": candidate,
                "policy": "baseline_rank1_veto_posthoc_audit",
                "original_delta_top1": original["delta_top1"],
                "veto_delta_top1": vetoed["delta_top1"],
                "original_top1_losses": original["top1_losses"],
                "veto_top1_losses": vetoed["top1_losses"],
                "original_false_candidates": original["prior_false_candidates"],
                "veto_false_candidates": vetoed["prior_false_candidates"],
                "original_positive_candidates": original["prior_positive_candidates"],
                "veto_positive_candidates": vetoed["prior_positive_candidates"],
                "original_positive_family_count": original["positive_family_count"],
                "veto_positive_family_count": vetoed["positive_family_count"],
                "veto_result": (
                    "safety_only_no_family_retention"
                    if vetoed["top1_losses"] == 0 and vetoed["positive_family_count"] < 2
                    else "needs_review"
                ),
            }
        )
    return rows


def _blocker_rows(audits: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate, candidate_rows in audits.items():
        for row in candidate_rows:
            if _safe_int(row.get("top1_loss")) or (_safe_int(row.get("prior_generated_candidates")) and _safe_int(row.get("prior_positive_candidates")) == 0):
                rows.append(
                    {
                        "candidate": candidate,
                        "anchor_group_id": row.get("anchor_group_id", ""),
                        "query_family": row.get("query_family", ""),
                        "expected_ids": row.get("expected_ids", ""),
                        "baseline_rank": row.get("baseline_rank", ""),
                        "treatment_rank": row.get("treatment_rank", ""),
                        "baseline_top1_id": row.get("baseline_top1_id", ""),
                        "treatment_top1_id": row.get("treatment_top1_id", ""),
                        "top1_loss": row.get("top1_loss", ""),
                        "prior_generated_candidates": row.get("prior_generated_candidates", ""),
                        "prior_positive_candidates": row.get("prior_positive_candidates", ""),
                        "prior_false_candidates": row.get("prior_false_candidates", ""),
                        "prior_candidate_ids": row.get("prior_candidate_ids", ""),
                        "audit_note": (
                            "baseline_rank1_loss_blocks_rebar_rescue"
                            if _safe_int(row.get("top1_loss")) and _safe_int(row.get("baseline_rank")) == 1
                            else "false_candidate_no_positive_retention"
                        ),
                    }
                )
    return rows


def _decision_rows(veto_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "option": "add_baseline_rank1_veto_inside_p17_lane",
            "decision": "do_not_execute_now",
            "reason": "Posthoc dev/OOF audit says the veto can remove the rebar Top1 loss, but it does not create pump/rebar positive family retention; it collapses back toward P17_F.",
        },
        {
            "option": "freeze_or_validate_p17_hijk",
            "decision": "blocked",
            "reason": "All fixed 17.24 candidates failed dev/OOF gates; no freeze candidate exists.",
        },
        {
            "option": "continue_p17_family_retention_by_threshold_tweaks",
            "decision": "blocked",
            "reason": "The current OSS index lacks a safe pump/rebar positive signal; more threshold tweaking risks replaying P17_G false expansion.",
        },
        {
            "option": "return_to_broader_oss_recall_index_redesign",
            "decision": "recommended_next",
            "reason": "The blocker is evidence/source representation for pump/rebar, not merely a final branch guard. Next work should redesign index/evidence features rather than validate P17.",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.25 Branch-Blocker Audit / Strategy Decision",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Findings",
        "",
        "- The only Top1 loss in P17_H/P17_J is the dev/OOF rebar row `dev:923:104`: expected `4-9-44`, baseline rank `1`, treatment `4-9-40`, expected rank `1->2`.",
        "- A baseline-rank1 veto is online-observable and would remove that loss in posthoc dev/OOF audit.",
        "- The veto does not solve the reason P17_H/I/J/K failed: positive family retention remains `1`, so pump/rebar still do not provide safe positive movement.",
        "",
        "## Veto Effect",
        "",
        "| candidate | delta Top1 original -> veto | losses original -> veto | false original -> veto | positive families original -> veto |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["veto_effect_rows"]:
        lines.append(
            f"| {row['candidate']} | {row['original_delta_top1']} -> {row['veto_delta_top1']} | "
            f"{row['original_top1_losses']} -> {row['veto_top1_losses']} | "
            f"{row['original_false_candidates']} -> {row['veto_false_candidates']} | "
            f"{row['original_positive_family_count']} -> {row['veto_positive_family_count']} |"
        )
    lines.extend(["", "## Strategy Options", "", "| option | decision | reason |", "|---|---|---|"])
    for row in report["decision_rows"]:
        lines.append(f"| {row['option']} | {row['decision']} | {row['reason']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    summary = _read_json(P17_SUMMARY)
    if summary.get("decision") != "no_freeze_candidate_all_precision_family_retention_candidates_failed_dev_oof_gate":
        raise ValueError(f"unexpected 17.24 decision: {summary.get('decision')}")

    audits = _candidate_row_audits()
    veto_rows = _veto_effect_rows(audits)
    blockers = _blocker_rows(audits)
    decisions = _decision_rows(veto_rows)

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    veto_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_veto_effect.csv")
    blockers_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocker_rows.csv")
    decisions_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_strategy_decisions.csv")
    report = {
        "stage": "17.25 branch-blocker audit / strategy decision",
        "decision": "stop_p17_family_retention_lane_return_to_broader_oss_recall_index_redesign",
        "read_only": True,
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "source_stage": summary["stage"],
        "source_decision": summary["decision"],
        "veto_effect_rows": veto_rows,
        "blocker_rows": blockers,
        "decision_rows": decisions,
        "next_boundary": (
            "17.26 broader OSS recall/index redesign scope may be defined next. It should focus on pump/rebar evidence representation "
            "and source/index features, not P17 threshold tweaks, validation, default enablement, or GoalSearcher changes."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "veto_effect_csv": str(veto_csv),
            "blocker_rows_csv": str(blockers_csv),
            "strategy_decisions_csv": str(decisions_csv),
        },
        "anti_drift_conclusion": (
            "17.25 is a read-only dev/OOF blocker audit. It does not run a new matrix, train, tune from heldout/hard, "
            "default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(
        veto_csv,
        veto_rows,
        [
            "candidate",
            "policy",
            "original_delta_top1",
            "veto_delta_top1",
            "original_top1_losses",
            "veto_top1_losses",
            "original_false_candidates",
            "veto_false_candidates",
            "original_positive_candidates",
            "veto_positive_candidates",
            "original_positive_family_count",
            "veto_positive_family_count",
            "veto_result",
        ],
    )
    _write_csv(
        blockers_csv,
        blockers,
        [
            "candidate",
            "anchor_group_id",
            "query_family",
            "expected_ids",
            "baseline_rank",
            "treatment_rank",
            "baseline_top1_id",
            "treatment_top1_id",
            "top1_loss",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "prior_candidate_ids",
            "audit_note",
        ],
    )
    _write_csv(decisions_csv, decisions, ["option", "decision", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
