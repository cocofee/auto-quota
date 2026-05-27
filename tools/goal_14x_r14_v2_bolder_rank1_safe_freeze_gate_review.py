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
DEFAULT_EXECUTION = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_candidate_scorecard.csv"
DEFAULT_SOURCE = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_source_fold_robustness.csv"
DEFAULT_TAXONOMY = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_taxonomy_empty_audit.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_leakage_gate_report.csv"
DEFAULT_GATE = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_gate_coverage.csv"
DEFAULT_OUTPUT = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review_summary.json"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
R14_A_APPLIED_RATE = 0.00232


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


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


def _index(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {row[key]: row for row in rows if row.get(key)}


def _taxonomy_net(taxonomy_rows: list[dict[str, str]], candidate_id: str, slice_name: str) -> int:
    for row in taxonomy_rows:
        if row.get("candidate_id") == candidate_id and row.get("taxonomy_slice") == slice_name:
            return _int(row.get("net"))
    return 0


def _review_candidate(
    row: dict[str, str],
    source_by_id: dict[str, dict[str, str]],
    taxonomy_rows: list[dict[str, str]],
    leakage_by_id: dict[str, dict[str, str]],
) -> dict[str, Any]:
    cid = row["candidate_id"]
    reasons: list[str] = []
    hit1_net = _int(row.get("hit1_net"))
    rank1_loss = _int(row.get("rank1_loss_count"))
    hit5_net = _int(row.get("hit5_net"))
    applied_rate = _float(row.get("applied_group_rate"))
    demotion_rate = _float(row.get("baseline_rank1_demotion_rate"))
    source = source_by_id.get(cid, {})
    source_share = _float(source.get("max_positive_net_share"))
    taxonomy_empty_net = _taxonomy_net(taxonomy_rows, cid, "taxonomy_empty")
    leakage = leakage_by_id.get(cid, {})
    if leakage.get("status") != "pass":
        reasons.append("leakage_gate_failed")
    if hit1_net <= 0:
        reasons.append("non_positive_hit1_net")
    if rank1_loss > 1:
        reasons.append("rank1_loss_gt_1")
    if demotion_rate > 0.0025:
        reasons.append("rank1_demotion_rate_gt_0_0025")
    if applied_rate <= R14_A_APPLIED_RATE:
        reasons.append("applied_rate_le_R14_A_noop")
    if applied_rate > 0.04:
        reasons.append("applied_rate_gt_0_04")
    if hit5_net < 0:
        reasons.append("negative_hit5_net")
    if taxonomy_empty_net > max(0, hit1_net // 2):
        reasons.append("taxonomy_empty_dominates_positive_net")
    if source_share > 0.50:
        reasons.append("source_family_positive_net_share_gt_0_50")
    elif source_share > 0.35:
        reasons.append("source_family_positive_net_share_warn")
    status = "freeze_candidate_for_future_validation" if not reasons else "reject_or_hold"
    return {
        "candidate_id": cid,
        "hit1_gain": _int(row.get("hit1_gain")),
        "hit1_loss": _int(row.get("hit1_loss")),
        "hit1_net": hit1_net,
        "rank1_loss_count": rank1_loss,
        "hit5_net": hit5_net,
        "applied_group_rate": applied_rate,
        "baseline_rank1_demotion_rate": demotion_rate,
        "source_family_positive_net_share": source_share,
        "taxonomy_empty_net": taxonomy_empty_net,
        "dev_oof_approval_status": row.get("approval_status"),
        "freeze_status": status,
        "freeze_reason": "|".join(reasons) if reasons else "passes_14_8_loss_budget",
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    rows = [["candidate", "hit1 net", "loss", "rank1 loss", "applied rate", "freeze status", "reason"]]
    for row in report["candidate_reviews"]:
        rows.append([row["candidate_id"], row["hit1_net"], row["hit1_loss"], row["rank1_loss_count"], row["applied_group_rate"], row["freeze_status"], row["freeze_reason"]])
    lines = [
        "# 14.12 R14 v2 Freeze Gate Review",
        "",
        "Read-only freeze/no-freeze review over 14.11 dev/OOF artifacts. Heldout/hard are not read.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Reason: {report['decision_reason']}",
        "",
        "## Candidate Reviews",
        "",
        _md_table(rows),
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **14.12 R14 v2 freeze gate review completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        report["decision_reason"],
        "",
        "## Best Candidate",
        "",
        f"- Candidate: `{report.get('frozen_candidate', {}).get('candidate_id', '<none>')}`",
        f"- Top1 gain/loss/net: `{report.get('frozen_candidate', {}).get('hit1_gain', 0)}/{report.get('frozen_candidate', {}).get('hit1_loss', 0)}/{report.get('frozen_candidate', {}).get('hit1_net', 0)}`",
        f"- rank1 loss: `{report.get('frozen_candidate', {}).get('rank1_loss_count', 0)}`",
        "",
        "## Boundary",
        "",
        "- No heldout/hard validation has been run for R14 v2.",
        "- No release or GoalSearcher integration has been done.",
        "- Future heldout/hard validation requires explicit validation go.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    frozen = report.get("frozen_candidate") or {}
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.12 R14 v2 freeze gate review 已完成。\n"
        f"结论：{report['decision']}。frozen={frozen.get('candidate_id', '<none>')}，Top1 net={frozen.get('hit1_net', 0)}，rank1_loss={frozen.get('rank1_loss_count', 0)}。\n"
        "下一步建议：14.13 R14 v2 validation boundary / explicit validation go-no-go。默认不跑 heldout/hard；只有明确 validation go 才允许验证。\n"
        "禁止：用 heldout/hard 调参、上线、改 GoalSearcher、改阈值、扩大候选矩阵。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.12 R14 v2 freeze gate review" not in text:
        row = f"""          <tr>
            <td>14.12 R14 v2 freeze gate review</td>
            <td>Read-only freeze/no-freeze review over R14 v2 dev/OOF outputs and loss budget.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {report['updated_at']} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.12 R14 v2 freeze gate review")
    parser.add_argument("--execution-summary", type=Path, default=DEFAULT_EXECUTION)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--source-fold", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--leakage", type=Path, default=DEFAULT_LEAKAGE)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    execution = _read_json(args.execution_summary)
    scorecard = _read_csv(args.scorecard)
    source_rows = _read_csv(args.source_fold)
    taxonomy_rows = _read_csv(args.taxonomy)
    leakage_rows = _read_csv(args.leakage)
    source_by_id = _index(source_rows, "candidate_id")
    leakage_by_id = _index(leakage_rows, "candidate_id")
    reviews = [_review_candidate(row, source_by_id, taxonomy_rows, leakage_by_id) for row in scorecard]
    frozen_candidates = [row for row in reviews if row["freeze_status"] == "freeze_candidate_for_future_validation"]
    frozen_candidates.sort(key=lambda row: (-row["hit1_net"], row["hit1_loss"], row["rank1_loss_count"]))
    frozen = frozen_candidates[0] if frozen_candidates else None
    if frozen:
        decision = "freeze_R14_v2_candidate_for_future_validation"
        reason = "At least one R14 v2 candidate passed the 14.8 dev/OOF loss budget and freeze checks."
    else:
        decision = "no_freeze_return_to_R14_v2_redesign"
        reason = "No R14 v2 candidate passed the 14.8 dev/OOF loss budget and freeze checks."

    output_md = args.output.with_suffix(".md")
    frozen_manifest = args.output.with_name(args.output.stem.replace("_summary", "_frozen_candidate") + ".json")
    report = {
        "stage": "14.12 R14 v2 freeze gate review",
        "read_only_review": True,
        "decision": decision,
        "decision_reason": reason,
        "execution_decision": execution.get("decision"),
        "candidate_reviews": reviews,
        "frozen_candidate": frozen or {},
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": {
            "summary_json": str(args.output),
            "summary_md": str(output_md),
            "frozen_candidate_json": str(frozen_manifest) if frozen else "",
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": (
            "14.12 reviewed dev/OOF artifacts only. It did not read heldout/hard, train, tune thresholds, release code, "
            "edit GoalSearcher, or change online behavior."
        ),
        "next_stage": {
            "recommended": "14.13 R14 v2 validation boundary / explicit validation go-no-go" if frozen else "14.13 R14 v2 redesign closure",
            "default": "do_not_validate",
        },
    }
    _write_json(args.output, report)
    _write_markdown(output_md, report)
    if frozen:
        _write_json(frozen_manifest, frozen)
    _write_csv(args.output.with_name(args.output.stem.replace("_summary", "_candidate_reviews") + ".csv"), reviews, ["candidate_id", "hit1_gain", "hit1_loss", "hit1_net", "rank1_loss_count", "hit5_net", "applied_group_rate", "baseline_rank1_demotion_rate", "source_family_positive_net_share", "taxonomy_empty_net", "dev_oof_approval_status", "freeze_status", "freeze_reason"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(args.output), "decision": decision, "frozen": (frozen or {}).get("candidate_id", "")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
