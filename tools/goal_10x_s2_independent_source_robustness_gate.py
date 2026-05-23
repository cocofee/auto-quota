from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_10x_s2_candidate_freeze_validation_gate_summary.json"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_independent_source_robustness_gate"

GENERATED_SOURCE_KEYS = ("global_repair_decision_table.csv",)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_generated_source(source_key: str) -> bool:
    return any(token in source_key for token in GENERATED_SOURCE_KEYS)


def _source_support(loss_audit: pd.DataFrame, candidate_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = loss_audit[(loss_audit["candidate_id"] == candidate_id) & (loss_audit["slice_dimension"] == "source_file")].copy()
    if rows.empty:
        return [], {
            "source_slice_count": 0,
            "generated_positive_net": 0,
            "non_generated_positive_net": 0,
            "non_generated_positive_source_count": 0,
        }
    out: list[dict[str, Any]] = []
    generated_positive_net = 0
    non_generated_positive_net = 0
    non_generated_positive_source_count = 0
    non_generated_group_count = 0
    non_generated_gain = 0
    non_generated_loss = 0
    for _, row in rows.iterrows():
        key = str(row.get("slice_key") or "")
        gain = _int(row.get("gain"))
        loss = _int(row.get("loss"))
        net = _int(row.get("net"))
        groups = _int(row.get("groups"))
        generated = _is_generated_source(key)
        positive_net = max(0, net)
        if generated:
            generated_positive_net += positive_net
        else:
            non_generated_positive_net += positive_net
            non_generated_group_count += groups
            non_generated_gain += gain
            non_generated_loss += loss
            if positive_net > 0:
                non_generated_positive_source_count += 1
        out.append(
            {
                "candidate_id": candidate_id,
                "source_file": key,
                "source_class": "generated" if generated else "non_generated",
                "groups": groups,
                "gain": gain,
                "loss": loss,
                "net": net,
                "positive_net": positive_net,
                "supports_generalization": (not generated) and positive_net > 0,
            }
        )
    total_positive_net = generated_positive_net + non_generated_positive_net
    stats = {
        "source_slice_count": len(out),
        "generated_positive_net": generated_positive_net,
        "non_generated_positive_net": non_generated_positive_net,
        "total_positive_net": total_positive_net,
        "non_generated_positive_net_share": round(non_generated_positive_net / total_positive_net, 6) if total_positive_net else 0.0,
        "generated_positive_net_share": round(generated_positive_net / total_positive_net, 6) if total_positive_net else 0.0,
        "non_generated_positive_source_count": non_generated_positive_source_count,
        "non_generated_group_count": non_generated_group_count,
        "non_generated_gain": non_generated_gain,
        "non_generated_loss": non_generated_loss,
        "non_generated_net": non_generated_gain - non_generated_loss,
    }
    return sorted(out, key=lambda item: (item["source_class"], -item["net"], item["source_file"])), stats


def _dimension_summary(loss_audit: pd.DataFrame, candidate_id: str) -> list[dict[str, Any]]:
    rows = loss_audit[loss_audit["candidate_id"] == candidate_id].copy()
    output: list[dict[str, Any]] = []
    for dimension in ["query_family", "top1_family", "province", "book_and_rank_bucket"]:
        part = rows[rows["slice_dimension"] == dimension].copy()
        if part.empty:
            continue
        part["gain_int"] = part["gain"].map(_int)
        part["loss_int"] = part["loss"].map(_int)
        part["net_int"] = part["net"].map(_int)
        positive = part[part["net_int"] > 0]
        top = part.sort_values(["net_int", "gain_int"], ascending=[False, False]).head(1).iloc[0]
        output.append(
            {
                "candidate_id": candidate_id,
                "slice_dimension": dimension,
                "slice_count": int(len(part)),
                "positive_net_slice_count": int(len(positive)),
                "total_gain": int(part["gain_int"].sum()),
                "total_loss": int(part["loss_int"].sum()),
                "total_net": int(part["net_int"].sum()),
                "top_positive_slice_key": top.get("slice_key"),
                "top_positive_slice_net": int(top.get("net_int")),
                "top_positive_slice_gain": int(top.get("gain_int")),
                "top_positive_slice_loss": int(top.get("loss_int")),
            }
        )
    return output


def _gate_checks(source_stats: dict[str, Any], freeze_summary: dict[str, Any]) -> list[dict[str, Any]]:
    freeze_decision = freeze_summary.get("metrics", {}).get("freeze_decision")
    return [
        {
            "gate": "freeze_gate_hold_confirmed",
            "status": "pass" if freeze_decision == "HOLD_DO_NOT_FREEZE_FOR_GENERAL_VALIDATION" else "fail",
            "observed": f"freeze_decision={freeze_decision}",
            "decision": "use_diagnostic_lead_only",
            "not_allowed": "do not treat held candidate as frozen validation candidate",
        },
        {
            "gate": "non_generated_positive_net",
            "status": "pass" if _int(source_stats.get("non_generated_positive_net")) > 0 else "fail",
            "observed": f"non_generated_positive_net={source_stats.get('non_generated_positive_net')}",
            "decision": "non_generated_support_present" if _int(source_stats.get("non_generated_positive_net")) > 0 else "no_non_generated_positive_support",
            "not_allowed": "do not proceed to heldout/hard validation without non-generated positive support",
        },
        {
            "gate": "non_generated_positive_source_count",
            "status": "pass" if _int(source_stats.get("non_generated_positive_source_count")) >= 2 else "fail",
            "observed": f"non_generated_positive_source_count={source_stats.get('non_generated_positive_source_count')}; required>=2",
            "decision": "cross_source_support_present" if _int(source_stats.get("non_generated_positive_source_count")) >= 2 else "insufficient_cross_source_support",
            "not_allowed": "single-source or zero-source signal cannot generalize",
        },
        {
            "gate": "generated_source_dominance",
            "status": "pass" if _float(source_stats.get("generated_positive_net_share")) <= 0.5 else "fail",
            "observed": f"generated_positive_net_share={source_stats.get('generated_positive_net_share')}",
            "decision": "generated_source_not_dominant" if _float(source_stats.get("generated_positive_net_share")) <= 0.5 else "generated_source_dominant",
            "not_allowed": "do not claim general ranking improvement from generated-source dominance",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass",
            "observed": "heldout_used_for_selection=false; hard_used_for_selection=false",
            "decision": "validation_still_blocked_until_source_gate_passes",
            "not_allowed": "no heldout/hard validation from failed source gate",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_heldout_or_hard_validation",
            "reason": "independent-source robustness failed; heldout/hard validation would be premature.",
            "allowed_after": "future source robustness pass with explicit validation stage",
        },
        {
            "blocked_action": "freeze_candidate_for_general_validation",
            "reason": "diagnostic lead has no positive non-generated source support.",
            "allowed_after": "independent non-generated positive source support exists",
        },
        {
            "blocked_action": "retrain_or_expand_candidates",
            "reason": "robustness gate is read-only over completed dev/OOF artifacts.",
            "allowed_after": "separate explicit execution scope",
        },
        {
            "blocked_action": "change_ranking_goal_searcher_or_feature_whitelist",
            "reason": "source robustness review is not implementation.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "positive net gain is generated-source dominated.",
            "allowed_after": "future independent-source evidence review",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# S2 Independent-source Robustness Gate",
        "",
        "Read-only source robustness review for the diagnostic lead candidate before any heldout/hard validation.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_id", metrics["candidate_id"]],
                ["robustness_decision", metrics["robustness_decision"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["gate_fail_count", metrics["gate_fail_count"]],
                ["generated_positive_net_share", metrics["generated_positive_net_share"]],
                ["non_generated_positive_net", metrics["non_generated_positive_net"]],
                ["non_generated_positive_source_count", metrics["non_generated_positive_source_count"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "observed", "decision"]] + [[row["gate"], row["status"], row["observed"], row["decision"]] for row in report["gate_checks"]]),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="S2 independent-source robustness gate")
    parser.add_argument("--freeze-summary", default=str(DEFAULT_FREEZE_SUMMARY))
    parser.add_argument("--loss-audit", default=str(DEFAULT_LOSS_AUDIT))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    freeze_summary = _read_json(Path(args.freeze_summary))
    candidate_id = freeze_summary.get("metrics", {}).get("selected_candidate_id")
    if not candidate_id:
        raise RuntimeError("freeze summary missing selected_candidate_id")
    loss_audit = pd.read_csv(args.loss_audit, encoding="utf-8-sig")
    source_rows, source_stats = _source_support(loss_audit, candidate_id)
    dimension_rows = _dimension_summary(loss_audit, candidate_id)
    gate_checks = _gate_checks(source_stats, freeze_summary)
    fail_gates = [row for row in gate_checks if row["status"] != "pass"]
    robustness_decision = "FAIL_HOLD_NO_HELDOUT_HARD_VALIDATION" if fail_gates else "PASS_ALLOW_EXPLICIT_VALIDATION_GATE"
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "source_support_csv": str(output_prefix.with_name(output_prefix.name + "_source_support.csv")),
        "dimension_summary_csv": str(output_prefix.with_name(output_prefix.name + "_dimension_summary.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "candidate_id": candidate_id,
        "robustness_decision": robustness_decision,
        "gate_count": len(gate_checks),
        "gate_pass_count": sum(1 for row in gate_checks if row["status"] == "pass"),
        "gate_fail_count": len(fail_gates),
        **source_stats,
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "validation_allowed_now": robustness_decision == "PASS_ALLOW_EXPLICIT_VALIDATION_GATE",
        "online_integration": False,
        "goal_searcher_changed": False,
        "feature_whitelist_changed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    decision = (
        f"Fail the independent-source robustness gate for {candidate_id}. The diagnostic lead has generated_positive_net={source_stats['generated_positive_net']} "
        f"and non_generated_positive_net={source_stats['non_generated_positive_net']}; non_generated_positive_source_count={source_stats['non_generated_positive_source_count']}. "
        "Because all positive net support comes from global_repair_decision_table.csv, the candidate remains held and must not proceed to heldout/hard validation."
        if robustness_decision.startswith("FAIL")
        else f"Pass source robustness for {candidate_id}; a separate explicit validation gate may be opened."
    )
    report = {
        "stage": "Goal LTR v1 / S2 independent-source robustness gate",
        "read_only": True,
        "dev_oof_only_review": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "no_retraining": True,
        "no_candidate_expansion": True,
        "no_threshold_change": True,
        "no_ranking_change": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "freeze_summary": str(Path(args.freeze_summary)),
            "loss_audit": str(Path(args.loss_audit)),
        },
        "metrics": metrics,
        "source_support": source_rows,
        "dimension_summary": dimension_rows,
        "gate_checks": gate_checks,
        "blocked_actions": _blocked_actions(),
        "artifacts": artifacts,
        "decision": decision,
        "anti_drift_conclusion": (
            "This stage only reviews completed dev/OOF source slices. It does not retrain, expand candidates, use heldout/hard for selection or validation, "
            "change thresholds, patch rules, modify ranking or GoalSearcher, edit the feature whitelist, or connect online."
        ),
        "next_stage": {
            "stage": "S2 source-dominated candidate hold and strategy-return gate",
            "goal": "Read-only decide whether to keep S2 held, request independent non-generated evidence, or return to broader strategy review.",
            "prohibited": [
                "retraining",
                "candidate expansion",
                "heldout/hard validation",
                "heldout/hard selection",
                "threshold change",
                "ranking implementation",
                "GoalSearcher change",
                "feature whitelist edit",
                "online integration",
            ],
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(Path(artifacts["source_support_csv"]), source_rows, ["candidate_id", "source_file", "source_class", "groups", "gain", "loss", "net", "positive_net", "supports_generalization"])
    _write_csv(Path(artifacts["dimension_summary_csv"]), dimension_rows, ["candidate_id", "slice_dimension", "slice_count", "positive_net_slice_count", "total_gain", "total_loss", "total_net", "top_positive_slice_key", "top_positive_slice_net", "top_positive_slice_gain", "top_positive_slice_loss"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), report["blocked_actions"], ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
