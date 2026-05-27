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
DEFAULT_HELDOUT = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_heldout_summary.json"
DEFAULT_HARD = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_hard_summary.json"
DEFAULT_HELDOUT_GATE = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_heldout_gate_coverage.csv"
DEFAULT_HARD_GATE = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_hard_gate_coverage.csv"
DEFAULT_HELDOUT_LOSS = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_heldout_loss_slices.csv"
DEFAULT_HARD_LOSS = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_hard_loss_slices.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_package_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def gate_rows(heldout: dict[str, Any], hard: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    hm = heldout["metrics"]
    hdm = hard["metrics"]
    rows = [
        {
            "gate": "heldout_hit1_net_positive",
            "status": "pass" if to_int(hm.get("hit1_net")) > 0 else "fail",
            "value": hm.get("hit1_net"),
            "reason": "Heldout Top1 net must be positive before release/integration.",
        },
        {
            "gate": "hard_hit1_net_non_negative",
            "status": "pass" if to_int(hdm.get("hit1_net")) >= 0 else "fail",
            "value": hdm.get("hit1_net"),
            "reason": "Hard Top1 net must not be negative.",
        },
        {
            "gate": "rank1_loss_budget",
            "status": "pass" if to_int(hm.get("rank1_loss_count")) <= 1 and to_int(hdm.get("rank1_loss_count")) <= 1 else "fail",
            "value": f"heldout={hm.get('rank1_loss_count')}; hard={hdm.get('rank1_loss_count')}",
            "reason": "Validation rank1 losses must remain extremely low for this zero-loss frozen candidate.",
        },
        {
            "gate": "gate_coverage_nonzero",
            "status": "pass" if to_int(hm.get("applied_groups")) > 0 and to_int(hdm.get("applied_groups")) > 0 else "fail",
            "value": f"heldout={hm.get('applied_groups')}; hard={hdm.get('applied_groups')}",
            "reason": "Low-confidence q25 gate must actually apply on both validation splits.",
        },
        {
            "gate": "hit5_secondary_signal",
            "status": "pass" if to_int(hm.get("hit5_net")) >= 0 and to_int(hdm.get("hit5_net")) >= 0 else "warn",
            "value": f"heldout={hm.get('hit5_net')}; hard={hdm.get('hit5_net')}",
            "reason": "Hit5 is secondary and cannot override Top1/rank1 failure.",
        },
        {
            "gate": "validation_not_used_for_selection",
            "status": "pass" if not hm.get("heldout_or_hard_used_for_selection") and not hdm.get("heldout_or_hard_used_for_selection") else "fail",
            "value": f"heldout={hm.get('heldout_or_hard_used_for_selection')}; hard={hdm.get('heldout_or_hard_used_for_selection')}",
            "reason": "Validation splits must remain independent.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_release_low_conf_validation_not_positive"
    elif any(row["status"] == "warn" for row in rows):
        decision = "validation_passed_with_secondary_warnings_enter_release_gate_review"
    else:
        decision = "validation_passed_enter_release_gate_review"
    return rows, decision


def focus_rows(*tables: list[dict[str, str]], limit: int = 30) -> list[dict[str, Any]]:
    rows = []
    for table in tables:
        for row in table:
            rows.append(
                {
                    "split": row.get("split", ""),
                    "slice_dimension": row.get("slice_dimension", ""),
                    "slice_key": row.get("slice_key", ""),
                    "groups": to_int(row.get("groups")),
                    "gain": to_int(row.get("gain")),
                    "loss": to_int(row.get("loss")),
                    "net": to_int(row.get("net")),
                }
            )
    rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    return rows[:limit]


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    lines = [
        "# 13.28 Low-Confidence Validation Package Review",
        "",
        "Read-only package review of heldout/hard A/B validation for frozen T1G_A1_low_conf_q25.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Split Metrics",
        "",
        md_table(
            [
                ["split", "top80_present", "applied_groups", "hit1 gain/loss/net", "rank1_loss", "hit5 gain/loss/net"],
                ["heldout", hm["rerankable_top80_present_groups"], hm["applied_groups"], f"{hm['hit1_gain']}/{hm['hit1_loss']}/{hm['hit1_net']}", hm["rank1_loss_count"], f"{hm['hit5_gain']}/{hm['hit5_loss']}/{hm['hit5_net']}"],
                ["hard", hdm["rerankable_top80_present_groups"], hdm["applied_groups"], f"{hdm['hit1_gain']}/{hdm['hit1_loss']}/{hdm['hit1_net']}", hdm["rank1_loss_count"], f"{hdm['hit5_gain']}/{hdm['hit5_loss']}/{hdm['hit5_net']}"],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Loss Focus",
        "",
        md_table([["split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"]] + [[row["split"], row["slice_dimension"], row["slice_key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["loss_focus"][:12]]),
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
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.28 heldout/hard A/B validation for frozen T1G_A1_low_conf_q25 已完成。\n"
        f"结论：{report['decision']}。heldout Top1 net={hm['hit1_net']}（gain/loss={hm['hit1_gain']}/{hm['hit1_loss']}），hard Top1 net={hdm['hit1_net']}（gain/loss={hdm['hit1_gain']}/{hdm['hit1_loss']}），rank1_loss heldout/hard={hm['rank1_loss_count']}/{hdm['rank1_loss_count']}。\n"
        "下一步建议：13.29 post-validation release/integration gate。只读决定是否请求 release/integration go；默认仍不接线上。\n"
        "禁止：自动 release、改 GoalSearcher、用 heldout/hard 调阈值或重选候选、把 Hit5 当 Top1。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.28 heldout/hard A/B validation for frozen T1G_A1_low_conf_q25</td>
            <td>Heldout/hard validation package review for frozen observable low-confidence candidate.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "13.28 heldout/hard A/B validation for frozen T1G_A1_low_conf_q25" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.28 low-confidence validation package review")
    parser.add_argument("--heldout-summary", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard-summary", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--heldout-gate", type=Path, default=DEFAULT_HELDOUT_GATE)
    parser.add_argument("--hard-gate", type=Path, default=DEFAULT_HARD_GATE)
    parser.add_argument("--heldout-loss", type=Path, default=DEFAULT_HELDOUT_LOSS)
    parser.add_argument("--hard-loss", type=Path, default=DEFAULT_HARD_LOSS)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    heldout = read_json(args.heldout_summary)
    hard = read_json(args.hard_summary)
    heldout_loss = read_csv(args.heldout_loss)
    hard_loss = read_csv(args.hard_loss)
    heldout_gate = read_csv(args.heldout_gate)
    hard_gate = read_csv(args.hard_gate)
    gates, decision = gate_rows(heldout, hard)
    loss_focus = focus_rows(heldout_loss, hard_loss)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "loss_focus_csv": str(output_prefix.with_name(output_prefix.name + "_loss_focus.csv")),
        "gate_coverage_combined_csv": str(output_prefix.with_name(output_prefix.name + "_gate_coverage_combined.csv")),
    }
    report = {
        "stage": "13.28 heldout/hard A/B validation for frozen T1G_A1_low_conf_q25",
        "read_only_review": True,
        "decision": decision,
        "heldout_metrics": heldout["metrics"],
        "hard_metrics": hard["metrics"],
        "gate_rows": gates,
        "loss_focus": loss_focus,
        "gate_coverage_rows": heldout_gate + hard_gate,
        "artifacts": artifacts,
        "next_stage": {
            "id": "13.29",
            "name": "post-validation release/integration gate",
            "recommended": "13.29：只读决定是否请求 release/integration go；默认仍不接线上、不改 GoalSearcher。",
        },
        "anti_drift_conclusion": "Validation package review only: no release, no GoalSearcher edit, no threshold change, no candidate reselection, no heldout/hard tuning, and no claim of general Top1 improvement beyond the fixed validation package.",
    }
    write_csv(Path(artifacts["gate_checks_csv"]), gates, ["gate", "status", "value", "reason"])
    write_csv(Path(artifacts["loss_focus_csv"]), loss_focus, ["split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"])
    write_csv(Path(artifacts["gate_coverage_combined_csv"]), heldout_gate + hard_gate, ["candidate_id", "split", "gate_reason", "groups", "applied", "vetoed", "gain", "loss", "net"])
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    update_dashboard(args.dashboard, report)
    print(json.dumps({"decision": decision, "summary": safe_rel(artifacts["summary_json"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
