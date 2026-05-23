from __future__ import annotations

import argparse
import csv
import json
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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_ACCEPTED_SOURCES = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_sources.csv"
DEFAULT_S2_SOURCE_SUPPORT = AGENT_STATE / "goal_10x_s2_independent_source_robustness_gate_source_support.csv"
DEFAULT_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review"
LEAD_CANDIDATE = "OBJ_A_current_lambda_rank_baseline__FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _to_int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _write_markdown(path: Path, report: dict[str, Any], source_rows: list[dict[str, Any]], reasons: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.28 S2 accepted-OSS evidence gap review",
        "",
        "Read-only review of why accepted human OSS sources still have zero S2 positive net.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["accepted_oss_source_file_count", metrics["accepted_oss_source_file_count"]],
                ["accepted_oss_source_family_count", metrics["accepted_oss_source_family_count"]],
                ["accepted_oss_s2_groups", metrics["accepted_oss_s2_groups"]],
                ["accepted_oss_s2_gain", metrics["accepted_oss_s2_gain"]],
                ["accepted_oss_s2_loss", metrics["accepted_oss_s2_loss"]],
                ["accepted_oss_s2_net", metrics["accepted_oss_s2_net"]],
                ["generated_positive_net_still_blocking", metrics["generated_positive_net_still_blocking"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Accepted OSS Source Evidence",
        "",
        _md_table(
            [["source_file", "source_family", "s2_groups", "gain", "loss", "net", "gap_explanation"]]
            + [
                [
                    row["source_file"],
                    row["source_family"],
                    row["s2_groups"],
                    row["s2_gain"],
                    row["s2_loss"],
                    row["s2_net"],
                    row["gap_explanation"],
                ]
                for row in source_rows
            ]
        ),
        "",
        "## Gap Reasons",
        "",
        _md_table(
            [["reason", "severity", "evidence", "recommended_next"]]
            + [[row["reason"], row["severity"], row["evidence"], row["recommended_next"]] for row in reasons]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review accepted OSS evidence gap for S2 without execution")
    parser.add_argument("--accepted-sources", default=str(DEFAULT_ACCEPTED_SOURCES))
    parser.add_argument("--s2-source-support", default=str(DEFAULT_S2_SOURCE_SUPPORT))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--hit1-flips", default=str(DEFAULT_HIT1_FLIPS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--candidate-id", default=LEAD_CANDIDATE)
    args = parser.parse_args()

    started = time.perf_counter()
    accepted = _read_csv(Path(args.accepted_sources))
    source_support = _read_csv(Path(args.s2_source_support))
    scorecard = _read_csv(Path(args.scorecard))
    flips = _read_jsonl(Path(args.hit1_flips))

    accepted_sources = {row["source_file"] for row in accepted}
    accepted_by_source = {row["source_file"]: row for row in accepted}
    support_by_source = {
        row["source_file"]: row
        for row in source_support
        if row.get("candidate_id") == args.candidate_id
    }
    lead_scorecard = next((row for row in scorecard if row.get("candidate_id") == args.candidate_id), {})
    lead_flips = [row for row in flips if row.get("candidate_id") == args.candidate_id]
    flip_counts_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for row in lead_flips:
        source = str(row.get("source_file") or "")
        if source in accepted_sources:
            flip_counts_by_source[source][str(row.get("flip_type") or "unknown")] += 1

    evidence_rows: list[dict[str, Any]] = []
    family_rollup: dict[str, dict[str, Any]] = {}
    for accepted_row in accepted:
        source_file = accepted_row["source_file"]
        support = support_by_source.get(source_file, {})
        gain = _to_int(support.get("gain"))
        loss = _to_int(support.get("loss"))
        net = _to_int(support.get("net"))
        groups = _to_int(support.get("groups"))
        family = accepted_row["source_family"]
        if family not in family_rollup:
            family_rollup[family] = {"source_family": family, "source_files": 0, "groups": 0, "gain": 0, "loss": 0, "net": 0, "positive_net": 0}
        family_rollup[family]["source_files"] += 1
        family_rollup[family]["groups"] += groups
        family_rollup[family]["gain"] += gain
        family_rollup[family]["loss"] += loss
        family_rollup[family]["net"] += net
        family_rollup[family]["positive_net"] += max(net, 0)
        evidence_rows.append(
            {
                "source_file": source_file,
                "source_family": family,
                "row_count_total": accepted_row.get("row_count_total", ""),
                "dev_row_count": accepted_row.get("dev_row_count", ""),
                "s2_groups": groups,
                "s2_gain": gain,
                "s2_loss": loss,
                "s2_net": net,
                "s2_positive_net": max(net, 0),
                "hit1_flip_gain_rows": flip_counts_by_source[source_file].get("gain", 0),
                "hit1_flip_loss_rows": flip_counts_by_source[source_file].get("loss", 0),
                "gap_explanation": (
                    "No accepted-OSS Top1 flips for the S2 diagnostic lead; baseline and candidate outcomes did not change on this slice."
                    if gain == 0 and loss == 0
                    else "Accepted-OSS slice has non-zero flips and needs separate review."
                ),
            }
        )

    family_rows = list(family_rollup.values())
    generated_positive_net = sum(
        _to_int(row.get("positive_net"))
        for row in source_support
        if row.get("candidate_id") == args.candidate_id and row.get("source_class") == "generated"
    )
    accepted_groups = sum(row["s2_groups"] for row in evidence_rows)
    accepted_gain = sum(row["s2_gain"] for row in evidence_rows)
    accepted_loss = sum(row["s2_loss"] for row in evidence_rows)
    accepted_net = sum(row["s2_net"] for row in evidence_rows)
    accepted_positive_families = sum(1 for row in family_rows if _to_int(row.get("positive_net")) > 0)
    reasons = [
        {
            "reason": "zero_observed_top1_flips_on_accepted_oss",
            "severity": "blocking",
            "evidence": f"accepted OSS gain={accepted_gain}, loss={accepted_loss}, net={accepted_net}; hit1 flip rows on accepted OSS=0",
            "recommended_next": "Do not reopen S2; require new independent dev/OOF evidence where accepted OSS has positive net.",
        },
        {
            "reason": "low_accepted_oss_s2_support",
            "severity": "blocking",
            "evidence": f"accepted OSS S2 groups={accepted_groups} across {len(accepted_sources)} files and {len(family_rows)} source families",
            "recommended_next": "Need broader accepted OSS evidence, or accept that current S2 lane has no demonstrated OSS lift.",
        },
        {
            "reason": "generated_gain_still_dominates",
            "severity": "blocking",
            "evidence": f"generated positive net={generated_positive_net}; accepted OSS positive net={max(accepted_net, 0)}",
            "recommended_next": "Keep generated repair-decision table out of learning evidence and do not claim general Top1 gain.",
        },
        {
            "reason": "source_provenance_solved_but_learning_signal_absent",
            "severity": "high",
            "evidence": "10.27 accepted v36_oss_* provenance, but every accepted source has S2 gain=0 and loss=0.",
            "recommended_next": "Park S2 unless a future read-only evidence package shows non-generated positive net > 0 from at least two accepted source families.",
        },
    ]
    decision_rows = [
        {
            "decision_item": "s2_lane_status",
            "decision": "KEEP_S2_PARKED",
            "rationale": "Accepted OSS provenance exists, but accepted OSS positive net is 0 and generated positive net remains dominant.",
        },
        {
            "decision_item": "request_more_independent_evidence",
            "decision": "YES_IF_CONTINUING_S2",
            "rationale": "Future S2 re-entry would need non-generated positive net > 0 and >=2 positive accepted source families.",
        },
        {
            "decision_item": "open_reentry_review_now",
            "decision": "NO",
            "rationale": "Source provenance acceptance alone is not a learning signal.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "open_learning_reentry_review",
            "reason": "Accepted OSS S2 positive net is 0 and positive-net source families are 0.",
            "allowed_after": "future independent accepted-OSS evidence shows positive net and passes remaining DQ gates",
        },
        {
            "blocked_action": "claim_s2_general_top1_gain",
            "reason": "All observed S2 positive net still comes from generated repair-decision source.",
            "allowed_after": "future source robustness pass on accepted non-generated sources",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "10.28 is read-only evidence-gap review.",
            "allowed_after": "separate explicit execution authorization after re-entry",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No frozen validation candidate and no accepted-source positive support exists.",
            "allowed_after": "future validation gate after re-entry and source robustness pass",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "accepted_source_gap_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_source_gap.csv")),
        "accepted_family_gap_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_family_gap.csv")),
        "gap_reasons_csv": str(output_prefix.with_name(output_prefix.name + "_gap_reasons.csv")),
        "decision_csv": str(output_prefix.with_name(output_prefix.name + "_decision.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "candidate_id": args.candidate_id,
        "scorecard_hit1_net": _to_int(lead_scorecard.get("hit1_net")),
        "scorecard_hit1_gain": _to_int(lead_scorecard.get("hit1_gain")),
        "scorecard_hit1_loss": _to_int(lead_scorecard.get("hit1_loss")),
        "accepted_oss_source_file_count": len(accepted_sources),
        "accepted_oss_source_family_count": len(family_rows),
        "accepted_oss_s2_groups": accepted_groups,
        "accepted_oss_s2_gain": accepted_gain,
        "accepted_oss_s2_loss": accepted_loss,
        "accepted_oss_s2_net": accepted_net,
        "accepted_oss_positive_source_family_count": accepted_positive_families,
        "accepted_oss_hit1_flip_rows": sum(sum(counter.values()) for counter in flip_counts_by_source.values()),
        "generated_positive_net_still_blocking": generated_positive_net,
        "needs_more_independent_evidence": True,
        "s2_lane_should_remain_parked": True,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.28 S2 accepted-OSS evidence gap review",
        "read_only": True,
        "accepted_oss_gap_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Keep S2 parked. The accepted v36_oss_* sources are now valid human OSS provenance, but the S2 diagnostic lead has gain=0, loss=0, net=0 on those accepted OSS slices. "
            "The dev/OOF positive net remains generated-source dominated, so the next requirement is additional independent accepted-OSS evidence, not training or validation."
        ),
        "anti_drift_conclusion": (
            "10.28 only explains the accepted-OSS evidence gap using existing dev/OOF artifacts. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or claim S2 general Top1 gain."
        ),
        "next_stage": {
            "stage": "10.29 S2 lane park / evidence request closure",
            "goal": "Read-only decide whether to formally keep S2 parked and request additional independent accepted-OSS evidence, or return to remaining DQ artifact backlog.",
            "blocked_until": "accepted OSS positive net > 0 and at least two accepted source families carry positive support without generated dominance",
        },
    }
    _write_csv(Path(artifacts["accepted_source_gap_csv"]), evidence_rows, [
        "source_file", "source_family", "row_count_total", "dev_row_count", "s2_groups",
        "s2_gain", "s2_loss", "s2_net", "s2_positive_net", "hit1_flip_gain_rows",
        "hit1_flip_loss_rows", "gap_explanation",
    ])
    _write_csv(Path(artifacts["accepted_family_gap_csv"]), family_rows, [
        "source_family", "source_files", "groups", "gain", "loss", "net", "positive_net",
    ])
    _write_csv(Path(artifacts["gap_reasons_csv"]), reasons, ["reason", "severity", "evidence", "recommended_next"])
    _write_csv(Path(artifacts["decision_csv"]), decision_rows, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, evidence_rows, reasons)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
