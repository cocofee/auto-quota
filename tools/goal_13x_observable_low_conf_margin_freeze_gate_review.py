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
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

DEFAULT_SUMMARY = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_candidate_scorecard.csv"
DEFAULT_GATE_COVERAGE = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_gating_coverage_report.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_loss_audit_by_slice.csv"
DEFAULT_SOURCE_ROBUSTNESS = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_source_fold_robustness.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_leakage_gate_report.csv"
DEFAULT_THRESHOLD = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_threshold_manifest.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_freeze_gate_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_rel(path: str | Path) -> str:
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def max_share(rows: list[dict[str, str]], candidate_id: str, dimension: str) -> tuple[float, str, int, int, int]:
    subset = [row for row in rows if row.get("candidate_id") == candidate_id and row.get("slice_dimension") == dimension]
    if not subset:
        return 0.0, "", 0, 0, 0
    top = max(subset, key=lambda row: to_float(row.get("abs_net_share_of_positive_total")))
    return (
        to_float(top.get("abs_net_share_of_positive_total")),
        str(top.get("slice_key") or ""),
        to_int(top.get("gain")),
        to_int(top.get("loss")),
        to_int(top.get("net")),
    )


def candidate_loss_count(loss_rows: list[dict[str, str]], candidate_id: str) -> int:
    gate_rows = [
        row for row in loss_rows
        if row.get("candidate_id") == candidate_id and row.get("slice_dimension") == "gate_reason"
    ]
    return sum(to_int(row.get("loss")) for row in gate_rows)


