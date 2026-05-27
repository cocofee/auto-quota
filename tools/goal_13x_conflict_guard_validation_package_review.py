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
DEFAULT_HELDOUT = AGENT_STATE / "goal_13x_conflict_guard_validation_heldout_summary.json"
DEFAULT_HARD = AGENT_STATE / "goal_13x_conflict_guard_validation_hard_summary.json"
DEFAULT_HELDOUT_GATE = AGENT_STATE / "goal_13x_conflict_guard_validation_heldout_gate_coverage.csv"
DEFAULT_HARD_GATE = AGENT_STATE / "goal_13x_conflict_guard_validation_hard_gate_coverage.csv"
DEFAULT_HELDOUT_LOSS = AGENT_STATE / "goal_13x_conflict_guard_validation_heldout_loss_slices.csv"
DEFAULT_HARD_LOSS = AGENT_STATE / "goal_13x_conflict_guard_validation_hard_loss_slices.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_conflict_guard_validation_package_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _gate_rows(heldout: dict[str, Any], hard: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    hm = heldout["metrics"]
    hdm = hard["metrics"]
    rows = [
        {
            "gate": "heldout_hit1_net_positive",
            "status": "pass" if _int(hm.get("hit1_net")) > 0 else "fail",
            "value": hm.get("hit1_net"),
            "reason": "Heldout Top1 net must be positive before release/integration.",
        },
        {
            "gate": "hard_hit1_net_non_negative",
            "status": "pass" if _int(hdm.get("hit1_net")) >= 0 else "fail",
            "value": hdm.get("hit1_net"),
            "reason": "Hard Top1 net must not be negative.",
        },
        {
            "gate": "rank1_loss_budget",
            "status": "pass" if _int(hm.get("rank1_loss_count")) <= 1 and _int(hdm.get("rank1_loss_count")) <= 1 else "fail",
            "value": f"heldout={hm.get('rank1_loss_count')}; hard={hdm.get('rank1_loss_count')}",
            "reason": "Validation rank1 losses must stay extremely low for a release gate.",
        },
        {
            "gate": "hit5_secondary_signal",
            "status": "pass" if _int(hm.get("hit5_net")) > 0 and _int(hdm.get("hit5_net")) > 0 else "warn",
            "value": f"heldout={hm.get('hit5_net')}; hard={hdm.get('hit5_net')}",
            "reason": "Hit5 improvement is useful diagnostic support but cannot override Top1 failure.",
        },
        {
            "gate": "validation_not_used_for_selection",
            "status": "pass" if not hm.get("heldout_or_hard_used_for_selection") and not hdm.get("heldout_or_hard_used_for_selection") else "fail",
            "value": f"heldout={hm.get('heldout_or_hard_used_for_selection')}; hard={hdm.get('heldout_or_hard_used_for_selection')}",
            "reason": "Validation splits must remain independent.",
        },
    ]
    decision = "do_not_release_conflict_guard_validation_not_positive" if any(row["status"] == "fail" for row in rows) else "validation_passed_enter_release_gate"
    return rows, decision


