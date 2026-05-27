from __future__ import annotations

import argparse
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
DEFAULT_HELDOUT = AGENT_STATE / "goal_14x_rank1_safe_source_robust_validation_heldout_summary.json"
DEFAULT_HARD = AGENT_STATE / "goal_14x_rank1_safe_source_robust_validation_hard_summary.json"
DEFAULT_OUTPUT = AGENT_STATE / "goal_14x_rank1_safe_source_robust_validation_package_review_summary.json"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _split_status(metrics: dict[str, Any]) -> str:
    if int(metrics.get("rank1_loss_count") or 0) > 0:
        return "fail_rank1_loss"
    if int(metrics.get("hit5_net") or 0) < 0:
        return "fail_hit5_negative"
    if int(metrics.get("hit1_net") or 0) > 0:
        return "pass_positive_rank1_net"
    if int(metrics.get("hit1_net") or 0) == 0:
        return "neutral_no_rank1_gain"
    return "fail_negative_rank1_net"


def _decision(split_reports: list[dict[str, Any]]) -> tuple[str, str]:
    statuses = [_split_status(report["metrics"]) for report in split_reports]
    if any(status.startswith("fail") for status in statuses):
        return "validation_failed_do_not_release", "At least one validation split failed rank1/hit5 safety or Top1 net."
    if all(status == "pass_positive_rank1_net" for status in statuses):
        return "validation_pass_release_gate_can_be_requested", "Both heldout and hard have positive Top1 net with no rank1 loss."
    return "validation_neutral_do_not_release_strategy_review", "Validation is rank1-safe but lacks positive Top1 net on every split."


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    rows = [["split", "hit1 gain/loss/net", "rank1_loss", "hit5 net", "applied", "status"]]
    for split_report in report["split_reports"]:
        m = split_report["metrics"]
        rows.append(
            [
                m["split"],
                f"{m['hit1_gain']}/{m['hit1_loss']}/{m['hit1_net']}",
                m["rank1_loss_count"],
                m["hit5_net"],
                m["applied_groups"],
                _split_status(m),
            ]
        )
    lines = [
        "# 14.6 R14_A Heldout/Hard Validation Package Review",
        "",
        "This combines the fixed-candidate heldout and hard A/B validation outputs. It does not select a new candidate, tune thresholds, or authorize release.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Reason: {report['decision_reason']}",
        "",
        "## Scorecard",
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
    rows = []
    for split_report in report["split_reports"]:
        m = split_report["metrics"]
        rows.append(f"- {m['split']}: Top1 gain/loss/net `{m['hit1_gain']}/{m['hit1_loss']}/{m['hit1_net']}`, rank1_loss `{m['rank1_loss_count']}`, hit5_net `{m['hit5_net']}`, applied `{m['applied_groups']}`")
    text = "\n".join(
        [
            "# Current Goal Roadmap Status",
            "",
            f"Updated: {report['updated_at']} Asia/Shanghai",
            "",
            "## Where We Are",
            "",
            "Current stage: **14.6 heldout/hard A/B validation completed**.",
            "",
            f"Decision: **{report['decision']}**.",
            "",
            report["decision_reason"],
            "",
            "## Validation Metrics",
            "",
            *rows,
            "",
            "## Current Boundary",
            "",
            "Default: `do_not_release`.",
            "",
            "Blocked unless separately authorized by a future gate:",
            "",
            "- release",
            "- GoalSearcher integration",
            "- online threshold changes",
            "- changing frozen candidate or gates using heldout/hard feedback",
            "",
            "Next recommended stage: **14.7 post-validation closure / strategy decision**.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    heldout = next(item["metrics"] for item in report["split_reports"] if item["metrics"]["split"] == "heldout")
    hard = next(item["metrics"] for item in report["split_reports"] if item["metrics"]["split"] == "hard")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.6 heldout/hard A/B validation for frozen R14_A 已完成。\n"
        f"结论：{report['decision']}。heldout Top1 net={heldout['hit1_net']} loss={heldout['hit1_loss']} rank1_loss={heldout['rank1_loss_count']}；"
        f"hard Top1 net={hard['hit1_net']} loss={hard['hit1_loss']} rank1_loss={hard['rank1_loss_count']}。\n"
        "下一步建议：14.7 post-validation closure / strategy decision。默认不 release、不接 GoalSearcher、不用 heldout/hard 反调候选；只读决定是收口、回到 14.x redesign，还是另开明确 release gate。\n"
        "禁止：用 heldout/hard 调参、改阈值、切换候选、扩大 gate、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.6 R14_A heldout/hard validation package review" not in text:
        row = f"""          <tr>
            <td>14.6 R14_A heldout/hard validation package review</td>
            <td>Fixed R14_A validation package across heldout and hard; no tuning, no release, no GoalSearcher change.</td>
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
    parser = argparse.ArgumentParser(description="14.6 R14_A heldout/hard validation package review")
    parser.add_argument("--heldout-summary", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard-summary", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    split_reports = [_read_json(args.heldout_summary), _read_json(args.hard_summary)]
    decision, reason = _decision(split_reports)
    output_md = args.output.with_suffix(".md")
    report = {
        "stage": "14.6 heldout/hard A/B validation package review for frozen R14_A",
        "read_only_review": True,
        "explicit_user_go_for_validation": True,
        "decision": decision,
        "decision_reason": reason,
        "split_reports": split_reports,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": {
            "summary_json": str(args.output),
            "summary_md": str(output_md),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": (
            "14.6 reviewed validation outputs only. It did not tune thresholds, switch candidates, expand gates, "
            "release code, edit GoalSearcher, or use heldout/hard for model selection."
        ),
        "next_stage": {
            "recommended": "14.7 post-validation closure / strategy decision",
            "default": "do_not_release",
        },
    }
    _write_json(args.output, report)
    _write_markdown(output_md, report)
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(args.output), "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