def classify_candidates(
    scorecard: list[dict[str, str]],
    source_rows: list[dict[str, str]],
    leakage_rows: list[dict[str, str]],
    loss_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    out = []
    for row in scorecard:
        candidate_id = row["candidate_id"]
        source_share, source_key, source_gain, source_loss, source_net = max_share(source_rows, candidate_id, "source_family")
        province_share, province_key, _, _, province_net = max_share(source_rows, candidate_id, "province")
        fold_share, fold_key, _, fold_loss, fold_net = max_share(source_rows, candidate_id, "oof_fold")
        leakage = next((item for item in leakage_rows if item.get("candidate_id") == candidate_id), {})
        rank1_loss = to_int(row.get("rank1_loss_count"))
        hit1_net = to_int(row.get("hit1_net"))
        hit1_gain = to_int(row.get("hit1_gain"))
        hit1_loss = to_int(row.get("hit1_loss"))
        demotion_rate = to_float(row.get("baseline_rank1_demotion_rate"))
        approval_status = row.get("approval_status", "")
        if approval_status != "pass_dev_oof_candidate":
            freeze_status = "reject_warning_status"
            freeze_reason = approval_status
        elif hit1_net <= 0:
            freeze_status = "reject_non_positive_top1_net"
            freeze_reason = "Top1 net must be positive"
        elif leakage.get("status") != "pass":
            freeze_status = "reject_leakage"
            freeze_reason = "leakage gate failed"
        elif rank1_loss == 0 and hit1_loss == 0:
            freeze_status = "freeze_preferred_zero_top1_loss"
            freeze_reason = "positive Top1 net with zero Top1/rank1 loss"
        elif rank1_loss <= 2 and demotion_rate <= 0.005:
            freeze_status = "freeze_eligible_with_rank1_loss"
            freeze_reason = "positive Top1 net but has rank1 losses; less conservative than zero-loss candidate"
        else:
            freeze_status = "reject_rank1_budget"
            freeze_reason = "rank1 loss budget exceeded"
        if province_share > 0.50:
            freeze_reason += "; province net concentration warning"
        if fold_share > 0.35:
            freeze_reason += "; fold net concentration warning"
        out.append(
            {
                "scorecard_rank": to_int(row.get("scorecard_rank")),
                "candidate_id": candidate_id,
                "hit1_gain": hit1_gain,
                "hit1_loss": hit1_loss,
                "hit1_net": hit1_net,
                "rank1_loss_count": rank1_loss,
                "baseline_rank1_demotion_rate": demotion_rate,
                "applied_group_rate": to_float(row.get("applied_group_rate")),
                "approval_status": approval_status,
                "leakage_status": leakage.get("status", ""),
                "source_family_top_share": source_share,
                "source_family_top_key": source_key,
                "source_family_top_net": source_net,
                "province_top_share": province_share,
                "province_top_key": province_key,
                "province_top_net": province_net,
                "fold_top_share": fold_share,
                "fold_top_key": fold_key,
                "fold_top_net": fold_net,
                "fold_top_loss": fold_loss,
                "gate_loss_count": candidate_loss_count(loss_rows, candidate_id),
                "freeze_status": freeze_status,
                "freeze_reason": freeze_reason,
            }
        )
    out.sort(key=lambda row: (
        row["freeze_status"] != "freeze_preferred_zero_top1_loss",
        row["freeze_status"] != "freeze_eligible_with_rank1_loss",
        -row["hit1_net"],
    ))
    return out


def select_freeze_candidate(review_rows: list[dict[str, Any]]) -> dict[str, Any]:
    zero_loss = [row for row in review_rows if row["freeze_status"] == "freeze_preferred_zero_top1_loss"]
    if zero_loss:
        zero_loss.sort(key=lambda row: (-row["hit1_net"], row["source_family_top_share"], row["fold_top_share"]))
        return zero_loss[0]
    eligible = [row for row in review_rows if row["freeze_status"] == "freeze_eligible_with_rank1_loss"]
    eligible.sort(key=lambda row: (-row["hit1_net"], row["rank1_loss_count"], row["source_family_top_share"]))
    return eligible[0] if eligible else {}


def gate_rows(frozen: dict[str, Any], threshold: dict[str, Any], summary: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "dev_oof_execution_complete",
            "status": "pass" if summary.get("decision") == "dev_oof_execution_completed_review_required" else "fail",
            "value": summary.get("decision", ""),
            "reason": "Freeze gate requires completed 13.25 dev/OOF execution.",
        },
        {
            "gate": "candidate_selected",
            "status": "pass" if frozen else "fail",
            "value": frozen.get("candidate_id", ""),
            "reason": "A freeze candidate must pass review gates.",
        },
        {
            "gate": "positive_top1_net",
            "status": "pass" if to_int(frozen.get("hit1_net")) > 0 else "fail",
            "value": frozen.get("hit1_net", 0),
            "reason": "Frozen candidate must have positive dev/OOF Top1 net.",
        },
        {
            "gate": "zero_rank1_loss_preferred",
            "status": "pass" if to_int(frozen.get("rank1_loss_count")) == 0 else "warn",
            "value": frozen.get("rank1_loss_count", 0),
            "reason": "For this lane, zero rank1 loss is preferred over slightly higher net with losses.",
        },
        {
            "gate": "demotion_rate_budget",
            "status": "pass" if to_float(frozen.get("baseline_rank1_demotion_rate")) <= 0.005 else "fail",
            "value": frozen.get("baseline_rank1_demotion_rate", 0),
            "reason": "Rank1 demotion rate must stay at or below 0.5% for freeze.",
        },
        {
            "gate": "source_family_concentration",
            "status": "pass" if to_float(frozen.get("source_family_top_share")) <= 0.35 else "warn",
            "value": frozen.get("source_family_top_share", 0),
            "reason": "Top source_family share should not exceed 35%.",
        },
        {
            "gate": "fold_concentration",
            "status": "warn" if to_float(frozen.get("fold_top_share")) > 0.35 else "pass",
            "value": frozen.get("fold_top_share", 0),
            "reason": "Fold concentration above 35% is a validation risk, but not a freeze blocker if Top1 losses are zero.",
        },
        {
            "gate": "threshold_scale_checked",
            "status": "pass" if threshold.get("confidence_scale") == "0_100" and to_float(threshold.get("confidence_q25")) > 1 else "fail",
            "value": f"scale={threshold.get('confidence_scale')}; q25={threshold.get('confidence_q25')}",
            "reason": "13.25 corrected the confidence-scale issue by using dev/OOF quantiles.",
        },
        {
            "gate": "heldout_hard_not_used",
            "status": "pass" if not summary.get("metrics", {}).get("heldout_used_for_selection") and not summary.get("metrics", {}).get("hard_used_for_selection") else "fail",
            "value": f"heldout={summary.get('metrics', {}).get('heldout_used_for_selection')}; hard={summary.get('metrics', {}).get('hard_used_for_selection')}",
            "reason": "Freeze gate remains dev/OOF-only.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_freeze_fix_gate_failures"
    elif any(row["status"] == "warn" for row in rows):
        decision = "freeze_conservative_zero_loss_candidate_with_validation_risk_notes"
    else:
        decision = "freeze_conservative_zero_loss_candidate"
    return rows, decision


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    frozen = report["frozen_candidate"]
    lines = [
        "# 13.26 Observable Low-Confidence/Margin Scorecard/Loss Review and Freeze Gate",
        "",
        "Read-only freeze gate after 13.25 dev/OOF execution. This gate compares the higher-net A3/A4 candidates against the zero-loss A1 candidate before any validation.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Frozen Candidate",
        "",
        md_table(
            [
                ["field", "value"],
                ["candidate_id", frozen.get("candidate_id", "")],
                ["hit1 gain/loss/net", f"{frozen.get('hit1_gain')}/{frozen.get('hit1_loss')}/{frozen.get('hit1_net')}"],
                ["rank1_loss_count", frozen.get("rank1_loss_count", "")],
                ["demotion_rate", frozen.get("baseline_rank1_demotion_rate", "")],
                ["applied_group_rate", frozen.get("applied_group_rate", "")],
                ["source_family_top_share", frozen.get("source_family_top_share", "")],
                ["fold_top_share", frozen.get("fold_top_share", "")],
            ]
        ),
        "",
        "## Candidate Review",
        "",
        md_table(
            [["rank", "candidate_id", "gain/loss/net", "rank1_loss", "source_share", "fold_share", "freeze_status", "reason"]]
            + [
                [
                    row["scorecard_rank"],
                    row["candidate_id"],
                    f"{row['hit1_gain']}/{row['hit1_loss']}/{row['hit1_net']}",
                    row["rank1_loss_count"],
                    row["source_family_top_share"],
                    row["fold_top_share"],
                    row["freeze_status"],
                    row["freeze_reason"],
                ]
                for row in report["candidate_review_rows"]
            ]
        ),
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
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


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    frozen = report["frozen_candidate"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.26 observable low-confidence/margin scorecard/loss review and freeze gate 已完成。\n"
        f"结论：{report['decision']}。冻结候选={frozen.get('candidate_id')}，dev/OOF Top1 gain/loss/net={frozen.get('hit1_gain')}/{frozen.get('hit1_loss')}/{frozen.get('hit1_net')}，rank1_loss={frozen.get('rank1_loss_count')}。A3/A4 净收益略高但有 rank1 loss，作为诊断 lead 不冻结。\n"
        "下一步建议：13.27 validation boundary / explicit validation go-no-go for frozen T1G_A1_low_conf_q25。默认不跑 heldout/hard，只有明确 go 才验证。\n"
        "禁止：直接 release、改 GoalSearcher、用 heldout/hard 反向选候选或阈值、把 A3/A4 当冻结候选、把 dev/OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.26 observable low-confidence/margin scorecard/loss review and freeze gate</td>
            <td>Read-only freeze gate selecting conservative zero-loss T1G_A1 candidate over higher-net A3/A4 diagnostic leads.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "13.26 observable low-confidence/margin scorecard/loss review and freeze gate" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.26 observable low-confidence/margin freeze gate review")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE_COVERAGE)
    parser.add_argument("--loss-audit", type=Path, default=DEFAULT_LOSS_AUDIT)
    parser.add_argument("--source-robustness", type=Path, default=DEFAULT_SOURCE_ROBUSTNESS)
    parser.add_argument("--leakage", type=Path, default=DEFAULT_LEAKAGE)
    parser.add_argument("--threshold", type=Path, default=DEFAULT_THRESHOLD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    summary = read_json(args.summary)
    scorecard = read_csv(args.scorecard)
    loss_rows = read_csv(args.loss_audit)
    source_rows = read_csv(args.source_robustness)
    leakage_rows = read_csv(args.leakage)
    threshold = read_json(args.threshold)

    review_rows = classify_candidates(scorecard, source_rows, leakage_rows, loss_rows)
    frozen = select_freeze_candidate(review_rows)
    gates, decision = gate_rows(frozen, threshold, summary)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_review_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_review.csv")),
        "frozen_candidate_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.csv")),
        "frozen_candidate_manifest_json": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.json")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
    }
    report = {
        "stage": "13.26 observable low-confidence/margin scorecard/loss review and freeze gate",
        "read_only_review": True,
        "decision": decision,
        "frozen_candidate": frozen,
        "candidate_review_rows": review_rows,
        "gate_rows": gates,
        "threshold_manifest": threshold,
        "artifacts": artifacts,
        "next_stage": {
            "id": "13.27",
            "name": "validation boundary / explicit validation go-no-go for frozen T1G_A1_low_conf_q25",
            "recommended": "13.27：只读定义 frozen T1G_A1_low_conf_q25 是否允许进入 heldout/hard validation；默认无明确 go 就不跑验证。",
            "default": "do_not_validate",
        },
        "anti_drift_conclusion": (
            "13.26 is read-only. It freezes only a dev/OOF candidate for possible future validation. "
            "It does not run heldout/hard, release, edit GoalSearcher, tune thresholds, or treat A3/A4 dev/OOF diagnostic leads as validation candidates."
        ),
    }
    write_csv(Path(artifacts["candidate_review_csv"]), review_rows, ["scorecard_rank", "candidate_id", "hit1_gain", "hit1_loss", "hit1_net", "rank1_loss_count", "baseline_rank1_demotion_rate", "applied_group_rate", "approval_status", "leakage_status", "source_family_top_share", "source_family_top_key", "source_family_top_net", "province_top_share", "province_top_key", "province_top_net", "fold_top_share", "fold_top_key", "fold_top_net", "fold_top_loss", "gate_loss_count", "freeze_status", "freeze_reason"])
    write_csv(Path(artifacts["frozen_candidate_manifest_csv"]), [frozen], ["candidate_id", "hit1_gain", "hit1_loss", "hit1_net", "rank1_loss_count", "baseline_rank1_demotion_rate", "applied_group_rate", "source_family_top_share", "source_family_top_key", "province_top_share", "province_top_key", "fold_top_share", "fold_top_key", "freeze_status", "freeze_reason"])
    write_json(Path(artifacts["frozen_candidate_manifest_json"]), frozen)
    write_csv(Path(artifacts["gate_checks_csv"]), gates, ["gate", "status", "value", "reason"])
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    update_dashboard(args.dashboard, report)
    print(json.dumps({"decision": decision, "frozen_candidate": frozen.get("candidate_id"), "summary": safe_rel(artifacts["summary_json"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