def _focus_rows(*tables: list[dict[str, str]], limit: int = 30) -> list[dict[str, Any]]:
    rows = []
    for table in tables:
        for row in table:
            rows.append(
                {
                    "split": row.get("split", ""),
                    "slice_dimension": row.get("slice_dimension", ""),
                    "slice_key": row.get("slice_key", ""),
                    "groups": _int(row.get("groups")),
                    "gain": _int(row.get("gain")),
                    "loss": _int(row.get("loss")),
                    "net": _int(row.get("net")),
                }
            )
    rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    return rows[:limit]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    lines = [
        "# 13.21 Conflict Guard Validation Package Review",
        "",
        "Read-only package review of heldout/hard A/B validation for frozen T1G_B_conflict_guard.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Split Metrics",
        "",
        _md_table(
            [
                ["split", "top80_present", "applied_groups", "hit1 gain/loss/net", "rank1_loss", "hit5 gain/loss/net"],
                ["heldout", hm["rerankable_top80_present_groups"], hm["applied_groups"], f"{hm['hit1_gain']}/{hm['hit1_loss']}/{hm['hit1_net']}", hm["rank1_loss_count"], f"{hm['hit5_gain']}/{hm['hit5_loss']}/{hm['hit5_net']}"],
                ["hard", hdm["rerankable_top80_present_groups"], hdm["applied_groups"], f"{hdm['hit1_gain']}/{hdm['hit1_loss']}/{hdm['hit1_net']}", hdm["rank1_loss_count"], f"{hdm['hit5_gain']}/{hdm['hit5_loss']}/{hdm['hit5_net']}"],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Loss Focus",
        "",
        _md_table([["split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"]] + [[row["split"], row["slice_dimension"], row["slice_key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["loss_focus"][:12]]),
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
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.21 heldout/hard A/B validation for frozen T1G_B_conflict_guard 已完成。\n"
        f"结论：{report['decision']}。heldout Top1 net={hm['hit1_net']}（gain/loss={hm['hit1_gain']}/{hm['hit1_loss']}），hard Top1 net={hdm['hit1_net']}（gain/loss={hdm['hit1_gain']}/{hdm['hit1_loss']}）；Hit5 均为 +4。\n"
        "下一步建议：13.22 validation-neutral/failed strategy decision。不要 release；大胆转向可观测 low-confidence/margin gate rewrite 或扩大 OSS source-aware training，而不是继续推当前 conflict guard。\n"
        "禁止：上线、改 GoalSearcher、用 heldout/hard 调参、把 Hit5 正收益宣称为 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.21 heldout/hard A/B validation for frozen T1G_B_conflict_guard" not in text:
        rows = f"""          <tr>
            <td>13.21 heldout/hard A/B validation for frozen T1G_B_conflict_guard</td>
            <td>A/B validation package review for frozen deployable conflict guard.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.20 validation boundary / explicit go-no-go for frozen T1G_B_conflict_guard</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.21 conflict guard validation package review")
    parser.add_argument("--heldout-summary", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard-summary", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--heldout-gate", type=Path, default=DEFAULT_HELDOUT_GATE)
    parser.add_argument("--hard-gate", type=Path, default=DEFAULT_HARD_GATE)
    parser.add_argument("--heldout-loss", type=Path, default=DEFAULT_HELDOUT_LOSS)
    parser.add_argument("--hard-loss", type=Path, default=DEFAULT_HARD_LOSS)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    heldout = _read_json(args.heldout_summary)
    hard = _read_json(args.hard_summary)
    heldout_loss = _read_csv(args.heldout_loss)
    hard_loss = _read_csv(args.hard_loss)
    heldout_gate = _read_csv(args.heldout_gate)
    hard_gate = _read_csv(args.hard_gate)
    gate_rows, decision = _gate_rows(heldout, hard)
    loss_focus = _focus_rows(heldout_loss, hard_loss)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "loss_focus_csv": str(output_prefix.with_name(output_prefix.name + "_loss_focus.csv")),
        "gate_coverage_combined_csv": str(output_prefix.with_name(output_prefix.name + "_gate_coverage_combined.csv")),
    }
    report = {
        "stage": "13.21 heldout/hard A/B validation for frozen T1G_B_conflict_guard",
        "read_only_review": True,
        "decision": decision,
        "heldout_metrics": heldout["metrics"],
        "hard_metrics": hard["metrics"],
        "gate_rows": gate_rows,
        "loss_focus": loss_focus,
        "gate_coverage_rows": heldout_gate + hard_gate,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Validation package review only: no release, no GoalSearcher edit, no threshold change, no candidate reselection, no heldout/hard tuning, and no claim of general Top1 improvement.",
        "next_stage": {
            "recommended": "13.22 validation-neutral/failed strategy decision: do not release current conflict guard; decide whether to rewrite observable low-confidence/margin gate or expand OSS source-aware training.",
            "default": "do_not_release",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["loss_focus_csv"]), loss_focus, ["split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"])
    _write_csv(Path(artifacts["gate_coverage_combined_csv"]), heldout_gate + hard_gate, ["candidate_id", "split", "gate_reason", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "heldout_hit1_net": heldout["metrics"]["hit1_net"], "hard_hit1_net": hard["metrics"]["hit1_net"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
