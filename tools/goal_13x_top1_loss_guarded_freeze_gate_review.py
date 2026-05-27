from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_SUMMARY = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_candidate_scorecard.csv"
DEFAULT_GATE_COVERAGE = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_gating_coverage_report.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_loss_audit_by_slice.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_leakage_gate_report.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_top1_loss_guarded_freeze_gate_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


DEPLOYABILITY = {
    "T1G_A_low_conf_margin_guard": ("mixed_requires_rewrite", "partly uses label-derived baseline_rank != 1 branch; low-confidence/margin part is deployable but plan needs rewrite"),
    "T1G_B_conflict_guard": ("deployable", "gate uses observable top1 conflict and challenger match features"),
    "T1G_C_non_rank1_only": ("diagnostic_only", "gate depends on positive rank / known answer position"),
    "T1G_D_near_miss_only": ("diagnostic_only", "gate depends on positive rank_2_5/rank_6_10 and is an upper-bound diagnostic"),
    "T1G_E_taxonomy_empty_guard": ("mixed_requires_rewrite", "taxonomy-empty is observable but weak-baseline branch partly uses label-derived non-rank1"),
    "T1G_F_hit5_rescue_with_top1_veto": ("diagnostic_only", "hit5 rescue / top1 veto depends on known positive movement during evaluation"),
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
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


def _candidate_review_rows(scorecard: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for row in scorecard:
        status, reason = DEPLOYABILITY.get(row["candidate_id"], ("unknown", "not classified"))
        rows.append(
            {
                "scorecard_rank": _int(row.get("scorecard_rank")),
                "candidate_id": row["candidate_id"],
                "deployability": status,
                "deployability_reason": reason,
                "hit1_gain": _int(row.get("hit1_gain")),
                "hit1_loss": _int(row.get("hit1_loss")),
                "hit1_net": _int(row.get("hit1_net")),
                "rank1_loss_count": _int(row.get("rank1_loss_count")),
                "baseline_rank1_demotion_rate": _float(row.get("baseline_rank1_demotion_rate")),
                "applied_group_rate": _float(row.get("applied_group_rate")),
                "approval_status": row.get("approval_status", ""),
            }
        )
    return rows


def _select_freeze_candidate(scorecard: list[dict[str, str]]) -> dict[str, str]:
    deployable = [
        row for row in scorecard
        if DEPLOYABILITY.get(row["candidate_id"], ("", ""))[0] == "deployable"
        and row.get("approval_status") == "pass_dev_oof_candidate"
    ]
    deployable.sort(key=lambda row: (_int(row.get("hit1_net")), -_int(row.get("hit1_loss"))), reverse=True)
    return deployable[0] if deployable else {}


def _source_net_stats(loss_audit: list[dict[str, str]], candidate_id: str) -> dict[str, Any]:
    source_rows = [
        row for row in loss_audit
        if row.get("candidate_id") == candidate_id and row.get("slice_dimension") in {"source_family", "source_file", "province"}
    ]
    positive = [_int(row.get("net")) for row in source_rows if _int(row.get("net")) > 0]
    positive_sum = sum(positive)
    top = max(source_rows, key=lambda row: _int(row.get("net")), default={})
    return {
        "positive_net_sum": positive_sum,
        "max_positive_net": _int(top.get("net")),
        "max_positive_key": top.get("slice_key", ""),
        "max_positive_dimension": top.get("slice_dimension", ""),
        "max_positive_net_share": round(_int(top.get("net")) / positive_sum, 6) if positive_sum else 0.0,
    }


def _gate_rows(best: dict[str, str], leakage: list[dict[str, str]], source_stats: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    candidate_id = best.get("candidate_id", "")
    leakage_row = next((row for row in leakage if row.get("candidate_id") == candidate_id), {})
    rows = [
        {
            "gate": "deployable_gate",
            "status": "pass" if DEPLOYABILITY.get(candidate_id, ("", ""))[0] == "deployable" else "fail",
            "value": DEPLOYABILITY.get(candidate_id, ("unknown", ""))[0],
            "reason": "Freeze candidate must use observable online gate features, not label-derived positive-rank gates.",
        },
        {
            "gate": "dev_oof_positive_top1_net",
            "status": "pass" if _int(best.get("hit1_net")) > 0 else "fail",
            "value": _int(best.get("hit1_net")),
            "reason": "Deployable candidate must improve Top1 on OSS dev/OOF.",
        },
        {
            "gate": "rank1_loss_budget",
            "status": "pass" if _int(best.get("rank1_loss_count")) <= max(3, _int(best.get("hit1_gain")) / 2) else "fail",
            "value": f"rank1_loss={best.get('rank1_loss_count')}; gain={best.get('hit1_gain')}",
            "reason": "Rank1 losses must be small relative to gains.",
        },
        {
            "gate": "rank1_demotion_rate",
            "status": "pass" if _float(best.get("baseline_rank1_demotion_rate")) <= 0.01 else "fail",
            "value": best.get("baseline_rank1_demotion_rate"),
            "reason": "Baseline rank1 demotion rate must stay within the 1% budget.",
        },
        {
            "gate": "leakage_gate",
            "status": "pass" if leakage_row.get("status") == "pass" else "fail",
            "value": leakage_row.get("forbidden_feature_present", ""),
            "reason": "Training features must not include source/provenance/answer IDs.",
        },
        {
            "gate": "source_net_concentration",
            "status": "pass" if source_stats["max_positive_net_share"] <= 0.35 else "warn",
            "value": source_stats["max_positive_net_share"],
            "reason": "Positive net should not be dominated by a single source/province slice.",
        },
        {
            "gate": "heldout_hard_not_used",
            "status": "pass",
            "value": "not_used",
            "reason": "13.19 freeze gate is dev/OOF-only.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_freeze_fix_gate_failures"
    else:
        decision = "freeze_deployable_conflict_guard_for_future_validation_go_no_go"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.19 Top1-Loss-Guarded Scorecard/Loss Review and Freeze Gate",
        "",
        "Read-only freeze gate after bounded dev/OOF execution. This review separates deployable gates from diagnostic upper-bound gates and does not run heldout/hard.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Frozen Candidate",
        "",
        _md_table(
            [
                ["field", "value"],
                ["candidate_id", report["frozen_candidate"].get("candidate_id", "")],
                ["hit1 gain/loss/net", f"{m['hit1_gain']}/{m['hit1_loss']}/{m['hit1_net']}"],
                ["rank1_loss_count", m["rank1_loss_count"]],
                ["baseline_rank1_demotion_rate", m["baseline_rank1_demotion_rate"]],
                ["applied_group_rate", m["applied_group_rate"]],
                ["deployability", m["deployability"]],
            ]
        ),
        "",
        "## Candidate Classification",
        "",
        _md_table([["rank", "candidate_id", "deployability", "hit1_net", "rank1_loss", "reason"]] + [[row["scorecard_rank"], row["candidate_id"], row["deployability"], row["hit1_net"], row["rank1_loss_count"], row["deployability_reason"]] for row in report["candidate_review_rows"]]),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.19 Top1-loss-guarded scorecard/loss review and freeze gate 已完成。\n"
        f"结论：{report['decision']}。冻结的是可部署 gate：{report['frozen_candidate'].get('candidate_id')}，hit1_net={m['hit1_net']}，gain/loss={m['hit1_gain']}/{m['hit1_loss']}，rank1_loss={m['rank1_loss_count']}。\n"
        "注意：T1G_D/T1G_C/T1G_F 是诊断上限或含标签 gate，不作为线上候选。下一步建议：13.20 validation boundary / explicit go-no-go for frozen deployable conflict guard；默认不跑 heldout/hard。\n"
        "禁止：上线、改 GoalSearcher、把诊断上限当线上候选、无明确 go 跑 heldout/hard。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.19 Top1-loss-guarded scorecard/loss review and freeze gate" not in text:
        rows = f"""          <tr>
            <td>13.19 Top1-loss-guarded scorecard/loss review and freeze gate</td>
            <td>Read-only freeze gate separating deployable guarded reranker candidate from diagnostic upper-bound gates.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.18 Top1-loss-guarded dev/OOF execution summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.19 Top1-loss-guarded scorecard/loss review and freeze gate")
    parser.add_argument("--execution-summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE_COVERAGE)
    parser.add_argument("--loss-audit", type=Path, default=DEFAULT_LOSS_AUDIT)
    parser.add_argument("--leakage", type=Path, default=DEFAULT_LEAKAGE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    execution_summary = _read_json(args.execution_summary)
    scorecard = _read_csv(args.scorecard)
    gate_coverage = _read_csv(args.gate_coverage)
    loss_audit = _read_csv(args.loss_audit)
    leakage = _read_csv(args.leakage)
    candidate_review_rows = _candidate_review_rows(scorecard)
    best = _select_freeze_candidate(scorecard)
    source_stats = _source_net_stats(loss_audit, best.get("candidate_id", ""))
    gate_rows, decision = _gate_rows(best, leakage, source_stats)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_classification_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_classification.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "frozen_candidate_manifest_json": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.json")),
        "frozen_candidate_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.csv")),
    }
    deployability, deploy_reason = DEPLOYABILITY.get(best.get("candidate_id", ""), ("unknown", ""))
    frozen_candidate = {
        "candidate_id": best.get("candidate_id", ""),
        "objective_variant": best.get("objective_variant", ""),
        "feature_toggle": best.get("feature_toggle", ""),
        "frozen_from_stage": "13.18 Top1-loss-guarded dev/OOF execution",
        "frozen_by_gate": "13.19 Top1-loss-guarded scorecard/loss review and freeze gate",
        "freeze_scope": "deployable conflict-gated reranker candidate only",
        "deployability": deployability,
        "deployability_reason": deploy_reason,
        "not_a_release": True,
        "not_validated_on_heldout_or_hard": True,
    }
    metrics = {
        "candidate_id": best.get("candidate_id", ""),
        "hit1_gain": _int(best.get("hit1_gain")),
        "hit1_loss": _int(best.get("hit1_loss")),
        "hit1_net": _int(best.get("hit1_net")),
        "hit5_net": _int(best.get("hit5_net")),
        "rank1_loss_count": _int(best.get("rank1_loss_count")),
        "baseline_rank1_demotion_rate": _float(best.get("baseline_rank1_demotion_rate")),
        "applied_group_rate": _float(best.get("applied_group_rate")),
        "deployability": deployability,
        **source_stats,
    }
    report = {
        "stage": "13.19 Top1-loss-guarded scorecard/loss review and freeze gate",
        "read_only": True,
        "decision": decision,
        "metrics": metrics,
        "execution_metrics": execution_summary.get("metrics", {}),
        "frozen_candidate": frozen_candidate,
        "candidate_review_rows": candidate_review_rows,
        "gate_rows": gate_rows,
        "gate_coverage_rows": [row for row in gate_coverage if row.get("candidate_id") == best.get("candidate_id")],
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only freeze review only: no heldout/hard validation, no release, no GoalSearcher edit, no threshold change, and diagnostic label-derived gates are not treated as deployable candidates.",
        "next_stage": {
            "recommended": "13.20 validation boundary / explicit go-no-go for the frozen deployable conflict guard. Default is do_not_validate unless explicit go is provided.",
            "default": "do_not_validate",
        },
    }
    _write_csv(Path(artifacts["candidate_classification_csv"]), candidate_review_rows, ["scorecard_rank", "candidate_id", "deployability", "deployability_reason", "hit1_gain", "hit1_loss", "hit1_net", "rank1_loss_count", "baseline_rank1_demotion_rate", "applied_group_rate", "approval_status"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_json(Path(artifacts["frozen_candidate_manifest_json"]), frozen_candidate)
    _write_csv(Path(artifacts["frozen_candidate_manifest_csv"]), [frozen_candidate], list(frozen_candidate.keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "frozen_candidate": frozen_candidate, "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
