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
DEFAULT_VALIDATION_REVIEW = AGENT_STATE / "goal_14x_rank1_safe_source_robust_validation_package_review_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_candidate_scorecard.csv"
DEFAULT_GATE_COVERAGE = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_strong_challenger_gate_coverage.csv"
DEFAULT_OUTPUT = AGENT_STATE / "goal_14x_post_validation_closure_strategy_decision_summary.json"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _candidate(row_by_id: dict[str, dict[str, str]], candidate_id: str) -> dict[str, str]:
    if candidate_id not in row_by_id:
        raise KeyError(f"missing candidate in scorecard: {candidate_id}")
    return row_by_id[candidate_id]


def _gate_applied(gate_rows: list[dict[str, str]], candidate_id: str) -> int:
    return sum(_int(row.get("applied")) for row in gate_rows if row.get("candidate_id") == candidate_id)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    validation_rows = [["split", "Top1 gain/loss/net", "rank1_loss", "applied"]]
    for split in report["validation_splits"]:
        validation_rows.append(
            [
                split["split"],
                f"{split['hit1_gain']}/{split['hit1_loss']}/{split['hit1_net']}",
                split["rank1_loss_count"],
                split["applied_groups"],
            ]
        )
    candidate_rows = [["candidate", "dev/OOF Top1 net", "loss", "rank1 loss", "applied", "status"]]
    for item in report["candidate_signal_summary"]:
        candidate_rows.append(
            [
                item["candidate_id"],
                item["hit1_net"],
                item["hit1_loss"],
                item["rank1_loss_count"],
                item["applied_groups"],
                item["interpretation"],
            ]
        )
    lines = [
        "# 14.7 Post-Validation Closure / Strategy Decision",
        "",
        "R14_A is closed as no-release. Heldout/hard validation showed rank1 safety, but zero intervention and zero Top1 gain on both splits.",
        "",
        "## Validation",
        "",
        _md_table(validation_rows),
        "",
        "## Dev/OOF Signals",
        "",
        _md_table(candidate_rows),
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Reason: {report['decision_reason']}",
        f"- Next stage: `{report['next_stage']['recommended']}`",
        "",
        "## Redesign Boundary",
        "",
        "\n".join(f"- {item}" for item in report["redesign_boundary"]),
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
        "Current stage: **14.7 post-validation closure / strategy decision completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        report["decision_reason"],
        "",
        "## What This Means",
        "",
        "- R14_A is rank1-safe but no-op on heldout/hard, so it should not be released.",
        "- R14_D proved there is larger dev/OOF signal, but its rank1 losses make it unsafe as-is.",
        "- The next useful path is a dev/OOF-only R14 v2 redesign that increases intervention coverage while adding a stricter online-observable rank1 protection veto.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not release R14_A.",
        "- Do not tune using heldout/hard.",
        "- Do not switch to R14_D directly.",
        "- Do not edit GoalSearcher or online thresholds.",
        "- Do not use heldout/hard again until a new dev/OOF-frozen candidate passes its own freeze gate.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.7 post-validation closure / strategy decision 已完成。\n"
        "结论：R14_A heldout/hard rank1-safe 但 applied=0、Top1 net=0，正式 no-release。\n"
        "下一步建议：14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan。只用 dev/OOF 和 OSS 训练矩阵，目标是提高 intervention coverage，同时保留线上可观测 rank1 protection veto。\n"
        "禁止：发布 R14_A、直接切 R14_D、用 heldout/hard 调参、改阈值、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.7 post-validation closure / strategy decision" not in text:
        row = f"""          <tr>
            <td>14.7 post-validation closure / strategy decision</td>
            <td>Closed R14_A as no-release after neutral heldout/hard validation and selected dev/OOF-only R14 v2 redesign as the next route.</td>
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
    parser = argparse.ArgumentParser(description="14.7 post-validation closure / strategy decision")
    parser.add_argument("--validation-review", type=Path, default=DEFAULT_VALIDATION_REVIEW)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE_COVERAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    validation_review = _read_json(args.validation_review)
    scorecard = _read_csv(args.scorecard)
    gate_rows = _read_csv(args.gate_coverage)
    by_id = {row["candidate_id"]: row for row in scorecard}
    r14_a = _candidate(by_id, "R14_A_rank1_veto_strong_challenger")
    r14_d = _candidate(by_id, "R14_D_near_miss_proxy_no_clean_rank1")
    validation_splits = [
        {
            "split": split_report["metrics"]["split"],
            "hit1_gain": split_report["metrics"]["hit1_gain"],
            "hit1_loss": split_report["metrics"]["hit1_loss"],
            "hit1_net": split_report["metrics"]["hit1_net"],
            "rank1_loss_count": split_report["metrics"]["rank1_loss_count"],
            "applied_groups": split_report["metrics"]["applied_groups"],
        }
        for split_report in validation_review["split_reports"]
    ]
    r14_a_validation_applied = sum(_int(row["applied_groups"]) for row in validation_splits)
    r14_a_validation_net = sum(_int(row["hit1_net"]) for row in validation_splits)
    if validation_review["decision"] != "validation_neutral_do_not_release_strategy_review":
        decision = "hold_for_manual_review"
        reason = f"Unexpected validation package decision: {validation_review['decision']}"
    elif r14_a_validation_applied == 0 and r14_a_validation_net == 0:
        decision = "close_R14_A_no_release_start_R14_v2_dev_oof_redesign"
        reason = "R14_A preserved rank1 safety, but heldout/hard applied zero times and produced zero Top1 gain; release would add complexity without accuracy value."
    else:
        decision = "close_R14_A_no_release_review_needed"
        reason = "R14_A did not satisfy positive heldout/hard validation criteria for release."

    candidate_signal_summary = [
        {
            "candidate_id": r14_a["candidate_id"],
            "hit1_net": _int(r14_a["hit1_net"]),
            "hit1_loss": _int(r14_a["hit1_loss"]),
            "rank1_loss_count": _int(r14_a["rank1_loss_count"]),
            "applied_groups": _gate_applied(gate_rows, r14_a["candidate_id"]),
            "interpretation": "too_conservative_no_release",
        },
        {
            "candidate_id": r14_d["candidate_id"],
            "hit1_net": _int(r14_d["hit1_net"]),
            "hit1_loss": _int(r14_d["hit1_loss"]),
            "rank1_loss_count": _int(r14_d["rank1_loss_count"]),
            "applied_groups": _gate_applied(gate_rows, r14_d["candidate_id"]),
            "interpretation": "use_as_dev_oof_signal_not_as_release_candidate",
        },
    ]
    output_md = args.output.with_suffix(".md")
    report = {
        "stage": "14.7 post-validation closure / strategy decision",
        "read_only_review": True,
        "decision": decision,
        "decision_reason": reason,
        "validation_package_decision": validation_review["decision"],
        "validation_splits": validation_splits,
        "candidate_signal_summary": candidate_signal_summary,
        "redesign_boundary": [
            "Use only dev/OOF and OSS training artifacts for redesign and candidate selection.",
            "Do not use heldout/hard again until a new candidate is frozen by dev/OOF-only gates.",
            "Increase intervention coverage beyond R14_A no-op behavior.",
            "Preserve online-observable rank1 protection veto; no label-derived runtime gates.",
            "Borrow R14_D's near-miss coverage signal, but require zero or tightly budgeted rank1 loss before any freeze.",
            "Keep taxonomy-empty/query-family-empty rows guarded or routed to DQ/parser lanes instead of broad reranker takeover.",
        ],
        "next_stage": {
            "recommended": "14.8 R14 v2 bolder rank1-safe dev/OOF redesign plan",
            "description": "Define a dev/OOF-only experiment plan for a less no-op but still rank1-safe reranker: candidate gates, objective variants, loss budget, required artifacts, and explicit execution go/no-go.",
            "default": "do_not_train_until_explicit_go",
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": {
            "summary_json": str(args.output),
            "summary_md": str(output_md),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": (
            "14.7 is read-only closure. It did not train, tune, release, edit GoalSearcher, change thresholds, "
            "switch to R14_D, or use heldout/hard for redesign decisions beyond rejecting R14_A release."
        ),
    }
    _write_json(args.output, report)
    _write_markdown(output_md, report)
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(args.output), "decision": decision, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
